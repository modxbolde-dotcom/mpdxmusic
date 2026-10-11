from __future__ import annotations
import functools
import logging
from pyrogram.types import CallbackQuery
log = logging.getLogger(__name__)
_STALE_ERRORS = ('QUERY_ID_INVALID', 'QUERY_ID_EMPTY', 'MESSAGE_ID_INVALID', 'BOT_RESPONSE_TIMEOUT')
_applied = False

def _is_stale(exc: BaseException) -> bool:
    text = f'{type(exc).__name__} {exc}'.upper()
    return any((marker in text for marker in _STALE_ERRORS))

def apply_callback_patch() -> None:
    global _applied
    if _applied or getattr(CallbackQuery.answer, '_melody_patched', False):
        return
    original = CallbackQuery.answer

    @functools.wraps(original)
    async def answer(self, *args, **kwargs):
        try:
            return await original(self, *args, **kwargs)
        except Exception as exc:
            if _is_stale(exc):
                log.debug('Ignoring stale callback answer: %s', exc)
                return None
            raise
    answer._melody_patched = True
    CallbackQuery.answer = answer
    _applied = True
    log.info('🛡 CallbackQuery.answer patched (stale QUERY_ID_INVALID ignored).')
__all__ = ['apply_callback_patch']
