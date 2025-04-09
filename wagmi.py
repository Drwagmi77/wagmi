import re
import asyncio
import logging
import os
import threading
from datetime import datetime

import requests
from flask import Flask
from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.tl.functions.channels import GetParticipantRequest

# ===== SUPABASE SETUP =====
from supabase import create_client, Client

SUPABASE_URL = "https://dbpgxflxpexjxgfeqyna.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRicGd4Zmx4cGV4anhnZmVxeW5hIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDM4NDQwNzMsImV4cCI6MjA1OTQyMDA3M30."
    "HroOexM1Oo-VwufnpxVrdosf6UUgkXgv8zEk1ZB_xJ4"
)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
logger.info("🔥 Logging setup complete. Bot is starting...")

# ===== TELEGRAM CONFIGURATION & GLOBAL CONSTANTS =====
api_id = 28885685
api_hash = 'c24e850a947c003557f614d6b34035d9'
user_session = 'monkey'
bot_session = 'lion'
bot_token = "7886946660:AAGXvcV7FS5uFduFUVGGzwwWg1kfua_Pzco"
DEFAULT_ADMIN_ID = 6489451767

# ===== INITIALIZE TELEGRAM CLIENTS =====
bot_client = TelegramClient(bot_session, api_id, api_hash)
user_client = TelegramClient(user_session, api_id, api_hash)

# ===== DEFAULT CHANNELS =====
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

# ===== HELPER: Run Supabase Query with Retry =====
async def run_supabase_query(query_callable, retries=3, delay=2):
    loop = asyncio.get_running_loop()
    for attempt in range(retries):
        try:
            result = await loop.run_in_executor(None, query_callable)
            return result
        except Exception as e:
            logger.error("Supabase query attempt %d failed: %s", attempt+1, e)
            if attempt < retries - 1:
                await asyncio.sleep(delay)
            else:
                raise

# ===== SUPABASE HELPER FUNCTIONS =====
async def get_admins():
    response = await run_supabase_query(lambda: supabase.table("admins").select("*").execute())
    admins = {}
    if response.data:
        for record in response.data:
            admins[int(record["user_id"])] = record
    return admins

async def add_admin(user_id: int, first_name: str, last_name: str = "", lang: str = "en", is_default: bool = False):
    await run_supabase_query(lambda: supabase.table("admins").insert({
        "user_id": user_id,
        "first_name": first_name,
        "last_name": last_name,
        "lang": lang,
        "is_default": is_default
    }).execute())

async def remove_admin(user_id: int):
    await run_supabase_query(lambda: supabase.table("admins").delete().eq("user_id", user_id).execute())

async def get_channels(channel_type: str):
    response = await run_supabase_query(lambda: supabase.table("channels").select("*").eq("channel_type", channel_type).execute())
    return response.data if response.data else []

async def add_channel(channel_id: int, username: str, title: str, channel_type: str):
    await run_supabase_query(lambda: supabase.table("channels").insert({
        "channel_id": channel_id,
        "username": username,
        "title": title,
        "channel_type": channel_type
    }).execute())

async def remove_channel(channel_id: int, channel_type: str):
    await run_supabase_query(lambda: supabase.table("channels").delete().eq("channel_id", channel_id).execute())

async def is_message_processed(chat_id: int, message_id: int) -> bool:
    response = await run_supabase_query(lambda: supabase.table("processed_messages").select("*").eq("chat_id", chat_id).eq("message_id", message_id).execute())
    return bool(response.data)

async def record_processed_message(chat_id: int, message_id: int):
    try:
        await run_supabase_query(lambda: supabase.table("processed_messages").insert({
            "chat_id": chat_id,
            "message_id": message_id
        }).execute())
    except Exception as e:
        if "duplicate key value violates unique constraint" in str(e):
            logger.info("Processed message for chat_id %s, message_id %s already exists.", chat_id, message_id)
        else:
            raise

async def is_contract_processed(contract_address: str) -> bool:
    response = await run_supabase_query(lambda: supabase.table("processed_contracts").select("*").eq("contract_address", contract_address).execute())
    return bool(response.data)

async def record_processed_contract(contract_address: str):
    try:
        await run_supabase_query(lambda: supabase.table("processed_contracts").insert({
            "contract_address": contract_address
        }).execute())
    except Exception as e:
        if "duplicate key value violates unique constraint" in str(e):
            logger.info("Processed contract %s already exists.", contract_address)
        else:
            raise

async def get_token_mapping(token_name: str):
    response = await run_supabase_query(lambda: supabase.table("token_mappings").select("*").eq("token_name", token_name).execute())
    if response.data:
        return response.data[0]["contract_address"]
    return None

async def add_token_mapping(token_name: str, contract_address: str):
    await run_supabase_query(lambda: supabase.table("token_mappings").insert({
        "token_name": token_name,
        "contract_address": contract_address
    }).execute())

def get_bot_setting(setting: str):
    response = supabase.table("bot_settings").select("*").eq("setting", setting).execute()
    if response.data:
        return response.data[0]["value"]
    return None

def set_bot_setting(setting: str, value: str):
    supabase.table("bot_settings").upsert({
        "setting": setting,
        "value": value,
        "updated_at": datetime.utcnow().isoformat()
    }).execute()

# ===== HELPER FUNCTIONS (Token extraction, Parsing, Message Templates) =====
def extract_contract(text: str) -> str | None:
    m = re.findall(r"\b[A-Za-z0-9]{32,50}\b", text)
    return m[0] if m else None

def parse_tff_output(text: str) -> dict:
    data = {}
    # Ensure token name is retrieved, else default to "unknown"
    tm = re.search(r"📌\s*([^\n⚠]+)", text) or re.search(r"💊\s*([^\s(]+)", text)
    data["token_name"] = tm.group(1).strip().split()[0].lower() if tm else "unknown"
    
    mint_match = re.search(r"🌿\s*Mint:\s*(\w+)", text)
    data["mint_status"] = mint_match.group(1) if mint_match else "N/A"
    
    liq_match = re.search(r"Liq:\s*\$?([\d\.Kk]+)", text)
    data["liquidity_status"] = liq_match.group(1) if liq_match else "N/A"
    
    mc_match = re.search(r"MC:\s*\$?([\d\.Kk]+)", text)
    data["market_cap"] = mc_match.group(1) if mc_match else "N/A"
    
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

# ===== ADMIN DASHBOARD & KEYBOARDS =====
async def get_admin_dashboard():
    try:
        aff = requests.get("https://www.affirmations.dev").json().get('affirmation', '')
    except Exception as e:
        logger.error("Affirmation error: %s", e)
        aff = ""
    try:
        q = requests.get("https://zenquotes.io/api/random").json()[0]
        mot = f"{q['q']} — {q['a']}"
    except Exception as e:
        logger.error("Motivation error: %s", e)
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
        dashboard = await get_admin_dashboard()
        return await event.edit(dashboard, buttons=build_admin_keyboard(), link_preview=False)
    if data == 'admin_start':
        set_bot_setting("bot_status", "running")
        await event.answer('▶️ Bot started')
        dashboard = await get_admin_dashboard()
        return await event.edit(dashboard, buttons=build_admin_keyboard())
    if data == 'admin_pause':
        kb = [[Button.inline("🔙 Back", b"admin_home")]]
        await event.edit("⏸ *Pause Bot*\n\nHow many minutes should I pause for?", buttons=kb)
        pending_input[uid] = {'action': 'pause'}
        return
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
        kb = [[Button.inline("🔙 Back", b"admin_admins")]]
        await event.edit("➕ *Add Admin*\n\nSend me the user ID to add:", buttons=kb)
        pending_input[uid] = {'action': 'confirm_add_admin'}
        return
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
        kb = [[Button.inline("🔙 Back", b"admin_targets")]]
        await event.edit("➕ *Add Target*\n\nSend me the channel ID to add:", buttons=kb)
        pending_input[uid] = {'action': 'confirm_add_target'}
        return
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
        kb = [[Button.inline("🔙 Back", b"admin_sources")]]
        await event.edit("➕ *Add Source*\n\nSend me the channel ID to add:", buttons=kb)
        pending_input[uid] = {'action': 'confirm_add_source'}
        return
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
        kb = [[Button.inline("🔙 Back", b"admin_home")]]
        await event.edit("🎬 *Update GIF*\n\nSend me the new GIF URL:", buttons=kb)
        pending_input[uid] = {'action': 'confirm_update_gif'}
        return
    if data.startswith('remove_admin:'):
        aid = int(data.split(':')[1])
        admins = await get_admins()
        if admins.get(aid, {}).get("is_default"):
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
            set_bot_setting("bot_status", "paused")
            await bot_client.send_message(uid, f"⏸️ Paused for {m} minutes.")
            asyncio.create_task(resume_after(m, uid))
            dashboard = await get_admin_dashboard()
            return await bot_client.send_message(uid, dashboard, buttons=build_admin_keyboard())
        if act == 'confirm_add_admin':
            try:
                new_id = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Invalid user ID.")
            await add_admin(new_id, f"ID:{new_id}")
            await bot_client.send_message(uid, f"✅ Admin {new_id} added.")
            dashboard = await get_admin_dashboard()
            return await bot_client.send_message(uid, dashboard, buttons=build_admin_keyboard())
        if act == 'confirm_add_target':
            try:
                cid = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Invalid channel ID.")
            try:
                me = await bot_client.get_me()
                await bot_client(GetParticipantRequest(channel=cid, participant=me.id))
            except Exception as e:
                logger.error("Target error: %s", e)
                return await bot_client.send_message(uid, "❌ Bot is not in that channel.")
            await add_channel(cid, f"#{cid}", f"Channel {cid}", "target")
            await bot_client.send_message(uid, f"✅ Target {cid} added.")
            dashboard = await get_admin_dashboard()
            return await bot_client.send_message(uid, dashboard, buttons=build_admin_keyboard())
        if act == 'confirm_add_source':
            try:
                cid = int(txt)
            except ValueError:
                return await bot_client.send_message(uid, "⚠️ Invalid channel ID.")
            try:
                me = await user_client.get_me()
                await user_client(GetParticipantRequest(channel=cid, participant=me.id))
            except Exception as e:
                logger.error("Source error: %s", e)
                return await bot_client.send_message(uid, "❌ Your account is not in that source.")
            await add_channel(cid, f"#{cid}", f"Channel {cid}", "source")
            await bot_client.send_message(uid, f"✅ Source {cid} added.")
            dashboard = await get_admin_dashboard()
            return await bot_client.send_message(uid, dashboard, buttons=build_admin_keyboard())
        if act == 'confirm_update_gif':
            link = txt
            if "dropboxusercontent.com" in link:
                link = link.replace("dl.dropboxusercontent.com", "dl.dropbox.com")
            if "?dl=0" in link:
                link = link.replace("?dl=0", "?dl=1")
            elif "?dl=1" not in link:
                link = link + "?dl=1"
            set_bot_setting("custom_gif", link)
            await bot_client.send_message(uid, f"✅ GIF updated.")
            dashboard = await get_admin_dashboard()
            return await bot_client.send_message(uid, dashboard, buttons=build_admin_keyboard())
    if txt.lower() in ("/start", "start"):
        await send_admin_dashboard(event)

async def resume_after(minutes: int, admin_id: int):
    await asyncio.sleep(minutes * 60)
    if get_bot_setting("bot_status") == "paused":
        set_bot_setting("bot_status", "running")
        await bot_client.send_message(admin_id, "▶️ Resumed after pause.")

@user_client.on(events.NewMessage)
async def channel_handler(event):
    if await is_message_processed(event.chat_id, event.id):
        return
    await record_processed_message(event.chat_id, event.id)
    if get_bot_setting("bot_status") != "running":
        return
    sources = await get_channels("source")
    src_ids = [ch["channel_id"] for ch in sources]
    if event.chat_id not in src_ids:
        return
    txt = event.raw_text.strip()
    logger.info("📨 Source message: %s", txt)
    upd = re.compile(r"MC:\s*\$?[\d\.Kk]+\s*(->|[-–>→])\s*\$?[\d\.Kk]+", re.IGNORECASE)
    if upd.search(txt):
        sym = (re.search(r"\$(\w+)", txt) or [None, "unknown"])[1].lower()
        c = await get_token_mapping(sym) or "unknown_contract"
        prof = (re.search(r"(\d+)%", txt) or [None, "0"])[1] + "%"
        new_mc = (re.search(r"MC:\s*\$?([\d\.Kk]+)\s*(->|[-–>→])\s*\$?([\d\.Kk]+)", txt) or [None, None, None, "N/A"])[3]
        troj = f"https://t.me/solana_trojanbot?start=r-ttf-{c}"
        upd_msg = (
            "🚀 *Early GEM Hunters Winning Big!* 💎\n\n"
            f"💵 *{sym.upper()}* Market Cap: {new_mc} 📈\n"
            f"🔥 {prof} & STILL RUNNING! 🔥\n\n"
            "Stay sharp for the next hidden GEM! 👀"
        )
        targets = await get_channels("target")
        for ch in targets:
            try:
                await bot_client.send_file(
                    ch["channel_id"],
                    file=get_bot_setting("custom_gif"),
                    caption=upd_msg,
                    buttons=[[Button.url("🔗 Don't Miss Out", troj)]]
                )
                logger.info("✅ Update sent to %s", ch["username"])
            except Exception as e:
                logger.error("❌ Update failed for %s: %s", ch["username"], e)
        return
    contract = extract_contract(txt)
    if not contract or await is_contract_processed(contract):
        return
    await record_processed_contract(contract)
    try:
        async with user_client.conversation('@ttfbotbot', timeout=90) as conv:
            await conv.send_message(contract)
            ev = await conv.get_response()
    except asyncio.TimeoutError:
        logger.warning("⚠️ TTF bot timeout for %s", contract)
        return
    except Exception as e:
        logger.error("❌ TTF bot error: %s", e)
        return
    data = parse_tff_output(ev.raw_text)
    new_text = build_new_template(
        data["token_name"], contract,
        data["market_cap"], data["liquidity_status"], data["mint_status"]
    )
    buttons = build_announcement_buttons(contract)
    await add_token_mapping(data["token_name"], contract)
    targets = await get_channels("target")
    for ch in targets:
        try:
            await bot_client.send_file(
                ch["channel_id"],
                file=get_bot_setting("custom_gif"),
                caption=new_text,
                buttons=buttons
            )
            logger.info("✅ Announcement sent to %s", ch["username"])
        except Exception as e:
            logger.error("❌ Announcement failed for %s: %s", ch["username"], e)

async def check_bot_admin() -> bool:
    try:
        me = await bot_client.get_me()
        part = await bot_client(GetParticipantRequest(
            channel=(await get_channels("target"))[0]["channel_id"],
            participant=me
        ))
        return isinstance(part.participant, (ChannelParticipantAdmin, ChannelParticipantCreator))
    except Exception as e:
        logger.error("Admin check error: %s", e)
        return False

async def send_admin_dashboard(event):
    dashboard = await get_admin_dashboard()
    await bot_client.send_message(
        event.sender_id,
        dashboard,
        buttons=build_admin_keyboard(),
        link_preview=False
    )

async def initialize_default_channels():
    sources = await get_channels("source")
    if not any(ch["channel_id"] == DEFAULT_SOURCE_CHANNEL["channel_id"] for ch in sources):
        await add_channel(
            DEFAULT_SOURCE_CHANNEL["channel_id"],
            DEFAULT_SOURCE_CHANNEL["username"],
            DEFAULT_SOURCE_CHANNEL["title"],
            DEFAULT_SOURCE_CHANNEL["channel_type"]
        )
        logger.info("✅ Default source channel seeded.")
    targets = await get_channels("target")
    if not any(ch["channel_id"] == DEFAULT_TARGET_CHANNEL["channel_id"] for ch in targets):
        await add_channel(
            DEFAULT_TARGET_CHANNEL["channel_id"],
            DEFAULT_TARGET_CHANNEL["username"] or "",
            DEFAULT_TARGET_CHANNEL["title"],
            DEFAULT_TARGET_CHANNEL["channel_type"]
        )
        logger.info("✅ Default target channel seeded.")

async def main():
    # Start both clients
    await user_client.start()
    await bot_client.start(bot_token=bot_token)
    await initialize_default_channels()
    if not await check_bot_admin():
        logger.error("Bot lacks admin rights; exiting.")
        return
    logger.info("🚀 Bot is running.")
    await user_client.run_until_disconnected()

app = Flask(__name__)
@app.route("/")
def home():
    return "Telegram Bot is running!", 200

def start_bot():
    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()

if __name__ == "__main__":
    start_bot()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
