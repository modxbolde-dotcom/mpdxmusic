from __future__ import annotations
import html
import time
RIBBON = '🧡🤍💚'
FLAGS = '🇮🇳 🤝 🇮🇹'
BULLET = '🔸'
SUB_BULLET = '🔹'
NOTE = '🎶'
SCORE = '🎼'
SPARK = '✨'
ARROW = '➡️'
CROWN = '👑'
OK = '✅'
BAD = '❌'
LIVE = '🔊'

def fancy_title(title: str) -> str:
    from strings.themes import fancy
    return fancy(title)

def headline(title: str, note: bool=False) -> str:
    lead = f'{NOTE} ' if note else f'{SCORE} '
    return f'<blockquote>{RIBBON} {lead}<b>{fancy_title(title)}</b> {RIBBON}</blockquote>'

def card(title: str, body: str, footer: str='', tags: str='') -> str:
    text = headline(title)
    if tags:
        text += f'\n{tags}'
    if body:
        text += f'\n{body}'
    if footer:
        text += f'\n<blockquote>{footer}</blockquote>'
    return text

def esc(value) -> str:
    return html.escape(str(value if value is not None else ''))

def tag(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={int(user_id)}">{esc(name)}</a>'

def tag_list(users, limit: int=12) -> str:
    users = list(users)
    lines = []
    for user in users[:limit]:
        uid = getattr(user, 'id', None)
        name = getattr(user, 'first_name', None) or getattr(user, 'title', None) or 'User'
        lines.append(f'   {BULLET} {(tag(uid, name) if uid else esc(name))}')
    extra = max(0, len(users) - limit)
    if extra:
        lines.append(f'   {BULLET} <i>+{extra} more</i>')
    return '\n'.join(lines) or f'   {BULLET} ➖'

def rule() -> str:
    return '🟧━━━━━⬜━━━━━🟩'

def accent(glyph: str, text: str) -> str:
    return f'{glyph} {ARROW} {text}'

def stamp() -> str:
    return time.strftime('%H:%M:%S', time.localtime())

def meter(count: int, cap: int=10) -> str:
    filled = max(0, min(cap, int(count or 0)))
    return '🟩' * filled + '⚪' * (cap - filled)
