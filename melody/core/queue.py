import html
import random
from dataclasses import dataclass, fields
from typing import Optional
from utils.database import get_setting, set_setting
from utils.formatters import premium_emoji, PREMIUM_EMOJI_IDS
from melody.core.settings_defaults import AUTOPLAY_DEFAULT
from utils.tasks import spawn

@dataclass
class Track:
    video_id: str
    title: str
    duration: int
    stream_url: str
    thumbnail: str
    uploader: str
    requester_id: int
    requester_name: str
    requested_in: int
    video: bool = False
    source_query: str = ''
_queues: dict[int, list[Track]] = {}
_current: dict[int, Track] = {}
_loop: dict[int, str] = {}
_volume: dict[int, int] = {}
_predownloaded: dict[int, Track] = {}
_autoplay_generation: dict[int, int] = {}

def autoplay_generation(chat_id: int) -> int:
    return _autoplay_generation.get(chat_id, 0)

def _persist(chat_id: int) -> None:
    try:
        from utils.playback_state import save_snapshot
        spawn(save_snapshot(chat_id, _current.get(chat_id), _queues.get(chat_id, []), _loop.get(chat_id, 'none'), _volume.get(chat_id, 100)))
    except RuntimeError:
        pass

def _track_from_dict(data) -> Optional[Track]:
    if not isinstance(data, dict):
        return None
    allowed = {f.name for f in fields(Track)}
    payload = {k: v for k, v in data.items() if k in allowed}
    if not payload.get('video_id'):
        return None
    try:
        return Track(**payload)
    except TypeError:
        return None

def restore_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict):
        return
    try:
        chat_id = int(snapshot['chat_id'])
    except (KeyError, TypeError, ValueError):
        return
    restored = _track_from_dict(snapshot.get('current'))
    if restored is not None:
        _current[chat_id] = restored
    else:
        _current.pop(chat_id, None)
    queue = snapshot.get('queue') or []
    _queues[chat_id] = [t for t in (_track_from_dict(i) for i in queue) if t]
    mode = str(snapshot.get('loop', 'none')).lower()
    _loop[chat_id] = mode if mode in {'none', 'single', 'all'} else 'none'
    try:
        volume = int(snapshot.get('volume', 100))
    except (TypeError, ValueError):
        volume = 100
    _volume[chat_id] = max(0, min(200, volume))

def get_queue(chat_id: int) -> list[Track]:
    return _queues.get(chat_id, [])

def add_to_queue(chat_id: int, track: Track):
    if chat_id not in _queues:
        _queues[chat_id] = []
    _queues[chat_id].append(track)
    _persist(chat_id)

def pop_next(chat_id: int) -> Optional[Track]:
    mode = _loop.get(chat_id, 'none')
    current = _current.get(chat_id)
    if mode == 'single' and current:
        return current
    if _queues.get(chat_id):
        track = _queues[chat_id].pop(0)
        if mode == 'all' and current:
            _queues[chat_id].append(current)
        _current[chat_id] = track
        _persist(chat_id)
        return track
    if mode == 'all' and current:
        _current[chat_id] = current
        return current
    return None

def set_current(chat_id: int, track: Track):
    _current[chat_id] = track
    _persist(chat_id)

def get_current(chat_id: int) -> Optional[Track]:
    return _current.get(chat_id)

def active_video_ids() -> set[str]:
    ids: set[str] = set()
    for track in _current.values():
        if track and track.video_id:
            ids.add(track.video_id)
    for tracks in _queues.values():
        for track in tracks:
            if track and track.video_id:
                ids.add(track.video_id)
    for track in _predownloaded.values():
        if track and track.video_id:
            ids.add(track.video_id)
    return ids

def clear_queue(chat_id: int, persist: bool=True):
    _queues[chat_id] = []
    _current.pop(chat_id, None)
    if persist:
        _persist(chat_id)

def remove_from_queue(chat_id: int, position: int) -> Optional[Track]:
    q = _queues.get(chat_id, [])
    idx = position - 1
    if 0 <= idx < len(q):
        track = q.pop(idx)
        _persist(chat_id)
        return track
    return None

def remove_matching_track(chat_id: int, video_id: str) -> bool:
    q = _queues.get(chat_id, [])
    for idx, track in enumerate(q):
        if str(getattr(track, 'video_id', '')) == str(video_id):
            q.pop(idx)
            _persist(chat_id)
            return True
    return False

def shuffle_queue(chat_id: int):
    q = _queues.get(chat_id, [])
    random.shuffle(q)
    _queues[chat_id] = q
    _persist(chat_id)

def set_loop(chat_id: int, mode: str):
    normalized = str(mode or 'none').lower()
    _loop[chat_id] = normalized if normalized in {'none', 'single', 'all'} else 'none'
    _persist(chat_id)

def get_loop(chat_id: int) -> str:
    return _loop.get(chat_id, 'none')

def get_volume(chat_id: int) -> int:
    return _volume.get(chat_id, 100)

def set_volume_local(chat_id: int, vol: int):
    try:
        value = int(vol)
    except (TypeError, ValueError):
        value = 100
    _volume[chat_id] = max(0, min(200, value))
    _persist(chat_id)

def set_predownloaded(chat_id: int, track: Optional[Track]):
    if track is None:
        _predownloaded.pop(chat_id, None)
    else:
        _predownloaded[chat_id] = track

def pop_predownloaded(chat_id: int) -> Optional[Track]:
    return _predownloaded.pop(chat_id, None)

def peek_predownloaded(chat_id: int) -> Optional[Track]:
    return _predownloaded.get(chat_id)
_last_user_track: dict = {}
_last_user_mode: dict = {}

def set_last_user_track(chat_id: int, video_id: Optional[str], video: bool=False):
    if not video_id:
        _last_user_track.pop(chat_id, None)
        _last_user_mode.pop(chat_id, None)
        return
    previous_id = _last_user_track.get(chat_id)
    previous_mode = _last_user_mode.get(chat_id, False)
    changed = previous_id != video_id or bool(previous_mode) != bool(video)
    _last_user_track[chat_id] = video_id
    _last_user_mode[chat_id] = bool(video)
    if changed:
        _predownloaded.pop(chat_id, None)

def get_last_user_track(chat_id: int) -> Optional[str]:
    return _last_user_track.get(chat_id)

def get_last_user_mode(chat_id: int) -> bool:
    return bool(_last_user_mode.get(chat_id, False))
AUTOPLAY_OFF_TTL = 30 * 60

async def is_autoplay_on(chat_id: int) -> bool:
    val = await get_setting(chat_id, 'autoplay', AUTOPLAY_DEFAULT)
    if bool(val):
        return True
    import time as _time
    off_at = await get_setting(chat_id, 'autoplay_off_at', 0) or 0
    try:
        off_at = float(off_at)
    except (TypeError, ValueError):
        off_at = 0.0
    if off_at and _time.time() - off_at >= AUTOPLAY_OFF_TTL:
        await set_autoplay(chat_id, True)
        return True
    return False

async def set_autoplay(chat_id: int, enabled: bool):
    import time as _time
    _autoplay_generation[chat_id] = autoplay_generation(chat_id) + 1
    await set_setting(chat_id, 'autoplay', enabled)
    await set_setting(chat_id, 'autoplay_off_at', 0 if enabled else _time.time())

def format_queue(chat_id: int, autoplay_on: bool=False) -> str:
    current = _current.get(chat_id)
    q = _queues.get(chat_id, [])
    predownloaded = _predownloaded.get(chat_id)
    lines = [f"<b>{premium_emoji(PREMIUM_EMOJI_IDS['queue'], '📋')} Music Queue</b>\n"]
    if current:
        safe_title = html.escape(current.title[:45])
        safe_name = html.escape(current.requester_name)
        lines.append(f'<b>▶️ Now Playing:</b>\n<code>{safe_title}</code> — {safe_name}\n')
    if q:
        lines.append('<b>⏳ Up Next:</b>')
        for i, t in enumerate(q[:10], 1):
            safe_t = html.escape(t.title[:40])
            safe_n = html.escape(t.requester_name)
            lines.append(f'<code>{i}.</code> {safe_t} — <i>{safe_n}</i>')
        if len(q) > 10:
            lines.append(f'\n<i>...and {len(q) - 10} more</i>')
        if predownloaded:
            safe_p = html.escape(predownloaded.title[:40])
            lines.append(f'\n<b>🤖 AutoPlay ready:</b> <code>{safe_p}</code> (pre-downloaded)')
    elif predownloaded:
        safe_p = html.escape(predownloaded.title[:40])
        lines.append('<b>⏳ Up Next (AutoPlay):</b>')
        lines.append(f'<code>{safe_p}</code>')
        lines.append('🙋 Requested by: <i>AutoPlay</i>')
    elif autoplay_on:
        lines.append('🤖 <b>AutoPlay is ON</b> — next song will be picked automatically')
    else:
        lines.append('<i>Queue is empty</i>')
    return f'<blockquote>{chr(10).join(lines)}</blockquote>'
