import functools
import inspect
import logging
import re
import time
from pyrogram import Client
from utils.telegram_html import strip_all_emoji_tags, verify_custom_emojis
log = logging.getLogger(__name__)
_PATCHED_METHODS = (('send_message', 'text'), ('edit_message_text', 'text'), ('edit_message_caption', 'caption'), ('send_photo', 'caption'), ('send_audio', 'caption'), ('send_video', 'caption'), ('send_animation', 'caption'), ('send_document', 'caption'), ('send_voice', 'caption'))
_applied = False
QUARANTINED_IDS: set = set()
_quarantined_at: dict = {}
_QUARANTINE_TTL = 1800.0
TELEGRAM_ENTITY_LIMIT = 100
ENTITY_SAFETY_MARGIN = 4
MAX_EMOJI_ENTITIES = 90
_OTHER_TAG_RE = re.compile('<(b|strong|i|em|u|ins|s|strike|del|code|pre|a|blockquote|spoiler|tg-spoiler)(?:\\s[^>]*)?>', re.IGNORECASE)

def emoji_entity_budget(text: str) -> int:
    others = len(_OTHER_TAG_RE.findall(text or ''))
    return max(8, min(MAX_EMOJI_ENTITIES, TELEGRAM_ENTITY_LIMIT - ENTITY_SAFETY_MARGIN - others))

def _quarantine(emoji_id: str) -> None:
    QUARANTINED_IDS.add(str(emoji_id))
    _quarantined_at[str(emoji_id)] = time.time()

def _expire_quarantine() -> None:
    now = time.time()
    for emoji_id in [i for i, ts in _quarantined_at.items() if now - ts >= _QUARANTINE_TTL]:
        QUARANTINED_IDS.discard(emoji_id)
        _quarantined_at.pop(emoji_id, None)

def cap_emoji_entities(text: str, limit: 'int | None'=None) -> str:
    if not text or '<emoji' not in text:
        return text
    if limit is None:
        limit = emoji_entity_budget(text)
    seen = 0

    def _sub(match):
        nonlocal seen
        seen += 1
        return match.group(0) if seen <= limit else match.group(2)
    return re.compile('<emoji id=[\\"\']?(-?\\d+)[\\"\']?>(.*?)</emoji>', re.DOTALL).sub(_sub, text)
_ENTITY_ERRORS = ('ENTITY_TEXT_INVALID', 'CUSTOM_EMOJI_INVALID', 'MSG_ENTITIES_INVALID', 'ENTITY_BOUNDS_INVALID', 'EMOJI_INVALID', 'EMOJI_NOT_MODIFIED', 'DOCUMENT_INVALID', 'PREMIUM_ACCOUNT_REQUIRED')
_ID_RE = re.compile('<emoji id=[\\"\']?(-?\\d+)[\\"\']?>')

def custom_emoji_allowed() -> bool:
    return True

def _is_entity_error(exc: BaseException) -> bool:
    text = f'{type(exc).__name__}: {exc}'.upper()
    return any((code in text for code in _ENTITY_ERRORS))

def _register_failure(exc: BaseException, text: str) -> None:
    err = str(exc).upper()
    tagged = re.findall('<emoji id=[\\"\']?(-?\\d+)[\\"\']?>(.*?)</emoji>', text or '', re.DOTALL)
    if 'TOO_MUCH' in err or 'TOO_LONG' in err or 'MESSAGE_TOO_LONG' in err:
        log.debug('emoji_patch: entity-count rejection (%s) — ids kept', exc)
        return
    if len(tagged) == 1:
        _quarantine(tagged[0][0])
    else:
        log.debug('emoji_patch: rejection with %d ids — not quarantining any to avoid killing valid premium emoji (%s)', len(tagged), exc)

def _register_success() -> None:
    pass

def drop_quarantined(text: str) -> str:
    if not text or '<emoji' not in text:
        return text
    _expire_quarantine()

    def _bad(emoji_id: str) -> bool:
        return emoji_id in QUARANTINED_IDS
    from utils.emoji_map import EMOJI_ID_FALLBACKS, EMOJI_ID_MAP

    def _remap(glyph: str) -> 'str | None':
        base = glyph.replace('️', '')
        for candidate in EMOJI_ID_FALLBACKS.get(base, [EMOJI_ID_MAP.get(base)]):
            if candidate and (not _bad(str(candidate))):
                return str(candidate)
        return None

    def _sub(match):
        emoji_id, glyph = (match.group(1), match.group(2))
        if not _bad(emoji_id):
            return match.group(0)
        replacement = _remap(glyph)
        if replacement:
            return f'<emoji id="{replacement}">{glyph}</emoji>'
        return glyph
    return cap_emoji_entities(re.compile('<emoji id=[\\"\']?(-?\\d+)[\\"\']?>(.*?)</emoji>', re.DOTALL).sub(_sub, text))

def _wants_html(bound_arguments) -> bool:
    parse_mode = bound_arguments.get('parse_mode')
    name = getattr(parse_mode, 'name', None) or str(parse_mode or '').upper()
    return 'DISABLED' not in name and 'MARKDOWN' not in name

def _wrap(method_name: str, param_name: str) -> None:
    original = getattr(Client, method_name)
    if getattr(original, '_emoji_patched', False):
        return
    sig = inspect.signature(original)

    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        bound = sig.bind_partial(self, *args, **kwargs)
        bound.apply_defaults()
        value = bound.arguments.get(param_name)
        plain_value = value
        if isinstance(value, str) and value:
            if not _wants_html(bound.arguments):
                bound.arguments[param_name] = strip_all_emoji_tags(value)
            elif custom_emoji_allowed():
                try:
                    prepared = await verify_custom_emojis(self, value)
                    bound.arguments[param_name] = drop_quarantined(prepared)
                except Exception:
                    log.exception('emoji_patch: falling back to plain %s()', method_name)
            else:
                bound.arguments[param_name] = strip_all_emoji_tags(value)
        final_value = bound.arguments.get(param_name)
        has_entities = isinstance(final_value, str) and '<emoji' in final_value
        try:
            result = await original(*bound.args, **bound.kwargs)
            if has_entities:
                _register_success()
            return result
        except Exception as exc:
            if not (has_entities and _is_entity_error(exc)):
                raise
            _register_failure(exc, final_value)
        try:
            retried = drop_quarantined(final_value)
            if retried != final_value and '<emoji' in retried:
                bound.arguments[param_name] = retried
                result = await original(*bound.args, **bound.kwargs)
                _register_success()
                return result
        except Exception as exc2:
            if _is_entity_error(exc2):
                _register_failure(exc2, bound.arguments.get(param_name) or '')
            else:
                raise
        bound.arguments[param_name] = strip_all_emoji_tags(plain_value)
        return await original(*bound.args, **bound.kwargs)
    wrapper._emoji_patched = True
    setattr(Client, method_name, wrapper)
_MEDIA_METHODS = (('send_media_group', 'media'), ('edit_message_media', 'media'))

def _wants_html_media(media) -> bool:
    parse_mode = getattr(media, 'parse_mode', None)
    name = getattr(parse_mode, 'name', None) or str(parse_mode or '').upper()
    if parse_mode is None:
        return True
    return 'DISABLED' not in name and 'MARKDOWN' not in name

async def _prepare_media_caption(client, media) -> None:
    caption = getattr(media, 'caption', None)
    if not isinstance(caption, str) or not caption:
        return
    if not _wants_html_media(media):
        try:
            media.caption = strip_all_emoji_tags(caption)
        except Exception:
            pass
        return
    try:
        media.caption = drop_quarantined(await verify_custom_emojis(client, caption))
    except Exception:
        log.exception('emoji_patch: could not premium-wrap a media caption')

def _wrap_media(method_name: str, param_name: str) -> None:
    original = getattr(Client, method_name, None)
    if original is None or getattr(original, '_emoji_patched', False):
        return
    sig = inspect.signature(original)

    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        bound = sig.bind_partial(self, *args, **kwargs)
        bound.apply_defaults()
        value = bound.arguments.get(param_name)
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            if item is not None and hasattr(item, 'caption'):
                await _prepare_media_caption(self, item)
        try:
            return await original(*bound.args, **bound.kwargs)
        except Exception as exc:
            if not _is_entity_error(exc):
                raise
            for item in items:
                if item is not None and isinstance(getattr(item, 'caption', None), str):
                    _register_failure(exc, item.caption)
                    item.caption = strip_all_emoji_tags(item.caption)
            return await original(*bound.args, **bound.kwargs)
    wrapper._emoji_patched = True
    setattr(Client, method_name, wrapper)

def apply_emoji_patch() -> None:
    global _applied
    if _applied:
        return
    for method_name, param_name in _PATCHED_METHODS:
        _wrap(method_name, param_name)
    for method_name, param_name in _MEDIA_METHODS:
        _wrap_media(method_name, param_name)
    _applied = True
    log.info('emoji_patch: premium-emoji auto-wrap active on %s', ', '.join((m for m, _ in _PATCHED_METHODS)))
