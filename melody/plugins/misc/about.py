"""/about — short bot profile card."""
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from melody import bot
from utils.branding import BOT_NAME
from utils.buttons import ikb, STYLE_DANGER, STYLE_PRIMARY
from utils.decorators import error_handler
from utils.formatters import PREMIUM_EMOJI_IDS, premium_emoji, send_quote
from strings.themes import fancy

ABOUT_TEXT = (
    f"<blockquote>{premium_emoji(PREMIUM_EMOJI_IDS['about'], '🎶')} "
    f"<b>About {fancy(BOT_NAME)}</b></blockquote>\n\n"
    f'<b>{BOT_NAME}</b> Telegram group aur channel voice chats me\n'
    'high quality YouTube audio stream karta hai.\n\n'
    '<blockquote>🔥 <b>Features</b>\n'
    '  🔸 HD YouTube streaming\n'
    '  🔸 Smart queue management\n'
    '  🔸 AutoPlay with related songs\n'
    '  🔸 Now playing cards\n'
    '  🔸 Channel voice chat support</blockquote>\n\n'
    'Made with 💛 for music lovers'
)


def _markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        ikb('↩ Back', style=STYLE_PRIMARY, callback_data='mb_home'),
        ikb('✖ Close', style=STYLE_DANGER, callback_data='gc_close'),
    ]])


@bot.on_message(filters.command('about'))
@error_handler
async def about_cmd(client: Client, message: Message):
    await send_quote(message, ABOUT_TEXT, client=client, reply_markup=_markup())


@bot.on_callback_query(filters.regex('^about_close$'))
@error_handler
async def about_close_cb(client: Client, cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.answer()
