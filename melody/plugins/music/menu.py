"""Callback handlers behind the /start and /help inline menus."""
from pyrogram import Client, filters
from pyrogram import enums
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup

from melody import bot
from melody.config import Config
from utils.buttons import ikb, STYLE_DANGER, STYLE_PRIMARY
from utils.decorators import error_handler
from utils.menu_ui import (
    SECTION_MAP,
    SECTION_PREFIX,
    help_keyboard,
    help_text,
    home_keyboard,
    home_text,
    section_keyboard,
)

HOME_ALIASES = '^(mb_home|mb_start_home|start_back|owner_back_home)$'
HELP_ALIASES = '^(mb_help_home|help_main|mb_music_help|mb_vc_help)$'


async def _render(query: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
    message = query.message
    try:
        if message.photo or message.video or message.animation or message.document:
            await message.edit_caption(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        else:
            await message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        await query.answer('Menu open nahi ho paaya, dobara try karo.', show_alert=True)
        return
    await query.answer()


@bot.on_callback_query(filters.regex(HOME_ALIASES))
@error_handler
async def menu_home(client: Client, query: CallbackQuery):
    user = query.from_user
    owner = bool(user and Config.OWNER_ID and user.id == Config.OWNER_ID)
    private = query.message.chat.type == enums.ChatType.PRIVATE
    await _render(query, home_text(user.first_name if user else ''), home_keyboard(owner, private=private))


@bot.on_callback_query(filters.regex(HELP_ALIASES))
@error_handler
async def menu_help(client: Client, query: CallbackQuery):
    await _render(query, help_text(), help_keyboard())


@bot.on_callback_query(filters.regex(f'^{SECTION_PREFIX}(?!home$)[a-z_]+$'))
@error_handler
async def menu_section(client: Client, query: CallbackQuery):
    key = (query.data or '')[len(SECTION_PREFIX):]
    section = SECTION_MAP.get(key)
    if not section:
        await query.answer('Yeh section available nahi hai.', show_alert=True)
        return
    _label, body = section
    await _render(query, body, section_keyboard())


@bot.on_callback_query(filters.regex('^mb_about$'))
@error_handler
async def menu_about(client: Client, query: CallbackQuery):
    from melody.plugins.misc.about import ABOUT_TEXT

    markup = InlineKeyboardMarkup([[
        ikb('↩ Back', style=STYLE_PRIMARY, callback_data='mb_home'),
        ikb('✖ Close', style=STYLE_DANGER, callback_data='gc_close'),
    ]])
    await _render(query, ABOUT_TEXT, markup)


@bot.on_callback_query(filters.regex('^gc_close$'))
async def menu_close(client: Client, query: CallbackQuery):
    try:
        await query.message.delete()
    except Exception:
        pass
    try:
        await query.answer()
    except Exception:
        pass
