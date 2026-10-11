from dataclasses import asdict
from time import time
import logging
from utils.database import db
state_col = db['playback_state']
LOGGER = logging.getLogger(__name__)
_WRITES_DISABLED = False

def _is_storage_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return 'over your space quota' in text or 'writes are blocked' in text or ('atlaserror' in text and 'quota' in text) or ('code 8000' in text)

def _disable_writes(exc: Exception) -> None:
    global _WRITES_DISABLED
    if not _WRITES_DISABLED:
        _WRITES_DISABLED = True
        LOGGER.warning('Mongo playback snapshots disabled for this process: Atlas storage quota is full; playback continues without restart recovery metadata. (%s)', str(exc).split(', full error:', 1)[0])

def track_to_dict(track):
    if track is None:
        return None
    payload = asdict(track)
    stream_url = payload.get('stream_url') or ''
    if stream_url.startswith(('http://127.0.0.1:', 'http://localhost:')):
        payload['stream_url'] = ''
    return payload

async def save_snapshot(chat_id: int, current, queue, loop: str, volume: int, *, active: bool | None=None, position: int | None=None, video: bool | None=None, speed: float | None=None) -> None:
    values = {'chat_id': chat_id, 'current': track_to_dict(current), 'queue': [track_to_dict(track) for track in queue], 'loop': loop, 'volume': volume}
    if active is not None:
        values['active'] = bool(active)
    if position is not None:
        values['position'] = max(0, int(position or 0))
        values['position_updated_at'] = int(time())
    if video is not None:
        values['video'] = bool(video)
    if speed is not None:
        values['speed'] = max(0.5, min(2.0, float(speed)))
    defaults = {'active': False, 'position': 0, 'video': False, 'speed': 1.0, 'position_updated_at': int(time())}
    insert_defaults = {field: default for field, default in defaults.items() if field not in values}
    update = {'$set': values}
    if insert_defaults:
        update['$setOnInsert'] = insert_defaults
    if _WRITES_DISABLED:
        return
    try:
        await state_col.update_one({'chat_id': chat_id}, update, upsert=True)
    except Exception as exc:
        if _is_storage_quota_error(exc):
            _disable_writes(exc)
            return
        raise

async def load_snapshots(active_only: bool=False) -> list[dict]:
    query = {'active': True} if active_only else {}
    return await state_col.find(query, {'_id': 0}).to_list(None)

async def mark_inactive(chat_id: int, *, clear: bool=False) -> None:
    if _WRITES_DISABLED:
        return
    try:
        if clear:
            await state_col.delete_one({'chat_id': chat_id})
        else:
            await state_col.update_one({'chat_id': chat_id}, {'$set': {'active': False, 'position': 0, 'position_updated_at': int(time())}})
    except Exception as exc:
        if _is_storage_quota_error(exc):
            _disable_writes(exc)
            return
        raise

async def ensure_indexes() -> None:
    if _WRITES_DISABLED:
        return
    try:
        await state_col.create_index('chat_id', unique=True)
    except Exception as exc:
        if _is_storage_quota_error(exc):
            _disable_writes(exc)
            return
        raise
