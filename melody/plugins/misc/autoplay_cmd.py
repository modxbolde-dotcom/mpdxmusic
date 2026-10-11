from utils.branding import BOT_NAME
import html
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from melody import bot
from melody.core.queue import set_autoplay, is_autoplay_on, get_current, add_to_queue, set_predownloaded
from melody.core.autoplay import prefetch_next
from melody.logging import log_activity
from utils.decorators import admin_or_auth, error_handler
from utils.tasks import spawn

@bot.on_message(filters.command('autoplay') & filters.group)
@error_handler
@admin_or_auth
async def autoplay_cmd(client: Client, message: Message):
    args = message.command
    chat = message.chat
    if len(args) < 2:
        status = await is_autoplay_on(chat.id)
        state = '<b>ON</b> 🟢' if status else '<b>OFF</b> 🔴'
        await message.reply(f'<blockquote>🤖 <b>AutoPlay:</b> {state}</blockquote>\n\nUse <code>/autoplay on</code> or <code>/autoplay off</code> to toggle.', parse_mode=enums.ParseMode.HTML)
        return
    arg = args[1].lower()
    actor = message.from_user
    actor_name = html.escape(actor.first_name) if actor else 'Someone'
    chat_name = html.escape(chat.title or str(chat.id))
    if arg == 'on':
        await set_autoplay(chat.id, True)
        await message.reply(f'<blockquote>🤖 <b>AutoPlay enabled!</b> 🟢</blockquote>\n\n{BOT_NAME} will keep the music going with related songs — the next one is being added to the queue and downloaded right now for zero-gap playback. 🎶', parse_mode=enums.ParseMode.HTML)
        if get_current(chat.id):

            async def _queue_next_autoplay_track():
                track = await prefetch_next(chat.id)
                if track:
                    add_to_queue(chat.id, track)
                    set_predownloaded(chat.id, None)
            spawn(_queue_next_autoplay_track())
        spawn(log_activity(f"🤖 <b>AutoPlay Enabled</b>\n• By: <code>{actor_name}</code> (<code>{(actor.id if actor else '—')}</code>)\n• Chat: <code>{chat_name}</code> (<code>{chat.id}</code>)"))
    elif arg == 'off':
        await set_autoplay(chat.id, False)
        await message.reply('<blockquote>🤖 <b>AutoPlay disabled.</b> 🔴</blockquote>', parse_mode=enums.ParseMode.HTML)
        spawn(log_activity(f"🤖 <b>AutoPlay Disabled</b>\n• By: <code>{actor_name}</code> (<code>{(actor.id if actor else '—')}</code>)\n• Chat: <code>{chat_name}</code> (<code>{chat.id}</code>)"))
    else:
        await message.reply('<b>Usage:</b> <code>/autoplay on</code> or <code>/autoplay off</code>', parse_mode=enums.ParseMode.HTML)
