from utils.branding import BOT_NAME
import asyncio
from html import escape as html_escape
import glob
import os
import tempfile
import time
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, StreamEnded
from pytgcalls.exceptions import NoActiveGroupCall
_IS_CLOUD_RUNTIME = bool(os.getenv('DYNO') or os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RENDER_SERVICE_ID') or os.getenv('FLY_APP_NAME'))
try:
    configured_download_delay = float(os.getenv('DOWNLOAD_START_DELAY', '0.0'))
except Exception:
    configured_download_delay = 0.0
_DOWNLOAD_START_DELAY = max(0.0, configured_download_delay)
try:
    _PLAY_PROBE_TIMEOUT = max(0.8, float(os.getenv('PLAY_PROBE_TIMEOUT', '2.5')))
except Exception:
    _PLAY_PROBE_TIMEOUT = 2.5
try:
    _LOCAL_PROXY_PLAY_TIMEOUT = max(_PLAY_PROBE_TIMEOUT, float(os.getenv('LOCAL_PROXY_PLAY_TIMEOUT', '15')))
except Exception:
    _LOCAL_PROXY_PLAY_TIMEOUT = max(_PLAY_PROBE_TIMEOUT, 15.0)
try:
    _LOCAL_PLAY_TIMEOUT = max(5.0, float(os.getenv('LOCAL_PLAY_TIMEOUT', '9')))
except Exception:
    _LOCAL_PLAY_TIMEOUT = 9.0
try:
    _VIDEO_DIRECT_ONLY_SECONDS = max(300, int(os.getenv('VIDEO_DIRECT_ONLY_SECONDS', '900')))
except (TypeError, ValueError):
    _VIDEO_DIRECT_ONLY_SECONDS = 900
try:
    _CONTROL_RPC_TIMEOUT = max(2.0, float(os.getenv('CONTROL_RPC_TIMEOUT', '5')))
except Exception:
    _CONTROL_RPC_TIMEOUT = 5.0
try:
    _PLAY_START_BUDGET = max(2.0, min(12.0, float(os.getenv('PLAY_START_BUDGET', '5.0'))))
except Exception:
    _PLAY_START_BUDGET = 8.0
try:
    _PLAY_FALLBACK_TIMEOUT = max(30.0, min(180.0, float(os.getenv('PLAY_FALLBACK_TIMEOUT', '45'))))
except Exception:
    _PLAY_FALLBACK_TIMEOUT = 45.0
try:
    from pytgcalls.exceptions import NotInCallError
except ImportError:

    class NotInCallError(Exception):
        pass
from pyrogram import enums
from pyrogram.types import ChatPermissions
from pyrogram.errors import ChannelInvalid, FloodWait, ChannelPrivate, ChatAdminRequired, PeerIdInvalid, UserBannedInChannel
from melody.logging import LOGGER, redact_sensitive_text, send_error_log

async def _persist_completed_song(filepath, track) -> None:
    if not filepath or not track or filepath.endswith(('.early', '.part', '.temp')):
        return
    try:
        from melody import bot as _bot
        from utils.telegram_archive import archive_completed_file
        ok = await archive_completed_file(_bot, track.video_id, getattr(track, 'title', f'{BOT_NAME}'), filepath, video=bool(getattr(track, 'video', False)))
        if ok:
            LOGGER.info('telegram archive saved %s', getattr(track, 'video_id', '?'))
    except Exception as exc:
        LOGGER.debug('telegram archive skipped for %s: %s', getattr(track, 'video_id', '?'), exc)
from melody.core.queue import get_current, set_current, pop_next, clear_queue, get_volume, set_volume_local, is_autoplay_on, get_queue, get_loop
NO_ACTIVE_VC_MESSAGE = 'ɴᴏ ᴀᴄᴛɪᴠᴇ ᴠɪᴅᴇᴏᴄʜᴀᴛ ꜰᴏᴜɴᴅ.\n\nᴘʟᴇᴀsᴇ sᴛᴀʀᴛ ᴠɪᴅᴇᴏᴄʜᴀᴛ ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ / ᴄʜᴀɴɴᴇʟ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.'
VC_ADMIN_REQUIRED_MESSAGE = '⚠️ <b>Voice chat start nahi ho paya — assistant account admin nahi hai.</b>\n\nNaya voice chat sirf group ka admin start kar sakta hai. Assistant account ko admin bana do, ya group me manually voice chat start karke phir <code>/play</code> try karo.'
_vc_admin_block: dict[int, float] = {}
_vc_admin_notified: dict[int, float] = {}
_VC_ADMIN_BLOCK_FOR = 45.0

def _vc_admin_blocked(chat_id: int) -> bool:
    deadline = _vc_admin_block.get(chat_id, 0.0)
    if deadline and time.monotonic() < deadline:
        return True
    _vc_admin_block.pop(chat_id, None)
    _vc_admin_notified.pop(chat_id, None)
    return False

def _block_vc_admin(chat_id: int) -> None:
    _vc_admin_block[chat_id] = time.monotonic() + _VC_ADMIN_BLOCK_FOR

def _vc_admin_notice_needed(chat_id: int) -> bool:
    return time.monotonic() >= _vc_admin_notified.get(chat_id, 0.0)

def _mark_vc_admin_notified(chat_id: int) -> None:
    _vc_admin_notified[chat_id] = time.monotonic() + _VC_ADMIN_BLOCK_FOR

def _clear_vc_admin_block(chat_id: int) -> None:
    _vc_admin_block.pop(chat_id, None)
    _vc_admin_notified.pop(chat_id, None)

def is_vc_admin_blocked(chat_id: int) -> bool:
    return _vc_admin_blocked(chat_id)
_pytgcalls: 'PyTgCalls | None' = None
_active: dict = {}
_leaving: dict = {}
_LEAVE_GRACE = 8.0
_silence_playing: dict = {}
_is_video: dict = {}
_play_start_time: dict = {}
_seek_offset: dict = {}
_speed: dict = {}
_muted: dict = {}
_listener_mode: dict = {}
_play_locks: dict[int, asyncio.Lock] = {}
_stream_commit_locks: dict[int, asyncio.Lock] = {}
_prefetch_inflight: dict[int, set[str]] = {}
_FORCE_DOWNLOAD_PRIORITY = -10
_gen_counter: dict[int, int] = {}
_stream_generation: dict[int, int] = {}
_resolving: dict[int, int] = {}
_resolving_track: dict[int, tuple[str, bool]] = {}
_resolving_origin: dict[int, str] = {}

def _cancel_stale_download(chat_id: int, new_video_id: str, new_video: bool) -> None:
    previous = _resolving_track.get(chat_id)
    if not previous or (previous[0] == new_video_id and previous[1] == new_video):
        return
    try:
        from melody.core.ytdl import cancel_download
        if cancel_download(previous[0], audio_only=not previous[1], owner=chat_id):
            LOGGER.info('#download cancel stale chat=%s video=%s for new_video=%s', chat_id, previous[0], new_video_id)
    except Exception as exc:
        LOGGER.debug('stale download cancellation skipped for %s: %s', chat_id, exc)

def _begin_generation(chat_id: int) -> int:
    gen = _gen_counter.get(chat_id, 0) + 1
    _gen_counter[chat_id] = gen
    _stream_generation[chat_id] = gen
    return gen

def _is_stale_generation(chat_id: int, gen: 'int | None') -> bool:
    return gen is not None and _stream_generation.get(chat_id) != gen

def _get_play_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _play_locks:
        _play_locks[chat_id] = asyncio.Lock()
    return _play_locks[chat_id]

def _get_stream_commit_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _stream_commit_locks:
        _stream_commit_locks[chat_id] = asyncio.Lock()
    return _stream_commit_locks[chat_id]

def _consume_task_exception(task: asyncio.Task) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass

def _get_audio_quality():
    from pytgcalls.types import AudioQuality
    return getattr(AudioQuality, 'HIGH_QUALITY', None) or getattr(AudioQuality, 'STUDIO', None)

def _get_video_quality():
    from pytgcalls.types import VideoQuality
    wanted = (os.getenv('VIDEO_QUALITY') or '480p').strip().lower()
    table = {'1080p': 'FHD_1080p', '720p': 'HD_720p', '480p': 'SD_480p', '360p': 'SD_360p'}
    name = table.get(wanted, 'SD_480p')
    return getattr(VideoQuality, name, None) or VideoQuality.SD_480p
try:
    _PREJOIN_TIMEOUT = float(os.getenv('PREJOIN_TIMEOUT', '4'))
except ValueError:
    _PREJOIN_TIMEOUT = 4.0
_SILENCE_PATH: str | None = None
_SILENCE_LOCK = asyncio.Lock()

async def _get_silence_file() -> str | None:
    global _SILENCE_PATH
    if _SILENCE_PATH and os.path.exists(_SILENCE_PATH):
        return _SILENCE_PATH
    async with _SILENCE_LOCK:
        if _SILENCE_PATH and os.path.exists(_SILENCE_PATH):
            return _SILENCE_PATH
        path = os.path.join(tempfile.gettempdir(), 'melody_vc_silence.mp3')
        try:
            proc = await asyncio.create_subprocess_exec('ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000', '-t', '60', '-c:a', 'libmp3lame', '-b:a', '64k', path, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=15)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                _SILENCE_PATH = path
                LOGGER.info('✅ Silence file created: %s', path)
        except Exception as e:
            LOGGER.debug('Silence file creation failed: %s', e)
    return _SILENCE_PATH

async def start_call_py():
    global _pytgcalls
    from melody import assistant
    _pytgcalls = PyTgCalls(assistant)

    @_pytgcalls.on_update()
    async def _on_stream_end(_, update):
        finished_title = None
        try:
            if not isinstance(update, StreamEnded):
                return
            chat_id = getattr(update, 'chat_id', None)
            if chat_id is None:
                return
            if _is_leaving(chat_id):
                LOGGER.debug('stream_end during deliberate leave for %s — ignoring', chat_id)
                return
            if _listener_mode.get(chat_id) and (not get_current(chat_id)):
                await _replay_listener_silence(chat_id)
                return
            if _silence_playing.get(chat_id):
                LOGGER.debug('stream_end during silence for %d — ignoring (real song loading)', chat_id)
                return
            if await _resume_if_premature_end(chat_id):
                return
            if _is_probably_stale_stream_end(chat_id):
                return
            current = get_current(chat_id)
            finished_title = current.title if current else None
            if current:
                _cleanup_track_file(current.video_id)
            await _play_next(chat_id)
        except Exception as exc:
            chat_id = getattr(update, 'chat_id', '?')
            LOGGER.exception('‼️ _on_stream_end crashed for chat %s', chat_id)
            try:
                await send_error_log(f'_on_stream_end crashed in {chat_id} (song ended, autoplay/next-up never ran)', exc, context={'chat_id': chat_id, 'song_title': finished_title})
            except Exception:
                pass
    await _pytgcalls.start()
    LOGGER.info('PyTgCalls (py-tgcalls 2.x) started.')
    spawn(_get_silence_file())

def _cache_limit_bytes() -> int:
    try:
        megabytes = int(os.getenv('SONG_CACHE_MB', '32'))
    except ValueError:
        megabytes = 96
    return max(16, min(megabytes, 64)) * 1024 * 1024
_CACHE_MAX_BYTES = _cache_limit_bytes()
_IN_PROGRESS_SUFFIXES = ('.part', '.ytdl', '.temp', '.tmp', '.dl')

def _is_in_progress(path: str) -> bool:
    return path.endswith(_IN_PROGRESS_SUFFIXES) or '.part-' in path or '.dl.' in path

def _cleanup_partial_files(video_id: str) -> None:
    if not video_id:
        return
    for f in glob.glob(f'/tmp/melody_{video_id}_?.*'):
        if _is_in_progress(f):
            continue
        try:
            os.unlink(f)
        except OSError:
            pass

def _cleanup_track_file(video_id: str):
    cache_files = []
    for f in glob.glob('/tmp/melody_*_?.*'):
        if _is_in_progress(f):
            continue
        try:
            st = os.stat(f)
            cache_files.append((f, st.st_mtime, st.st_size))
        except Exception:
            pass
    try:
        from melody.core.ytdl import _sweep_staging_dir
        _sweep_staging_dir()
    except Exception:
        pass
    cache_files.sort(key=lambda x: x[1])
    total = sum((c[2] for c in cache_files))
    for f, _, size in cache_files:
        if total <= _CACHE_MAX_BYTES:
            break
        try:
            from utils.diskguard import _is_protected, _protected_video_ids
            if _is_protected(f, _protected_video_ids()):
                continue
        except Exception:
            pass
        try:
            os.unlink(f)
            total -= size
        except Exception:
            pass
    try:
        from utils.diskguard import free_bytes, min_free_bytes, sweep
        if free_bytes() < min_free_bytes():
            sweep(aggressive=True)
    except Exception:
        pass
_premature_resumes: dict = {}

async def _resume_if_premature_end(chat_id: int) -> bool:
    track = get_current(chat_id)
    if not track:
        return False
    duration = int(getattr(track, 'duration', 0) or 0)
    elapsed = get_playback_position(chat_id)
    if duration > 0:
        if elapsed >= duration - 8:
            return False
    elif elapsed >= 25:
        return False
    key = f'{chat_id}:{track.video_id}'
    count = _premature_resumes.get(key, 0)
    if count >= 15:
        LOGGER.warning('giving up resuming %s in %s after %d premature ends', track.video_id, chat_id, count)
        return False
    video = _is_video.get(chat_id, False)
    try:
        from melody.core.ytdl import is_download_in_progress
        for _ in range(30):
            if not is_download_in_progress(track.video_id, audio_only=not video):
                break
            await asyncio.sleep(0.5)
    except Exception:
        pass
    _premature_resumes[key] = count + 1
    try:
        await seek_stream(chat_id, max(elapsed - 1, 0), prefer_local=True)
        LOGGER.info('▶️ resumed %s in %s at %ss (stream ended early — file was still downloading)', track.video_id, chat_id, elapsed)
        return True
    except Exception as exc:
        LOGGER.debug('premature-end resume failed for %s: %s', chat_id, exc)
        return False

async def _play_next(chat_id: int):
    from melody.core.autoplay import try_autoplay
    lock = _get_play_lock(chat_id)
    async with lock:
        if chat_id in _resolving:
            LOGGER.debug('_play_next skipped for %s — a play request is already resolving', chat_id)
            return
        next_track = pop_next(chat_id)
        gen = None
        if next_track:
            inflight = _prefetch_inflight.get(chat_id)
            if inflight is not None:
                inflight.discard(next_track.video_id)
                if not inflight:
                    _prefetch_inflight.pop(chat_id, None)
            gen = _begin_generation(chat_id)
            _resolving[chat_id] = gen
            _resolving_track[chat_id] = (next_track.video_id, bool(next_track.video))
            _resolving_origin[chat_id] = 'queue'
    if next_track:
        try:
            await _stream_track(chat_id, next_track, video=next_track.video, gen=gen, priority=0)
        finally:
            if _resolving.get(chat_id) == gen:
                _resolving.pop(chat_id, None)
                _resolving_track.pop(chat_id, None)
                _resolving_origin.pop(chat_id, None)
        return
    _prefetch_inflight.pop(chat_id, None)
    if await is_autoplay_on(chat_id) and await try_autoplay(chat_id):
        return
    _mark_leaving(chat_id)
    from melody.core.queue import clear_queue
    clear_queue(chat_id)
    _forget_call_state(chat_id)
    from utils.playback_state import mark_inactive
    await mark_inactive(chat_id)
    await _hard_leave(chat_id)

async def _prefetch_upcoming(chat_id: int) -> None:
    prefetch_default = 'false' if os.getenv('MEMORY_LIMIT_MB', '512').strip().isdigit() and int(os.getenv('MEMORY_LIMIT_MB', '512')) <= 768 else 'true'
    if os.getenv('PREFETCH_ENABLED', prefetch_default).strip().lower() not in {'1', 'true', 'yes', 'on'}:
        return
    from melody.core.ytdl import cached_file_path, download_audio, is_download_cancelled, on_cloud_host, resolve_stream_urls, should_try_direct_stream
    from melody.core.autoplay import _cloud_prefetch_enabled
    try:
        memory_mb = int(os.getenv('MEMORY_LIMIT_MB', '512'))
    except ValueError:
        memory_mb = 512
    try:
        requested = max(1, int(os.getenv('PREFETCH_WORKERS', '2')))
    except ValueError:
        requested = 2
    prefetch_workers = min(2, requested) if memory_mb >= 1024 else 1
    queue = get_queue(chat_id)
    if queue:
        inflight = _prefetch_inflight.setdefault(chat_id, set())
        candidates = [upcoming for upcoming in list(queue[:3]) if upcoming.video_id not in inflight and (not cached_file_path(upcoming.video_id, audio_only=not upcoming.video))]
        candidates = candidates[:prefetch_workers]

        async def _warm(upcoming, rank: int) -> None:
            inflight.add(upcoming.video_id)
            try:
                if should_try_direct_stream() and (not on_cloud_host()):
                    try:
                        await resolve_stream_urls(upcoming.video_id, want_video=upcoming.video)
                    except Exception:
                        pass
                if on_cloud_host() and (not _cloud_prefetch_enabled()):
                    LOGGER.info('prefetch: cloud download skipped for %s to protect interactive playback', upcoming.video_id)
                    return
                path = await download_audio(upcoming.video_id, audio_only=not upcoming.video, priority=20 + rank * 15, owner=chat_id)
                if path:
                    LOGGER.info('prefetch: cached queue item %s (%s)', upcoming.video_id, upcoming.title[:40])
            except Exception as exc:
                if is_download_cancelled(exc):
                    LOGGER.info('prefetch stopped for %s after interactive priority request', chat_id)
                    return
                LOGGER.info('prefetch of queued track failed in %s (%s): %s', chat_id, upcoming.video_id, type(exc).__name__)
            finally:
                inflight.discard(upcoming.video_id)
        await asyncio.gather(*(_warm(track, rank) for rank, track in enumerate(candidates)), return_exceptions=True)
        if not inflight:
            _prefetch_inflight.pop(chat_id, None)
        return
    if await is_autoplay_on(chat_id):
        from melody.core.autoplay import prefetch_next
        await prefetch_next(chat_id)
_HTTP_RECONNECT_FFMPEG = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 2 -rw_timeout 5000000'

def _is_remote_source(path: str | None) -> bool:
    return bool(path) and str(path).lower().startswith(('http://', 'https://'))

def _is_local_media_proxy(path: str | None) -> bool:
    return bool(path) and str(path).lower().startswith(('http://127.0.0.1:', 'http://localhost:'))

async def _build_direct_stream(chat_id: int, track, video: bool, seconds: int=0, force: bool=False):
    try:
        from melody.core.ytdl import resolve_stream_urls
        proxy_url = getattr(track, 'stream_url', '') or ''
        if proxy_url.startswith(('http://127.0.0.1:', 'http://localhost:')):
            urls = {'audio': proxy_url, 'video': proxy_url if video else None, 'headers': {}}
        else:
            urls = await resolve_stream_urls(track.video_id, want_video=video, force=force)
    except Exception as exc:
        if not force and (not str(getattr(track, 'stream_url', '') or '').lower().startswith(('http://127.0.0.1:', 'http://localhost:'))):
            try:
                if os.getenv('DISABLE_DIRECT_STREAM', '0') == '1':
                    raise ValueError('FastPlay: skip direct stream')
                return await _build_direct_stream(chat_id, track, video, seconds, force=True)
            except Exception as retry_exc:
                LOGGER.info('#stream fresh resolver retry failed for %s (%s)', getattr(track, 'video_id', '?'), type(retry_exc).__name__)
        LOGGER.info('#stream direct-stream unavailable for %s (%s: %r) — falling back to download', getattr(track, 'video_id', '?'), type(exc).__name__, exc)
        return None
    audio_url = urls.get('audio')
    video_url = urls.get('video')
    if not audio_url or (video and (not video_url)):
        return None
    ffmpeg_params = _ffmpeg_params(chat_id, seconds, source=audio_url, include_reconnect=False) or None
    headers = urls.get('headers') or None
    if video:
        return MediaStream(video_url, audio_parameters=_get_audio_quality(), video_parameters=_get_video_quality(), audio_path=audio_url, headers=headers, ffmpeg_parameters=ffmpeg_params)
    return MediaStream(audio_url, audio_parameters=_get_audio_quality(), audio_path=audio_url, video_flags=MediaStream.Flags.IGNORE, headers=headers, ffmpeg_parameters=ffmpeg_params)

def get_speed(chat_id: int) -> float:
    return float(_speed.get(chat_id, 1.0))

def _ffmpeg_params(chat_id: int, seconds: int=0, source: str | None=None, include_reconnect: bool=True) -> str:
    parts = []
    if include_reconnect and _is_remote_source(source):
        parts.append(_HTTP_RECONNECT_FFMPEG)
    if seconds > 0:
        parts.append(f'-ss {seconds}')
    speed = get_speed(chat_id)
    if abs(speed - 1.0) > 0.001:
        parts.append(f'--audio -atmid -af atempo={speed:.3f}')
        parts.append(f'--video -itsscale {1 / speed:.6f}')
    return ' '.join(parts)

def _local_media_stream(chat_id: int, filepath: str, video: bool, seconds: int=0):
    audio_quality = _get_audio_quality()
    if video:
        return MediaStream(filepath, audio_parameters=audio_quality, video_parameters=_get_video_quality(), ffmpeg_parameters=_ffmpeg_params(chat_id, seconds, source=filepath) or None)
    return MediaStream(filepath, audio_parameters=audio_quality, video_flags=MediaStream.Flags.IGNORE, ffmpeg_parameters=_ffmpeg_params(chat_id, seconds, source=filepath) or None)
from utils.pytgcalls_patch import apply_pytgcalls_probe_patch
from utils.tasks import spawn
apply_pytgcalls_probe_patch()
_PROBE_ERROR_NAMES = ('StreamProbeUnavailable', 'JSONDecodeError', 'ProcessLookupError', 'TimeoutError', 'NoAudioSourceFound', 'NoVideoSourceFound', 'InvalidMediaPath', 'FFmpegNotInstalled')

def _is_probe_error(exc: BaseException) -> bool:
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ in _PROBE_ERROR_NAMES:
            return True
        text = str(cur)
        if 'check_stream' in text or 'ffprobe' in text.lower():
            return True
        cur = cur.__cause__ or cur.__context__
    return False
_UNAVAILABLE_MARKERS = ('video unavailable', 'video is unavailable', 'error code 152', 'removed by the uploader', 'private video', 'this video is not available', 'video has been removed', 'account associated with this video has been terminated', 'sign in to confirm your age', 'who has blocked it in your country', 'is not available in your country', "this content isn't available", 'this content isn’t available', 'members-only content', 'this live event has ended', 'requested format is not available')

def _is_unavailable_media_error(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    seen = 0
    while cur is not None and seen < 8:
        text = str(cur).lower()
        if any((marker in text for marker in _UNAVAILABLE_MARKERS)):
            return True
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False
_peer_ready: set = set()
_peer_verified: set = set()
_join_block: dict = {}
_join_warned: set = set()
_JOIN_BLOCK_FOR = 900.0

def _join_blocked(chat_id: int) -> bool:
    import time as _time
    return _time.monotonic() < _join_block.get(chat_id, 0.0)

def _block_join(chat_id: int) -> None:
    import time as _time
    _join_block[chat_id] = _time.monotonic() + _JOIN_BLOCK_FOR

def _join_log_level(chat_id: int) -> int:
    import logging as _logging
    if chat_id in _join_warned:
        return _logging.DEBUG
    _join_warned.add(chat_id)
    return _logging.WARNING

def _join_peek_level(chat_id: int) -> int:
    import logging as _logging
    return _logging.DEBUG if chat_id in _join_warned else _logging.INFO
_peer_inflight: dict[int, asyncio.Task] = {}

async def ensure_assistant_peer(chat_id: int) -> bool:
    existing = _peer_inflight.get(chat_id)
    if existing is not None and (not existing.done()):
        return bool(await asyncio.shield(existing))
    task = asyncio.create_task(_ensure_assistant_peer_impl(chat_id))
    _peer_inflight[chat_id] = task
    try:
        return bool(await asyncio.shield(task))
    finally:
        if _peer_inflight.get(chat_id) is task and task.done():
            _peer_inflight.pop(chat_id, None)

async def _assistant_is_member(chat_id: int) -> 'bool | None':
    try:
        from melody import bot
        assistant_id = await _get_assistant_id()
        if not assistant_id:
            return None
        member = await bot.get_chat_member(chat_id, assistant_id)
    except Exception as exc:
        name = type(exc).__name__.upper() + str(exc).upper()
        if 'USER_NOT_PARTICIPANT' in name or 'USERNOTPARTICIPANT' in name:
            return False
        return None
    return member.status not in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED)

async def _ensure_assistant_peer_impl(chat_id: int) -> bool:
    try:
        from melody import assistant
    except Exception:
        return False
    if assistant is None:
        return False
    try:
        peer = await assistant.resolve_peer(chat_id)
        if chat_id in _peer_verified:
            _peer_ready.add(chat_id)
            return True
        try:
            await assistant.get_chat(chat_id)
            if await _assistant_is_member(chat_id) is False:
                raise ChannelPrivate()
            _peer_verified.add(chat_id)
            _peer_ready.add(chat_id)
            return True
        except (ChannelInvalid, ChannelPrivate, PeerIdInvalid) as exc:
            LOGGER.warning('assistant peer for %s is stale (%s) — purging and re-learning it', chat_id, type(exc).__name__)
            purge_assistant_peer_storage(chat_id)
            _peer_ready.discard(chat_id)
        except Exception:
            _peer_ready.add(chat_id)
            return True
        del peer
    except Exception:
        _peer_ready.discard(chat_id)
    if await _assistant_is_member(chat_id) is False:
        try:
            if await _auto_join_assistant(chat_id):
                await assistant.resolve_peer(chat_id)
                _peer_ready.add(chat_id)
                _peer_verified.add(chat_id)
                return True
        except Exception as exc:
            LOGGER.debug('direct auto-join failed for %s: %s', chat_id, exc)
        return False
    try:
        await assistant.get_chat(chat_id)
        await assistant.resolve_peer(chat_id)
        _peer_ready.add(chat_id)
        _peer_verified.add(chat_id)
        return True
    except Exception as exc:
        LOGGER.debug('assistant peer warm-up failed for %s: %s', chat_id, exc)
    if await _peer_from_dialogs(chat_id):
        _peer_ready.add(chat_id)
        _peer_verified.add(chat_id)
        return True
    if await _peer_from_invite_link(chat_id):
        _peer_ready.add(chat_id)
        _peer_verified.add(chat_id)
        return True
    try:
        if await _auto_join_assistant(chat_id):
            try:
                await assistant.resolve_peer(chat_id)
            except Exception:
                await assistant.get_chat(chat_id)
                await assistant.resolve_peer(chat_id)
            _peer_ready.add(chat_id)
            _peer_verified.add(chat_id)
            return True
    except Exception as exc:
        LOGGER.debug('assistant peer join-warm-up failed for %s: %s', chat_id, exc)
    return False

async def _peer_from_dialogs(chat_id: int) -> bool:
    try:
        from melody import assistant
        async for dialog in assistant.get_dialogs(limit=400):
            if getattr(dialog.chat, 'id', None) == chat_id:
                await assistant.resolve_peer(chat_id)
                LOGGER.debug('✅ assistant peer for %s recovered from dialogs', chat_id)
                return True
    except Exception as exc:
        LOGGER.debug('dialog peer scan failed for %s: %s', chat_id, exc)
    return False

async def _peer_from_invite_link(chat_id: int) -> bool:
    try:
        from melody import bot, assistant
    except Exception:
        return False
    try:
        chat = await bot.get_chat(chat_id)
    except Exception as exc:
        LOGGER.debug('bot could not read chat %s: %s', chat_id, exc)
        return False
    username = getattr(chat, 'username', None)
    if username:
        try:
            await assistant.get_chat(username)
            await assistant.resolve_peer(chat_id)
            LOGGER.debug('✅ assistant peer for %s resolved via @%s', chat_id, username)
            return True
        except Exception as exc:
            LOGGER.debug('assistant could not resolve @%s: %s', username, exc)
    link = getattr(chat, 'invite_link', None)
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat_id)
        except Exception as exc:
            LOGGER.debug('bot could not export invite link for %s: %s', chat_id, exc)
            return False
    try:
        await assistant.get_chat(link)
        await assistant.resolve_peer(chat_id)
        LOGGER.debug('✅ assistant peer for %s resolved via invite link', chat_id)
        return True
    except Exception as exc:
        LOGGER.debug('invite-link peer resolve failed for %s: %s', chat_id, exc)
    return False

def forget_assistant_peer(chat_id: int) -> None:
    _peer_ready.discard(chat_id)
    _peer_verified.discard(chat_id)

def purge_assistant_peer_storage(chat_id: int) -> bool:
    try:
        from melody import assistant
        storage = getattr(assistant, 'storage', None)
        conn = getattr(storage, 'conn', None)
        if conn is None:
            return False
        with conn:
            conn.execute('DELETE FROM peers WHERE id = ?', (chat_id,))
        LOGGER.debug('🧹 purged stale assistant peer row for %s', chat_id)
        return True
    except Exception as exc:
        LOGGER.debug('could not purge assistant peer row for %s: %s', chat_id, exc)
        return False

async def warm_assistant_peers(limit: int=200) -> int:
    try:
        from melody import assistant
    except Exception:
        return 0
    if assistant is None:
        return 0
    count = 0
    try:
        async for dialog in assistant.get_dialogs(limit=limit):
            chat = getattr(dialog, 'chat', None)
            if chat is None or not getattr(chat, 'id', None):
                continue
            try:
                await assistant.resolve_peer(chat.id)
            except Exception:
                continue
            _peer_ready.add(chat.id)
            count += 1
    except Exception as exc:
        LOGGER.debug('assistant dialog warm-up failed: %s', exc)
    if count:
        LOGGER.debug('✅ Assistant peer cache warmed for %d chats', count)
    return count

async def _stream_track(chat_id: int, track, video: bool=False, _retry: bool=False, start_at: int=0, _dl_retry: bool=False, gen: 'int | None'=None, priority: int=0):
    startup_deadline = time.monotonic() + _PLAY_START_BUDGET
    if _resolving_origin.get(chat_id) in {'manual', 'queue', 'autoplay'} and (not _retry):
        if start_at:
            LOGGER.debug('#stream reset stale start offset chat=%s video=%s offset=%s', chat_id, getattr(track, 'video_id', '?'), start_at)
        start_at = 0
    if _vc_admin_blocked(chat_id) and (not _active.get(chat_id)):
        if _vc_admin_notice_needed(chat_id):
            _mark_vc_admin_notified(chat_id)
            await _notify_playback_failed(chat_id, VC_ADMIN_REQUIRED_MESSAGE)
        return False
    try:
        from melody.core.ytdl import download_audio
        from melody.core.ytdl import cached_file_path, is_direct_url, is_tg_media_id, should_try_direct_stream, direct_stream_muted, is_download_inflight, wait_for_download, wait_for_early_download
        live_source = is_direct_url(track.video_id) or is_direct_url(getattr(track, 'stream_url', ''))
        stream = None
        filepath = None
        local_proxy_source = _is_local_media_proxy(getattr(track, 'stream_url', '') or '')
        direct_only_video = bool(video and (not local_proxy_source) and (int(getattr(track, 'duration', 0) or 0) >= _VIDEO_DIRECT_ONLY_SECONDS))
        source_errors = []
        direct_task = None
        download_task = None
        early_task = None
        download_cancelled = False
        early_file = False
        pending: set = set()
        cached = cached_file_path(track.video_id, audio_only=not video)
        # Archive lookup happens before any YouTube resolver. On a fresh
        # Heroku dyno this also searches LOG_GROUP_ID, so the local SQLite index
        # does not need to survive a restart. Audio and VPlay use separate
        # archive variants (a=audio, v=video).
        if not cached:
            try:
                from melody import bot as _archive_bot
                from utils.telegram_archive import restore_archived_file
                cached = await restore_archived_file(_archive_bot, track.video_id, video=video)
            except Exception:
                cached = None
        if cached:
            filepath = cached
            stream = _local_media_stream(chat_id, filepath, video, start_at)
            LOGGER.info('#stream cache-hit %s → instant play (%s)', track.video_id, 'telegram archive' if str(cached).startswith(('http://127.0.0.1:', 'http://localhost:')) else 'local file')
        else:
            if is_download_inflight(track.video_id, audio_only=not video):
                LOGGER.debug('#stream reusing in-flight download %s', track.video_id)
            # If this audio already exists in the Telegram archive, skip the
            # YouTube resolver entirely. download_audio() will return a local
            # Telegram range-proxy URL, so cached songs start without spawning
            # another yt-dlp/direct-stream job. Video/VPlay behavior is unchanged.
            archived_audio = False
            if not video and not live_source:
                try:
                    from utils.telegram_archive import has_archive
                    archived_audio = has_archive(track.video_id, video=False)
                except Exception:
                    archived_audio = False
            audio_direct = (video or os.getenv('AUDIO_DIRECT_STREAM', 'false').strip().lower() in {'1', 'true', 'yes', 'on'})
            direct_first = (not archived_audio) and (live_source or (audio_direct and should_try_direct_stream() and (not direct_stream_muted()) and (not is_tg_media_id(track.video_id))))
            if direct_first:
                direct_task = asyncio.create_task(_build_direct_stream(chat_id, track, video, start_at))
                pending.add(direct_task)

            async def _delayed_download(delay: float):
                nonlocal download_cancelled
                if delay > 0:
                    if direct_task is not None:
                        try:
                            await asyncio.wait({direct_task}, timeout=delay)
                        except Exception:
                            pass
                    else:
                        await asyncio.sleep(delay)
                try:
                    filepath = await download_audio(track.video_id, audio_only=not video, priority=priority, owner=chat_id, allow_early=not video)
                    return filepath
                except Exception as exc:
                    from melody.core.ytdl import is_download_cancelled
                    if is_download_cancelled(exc):
                        download_cancelled = True
                        LOGGER.info('#download intentional cancellation chat=%s video=%s', chat_id, track.video_id)
                        return None
                    raise

            async def _archive_download_task(task):
                try:
                    archived_path = await asyncio.shield(task)
                    if archived_path and archived_path.endswith('.early'):
                        archived_path = await wait_for_download(track.video_id, audio_only=not video)
                    if archived_path and (not archived_path.endswith('.early')):
                        await _persist_completed_song(archived_path, track)
                except Exception as exc:
                    LOGGER.debug('fallback archive skipped for %s: %s', track.video_id, exc)
            early_task = None
            if not live_source and (not direct_only_video):
                download_task = asyncio.create_task(_delayed_download(_DOWNLOAD_START_DELAY if direct_first else 0.0))
                pending.add(download_task)
                if not video:

                    async def _await_early_prefix():
                        try:
                            return await wait_for_early_download(track.video_id, audio_only=True, timeout=max(0.5, startup_deadline - time.monotonic()))
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            return None
                        except Exception:
                            return None
                    early_task = asyncio.create_task(_await_early_prefix())
                    pending.add(early_task)

        async def _finish_started_fallback():
            if download_task is None:
                return None
            if download_task.done():
                try:
                    return download_task.result()
                except Exception as exc:
                    source_errors.append(exc)
                    return None
            try:
                try:
                    early_path = await wait_for_early_download(track.video_id, audio_only=not video, timeout=min(float(os.getenv('FASTPLAY_MAX_WAIT', '1.5')), _PLAY_FALLBACK_TIMEOUT))
                except asyncio.TimeoutError:
                    early_path = None
                if early_path:
                    return early_path
                return await asyncio.wait_for(asyncio.shield(download_task), timeout=_PLAY_FALLBACK_TIMEOUT)
            except asyncio.TimeoutError:
                LOGGER.warning('fallback source exceeded grace window for %s in %s', track.video_id, chat_id)
                raise asyncio.TimeoutError('fallback download exceeded startup budget; startup grace window expired')
        while pending and stream is None:
            remaining = startup_deadline - time.monotonic()
            if remaining <= 0:
                fallback_path = await _finish_started_fallback()
                if fallback_path:
                    filepath = fallback_path
                    early_file = bool(filepath.endswith('.early'))
                    stream = _local_media_stream(chat_id, filepath, video, start_at)
                    spawn(_archive_download_task(download_task), name=f'archive:{track.video_id}')
                    break
                for task in pending:
                    if task is not download_task:
                        task.cancel()
                raise asyncio.TimeoutError('playback source startup budget exceeded')
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED, timeout=remaining)
            if not done:
                fallback_path = await _finish_started_fallback()
                if fallback_path:
                    filepath = fallback_path
                    early_file = bool(filepath.endswith('.early'))
                    stream = _local_media_stream(chat_id, filepath, video, start_at)
                    spawn(_archive_download_task(download_task), name=f'archive:{track.video_id}')
                    break
                for task in pending:
                    if task is not download_task:
                        task.cancel()
                raise asyncio.TimeoutError('playback source startup budget exceeded')
            for task in done:
                try:
                    result = task.result()
                except Exception as source_exc:
                    source_errors.append(source_exc)
                    continue
                if task is direct_task and result is not None:
                    stream = result
                    break
                if task is early_task and result:
                    filepath = result
                    early_file = bool(filepath.endswith('.early'))
                    LOGGER.info('⚡ #stream early-prefix handoff %s (instant start)', track.video_id)
                    if download_task is not None:
                        spawn(_archive_download_task(download_task), name=f'archive:{track.video_id}')
                    stream = _local_media_stream(chat_id, filepath, video, start_at)
                    break
                if task is download_task and result:
                    filepath = result
                    early_file = bool(filepath and filepath.endswith('.early'))
                    spawn(_archive_download_task(task), name=f'archive:{track.video_id}')
                    stream = _local_media_stream(chat_id, filepath, video, start_at)
                    break
        if stream is None:
            if download_cancelled:
                from melody.core.ytdl import _DownloadCancelled
                raise _DownloadCancelled('superseded by a newer playback request')
            if source_errors:
                raise source_errors[-1]
            raise RuntimeError(f'No playable source for {track.video_id}')
        if direct_task is not None and direct_task in pending:
            direct_task.add_done_callback(_consume_task_exception)
        if early_task is not None and (not early_task.done()):
            early_task.cancel()
        if filepath is None and download_task is not None:
            if not download_task.done():
                download_task.add_done_callback(_consume_task_exception)
            spawn(_archive_download_task(download_task), name=f'archive:{track.video_id}')
            LOGGER.debug('#download recovery backup retained after direct stream chat=%s video=%s', chat_id, track.video_id)
        _silence_playing.pop(chat_id, None)
        was_active = _active.get(chat_id)
        peer_ok = await ensure_assistant_peer(chat_id)
        if not peer_ok:
            LOGGER.info('assistant peer miss for %s — attempting one clean auto-join before play()', chat_id)
            joined = await _auto_join_assistant(chat_id)
            peer_ok = joined and await ensure_assistant_peer(chat_id)
        if not peer_ok:
            await _notify_playback_failed(chat_id, '⚠️ <b>Assistant group me properly join nahi ho paya.</b>\nAssistant ko group/channel me member aur voice-chat permissions ke saath admin rakho, phir <code>/play</code> dobara bhejo.')
            return False
        async with _get_stream_commit_lock(chat_id):
            if _is_leaving(chat_id):
                LOGGER.debug('stream aborted for %s — leave requested mid-download', chat_id)
                return
            if _is_stale_generation(chat_id, gen):
                LOGGER.info('#stream discarding stale resolve for %s in %s (gen=%s no longer authoritative — a newer play request superseded it)', track.video_id, chat_id, gen)
                return
            try:
                if filepath is None:
                    await asyncio.wait_for(_pytgcalls.play(chat_id, stream), timeout=_LOCAL_PROXY_PLAY_TIMEOUT if local_proxy_source else _PLAY_PROBE_TIMEOUT)
                else:
                    await asyncio.wait_for(_pytgcalls.play(chat_id, stream), timeout=_LOCAL_PLAY_TIMEOUT)
            except Exception as play_exc:
                if local_proxy_source:
                    LOGGER.warning('local Telegram media proxy handoff failed for %s in %s (%s)', track.video_id, chat_id, type(play_exc).__name__)
                    return False
                if filepath is not None and (not early_file) or not (_is_probe_error(play_exc) or isinstance(play_exc, asyncio.TimeoutError)):
                    raise
                direct_retry_ok = False
                if filepath is None and (not early_file) and (not local_proxy_source) and (startup_deadline - time.monotonic() > 2.0):
                    try:
                        fresh_stream = await _build_direct_stream(chat_id, track, video, start_at, force=True)
                        if fresh_stream is not None:
                            await asyncio.wait_for(_pytgcalls.play(chat_id, fresh_stream), timeout=_PLAY_PROBE_TIMEOUT)
                            stream = fresh_stream
                            direct_retry_ok = True
                            LOGGER.info('#stream fresh direct retry recovered %s in %s', track.video_id, chat_id)
                    except Exception as retry_exc:
                        LOGGER.info('#stream fresh direct retry failed for %s in %s (%s)', track.video_id, chat_id, type(retry_exc).__name__)
                if direct_retry_ok:
                    pass
                elif early_file:
                    LOGGER.info('Early audio prefix probe failed for %s in %s — waiting for completed file.', track.video_id, chat_id)
                    completed = await wait_for_download(track.video_id, audio_only=not video)
                    filepath = completed
                    if not filepath:
                        raise RuntimeError('early audio download completed without a file')
                    early_file = False
                elif direct_only_video:
                    LOGGER.warning('#stream direct-only video unavailable for %s in %s; skipping unsafe full download fallback', track.video_id, chat_id)
                    raise RuntimeError('long video direct stream unavailable; local fallback disabled') from play_exc
                else:
                    LOGGER.info('Direct CDN stream unusable for %s in %s (%s: %s) — falling back to download.', track.video_id, chat_id, type(play_exc).__name__, redact_sensitive_text(play_exc))
                    if download_task is not None and (not download_task.done()):
                        filepath = await asyncio.wait_for(asyncio.shield(download_task), timeout=_PLAY_FALLBACK_TIMEOUT)
                    else:
                        filepath = await download_audio(track.video_id, audio_only=not video, priority=priority, owner=chat_id, allow_early=not video)
                if filepath and (not filepath.endswith('.early')):
                    spawn(_persist_completed_song(filepath, track), name=f'song-cache:{track.video_id}')
                stream = _local_media_stream(chat_id, filepath, video, start_at)
                if _is_stale_generation(chat_id, gen):
                    LOGGER.info('#stream discarding stale fallback resolve for %s in %s (gen=%s no longer authoritative)', track.video_id, chat_id, gen)
                    return
                forget_assistant_peer(chat_id)
                await ensure_assistant_peer(chat_id)
                try:
                    await asyncio.wait_for(_pytgcalls.play(chat_id, stream), timeout=_LOCAL_PLAY_TIMEOUT)
                except ChatAdminRequired:
                    _block_vc_admin(chat_id)
                    if download_task is not None and (not download_task.done()):
                        download_task.cancel()
                        download_task.add_done_callback(_consume_task_exception)
                    if _vc_admin_notice_needed(chat_id):
                        _mark_vc_admin_notified(chat_id)
                        await _notify_playback_failed(chat_id, VC_ADMIN_REQUIRED_MESSAGE)
                    LOGGER.info('fallback VC creation blocked in %s: assistant needs admin rights', chat_id)
                    return False
            if _is_stale_generation(chat_id, gen):
                LOGGER.info('#stream stale play result fenced for %s in %s (gen=%s)', track.video_id, chat_id, gen)
                return False
            if _is_leaving(chat_id):
                LOGGER.debug('stream started for %s during a leave — disconnecting', chat_id)
                await _hard_leave(chat_id)
                _forget_call_state(chat_id)
                return
            _clear_vc_admin_block(chat_id)
            _active[chat_id] = True
            _listener_mode.pop(chat_id, None)
            _is_video[chat_id] = video
            _play_start_time[chat_id] = time.time()
            _seek_offset[chat_id] = max(0, int(start_at or 0))
            for k in [k for k in _premature_resumes if k.startswith(f'{chat_id}:') and (not k.endswith(f':{track.video_id}'))]:
                _premature_resumes.pop(k, None)
            if not was_active:
                vol = get_volume(chat_id)
                if vol != 100:
                    try:
                        await _pytgcalls.change_volume_call(chat_id, vol)
                    except Exception:
                        pass
        from utils.playback_state import save_snapshot
        await save_snapshot(chat_id, get_current(chat_id), get_queue(chat_id), get_loop(chat_id), get_volume(chat_id), active=True, position=_seek_offset[chat_id], video=video, speed=get_speed(chat_id))
        return True
    except Exception as exc:
        _active.pop(chat_id, None)
        _silence_playing.pop(chat_id, None)
        try:
            from melody.core.ytdl import is_download_cancelled
            if is_download_cancelled(exc):
                LOGGER.info('#download cancellation consumed chat=%s video=%s gen=%s', chat_id, getattr(track, 'video_id', '?'), gen)
                return False
        except Exception:
            pass
        if _is_unavailable_media_error(exc):
            source_query = getattr(track, 'source_query', '') or ''
            if source_query and (not getattr(track, '_alternate_attempted', False)):
                setattr(track, '_alternate_attempted', True)
                try:
                    from melody.core.ytdl import find_playable_candidate
                    alternate = await find_playable_candidate(source_query, exclude_video_id=getattr(track, 'video_id', ''), want_video=video)
                except Exception as alternate_exc:
                    alternate = None
                    LOGGER.info('alternate candidate recovery failed: %s', alternate_exc)
                if alternate:
                    old_id = track.video_id
                    track.video_id = alternate['id']
                    track.title = alternate.get('title', track.title)
                    track.duration = int(alternate.get('duration') or 0)
                    track.stream_url = alternate.get('stream_url', '')
                    track.thumbnail = alternate.get('thumbnail', track.thumbnail)
                    track.uploader = alternate.get('uploader', track.uploader)
                    LOGGER.info('Retrying playback with alternate candidate old=%s new=%s chat=%s', old_id, track.video_id, chat_id)
                    started = await _stream_track(chat_id, track, video=video, _retry=True, start_at=start_at, gen=gen, priority=priority)
                    if started:
                        return started
            LOGGER.info('Skipping unavailable track %s in %s', getattr(track, 'video_id', '?'), chat_id)
            title = html_escape((getattr(track, 'title', '') or 'Ye gaana')[:50])
            await _notify_playback_failed(chat_id, f'⏭ <b>{title}</b> YouTube par available nahi hai (removed/private).\nNext song play kar raha hoon…')
            try:
                await _play_next(chat_id)
            except Exception:
                pass
            return
        if isinstance(exc, NoActiveGroupCall):
            # A just-started VC can race Telegram's group-call propagation.
            # Refresh the assistant peer and retry once before telling the user
            # that no VC exists.
            if not _retry and (not _is_leaving(chat_id)) and (not _is_stale_generation(chat_id, gen)):
                LOGGER.info('NoActiveGroupCall race in %s — refreshing peer and retrying VC handoff once', chat_id)
                forget_assistant_peer(chat_id)
                await asyncio.sleep(0.8)
                joined = await _auto_join_assistant(chat_id)
                if joined and await ensure_assistant_peer(chat_id):
                    await _stream_track(chat_id, track, video=video, _retry=True, start_at=start_at, gen=gen, priority=priority)
                    return
            await _notify_playback_failed(chat_id, NO_ACTIVE_VC_MESSAGE)
            return
        flood_wait = _flood_seconds(exc)
        if flood_wait is not None:
            LOGGER.warning('FLOOD_WAIT %ss while joining VC in %s', flood_wait, chat_id)
            if flood_wait <= _FLOOD_RETRY_LIMIT and (not _retry):
                await _notify_playback_failed(chat_id, f'⏳ Tᴇʟᴇɢʀᴀᴍ ɴᴇ ᴛʜᴏᴅᴀ ʀᴜᴋɴᴇ ᴋᴏ ʙᴏʟᴀ ʜᴀɪ (<code>{flood_wait}s</code>) — ᴀᴜᴛᴏ-ʀᴇᴛʀʏ ᴋᴀʀ ʀᴀʜᴀ ʜᴏᴏɴ 🔁')
                await asyncio.sleep(flood_wait + 1)
                if _is_leaving(chat_id) or _is_stale_generation(chat_id, gen):
                    return
                await _stream_track(chat_id, track, video=video, _retry=True, start_at=start_at, gen=gen)
                return
            minutes = max(1, round(flood_wait / 60))
            await _notify_playback_failed(chat_id, f'⏳ <b>Tᴇʟᴇɢʀᴀᴍ ғʟᴏᴏᴅ ʟɪᴍɪᴛ</b>\nAꜱꜱɪꜱᴛᴀɴᴛ ᴋᴏ <code>{flood_wait}s</code> (~{minutes} ᴍɪɴ) ᴡᴀɪᴛ ᴋᴀʀɴᴀ ᴘᴀᴅᴇɢᴀ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴊᴏɪɴ ᴋᴀʀɴᴇ ᴋᴇ ʟɪʏᴇ.\nUᴛɴᴇ ᴅᴇʀ ʙᴀᴀᴅ ᴅᴏʙᴀʀᴀ <code>/play</code> ᴋᴀʀᴏ 🎧')
            return
        _peer_missing = isinstance(exc, KeyError) and 'ID not found' in str(exc)
        if _peer_missing and (not _retry):
            forget_assistant_peer(chat_id)
            purge_assistant_peer_storage(chat_id)
            if await ensure_assistant_peer(chat_id) and (not _is_stale_generation(chat_id, gen)):
                LOGGER.debug('✅ Assistant peer resolved for %s — retrying playback.', chat_id)
                await _stream_track(chat_id, track, video=video, _retry=True, start_at=start_at, gen=gen)
                return
        if isinstance(exc, (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserBannedInChannel)) and (not _retry):
            LOGGER.warning('Assistant cannot resolve chat %s (%s) — attempting auto-join/unban.', chat_id, type(exc).__name__)
            forget_assistant_peer(chat_id)
            purge_assistant_peer_storage(chat_id)
            joined = await _auto_join_assistant(chat_id) or await ensure_assistant_peer(chat_id)
            if joined and (not _is_stale_generation(chat_id, gen)):
                LOGGER.debug('✅ Assistant auto-joined/unbanned in chat %s — retrying playback.', chat_id)
                await _stream_track(chat_id, track, video=video, _retry=True, start_at=start_at, gen=gen)
                return
            await _notify_playback_failed(chat_id, "⚠️ <b>Voice assistant account is group mein nahi hai, aur auto-join bhi fail ho gaya.</b>\n\nVoice chat me gaana bajane ke liye assistant account ka bhi is group ka member hona zaroori hai. Please assistant ko group mein manually add karo (ya bot ko 'Invite Users via Link' admin permission do) aur phir se <code>/play</code> try karo.")
            return False
        elif isinstance(exc, (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserBannedInChannel)):
            await _notify_playback_failed(chat_id, '⚠️ <b>Assistant ko group mein add/unban karne ke baad bhi gaana play nahi ho paya.</b>\n\nDobara <code>/play</code> try karo.')
            return False
        elif isinstance(exc, ChatAdminRequired):
            _block_vc_admin(chat_id)
            if _vc_admin_notice_needed(chat_id):
                _mark_vc_admin_notified(chat_id)
                await _notify_playback_failed(chat_id, VC_ADMIN_REQUIRED_MESSAGE)
            return False
        else:
            if not _dl_retry:
                LOGGER.warning('playback failed for %s in %s (%s: %s) — one clean retry', getattr(track, 'video_id', '?'), chat_id, type(exc).__name__, exc)
                try:
                    _cleanup_partial_files(getattr(track, 'video_id', ''))
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                if _is_stale_generation(chat_id, gen):
                    return
                try:
                    await _stream_track(chat_id, track, video=video, start_at=start_at, _dl_retry=True, gen=gen)
                    return
                except Exception as retry_exc:
                    exc = retry_exc
            await _notify_playback_failed(chat_id, '❌ <b>Gaana play nahi ho paya.</b>\n\nDobara <code>/play</code> try karo.')
            if isinstance(exc, asyncio.TimeoutError):
                LOGGER.warning('playback timed out for %s in %s after one retry (%s)', getattr(track, 'video_id', '?'), chat_id, exc)
                return False
        await send_error_log(f'_stream_track failed in {chat_id}', exc, context={'chat_id': chat_id, 'song_title': track.title if track else None, 'video_id': track.video_id if track else None, 'uploader': track.uploader if track else None})
_assistant_id: 'int | None' = None

async def _get_assistant_id() -> 'int | None':
    global _assistant_id
    if _assistant_id is not None:
        return _assistant_id
    try:
        from melody import assistant
        me = await assistant.get_me()
        _assistant_id = me.id
        return _assistant_id
    except Exception as exc:
        LOGGER.warning("Could not resolve assistant's own user id: %s", exc)
        return None

async def _unban_or_unmute_assistant(chat_id: int) -> str:
    try:
        from melody import bot
    except Exception:
        return 'unknown'
    assistant_id = await _get_assistant_id()
    if not assistant_id:
        return 'unknown'
    try:
        member = await bot.get_chat_member(chat_id, assistant_id)
    except Exception:
        return 'not_member'
    was_banned = member.status == enums.ChatMemberStatus.BANNED
    try:
        if was_banned:
            LOGGER.log(_join_peek_level(chat_id), "Assistant is banned in %s — auto-unbanning with bot's admin rights.", chat_id)
            await bot.unban_chat_member(chat_id, assistant_id)
            return 'unbanned'
        elif member.status == enums.ChatMemberStatus.RESTRICTED:
            perms = member.permissions
            is_muted = not (perms and perms.can_send_messages)
            if is_muted:
                LOGGER.warning('Assistant is muted/restricted in %s — auto-lifting restriction.', chat_id)
                await bot.restrict_chat_member(chat_id, assistant_id, permissions=ChatPermissions(all_perms=True))
    except Exception as exc:
        LOGGER.log(_join_log_level(chat_id), 'Could not auto-unban/unmute assistant in %s: %s', chat_id, exc)
        if was_banned:
            await _ask_for_unban(chat_id, assistant_id)
            return 'banned_no_rights'
    return 'ok'

async def _ask_for_unban(chat_id: int, assistant_id: int) -> None:
    try:
        from melody import bot
        from pyrogram import enums as _enums
        await bot.send_message(chat_id, f'⚠️ <b>Mᴇʀᴀ ᴀssɪsᴛᴀɴᴛ ᴀᴄᴄᴏᴜɴᴛ ɪs ɢʀᴏᴜᴘ ᴍᴇɪɴ BAN ʜᴀɪ</b> 🚫\n\nIsliye voice chat me gaana play nahi ho sakta. Do options hain:\n\n1️⃣ <b>Mujhe "Ban Users" admin permission do</b> — phir main khud assistant ko unban karke VC me bula lunga (kuch karna nahi padega).\n\n2️⃣ <b>Ya khud unban kar do</b> — ye command bhejo:\n<code>/unban {assistant_id}</code>\n\nUske baad <code>/play</code> dobara bhejo 🎶', parse_mode=_enums.ParseMode.HTML)
    except Exception as exc:
        LOGGER.debug('Could not ask %s for unban rights: %s', chat_id, exc)

async def _auto_join_assistant(chat_id: int) -> bool:
    try:
        from melody import bot, assistant
    except Exception as exc:
        LOGGER.warning('Auto-join: could not import bot/assistant clients: %s', exc)
        return False
    if _join_blocked(chat_id):
        LOGGER.debug('Auto-join: skipping %s (cooling down after a permission failure)', chat_id)
        return False
    status = await _unban_or_unmute_assistant(chat_id)
    if status == 'banned_no_rights':
        _block_join(chat_id)
        return False
    link = None
    try:
        chat = await bot.get_chat(chat_id)
        if getattr(chat, 'username', None):
            link = chat.username
        else:
            link = getattr(chat, 'invite_link', None)
    except Exception as exc:
        LOGGER.debug('Auto-join: bot could not read chat %s: %s', chat_id, exc)
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat_id)
        except Exception as exc:
            if 'ADMIN' in str(exc).upper() or 'RIGHT' in str(exc).upper():
                _block_join(chat_id)
                await _notify_playback_failed(chat_id, '⚠️ <b>Assistant ko group me add nahi kar pa raha.</b>\nMujhe admin banao aur <b>Invite Users via Link</b> permission do, phir <code>/play</code> dobara bhejo.')
            LOGGER.warning('Auto-join: bot could not export invite link for %s: %s', chat_id, exc)
            return False
    try:
        try:
            await assistant.join_chat(link)
        except Exception as first_exc:
            txt = str(first_exc).upper()
            if 'INVITE_HASH' in txt or 'EXPIRED' in txt or 'INVALID' in txt:
                link = await bot.export_chat_invite_link(chat_id)
                await assistant.join_chat(link)
            else:
                raise
    except Exception as exc:
        if 'REQUEST' in str(exc).upper() or 'APPROV' in str(exc).upper():
            try:
                me = await assistant.get_me()
                await bot.approve_chat_join_request(chat_id, me.id)
                LOGGER.debug('Auto-join: approved assistant join request in %s', chat_id)
            except Exception as approve_exc:
                LOGGER.warning('Auto-join: could not approve assistant join request in %s: %s', chat_id, approve_exc)
        LOGGER.debug('Auto-join: assistant.join_chat raised (may be harmless): %s', exc)
    try:
        # Telegram may acknowledge join before the new membership/peer is
        # visible to PyTgCalls. Give it a short propagation window and verify
        # both chat access and peer resolution before reporting success.
        await asyncio.sleep(0.4)
        await assistant.get_chat(chat_id)
        await assistant.resolve_peer(chat_id)
        _join_block.pop(chat_id, None)
        _join_warned.discard(chat_id)
        return True
    except Exception as exc:
        import time as _time
        _join_block[chat_id] = _time.monotonic() + 30.0
        LOGGER.log(_join_log_level(chat_id), 'Auto-join: assistant still cannot resolve %s after join attempt: %s', chat_id, exc)
        return False
_FLOOD_RETRY_LIMIT = 45

def _flood_seconds(exc: BaseException) -> 'int | None':
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, FloodWait):
            return int(getattr(exc, 'value', 0) or getattr(exc, 'x', 0) or 0)
        exc = exc.__cause__ or exc.__context__
    return None
_playback_notice_until: dict[int, float] = {}
_PLAYBACK_NOTICE_TTL = 60.0

async def _notify_playback_failed(chat_id: int, text: str):
    now = time.monotonic()
    deadline = _playback_notice_until.get(chat_id, 0.0)
    if deadline > now:
        return
    _playback_notice_until[chat_id] = now + _PLAYBACK_NOTICE_TTL
    try:
        from melody import bot
        from pyrogram import enums
        await bot.send_message(chat_id, text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass

async def pre_join(chat_id: int, video: bool=False) -> bool:
    if video:
        return False
    if _active.get(chat_id):
        return False
    silence = await _get_silence_file()
    if not silence or not _pytgcalls:
        return False
    async with _get_play_lock(chat_id):
        if _active.get(chat_id):
            return False
        return await _pre_join_locked(chat_id, silence)

async def _pre_join_locked(chat_id: int, silence: str) -> bool:
    if _is_leaving(chat_id):
        return False
    try:
        peer_ok = await ensure_assistant_peer(chat_id)
        if not peer_ok:
            joined = await _auto_join_assistant(chat_id)
            peer_ok = joined and await ensure_assistant_peer(chat_id)
        if not peer_ok:
            LOGGER.info('pre_join skipped for %s — assistant peer/member is not ready', chat_id)
            return False
        audio_quality = _get_audio_quality()
        sstream = MediaStream(silence, audio_parameters=audio_quality, video_flags=MediaStream.Flags.IGNORE)
        _silence_playing[chat_id] = True
        await asyncio.wait_for(_pytgcalls.play(chat_id, sstream), timeout=_PREJOIN_TIMEOUT)
        _active[chat_id] = True
        LOGGER.debug('⚡ pre_join: instant VC join done for %d', chat_id)
        return True
    except asyncio.TimeoutError:
        LOGGER.debug('pre_join timed out for %d — real song will join VC normally', chat_id)
        _silence_playing.pop(chat_id, None)
        return False
    except ChatAdminRequired:
        _block_vc_admin(chat_id)
        _silence_playing.pop(chat_id, None)
        if _vc_admin_notice_needed(chat_id):
            _mark_vc_admin_notified(chat_id)
            await _notify_playback_failed(chat_id, VC_ADMIN_REQUIRED_MESSAGE)
        LOGGER.info('pre_join blocked in %s: assistant needs admin rights to create the VC', chat_id)
        return False
    except Exception as e:
        LOGGER.debug('pre_join failed for %d: %s — real song will join VC normally', chat_id, e)
        _silence_playing.pop(chat_id, None)
        return False

async def abort_prejoin_if_idle(chat_id: int):
    if _silence_playing.get(chat_id) and (not get_current(chat_id)):
        _mark_leaving(chat_id)
        _active.pop(chat_id, None)
        _silence_playing.pop(chat_id, None)
        await _hard_leave(chat_id)

async def _replay_listener_silence(chat_id: int) -> None:
    silence = await _get_silence_file()
    if not silence or not _pytgcalls or (not _listener_mode.get(chat_id)):
        return
    try:
        await _pytgcalls.play(chat_id, MediaStream(silence, audio_parameters=_get_audio_quality(), video_flags=MediaStream.Flags.IGNORE))
        _silence_playing[chat_id] = True
    except Exception as exc:
        LOGGER.debug('listener silence loop failed for %s: %s', chat_id, exc)
        _listener_mode.pop(chat_id, None)
        _forget_call_state(chat_id)
        await _hard_leave(chat_id)

def is_listener(chat_id: int) -> bool:
    return bool(_listener_mode.get(chat_id))

async def join_as_listener(chat_id: int) -> bool:
    LOGGER.debug('join_as_listener is disabled (no silent VC presence) — %s', chat_id)
    return False

async def leave_listener(chat_id: int) -> None:
    if not _listener_mode.pop(chat_id, None):
        return
    if get_current(chat_id):
        return
    _mark_leaving(chat_id)
    _forget_call_state(chat_id)
    await _hard_leave(chat_id)
    LOGGER.debug('🎧 listener mode left VC in %s', chat_id)

async def force_play_stream(chat_id: int, track, video: bool=False, prejoin: bool=False) -> bool:
    _clear_leaving(chat_id)
    try:
        from melody.core.ytdl import cancel_lower_priority_downloads
        cancel_lower_priority_downloads(0, exclude_video_id=track.video_id)
    except Exception:
        pass
    async with _get_play_lock(chat_id):
        _cancel_stale_download(chat_id, track.video_id, bool(video))
        try:
            from melody.core.queue import remove_matching_track
            remove_matching_track(chat_id, track.video_id)
        except Exception:
            pass
        set_current(chat_id, track)
        _seek_offset[chat_id] = 0
        gen = _begin_generation(chat_id)
        _resolving[chat_id] = gen
        _resolving_track[chat_id] = (track.video_id, bool(video))
        _resolving_origin[chat_id] = 'manual'
        if prejoin and (not _active.get(chat_id)):
            silence = await _get_silence_file()
            if silence and _pytgcalls and (not video):
                await _pre_join_locked(chat_id, silence)
    result = False
    try:
        result = await _stream_track(chat_id, track, video=video, gen=gen, priority=_FORCE_DOWNLOAD_PRIORITY)
        return result is True and (not is_vc_admin_blocked(chat_id))
    finally:
        if result is not True and _resolving.get(chat_id) == gen and (get_current(chat_id) is track):
            set_current(chat_id, None)
        if _resolving.get(chat_id) == gen:
            _resolving.pop(chat_id, None)
            _resolving_track.pop(chat_id, None)
            _resolving_origin.pop(chat_id, None)

async def play_stream(chat_id: int, track, video: bool=False, prejoin: bool=True) -> bool:
    from melody.core.queue import add_to_queue
    try:
        from melody.core.ytdl import cancel_lower_priority_downloads
        cancel_lower_priority_downloads(0, exclude_video_id=track.video_id)
    except Exception:
        pass
    _clear_leaving(chat_id)
    lock = _get_play_lock(chat_id)
    async with lock:
        if _active.get(chat_id) and (not _silence_playing.get(chat_id)) and (_resolving_origin.get(chat_id) != 'autoplay'):
            add_to_queue(chat_id, track)
            spawn(_prefetch_upcoming(chat_id))
            return False
        if _resolving_origin.get(chat_id) == 'autoplay':
            LOGGER.info('#play manual takeover of autoplay transition in %s', chat_id)
        if chat_id in _resolving:
            if not _active.get(chat_id) or _silence_playing.get(chat_id) or _resolving_origin.get(chat_id) == 'autoplay':
                _cancel_stale_download(chat_id, track.video_id, bool(video))
            else:
                add_to_queue(chat_id, track)
                spawn(_prefetch_upcoming(chat_id))
                return False
        set_current(chat_id, track)
        _seek_offset[chat_id] = 0
        gen = _begin_generation(chat_id)
        _resolving[chat_id] = gen
        _resolving_track[chat_id] = (track.video_id, bool(video))
        _resolving_origin[chat_id] = 'manual'
        if prejoin and (not _active.get(chat_id)):
            silence = await _get_silence_file()
            if silence and _pytgcalls and (not video):
                await _pre_join_locked(chat_id, silence)
    try:
        from melody.core.autoplay import reset_autoplay_guard
        reset_autoplay_guard(chat_id)
    except Exception:
        pass
    result = False
    try:
        result = await _stream_track(chat_id, track, video=video, gen=gen, priority=0)
    finally:
        if result is not True and _resolving.get(chat_id) == gen and (get_current(chat_id) is track):
            set_current(chat_id, None)
        if _resolving.get(chat_id) == gen:
            _resolving.pop(chat_id, None)
            _resolving_track.pop(chat_id, None)
            _resolving_origin.pop(chat_id, None)
    if result is not True or is_vc_admin_blocked(chat_id):
        return False
    return True

def _is_probably_stale_stream_end(chat_id: int) -> bool:
    current = get_current(chat_id)
    if current is None:
        return False
    try:
        duration = int(getattr(current, 'duration', 0) or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 3:
        return False
    started = _play_start_time.get(chat_id)
    if not started:
        return False
    elapsed = time.time() - started
    if 0 <= elapsed < 1.5:
        LOGGER.info('#stream ignoring early StreamEnded for %s (%s, %.2fs old)', chat_id, getattr(current, 'video_id', '?'), elapsed)
        return True
    return False

def _mark_leaving(chat_id: int) -> None:
    _leaving[chat_id] = time.monotonic() + _LEAVE_GRACE

def _is_leaving(chat_id: int) -> bool:
    deadline = _leaving.get(chat_id)
    if not deadline:
        return False
    if time.monotonic() > deadline:
        _leaving.pop(chat_id, None)
        return False
    return True

def _clear_leaving(chat_id: int) -> None:
    _leaving.pop(chat_id, None)

async def _hard_leave(chat_id: int) -> None:
    for attempt in (1, 2):
        try:
            await asyncio.wait_for(_pytgcalls.leave_call(chat_id), timeout=10)
            return
        except Exception as exc:
            if _is_not_in_call(exc):
                return
            if attempt == 2:
                LOGGER.warning('leave_call failed for %s: %s', chat_id, exc)
                return
            await asyncio.sleep(0.5)

def _forget_call_state(chat_id: int) -> None:
    _active.pop(chat_id, None)
    _silence_playing.pop(chat_id, None)
    _is_video.pop(chat_id, None)
    _play_start_time.pop(chat_id, None)
    _seek_offset.pop(chat_id, None)
    _prefetch_inflight.pop(chat_id, None)
    for k in [k for k in _premature_resumes if k.startswith(f'{chat_id}:')]:
        _premature_resumes.pop(k, None)
    _speed.pop(chat_id, None)
    _muted.pop(chat_id, None)
    _listener_mode.pop(chat_id, None)

def _is_not_in_call(exc: BaseException) -> bool:
    if isinstance(exc, NotInCallError):
        return True
    names = {type(exc).__name__}
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        names.add(type(cause).__name__)
    if names & {'NotInCallError', 'ConnectionNotFound', 'GroupCallNotFound'}:
        return True
    text = ' '.join((str(e).lower() for e in (exc, cause) if e is not None))
    return any((marker in text for marker in ('no active group call', 'not in a call', 'group call not found', 'groupcall_invalid', 'connectionnotfound')))

async def pause_stream(chat_id: int) -> bool:
    if not _pytgcalls or not _active.get(chat_id):
        _forget_call_state(chat_id)
        return False
    try:
        await asyncio.wait_for(_pytgcalls.pause(chat_id), timeout=_CONTROL_RPC_TIMEOUT)
        return True
    except Exception as exc:
        if _is_not_in_call(exc):
            LOGGER.info('pause_stream: not in call for %s — clearing state', chat_id)
            _forget_call_state(chat_id)
            return False
        current = get_current(chat_id)
        await send_error_log(f'pause_stream failed in {chat_id}', exc, context={'chat_id': chat_id, 'song_title': current.title if current else None})
        return False

async def resume_stream(chat_id: int) -> bool:
    if not _pytgcalls or not _active.get(chat_id):
        _forget_call_state(chat_id)
        return False
    try:
        await asyncio.wait_for(_pytgcalls.resume(chat_id), timeout=_CONTROL_RPC_TIMEOUT)
        return True
    except Exception as exc:
        if _is_not_in_call(exc):
            LOGGER.info('resume_stream: not in call for %s — clearing state', chat_id)
            _forget_call_state(chat_id)
            return False
        current = get_current(chat_id)
        await send_error_log(f'resume_stream failed in {chat_id}', exc, context={'chat_id': chat_id, 'song_title': current.title if current else None})
        return False

async def skip_stream(chat_id: int):
    current = get_current(chat_id)
    if current:
        try:
            from melody.core.ytdl import cancel_download
            cancel_download(current.video_id, audio_only=not bool(getattr(current, 'video', False)), owner=chat_id)
        except Exception:
            pass
    _cancel_stale_download(chat_id, '__control_skip__', False)
    _resolving.pop(chat_id, None)
    _begin_generation(chat_id)
    await _play_next(chat_id)

async def stop_stream(chat_id: int):
    current = get_current(chat_id)
    if current:
        try:
            from melody.core.ytdl import cancel_download
            cancel_download(current.video_id, audio_only=not bool(getattr(current, 'video', False)), owner=chat_id)
        except Exception:
            pass
    _cancel_stale_download(chat_id, '__control_stop__', False)
    _resolving.pop(chat_id, None)
    _begin_generation(chat_id)
    _mark_leaving(chat_id)
    clear_queue(chat_id, persist=False)
    set_current(chat_id, None)
    _forget_call_state(chat_id)
    await _hard_leave(chat_id)
    from utils.playback_state import mark_inactive
    await mark_inactive(chat_id, clear=True)

async def recover_playback() -> int:
    from melody.core.queue import restore_snapshot
    from utils.playback_state import load_snapshots, mark_inactive
    recovered = 0
    for snapshot in await load_snapshots(active_only=True):
        chat_id = snapshot.get('chat_id')
        current = snapshot.get('current')
        if not chat_id or not current:
            continue
        try:
            restore_snapshot(snapshot)
            track = get_current(int(chat_id))
            if track is None:
                continue
            saved_position = max(0, int(snapshot.get('position', 0)))
            speed = max(0.5, min(2.0, float(snapshot.get('speed', 1.0))))
            _speed[int(chat_id)] = speed
            position_updated_at = int(snapshot.get('position_updated_at', snapshot.get('updated_at', time.time())))
            downtime = max(0, int(time.time()) - position_updated_at)
            position = saved_position + int(downtime * speed)
            if track.duration:
                position = min(position, max(track.duration - 2, 0))
            await _stream_track(int(chat_id), track, video=bool(snapshot.get('video', getattr(track, 'video', False))), start_at=position, priority=30)
            recovered += 1
        except Exception as exc:
            LOGGER.warning('Playback recovery failed for %s: %s', chat_id, exc)
            await mark_inactive(int(chat_id))
    return recovered

def get_playback_position(chat_id: int) -> int:
    if chat_id not in _play_start_time:
        return 0
    played = (time.time() - _play_start_time[chat_id]) * get_speed(chat_id)
    elapsed = _seek_offset.get(chat_id, 0) + int(played)
    track = get_current(chat_id)
    if track and track.duration:
        elapsed = min(elapsed, track.duration)
    return max(elapsed, 0)

async def seek_stream(chat_id: int, seconds: int, *, prefer_local: bool=False) -> int:
    track = get_current(chat_id)
    if not track or not _pytgcalls or (not _active.get(chat_id)):
        raise RuntimeError('Nothing is playing right now.')
    seconds = max(0, int(seconds))
    if track.duration:
        seconds = min(seconds, max(track.duration - 1, 0))
    from melody.core.ytdl import cached_file_path, download_audio
    video = _is_video.get(chat_id, False)
    local_path = cached_file_path(track.video_id, audio_only=not video) if prefer_local else None
    if prefer_local and (not local_path):
        local_path = await download_audio(track.video_id, audio_only=not video, allow_early=False)
    stream = None
    if local_path:
        stream = _local_media_stream(chat_id, local_path, video, seconds)
    else:
        stream = await _build_direct_stream(chat_id, track, video, seconds)
        if stream is None:
            local_path = await download_audio(track.video_id, audio_only=not video)
            stream = _local_media_stream(chat_id, local_path, video, seconds)
    _silence_playing.pop(chat_id, None)
    try:
        await _pytgcalls.play(chat_id, stream)
    except Exception as play_exc:
        if local_path is not None or not _is_probe_error(play_exc):
            raise
        LOGGER.warning('Seek: direct CDN stream unusable for %s in %s (%s) — downloading.', track.video_id, chat_id, type(play_exc).__name__)
        local_path = await download_audio(track.video_id, audio_only=not video)
        stream = _local_media_stream(chat_id, local_path, video, seconds)
        await _pytgcalls.play(chat_id, stream)
    _active[chat_id] = True
    _seek_offset[chat_id] = seconds
    _play_start_time[chat_id] = time.time()
    from utils.playback_state import save_snapshot
    await save_snapshot(chat_id, get_current(chat_id), get_queue(chat_id), get_loop(chat_id), get_volume(chat_id), active=True, position=seconds, video=video, speed=get_speed(chat_id))
    return seconds

async def change_volume(chat_id: int, volume: int, _retry: bool=False) -> bool:
    set_volume_local(chat_id, volume)
    try:
        await _pytgcalls.change_volume_call(chat_id, volume)
        return True
    except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, UserBannedInChannel) as exc:
        if not _retry and await _auto_join_assistant(chat_id):
            return await change_volume(chat_id, volume, _retry=True)
        current = get_current(chat_id)
        await send_error_log(f'change_volume failed in {chat_id}', exc, context={'chat_id': chat_id, 'song_title': current.title if current else None})
        return False
    except Exception as exc:
        if not _retry and 'GROUPCALL_FORBIDDEN' in str(exc) and await _auto_join_assistant(chat_id):
            return await change_volume(chat_id, volume, _retry=True)
        current = get_current(chat_id)
        await send_error_log(f'change_volume failed in {chat_id}', exc, context={'chat_id': chat_id, 'song_title': current.title if current else None})
        return False

async def set_playback_speed(chat_id: int, speed: float) -> bool:
    speed = round(float(speed), 2)
    if not 0.5 <= speed <= 2.0:
        return False
    if not get_current(chat_id) or not _pytgcalls or (not _active.get(chat_id)):
        return False
    position = get_playback_position(chat_id)
    _speed[chat_id] = speed
    try:
        await seek_stream(chat_id, position)
        return True
    except Exception as exc:
        if _is_not_in_call(exc):
            _forget_call_state(chat_id)
            return False
        await send_error_log(f'set_playback_speed failed in {chat_id}', exc, context={'chat_id': chat_id, 'speed': speed})
        return False

async def mute_stream(chat_id: int) -> bool:
    if not _pytgcalls or not _active.get(chat_id):
        return False
    try:
        await _pytgcalls.mute(chat_id)
        _muted[chat_id] = True
        return True
    except Exception as exc:
        if _is_not_in_call(exc):
            _forget_call_state(chat_id)
            return False
        await send_error_log(f'mute_stream failed in {chat_id}', exc, context={'chat_id': chat_id})
        return False

async def unmute_stream(chat_id: int) -> bool:
    if not _pytgcalls or not _active.get(chat_id):
        return False
    try:
        await _pytgcalls.unmute(chat_id)
        _muted.pop(chat_id, None)
        return True
    except Exception as exc:
        if _is_not_in_call(exc):
            _forget_call_state(chat_id)
            return False
        await send_error_log(f'unmute_stream failed in {chat_id}', exc, context={'chat_id': chat_id})
        return False

def is_muted(chat_id: int) -> bool:
    return bool(_muted.get(chat_id))

def is_active(chat_id: int) -> bool:
    return bool(_active.get(chat_id))

def is_video_active(chat_id: int) -> bool:
    return bool(_is_video.get(chat_id))

async def get_participants(chat_id: int) -> list:
    try:
        return await _pytgcalls.get_participants(chat_id)
    except Exception:
        return []
AUTO_LEAVE_AFTER = int(os.environ.get('AUTO_LEAVE_AFTER', '60') or 60)
_AUTO_LEAVE_POLL = 20
_empty_since: dict = {}

def _is_playing_track(chat_id: int) -> bool:
    if not _active.get(chat_id):
        return False
    if _silence_playing.get(chat_id):
        return False
    try:
        from melody.core.queue import get_current, get_queue
        return bool(get_current(chat_id) or get_queue(chat_id))
    except Exception:
        return True

async def _vc_is_empty(chat_id: int) -> bool:
    try:
        participants = await get_participants(chat_id)
    except Exception:
        return False
    if not participants:
        if _is_playing_track(chat_id):
            return False
        return bool(_active.get(chat_id))
    assistant_id = await _get_assistant_id()
    for participant in participants:
        pid = getattr(participant, 'user_id', None) or getattr(participant, 'id', None)
        if pid and pid != assistant_id:
            return False
    return True

async def auto_leave_watchdog() -> None:
    if AUTO_LEAVE_AFTER <= 0:
        LOGGER.info('Auto-leave disabled (AUTO_LEAVE_AFTER=0).')
        return
    while True:
        await asyncio.sleep(_AUTO_LEAVE_POLL)
        for chat_id in list(_active.keys()):
            try:
                if _is_leaving(chat_id):
                    _empty_since.pop(chat_id, None)
                    continue
                if _listener_mode.get(chat_id):
                    _empty_since.pop(chat_id, None)
                    continue
                if _is_playing_track(chat_id):
                    _empty_since.pop(chat_id, None)
                    continue
                idle = await _vc_is_empty(chat_id)
                if not idle:
                    _empty_since.pop(chat_id, None)
                    continue
                since = _empty_since.setdefault(chat_id, time.monotonic())
                if time.monotonic() - since < AUTO_LEAVE_AFTER:
                    continue
                _empty_since.pop(chat_id, None)
                LOGGER.debug('Auto-leaving empty voice chat %s', chat_id)
                await stop_stream(chat_id)
            except Exception as exc:
                LOGGER.warning('auto_leave_watchdog failed for %s: %s', chat_id, exc)
