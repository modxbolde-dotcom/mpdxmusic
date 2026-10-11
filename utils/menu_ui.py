"""Shared inline-menu text and keyboards for /start, /help and group greetings."""
from __future__ import annotations

import html

from pyrogram.types import InlineKeyboardMarkup

from melody.config import Config
from utils.branding import BOT_NAME, BOT_TAGLINE
from utils.buttons import ikb, STYLE_DANGER, STYLE_PRIMARY, STYLE_SUCCESS

ADD_TO_GROUP_URL = 'https://t.me/{username}?startgroup=true'
SECTION_PREFIX = 'mb_help_'


def _bot_username() -> str:
    return (Config.BOT_USERNAME or '').lstrip('@')


def add_group_url() -> str:
    username = _bot_username()
    return ADD_TO_GROUP_URL.format(username=username) if username else 'https://t.me/telegram'


HELP_SECTIONS: tuple[tuple[str, str, str], ...] = (
    (
        'playback',
        '▶️ Playback',
        '<b>Playback</b>\n\n'
        '<code>/play</code> — song name ya YouTube link se audio play\n'
        '<code>/vplay</code> — video stream play\n'
        '<code>/playforce</code> · <code>/vplayforce</code> — queue skip karke turant play\n'
        '<code>/pause</code> · <code>/resume</code> — rokna ya wapas chalana\n'
        '<code>/skip</code> — agla track\n'
        '<code>/stop</code> — playback band aur queue clear\n'
        '<code>/replay</code> — current track dobara shuru',
    ),
    (
        'controls',
        '🎛 Controls',
        '<b>Controls</b>\n\n'
        '<code>/volume 1-200</code> — volume set\n'
        '<code>/mute</code> · <code>/unmute</code> — stream mute\n'
        '<code>/loop</code> · <code>/loopall</code> · <code>/noloop</code> — loop modes\n'
        '<code>/shuffle</code> — queue shuffle\n'
        '<code>/seek</code> · <code>/seekback</code> — aage ya peeche jao\n'
        '<code>/speed 0.5-2.0</code> — playback speed\n'
        '<code>/playmode</code> — access aur search mode',
    ),
    (
        'queue',
        '📋 Queue & Playlist',
        '<b>Queue &amp; Playlist</b>\n\n'
        '<code>/queue</code> — chal rahi queue\n'
        '<code>/np</code> — now playing card\n'
        '<code>/queuepos</code> — queue position\n'
        '<code>/remove</code> · <code>/clearqueue</code> — track hatao ya queue clear\n'
        '<code>/playlist</code> — poori YouTube playlist queue karo\n'
        '<code>/addplaylist</code> · <code>/myplaylist</code> — apni playlist save aur dekho\n'
        '<code>/playplaylist</code> · <code>/delplaylist</code> — play ya delete',
    ),
    (
        'extras',
        '🎧 Extras',
        '<b>Extras</b>\n\n'
        '<code>/search</code> — YouTube search results\n'
        '<code>/lyrics</code> — song lyrics\n'
        '<code>/dl</code> — song ya video file download\n'
        '<code>/live</code> · <code>/radio</code> — live aur radio streams\n'
        '<code>/autoplay</code> — related songs auto queue\n'
        '<code>/channelplay</code> — channel voice chat link karo',
    ),
    (
        'channel',
        '📺 Channel',
        '<b>Channel voice chat</b>\n\n'
        'Channel commands <code>c</code> se shuru hote hain:\n'
        '<code>/cplay</code> · <code>/cvplay</code> — channel VC me play\n'
        '<code>/cpause</code> · <code>/cresume</code> · <code>/cskip</code> · <code>/cstop</code>\n'
        '<code>/cqueue</code> · <code>/cnp</code> — queue aur now playing\n'
        '<code>/cvolume</code> · <code>/cmute</code> · <code>/cunmute</code>\n'
        '<code>/cseek</code> · <code>/cseekback</code> · <code>/cspeed</code>\n'
        '<code>/cshuffle</code> · <code>/cloop</code> · <code>/cloopall</code> · <code>/cnoloop</code>',
    ),
    (
        'tools',
        '🛠 Utilities',
        '<b>Utilities</b>\n\n'
        '<code>/ping</code> — latency check\n'
        '<code>/alive</code> — uptime aur status\n'
        '<code>/stats</code> — bot statistics\n'
        '<code>/about</code> — bot info\n'
        '<code>/help</code> — yeh menu kabhi bhi kholo',
    ),
)

SECTION_MAP: dict[str, tuple[str, str]] = {key: (label, body) for key, label, body in HELP_SECTIONS}


def home_text(user_first_name: str = '') -> str:
    greeting = f'Hey {html.escape(user_first_name)}!' if user_first_name else 'Hey there!'
    return (
        f'<blockquote><b>{html.escape(BOT_NAME)}</b> — {html.escape(BOT_TAGLINE)}</blockquote>\n\n'
        f'{greeting}\n\n'
        'Main group aur channel voice chats me high quality music stream karta hoon.\n\n'
        '<b>Shuru kaise karein</b>\n'
        '1. Mujhe apne group me add karo\n'
        '2. Voice chat start karo\n'
        '3. <code>/play song name</code> bhejo\n\n'
        'Neeche buttons se saare commands dekh sakte ho.'
    )


def group_text() -> str:
    return (
        f'<blockquote><b>{html.escape(BOT_NAME)}</b> — {html.escape(BOT_TAGLINE)}</blockquote>\n\n'
        'Voice chat online hai. <code>/play song name</code> bhejo aur music shuru.\n'
        'Saari commands ke liye neeche <b>Help</b> dabao.'
    )


def welcome_text(group_title: str, added_by: str = '') -> str:
    added = f'\n👤 Added by: {added_by}' if added_by else ''
    return (
        f'<blockquote><b>{html.escape(BOT_NAME)}</b> joined <b>{html.escape(group_title)}</b></blockquote>\n'
        f'{added}\n\n'
        'Thanks for adding me! 🎶\n\n'
        '<b>Setup</b>\n'
        '• Mujhe <b>admin</b> banao — Delete messages, Invite users, Manage video chats\n'
        '• Group ka voice chat start karo\n'
        '• <code>/play song name</code> bhejo\n\n'
        'Full command list ke liye <b>Help</b> dabao.'
    )


def home_keyboard(owner: bool = False, private: bool = True) -> InlineKeyboardMarkup:
    rows = [[ikb('➕ Add me to your group', style=STYLE_SUCCESS, url=add_group_url())]]
    if private:
        rows.append([
            ikb('▶️ Play Music', style=STYLE_SUCCESS, switch_inline_query_current_chat=''),
            ikb('📖 Help', style=STYLE_PRIMARY, callback_data='mb_help_home'),
        ])
    else:
        rows.append([ikb('📖 Help', style=STYLE_PRIMARY, callback_data='mb_help_home')])
    rows.append([
        ikb('ℹ️ About', style=STYLE_PRIMARY, callback_data='mb_about'),
        ikb('🏓 Ping', style=STYLE_PRIMARY, callback_data='ping_cb'),
    ])
    if owner:
        rows.append([ikb('👑 Owner Panel', style=STYLE_DANGER, callback_data='owner_panel')])
    rows.append([ikb('✖ Close', style=STYLE_DANGER, callback_data='gc_close')])
    return InlineKeyboardMarkup(rows)


def welcome_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ikb('📖 Help', style=STYLE_PRIMARY, callback_data='mb_help_home'),
         ikb('▶️ Play Music', style=STYLE_SUCCESS, switch_inline_query_current_chat='')],
        [ikb('✖ Close', style=STYLE_DANGER, callback_data='gc_close')],
    ])


def help_text() -> str:
    return (
        f'<blockquote><b>{html.escape(BOT_NAME)}</b> — Help</blockquote>\n\n'
        'Kisi bhi category pe tap karo, uske saare commands mil jayenge.\n'
        'Tip: group me commands chalane ke liye voice chat khula hona chahiye.'
    )


def help_keyboard() -> InlineKeyboardMarkup:
    buttons = [ikb(label, style=STYLE_PRIMARY, callback_data=f'{SECTION_PREFIX}{key}') for key, label, _ in HELP_SECTIONS]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([ikb('➕ Add me to your group', style=STYLE_SUCCESS, url=add_group_url())])
    rows.append([
        ikb('↩ Back', style=STYLE_PRIMARY, callback_data='mb_home'),
        ikb('✖ Close', style=STYLE_DANGER, callback_data='gc_close'),
    ])
    return InlineKeyboardMarkup(rows)


def section_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [ikb('▶️ Play Music', style=STYLE_SUCCESS, switch_inline_query_current_chat='')],
        [ikb('↩ Help menu', style=STYLE_PRIMARY, callback_data='mb_help_home'),
         ikb('✖ Close', style=STYLE_DANGER, callback_data='gc_close')],
    ])


__all__ = [
    'HELP_SECTIONS',
    'SECTION_MAP',
    'SECTION_PREFIX',
    'add_group_url',
    'group_text',
    'help_keyboard',
    'help_text',
    'home_keyboard',
    'home_text',
    'section_keyboard',
    'welcome_keyboard',
    'welcome_text',
]
