import os
import logging
import asyncio
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.custom import Button
from PIL import Image, ImageDraw, ImageFont
import io

# Logging yapılandırması
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Çevresel değişkenler
api_id = os.environ.get('API_ID')
api_hash = os.environ.get('API_HASH')
bot_token = os.environ.get('BOT_TOKEN')
DEFAULT_ADMIN_ID = int(os.environ.get('DEFAULT_ADMIN_ID', '6489451767'))
DEFAULT_SOURCE_CHANNEL = {'channel_id': -1001998961899}  # @gem_tools_calls
DEFAULT_TARGET_CHANNEL = {'channel_id': -1002405509240}  # Wagmi Vip ☢

# Telegram istemcileri
bot_client = TelegramClient('bot', api_id, api_hash).start(bot_token=bot_token)
user_client = TelegramClient('user', api_id, api_hash)

# Veritabanı simülasyonu (gerçek bir DB kullanıyorsan bu kısmı güncelle)
processed_contracts = set()

def get_channels_sync(type):
    if type == 'source':
        return [DEFAULT_SOURCE_CHANNEL]
    elif type == 'target':
        return [DEFAULT_TARGET_CHANNEL]
    return []

def extract_contract(message):
    # Basit bir contract çıkarma mantığı (örneğin, 44 karakterli adresler)
    import re
    contract_pattern = r'[1-9A-HJ-NP-Za-km-z]{44}'
    match = re.search(contract_pattern, message)
    return match.group(0) if match else None

def is_processed_contract(contract):
    return contract in processed_contracts

def record_processed_contract(contract):
    processed_contracts.add(contract)

def parse_ttf_output(text):
    # TTF artık kullanılmıyor, bu fonksiyon boş bırakılabilir
    return {}

def build_new_template(contract, network):
    # TTF’siz GIF şablon
    img = Image.new('RGB', (400, 300), color=(0, 128, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    d.text((10, 10), f"Contract: {contract}", fill=(255, 255, 255), font=font)
    d.text((10, 50), f"Network: {network}", fill=(255, 255, 255), font=font)
    d.text((10, 90), "New GEM Landed! 💎", fill=(255, 255, 255), font=font)
    d.text((10, 130), "Early GEM Hunters Winning Big! 💎", fill=(255, 255, 255), font=font)

    file_content = io.BytesIO()
    img.save(file_content, format='GIF')
    file_content.name = f"gem_announcement_{contract}.gif"
    file_content.seek(0)
    return file_content

def build_update_template(token_name, market_cap, prof):
    # TTF’siz güncelleme şablonu
    img = Image.new('RGB', (400, 300), color=(0, 128, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    d.text((10, 10), f"Token: {token_name}", fill=(255, 255, 255), font=font)
    d.text((10, 50), f"Market Cap: {market_cap}", fill=(255, 255, 255), font=font)
    d.text((10, 90), f"Profit: {prof}", fill=(255, 255, 255), font=font)
    d.text((10, 130), "Early GEM Hunters Winning Big! 💎", fill=(255, 255, 255), font=font)

    file_content = io.BytesIO()
    img.save(file_content, format='GIF')
    file_content.name = f"update_announcement_{token_name}.gif"
    file_content.seek(0)
    return file_content

def build_announcement_buttons(contract):
    return [
        [Button.url("Chart", f"https://dexscreener.com/solana/{contract}")],
        [Button.url("Trojan", f"https://t.me/solana_trojanbot?start=r-ttf-{contract}")],
        [Button.url("Soul", f"https://t.me/soul_sniper_bot?start=4U0hnwLCbX_{contract}")],
        [Button.url("MEVX", f"https://t.me/MEVXTradingBot?start=<contract>{contract}")],
        [Button.url("Algora", f"https://t.me/algoratradingbot?start=r-ttf-{contract}")],
        [Button.url("Trojan N", f"https://t.me/nestor_trojanbot?start=r-shielZukn5b-{contract}")],
        [Button.url("GMGN", f"https://t.me/GMGN_sol3_bot?start=CcJ5m3wJ5JhLpc5mFB0ydeHuKasPQeFa1zLc_{contract}")]
    ]

@user_client.on(events.NewMessage(incoming=True, chats=[c['channel_id'] for c in get_channels_sync('source')]))
async def channel_handler(event):
    chat_id = event.chat_id
    message_id = event.id
    message_text = event.message.message
    logger.info(f"Received message {message_id} from chat {chat_id}: {message_text}")

    contract = extract_contract(message_text)
    if not contract:
        return

    logger.info(f"Processing as new call for contract: {contract} from message {message_id}")
    if is_processed_contract(contract):
        return

    record_processed_contract(contract)
    logger.info(f"Recorded processed contract: {contract}")

    # @ttfbotbot ile TTF sorgusu çıkarılıyor, senin hesabın üzerinden devam ediliyor
    # Mesajdan ağ bilgisini çıkar
    network = "Unknown"  # Varsayılan
    if "#SOL" in message_text:
        network = "#SOL"
    elif "#ETH" in message_text:
        network = "#ETH"
    # Daha fazla ağ eklenebilir

    # TTF’siz şablon oluştur
    template = build_new_template(contract, network)
    buttons = build_announcement_buttons(contract)
    await bot_client.send_file(
        -1002405509240,
        file=template,
        caption=f"New {network} GEM Landed! 💎 {contract}",
        buttons=buttons
    )
    logger.info(f"Sending new call announcement for contract {contract} to target channel")

# Admin panelini devre dışı bırak (yorum satırına al)
# @bot_client.on(events.NewMessage(incoming=True, from_users=[DEFAULT_ADMIN_ID]))
# async def admin_handler(event):
#     if event.message.message == '/start':
#         await event.respond("Hey Boss!")
#         logger.info("Admin panel accessed")

async def main():
    await user_client.start(phone='+905424277677')  # Telefon numarası manuel olarak eklendi
    await bot_client.start()
    await user_client.run_until_disconnected()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
