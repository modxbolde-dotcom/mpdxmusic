import html
from pyrogram import Client, filters
from pyrogram.types import Message
from melody import bot
from melody.core.queue import get_current, peek_predownloaded
from utils.decorators import error_handler
from utils.formatters import format_duration, send_quote, premium_emoji, PREMIUM_EMOJI_IDS

@bot.on_message(filters.command(['np', 'playing']) & filters.group)
@error_handler
async def nowplaying_cmd(client: Client, message: Message):
    track = get_current(message.chat.id)
    if not track:
        await send_quote(message, '❌ Nothing is playing right now.', client=client)
        return
    _np_emoji = premium_emoji(PREMIUM_EMOJI_IDS['nowplaying'], '🎵')
    text = f'{_np_emoji} **Now Playing**\n\n**{html.escape(track.title[:50])}**\n👤 `{track.uploader}`\n⏱ `{format_duration(track.duration)}`\n🙋 Requested by `{track.requester_name}`'
    up_next = peek_predownloaded(message.chat.id)
    if up_next:
        text += f'\n\n🤖 **Up Next (AutoPlay):**\n`{up_next.title[:45]}`\n🙋 Requested by `AutoPlay`'
    await send_quote(message, text, client=client)
