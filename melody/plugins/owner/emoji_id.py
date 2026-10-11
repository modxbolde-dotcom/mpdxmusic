from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import MessageEntityType
from melody import bot
from utils.decorators import owner_only, error_handler
from utils.formatters import send_quote

@bot.on_message(filters.command('emojiid'))
@error_handler
@owner_only
async def emoji_id_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not (reply.text or reply.caption):
        await send_quote(message, "🆔 <b>Get a real premium emoji id</b>\n\nReply to a message that contains a premium animated emoji with <code>/emojiid</code> — Telegram itself will tell us the real document id, so it's guaranteed to work.")
        return
    text = reply.text or reply.caption
    entities = reply.entities or reply.caption_entities or []
    custom = [e for e in entities if e.type == MessageEntityType.CUSTOM_EMOJI]
    if not custom:
        await send_quote(message, "❌ That message doesn't contain any premium animated emoji (no <code>custom_emoji</code> entities found).")
        return
    ids = [e.custom_emoji_id for e in custom]
    glyphs = [text[e.offset:e.offset + e.length] for e in custom]
    try:
        from utils.custom_emoji import resolve_custom_emoji
        resolvable = set(await resolve_custom_emoji(client, ids))
    except Exception:
        resolvable = set()
    lines = ['🆔 <b>Real premium emoji ids found:</b>\n']
    for emoji_id, glyph in zip(ids, glyphs):
        status = '✅' if emoji_id in resolvable else '⚠️ not resolvable right now'
        lines.append(f'{status} <code>{glyph}</code> → <code>premium_emoji("{emoji_id}", "{glyph}")</code>')
    lines.append('\n<i>Paste the ✅ ones into utils/formatters.py or a message string and send it through send_quote() — invalid ids always fail safe.</i>')
    await send_quote(message, '\n'.join(lines), client=client)
