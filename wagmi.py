import re
import asyncio
import logging
import os
import threading
import time
import json
from datetime import datetime
import sqlite3
import random
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.tl.functions.channels import GetParticipantRequest
from flask import Flask, jsonify, request, redirect, session, render_template_string
import hmac
import hashlib
import base64
import urllib.parse

# Ortam değişkenleri
DB_NAME = os.environ.get("DB_NAME").strip()
DB_USER = os.environ.get("DB_USER").strip()
DB_PASS = os.environ.get("DB_PASS")
DB_HOST = os.environ.get("DB_HOST").strip()
DB_PORT = os.environ.get("DB_PORT")
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24).hex())
X_CONSUMER_KEY = os.environ.get("X_CONSUMER_KEY")
X_CONSUMER_SECRET = os.environ.get("X_CONSUMER_SECRET")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET")

app = Flask(__name__)
app.secret_key = SECRET_KEY

bot_client = TelegramClient('lion', API_ID, API_HASH)
user_client = TelegramClient('monkey', API_ID, API_HASH)

# ====================== PİPEDREAM KODU - %100 AYNI ======================
def post_to_x(message):
    x_posting_enabled = get_bot_setting_sync("x_posting_enabled") or "enabled"
    if x_posting_enabled != "enabled":
        logger.info("X paylaşımı devre dışı.")
        return

    text = (message or "").strip()
    if not text:
        logger.warning("X'e gönderilecek mesaj boş.")
        return
    if len(text) > 280:
        text = text[:277] + "..."

    url = "https://api.twitter.com/2/tweets"
    method = "POST"

    oauth_params = {
        "oauth_consumer_key": X_CONSUMER_KEY,
        "oauth_token": X_ACCESS_TOKEN,
        "oauth_nonce": base64.b64encode(os.urandom(16)).decode('utf-8'),
        "oauth_timestamp": str(int(time.time())),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0"
    }

    param_string = "&".join([
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(oauth_params.items())
    ])

    base_string = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(param_string, safe='')}"
    signing_key = f"{urllib.parse.quote(X_CONSUMER_SECRET, safe='')}&{urllib.parse.quote(X_ACCESS_TOKEN_SECRET or '', safe='')}"
    hashed = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1)
    signature = base64.b64encode(hashed.digest()).decode()
    oauth_params["oauth_signature"] = signature

    auth_header = "OAuth " + ", ".join([
        f'{k}="{urllib.parse.quote(v, safe="")}"' for k, v in sorted(oauth_params.items())
    ])

    try:
        response = requests.post(
            url=url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "User-Agent": "GemWagmiBot/1.0"
            },
            json={"text": text}
        )
        if response.status_code == 201:
            logger.info(f"Tweet atıldı: {len(text)} karakter → {text[:50]}...")
        else:
            logger.error(f"X API Hatası: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"X paylaşım hatası: {e}")
# =====================================================================

def get_connection():
    try:
        return psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT,
            sslmode="require"
        )
    except psycopg2.OperationalError as e:
        logger.error(f"Database connection failed: {e}")
        raise e

def init_db_sync():
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id SERIAL PRIMARY KEY,
                    channel_id BIGINT NOT NULL UNIQUE,
                    username TEXT,
                    title TEXT,
                    channel_type TEXT CHECK (channel_type IN ('source','target'))
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    chat_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_contracts (
                    contract_address TEXT PRIMARY KEY
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS token_mappings (
                    token_name TEXT PRIMARY KEY,
                    contract_address TEXT NOT NULL,
                    announcement_message_id BIGINT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT
                );
            """)
        conn.commit()
        logger.info("Database initialized or already exists.")
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")
        raise
    finally:
        if conn:
            conn.close()

def get_admins_sync():
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM admins")
            rows = cur.fetchall()
            return {r["user_id"]: r for r in rows}
    except Exception as e:
        logger.error(f"Error getting admins: {e}")
        return {}
    finally:
        if conn:
            conn.close()

def add_admin_sync(user_id, first_name, last_name="", lang="en", is_default=False):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO admins (user_id, first_name, last_name, lang, is_default)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                  SET first_name=%s, last_name=%s, lang=%s, is_default=%s;
            """, (user_id, first_name, last_name, lang, is_default,
                  first_name, last_name, lang, is_default))
        conn.commit()
        logger.info(f"Admin {user_id} added/updated.")
    except Exception as e:
        logger.error(f"Error adding admin {user_id}: {e}")
    finally:
        if conn:
            conn.close()

def remove_admin_sync(user_id):
    admins = get_admins_sync()
    if admins.get(user_id, {}).get("is_default"):
        logger.warning(f"Attempted to remove default admin {user_id}.")
        return
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM admins WHERE user_id = %s", (user_id,))
        conn.commit()
        logger.info(f"Admin {user_id} removed.")
    except Exception as e:
        logger.error(f"Error removing admin {user_id}: {e}")
    finally:
        if conn:
            conn.close()

def get_channels_sync(channel_type):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM channels WHERE channel_type = %s", (channel_type,))
            return cur.fetchall()
    except Exception as e:
        logger.error(f"Error getting {channel_type} channels: {e}")
        return []
    finally:
        if conn:
            conn.close()

def add_channel_sync(channel_id, username, title, channel_type):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channels (channel_id, username, title, channel_type)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (channel_id) DO NOTHING;
            """, (channel_id, username, title, channel_type))
        conn.commit()
        if cur.rowcount > 0:
            logger.info(f"{channel_type.capitalize()} channel {channel_id} ('{title}') added.")
        else:
            logger.info(f"{channel_type.capitalize()} channel {channel_id} already exists.")
    except Exception as e:
        logger.error(f"Error adding {channel_type} channel {channel_id}: {e}")
    finally:
        if conn:
            conn.close()

def remove_channel_sync(channel_id, channel_type):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM channels WHERE channel_id = %s AND channel_type = %s",
                        (channel_id, channel_type))
        conn.commit()
        if cur.rowcount > 0:
            logger.info(f"{channel_type.capitalize()} channel {channel_id} removed.")
        else:
            logger.warning(f"No {channel_type} channel found with ID {channel_id} to remove.")
    except Exception as e:
        logger.error(f"Error removing {channel_type} channel {channel_id}: {e}")
    finally:
        if conn:
            conn.close()

def is_message_processed_sync(chat_id, message_id):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM processed_messages WHERE chat_id = %s AND message_id = %s",
                        (chat_id, message_id))
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking if message {message_id} in chat {chat_id} processed: {e}")
        return False
    finally:
        if conn:
            conn.close()

def record_processed_message_sync(chat_id, message_id):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_messages (chat_id, message_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (chat_id, message_id))
        conn.commit()
        if cur.rowcount > 0:
            logger.debug(f"Recorded processed message {message_id} in chat {chat_id}.")
    except Exception as e:
        logger.error(f"Error recording processed message {message_id} in chat {chat_id}: {e}")
    finally:
        if conn:
            conn.close()

def is_contract_processed_sync(contract_address):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM processed_contracts WHERE contract_address = %s",
                        (contract_address,))
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking if contract {contract_address} processed: {e}")
        return False
    finally:
        if conn:
            conn.close()

def record_processed_contract_sync(contract_address):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_contracts (contract_address)
                VALUES (%s) ON CONFLICT DO NOTHING
            """, (contract_address,))
        conn.commit()
        if cur.rowcount > 0:
            logger.info(f"Recorded processed contract: {contract_address}.")
    except Exception as e:
        logger.error(f"Error recording processed contract {contract_address}: {e}")
    finally:
        if conn:
            conn.close()

def get_token_mapping_sync(token_name):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM token_mappings WHERE token_name = %s", (token_name,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error getting token mapping for '{token_name}': {e}")
        return None
    finally:
        if conn:
            conn.close()

def add_token_mapping_sync(token_name, contract_address, announcement_message_id=None):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO token_mappings (token_name, contract_address, announcement_message_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (token_name) DO UPDATE
                  SET contract_address = %s,
                      announcement_message_id = COALESCE(%s, token_mappings.announcement_message_id)
            """, (token_name, contract_address, announcement_message_id,
                  contract_address, announcement_message_id))
        conn.commit()
        if cur.rowcount > 0:
            logger.info(f"Token mapping added/updated for '{token_name}' -> {contract_address}.")
    except Exception as e:
        logger.error(f"Error adding/updating token mapping for '{token_name}': {e}")
    finally:
        if conn:
            conn.close()

def update_token_announcement_sync(token_name, announcement_message_id):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE token_mappings SET announcement_message_id = %s
                WHERE token_name = %s
            """, (announcement_message_id, token_name))
        conn.commit()
        if cur.rowcount > 0:
            logger.info(f"Updated announcement ID for '{token_name}' to {announcement_message_id}.")
        else:
            logger.warning(f"No token mapping found for '{token_name}' to update announcement ID.")
    except Exception as e:
        logger.error(f"Error updating token announcement ID for '{token_name}': {e}")
    finally:
        if conn:
            conn.close()

def get_mapping_by_announcement_sync(announcement_message_id):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM token_mappings WHERE announcement_message_id = %s",
                        (announcement_message_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"Error getting mapping by announcement ID {announcement_message_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_bot_setting_sync(setting):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT setting_value FROM bot_settings WHERE setting_key = %s", (setting,))
            row = cur.fetchone()
            return row["setting_value"] if row else None
    except Exception as e:
        logger.error(f"Error getting bot setting '{setting}': {e}")
        return None
    finally:
        if conn:
            conn.close()

def set_bot_setting_sync(setting, value):
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_settings (setting_key, setting_value)
                VALUES (%s, %s)
                ON CONFLICT (setting_key) DO UPDATE SET setting_value = %s
            """, (setting, value, value))
        conn.commit()
        if cur.rowcount > 0:
            logger.info(f"Bot setting '{setting}' set to '{value}'.")
    except Exception as e:
        logger.error(f"Error setting bot setting '{setting}': {e}")
    finally:
        if conn:
            conn.close()

async def init_db():
    await asyncio.to_thread(init_db_sync)

async def get_admins():
    return await asyncio.to_thread(get_admins_sync)

async def add_admin(user_id, first_name, last_name="", lang="en", is_default=False):
    await asyncio.to_thread(add_admin_sync, user_id, first_name, last_name, lang, is_default)

async def remove_admin(user_id):
    await asyncio.to_thread(remove_admin_sync, user_id)

async def get_channels(channel_type):
    return await asyncio.to_thread(get_channels_sync, channel_type)

async def add_channel(channel_id, username, title, channel_type):
    await asyncio.to_thread(add_channel_sync, channel_id, username, title, channel_type)

async def remove_channel(channel_id, channel_type):
    await asyncio.to_thread(remove_channel_sync, channel_id, channel_type)

async def is_message_processed(chat_id, message_id):
    return await asyncio.to_thread(is_message_processed_sync, chat_id, message_id)

async def record_processed_message(chat_id, message_id):
    await asyncio.to_thread(record_processed_message_sync, chat_id, message_id)

async def is_contract_processed(contract_address):
    return await asyncio.to_thread(is_contract_processed_sync, contract_address)

async def record_processed_contract(contract_address):
    await asyncio.to_thread(record_processed_contract_sync, contract_address)

async def get_token_mapping(token_name):
    return await asyncio.to_thread(get_token_mapping_sync, token_name)

async def add_token_mapping(token_name, contract_address, announcement_message_id=None):
    await asyncio.to_thread(add_token_mapping_sync, token_name, contract_address, announcement_message_id)

async def update_token_announcement(token_name, announcement_message_id):
    await asyncio.to_thread(update_token_announcement_sync, token_name, announcement_message_id)

async def get_mapping_by_announcement(announcement_message_id):
    return await asyncio.to_thread(get_mapping_by_announcement_sync, announcement_message_id)

async def get_bot_setting(setting):
    val = await asyncio.to_thread(get_bot_setting_sync, setting)
    return val if val is not None else DEFAULT_BOT_SETTINGS.get(setting)

async def set_bot_setting(setting, value):
    await asyncio.to_thread(set_bot_setting_sync, setting, value)

DEFAULT_ADMIN_ID = int(os.environ.get("DEFAULT_ADMIN_ID", "7567322437"))
DEFAULT_SOURCE_CHANNEL = {
    "channel_id": -1001998961899,
    "username": "@gem_tools_calls",
    "title": "GemTools Calls",
    "channel_type": "source"
}
DEFAULT_TARGET_CHANNEL = json.loads(os.environ.get("DEFAULT_TARGET_CHANNEL", '{"channel_id": -1002829702089, "username": "", "title": "Wagmi Gem Hunter", "channel_type": "target"}'))
DEFAULT_BOT_SETTINGS = {
    "bot_status": "running",
    "custom_gif": "https://dl.dropbox.com/scl/fi/u6r3x30cno1ebmvbpu5k1/video.mp4?rlkey=ytfk8qkdpwwm3je6hjcqgd89s&st=vxjkqe6c?dl=1",
    "x_posting_enabled": "enabled"
}

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/bot_logs.log", mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def retry_telethon_call(coro, max_retries=5, base_delay=1.0):
    for i in range(max_retries):
        try:
            return await coro
        except sqlite3.OperationalError as e:
            logger.warning(f"Retry attempt {i+1}/{max_retries} for Telethon call due to database locked: {e}")
            if i < max_retries - 1:
                delay = base_delay * (2 ** i) + random.uniform(0, 1)
                await asyncio.sleep(delay)
            else:
                logger.error(f"Max retries reached for Telethon call: {e}")
                raise
        except Exception as e:
            logger.error(f"Non-retryable error during Telethon call: {e}")
            raise
    raise RuntimeError("Retry logic failed or max_retries was 0")

def extract_contract(text: str) -> str | None:
    m = re.findall(r"\b[A-Za-z0-9]{32,50}\b", text)
    return m[0] if m else None

def extract_token_name_from_source(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return "unknown"
    for line in lines:
        match = re.search(r"\$([A-Za-z0-9_]+)", line)
        if match:
            token = match.group(1)
            return token
    return "unknown"

def parse_tff_output(text: str) -> dict:
    data = {}
    data["mint_status"] = (re.search(r"Mint:\s*(\w+)", text) or [None, "N/A"])[1]
    data["liquidity_status"] = (re.search(r"Liq:\s*\$?([\d\.,KkMmBb]+)", text) or [None, "N/A"])[1]
    data["market_cap"] = (re.search(r"MC:\s*\$?([\d\.,KkMmBb]+)", text) or [None, "N/A"])[1]
    return data

def build_new_template_with_emoji(token_name, contract, market_cap, liquidity_status, mint_status):
   return (
        "🚀 *New 💎 GEM Landed!* 💎\n\n"
        f"💰 ${token_name.upper()}\n\n"
        f"📊 *Market Cap:* {market_cap}\n"
        f"💦 *Liquidity:* {liquidity_status}\n"
        f"🔥 *Minting:* {mint_status}\n\n"
        f"🔗 *Contract:* `{contract}`\n"
        "🌐 *Network:* #SOL"
    )

def build_x_text(token_name, contract, market_cap, liquidity_status, mint_status):
    return (
        "🚀 New GEM Landed! 💎\n\n"  # Emojiler eklendi
        f" ${token_name.upper()}\n\n"
        f"💰 Market Cap: {market_cap}\n"
        f"📝 *Contract:* `{contract}`\n"
        f"🔗 *Network:* #SOL\n\n"
        f" Join our AI-powered Telegram group:\n"
        f"https://t.me/wagmi100xgem"
    )

def build_update_template(token_name, old_mc, new_mc, profit):
   return (
        f"🚀 *Early GEM Hunters Winning Big!* 💎\n\n"
        f"💵 *{token_name.upper()}* Market Cap: {new_mc} 💎\n"
        f"🔥 {prof} & STILL RUNNING! 💎\n\n"
        "Stay sharp for the next hidden GEM! 💎"
    )

def build_announcement_buttons(contract):
    return [
        [
            Button.url("Chart", f"https://dexscreener.com/solana/{contract}"),
            Button.url("Trojan", "https://t.me/solana_trojanbot?start=r-gemwagmi0001"),
            Button.url("Soul", "https://t.me/soul_sniper_bot?start=WpQErcIT5oHr"),
            Button.url("MEVX", "https://t.me/Mevx?start=wN17b0M1lsJs")
        ],
        [
            Button.url("Algora", f"https://t.me/algoratradingbot?start=r-tff-{contract}"),
            Button.url("Trojan N", f"https://t.me/nestor_trojanbot?start=r-shielzuknf5b-{contract}"),
            Button.url("GMGN", f"https://t.me/GMGN_sol03_bot?start=CcJ5M3wBy35JHLp4csmFF8QyxdeHuKasPqKQeFa1TzLC"),
            Button.url("Padre", "https://trade.padre.gg/rk/gemwagmi")
        ],
        [Button.url("Axiom", "https://axiom.trade/@gemwagmi")]
    ]

pending_input = {}

LOGIN_FORM = """<!doctype html>
<title>Login to Telegram</title>
<h2>Step 1: Enter your phone number</h2>
<form method="post">
  <input name="phone" placeholder="+1234567890" required>
  <button type="submit">Send Code</button>
</form>
"""

CODE_FORM = """<!doctype html>
<title>Enter the Code</title>
<h2>Step 2: Enter the code you received</h2>
<form method="post">
  <input name="code" placeholder="12345" required>
  <button type="submit">Verify</button>
</form>
"""

@app.route('/login', methods=['GET', 'POST'])
async def login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        if not phone:
            return "<p>Phone number is required.</p>", 400
        session['phone'] = phone
        try:
            await user_client.connect()
            await user_client.send_code_request(phone)
            logger.info(f"Sent login code request to {phone}")
            return redirect('/submit-code')
        except Exception as e:
            logger.error(f"Error sending login code to {phone}: {e}")
            return f"<p>Error sending code: {e}</p>", 500
    return render_template_string(LOGIN_FORM)

@app.route('/submit-code', methods=['GET', 'POST'])
async def submit_code():
    if 'phone' not in session:
        return redirect('/login')

    phone = session['phone']

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if not code:
            return "<p>Code is required.</p>", 400
        try:
            await user_client.sign_in(phone, code)
            logger.info(f"Logged in user-client for {phone}")
            session.pop('phone', None)
            return "<p>Login successful! You can close this tab.</p>"
        except Exception as e:
            logger.error(f"Login failed for {phone}: {e}")
            return f"<p>Login failed: {e}</p>", 400

    return render_template_string(CODE_FORM)

@app.route('/')
def root():
    return jsonify(status="ok", message="Bot is running"), 200

@app.route('/health')
def health():
    return jsonify(status="ok"), 200

@bot_client.on(events.CallbackQuery)
async def admin_callback_handler(event):
    uid = event.sender_id
    admins = await get_admins()
    if uid not in admins:
        logger.warning(f"Unauthorized callback query from user ID: {uid}")
        return await event.answer("Not authorized")

    data = event.data.decode()
    logger.info(f"Admin {uid} triggered callback: {data}")

    try:
        if data == 'admin_home':
            return await event.edit(await get_admin_dashboard(),
                                   buttons=await build_admin_keyboard(), link_preview=False)
        if data == 'admin_start':
            await set_bot_setting("bot_status", "running")
            await event.answer('Bot started')
            return await event.edit(await get_admin_dashboard(),
                                   buttons=await build_admin_keyboard(), link_preview=False)
        if data == 'admin_pause':
            pending_input[uid] = {'action': 'pause'}
            kb = [[Button.inline("Back", b"admin_home")]]
            return await event.edit("*Pause Bot*\n\nHow many minutes should I pause for?",
                                   buttons=kb, link_preview=False)
        if data == 'admin_stop':
            await set_bot_setting("bot_status", "stopped")
            await event.answer('Bot stopped')
            return await event.edit("*Bot has been shut down.*",
                                   buttons=[[Button.inline("Restart Bot (set to running)", b"admin_start")],
                                            [Button.inline("Back", b"admin_home")]],
                                   link_preview=False)
        if data == 'admin_start_x_posting':
            await set_bot_setting("x_posting_enabled", "enabled")
            await event.answer('X Posting started')
            return await event.edit(await get_admin_dashboard(),
                                   buttons=await build_admin_keyboard(), link_preview=False)
        if data == 'admin_pause_x_posting':
            await set_bot_setting("x_posting_enabled", "disabled")
            await event.answer('X Posting paused')
            return await event.edit(await get_admin_dashboard(),
                                   buttons=await build_admin_keyboard(), link_preview=False)
        if data == 'admin_admins':
            admins = await get_admins()
            kb = [
                [Button.inline("Add Admin", b"admin_add_admin")],
            ]
            removable_admins = {aid: info for aid, info in admins.items() if aid != DEFAULT_ADMIN_ID and not info.get("is_default")}
            if removable_admins:
                kb.append([Button.inline("Remove Admin", b"admin_show_remove_admins")])
            kb.append([Button.inline("Back", b"admin_home")])
            return await event.edit("*Manage Admins*", buttons=kb, link_preview=False)
        if data == 'admin_show_remove_admins':
            admins = await get_admins()
            kb = []
            for aid, info in admins.items():
                if aid != DEFAULT_ADMIN_ID and not info.get("is_default"):
                    kb.append([Button.inline(f"{info.get('first_name', 'N/A')} ({aid})", b"noop"),
                              Button.inline("Remove", f"remove_admin:{aid}".encode())])
            kb.append([Button.inline("Back", b"admin_admins")])
            if not kb:
                return await event.edit("*No more removable admins.*",
                                       buttons=[[Button.inline("Back", b"admin_admins")]], link_preview=False)
            return await event.edit("*Select Admin to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_add_admin':
            pending_input[uid] = {'action': 'confirm_add_admin'}
            return await event.edit("*Add Admin*\n\nSend me the user ID to add:",
                                   buttons=[[Button.inline("Back", b"admin_admins")]], link_preview=False)
        if data.startswith('remove_admin:'):
            aid = int(data.split(':')[1])
            await remove_admin(aid)
            await event.answer("Admin removed", alert=True)
            admins = await get_admins()
            kb = []
            for admin_id, info in admins.items():
                if admin_id != DEFAULT_ADMIN_ID and not info.get("is_default"):
                    kb.append([Button.inline(f"{info.get('first_name', 'N/A')} ({admin_id})", b"noop"),
                              Button.inline("Remove", f"remove_admin:{admin_id}".encode())])
            kb.append([Button.inline("Back", b"admin_admins")])
            if not kb:
                return await event.edit("*No more removable admins.*",
                                       buttons=[[Button.inline("Back", b"admin_admins")]], link_preview=False)
            return await event.edit("*Select Admin to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_targets':
            kb = [
                [Button.inline("Add Target", b"admin_add_target")],
            ]
            targets = await get_channels('target')
            if targets:
                kb.append([Button.inline("Remove Target", b"admin_show_remove_targets")])
            kb.append([Button.inline("Back", b"admin_home")])
            return await event.edit("*Manage Target Channels*", buttons=kb, link_preview=False)
        if data == 'admin_show_remove_targets':
            targets = await get_channels('target')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("Remove", f"remove_target:{ch['channel_id']}".encode())]
                for ch in targets
            ]
            kb.append([Button.inline("Back", b"admin_targets")])
            return await event.edit("*Select Target Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_add_target':
            pending_input[uid] = {'action': 'confirm_add_target'}
            return await event.edit("*Add Target Channel*\n\nSend me the channel ID (e.g., `-1001234567890`) or @username to add:",
                                   buttons=[[Button.inline("Back", b"admin_targets")]], link_preview=False)
        if data.startswith('remove_target:'):
            tid = int(data.split(':')[1])
            await remove_channel(tid, "target")
            await event.answer("Target channel removed", alert=True)
            targets = await get_channels('target')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("Remove", f"remove_target:{ch['channel_id']}".encode())]
                for ch in targets
            ]
            kb.append([Button.inline("Back", b"admin_targets")])
            if not targets:
                return await event.edit("*No more target channels.*",
                                       buttons=[[Button.inline("Back", b"admin_targets")]], link_preview=False)
            return await event.edit("*Select Target Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_sources':
            kb = [
                [Button.inline("Add Source", b"admin_add_source")],
            ]
            sources = await get_channels('source')
            if sources:
                kb.append([Button.inline("Remove Source", b"admin_show_remove_sources")])
            kb.append([Button.inline("Back", b"admin_home")])
            return await event.edit("*Manage Source Channels*", buttons=kb, link_preview=False)
        if data == 'admin_show_remove_sources':
            sources = await get_channels('source')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("Remove", f"remove_source:{ch['channel_id']}".encode())]
                for ch in sources
            ]
            kb.append([Button.inline("Back", b"admin_sources")])
            return await event.edit("*Select Source Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_add_source':
            pending_input[uid] = {'action': 'confirm_add_source'}
            return await event.edit("*Add Source Channel*\n\nSend me the channel ID (e.g., `-1001234567890`) or @username to add:",
                                   buttons=[[Button.inline("Back", b"admin_sources")]], link_preview=False)
        if data.startswith('remove_source:'):
            sid = int(data.split(':')[1])
            await remove_channel(sid, "source")
            await event.answer("Source channel removed", alert=True)
            sources = await get_channels('source')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("Remove", f"remove_source:{ch['channel_id']}".encode())]
                for ch in sources
            ]
            kb.append([Button.inline("Back", b"admin_sources")])
            if not sources:
                return await event.edit("*No more source channels.*",
                                       buttons=[[Button.inline("Back", b"admin_sources")]], link_preview=False)
            return await event.edit("*Select Source Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_update_gif':
            pending_input[uid] = {'action': 'confirm_update_gif'}
            return await event.edit("*Update GIF*\n\nSend me the new GIF URL:",
                                   buttons=[[Button.inline("Back", b"admin_home")]], link_preview=False)

    except Exception as e:
        logger.error(f"Error in admin callback handler for user {uid}, data {data}: {e}")
        await event.answer("An error occurred.", alert=True)
        try:
            await event.edit(await get_admin_dashboard(), buttons=await build_admin_keyboard(),
                            link_preview=False)
        except Exception:
            pass

    # FONKSİYON İÇİNDE → DOĞRU
    await event.answer("Done")

@bot_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def admin_private_handler(event):
    uid = event.sender_id
    admins = await get_admins()
    if uid not in admins:
        logger.warning(f"Unauthorized private message from user ID: {uid}")
        return

    txt = event.raw_text.strip().lower()
    logger.info(f"Admin {uid} sent private message: {txt}")

    # YENİ X KONTROL KOMUTLARI
    if txt == "x pause":
        await set_bot_setting("x_posting_enabled", "disabled")
        await event.reply("X paylaşımı durduruldu.")
        return
    elif txt == "x start":
        await set_bot_setting("x_posting_enabled", "enabled")
        await event.reply("X paylaşımı başlatıldı.")
        return
    elif txt == "x status":
        status = await get_bot_setting("x_posting_enabled") or "enabled"
        await event.reply(f"X paylaşımı: {status.capitalize()}")
        return

    if uid in pending_input:
        act = pending_input.pop(uid)['action']
        try:
            if act == 'pause':
                try:
                    m = int(txt)
                    if m <= 0: raise ValueError("Must be positive")
                except ValueError:
                    await event.reply("Please send a valid positive number of minutes.")
                    pending_input[uid] = {'action': 'pause'}
                    return
                await set_bot_setting("bot_status", "paused")
                await event.reply(f"Paused for {m} minutes.")
                asyncio.create_task(resume_after(m, uid))
                await retry_telethon_call(bot_client.send_message(uid, await get_admin_dashboard(),
                                                                buttons=await build_admin_keyboard(), link_preview=False))
                return
            if act == 'confirm_add_admin':
                try:
                    new_id = int(txt)
                    if new_id <= 0: raise ValueError("Must be positive")
                except ValueError:
                    await event.reply("Invalid user ID. Please send a positive integer ID.")
                    pending_input[uid] = {'action': 'confirm_add_admin'}
                    return
                current_admins = await get_admins()
                if new_id in current_admins:
                    await event.reply(f"User ID {new_id} is already an admin.")
                else:
                    await add_admin(new_id, f"ID:{new_id}")
                    await event.reply(f"Admin {new_id} added.")
                await retry_telethon_call(bot_client.send_message(uid, await get_admin_dashboard(),
                                                                buttons=await build_admin_keyboard(), link_preview=False))
                return
            if act == 'confirm_add_target':
                channel_input = txt.strip()
                try:
                    cid = int(channel_input)
                except ValueError:
                    cid = channel_input
                try:
                    entity = await bot_client.get_entity(cid)
                    if not hasattr(entity, 'id'):
                        raise ValueError("Could not resolve channel entity.")
                    channel_id = entity.id
                    channel_title = entity.title
                    channel_username = entity.username
                    try:
                        me = await bot_client.get_me()
                        await bot_client(GetParticipantRequest(channel=channel_id, participant=me.id))
                    except Exception:
                        await event.reply("Bot is not in that channel or doesn't have access. Make sure the bot is added.")
                        pending_input[uid] = {'action': 'confirm_add_target'}
                        return
                    await add_channel(channel_id, channel_username, channel_title, "target")
                    await event.reply(f"Target channel {channel_title} ({channel_id}) added.")
                    await retry_telethon_call(bot_client.send_message(uid, await get_admin_dashboard(),
                                                                    buttons=await build_admin_keyboard(), link_preview=False))
                    return
                except Exception as e:
                    logger.error(f"Error adding target channel {channel_input}: {e}")
                    await event.reply(f"Could not add target channel '{channel_input}'. Error: {e}")
                    pending_input[uid] = {'action': 'confirm_add_target'}
                    return
            if act == 'confirm_add_source':
                channel_input = txt.strip()
                try:
                    cid = int(channel_input)
                except ValueError:
                    cid = channel_input
                try:
                    if not await user_client.is_user_authorized():
                        await event.reply("User client not authorized. Cannot verify source channel access.")
                        pending_input[uid] = {'action': 'confirm_add_source'}
                        return
                    entity = await user_client.get_entity(cid)
                    if not hasattr(entity, 'id'):
                        raise ValueError("Could not resolve channel entity.")
                    channel_id = entity.id
                    channel_title = entity.title
                    channel_username = entity.username
                    try:
                        me_user = await user_client.get_me()
                        await user_client(GetParticipantRequest(channel=channel_id, participant=me_user.id))
                    except Exception:
                        await event.reply("Your user account is not in that source channel or it's not accessible.")
                        pending_input[uid] = {'action': 'confirm_add_source'}
                        return
                    await add_channel(channel_id, channel_username, channel_title, "source")
                    await event.reply(f"Source channel {channel_title} ({channel_id}) added.")
                    await retry_telethon_call(bot_client.send_message(uid, await get_admin_dashboard(),
                                                                    buttons=await build_admin_keyboard(), link_preview=False))
                    return
                except Exception as e:
                    logger.error(f"Error adding source channel {channel_input}: {e}")
                    await event.reply(f"Could not add source channel '{channel_input}'. Error: {e}")
                    pending_input[uid] = {'action': 'confirm_add_source'}
                    return
            if act == 'confirm_update_gif':
                link = txt.strip()
                if not link.startswith(('http://', 'https://')):
                    await event.reply("Invalid URL format. Please send a valid HTTP or HTTPS URL.")
                    pending_input[uid] = {'action': 'confirm_update_gif'}
                    return
                if "dropboxusercontent.com" in link:
                    link = link.replace("dl.dropboxusercontent.com", "dl.dropbox.com")
                if "?dl=0" in link:
                    link = link.replace("?dl=0", "?dl=1")
                elif "?dl=1" not in link and "?" not in link:
                    link += "?dl=1"
                elif "?dl=1" not in link and "?" in link:
                    link = link.replace("?", "?dl=1&")
                await set_bot_setting("custom_gif", link)
                await event.reply("GIF URL updated.")
                await retry_telethon_call(bot_client.send_message(uid, await get_admin_dashboard(),
                                                                buttons=await build_admin_keyboard(), link_preview=False))
                return
        except Exception as e:
            logger.error(f"Error handling admin input for user {uid}, action {act}: {e}")
            await event.reply("An unexpected error occurred while processing your input.")
            pending_input.pop(uid, None)
            try:
                await retry_telethon_call(bot_client.send_message(uid, "An error occurred. Returning to dashboard.",
                                                                buttons=await build_admin_keyboard(), link_preview=False))
            except Exception:
                pass
    elif txt.lower() in ('/start', 'start'):
        await retry_telethon_call(bot_client.send_message(uid, await get_admin_dashboard(),
                                                        buttons=await build_admin_keyboard(), link_preview=False))
    else:
        pass

@user_client.on(events.NewMessage(incoming=True, chats=[c['channel_id'] for c in get_channels_sync('source')]))
async def channel_handler(event):
    chat_id = event.chat_id
    message_id = event.id
    if await is_message_processed(chat_id, message_id):
        logger.debug(f"Message {message_id} in chat {chat_id} already processed. Skipping.")
        return
    await record_processed_message(chat_id, message_id)
    bot_status = await get_bot_setting('bot_status')
    if bot_status != 'running':
        logger.info(f"Bot status is '{bot_status}'. Skipping message processing for {message_id} in {chat_id}.")
        return
    txt = event.raw_text.strip()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(f"Processing message {message_id} at {now} from chat {chat_id}: {txt[:100]}...")
    update_pattern_check = re.compile(r"MC:\s*\$?[\d\.,KkMmBb]+\s*(?:->|[-–>→])\s*\$?([\d\.,KkMmBb]+)", re.IGNORECASE)
    if update_pattern_check.search(txt):
        logger.info(f"Update pattern matched for message {message_id}.")
        token_sym = extract_token_name_from_source(txt)
        if token_sym == "unknown":
            logger.warning(f"Could not extract token symbol from update message {message_id}. Skipping update processing.")
            return
        mapping = await get_token_mapping(token_sym.lower())
        contract_for_button = "unknown_contract"
        announcement_to_reply_to = None
        if mapping:
            contract_for_button = mapping.get("contract_address", "unknown_contract")
            announcement_to_reply_to = mapping.get("announcement_message_id")
            logger.info(f"Found mapping for token '{token_sym}': CA={contract_for_button}, ReplyMsgID={announcement_to_reply_to} for message {message_id}")
        else:
            logger.warning(f"No mapping found for token symbol: '{token_sym}' in update message {message_id}. Cannot reply to initial announcement.")
        prof_match = re.search(r"(\d+)%", txt)
        prof = prof_match.group(1) if prof_match else "unknown"
        mc_extraction_regex = re.compile(r"MC:\s*\$?([\d\.,KkMmBb]+)\s*(?:->|[-–>→])\s*\$?([\d\.,KkMmBb]+)", re.IGNORECASE)
        mc_match_obj = mc_extraction_regex.search(txt)
        old_mc = "unknown"
        new_mc = "unknown"
        if mc_match_obj:
            old_mc = mc_match_obj.group(1)
            new_mc = mc_match_obj.group(2)
            logger.debug(f"Extracted old MC: {old_mc}, new MC: {new_mc} from message {message_id}.")
        else:
            logger.warning(f"Could not extract MC values using primary regex for update message {message_id}: {txt[:100]}...")
            simple_mc_search = re.findall(r"MC:\s*\$?(?:[\d\.,KkMmBb]+\s*(?:->|[-–>→])\s*\$?)?([\d\.,KkMmBb]+)", txt, re.IGNORECASE)
            if len(simple_mc_search) >= 2:
                old_mc = simple_mc_search[0]
                new_mc = simple_mc_search[1]
                logger.debug(f"Extracted old MC (fallback): {old_mc}, new MC (fallback): {new_mc} from message {message_id}.")
            else:
                logger.warning(f"Could not extract *any* MC values for update message {message_id}: {txt[:100]}...")
        upd_text = build_update_template(token_sym, old_mc, new_mc, prof)
        target_channels = await get_channels('target')
        if not target_channels:
            logger.warning("No target channels configured to send update.")
            return
        for target_channel_info in target_channels:
            target_channel_id = target_channel_info["channel_id"]
            try:
                logger.info(f"Sending update for '{token_sym}' to target channel ID: {target_channel_id} (replying to {announcement_to_reply_to}).")
                await retry_telethon_call(bot_client.send_message(
                    target_channel_id,
                    message=upd_text,
                    reply_to=announcement_to_reply_to
                ))
                logger.info(f"Update sent successfully to {target_channel_id}.")
            except Exception as e:
                logger.error(f"Error sending update to target {target_channel_id} for message {message_id}: {e}")
        return
    contract = extract_contract(txt)
    if not contract:
        logger.info(f"No contract address found in message {message_id}. Skipping new call processing.")
        return
    if await is_contract_processed(contract):
        logger.info(f"Contract {contract} from message {message_id} already processed. Skipping.")
        return
    logger.info(f"Processing as new call for contract: {contract} from message {message_id}.")
    await record_processed_contract(contract)
    logger.info(f"Sending contract {contract} to @ttfbotbot at {now} for message {message_id}.")
    ttf_response = None
    try:
        if not await user_client.is_user_authorized():
            logger.warning(f"User client not authorized for TTF interaction. Skipping for message {message_id}.")
            await retry_telethon_call(bot_client.send_message(DEFAULT_ADMIN_ID, f"User client not authorized for contract: `{contract}` (from message {message_id} in {chat_id}). Please visit /login."))
            return
        if not user_client.is_connected():
            await user_client.connect()
            logger.info("User client reconnected.")
        async with user_client.conversation('@ttfbotbot', timeout=90) as conv:
            await retry_telethon_call(conv.send_message(contract))
            logger.info(f"Sent '{contract}' to @ttfbotbot.")
            ttf_response = await retry_telethon_call(conv.get_response())
            logger.info(f"Received response from @ttfbotbot (Msg ID: {ttf_response.id}) for message {message_id}.")
    except asyncio.TimeoutError:
        logger.warning(f"TTF bot conversation timed out for contract {contract} from message {message_id}.")
        await retry_telethon_call(bot_client.send_message(DEFAULT_ADMIN_ID, f"TTF bot timed out for contract: `{contract}` (from message {message_id} in {chat_id})."))
        return
    except Exception as e:
        logger.error(f"TTF bot error for contract {contract} (from message {message_id}): {e}")
        await retry_telethon_call(bot_client.send_message(DEFAULT_ADMIN_ID, f"TTF bot error for contract `{contract}` (from message {message_id} in {chat_id}): {e}"))
        return
    if ttf_response and ttf_response.raw_text:
        logger.info(f"Parsing TFF bot output for contract {contract}: {ttf_response.raw_text[:100]}...")
        data = parse_tff_output(ttf_response.raw_text)
        token_name = extract_token_name_from_source(txt)
        if token_name == "unknown":
            logger.warning(f"Could not extract token name from source message {message_id} for contract {contract}. Using 'UNKNOWN'.")
            token_name = "UNKNOWN"
        new_text = build_new_template_with_emoji(token_name, contract, data.get('market_cap', 'N/A'),
                                     data.get('liquidity_status', 'N/A'), data.get('mint_status', 'N/A'))
        buttons = build_announcement_buttons(contract)
        target_channels = await get_channels('target')
        if not target_channels:
            logger.warning("No target channels configured to send new call announcement.")
            return
        announcement_id = None
        for target_channel_info in target_channels:
            target_channel_id = target_channel_info["channel_id"]
            try:
                logger.info(f"Sending new call announcement for '{token_name}' ({contract}) to target channel ID: {target_channel_id}.")
                msg = await retry_telethon_call(bot_client.send_message(
                    target_channel_id,
                    message=new_text,
                    file='https://media.giphy.com/media/v1.Y2lkPWVjZjA1ZTQ3amJmaWxtZzYwdVZhaWZvdzg2MDMwNTFpcndnc3A1dGljbnR4YjZidSZlcD12MV9naWZzX3NlYXJjaCZjdD1n/U4Go851LRU7icahyaj/giphy.gif',
                    buttons=buttons
                ))
                logger.info(f"New announcement sent to {target_channel_id}, message_id: {msg.id}.")
                # SADECE YENİ SİNYAL → X'E GÖNDER
                if "New GEM Landed!" in new_text:
                    x_text = build_x_text(token_name, contract, data.get('market_cap', 'N/A'),
                                          data.get('liquidity_status', 'N/A'), data.get('mint_status', 'N/A'))
                    post_to_x(x_text)
                else:
                    logger.info("Update mesajı → X'e gönderilmedi.")
                await retry_telethon_call(bot_client.send_message(
                    target_channel_id,
                    message=contract
                ))
                logger.info(f"Contract address '{contract}' sent as separate message to {target_channel_id}.")
                if announcement_id is None:
                    announcement_id = msg.id
            except Exception as e:
                logger.error(f"Error sending new call announcement or contract to target {target_channel_id} for contract {contract}: {e}")
        if announcement_id is not None:
            await add_token_mapping(token_name.lower(), contract, announcement_id)
            logger.info(f"Recorded mapping for '{token_name}' -> {contract} with announcement ID {announcement_id}.")
        else:
            await add_token_mapping(token_name.lower(), contract, None)
            logger.warning(f"Failed to send announcement to any target channels for '{token_name}' ({contract}). Mapping stored without announcement ID.")
    else:
        logger.warning(f"TTF bot did not return a message or message was empty for contract {contract} from message {message_id}. Cannot announce.")
        await retry_telethon_call(bot_client.send_message(DEFAULT_ADMIN_ID, f"TTF bot returned empty message for contract: `{contract}` (from message {message_id} in {chat_id}). Cannot announce."))

async def resume_after(minutes: int, admin_id: int):
    if minutes <= 0:
        return
    logger.info(f"Bot pausing for {minutes} minutes.")
    await asyncio.sleep(minutes * 60)
    current_status = await get_bot_setting('bot_status')
    if current_status == 'paused':
        await set_bot_setting('bot_status', 'running')
        logger.info("Bot resuming after pause.")
        try:
            await retry_telethon_call(bot_client.send_message(admin_id, "Resumed after pause."))
        except Exception as e:
            logger.error(f"Failed to send resume message to admin {admin_id}: {e}")
    else:
        logger.info(f"Bot status changed from 'paused' to '{current_status}' during pause period. Not automatically resuming.")

async def correct_last_announcement():
    targets = await get_channels('target')
    if not targets:
        logger.info("No target channels configured. Skipping last announcement correction.")
        return
    if not await user_client.is_user_authorized():
        logger.warning("User client not authorized. Cannot correct last announcement.")
        return
    logger.info("Starting last announcement correction task...")
    for ch in targets:
        try:
            if not user_client.is_connected():
                await user_client.connect()
            last_msgs = await retry_telethon_call(user_client.get_messages(ch["channel_id"], limit=1))
            if not last_msgs:
                logger.debug(f"No messages found in target channel {ch['channel_id']}.")
                continue
            last_msg = last_msgs[0]
            text = last_msg.message or ""
            if not text:
                logger.debug(f"Last message in target channel {ch['channel_id']} is empty. Skipping correction.")
                continue
            extracted_token = extract_token_name_from_source(text)
            if extracted_token == "unknown":
                logger.debug(f"Could not extract token from last message {last_msg.id} in channel {ch['channel_id']}. Skipping correction.")
                continue
            mapping_by_id = await get_mapping_by_announcement(last_msg.id)
            if mapping_by_id:
                old_token = mapping_by_id.get("token_name")
                contract_address = mapping_by_id.get("contract_address")
                if old_token and contract_address and extracted_token.lower() != old_token.lower():
                    await add_token_mapping(extracted_token.lower(), contract_address, last_msg.id)
                    logger.info("Corrected token mapping for message id %s in channel %s: '%s' -> '%s' (CA: %s)",
                                last_msg.id, ch['channel_id'], old_token, extracted_token, contract_address)
                elif old_token and contract_address:
                    logger.debug(f"Mapping for message ID {last_msg.id} in channel {ch['channel_id']} is already correct ('{old_token}').")
                else:
                    logger.warning(f"Mapping found by ID {last_msg.id} but missing token_name or contract_address in DB for channel {ch['channel_id']}.")
            else:
                if "New GEM Landed!" in text and extract_contract(text):
                    existing_mapping_by_token = await get_token_mapping(extracted_token.lower())
                    if existing_mapping_by_token and existing_mapping_by_token.get("announcement_message_id") is None:
                        await update_token_announcement(extracted_token.lower(), last_msg.id)
                        logger.info("Updated announcement ID for token '%s' to %s in channel %s.",
                                    extracted_token, last_msg.id, ch['channel_id'])
                else:
                    logger.debug(f"No existing mapping found by announcement ID {last_msg.id} and message doesn't look like a new announcement in channel {ch['channel_id']}.")
        except Exception as e:
            logger.error(f"Error correcting last announcement in channel {ch['channel_id']}: {e}")
    logger.info("Last announcement correction task finished.")

async def check_bot_admin() -> bool:
    target_channels = await get_channels('target')
    if not target_channels:
        logger.warning("No target channels configured. Cannot check bot admin status.")
        return False
    is_admin_in_all_targets = True
    me = await bot_client.get_me()
    for ch in target_channels:
        target_channel_id = ch["channel_id"]
        try:
            participant = await bot_client(GetParticipantRequest(
                channel=target_channel_id,
                participant=me.id
            ))
            if not isinstance(participant.participant, (ChannelParticipantAdmin, ChannelParticipantCreator)):
                logger.error(f"Bot lacks admin rights in target channel: {target_channel_id} ({ch.get('title', 'N/A')}). Required for posting.")
                is_admin_in_all_targets = False
            else:
                logger.info(f"Bot has admin rights in target channel: {target_channel_id} ({ch.get('title', 'N/A')}).")
        except Exception as e:
            logger.error(f"Error checking bot admin status in channel {target_channel_id} ({ch.get('title', 'N/A')}): {e}")
            is_admin_in_all_targets = False
    return is_admin_in_all_targets

async def get_admin_dashboard():
    loop = asyncio.get_running_loop()
    try:
        aff_response = await loop.run_in_executor(None, requests.get, "https://www.affirmations.dev")
        aff_response.raise_for_status()
        aff = aff_response.json().get('affirmation', '')
    except Exception as e:
        logger.error(f"Error fetching affirmation for dashboard: {e}")
        aff = "Could not fetch affirmation."
    try:
        quote_response = await loop.run_in_executor(None, requests.get, "https://zenquotes.io/api/random")
        quote_response.raise_for_status()
        q = quote_response.json()[0]
        mot = f"{q['q']} — {q['a']}"
    except Exception as e:
        logger.error(f"Error fetching motivation quote for dashboard: {e}")
        mot = "Could not fetch motivation."
    bot_status = (await get_bot_setting("bot_status")) or "running"
    x_posting_status = (await get_bot_setting("x_posting_enabled")) or "enabled"
    return (
        "*Hey Boss!*\n\n"
        f"*Bot Status:* `{bot_status.capitalize()}`\n"
        f"*X Posting:* `{x_posting_status.capitalize()}`\n\n"
        f"*Affirmation:* {aff}\n"
        f"*Motivation:* {mot}\n\n"
        "What would you like to do?"
    )

async def build_admin_keyboard():
    x_posting_status = await get_bot_setting("x_posting_enabled") or "enabled"
    x_posting_button = (
        Button.inline("Pause X Posting", b"admin_pause_x_posting")
        if x_posting_status == "enabled"
        else Button.inline("Start X Posting", b"admin_start_x_posting")
    )
    return [
        [Button.inline("Start Bot", b"admin_start"),
         Button.inline("Pause Bot", b"admin_pause"),
         Button.inline("Stop Bot", b"admin_stop")],
        [Button.inline("Admins", b"admin_admins"),
         Button.inline("Targets", b"admin_targets"),
         Button.inline("Sources", b"admin_sources")],
        [Button.inline("Update GIF", b"admin_update_gif"),
         x_posting_button]
    ]

async def main():
    logger.info("Starting main bot asynchronous tasks...")
    await init_db()
    logger.info("Database initialization complete.")
    admins = await get_admins()
    if DEFAULT_ADMIN_ID not in admins:
        await add_admin(DEFAULT_ADMIN_ID, 'Default', is_default=True)
        logger.info(f"Default admin {DEFAULT_ADMIN_ID} ensured.")
    else:
        logger.info(f"Default admin {DEFAULT_ADMIN_ID} already exists.")
    src_ch = await get_channels('source')
    if not any(c['channel_id'] == DEFAULT_SOURCE_CHANNEL['channel_id'] for c in src_ch):
        await add_channel(**DEFAULT_SOURCE_CHANNEL)
        logger.info("Default source channel ensured.")
    else:
        logger.info("Default source channel already exists.")
    tgt_ch = await get_channels('target')
    if not any(c['channel_id'] == DEFAULT_TARGET_CHANNEL['channel_id'] for c in tgt_ch):
        await add_channel(**DEFAULT_TARGET_CHANNEL)
        logger.info("Default target channel ensured.")
    else:
        logger.info("Default target channel already exists.")
    for k, v in DEFAULT_BOT_SETTINGS.items():
        db_val = await asyncio.to_thread(get_bot_setting_sync, k)
        if db_val is None:
            await set_bot_setting(k, v)
            logger.info(f"Default setting '{k}' ensured.")
        else:
            logger.debug(f"Setting '{k}' already exists in DB.")
    try:
        await bot_client.start(bot_token=BOT_TOKEN)
        logger.info("Bot client started and connected.")
    except Exception as e:
        logger.critical(f"Failed to start bot client: {e}. Please check BOT_TOKEN environment variable.")
        raise
    await user_client.connect()
    if not await user_client.is_user_authorized():
        logger.warning("User client not authorized. Please visit /login to authorize.")
    else:
        logger.info("User client authorized.")
        asyncio.create_task(correct_last_announcement())
        logger.info("Started background task: correct_last_announcement.")
    if not await check_bot_admin():
        logger.error("Bot lacks admin rights in one or more target channels. Posting might fail.")
    else:
        logger.info("Bot has admin rights in all configured target channels.")
    logger.info("Bot is now running in the asyncio event loop.")
    await asyncio.Event().wait()

if __name__ == '__main__':
    from hypercorn.asyncio import serve
    from hypercorn.config import Config
    from asgiref.wsgi import WsgiToAsgi
    asgi_app = WsgiToAsgi(app)
    config = Config()
    config.bind = [f"0.0.0.0:{int(os.environ.get('PORT', '5000'))}"]
    config.accesslog = '-'
    config.errorlog = '-'
    def start_self_ping():
        def ping():
            try:
                port = int(os.environ.get('PORT', '5000'))
                response = requests.get(f"http://localhost:{port}/health")
                if response.status_code == 200:
                    logger.info("Self-ping OK")
                else:
                    logger.warning(f"Self-ping returned status code {response.status_code}")
            except requests.exceptions.ConnectionError as e:
                logger.error(f"Self-ping failed: ConnectionError - {e}")
            except Exception as e:
                logger.error(f"Self-ping failed: {e}")
            threading.Timer(4*60, ping).start()
        time.sleep(5)
        ping()
    threading.Thread(target=start_self_ping, daemon=True).start()
    logger.info(f"Starting Hypercorn server on {config.bind[0]} and running bot asyncio loop.")
    async def runner():
        server_task = asyncio.create_task(serve(asgi_app, config))
        logger.info("Hypercorn server task created.")
        bot_task = asyncio.create_task(main())
        logger.info("Main bot task created.")
        await asyncio.gather(server_task, bot_task)
    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user. Shutting down.")
    except Exception as e:
        logger.critical(f"Unhandled exception in main runner: {e}")
