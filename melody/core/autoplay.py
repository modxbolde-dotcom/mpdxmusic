import html
import asyncio
from pyrogram import enums
from utils.branding import BOT_NAME
from melody.logging import LOGGER, send_error_log, log_activity
from melody.core.ytdl import get_related_videos
from melody.core.queue import Track, set_current, set_predownloaded, pop_predownloaded, peek_predownloaded
from utils.database import get_history, add_history
_prefetch_locks: dict = {}
_failed_ids: dict = {}
_FAILED_MEMORY = 12
_recent_ids: dict = {}
_RECENT_MEMORY = 30

def remember_played(chat_id: int, video_id: str) -> None:
    if not video_id:
        return
    ids = _recent_ids.setdefault(chat_id, [])
    if video_id in ids:
        ids.remove(video_id)
    ids.append(video_id)
    del ids[:-_RECENT_MEMORY]

def recent_ids(chat_id: int) -> list:
    return list(_recent_ids.get(chat_id, []))

def remember_failed(chat_id: int, video_id: str) -> None:
    if not video_id:
        return
    ids = _failed_ids.setdefault(chat_id, [])
    if video_id in ids:
        return
    ids.append(video_id)
    del ids[:-_FAILED_MEMORY]
import os as _os
from utils.tasks import spawn

def _autoplay_duration_limit() -> int:
    raw = (_os.environ.get('AUTOPLAY_MAX_DURATION') or '').strip()
    try:
        value = int(raw) if raw else 600
    except (TypeError, ValueError):
        value = 600
    return max(1, value)
AUTOPLAY_MAX_DURATION = _autoplay_duration_limit()

def _cloud_prefetch_enabled() -> bool:
    raw = (_os.environ.get('AUTOPLAY_PREDOWNLOAD') or '').strip().lower()
    if raw:
        return raw in {'1', 'true', 'yes', 'on'}
    return True

def _candidate_duration(candidate) -> int:
    try:
        value = int(candidate.get('duration') or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, value)

def _get_prefetch_lock(chat_id: int) -> asyncio.Lock:
    lock = _prefetch_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        _prefetch_locks[chat_id] = lock
    return lock

async def _pick_related_track(chat_id: int) -> 'Track | None':
    from melody.core.queue import get_current, get_last_user_track
    history = await get_history(chat_id)
    exclude_ids = [h['id'] for h in history] if history else []
    blocked = [*exclude_ids, *_failed_ids.get(chat_id, []), *_recent_ids.get(chat_id, [])]
    _cur = get_current(chat_id)
    if _cur and getattr(_cur, 'video_id', None):
        blocked.append(_cur.video_id)
    _pre = peek_predownloaded(chat_id)
    if _pre and getattr(_pre, 'video_id', None):
        blocked.append(_pre.video_id)
    exclude_ids = [v for v in dict.fromkeys(blocked) if v]
    current = get_current(chat_id)
    last_id = current.video_id if current and current.video_id else None
    if not last_id:
        last_id = get_last_user_track(chat_id)
    if not last_id and history:
        last_id = history[-1]['id']
    if not last_id:
        return None
    from melody.core.ytdl import is_tg_media_id
    if is_tg_media_id(last_id):
        last_id = next((h['id'] for h in reversed(history or []) if not is_tg_media_id(h.get('id', ''))), None)
        if not last_id:
            return None
    related = await get_related_videos(last_id, exclude_ids=exclude_ids)
    if not related:
        return None
    _blocked_set = set(exclude_ids)
    related = [c for c in related if (c.get('id') or '') and (c.get('id') or '') not in _blocked_set]
    if not related:
        return None
    playable = [c for c in related if not _candidate_duration(c) or _candidate_duration(c) <= AUTOPLAY_MAX_DURATION]
    if not playable:
        LOGGER.info('AutoPlay: every suggestion for %s was longer than %ds — skipping', last_id, AUTOPLAY_MAX_DURATION)
        return None
    related = playable
    top = related[0]
    vid = top.get('id') or ''
    import re
    if not re.fullmatch('[A-Za-z0-9_-]{11}', vid):
        LOGGER.warning('AutoPlay: skipping invalid related video_id %r', vid)
        for cand in related[1:]:
            cand_vid = cand.get('id') or ''
            if re.fullmatch('[A-Za-z0-9_-]{11}', cand_vid):
                top = cand
                vid = cand_vid
                break
        else:
            return None
    title = top.get('title') or 'Unknown'
    duration = int(top.get('duration') or 0)
    uploader = top.get('uploader') or 'Unknown'
    thumbnail = top.get('thumbnail') or f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'
    if title == 'Unknown' or not duration or uploader == 'Unknown':
        try:
            from melody.core.ytdl import get_video_details
            details = await get_video_details(vid)
        except Exception:
            details = None
        if details:
            if title == 'Unknown':
                title = details.get('title') or title
            if not duration:
                duration = int(details.get('duration') or 0)
            if uploader == 'Unknown':
                uploader = details.get('uploader') or uploader
            thumbnail = details.get('thumbnail') or thumbnail
    return Track(video_id=vid, title=title, duration=duration, stream_url='', thumbnail=thumbnail, uploader=uploader, requester_id=0, requester_name='AutoPlay', requested_in=chat_id)

async def prefetch_next(chat_id: int) -> 'Track | None':
    if _os.environ.get('AUTOPLAY_PREDOWNLOAD', 'true').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return None
    '\n    Predict + pre-download the next AutoPlay track for `chat_id`.\n\n    Called right after a track starts playing (from call.py) whenever the\n    manual queue is empty and AutoPlay is ON for that chat, AND right when\n    a chat turns AutoPlay on (see autoplay_cmd.py) — "jese hi on hoga next\n    song queue me add + download". Safe / cheap to call repeatedly — it\n    no-ops if a pre-download is already queued. Returns the track (already\n    cached to /tmp) so callers can also surface it in the visible queue.\n    '
    try:
        cached = peek_predownloaded(chat_id)
        if cached:
            return cached
        async with _get_prefetch_lock(chat_id):
            cached = peek_predownloaded(chat_id)
            if cached:
                return cached
            return await _prefetch_next_locked(chat_id)
    except Exception as exc:
        LOGGER.warning('AutoPlay prefetch failed in %s: %s', chat_id, exc)
        try:
            async with _get_prefetch_lock(chat_id):
                if peek_predownloaded(chat_id):
                    return peek_predownloaded(chat_id)
                return await _prefetch_next_locked(chat_id)
        except Exception as retry_exc:
            LOGGER.warning('AutoPlay prefetch retry also failed in %s: %s', chat_id, retry_exc)
        return None

async def _prefetch_next_locked(chat_id: int) -> 'Track | None':
    track = await _pick_related_track(chat_id)
    if not track:
        return None
    track_duration = _candidate_duration({'duration': track.duration})
    if track_duration > AUTOPLAY_MAX_DURATION:
        LOGGER.info('AutoPlay: skipping %r (%ds > %ds cap)', track.title, track_duration, AUTOPLAY_MAX_DURATION)
        return None
    from melody.core.call import is_active, is_video_active
    from melody.core.ytdl import resolve_stream_urls, download_audio, on_cloud_host
    from melody.core.queue import get_last_user_mode
    if is_active(chat_id):
        want_video = is_video_active(chat_id)
    else:
        want_video = get_last_user_mode(chat_id) or bool(getattr(track, 'video', False))
    track.video = want_video
    if on_cloud_host() and (not _cloud_prefetch_enabled()):
        remember_played(chat_id, track.video_id)
        set_predownloaded(chat_id, track)
        LOGGER.info('AutoPlay: cloud pre-download skipped for %s to protect interactive playback', track.video_id)
        return track
    from melody.core.ytdl import on_cloud_host, should_try_direct_stream
    if should_try_direct_stream() and (not on_cloud_host()):
        try:
            await resolve_stream_urls(track.video_id, want_video=want_video)
        except Exception as exc:
            LOGGER.info('AutoPlay: stream-url warm failed for %s (%s) — will resolve at play time.', track.video_id, exc)
    try:
        path = await download_audio(track.video_id, audio_only=not want_video, priority=50, owner=chat_id)
        if path:
            from melody.core.call import _persist_completed_song
            upcoming = track
            await _persist_completed_song(path, upcoming)
            LOGGER.info('AutoPlay: pre-downloaded %s → instant next play', track.video_id)
    except Exception as exc:
        LOGGER.info('AutoPlay: pre-download failed for %s (%s)', track.video_id, exc)
    remember_played(chat_id, track.video_id)
    set_predownloaded(chat_id, track)
    return track
_last_autoplay: dict = {}
_autoplay_cooldown: dict = {}
_AUTOPLAY_MIN_ALIVE = 20.0
_AUTOPLAY_MAX_FAILS = 4
_AUTOPLAY_COOLDOWN = 60.0

def reset_autoplay_guard(chat_id: int) -> None:
    _last_autoplay.pop(chat_id, None)
    _autoplay_cooldown.pop(chat_id, None)
    _failed_ids.pop(chat_id, None)

async def try_autoplay(chat_id: int) -> bool:
    try:
        import time as _time
        from melody.core.call import _stream_track, is_active, is_video_active, _begin_generation, _resolving, _resolving_track, _resolving_origin, _is_stale_generation
        from melody.core.queue import get_last_user_mode, get_current
        cooling = _autoplay_cooldown.get(chat_id, 0.0)
        if cooling and _time.time() < cooling:
            LOGGER.info('AutoPlay in %s is cooling down for %.0fs more', chat_id, cooling - _time.time())
            return False
        last_ts, last_vid, fails = _last_autoplay.get(chat_id, (0.0, None, 0))
        if last_ts and _time.time() - last_ts < _AUTOPLAY_MIN_ALIVE:
            fails += 1
            if last_vid:
                remember_failed(chat_id, last_vid)
                set_predownloaded(chat_id, None)
            if fails >= _AUTOPLAY_MAX_FAILS:
                _last_autoplay.pop(chat_id, None)
                _autoplay_cooldown[chat_id] = _time.time() + _AUTOPLAY_COOLDOWN
                LOGGER.warning('AutoPlay chain in %s died %d times — cooling down %.0fs, then resuming automatically', chat_id, fails, _AUTOPLAY_COOLDOWN)
                return False
        else:
            fails = 0
        track = pop_predownloaded(chat_id)
        if track and track.video_id in _failed_ids.get(chat_id, []):
            track = None
        current = get_current(chat_id)
        if track and current and (track.video_id == getattr(current, 'video_id', None)):
            track = None
        if not track:
            track = await _pick_related_track(chat_id)
        if not track:
            return False
        autoplay_gen = _begin_generation(chat_id)
        _resolving[chat_id] = autoplay_gen
        _resolving_track[chat_id] = (track.video_id, bool(track.video))
        _resolving_origin[chat_id] = 'autoplay'
        if is_active(chat_id):
            video = is_video_active(chat_id)
        else:
            video = get_last_user_mode(chat_id) or bool(track.video)
        track.video = video
        _last_autoplay[chat_id] = (_time.time(), track.video_id, fails)
        set_current(chat_id, track)
        try:
            started = await _stream_track(chat_id, track, video=video, gen=autoplay_gen, priority=25)
        finally:
            if _resolving.get(chat_id) == autoplay_gen:
                _resolving.pop(chat_id, None)
                _resolving_track.pop(chat_id, None)
                _resolving_origin.pop(chat_id, None)
        if started is not True or _is_stale_generation(chat_id, autoplay_gen):
            if _is_stale_generation(chat_id, autoplay_gen):
                LOGGER.info('AutoPlay candidate %s superseded in %s', track.video_id, chat_id)
            if not _is_stale_generation(chat_id, autoplay_gen):
                current = get_current(chat_id)
                if current is track or getattr(current, 'video_id', None) == track.video_id:
                    set_current(chat_id, None)
            return False
        remember_played(chat_id, track.video_id)
        await add_history(chat_id, track.video_id, track.title)
        safe_title = html.escape((track.title or 'Unknown')[:60])
        try:
            from melody.core.playcard import send_playing_card
            from melody import bot
            chat_obj = await bot.get_chat(chat_id)
            await send_playing_card(bot, chat_obj, track, status_label='AutoPlay ▶️ (ᴀᴜᴛᴏ-ᴘɪᴄᴋᴇᴅ)', requester_mention='🤖 <i>AutoPlay</i>')
        except Exception:
            from melody import bot
            try:
                await bot.send_message(chat_id, f'<blockquote>🎶 <b>AutoPlay ▶️</b> <code>{safe_title}</code>\n<i>{BOT_NAME} ne sunwaya!</i>\n🙋 Requested by: <i>AutoPlay</i></blockquote>', parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
        spawn(log_activity(f'🤖 <b>AutoPlay Triggered</b>\n• Song: <code>{safe_title}</code>\n• Chat: <code>{chat_id}</code>'))
        spawn(prefetch_next(chat_id), name=f'autoplay-prefetch:{chat_id}')
        return True
    except Exception as exc:
        current = get_current(chat_id)
        await send_error_log(f'try_autoplay failed in {chat_id}', exc, context={'chat_id': chat_id, 'song_title': current.title if current else None, 'video_id': current.video_id if current else None})
        return False
