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
from flask import Flask, jsonify, request, redirect, session, render_template_string

===== ENV / CONFIG =====

DB_NAME    = os.environ.get("DB_NAME", "wagmi_82kq")
DB_USER    = os.environ.get("DB_USER", "wagmi_82kq_user")
DB_PASS    = os.environ.get("DB_PASS", "ROPvICF4rzRBA5nIGoLzweJMJYOXUKWo")
DB_HOST    = os.environ.get("DB_HOST", "dpg-d0dojsmuk2gs73dbrcbg-a.oregon-postgres.render.com")
DB_PORT    = os.environ.get("DB_PORT", "5432")
API_ID     = int(os.environ.get("API_ID", 28146969))
API_HASH   = os.environ.get("API_HASH", '5c8acdf2a7358589696af178e2319443')
BOT_TOKEN  = os.environ.get("BOT_TOKEN", '7834122356:AAGszZL-bgmggu_77aH0_lszBqe-Rei25_w')
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24).hex())

===== FLASK APP & SESSION =====

app = Flask(name)
app.secret_key = SECRET_KEY

===== TELETHON CLIENTS =====

bot_client  = TelegramClient('lion', API_ID, API_HASH)
user_client = TelegramClient('monkey', API_ID, API_HASH)

===== DATABASE HELPERS (sync + async) =====

def get_connection():
return psycopg2.connect(
dbname=DB_NAME,
user=DB_USER,
password=DB_PASS,
host=DB_HOST,
port=DB_PORT
)

def init_db_sync():
conn = get_connection()
try:
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
channel_id BIGINT NOT NULL,
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
finally:
conn.close()

def get_admins_sync():
conn = get_connection()
try:
with conn.cursor(cursor_factory=RealDictCursor) as cur:
cur.execute("SELECT * FROM admins")
rows = cur.fetchall()
return {r["user_id"]: r for r in rows}
finally:
conn.close()

def add_admin_sync(user_id, first_name, last_name="", lang="en", is_default=False):
conn = get_connection()
try:
with conn.cursor() as cur:
cur.execute("""
INSERT INTO admins (user_id, first_name, last_name, lang, is_default)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (user_id) DO UPDATE
SET first_name=%s, last_name=%s, lang=%s, is_default=%s;
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
return cur.fetchall()
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
cur.execute("DELETE FROM channels WHERE channel_id = %s AND channel_type = %s",
(channel_id, channel_type))
conn.commit()
finally:
conn.close()

def is_message_processed_sync(chat_id, message_id):
conn = get_connection()
try:
with conn.cursor() as cur:
cur.execute("SELECT 1 FROM processed_messages WHERE chat_id = %s AND message_id = %s",
(chat_id, message_id))
return cur.fetchone() is not None
finally:
conn.close()

def record_processed_message_sync(chat_id, message_id):
conn = get_connection()
try:
with conn.cursor() as cur:
cur.execute("""
INSERT INTO processed_messages (chat_id, message_id)
VALUES (%s, %s) ON CONFLICT DO NOTHING
""", (chat_id, message_id))
conn.commit()
finally:
conn.close()

def is_contract_processed_sync(contract_address):
conn = get_connection()
try:
with conn.cursor() as cur:
cur.execute("SELECT 1 FROM processed_contracts WHERE contract_address = %s",
(contract_address,))
return cur.fetchone() is not None
finally:
conn.close()

def record_processed_contract_sync(contract_address):
conn = get_connection()
try:
with conn.cursor() as cur:
cur.execute("""
INSERT INTO processed_contracts (contract_address)
VALUES (%s) ON CONFLICT DO NOTHING
""", (contract_address,))
conn.commit()
finally:
conn.close()

def get_token_mapping_sync(token_name):
conn = get_connection()
try:
with conn.cursor(cursor_factory=RealDictCursor) as cur:
cur.execute("SELECT * FROM token_mappings WHERE token_name = %s", (token_name,))
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
SET contract_address = %s,
announcement_message_id = COALESCE(%s, token_mappings.announcement_message_id)
""", (token_name, contract_address, announcement_message_id,
contract_address, announcement_message_id))
conn.commit()
finally:
conn.close()

def update_token_announcement_sync(token_name, announcement_message_id):
conn = get_connection()
try:
with conn.cursor() as cur:
cur.execute("""
UPDATE token_mappings SET announcement_message_id = %s
WHERE token_name = %s
""", (announcement_message_id, token_name))
conn.commit()
finally:
conn.close()

def get_mapping_by_announcement_sync(announcement_message_id):
conn = get_connection()
try:
with conn.cursor(cursor_factory=RealDictCursor) as cur:
cur.execute("SELECT * FROM token_mappings WHERE announcement_message_id = %s",
(announcement_message_id,))
row = cur.fetchone()
return dict(row) if row else None
finally:
conn.close()

def get_bot_setting_sync(setting):
conn = get_connection()
try:
with conn.cursor(cursor_factory=RealDictCursor) as cur:
cur.execute("SELECT setting_value FROM bot_settings WHERE setting_key = %s", (setting,))
row = cur.fetchone()
return row["setting_value"] if row else None
finally:
conn.close()

def set_bot_setting_sync(setting, value):
conn = get_connection()
try:
with conn.cursor() as cur:
cur.execute("""
INSERT INTO bot_settings (setting_key, setting_value)
VALUES (%s, %s)
ON CONFLICT (setting_key) DO UPDATE SET setting_value = %s
""", (setting, value, value))
conn.commit()
finally:
conn.close()

Async wrappers

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

===== DEFAULTS & LOGGING =====

DEFAULT_ADMIN_ID = 6489451767
DEFAULT_SOURCE_CHANNEL = {
"channel_id": -1001998961899,
"username": "@gem_tools_calls",
"title": "💎 GemTools 💎 Calls",
"channel_type": "source"
}
DEFAULT_TARGET_CHANNEL = {
"channel_id": -1002405509240,
"username": "",
"title": "Wagmi Vip ☢️",
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
logger = logging.getLogger(name)
logger.info("🔥 Logging setup complete. Bot is starting...")

===== HELPER FUNCTIONS =====

def extract_contract(text: str) -> str | None:
m = re.findall(r"\b[A-Za-z0-9]{32,50}\b", text)
return m[0] if m else None

def extract_token_name_from_source(text: str) -> str:
lines = text.strip().splitlines()
if not lines:
logger.info("Empty message received; returning 'unknown'.")
return "unknown"
for line in lines:
match = re.search(r"$([A-Za-z0-9_]+)", line)
if match:
token = match.group(1)
logger.info(f"Token extracted: '{token}' from line: '{line}'")
return token
logger.info("No valid token found in the message; returning 'unknown'.")
return "unknown"

def parse_tff_output(text: str) -> dict:
data = {}
data["mint_status"]      = (re.search(r"🌿\sMint:\s(\w+)", text) or [None, "N/A"])[1]
data["liquidity_status"] = (re.search(r"Liq:\s*$?([\d.Kk]+)", text) or [None, "N/A"])[1]
data["market_cap"]       = (re.search(r"MC:\s*$?([\d.Kk]+)", text) or [None, "N/A"])[1]
logger.info("✅ Parsed TTF output.")
return data

def build_new_template(token_name, contract, market_cap, liquidity_status, mint_status):
return (
"🚀 New 💎 GEM Landed! 🚀\n\n"
f"💰 ${token_name.upper()}\n\n"
f"📊 Market Cap: {market_cap}\n"
f"💦 Liquidity: {liquidity_status}\n"
f"🔥 Minting: {mint_status}\n\n"
f"🔗 Contract: {contract}\n"
"🌐 Network: #SOL"
)

def build_update_template(token_name, new_mc, prof):
return (
f"🚀 Early GEM Hunters Winning Big! 💎\n\n"
f"💵 {token_name.upper()} Market Cap: {new_mc} 📈\n"
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
[Button.url("🚀 Trojan N", f"https://t.me/nestor_trojanbot?start=r-shielzuknf5b-{contract}"),
Button.url("🔗 GMGN", "https://t.me/GMGN_sol03_bot?start=CcJ5M3wBy35JHLp4csmFF8QyxdeHuKasPqKQeFa1TzLC")]
]

===== STATE =====

pending_input = {}

===== FLASK LOGIN ROUTES =====

LOGIN_FORM = """
<!doctype html>

<title>Login to Telegram</title>  
<h2>Step 1: Enter your phone number</h2>  
<form method="post">  
  <input name="phone" placeholder="+1234567890" required>  
  <button type="submit">Send Code</button>  
</form>  
"""  
CODE_FORM = """  
<!doctype html>  
<title>Enter the Code</title>  
<h2>Step 2: Enter the code you received</h2>  
<form method="post">  
  <input name="code" placeholder="12345" required>  
  <button type="submit">Verify</button>  
</form>  
"""  @app.route('/login', methods=['GET', 'POST'])
async def login():
if request.method == 'POST':
phone = request.form['phone'].strip()
session['phone'] = phone
try:
await user_client.connect()
await user_client.send_code_request(phone)
logger.info(f"➡️ Sent login code to {phone}")
return redirect('/submit-code')
except Exception as e:
logger.error(f"❌ Error sending login code: {e}")
return "<p>Error sending code. Check logs.</p>", 500
return render_template_string(LOGIN_FORM)

@app.route('/submit-code', methods=['GET', 'POST'])
async def submit_code():
if 'phone' not in session:
return redirect('/login')
if request.method == 'POST':
code = request.form['code'].strip()
phone = session['phone']
try:
await user_client.sign_in(phone, code)
logger.info(f"✅ Logged in user-client for {phone}")
return "<p>Login successful! You can close this tab.</p>"
except Exception as e:
logger.error(f"❌ Login failed: {e}")
return "<p>Invalid code or error. Try again.</p>", 400
return render_template_string(CODE_FORM)

===== FLASK HEALTHCHECK =====

@app.route('/')
def root():
return jsonify(status="ok", message="Bot is running"), 200

@app.route('/health')
def health():
return jsonify(status="ok"), 200

===== TELETHON ADMIN CALLBACK HANDLER =====

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
pending_input[uid] = {'action': 'pause'}
kb = [[Button.inline("🔙 Back", b"admin_home")]]
return await event.edit("⏸ Pause Bot\n\nHow many minutes should I pause for?", buttons=kb)
if data == 'admin_stop':
await set_bot_setting("bot_status", "stopped")
await event.answer('🛑 Bot stopped')
return await event.edit("🛑 Bot has been shut down.", buttons=[[Button.inline("🔙 Back", b"admin_home")]])
if data == 'admin_admins':
kb = [
[Button.inline("➕ Add Admin", b"admin_add_admin")],
[Button.inline("🗑️ Remove Admin", b"admin_remove_admin")],
[Button.inline("🔙 Back", b"admin_admins")]
]
return await event.edit("👤 Manage Admins", buttons=kb)
if data == 'admin_add_admin':
pending_input[uid] = {'action': 'confirm_add_admin'}
return await event.edit("➕ Add Admin\n\nSend me the user ID to add:", buttons=[[Button.inline("🔙 Back", b"admin_admins")]])
if data == 'admin_remove_admin':
admins = await get_admins()
kb = []
for aid, info in admins.items():
if aid == DEFAULT_ADMIN_ID or info.get("is_default"):
kb.append([Button.inline(f"{info['first_name']} ({aid})", b"noop")])
else:
kb.append([
Button.inline(f"{info['first_name']} ({aid})", b"noop"),
Button.inline("❌ Remove", f"remove_admin:{aid}")
])
kb.append([Button.inline("🔙 Back", b"admin_admins")])
return await event.edit("🗑️ Remove Admin", buttons=kb)
if data == 'admin_targets':
kb = [
[Button.inline("➕ Add Target", b"admin_add_target")],
[Button.inline("🗑️ Remove Target", b"admin_remove_target")],
[Button.inline("🔙 Back", b"admin_home")]
]
return await event.edit("📺 Manage Targets", buttons=kb)
if data == 'admin_add_target':
pending_input[uid] = {'action': 'confirm_add_target'}
return await event.edit("➕ Add Target\n\nSend me the channel ID to add:", buttons=[[Button.inline("🔙 Back", b"admin_targets")]])
if data == 'admin_remove_target':
targets = await get_channels('target')
kb = [[Button.inline(ch['title'], b"noop") + [Button.inline("❌ Remove", f"remove_target:{ch['channel_id']}")] for ch in targets],
[Button.inline("🔙 Back", b"admin_targets")]]
return await event.edit("🗑️ Remove Target", buttons=kb)
if data == 'admin_sources':
kb = [
[Button.inline("➕ Add Source", b"admin_add_source")],
[Button.inline("🗑️ Remove Source", b"admin_remove_source")],
[Button.inline("🔙 Back", b"admin_home")]
]
return await event.edit("📡 Manage Sources", buttons=kb)
if data == 'admin_add_source':
pending_input[uid] = {'action': 'confirm_add_source'}
return await event.edit("➕ Add Source\n\nSend me the channel ID to add:", buttons=[[Button.inline("🔙 Back", b"admin_sources")]])
if data == 'admin_remove_source':
sources = await get_channels('source')
kb = [[Button.inline(ch['title'], b"noop") + [Button.inline("❌ Remove", f"remove_source:{ch['channel_id']}")] for ch in sources],
[Button.inline("🔙 Back", b"admin_sources")]]
return await event.edit("🗑️ Remove Source", buttons=kb)
if data == 'admin_update_gif':
pending_input[uid] = {'action': 'confirm_update_gif'}
return await event.edit("🎬 Update GIF\n\nSend me the new GIF URL:", buttons=[[Button.inline("🔙 Back", b"admin_home")]])
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

===== TELETHON PRIVATE MESSAGE HANDLER =====

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
except Exception as e:
logger.error(f"Error checking source channel participation for {cid}: {e}")
return await bot_client.send_message(uid, "❌ Your user account is not in that source channel or it's not accessible.")
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

===== TELETHON CHANNEL MESSAGE HANDLER =====

@user_client.on(events.NewMessage)
async def channel_handler(event):
if await is_message_processed(event.chat_id, event.id):
return
await record_processed_message(event.chat_id, event.id)
if (await get_bot_setting('bot_status')) != 'running':
return

src_ids = [c['channel_id'] for c in await get_channels('source')]  
if event.chat_id not in src_ids:  
    return  

txt = event.raw_text.strip()  
now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")  
logger.info(f"📥 Received at {now}: {txt}")  

upd = re.compile(r"MC:\s*\$?[\d\.Kk]+\s*(->|[-–>→])\s*\$?[\d\.Kk]+", re.IGNORECASE)  
if upd.search(txt):  
    token_sym = extract_token_name_from_source(txt)  
    mapping = await get_token_mapping(token_sym.lower())  
    c = mapping["contract_address"] if mapping else "unknown_contract"  
    prof = (re.search(r"(\d+)%", txt) or [None, "0"])[1] + "%"  
    new_mc = (re.search(r"MC:\s*\$?[\d\.Kk]+\s*(->|[-–>→])\s*\$?[\d\.Kk]+", txt) or [None, None, None, "N/A"])[3]  
    upd_text = build_update_template(token_sym, new_mc, prof)  
    gif_url = await get_bot_setting('custom_gif')  
    await bot_client.send_file(  
        DEFAULT_TARGET_CHANNEL["channel_id"],  
        file=gif_url,  
        caption=upd_text,  
        reply_to=mapping.get("announcement_message_id") if mapping else None,  
        buttons=[[Button.url("🔗 Don't Miss Out", f"https://t.me/solana_trojanbot?start=r-ttf-{c}")]]  
    )  
    return  

contract = extract_contract(txt)  
if not contract or await is_contract_processed(contract):  
    return  
await record_processed_contract(contract)  

logger.info(f"➡️ Sending to TTF bot at {now}: {contract}")  
try:  
    if not user_client.is_connected():  
        await user_client.connect()  
    if not await user_client.is_user_authorized():  
        logger.warning("⚠️ User client not authorized. TTF bot interaction skipped.")  
        return  

    async with user_client.conversation('@ttfbotbot', timeout=90) as conv:  
        await conv.send_message(contract)  
        ev = await conv.get_response()  
except Exception as e:  
    logger.warning("⚠️ TTF bot error: %s", e)  
    return  

logger.info(f"⬅️ Received from TTF bot at {now}: {ev.raw_text}")  
data = parse_tff_output(ev.raw_text)  
token_name = extract_token_name_from_source(txt)  
new_text = build_new_template(token_name, contract, data['market_cap'], data['liquidity_status'], data['mint_status'])  
buttons = build_announcement_buttons(contract)  
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
extracted_token = extract_token_name_from_source(text)
mapping = await get_mapping_by_announcement(last_msg.id)
if mapping:
old_token = mapping["token_name"]
if extracted_token and extracted_token != old_token:
await add_token_mapping(extracted_token.lower(), mapping["contract_address"], last_msg.id)
logger.info("Corrected token mapping for message id %s: '%s' -> '%s'",
last_msg.id, old_token, extracted_token)
else:
logger.info("No mapping found for announcement message id %s", last_msg.id)

async def check_bot_admin() -> bool:
try:
me = await bot_client.get_me()
part = await bot_client(GetParticipantRequest(
channel=DEFAULT_TARGET_CHANNEL["channel_id"],
participant=me
))
return isinstance(part.participant, (ChannelParticipantAdmin, ChannelParticipantCreator))
except Exception as e:
logger.error("Admin check error: %s", e)
return False

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
"👋 Hey Boss! 👋\n\n"
f"🤖 Bot Status: {bot_status.capitalize()}\n\n"
f"💖 Affirmation: {aff}\n"
f"🚀 Motivation: {mot}\n\n"
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

===== MAIN ENTRY (Hypercorn + asyncio) =====

async def main():
# Initialize DB & defaults
await init_db()
admins = await get_admins()
if DEFAULT_ADMIN_ID not in admins:
await add_admin(DEFAULT_ADMIN_ID, 'Default', is_default=True)

src_ch = await get_channels('source')  
if not any(c['channel_id']==DEFAULT_SOURCE_CHANNEL['channel_id'] for c in src_ch):  
    await add_channel(**DEFAULT_SOURCE_CHANNEL)  
tgt_ch = await get_channels('target')  
if not any(c['channel_id']==DEFAULT_TARGET_CHANNEL['channel_id'] for c in tgt_ch):  
    await add_channel(**DEFAULT_TARGET_CHANNEL)  

for k,v in DEFAULT_BOT_SETTINGS.items():  
    if await get_bot_setting(k) is None:  
        await set_bot_setting(k, v)  

# Start bot client  
await bot_client.start(bot_token=BOT_TOKEN)  
logger.info("🤖 Bot client started.")  

await user_client.connect()  
if await user_client.is_user_authorized():  
    await correct_last_announcement()  
else:  
    logger.info("👤 User client not yet authorized; please /login via web.")  

if not await check_bot_admin():  
    logger.error("❌ Bot lacks admin rights in target channel.")  

await asyncio.Event().wait()

if name == 'main':
from hypercorn.asyncio import serve
from hypercorn.config     import Config
from asgiref.wsgi         import WsgiToAsgi

asgi_app = WsgiToAsgi(app)  
config   = Config()  
config.bind     = [f"0.0.0.0:{os.environ.get('PORT','5000')}"]  
config.accesslog = '-'  
config.errorlog  = '-'  

def start_self_ping():  
    def ping():  
        try:  
            port = os.environ.get('PORT','5000')  
            requests.get(f"http://localhost:{port}/health")  
            logger.info("✅ Self-ping OK")  
        except Exception as e:  
            logger.error(f"❌ Self-ping failed: {e}")  
        threading.Timer(4*60, ping).start()  
    ping()  

def delayed_self_ping():  
    time.sleep(5)  
    start_self_ping()  

threading.Thread(target=delayed_self_ping, daemon=True).start()  
logger.info(f"Starting Hypercorn on {config.bind[0]}")  

async def runner():  
    await asyncio.gather(  
        serve(asgi_app, config),  
        main()  
    )  

asyncio.run(runner())



