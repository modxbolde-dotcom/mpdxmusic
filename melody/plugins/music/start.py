"""Minimal /start and /help used when MUSIC_ONLY_MODE is enabled."""
from pyrogram import Client, enums, filters
from pyrogram.types import Message

from melody import bot
from melody.config import Config
from utils.decorators import error_handler
from utils.menu_ui import help_keyboard, help_text, home_keyboard, home_text

_music_only = filters.create(lambda _client, _update: bool(getattr(Config, 'MUSIC_ONLY_MODE', False)))


@bot.on_message(filters.command('start') & filters.private & _music_only)
@error_handler
async def start_command(client: Client, message: Message):
    user = message.from_user
    owner = bool(user and Config.OWNER_ID and user.id == Config.OWNER_ID)
    await message.reply(
        home_text(user.first_name if user else ''),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=home_keyboard(owner, private=True),
    )


@bot.on_message(filters.command('help') & _music_only)
@error_handler
async def help_command(client: Client, message: Message):
    await message.reply(
        help_text(),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=help_keyboard(),
    )
