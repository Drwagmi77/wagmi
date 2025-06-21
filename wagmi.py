import re
import asyncio
import logging
import os
import threading
import time
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

DB_NAME = os.environ.get("DB_NAME", "wagmi_82kq")
DB_USER = os.environ.get("DB_USER", "wagmi_82kq_user")
DB_PASS = os.environ.get("DB_PASS", "ROPvICF4rzRBA5nIGoLzweJMJYOXUKWo")
DB_HOST = os.environ.get("DB_HOST", "dpg-d0dojsmuk2gs73dbrcbg-a.oregon-postgres.render.com")
DB_PORT = os.environ.get("DB_PORT", "5432")
API_ID = int(os.environ.get("API_ID", 28146969))
API_HASH = os.environ.get("API_HASH", '5c8acdf2a7358589696af178e2319443')
BOT_TOKEN = os.environ.get("BOT_TOKEN", '7834122356:AAGszZL-bgmggu_77aH0_lszBqe-Rei25_w')
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24).hex())

app = Flask(__name__)
app.secret_key = SECRET_KEY

bot_client = TelegramClient('lion', API_ID, API_HASH)
user_client = TelegramClient('monkey', API_ID, API_HASH)

def get_connection():
    try:
        return psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
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
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    last_name TEXT,
                    lang TEXT,
                    is_default BOOLEAN DEFAULT FALSE
                );
            """)
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

DEFAULT_ADMIN_ID = 1116670397  # Senin Telegram ID'n
DEFAULT_SOURCE_CHANNEL = {
    "channel_id": -1001998961899,
    "username": "@gem_tools_calls",
    "title": "💎 GemTools 💎 Calls",
    "channel_type": "source"
}
DEFAULT_TARGET_CHANNEL = {
    "channel_id": -1002405509240,
    "username": "",
    "title": "Wagmi Vip ☢",
    "channel_type": "target"
}
DEFAULT_BOT_SETTINGS = {
    "bot_status": "running",
    "custom_gif": "https://dl.dropbox.com/scl/fi/u6r3x30cno1ebmvbpu5k1/video.mp4?rlkey=ytfk8qkdpwwm3je6hjcqgd89s&st=vxjkqe6c?dl=1"
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
logger.info("🔥 Logging setup complete. Bot is starting...")

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
        logger.debug("Empty message received for token extraction; returning 'unknown'.")
        return "unknown"
    for line in lines:
        match = re.search(r"\$([A-Za-z0-9_]+)", line)
        if match:
            token = match.group(1)
            logger.debug(f"Token extracted: '{token}' from line: '{line}'")
            return token
    logger.debug("No valid token ($WORD) found in the message; returning 'unknown'.")
    return "unknown"

def parse_tff_output(text: str) -> dict:
    data = {}
    data["mint_status"] = (re.search(r"🌿\s*Mint:\s*(\w+)", text) or [None, "N/A"])[1]
    data["liquidity_status"] = (re.search(r"Liq:\s*\$?([\d\.,KkMmBb]+)", text) or [None, "N/A"])[1]
    data["market_cap"] = (re.search(r"MC:\s*\$?([\d\.,KkMmBb]+)", text) or [None, "N/A"])[1]
    logger.debug("✅ Parsed TFF output.")
    return data

def build_new_template(token_name, contract, market_cap, liquidity_status, mint_status):
    return (
        "🚀 *New 💎 GEM Landed!* 💎\n\n"
        f"💰 ${token_name.upper()}\n\n"
        f"📊 *Market Cap:* {market_cap}\n"
        f"💦 *Liquidity:* {liquidity_status}\n"
        f"🔥 *Minting:* {mint_status}\n\n"
        f"🔗 *Contract:* `{contract}`\n"
        "🌐 *Network:* #SOL"
    )

def build_update_template(token_name, new_mc, prof):
    return (
        f"🚀 *Early GEM Hunters Winning Big!* 💎\n\n"
        f"💵 *{token_name.upper()}* Market Cap: {new_mc} 💎\n"
        f"🔥 {prof} & STILL RUNNING! 💎\n\n"
        "Stay sharp for the next hidden GEM! 💎"
    )

def build_announcement_buttons(contract):
    return [
        [Button.url("📈 Chart", f"https://dexscreener.com/solana/{contract}"),
         Button.url("🛡 Trojan", "https://t.me/solana_trojanbot?start=r-gemwagmi")],
        [Button.url("🐉 Soul", "https://t.me/soul_sniper_bot?start=9FDbnU6TsKGX"),
         Button.url("🤖 MEVX", f"https://t.me/MevxTradingBot?start={contract}")],
        [Button.url("📊 Algora", f"https://t.me/algoratradingbot?start=r-tff-{contract}")],
        [Button.url("🚀 Trojan N", f"https://t.me/nestor_trojanbot?start=r-shielzuknf5b-{contract}"),
         Button.url("🔗 GMGN", f"https://t.me/GMGN_sol03_bot?start=CcJ5M3wBy35JHLp4csmFF8QyxdeHuKasPqKQeFa1TzLC")]
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
            logger.info(f"➡ Sent login code request to {phone}")
            return redirect('/submit-code')
        except Exception as e:
            logger.error(f"❌ Error sending login code to {phone}: {e}")
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
            logger.info(f"✅ Logged in user-client for {phone}")
            session.pop('phone', None)
            return "<p>Login successful! You can close this tab.</p>"
        except Exception as e:
            logger.error(f"❌ Login failed for {phone}: {e}")
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
        return await event.answer("❌ Not authorized")

    data = event.data.decode()
    logger.info(f"Admin {uid} triggered callback: {data}")

    try:
        if data == 'admin_home':
            return await event.edit(await get_admin_dashboard(),
                                   buttons=build_admin_keyboard(), link_preview=False)
        if data == 'admin_start':
            await set_bot_setting("bot_status", "running")
            await event.answer('▶ Bot started')
            return await event.edit(await get_admin_dashboard(),
                                   buttons=build_admin_keyboard(), link_preview=False)
        if data == 'admin_pause':
            pending_input[uid] = {'action': 'pause'}
            kb = [[Button.inline("🔙 Back", b"admin_home")]]
            return await event.edit("⏸ *Pause Bot*\n\nHow many minutes should I pause for?",
                                   buttons=kb, link_preview=False)
        if data == 'admin_stop':
            await set_bot_setting("bot_status", "stopped")
            await event.answer('🛑 Bot stopped')
            return await event.edit("🛑 *Bot has been shut down.*",
                                   buttons=[[Button.inline("🔄 Restart Bot (set to running)", b"admin_start")],
                                            [Button.inline("🔙 Back", b"admin_home")]],
                                   link_preview=False)
        if data == 'admin_admins':
            admins = await get_admins()
            kb = [
                [Button.inline("➕ Add Admin", b"admin_add_admin")],
            ]
            removable_admins = {aid: info for aid, info in admins.items() if aid != DEFAULT_ADMIN_ID and not info.get("is_default")}
            if removable_admins:
                kb.append([Button.inline("🗑 Remove Admin", b"admin_show_remove_admins")])
            kb.append([Button.inline("🔙 Back", b"admin_home")])
            return await event.edit("👤 *Manage Admins*", buttons=kb, link_preview=False)
        if data == 'admin_show_remove_admins':
            admins = await get_admins()
            kb = []
            for aid, info in admins.items():
                if aid != DEFAULT_ADMIN_ID and not info.get("is_default"):
                    kb.append([Button.inline(f"{info.get('first_name', 'N/A')} ({aid})", b"noop"),
                              Button.inline("❌ Remove", f"remove_admin:{aid}".encode())])
            kb.append([Button.inline("🔙 Back", b"admin_admins")])
            if not kb:
                return await event.edit("🗑 *No more removable admins.*",
                                       buttons=[[Button.inline("🔙 Back", b"admin_admins")]], link_preview=False)
            return await event.edit("🗑 *Select Admin to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_add_admin':
            pending_input[uid] = {'action': 'confirm_add_admin'}
            return await event.edit("➕ *Add Admin*\n\nSend me the user ID to add:",
                                   buttons=[[Button.inline("🔙 Back", b"admin_admins")]], link_preview=False)
        if data.startswith('remove_admin:'):
            aid = int(data.split(':')[1])
            await remove_admin(aid)
            await event.answer("✅ Admin removed", alert=True)
            admins = await get_admins()
            kb = []
            for admin_id, info in admins.items():
                if admin_id != DEFAULT_ADMIN_ID and not info.get("is_default"):
                    kb.append([Button.inline(f"{info.get('first_name', 'N/A')} ({admin_id})", b"noop"),
                              Button.inline("❌ Remove", f"remove_admin:{admin_id}".encode())])
            kb.append([Button.inline("🔙 Back", b"admin_admins")])
            if not kb:
                return await event.edit("🗑 *No more removable admins.*",
                                       buttons=[[Button.inline("🔙 Back", b"admin_admins")]], link_preview=False)
            return await event.edit("🗑 *Select Admin to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_targets':
            kb = [
                [Button.inline("➕ Add Target", b"admin_add_target")],
            ]
            targets = await get_channels('target')
            if targets:
                kb.append([Button.inline("🗑 Remove Target", b"admin_show_remove_targets")])
            kb.append([Button.inline("🔙 Back", b"admin_home")])
            return await event.edit("📺 *Manage Target Channels*", buttons=kb, link_preview=False)
        if data == 'admin_show_remove_targets':
            targets = await get_channels('target')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("❌ Remove", f"remove_target:{ch['channel_id']}".encode())]
                for ch in targets
            ]
            kb.append([Button.inline("🔙 Back", b"admin_targets")])
            return await event.edit("🗑 *Select Target Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_add_target':
            pending_input[uid] = {'action': 'confirm_add_target'}
            return await event.edit("➕ *Add Target Channel*\n\nSend me the channel ID (e.g., `-1001234567890`) or @username to add:",
                                   buttons=[[Button.inline("🔙 Back", b"admin_targets")]], link_preview=False)
        if data.startswith('remove_target:'):
            tid = int(data.split(':')[1])
            await remove_channel(tid, "target")
            await event.answer("✅ Target channel removed", alert=True)
            targets = await get_channels('target')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("❌ Remove", f"remove_target:{ch['channel_id']}".encode())]
                for ch in targets
            ]
            kb.append([Button.inline("🔙 Back", b"admin_targets")])
            if not targets:
                return await event.edit("🗑 *No more target channels.*",
                                       buttons=[[Button.inline("🔙 Back", b"admin_targets")]], link_preview=False)
            return await event.edit("🗑 *Select Target Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_sources':
            kb = [
                [Button.inline("➕ Add Source", b"admin_add_source")],
            ]
            sources = await get_channels('source')
            if sources:
                kb.append([Button.inline("🗑 Remove Source", b"admin_show_remove_sources")])
            kb.append([Button.inline("🔙 Back", b"admin_home")])
            return await event.edit("📡 *Manage Source Channels*", buttons=kb, link_preview=False)
        if data == 'admin_show_remove_sources':
            sources = await get_channels('source')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("❌ Remove", f"remove_source:{ch['channel_id']}".encode())]
                for ch in sources
            ]
            kb.append([Button.inline("🔙 Back", b"admin_sources")])
            return await event.edit("🗑 *Select Source Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_add_source':
            pending_input[uid] = {'action': 'confirm_add_source'}
            return await event.edit("➕ *Add Source Channel*\n\nSend me the channel ID (e.g., `-1001234567890`) or @username to add:",
                                   buttons=[[Button.inline("🔙 Back", b"admin_sources")]], link_preview=False)
        if data.startswith('remove_source:'):
            sid = int(data.split(':')[1])
            await remove_channel(sid, "source")
            await event.answer("✅ Source channel removed", alert=True)
            sources = await get_channels('source')
            kb = [
                [Button.inline(ch.get('title', 'N/A'), b"noop"),
                 Button.inline("❌ Remove", f"remove_source:{ch['channel_id']}".encode())]
                for ch in sources
            ]
            kb.append([Button.inline("🔙 Back", b"admin_sources")])
            if not sources:
                return await event.edit("🗑 *No more source channels.*",
                                       buttons=[[Button.inline("🔙 Back", b"admin_sources")]], link_preview=False)
            return await event.edit("🗑 *Select Source Channel to Remove*", buttons=kb, link_preview=False)
        if data == 'admin_update_gif':
            pending_input[uid] = {'action': 'confirm_update_gif'}
            return await event.edit("🎬 *Update GIF*\n\nSend me the new GIF URL:",
                                   buttons=[[Button.inline("🔙 Back", b"admin_home")]], link_preview=False)
    except Exception as e:
        logger.error(f"Error in admin callback handler for user {uid}, data {data}: {e}")
        await event.answer("An error occurred.", alert=True)
        try:
            await event.edit(await get_admin_dashboard(), buttons=build_admin_keyboard(),
                            link_preview=False)
        except Exception:
            pass

    await event.answer("✅ Done" if not event.answered else "")

@bot_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
async def admin_private_handler(event):
    uid = event.sender_id
    admins = await get_admins()
    if uid not in admins:
        logger.warning(f"Unauthorized private message from user ID: {uid}")
        return

    txt = event.raw_text.strip()
    logger.info(f"Admin {uid} sent private message: {txt}")

    if uid in pending_input:
        act = pending_input.pop(uid)['action']
        try:
            if act == 'pause':
                try:
                    m = int(txt)
                    if m <= 0: raise ValueError("Must be positive")
                except ValueError:
                    await event.reply("⚠ Please send a valid positive number of minutes.")
                    pending_input[uid] = {'action': 'pause'}
                    return
                await set_bot_setting("bot_status", "paused")
                await event.reply(f"⏸ Paused for {m} minutes.")
                asyncio.create_task(resume_after(m, uid))
                await retry_telethon_call(bot_client.send_message(uid, await get_admin_dashboard(),
                                                                buttons=build_admin_keyboard(), link_preview=False))
                return
            if act == 'confirm_add_admin':
                try:
                    new_id = int(txt)
                    if new_id <= 0: raise ValueError("Must be positive")
                except ValueError:
                    await event.reply("⚠ Invalid user ID. Please send a positive integer ID.")
                    pending_input[uid] = {'action': 'confirm_add_admin'}
                    return
                current_admins = await get_admins()
                if new_id in current_admins:
                    await event.reply
