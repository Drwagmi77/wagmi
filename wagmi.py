import re
import asyncio
import logging
import os
import threading
from datetime import datetime

from flask import Flask
from telethon import TelegramClient, events, Button
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator

# ===== CONFIGURATION =====

api_id = 28885685  # Your API ID
api_hash = 'c24e850a947c003557f614d6b34035d9'  # Your API hash
user_session = 'gobble'  # Using the user session
bot_session = 'wobble'   # Bot client session name
bot_token = '7886946660:AAGXvcV7FS5uFduFUVGGzwwWg1kfua_Pzco'  # Your Bot Token

# Correction: Added '#' to make this a comment
# Channel details:
SOURCE_CHANNEL_ID = -1001998961899  # Source channel (@gem_tools_calls)
TARGET_CHANNEL_ID = -1002405509240   # Target channel (Wagmi Vip ☢️)
TARGET_CHANNEL_TITLE = "Wagmi Vip ☢️"  # For logging purposes

# Correction: Added '#' to make this a comment
# Custom file path – using the new video link as our custom GIF path.
custom_gif_path = "https://dl.dropboxusercontent.com/scl/fi/u6r3x30cno1ebmvbpu5k1/video.mp4?rlkey=ytfk8qkdpwwm3je6hjcqgd89s&st=vxjkqe6s"

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# Correction: Changed 'name' to '__name__'
logger = logging.getLogger(__name__)

# ===== INITIALIZE CLIENTS =====
user_client = TelegramClient(user_session, api_id, api_hash)
bot_client = TelegramClient(bot_session, api_id, api_hash)

# Global mapping to track processed tokens by token name.
# We now store token names in lowercase.
token_mapping = {}

# ===== FLASK APP FOR RENDER DETECTION =====
# Correction: Changed 'name' to '__name__'
app = Flask(__name__)

@app.route('/')
def index():
    return "Service is running", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    # Run on all interfaces so that Render can access the service.
    app.run(host='0.0.0.0', port=port)

# ===== HELPER FUNCTIONS =====

def extract_contract(text: str) -> str | None:
    """
    Extracts a Solana contract address.
    """
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
    """
    Parses the output from TFF bot and returns a dictionary with:
    token_name, market_cap, mint_status, and liquidity_status.
    Handles messages starting with both "📌" and "💊".
    """
    data = {}
    # Try to extract token name using the "📌" pattern first.
    m = re.search(r"📌\s*([^\n⚠]+)", text)
    if m:
        token_name = m.group(1).strip()
    else:
        # Fallback for messages starting with "💊": extract the first word after the emoji.
        m = re.search(r"💊\s*([^\s(]+)", text)
        token_name = m.group(1).strip() if m else "unknown token"
    # Remove any trailing parentheses and their contents.
    token_name = re.sub(r"\s*\(.*\)", "", token_name).strip()
    data["token_name"] = token_name.lower()

    # Attempt to extract mint status, if present.
    m = re.search(r"🌿\s*Mint:\s*(\w+)", text)
    data["mint_status"] = m.group(1).strip() if m else "N/A"

    # Extract liquidity status; handle optional dollar sign.
    m = re.search(r"Liq:\s*\$?([\d\.Kk]+)", text)
    data["liquidity_status"] = m.group(1).strip() if m else "N/A"

    # Extract market cap; handle optional dollar sign.
    m = re.search(r"MC:\s*\$?([\d\.Kk]+)", text)
    data["market_cap"] = m.group(1).strip() if m else "N/A"

    logger.info("Parsed TFF output (updated for 💊 messages) successfully.")
    return data

def build_new_template(token_name: str, contract: str, market_cap: str,
                       liquidity_status: str, mint_status: str, chart_url: str) -> str:
    """
    Builds the new announcement message template.
    """
    template = (
        "🚀 New 💎 GEM Just Landed! 🚀\n\n"
        f"💰 {token_name.capitalize()}– Ape in Before Liftoff!\n\n"
        "💬 \"Degens move fast. Follow my lead, and wealth will follow. I have paved the way before—and I will do it again.\"\n\n"
        f"📊 Market Cap: {market_cap}\n"
        f"💦 Liquidity: {liquidity_status}\n"
        f"🔥 Minting: {mint_status}\n\n"
        f"🔗 Contract: {contract}\n"
        "🌐 Network: #SOL\n\n"
        "🔥 100x or dust—are you printing or fading? 🔥\n"
        "🔹 Real-time chart tracking to keep you ahead!\n"
        "🔹 Maximum hype—early apes prosper first!\n\n"
        "⚔️ Ape in & send this 💎 GEM to Valhalla! ⚔️\n"
        "📢 Tag the squad and let’s all rise to riches together.\n\n"
        "⚠️ Degens, remember:\n"
        "🔸 Always secure your profits—don’t hold on to bags too long.\n"
        "🔸 Ride the wave, but never let greed overtake you.\n"
        "🔸 DYOR & only invest what you can afford to lose."
    )
    return template

def build_inline_buttons(tff_data: dict) -> list:
    """
    Builds inline buttons for the initial announcement message.
    """
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
    """
    Registers the handler before sending the contract, then sends the contract to the TFF bot
    and waits for its detailed response.
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    async def handler(event):
        received_text = event.raw_text.strip()
        received_time = event.date.strftime('%H:%M:%S') if event.date else "unknown"
        logger.info(f"Received message from TFF bot at {received_time}")
        # If the message starts with "📌" or "💊", accept immediately.
        if received_text.startswith("📌") or received_text.startswith("💊"):
            if not future.done():
                future.set_result(event)
        else:
            # For any other message, wait a short period (e.g. 5 seconds)
            # to give a chance for a proper response to arrive.
            async def set_fallback():
                await asyncio.sleep(5)
                if not future.done():
                    logger.info("No preferred emoji found; accepting fallback message.")
                    future.set_result(event)
            asyncio.create_task(set_fallback())

    # Register the event handler BEFORE sending the message.
    user_client.add_event_handler(handler, events.NewMessage(chats='@ttfbotbot'))
    send_time = datetime.now().strftime('%H:%M:%S')
    logger.info(f"Sending contract {contract} to TFF bot at {send_time}.")
    await user_client.send_message('@ttfbotbot', contract)
    logger.info(f"Sent contract {contract} to TFF bot, waiting for response...")
    try:
        event = await asyncio.wait_for(future, timeout=90)
    except asyncio.TimeoutError:
        logger.error("Timeout waiting for TFF bot's response.")
        user_client.remove_event_handler(handler)
        raise
    # Remove the handler once we have the response.
    user_client.remove_event_handler(handler)
    tff_text = event.raw_text
    logger.info(f"Full TFF bot response:\n{tff_text}")
    tff_data = parse_tff_output(tff_text)
    tff_data["contract"] = contract  # Ensure contract is set
    return tff_data

# ===== EVENT HANDLERS =====

# 1. Main handler for new token announcements from the source channel.
@user_client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
async def message_handler(event):
    text = event.raw_text
    logger.info(f"Received message from source channel:\n{text}")

    contract = extract_contract(text)
    if not contract:
        logger.warning("No contract address found in message, skipping.")
        return

    try:
        tff_data = await get_tff_data(contract)
    except Exception as e:
        logger.error(f"Error obtaining TFF bot data: {e}")
        return

    # Use normalized token name from TFF bot (which is in lowercase)
    token_name = tff_data.get("token_name", "unknown token")
    tff_data["contract"] = contract
    tff_data["chart_url"] = f"https://dexscreener.com/solana/{contract}"

    new_text = build_new_template(
        token_name,
        contract,
        tff_data.get("market_cap", "N/A"),
        tff_data.get("liquidity_status", "N/A"),
        tff_data.get("mint_status", "N/A"),
        tff_data.get("chart_url", f"https://dexscreener.com/solana/{contract}")
    )
    logger.info(f"Reformatted Message:\n{new_text}")

    buttons = build_inline_buttons(tff_data)
    # Store the token name (normalized) and its contract for later update messages.
    token_mapping[token_name] = contract

    try:
        await bot_client.send_file(
            TARGET_CHANNEL_ID,
            file=custom_gif_path,
            caption=new_text,
            buttons=buttons
        )
        logger.info("Reformatted message with custom GIF sent to target channel successfully.")
    except Exception as e:
        logger.error(f"Failed to send message to target channel: {e}")

# 2. Handler for token update messages from the source channel.
@user_client.on(events.NewMessage(chats=SOURCE_CHANNEL_ID))
async def token_update_handler(event):
    text = event.raw_text
    logger.info(f"Received update message:\n{text}")

    # Check if the message appears to be an update message.
    if "MC:" in text and ("PROFIT" in text or "x" in text):
        # Try to extract token name using the pattern "$TokenName"
        token_match = re.search(r"\$(\w+)", text)
        token_name = token_match.group(1).lower().strip() if token_match else None

        # Fallback: if token name not found, check if any known token name from TFF exists in the text.
        if not token_name:
            for stored_token in token_mapping.keys():
                if stored_token in text.lower():
                    token_name = stored_token
                    break

        if not token_name:
            logger.warning("No token name found in update message, skipping update.")
            return

        # Ensure this token has been processed in a prior announcement.
        if token_name not in token_mapping:
            logger.warning(f"Token {token_name} not processed yet; skipping update.")
            return

        # Extract market cap changes.
        mc_match = re.search(r"MC:\s*\$?([\dKk]+)\s*(?:->|[-–>→])\s*\$?([\dKk]+)", text)
        if mc_match:
            old_mc, new_mc = mc_match.group(1), mc_match.group(2)
        else:
            old_mc, new_mc = "N/A", "N/A"

        # Extract profit information.
        profit_match = re.search(r"(\d+)%", text)
        profit_text = f"{profit_match.group(1)}%" if profit_match else "PROFITS"

        # Determine Trojan URL: either extract from message or build using stored contract.
        trojan_match = re.search(r"(https://t\.me/solana_trojanbot\?start=\S+)", text)
        if trojan_match:
            trojan_url = trojan_match.group(1)
        else:
            contract = token_mapping.get(token_name)
            if contract:
                trojan_url = f"https://t.me/solana_trojanbot?start=r-ttf-{contract}"
            else:
                return

        # Build the update message.
        update_message = (
            "🚀 EARLY GEM HUNTERS WINNING BIG! 💎\n\n"
            f"💵 ${token_name.capitalize()} MC: ${old_mc} → ${new_mc} 📈\n"
            f"🔥 {profit_text} PROFITS & STILL RUNNING! 🔥\n\n"
            "The next hidden GEM is already loading… Stay sharp! 🚀"
        )

        buttons = [[Button.url("DONT MISS OUT", trojan_url)]]
        # Ensure the reply is threaded by specifying reply_to the original update message.
        await event.reply(update_message, buttons=buttons, reply_to=event.message.id)

# ===== MAIN EXECUTION =====

async def main():
    await user_client.start()
    await bot_client.start(bot_token=bot_token)
    logger.info("User client (gobble) and Bot client (wobble) are running...")

    # Run the user client until disconnected.
    await user_client.run_until_disconnected()

# Correction: Changed 'name' to '__name__' and fixed the string comparison
if __name__ == "__main__":
    # Start the Flask web server in a background thread so Render detects a web service.
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutdown gracefully")
