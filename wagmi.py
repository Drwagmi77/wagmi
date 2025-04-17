import re
import asyncio
import logging
import os
import threading
import time
from datetime import datetime

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.tl.functions.channels import GetParticipantRequest
from flask import Flask, jsonify  # <-- Flask import

# ===== DATABASE CONFIGURATION =====
DB_NAME = "wagmi"
DB_USER = "wagmi_user"
DB_PASS = "LP68srOcSsau0NmPEmPrgSkxnuj8DF9l"
DB_HOST = "dpg-cvrtatq4d50c73d5n5f0-a.oregon-postgres.render.com"
DB_PORT = "5432"

def get_connection():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        host=DB_HOST,
        port=DB_PORT
    )

# ----- Synchronous Database Functions -----
def init_db_sync():
    """Create the necessary tables using psycopg2 (synchronous version)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Table for admins
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY,
                    first_name TEXT NOT NULL,
                    last_name TEXT,
                    lang TEXT,
                    is_default BOOLEAN DEFAULT FALSE
                );
            """)
            # Table for channels
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id SERIAL PRIMARY KEY,
                    channel_id BIGINT NOT NULL,
                    username TEXT,
                    title TEXT,
                    channel_type TEXT CHECK (channel_type IN ('source','target'))
                );
            """)
            # Table for processed messages (composite primary key)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    chat_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                );
            """)
            # Table for processed contracts
            cur.execute("""
                CREATE TABLE IF NOT EXISTS processed_contracts (
                    contract_address TEXT PRIMARY KEY
                );
            """)
            # Table for token mappings (stores announcement_message_id for threaded updates)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS token_mappings (
                    token_name TEXT PRIMARY KEY,
                    contract_address TEXT NOT NULL,
                    announcement_message_id BIGINT
                );
            """)
            # Table for bot settings
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT
                );
            """)
        conn.commit()
    finally:
        conn.close()

def get_admins_sync():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM admins")
            rows = cur.fetchall()
            return {row["user_id"]: row for row in rows}
    finally:
        conn.close()

def add_admin_sync(user_id, first_name, last_name="", lang="en", is_default=False):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO admins (user_id, first_name, last_name, lang, is_default)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET first_name=%s, last_name=%s, lang=%s, is_default=%s;
            """, (user_id, first_name, last_name, lang, is_default,
                  first_name, last_name, lang, is_default))
            conn.commit()
    finally:
        conn.close()

def remove_admin_sync(user_id):
    admins = get_admins_sync()
    if admins.get(user_id, {}).get("is_default"):
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM admins WHERE user_id = %s", (user_id,))
            conn.commit()
    finally:
        conn.close()

def get_channels_sync(channel_type):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM channels WHERE channel_type = %s", (channel_type,))
            rows = cur.fetchall()
            return [row for row in rows]
    finally:
        conn.close()

def add_channel_sync(channel_id, username, title, channel_type):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channels (channel_id, username, title, channel_type)
                VALUES (%s, %s, %s, %s)
            """, (channel_id, username, title, channel_type))
            conn.commit()
    finally:
        conn.close()

def remove_channel_sync(channel_id, channel_type):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM channels WHERE channel_id = %s AND channel_type = %s", (channel_id, channel_type))
            conn.commit()
    finally:
        conn.close()

def is_message_processed_sync(chat_id, message_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM processed_messages WHERE chat_id = %s AND message_id = %s;", (chat_id, message_id))
            return cur.fetchone() is not None
    finally:
        conn.close()

def record_processed_message_sync(chat_id, message_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_messages (chat_id, message_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
            """, (chat_id, message_id))
            conn.commit()
    finally:
        conn.close()

def is_contract_processed_sync(contract_address):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM processed_contracts WHERE contract_address = %s;", (contract_address,))
            return cur.fetchone() is not None
    finally:
        conn.close()

def record_processed_contract_sync(contract_address):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO processed_contracts (contract_address)
                VALUES (%s)
                ON CONFLICT DO NOTHING;
            """, (contract_address,))
            conn.commit()
    finally:
        conn.close()

def get_token_mapping_sync(token_name):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM token_mappings WHERE token_name = %s;", (token_name,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def add_token_mapping_sync(token_name, contract_address, announcement_message_id=None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO token_mappings (token_name, contract_address, announcement_message_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (token_name) DO UPDATE
                SET contract_address = %s, announcement_message_id = COALESCE(%s, token_mappings.announcement_message_id);
            """, (token_name, contract_address, announcement_message_id,
                  contract_address, announcement_message_id))
            conn.commit()
    finally:
        conn.close()

def update_token_announcement_sync(token_name, announcement_message_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE token_mappings SET announcement_message_id = %s WHERE token_name = %s;", (announcement_message_id, token_name))
            conn.commit()
    finally:
        conn.close()

def get_mapping_by_announcement_sync(announcement_message_id):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM token_mappings WHERE announcement_message_id = %s;", (announcement_message_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()

def get_bot_setting_sync(setting):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT setting_value FROM bot_settings WHERE setting_key = %s;", (setting,))
            row = cur.fetchone()
            if row:
                return row["setting_value"]
            return DEFAULT_BOT_SETTINGS.get(setting)
    finally:
        conn.close()

def set_bot_setting_sync(setting, value):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO bot_settings (setting_key, setting_value)
                VALUES (%s, %s)
                ON CONFLICT (setting_key) DO UPDATE SET setting_value = %s;
            """, (setting, value, value))
            conn.commit()
    finally:
        conn.close()

# ----- Async Wrappers using asyncio.to_thread -----
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
    return await asyncio.to_thread(get_bot_setting_sync, setting)

async def set_bot_setting(setting, value):
    await asyncio.to_thread(set_bot_setting_sync, setting, value)

# ===== DEFAULT VALUES & LOGGING =====
DEFAULT_ADMIN_ID = 6489451767

# Updated Default Source Channel using gem_tools_calls
DEFAULT_SOURCE_CHANNEL = {
    "channel_id": -1001998961899,
    "username": "@gem_tools_calls",
    "title": "💎 GemTools 💎 Calls",
    "channel_type": "source"
}

# Updated Default Target Channel using Wagmi Vip
DEFAULT_TARGET_CHANNEL = {
    "channel_id": -1002405509240,
    "username": None,  # No username provided
    "title": "Wagmi Vip ☢️",
    "channel_type": "target"
}

DEFAULT_BOT_SETTINGS = {
    "bot_status": "running",
    "custom_gif": "https://dl.dropboxusercontent.com/scl/fi/u6r3x30cno1ebmvbpu5k1/video.mp4?rlkey=ytfk8qkdpwwm3je6hjcqgd89s&st=vxjkqe6s"
}

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "bot_logs.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_file, mode='a'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
logger.info("🔥 Logging setup complete. Bot is starting...")

# ===== TELEGRAM CONFIGURATION =====
# Using 'monkey' as user session and 'lion' as bot session
api_id = 28885685
api_hash = 'c24e850a947c003557f614d6b34035d9'
bot_token = '7886946660:AAGXvcV7FS5uFduFUVGGzwwWg1kfua_Pzco'
user_session = 'monkey'
bot_session = 'lion'

bot_client = TelegramClient(bot_session, api_id, api_hash)
user_client = TelegramClient(user_session, api_id, api_hash)

# ===== HELPER FUNCTIONS FOR TOKEN EXTRACTION AND TEMPLATES =====
def extract_contract(text: str) -> str | None:
    m = re.findall(r"\b[A-Za-z0-9]{32,50}\b", text)
    return m[0] if m else None

# Revised token extraction with logging
def extract_token_name_from_source(text: str) -> str:
    """
    Iterates over all lines in the source message and returns the first occurrence
    of a token name defined as a word immediately following '$'. If no token is found, returns "unknown".
    """
    lines = text.strip().splitlines()
    if not lines:
        logger.info("Empty message received; returning 'unknown'.")
        return "unknown"

    for line in lines:
        match = re.search(r"\$([A-Za-z0-9_]+)", line)
        if match:
            token = match.group(1)
            logger.info(f"Token extracted: '{token}' from line: '{line}'")
            return token

    logger.info("No valid token found in the message; returning 'unknown'.")
    return "unknown"

# Parse TFF output for market cap, liquidity, and mint status.
def parse_tff_output(text: str) -> dict:
    data = {}
    data["mint_status"]      = (re.search(r"🌿\s*Mint:\s*(\w+)", text) or [None, "N/A"])[1]
    data["liquidity_status"] = (re.search(r"Liq:\s*\$?([\d\.Kk]+)", text) or [None, "N/A"])[1]
    data["market_cap"]       = (re.search(r"MC:\s*\$?([\d\.Kk]+)", text) or [None, "N/A"])[1]
    logger.info("✅ Parsed TTF output.")
    return data

def build_new_template(token_name, contract, market_cap, liquidity_status, mint_status):
    return (
        "🚀 *New 💎 GEM Landed!* 🚀\n\n"
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
        f"💵 *{token_name.upper()}* Market Cap: {new_mc} 📈\n"
        f"🔥 {prof} & STILL RUNNING! 🔥\n\n"
        "Stay sharp for the next hidden GEM! 👀"
    )

def build_announcement_buttons(contract):
    return [
        [Button.url("📈 Chart", f"https://dexscreener.com/solana/{contract}"),
         Button.url("🛡️ Trojan", f"https://t.me/solana_trojanbot?start=r-ttf-{contract}")],
        [Button.url("🐉 Soul", f"https://t.me/soul_sniper_bot?start=4U4QhnwlCBxS_{contract}"),
         Button.url("🤖 MEVX", f"https://t.me/MevxTradingBot?start={contract}")],
        [Button.url("📊 Algora", f"https://t.me/algoratradingbot?start=r-tff-{contract}")],
        [Button.url("🚀 Trojan N", f"https://t.me/nestor_trojanbot?start=r-shielzuknf5b-{contract}"),
         Button.url("🔗 GMGN", "https://t.me/GMGN_sol03_bot?start=CcJ5M3wBy35JHLp4csmFF8QyxdeHuKasPqKQeFa1TzLC")]
    ]

# Correct last announcement mapping function.
async def correct_last_announcement():
    targets = await get_channels('target')
    if not targets:
        return

    for ch in targets:
        last_msgs = await user_client.get_messages(ch["channel_id"], limit=1)
        if not last_msgs:
            continue
        last_msg = last_msgs[0]
        text = last_msg.message or ""
        if not text:
            continue

        # Extract token name from the announcement message (if applicable)
        extracted_token = extract_token_name_from_source(text)
        mapping = await get_mapping_by_announcement(last_msg.id)
        if mapping:
            old_token = mapping["token_name"]
            if extracted_token and extracted_token != old_token:
                await add_token_mapping(extracted_token.lower(), mapping["contract_address"], last_msg.id)
                logger.info(
                    "Corrected token mapping for message id %s: '%s' -> '%s'",
                    last_msg.id, old_token, extracted_token
                )
        else:
            logger.info("No mapping found for announcement message id %s", last_msg.id)

# ===== ADMIN DASHBOARD & KEYBOARD =====
async def get_admin_dashboard():
    try:
        aff = requests.get("https://www.affirmations.dev").json().get('affirmation', '')
    except:
        aff = ""
    try:
        q = requests.get("https://zenquotes.io/api/random").json()[0]
        mot = f"{q['q']} — {q['a']}"
    except:
        mot = ""
    bot_status = (await get_bot_setting("bot_status")) or "running"
    return (
        "👋 *Hey Boss!* 👋\n\n"
        f"🤖 *Bot Status:* `{bot_status.capitalize()}`\n\n"
        f"💖 *Affirmation:* {aff}\n"
        f"🚀 *Motivation:* {mot}\n\n"
        "What would you like to do?"
    )

def build_admin_keyboard():
    return [
        [Button.inline("▶️ Start Bot", b"admin_start"),
         Button.inline("⏸️ Pause Bot", b"admin_pause"),
         Button.inline("🛑 Stop Bot", b"admin_stop")],
        [Button.inline("👤 Admins", b"admin_admins"),
         Button.inline("📺 Targets", b"admin_targets"),
         Button.inline("📡 Sources", b"admin_sources")],
        [Button.inline("🎬 Update GIF", b"admin_update_gif")]
    ]

pending_input = {}

# ===== CALLBACK HANDLER =====
@bot_client.on(events.CallbackQuery)
async def admin_callback_handler(event):
    uid = event.sender_id
    admins = await get_admins()
    if uid not in admins:
        return await event.answer("❌ Not authorized")
    data = event.data.decode()
    if data == 'admin_home':
        return await event.edit(await get_admin_dashboard(), buttons=build_admin_keyboard(), link_preview=False)
    if data == 'admin_start':
        await set_bot_setting("bot_status", "running")
        await event.answer('▶️ Bot started')
        return await event.edit(await get_admin_dashboard(), buttons=build_admin_keyboard())
    if data == 'admin_pause':
        kb = [[Button.inline("🔙 Back", b"admin_home")]]
        pending_input[uid] = {'action': 'pause'}
        return await event.edit("⏸ *Pause Bot*\n\nHow many minutes should I pause for?", buttons=kb)
    if data == 'admin_stop':
        await set_bot_setting("bot_status", "stopped")
        await event.answer('🛑 Bot stopped')
        return await event.edit("🛑 *Bot has been shut down.*", buttons=[[Button.inline("🔙 Back", b"admin_home")]])
    if data == 'admin_admins':
        kb = [
            [Button.inline("➕ Add Admin", b"admin_add_admin")],
            [Button.inline("🗑️ Remove Admin", b"admin_remove_admin")],
            [Button.inline("🔙 Back", b"admin_admins")]
        ]
        return await event.edit("👤 *Manage Admins*", buttons=kb)
    if data == 'admin_add_admin':
        pending_input[uid] = {'action': 'confirm_add_admin'}
        return await event.edit("➕ *Add Admin*\n\nSend me the user ID to add:", buttons=[[Button.inline("🔙 Back", b"admin_admins")]])
    if data == 'admin_remove_admin':
        admins = await get_admins()
        kb = []
        for aid, info in admins.items():
            if aid == DEFAULT_ADMIN_ID or info.get("is_default"):
                kb.append([Button.inline(f"{info['first_name']} ({aid})", "noop")])
            else:
                kb.append([
                    Button.inline(f"{info['first_name']} ({aid})", "noop"),
                    Button.inline("❌ Remove", f"remove_admin:{aid}")
                ])
        kb.append([Button.inline("🔙 Back", b"admin_admins")])
        return await event.edit("🗑️ *Remove Admin*", buttons=kb)
    if data == 'admin_targets':
        kb = [
            [Button.inline("➕ Add Target", b"admin_add_target")],
            [Button.inline("🗑️ Remove Target", b"admin_remove_target")],
            [Button.inline("🔙 Back", b"admin_home")]
        ]
        return await event.edit("📺 *Manage Targets*", buttons=kb)
    if data == 'admin_add_target':
        pending_input[uid] = {'action': 'confirm_add_target'}
        return await event.edit("➕ *Add Target*\n\nSend me the channel ID to add:", buttons=[[Button.inline("🔙 Back", b"admin_targets")]])
    if data == 'admin_remove_target':
        targets = await get_channels('target')
        kb = [*[Button.inline(ch['title'], "noop") + [Button.inline("❌ Remove", f"remove_target:{ch['channel_id']}")] for ch in targets],
              [Button.inline("🔙 Back", b"admin_targets")]]
        return await event.edit("🗑️ *Remove Target*", buttons=kb)
    if data == 'admin_sources':
        kb = [
            [Button.inline("➕ Add Source", b"admin_add_source")],
            [Button.inline("🗑️ Remove Source", b"admin_remove_source")],
            [Button.inline("🔙 Back", b"admin_home")]
        ]
        return await event.edit("📡 *Manage Sources*", buttons=kb)
    if data == 'admin_add_source':
        pending_input[uid] = {'action': 'confirm_add_source'}
        return await event.edit("➕ *Add Source*\n\nSend me the channel ID to add:", buttons=[[Button.inline("🔙 Back", b"admin_sources")]])
    if data == 'admin_remove_source':
        sources = await get_channels('source')
        kb = [*[Button.inline(ch['title'], "noop") + [Button.inline("❌ Remove", f"remove_source:{ch['channel_id']}")] for ch in sources],
              [Button.inline("🔙 Back", b"admin_sources")]]
        return await event.edit("🗑️ *Remove Source*", buttons=kb)
    if data == 'admin_update_gif':
        pending_input[uid] = {'action': 'confirm_update_gif'}
        return await event.edit("🎬 *Update GIF*\n\nSend me the new GIF URL:", buttons=[[Button.inline("🔙 Back", b"admin_home")]])
    if data.startswith('remove_admin:'):
        aid = int(data.split(':')[1])
        await remove_admin(aid)
        return await event.answer("✅ Admin removed", alert=True)
    if data.startswith('remove_target:'):
        tid = int(data.split(':')[1])
        await remove_channel(tid, "target")
        return await event.answer("✅ Target removed", alert=True)
    if data.startswith('remove_source:'):
        sid = int(data.split(':')[1])
        await remove_channel(sid, "source")
        return await event.answer("✅ Source removed", alert=True)
    await event.answer("❓ Unknown command")

# ===== PRIVATE MESSAGE HANDLER =====
@bot_client.on(events.NewMessage)
async def admin_private_handler(event):
    if not event.is_private:
        return
    uid = event.sender_id
    admins = await get_admins()
    if uid not in admins:
        return
    txt = event.raw_text.strip()
    if uid in pending_input:
        act = pending_input.pop(uid)['action']
        if act == 'pause':
            try:
                m = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Please send a valid number of minutes.")
            await set_bot_setting("bot_status", "paused")
            await bot_client.send_message(uid, f"⏸️ Paused for {m} minutes.")
            asyncio.create_task(resume_after(m, uid))
            return await bot_client.send_message(uid, await get_admin_dashboard(), buttons=build_admin_keyboard())
        if act == 'confirm_add_admin':
            try:
                new_id = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Invalid user ID.")
            await add_admin(new_id, f"ID:{new_id}")
            await bot_client.send_message(uid, f"✅ Admin {new_id} added.")
            return await bot_client.send_message(uid, await get_admin_dashboard(), buttons=build_admin_keyboard())
        if act == 'confirm_add_target':
            try:
                cid = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Invalid channel ID.")
            try:
                me = await bot_client.get_me()
                await bot_client(GetParticipantRequest(channel=cid, participant=me.id))
            except:
                return await bot_client.send_message(uid, "❌ Bot is not in that channel.")
            await add_channel(cid, f"#{cid}", f"Channel {cid}", "target")
            await bot_client.send_message(uid, f"✅ Target {cid} added.")
            return await bot_client.send_message(uid, await get_admin_dashboard(), buttons=build_admin_keyboard())
        if act == 'confirm_add_source':
            try:
                cid = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Invalid channel ID.")
            try:
                me = await user_client.get_me()
                await user_client(GetParticipantRequest(channel=cid, participant=me.id))
            except:
                return await bot_client.send_message(uid, "❌ Your account is not in that source.")
            await add_channel(cid, f"#{cid}", f"Channel {cid}", "source")
            await bot_client.send_message(uid, f"✅ Source {cid} added.")
            return await bot_client.send_message(uid, await get_admin_dashboard(), buttons=build_admin_keyboard())
        if act == 'confirm_update_gif':
            link = txt
            if "dropboxusercontent.com" in link:
                link = link.replace("dl.dropboxusercontent.com", "dl.dropbox.com")
            if "?dl=0" in link:
                link = link.replace("?dl=0", "?dl=1")
            elif "?dl=1" not in link:
                link += "?dl=1"
            await set_bot_setting("custom_gif", link)
            await bot_client.send_message(uid, "✅ GIF updated.")
            return await bot_client.send_message(uid, await get_admin_dashboard(), buttons=build_admin_keyboard())
    elif txt.lower() in ('/start', 'start'):
        await bot_client.send_message(uid, await get_admin_dashboard(), buttons=build_admin_keyboard(), link_preview=False)

# ===== CHANNEL MESSAGE HANDLER =====
@user_client.on(events.NewMessage)
async def channel_handler(event):
    if await is_message_processed(event.chat_id, event.id):
        return
    await record_processed_message(event.chat_id, event.id)
    if (await get_bot_setting('bot_status')) != 'running':
        return
    # Process only if the source channel matches the default source channel
    src_channels = await get_channels('source')
    src_ids = [ch['channel_id'] for ch in src_channels]
    if event.chat_id not in src_ids:
        return

    txt = event.raw_text.strip()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(f"📥 Received at {now}: {txt}")

    # ----- UPDATE MESSAGE BRANCH -----
    upd = re.compile(r"MC:\s*\$?[\d\.Kk]+\s*(->|[-–>→])\s*\$?[\d\.Kk]+", re.IGNORECASE)
    if upd.search(txt):
        token_sym = extract_token_name_from_source(txt)
        mapping = await get_token_mapping(token_sym.lower())
        c = mapping["contract_address"] if mapping and mapping.get("contract_address") else "unknown_contract"
        prof = (re.search(r"(\d+)%", txt) or [None, "0"])[1] + "%"
        new_mc = (re.search(r"MC:\s*\$?([\d\.Kk]+)\s*(->|[-–>→])\s*\$?([\d\.Kk]+)", txt) or [None, None, None, "N/A"])[3]
        upd_text = build_update_template(token_sym, new_mc, prof)
        gif_url = await get_bot_setting('custom_gif')
        # POST STRICTLY in the default target channel only!
        await bot_client.send_file(
            DEFAULT_TARGET_CHANNEL["channel_id"],
            file=gif_url,
            caption=upd_text,
            reply_to=mapping["announcement_message_id"] if mapping and mapping.get("announcement_message_id") else None,
            buttons=[[Button.url("🔗 Don't Miss Out", f"https://t.me/solana_trojanbot?start=r-ttf-{c}")]]
        )
        return

    # ----- NEW CONTRACT BRANCH -----
    contract = extract_contract(txt)
    if not contract or await is_contract_processed(contract):
        return
    await record_processed_contract(contract)

    logger.info(f"➡️ Sending to TTF bot at {now}: {contract}")
    try:
        async with user_client.conversation('@ttfbotbot', timeout=90) as conv:
            await conv.send_message(contract)
            ev = await conv.get_response()
    except Exception as e:
        logger.warning("⚠️ TTF bot error: %s", e)
        return

    logger.info(f"⬅️ Received from TTF bot at {now}: {ev.raw_text}")
    data = parse_tff_output(ev.raw_text)
    # Extract token name from the source message.
    token_name = extract_token_name_from_source(txt)
    # Build announcement template using extracted token name
    new_text = build_new_template(token_name, contract, data['market_cap'], data['liquidity_status'], data['mint_status'])
    buttons = build_announcement_buttons(contract)
    # POST NEW ANNOUNCEMENT STRICTLY to the default target channel
    msg = await bot_client.send_file(
        DEFAULT_TARGET_CHANNEL["channel_id"],
        file=(await get_bot_setting('custom_gif')),
        caption=new_text,
        buttons=buttons
    )
    announcement_id = msg.id
    await add_token_mapping(token_name.lower(), contract, announcement_id)

async def resume_after(minutes: int, admin_id: int):
    await asyncio.sleep(minutes * 60)
    if (await get_bot_setting('bot_status')) == 'paused':
        await set_bot_setting('bot_status', 'running')
        await bot_client.send_message(admin_id, "▶️ Resumed after pause.")

async def check_bot_admin() -> bool:
    try:
        me = await bot_client.get_me()
        # Check admin rights in the default target channel
        part = await bot_client(GetParticipantRequest(
            channel=DEFAULT_TARGET_CHANNEL["channel_id"],
            participant=me
        ))
        return isinstance(part.participant, (ChannelParticipantAdmin, ChannelParticipantCreator))
    except Exception as e:
        logger.error("Admin check error: %s", e)
        return False

# ===== FLASK HEALTHCHECK ENDPOINT =====
# We add a simple Flask app that provides a health-check endpoint.
app = Flask(__name__)

@app.route('/')
def root():
    return jsonify(status="ok", message="Bot is running"), 200

@app.route('/health')
def health():
    return jsonify(status="ok"), 200

def start_self_ping():
    def ping_health():
        try:
            requests.get(f"http://localhost:{os.environ.get('PORT', '5000')}/health")
            logger.info("✅ Self-ping successful")
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Self-ping failed: {e}")
        threading.Timer(4 * 60, ping_health).start()  # Ping every 4 minutes

# ===== MAIN =====
async def main():
    # 1) Initialize the database and seed defaults
    await init_db()
    admins = await get_admins()
    if DEFAULT_ADMIN_ID not in admins:
        await add_admin(DEFAULT_ADMIN_ID, 'Default', is_default=True)

    # Seed default source channel if not already present
    source_channels = await get_channels('source')
    if not any(c['channel_id'] == DEFAULT_SOURCE_CHANNEL['channel_id'] for c in source_channels):
        await add_channel(**DEFAULT_SOURCE_CHANNEL)
        logger.info("✅ Default source channel seeded.")

    # Seed default target channel if not already present
    target_channels = await get_channels('target')
    if not any(c['channel_id'] == DEFAULT_TARGET_CHANNEL['channel_id'] for c in target_channels):
        tgt = DEFAULT_TARGET_CHANNEL.copy()
        tgt['username'] = tgt['username'] or ''
        await add_channel(**tgt)
        logger.info("✅ Default target channel seeded.")

    for key, value in DEFAULT_BOT_SETTINGS.items():
        current_val = await get_bot_setting(key)
        if current_val is None:
            await set_bot_setting(key, value)

    # 2) Start both Telegram clients so they're connected
    await user_client.start()
    await bot_client.start(bot_token=bot_token)

    # 3) Now that we're connected, correct the last announcement
    await correct_last_announcement()

    # 4) Verify bot has admin rights in the target channel
    if not await check_bot_admin():
        logger.error("Bot lacks admin rights in the target channel; exiting.")
        return

    logger.info("🚀 Bot is running.")

    # 5) Start self-pinging
    start_self_ping()

    # 6) Run until disconnected
    await user_client.run_until_disconnected()

if __name__ == '__main__':
    # Start Flask in a daemon thread so Render detects the webservice.
    port = int(os.environ.get('PORT', 5000))
    flask_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True)
    flask_thread.start()

    # Run the Telegram bot's async main
    asyncio.run(main())

