from __future__ import annotations
import inspect
import re
from typing import Any, Optional
from pyrogram.types import InlineKeyboardButton as _RawButton
try:
    _PARAMS = set(inspect.signature(_RawButton.__init__).parameters)
except (TypeError, ValueError):
    _PARAMS = set()
SUPPORTS_ICON: bool = 'icon_custom_emoji_id' in _PARAMS
SUPPORTS_STYLE: bool = 'style' in _PARAMS

class _StyleFallback(str):
    pass

def _load_styles():
    try:
        from pyrogram.enums import ButtonStyle as _BS
        return _BS
    except Exception:

        class _BS:
            DEFAULT = _StyleFallback('default')
            PRIMARY = _StyleFallback('primary')
            SUCCESS = _StyleFallback('success')
            DANGER = _StyleFallback('danger')
            WARNING = _StyleFallback('warning')
            SECONDARY = _StyleFallback('secondary')
        return _BS
ButtonStyle = _load_styles()
STYLE_DEFAULT = getattr(ButtonStyle, 'DEFAULT', None)
STYLE_PRIMARY = getattr(ButtonStyle, 'PRIMARY', STYLE_DEFAULT)
STYLE_SUCCESS = getattr(ButtonStyle, 'SUCCESS', STYLE_DEFAULT)
STYLE_DANGER = getattr(ButtonStyle, 'DANGER', STYLE_DEFAULT)
STYLE_WARNING = getattr(ButtonStyle, 'WARNING', STYLE_DEFAULT)

def label_glyphs(label: str) -> list:
    if not label:
        return []
    try:
        from utils.telegram_html import _EMOJI_SEQ_RE
        return [m.group(0) for m in _EMOJI_SEQ_RE.finditer(label)]
    except Exception:
        return [ch for ch in label if not (ch.isspace() or ch.isalnum())]

def icon_ids_for(label: str) -> list:
    if not label:
        return []
    try:
        from utils.emoji_map import EMOJI_ID_FALLBACKS, EMOJI_ID_MAP, _strip_vs, entity_text
    except Exception:
        return []
    try:
        from utils.emoji_map import _dead_ids
        dead = {str(i) for i in _dead_ids()}
    except Exception:
        dead = set()
    for glyph in label_glyphs(label):
        if entity_text(glyph) is None:
            continue
        key = _strip_vs(glyph)
        candidates = [str(i) for i in EMOJI_ID_FALLBACKS.get(key) or []]
        primary = EMOJI_ID_MAP.get(key)
        if primary and str(primary) in candidates:
            candidates.remove(str(primary))
        if primary:
            candidates.insert(0, str(primary))
        if not candidates:
            try:
                from utils import emoji_search
                looked_up = emoji_search.cached_id(key)
                if looked_up:
                    candidates = [str(looked_up)]
                else:
                    emoji_search.request(key)
            except Exception:
                pass
        alive = [c for c in candidates if c not in dead]
        if alive:
            return alive
    return []

def icon_id_for(label: str) -> Optional[str]:
    ids = icon_ids_for(label)
    return ids[0] if ids else None
_GLYPH_RE = re.compile('[🀀-\U0001faff←-⇿ℹⓂ‼⁉™〰〽㊗㊙⌀-➿⬀-⯿☀-⛿️︎\u200d⃣]+')

def strip_glyphs(text: str) -> str:
    cleaned = _GLYPH_RE.sub(' ', text or '')
    return re.sub('\\s{2,}', ' ', cleaned).strip()
_DANGER_WORDS = ('close', 'stop', 'cancel', 'delete', 'remove', 'ban', 'back', 'off', 'end', '✖', '❌', '⏹', '🗑')
_SUCCESS_WORDS = ('play', 'resume', 'start', 'add', 'on', 'approve', 'enable', 'yes', '▶', '✅', '🟢')

def infer_style(text: str) -> Any:
    low = (text or '').lower()
    if any((w in low for w in _DANGER_WORDS)):
        return STYLE_DANGER
    if any((w in low for w in _SUCCESS_WORDS)):
        return STYLE_SUCCESS
    return STYLE_PRIMARY

def ikb(text: str, *, style: Any=None, icon: Optional[str]=None, auto_icon: bool=True, **kwargs: Any) -> _RawButton:
    if SUPPORTS_STYLE:
        chosen = style if style is not None else infer_style(text)
        if chosen is not None:
            kwargs['style'] = chosen
    if SUPPORTS_ICON:
        chain = [str(icon)] if icon else icon_ids_for(text) if auto_icon else []
        emoji_id = chain[0] if chain else None
        if chain:
            try:
                from utils.emoji_patch import QUARANTINED_IDS, custom_emoji_allowed, _expire_quarantine
                _expire_quarantine()
                if not custom_emoji_allowed():
                    emoji_id = None
                else:
                    blocked = {str(i) for i in QUARANTINED_IDS}
                    emoji_id = next((c for c in chain if c not in blocked), None)
            except Exception:
                pass
        if emoji_id:
            kwargs['icon_custom_emoji_id'] = emoji_id
            cleaned = strip_glyphs(text)
            if cleaned:
                text = cleaned
    try:
        return _RawButton(text, **kwargs)
    except TypeError:
        kwargs.pop('style', None)
        kwargs.pop('icon_custom_emoji_id', None)
        return _RawButton(text, **kwargs)
__all__ = ['ButtonStyle', 'SUPPORTS_ICON', 'SUPPORTS_STYLE', 'STYLE_DEFAULT', 'STYLE_PRIMARY', 'STYLE_SUCCESS', 'STYLE_DANGER', 'STYLE_WARNING', 'icon_id_for', 'icon_ids_for', 'label_glyphs', 'ikb', 'strip_glyphs']
