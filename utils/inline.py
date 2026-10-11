from __future__ import annotations
from utils.branding import BOT_NAME
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from utils.buttons import STYLE_DANGER, STYLE_DEFAULT, STYLE_PRIMARY, STYLE_SUCCESS, ikb as sbtn

class Style:
    DEFAULT = STYLE_DEFAULT
    PRIMARY = STYLE_PRIMARY
    DANGER = STYLE_DANGER
    SUCCESS = STYLE_SUCCESS

def _glyph(primary: str, fallback: str='') -> str:
    try:
        from utils.emoji_map import EMOJI_ID_MAP
    except Exception:
        return primary
    if primary in EMOJI_ID_MAP or primary.rstrip('️') in EMOJI_ID_MAP:
        return primary
    return fallback or primary
QUEUE_ICON_ID = '6154222749691679144'
AUTOPLAY_ON_ICON_ID = '5226702984204797593'
AUTOPLAY_OFF_ICON_ID = '5098211821999883164'
BRAND_ICON_ID = '4916169087698076775'
BRAND_LABEL = f'- {BOT_NAME}'
PE = {'play': '▶', 'pause': '⏸', 'replay': '🔄', 'skip': '⏭', 'stop': '⏹', 'close': '❌', 'queue': '📋', 'auto': '🔁', 'bot': '🤖', 'music': '🎵', 'note': '🎶', 'live': '🔴', 'fast': '⚡', 'search': '🔍', 'info': 'ℹ', 'panel': '🔧'}

def deco(key: str, label: str='', back_key: str | None=None) -> str:
    front = _glyph(PE.get(key, '')) or _glyph(PE.get(back_key or key, ''))
    if not label:
        return front
    return f'{front} {label}'.strip()
CLOSE_LABEL = deco('close', 'Close')

class Inline:
    ikm = InlineKeyboardMarkup

    def controls(self, chat_id: int, paused: bool=False, status: str | None=None, remove: bool=False) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if status:
            rows.append([sbtn(deco('live', status, 'live'), style=Style.DANGER if paused else Style.PRIMARY, callback_data=f'controls status {chat_id}')])
        if not remove:
            rows.append([sbtn(deco('play' if paused else 'pause'), style=Style.SUCCESS if paused else Style.PRIMARY, callback_data=f"controls {('resume' if paused else 'pause')} {chat_id}"), sbtn(deco('replay'), style=Style.PRIMARY, callback_data=f'controls replay {chat_id}'), sbtn(deco('skip'), style=Style.PRIMARY, callback_data=f'controls skip {chat_id}'), sbtn(deco('stop'), style=Style.DANGER, callback_data=f'controls stop {chat_id}')])
        return self.ikm(rows)

    def play_card(self, chat_id: int, chat_title: str, autoplay_on: bool=False, paused: bool=False, bot_username: str | None=None, bot_name: str=f'{BOT_NAME}') -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = list(self.controls(chat_id, paused=paused).inline_keyboard)
        rows.append([sbtn('Queue', style=Style.PRIMARY, icon=QUEUE_ICON_ID, callback_data='queue'), sbtn('Auto On' if autoplay_on else 'Auto Off', style=Style.SUCCESS if autoplay_on else Style.DEFAULT, icon=AUTOPLAY_ON_ICON_ID if autoplay_on else AUTOPLAY_OFF_ICON_ID, callback_data='autoplay_toggle')])
        utility_row: list[InlineKeyboardButton] = []
        utility_row.append(sbtn(BRAND_LABEL, style=Style.SUCCESS, icon=BRAND_ICON_ID, url=f'https://t.me/{bot_username}?startgroup=true') if bot_username else sbtn(BRAND_LABEL, style=Style.PRIMARY, icon=BRAND_ICON_ID, callback_data='noop'))
        rows.append(utility_row)
        rows.append([self.close_button()])
        return self.ikm(rows)

    def close_button(self) -> InlineKeyboardButton:
        return sbtn(CLOSE_LABEL, style=Style.DANGER, callback_data='close')

    def close_markup(self) -> InlineKeyboardMarkup:
        return self.ikm([[self.close_button()]])

    def queued(self, chat_id: int, item_id: str | int, label: str='Play Now') -> InlineKeyboardMarkup:
        return self.ikm([[sbtn(deco('fast', label, 'play'), style=Style.PRIMARY, callback_data=f'controls force {chat_id} {item_id}')], [self.close_button()]])

    def yt_key(self, link: str) -> InlineKeyboardMarkup:
        return self.ikm([[sbtn(deco('search', 'YouTube', 'music'), style=Style.PRIMARY, url=link), self.close_button()]])
inline = Inline()
