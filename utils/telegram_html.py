import logging
import re
from typing import Awaitable, Callable, Dict
from pyrogram.enums import ParseMode
from pyrogram.errors import RPCError
from utils.emoji_map import EMOJI_ID_MAP
log = logging.getLogger(__name__)
_EMOJI_TAG_RE = re.compile('<emoji id=[\\"\']?(-?\\d+)[\\"\']?>(.*?)</emoji>', re.DOTALL)
_INT64_MAX = 2 ** 63 - 1
_verified_cache: Dict[int, Dict[int, bool]] = {}
_PROTECTED_RE = re.compile('<emoji id=[\\"\']?-?\\d+[\\"\']?>.*?</emoji>|<pre(?:\\s[^>]*)?>.*?</pre>|<code(?:\\s[^>]*)?>.*?</code>', re.DOTALL)
_GLYPH_RE = None

def refresh_glyph_pattern() -> None:
    global _GLYPH_RE
    if not EMOJI_ID_MAP:
        _GLYPH_RE = None
        return
    glyphs = sorted(EMOJI_ID_MAP, key=len, reverse=True)
    _GLYPH_RE = re.compile('(' + '|'.join((re.escape(g) for g in glyphs)) + ')(️?)')
refresh_glyph_pattern()
_EMOJI_SEQ_RE = re.compile('(?:[#*0-9]\\ufe0f?\\u20e3|[🇦-🇿]{2}|(?:[©®‼⁉™ℹ↔-↪⌚-⌛⌨⏏⏩-⏺Ⓜ▪-◾☀-➿⤴-⤵⬀-⯿〰〽㊗㊙🀀-\U0001faff][🏻-🏿]?️?(?:\u200d[☀-➿🀀-\U0001faff][🏻-🏿]?️?)*))')

def _premium_tag(sequence: str) -> str:
    from utils.emoji_map import assign_pool_id, entity_text
    emoji_id = assign_pool_id(sequence)
    if not emoji_id:
        return sequence
    display = entity_text(sequence) or sequence
    return f'<emoji id="{emoji_id}">{display}</emoji>'

def auto_premium_emoji(text: str) -> str:
    if not text:
        return text
    report_unmapped(text)

    def _wrap_glyphs(segment: str) -> str:
        if not segment:
            return segment
        return _EMOJI_SEQ_RE.sub(lambda m: _premium_tag(m.group(0)), segment)
    parts = _PROTECTED_RE.split(text)
    protected = _PROTECTED_RE.findall(text)
    out = [_wrap_glyphs(parts[0])]
    for chunk, tail in zip(protected, parts[1:]):
        out.append(chunk)
        out.append(_wrap_glyphs(tail))
    return ''.join(out)
_ANY_EMOJI_RE = re.compile('[🌀-\U0001faff🀀-\U0001f2ff☀-➿←-⇿⬀-⯿️]')
UNMAPPED_GLYPHS: set = set()

def report_unmapped(text: str) -> None:
    for glyph in _ANY_EMOJI_RE.findall(text or ''):
        if glyph == '️' or glyph in EMOJI_ID_MAP:
            continue
        if glyph in UNMAPPED_GLYPHS:
            continue
        UNMAPPED_GLYPHS.add(glyph)
        try:
            from utils.emoji_map import assign_pool_id
            if assign_pool_id(glyph):
                continue
        except Exception:
            pass
        log.debug('premium-emoji: no custom-emoji id for %r — add one to utils/emoji_ids.py to make it premium too.', glyph)

def strip_out_of_range_emoji(text: str) -> str:

    def _sub(match: 're.Match[str]') -> str:
        emoji_id, inner = (match.group(1), match.group(2))
        if abs(int(emoji_id)) > _INT64_MAX:
            return inner
        return match.group(0)
    return _EMOJI_TAG_RE.sub(_sub, text)

def strip_all_emoji_tags(text: str) -> str:
    return _EMOJI_TAG_RE.sub(lambda m: m.group(2), text)
_TAG_RE = re.compile('<[^>]+>')
_ENTITY_UNESCAPE = (('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"'), ('&amp;', '&'))

def strip_html_tags(text: str) -> str:
    out = _TAG_RE.sub('', text)
    for src, dst in _ENTITY_UNESCAPE:
        out = out.replace(src, dst)
    return out

def strip_blockquote(text: str) -> str:
    return text.replace('<blockquote expandable>', '').replace('<blockquote>', '').replace('</blockquote>', '')

async def verify_custom_emojis(client, text: str) -> str:
    from utils import emoji_patch
    if not emoji_patch.custom_emoji_allowed():
        return strip_all_emoji_tags(text)
    text = auto_premium_emoji(text)
    text = strip_out_of_range_emoji(text)
    if client is None:
        return text
    ids = {int(m.group(1)) for m in _EMOJI_TAG_RE.finditer(text)}
    if not ids:
        return text
    cache = _verified_cache.setdefault(id(client), {})
    unknown = [i for i in ids if i not in cache]
    if unknown:
        from utils.custom_emoji import EmojiResolutionUnavailable, resolve_custom_emoji
        try:
            found_ids = set(await resolve_custom_emoji(client, unknown))
        except EmojiResolutionUnavailable as e:
            log.debug('Skipping custom-emoji verification: %s', e)
            return text
        except Exception as e:
            log.warning('Unexpected error verifying custom emoji ids %s: %s', unknown, e)
            return text
        for i in unknown:
            cache[i] = i in found_ids

    def _sub(match: 're.Match[str]') -> str:
        emoji_id, inner = (int(match.group(1)), match.group(2))
        return match.group(0) if cache.get(emoji_id, True) else inner
    return _EMOJI_TAG_RE.sub(_sub, text)

async def safe_html_action(action: Callable[..., Awaitable], text: str, *, client=None, **kwargs) -> object:
    from pyrogram.errors import MessageNotModified
    prepared = await verify_custom_emojis(client, text)
    try:
        return await action(prepared, parse_mode=ParseMode.HTML, **kwargs)
    except MessageNotModified:
        return None
    except RPCError as e:
        log.warning('safe_html_action: send failed with emoji entities (%s), retrying without them', e)
        from utils import emoji_patch
        if emoji_patch._is_entity_error(e):
            emoji_patch._register_failure(e, prepared)
    stripped = strip_all_emoji_tags(text)
    try:
        return await action(stripped, parse_mode=ParseMode.HTML, **kwargs)
    except MessageNotModified:
        return None
    except RPCError as e:
        log.error('safe_html_action: send failed without emoji entities too (%s), falling back to plain text', e)
    plain = strip_html_tags(strip_blockquote(strip_all_emoji_tags(stripped)))
    return await action(plain, parse_mode=ParseMode.DISABLED, **kwargs)
