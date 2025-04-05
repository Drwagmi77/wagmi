import re
import asyncio
import logging
import os
import threading
from datetime import datetime

from flask import Flask
from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.tl.functions.channels import GetParticipantRequest

# ===== LOGGING CONFIGURATION =====
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)
log_file = os.path.join(LOG_DIR, "bot_logs.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file, mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("Logging setup complete. Bot is starting...")

# Suppress Telethon debug logs
logging.getLogger("telethon").setLevel(logging.WARNING)

# ===== CONFIGURATION =====
api_id = 28885685
api_hash = 'c24e850a947c003557f614d6b34035d9'
user_session = 'fumble'  # User session file
bot_session = 'tumble'   # Bot session file
bot_token = '7886946660:AAGXvcV7FS5uFduFUVGGzwwWg1kfua_Pzco'

SOURCE_CHANNEL_ID = -1001998961899   # Source channel (@gem_tools_calls)
TARGET_CHANNEL_ID = -1002405509240    # Target channel (Wagmi Vip ☢️)

custom_gif_path = "https://dl.dropboxusercontent.com/scl/fi/u6r3x30cno1ebmvbpu5k1/video.mp4?rlkey=ytfk8qkdpwwm3je6hjcqgd89s&st=vxjkqe6s"

# ===== INITIALIZE TELETHON CLIENTS =====
user_client = TelegramClient(user_session, api_id, api_hash)
bot_client = TelegramClient(bot_session, api_id, api_hash)

# Global mapping to track processed tokens by token symbol (in lowercase)
token_mapping = {}

# ===== HELPER FUNCTIONS =====
def extract_contract(text: str) -> str | None:
    pattern = r"Contract Address:\s*\n\s*([A-Za-z0-9]{32,50})"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"Ca\s*:\s*([A-Za-z0-9]{32,50})", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    tokens = re.findall(r'\b[A-Za-z0-9]{32,50}\b', text)
    return tokens[0].strip() if tokens else None

def parse_tff_output(text: str) -> dict:
    data = {}
    m = re.search(r"📌\s*([^\n⚠]+)", text)
    if m:
        token_name = m.group(1).strip()
    else:
        m = re.search(r"💊\s*([^\s(]+)", text)
        token_name = m.group(1).strip() if m else "unknown token"
    token_name = token_name.split()[0].lower()
    data["token_name"] = token_name

    m = re.search(r"🌿\s*Mint:\s*(\w+)", text)
    data["mint_status"] = m.group(1).strip() if m else "N/A"

    m = re.search(r"Liq:\s*\$?([\d\.Kk]+)", text)
    data["liquidity_status"] = m.group(1).strip() if m else "N/A"

    m = re.search(r"MC:\s*\$?([\d\.Kk]+)", text)
    data["market_cap"] = m.group(1).strip() if m else "N/A"

    logger.info("TFF output parsed for token '%s'.", data["token_name"])
    return data

# --- Updated message template function ---
def build_new_template(token_name: str, contract: str, market_cap: str,
                       liquidity_status: str, mint_status: str, chart_url: str) -> str:
    template = (
        "🚀 New 💎 GEM Just Landed! 🚀\n\n"
        f"💰 {token_name} – Ape in Before Liftoff!\n\n"
        "💬 \"Degens move fast. Follow my lead, and wealth will follow. I have paved the way before—and I will do it again.\"\n\n"
        f"📊 Market Cap: {market_cap}\n"
        f"💦 Liquidity: {liquidity_status}\n"
        f"🔥 Minting: {mint_status}\n\n"
        f"🔗 Contract: {contract}\n"
        "🌐 Network: #SOL"
    )
    return template

def build_inline_buttons(tff_data: dict) -> list:
    contract = tff_data.get("contract", "")
    row1 = [
        Button.url("View Chart", f"https://dexscreener.com/solana/{contract}"),
        Button.url("Trojan", f"https://t.me/solana_trojanbot?start=r-ttf-{contract}")
    ]
    row2 = [
        Button.url("Bit Foot", f"https://t.me/BitFootBot?start=buy={contract}Solana_ttfbot"),
        Button.url("MEVX", f"https://t.me/MevxTradingBot?start={contract}")
    ]
    row3 = [
        Button.url("Algora", f"https://t.me/algoratradingbot?start=r-tff-{contract}")
    ]
    row4 = [
        Button.url("GMGN", f"https://t.me/GMGN_sol03_bot?start=i_30I510nA_c{contract}"),
        Button.url("Nova", f"https://t.me/TradeonNovaBot?start=r-VF5CM4TB-{contract}")
    ]
    return [row1, row2, row3, row4]

async def get_tff_data(contract: str) -> dict:
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    async def handler(event):
        received_text = event.raw_text.strip()
        received_time = event.date.strftime('%H:%M:%S') if event.date else "unknown"
        logger.info("TFF bot response received at %s.", received_time)
        if received_text.startswith("📌") or received_text.startswith("💊"):
            if not future.done():
                future.set_result(event)
        else:
            async def set_fallback():
                await asyncio.sleep(5)
                if not future.done():
                    logger.info("No preferred emoji in TFF response; using fallback message.")
                    future.set_result(event)
            asyncio.create_task(set_fallback())

    user_client.add_event_handler(handler, events.NewMessage(chats='@ttfbotbot'))
    send_time = datetime.now().strftime('%H:%M:%S')
    logger.info("Sending contract %s to TFF bot at %s.", contract, send_time)
    await user_client.send_message('@ttfbotbot', contract)
    try:
        event = await asyncio.wait_for(future, timeout=90)
    except asyncio.TimeoutError:
        logger.error("Timeout waiting for TFF bot's response.")
        user_client.remove_event_handler(handler)
        raise
    user_client.remove_event_handler(handler)
    tff_text = event.raw_text
    logger.info("Full TFF bot response: %s", tff_text)
    tff_data = parse_tff_output(tff_text)
    tff_data["contract"] = contract
    return tff_data

async def check_bot_admin():
    """Check if the bot has admin rights in the target channel."""
    try:
        bot_me = await bot_client.get_me()
        participant = await bot_client(GetParticipantRequest(
            channel=TARGET_CHANNEL_ID,
            participant=bot_me
        ))
        if not isinstance(participant.participant, (ChannelParticipantAdmin, ChannelParticipantCreator)):
            logger.error("Bot is not an admin in the target channel. Please add the bot as an admin.")
            return False
        logger.info("Bot has admin rights in the target channel.")
        return True
    except Exception as e:
        logger.error("Error checking bot admin rights: %s", e)
        return False

# ===== UNIFIED EVENT HANDLER =====
@user_client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
async def unified_handler(event):
    text = event.raw_text
    logger.info("Received message:\n%s", text)

    # Use a robust regex to detect update messages (i.e. market cap change with an arrow)
    update_pattern = re.compile(r"MC:\s*\$?[\d\.Kk]+\s*(->|[-–>→])\s*\$?[\d\.Kk]+", re.IGNORECASE)
    is_update = update_pattern.search(text)

    if is_update:
        # Process as an update message
        token_match = re.search(r"\$(\w+)", text)
        token_symbol = token_match.group(1).lower().strip() if token_match else "unknown"
        contract = token_mapping.get(token_symbol, "unknown_contract")
        if contract == "unknown_contract":
            logger.info("Token '%s' not found in stored mapping; defaulting contract to unknown_contract.", token_symbol)
        profit_match = re.search(r"(\d+)%", text)
        profit_text = f"{profit_match.group(1)}%" if profit_match else "PROFITS"

        # Build fallback Trojan URL based on contract
        trojan_url = (f"https://t.me/solana_trojanbot?start=r-ttf-{contract}"
                      if contract != "unknown_contract" else "https://t.me/solana_trojanbot")

        mc_match = re.search(r"MC:\s*\$?([\d\.Kk]+)\s*(->|[-–>→])\s*\$?([\d\.Kk]+)", text, re.IGNORECASE)
        if mc_match:
            old_mc, new_mc = mc_match.group(1), mc_match.group(3)
        else:
            old_mc, new_mc = "N/A", "N/A"

        update_message = (
            "🚀 EARLY GEM HUNTERS WINNING BIG! 💎\n\n"
            f"💵 ${token_symbol.upper()} Market Cap: ${new_mc} 📈\n"
            f"🔥 {profit_text} PROFITS & STILL RUNNING! 🔥\n\n"
            "The next hidden GEM is already loading… Stay sharp! 🚀"
        )
        buttons = [[Button.url("DONT MISS OUT", trojan_url)]]
        try:
            await bot_client.send_file(
                TARGET_CHANNEL_ID,
                file=custom_gif_path,
                caption=update_message,
                buttons=buttons
            )
            logger.info("Update message sent with custom GIF for '%s'.", token_symbol)
        except Exception as e:
            logger.error("Failed to send update message for '%s': %s", token_symbol, e)
    else:
        # Process as an announcement message
        contract = extract_contract(text)
        if not contract:
            logger.info("No contract address found in announcement message; skipping.")
            return

        orig_mc_match = re.search(r"Market Cap:\s*\$?([\d\.Kk]+)", text, re.IGNORECASE)
        orig_market_cap = orig_mc_match.group(1) if orig_mc_match else "N/A"

        orig_token_match = re.search(r"\n\s*(.+?)\s*-\s*\$(\w+)", text)
        if orig_token_match:
            token_full = orig_token_match.group(1).strip()
            token_symbol = orig_token_match.group(2).strip().lower()
        else:
            token_full = None
            token_symbol = None

        try:
            tff_data = await get_tff_data(contract)
        except Exception as e:
            logger.error("Error obtaining TFF bot data: %s", e)
            return

        token_name = token_full if token_full else tff_data.get("token_name", "unknown token")
        market_cap_value = orig_market_cap if orig_market_cap != "N/A" else tff_data.get("market_cap", "N/A")
        tff_data["chart_url"] = f"https://dexscreener.com/solana/{contract}"

        new_text = build_new_template(
            token_name,
            contract,
            market_cap_value,
            tff_data.get("liquidity_status", "N/A"),
            tff_data.get("mint_status", "N/A"),
            tff_data.get("chart_url")
        )
        logger.info("Reformatted announcement for '%s':\n%s", token_name, new_text)
        buttons = build_inline_buttons(tff_data)
        key = token_symbol if token_symbol else token_name.lower()
        token_mapping[key] = contract
        logger.info("Stored token mapping: %s -> %s", key, contract)
        try:
            await bot_client.send_file(
                TARGET_CHANNEL_ID,
                file=custom_gif_path,
                caption=new_text,
                buttons=buttons
            )
            logger.info("Announcement sent to target channel with custom GIF for '%s'.", token_name)
        except Exception as e:
            logger.error("Failed to send announcement: %s", e)

# ===== MAIN TELETHON EXECUTION =====
async def main():
    await user_client.start()
    await bot_client.start(bot_token=bot_token)
    
    # Immediately check bot admin rights and print the result to terminal.
    if not await check_bot_admin():
        return

    # Process only the last message from the source channel on startup.
    logger.info("Processing the last message from the source channel...")
    messages = await user_client.get_messages(SOURCE_CHANNEL_ID, limit=1)
    if messages:
        await unified_handler(messages[0])
    else:
        logger.info("No messages found in the source channel.")

    logger.info("User client and Bot client are running...")
    await user_client.run_until_disconnected()

# ===== FLASK WEB SERVICE SETUP =====
app = Flask(__name__)

@app.route("/")
def index():
    return "Telegram bot is running.", 200

def start_bot():
    asyncio.run(main())

if __name__ == "__main__":
    # Start the Telegram bot in a separate daemon thread.
    threading.Thread(target=start_bot, daemon=True).start()
    # Run the Flask app. Render sets the PORT env variable.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
