from typing import Dict, Iterable, Optional
import logging
import time
from pyrogram import raw
log = logging.getLogger(__name__)
MAX_IDS_PER_REQUEST = 200

class EmojiResolutionUnavailable(RuntimeError):
    pass
RESOLUTION_SUPPORTED = True
_RESOLUTION_RETRY_AT = 0.0
_RESOLUTION_RETRY_COOLDOWN = 60.0
_UNSUPPORTED_MARKERS = ('BOT_METHOD_INVALID', 'METHOD_INVALID', 'USER_BOT_INVALID')

async def resolve_custom_emoji(client, ids: Iterable[int]) -> Dict[int, Optional[str]]:
    global RESOLUTION_SUPPORTED, _RESOLUTION_RETRY_AT
    wanted = [int(i) for i in dict.fromkeys(ids)]
    resolved: Dict[int, Optional[str]] = {}
    if not wanted:
        return resolved
    if not RESOLUTION_SUPPORTED:
        raise EmojiResolutionUnavailable('messages.GetCustomEmojiDocuments is not available for this account')
    if time.monotonic() < _RESOLUTION_RETRY_AT:
        raise EmojiResolutionUnavailable('custom-emoji verification is temporarily backing off after a timeout')
    failures = 0
    for start in range(0, len(wanted), MAX_IDS_PER_REQUEST):
        batch = wanted[start:start + MAX_IDS_PER_REQUEST]
        try:
            import asyncio
            documents = await asyncio.wait_for(client.invoke(raw.functions.messages.GetCustomEmojiDocuments(document_id=batch)), timeout=5.0)
        except Exception as exc:
            failures += 1
            _RESOLUTION_RETRY_AT = time.monotonic() + _RESOLUTION_RETRY_COOLDOWN
            if any((m in str(exc).upper() for m in _UNSUPPORTED_MARKERS)):
                RESOLUTION_SUPPORTED = False
                log.info('Custom-emoji id verification unavailable for this account (%s) — premium emoji ids will be used unverified.', exc)
            else:
                log.warning('Custom-emoji verification temporarily unavailable (%s); backing off for %.0fs.', exc, _RESOLUTION_RETRY_COOLDOWN)
            continue
        for document in documents or []:
            document_id = getattr(document, 'id', None)
            if document_id is None:
                continue
            alt = None
            for attribute in getattr(document, 'attributes', None) or []:
                if isinstance(attribute, raw.types.DocumentAttributeCustomEmoji):
                    alt = attribute.alt
                    break
            resolved[int(document_id)] = alt
    if failures and (not resolved):
        raise EmojiResolutionUnavailable(f'could not verify {len(wanted)} custom-emoji id(s)')
    return resolved
