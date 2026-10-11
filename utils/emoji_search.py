from __future__ import annotations
import asyncio
import json
import logging
import os
from typing import Iterable, Optional
log = logging.getLogger(__name__)
CACHE_PATH = os.environ.get('EMOJI_LOOKUP_CACHE', '/tmp/melody_emoji_lookup.json')
LOOKUP: dict[str, str] = {}
_MISSES: set[str] = set()
_queue: 'asyncio.Queue[str] | None' = None
_worker: 'asyncio.Task | None' = None
_client = None
_dirty = False

def _load() -> None:
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            LOOKUP.update({str(k): str(v) for k, v in data.items() if k and v})
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.debug('emoji_search: could not read cache: %s', exc)

def _save() -> None:
    global _dirty
    if not _dirty:
        return
    try:
        tmp = CACHE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(LOOKUP, fh, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
        _dirty = False
    except Exception as exc:
        log.debug('emoji_search: could not write cache: %s', exc)
_load()

def cached_id(glyph: str) -> Optional[str]:
    return LOOKUP.get(glyph)

def request(glyph: str) -> None:
    if not glyph or glyph in LOOKUP or glyph in _MISSES:
        return
    if _queue is None:
        return
    try:
        _queue.put_nowait(glyph)
    except Exception:
        pass

def _adopt(glyph: str, emoji_id: str) -> None:
    global _dirty
    LOOKUP[glyph] = emoji_id
    _dirty = True
    try:
        from utils.emoji_map import AUTO_ASSIGNED_IDS, EMOJI_ID_FALLBACKS, EMOJI_ID_MAP
        previous = EMOJI_ID_MAP.get(glyph)
        EMOJI_ID_MAP[glyph] = emoji_id
        AUTO_ASSIGNED_IDS[glyph] = emoji_id
        chain = EMOJI_ID_FALLBACKS.setdefault(glyph, [])
        EMOJI_ID_FALLBACKS[glyph] = list(dict.fromkeys([emoji_id, *([previous] if previous else []), *chain]))
        from utils.telegram_html import refresh_glyph_pattern
        refresh_glyph_pattern()
    except Exception as exc:
        log.debug('emoji_search: could not adopt %r: %s', glyph, exc)

async def _search(client, glyph: str) -> Optional[str]:
    from pyrogram import raw
    from utils.custom_emoji import EmojiResolutionUnavailable, resolve_custom_emoji
    try:
        result = await client.invoke(raw.functions.messages.SearchCustomEmoji(emoticon=glyph, hash=0))
    except Exception as exc:
        log.debug('emoji_search: search failed for %r: %s', glyph, exc)
        return None
    ids = [int(i) for i in getattr(result, 'document_id', None) or []][:20]
    if not ids:
        return None
    try:
        from utils.emoji_patch import QUARANTINED_IDS
        dead = {str(i) for i in QUARANTINED_IDS}
    except Exception:
        dead = set()
    try:
        resolved = await resolve_custom_emoji(client, ids)
    except EmojiResolutionUnavailable:
        return next((str(i) for i in ids if str(i) not in dead), None)
    base = glyph.replace('️', '')
    fallback: Optional[str] = None
    for emoji_id in ids:
        if str(emoji_id) in dead or emoji_id not in resolved:
            continue
        alt = (resolved.get(emoji_id) or '').replace('️', '')
        if alt == base:
            return str(emoji_id)
        fallback = fallback or str(emoji_id)
    return fallback

async def _run() -> None:
    assert _queue is not None
    pending_save = False
    while True:
        try:
            glyph = await _queue.get()
        except asyncio.CancelledError:
            raise
        if glyph in LOOKUP or glyph in _MISSES:
            continue
        client = _client
        if client is None:
            _MISSES.add(glyph)
            continue
        emoji_id = await _search(client, glyph)
        if emoji_id:
            _adopt(glyph, emoji_id)
            pending_save = True
        else:
            _MISSES.add(glyph)
        if pending_save and _queue.empty():
            _save()
            pending_save = False
        await asyncio.sleep(0.4)

def start(client, warm: Iterable[str] | None=None) -> None:
    global _queue, _worker, _client
    _client = client
    if _queue is None:
        _queue = asyncio.Queue(maxsize=5000)
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_run())
        log.info('🌍 Universal premium-emoji resolver started (%d cached).', len(LOOKUP))
    for glyph in warm or ():
        request(glyph)
    for glyph, emoji_id in list(LOOKUP.items()):
        _adopt(glyph, emoji_id)
