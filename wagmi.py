import os
import logging
import asyncio
import re
from telethon import TelegramClient, events
from PIL import Image, ImageDraw, ImageFont
import io

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')
bot_token = os.environ.get('BOT_TOKEN')
SOURCE_CHANNEL = int(os.environ.get('SOURCE_CHANNEL', '-1001998961899'))
TARGET_CHANNEL = int(os.environ.get('TARGET_CHANNEL', '-1002405509240'))

# Global variables
processed_contracts = set()

def extract_contract(message: str) -> str:
    contract_pattern = r'[1-9A-HJ-NP-Za-km-z]{44}'
    match = re.search(contract_pattern, message)
    return match.group(0) if match else None

def create_announcement_image(contract: str, network: str) -> io.BytesIO:
    img = Image.new('RGB', (800, 400), color=(30, 35, 40))
    d = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except:
        font = ImageFont.load_default()

    d.text((50, 50), f"New {network} GEM Found!", fill=(255, 215, 0), font=font)
    d.text((50, 100), f"Contract: {contract}", fill=(255, 255, 255), font=font)
    d.text((50, 150), "Early hunters winning big! 💎", fill=(255, 255, 255), font=font)
    
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr

async def main():
    client = TelegramClient('bot', api_id, api_hash).start(bot_token=bot_token)
    
    @client.on(events.NewMessage(chats=SOURCE_CHANNEL))
    async def handler(event):
        contract = extract_contract(event.text)
        if not contract or contract in processed_contracts:
            return
            
        processed_contracts.add(contract)
        network = "Solana" if "#SOL" in event.text else "Ethereum"
        
        image = create_announcement_image(contract, network)
        await client.send_file(
            TARGET_CHANNEL,
            file=image,
            caption=f"New {network} GEM!\nContract: {contract}"
        )
        logger.info(f"Processed contract: {contract}")

    logger.info("Bot started successfully")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
