import re
import asyncio
import logging
import os
from datetime import datetime
from threading import Thread

import requests
from flask import Flask, jsonify, request
from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.tl.functions.channels import GetParticipantRequest

# ===== FLASK SETUP =====
app = Flask(__name__)

# ===== GLOBAL IN-MEMORY STORAGE =====
ADMINS = {}
CHANNELS = {"source": [], "target": []}
PROCESSED_MESSAGES = set()
PROCESSED_CONTRACTS = set()
TOKEN_MAPPINGS = {}
BOT_SETTINGS = {
    "bot_status": "running",
    "custom_gif": "https://dl.dropboxusercontent.com/scl/fi/u6r3x30cno1ebmvbpu5k1/video.mp4?rlkey=ytfk8qkdpwwm3je6hjcqgd89s&st=vxjkqe6s"
}

DEFAULT_ADMIN_ID = 6489451767
DEFAULT_SOURCE_CHANNEL = {
    "channel_id": -1001998961899,
    "username": "@gem_tools_calls",
    "title": "💎 GemTools 💎 Calls",
    "channel_type": "source"
}
DEFAULT_TARGET_CHANNEL = {
    "channel_id": -1002405509240,
    "username": None,
    "title": "Wagmi Vip ☢️",
    "channel_type": "target"
}

# ===== LOGGING CONFIGURATION =====
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "bot_logs.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_file, mode='a'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
logger.info("🔥 Flask + Telethon bot container starting...")

# ===== TELEGRAM CONFIGURATION =====
api_id = 28885685
api_hash = 'c24e850a947c003557f614d6b34035d9'
bot_token = '7886946660:AAGXvcV7FS5uFduFUVGGzwwWg1kfua_Pzco'
user_session = 'monkey'
bot_session = 'lion'

bot_client = TelegramClient(bot_session, api_id, api_hash)
user_client = TelegramClient(user_session, api_id, api_hash)

# ===== IN-MEMORY DATA HELPERS =====
async def get_admins(): return ADMINS.copy()
async def add_admin(user_id, first_name, last_name="", lang="en", is_default=False):
    ADMINS[user_id] = {"user_id": user_id, "first_name": first_name, "last_name": last_name, "lang": lang, "is_default": is_default}
async def remove_admin(user_id):
    if user_id in ADMINS and not ADMINS[user_id].get("is_default", False):
        ADMINS.pop(user_id, None)

async def get_channels(channel_type): return CHANNELS.get(channel_type, [])
async def add_channel(channel_id, username, title, channel_type):
    CHANNELS.setdefault(channel_type, []).append({"channel_id": channel_id, "username": username, "title": title})
async def remove_channel(channel_id, channel_type):
    CHANNELS[channel_type] = [c for c in CHANNELS[channel_type] if c["channel_id"] != channel_id]

async def is_message_processed(chat_id, message_id): return (chat_id, message_id) in PROCESSED_MESSAGES
async def record_processed_message(chat_id, message_id): PROCESSED_MESSAGES.add((chat_id, message_id))
async def is_contract_processed(contract_address): return contract_address in PROCESSED_CONTRACTS
async def record_processed_contract(contract_address): PROCESSED_CONTRACTS.add(contract_address)

async def get_token_mapping(token_name): return TOKEN_MAPPINGS.get(token_name)
async def add_token_mapping(token_name, contract_address): TOKEN_MAPPINGS[token_name] = contract_address

def get_bot_setting(setting): return BOT_SETTINGS.get(setting)
def set_bot_setting(setting, value): BOT_SETTINGS[setting] = value

# ===== HELPER FUNCTIONS =====
def extract_contract(text: str) -> str | None:
    m = re.findall(r"\b[A-Za-z0-9]{32,50}\b", text)
    return m[0] if m else None

def parse_tff_output(text: str) -> dict:
    data = {}
    tm = re.search(r"📌\s*([^\n⚠]+)", text) or re.search(r"💊\s*([^\s(]+)", text)
    data["token_name"] = tm.group(1).strip().split()[0].lower() if tm else "unknown"
    data["mint_status"] = (re.search(r"🌿\s*Mint:\s*(\w+)", text) or [None, "N/A"])[1]
    data["liquidity_status"] = (re.search(r"Liq:\s*\$?([\d\.Kk]+)", text) or [None, "N/A"])[1]
    data["market_cap"] = (re.search(r"MC:\s*\$?([\d\.Kk]+)", text) or [None, "N/A"])[1]
    logger.info("✅ Parsed TFF output for token '%s'.", data["token_name"])
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

def build_announcement_buttons(c):
    return [
        [Button.url("📈 Chart", f"https://dexscreener.com/solana/{c}"),
         Button.url("🛡️ Trojan", f"https://t.me/solana_trojanbot?start=r-ttf-{c}")],
        [Button.url("🐉 Soul", f"https://t.me/soul_sniper_bot?start=4U4QhnwlCBxS_{c}"),
         Button.url("🤖 MEVX", f"https://t.me/MevxTradingBot?start={c}")],
        [Button.url("📊 Algora", f"https://t.me/algoratradingbot?start=r-tff-{c}")],
        [Button.url("🚀 Trojan N", f"https://t.me/nestor_trojanbot?start=r-shielzuknf5b-{c}"),
         Button.url("🔗 GMGN", "https://t.me/GMGN_sol03_bot?start=CcJ5M3wBy35JHLp4csmFF8QyxdeHuKasPqKQeFa1TzLC")]
    ]

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
    bot_status = get_bot_setting("bot_status") or "running"
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
        set_bot_setting("bot_status", "running")
        await event.answer('▶️ Bot started')
        return await event.edit(await get_admin_dashboard(), buttons=build_admin_keyboard())

    if data == 'admin_pause':
        kb = [[Button.inline("🔙 Back", b"admin_home")]]
        pending_input[uid] = {'action': 'pause'}
        return await event.edit("⏸ *Pause Bot*\n\nHow many minutes should I pause for?", buttons=kb)

    if data == 'admin_stop':
        set_bot_setting("bot_status", "stopped")
        await event.answer('🛑 Bot stopped')
        return await event.edit("🛑 *Bot has been shut down.*", buttons=[[Button.inline("🔙 Back", b"admin_home")]])

    if data == 'admin_admins':
        kb = [
            [Button.inline("➕ Add Admin", b"admin_add_admin")],
            [Button.inline("🗑️ Remove Admin", b"admin_remove_admin")],
            [Button.inline("🔙 Back", b"admin_home")]
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
        targets = await get_channels("target")
        kb = [
            *[[Button.inline(ch['title'], "noop"),
               Button.inline("❌ Remove", f"remove_target:{ch['channel_id']}")]
              for ch in targets],
            [Button.inline("🔙 Back", b"admin_targets")]
        ]
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
        sources = await get_channels("source")
        kb = [
            *[[Button.inline(ch['title'], "noop"),
               Button.inline("❌ Remove", f"remove_source:{ch['channel_id']}")]
              for ch in sources],
            [Button.inline("🔙 Back", b"admin_sources")]
        ]
        return await event.edit("🗑️ *Remove Source*", buttons=kb)

    if data == 'admin_update_gif':
        pending_input[uid] = {'action': 'confirm_update_gif'}
        return await event.edit("🎬 *Update GIF*\n\nSend me the new GIF URL:", buttons=[[Button.inline("🔙 Back", b"admin_home")]])

    if data.startswith('remove_admin:'):
        aid = int(data.split(':')[1])
        if ADMINS.get(aid, {}).get("is_default"):
            return await event.answer("❌ Cannot remove the default admin", alert=True)
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
    if uid not in ADMINS:
        return
    txt = event.raw_text.strip()
    if uid in pending_input:
        act = pending_input.pop(uid)['action']

        if act == 'pause':
            try:
                m = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Please send a valid number of minutes.")
            set_bot_setting("bot_status", "paused")
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
            set_bot_setting("custom_gif", link)
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

    if get_bot_setting('bot_status') != 'running':
        return
    src_ids = [ch['channel_id'] for ch in await get_channels('source')]
    if event.chat_id not in src_ids:
        return

    txt = event.raw_text.strip()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(f"📥 Received at {now}: {txt}")

    # Market cap update branch
    upd = re.compile(r"MC:\s*\$?[\d\.Kk]+\s*(->|[-–>→])\s*\$?[\d\.Kk]+", re.IGNORECASE)
    if upd.search(txt):
        sym = (re.search(r"\$(\w+)", txt) or [None, "unknown"])[1].lower()
        c = await get_token_mapping(sym) or "unknown_contract"
        prof = (re.search(r"(\d+)%", txt) or [None, "0"])[1] + "%"
        new_mc = (re.search(r"MC:\s*\$?([\d\.Kk]+)\s*(->|[-–>→])\s*\$?([\d\.Kk]+)", txt) or [None, None, None, "N/A"])[3]
        upd_msg = (
            "🚀 *Early GEM Hunters Winning Big!* 💎\n\n"
            f"💵 *{sym.upper()}* Market Cap: {new_mc} 📈\n"
            f"🔥 {prof} & STILL RUNNING! 🔥\n\n"
            "Stay sharp for the next hidden GEM! 👀"
        )
        for ch in await get_channels('target'):
            await bot_client.send_file(
                ch['channel_id'],
                file=get_bot_setting('custom_gif'),
                caption=upd_msg,
                buttons=[[Button.url("🔗 Don't Miss Out", f"https://t.me/solana_trojanbot?start=r-ttf-{c}")]]
            )
        return

    # New contract branch
    contract = extract_contract(txt)
    if not contract or await is_contract_processed(contract):
        return
    await record_processed_contract(contract)

    logger.info(f"➡️ Sending to TFF bot at {now}: {contract}")
    try:
        async with user_client.conversation('@ttfbotbot', timeout=90) as conv:
            await conv.send_message(contract)
            ev = await conv.get_response()
    except Exception as e:
        logger.warning("⚠️ TTF bot error: %s", e)
        return

    logger.info(f"⬅️ Received from TFF bot at {now}: {ev.raw_text}")
    data = parse_tff_output(ev.raw_text)
    new_text = build_new_template(
        data['token_name'], contract,
        data['market_cap'], data['liquidity_status'], data['mint_status']
    )
    buttons = build_announcement_buttons(contract)
    await add_token_mapping(data['token_name'], contract)

    for ch in await get_channels('target'):
        await bot_client.send_file(
            ch['channel_id'],
            file=get_bot_setting('custom_gif'),
            caption=new_text,
            buttons=buttons
        )

async def resume_after(minutes: int, admin_id: int):
    await asyncio.sleep(minutes * 60)
    if get_bot_setting('bot_status') == 'paused':
        set_bot_setting('bot_status', 'running')
        await bot_client.send_message(admin_id, "▶️ Resumed after pause.")

async def check_bot_admin() -> bool:
    try:
        me = await bot_client.get_me()
        targets = await get_channels('target')
        if not targets:
            return False
        part = await bot_client(GetParticipantRequest(
            channel=targets[0]['channel_id'],
            participant=me
        ))
        return isinstance(part.participant, (ChannelParticipantAdmin, ChannelParticipantCreator))
    except Exception as e:
        logger.error("Admin check error: %s", e)
        return False

async def main():
    # Seed defaults
    await add_admin(DEFAULT_ADMIN_ID, 'Default', is_default=True)
    if not any(c['channel_id'] == DEFAULT_SOURCE_CHANNEL['channel_id'] for c in await get_channels('source')):
        await add_channel(**DEFAULT_SOURCE_CHANNEL)
        logger.info("✅ Default source channel seeded.")
    if not any(c['channel_id'] == DEFAULT_TARGET_CHANNEL['channel_id'] for c in await get_channels('target')):
        tgt = DEFAULT_TARGET_CHANNEL.copy()
        tgt['username'] = tgt['username'] or ''
        await add_channel(**tgt)
        logger.info("✅ Default target channel seeded.")

    await user_client.start()
    await bot_client.start(bot_token=bot_token)

    if not await check_bot_admin():
        logger.error("Bot lacks admin rights; exiting.")
        return

    logger.info("🚀 Bot is running.")
    await user_client.run_until_disconnected()

# ===== FLASK ENDPOINTS =====
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "bot_status": get_bot_setting("bot_status")})

@app.route("/start", methods=["POST"])
def start_bot():
    def run_bot():
        asyncio.run(main())
    thread = Thread(target=run_bot, daemon=True)
    thread.start()
    return jsonify({"status": "starting"}), 202

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
