"""/start, /help entry points and the group welcome greeting."""
import html

from pyrogram import Client, enums, filters
from pyrogram.types import Message

from melody import bot
from melody.config import Config
from melody.logging import log_activity, log_group_event
from melody.plugins.misc.pics import send_with_pic
from utils.database import add_user
from utils.decorators import error_handler
from utils.menu_ui import (
    group_text,
    help_keyboard,
    help_text,
    home_keyboard,
    home_text,
    welcome_keyboard,
    welcome_text,
)
from utils.tasks import spawn


def _is_owner(user) -> bool:
    return bool(user and Config.OWNER_ID and user.id == Config.OWNER_ID)


@bot.on_message(filters.command('start') & filters.private)
@error_handler
async def start_dm(client: Client, message: Message):
    user = message.from_user
    if user:
        spawn(
            add_user(user.id, user.first_name or '', user.username or ''),
            name=f'remember-user-{user.id}',
        )
        spawn(
            log_activity(
                '#start #dm\n<b>DM /start</b>\n'
                f"• User: <code>{html.escape(user.first_name or 'Unknown')}</code> "
                f"(@{html.escape(user.username or '—')})\n"
                f'• User ID: <code>{user.id}</code>'
            ),
            name=f'log-dm-start-{user.id}',
        )
    await send_with_pic(
        client,
        message.chat.id,
        'start',
        home_text(user.first_name if user else ''),
        reply_markup=home_keyboard(_is_owner(user), private=True),
        reply_to=message.id,
    )


@bot.on_message(filters.command('start') & filters.group)
@error_handler
async def start_group(client: Client, message: Message):
    await send_with_pic(
        client,
        message.chat.id,
        'start',
        group_text(),
        reply_markup=home_keyboard(_is_owner(message.from_user), private=False),
        reply_to=message.id,
    )


@bot.on_message(filters.command('help'))
@error_handler
async def help_command(client: Client, message: Message):
    await message.reply(
        help_text(),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=help_keyboard(),
    )


@bot.on_message(filters.new_chat_members)
@error_handler
async def bot_added_to_group(client: Client, message: Message):
    me = await client.get_me()
    members = message.new_chat_members or []
    if not any(member.id == me.id for member in members):
        return

    adder = message.from_user
    group = message.chat
    added_by = ''
    if adder:
        added_by = f'<a href="tg://user?id={adder.id}">{html.escape(adder.first_name or "User")}</a>'

    await send_with_pic(
        client,
        group.id,
        'welcome',
        welcome_text(group.title or str(group.id), added_by),
        reply_markup=welcome_keyboard(),
    )
    spawn(
        log_group_event(
            client,
            'bot_added_to_group',
            group.id,
            actor=adder,
            target=me,
            result='bot joined',
            details=f'Greeting sent in {html.escape(group.title or str(group.id))}.',
        ),
        name=f'log-group-added-{group.id}',
    )
