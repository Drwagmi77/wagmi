# Ana değişiklik: TTF Bot kaldırıldı, mesaj işleme basitleştirildi.
@user_client.on(events.NewMessage(incoming=True, chats=[c['channel_id'] for c in get_channels_sync('source')]))
async def channel_handler(event):
    chat_id = event.chat_id
    message_id = event.id

    if await is_message_processed(chat_id, message_id):
        return

    await record_processed_message(chat_id, message_id)

    bot_status = await get_bot_setting('bot_status')
    if bot_status != 'running':
        return

    txt = event.raw_text
    contract = extract_contract(txt)
    if not contract:
        return

    if await is_contract_processed(contract):
        return

    await record_processed_contract(contract)

    # TTF Bot yerine direkt işlem:
    token_name = extract_token_name_from_source(txt)
    if token_name == "unknown":
        token_name = "UNKNOWN"

    new_text = (
        f"🚀 *New Token Alert!* 💎\n\n"
        f"💰 ${token_name.upper()}\n\n"
        f"🔗 *Contract:* `{contract}`\n"
        "🌐 *Network:* #SOL"
    )

    buttons = [
        [Button.url("📈 Chart", f"https://dexscreener.com/solana/{contract}")]
    ]

    target_channels = await get_channels('target')
    for target in target_channels:
        try:
            await bot_client.send_message(
                target["channel_id"],
                new_text,
                buttons=buttons
            )
        except Exception as e:
            logger.error(f"Error sending to target {target['channel_id']}: {e}")
