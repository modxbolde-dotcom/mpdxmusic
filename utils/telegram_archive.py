from __future__ import annotations
import asyncio
import os
import re
import sqlite3
import time
from typing import Any
_DB_PATH = '/tmp/melody_telegram_archive.sqlite3'
_LOCK = asyncio.Lock()

def archive_channel_id() -> int | None:
    raw = (os.getenv('MUSIC_ARCHIVE_CHANNEL_ID') or os.getenv('LOG_GROUP_ID') or '').strip().strip('"\'')
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value < 0 else None

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.execute('CREATE TABLE IF NOT EXISTS archive (video_id TEXT NOT NULL, variant TEXT NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, file_id TEXT, updated REAL NOT NULL, PRIMARY KEY(video_id, variant))')
    return conn

def _tag(video_id: str, video: bool) -> str:
    return f"[melody:{video_id}:{('v' if video else 'a')}]"

def _save(video_id: str, video: bool, chat_id: int, message_id: int, file_id: str='') -> None:
    try:
        with _db() as conn:
            conn.execute('INSERT INTO archive(video_id,variant,chat_id,message_id,file_id,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(video_id,variant) DO UPDATE SET chat_id=excluded.chat_id,message_id=excluded.message_id,file_id=excluded.file_id,updated=excluded.updated', (video_id, 'v' if video else 'a', chat_id, message_id, file_id or '', time.time()))
    except Exception:
        pass

def _load(video_id: str, video: bool) -> tuple[int, int, str] | None:
    try:
        with _db() as conn:
            row = conn.execute('SELECT chat_id,message_id,file_id FROM archive WHERE video_id=? AND variant=?', (video_id, 'v' if video else 'a')).fetchone()
        return (int(row[0]), int(row[1]), row[2] or '') if row else None
    except Exception:
        return None

def has_archive(video_id: str, video: bool=False) -> bool:
    return _load(video_id, video) is not None

def _media_file_id(message: Any, video: bool) -> str:
    obj = getattr(message, 'video', None) if video else getattr(message, 'audio', None)
    if obj is None:
        obj = getattr(message, 'document', None)
    return str(getattr(obj, 'file_id', '') or '')

def _safe_title(title: str) -> str:
    return re.sub('[\\r\\n]+', ' ', (title or 'Melody')[:80])

async def archive_completed_file(client, video_id: str, title: str, filepath: str, *, video: bool=False) -> bool:
    if not client or not video_id or (not filepath) or (not os.path.isfile(filepath)):
        return False
    if any((x in os.path.basename(filepath).lower() for x in ('.part', '.temp', '.ytdl', '.early'))):
        return False
    channel_id = archive_channel_id()
    if channel_id is None:
        return False
    if _load(video_id, video):
        return True
    caption = f'{_tag(video_id, video)}\n🎵 {_safe_title(title)}'
    async with _LOCK:
        if _load(video_id, video):
            return True
        try:
            if video:
                message = await client.send_video(channel_id, video=filepath, caption=caption, supports_streaming=True)
            else:
                # Keep normal /play files as Telegram audio. If Telegram rejects
                # the codec metadata, retain a document fallback instead of
                # losing the archive.
                try:
                    message = await client.send_audio(channel_id, audio=filepath, caption=caption, title=_safe_title(title), performer='MusicBot')
                except Exception:
                    message = await client.send_document(channel_id, document=filepath, caption=caption)
            _save(video_id, video, channel_id, int(message.id), _media_file_id(message, video))
            return True
        except Exception:
            return False

async def restore_archived_file(client, video_id: str, video: bool=False) -> str | None:
    if not client or not video_id:
        return None
    channel_id = archive_channel_id()
    if channel_id is None:
        return None
    ref = _load(video_id, video)
    try:
        message = None
        if ref:
            ref_chat, message_id, file_id = ref
            message = await client.get_messages(ref_chat, message_id)
        if message is None:
            needle = _tag(video_id, video)
            async for candidate in client.search_messages(channel_id, query=needle, limit=1):
                message = candidate
                break
        if not message:
            return None
        _save(video_id, video, channel_id, int(message.id), _media_file_id(message, video))
        # Audio and video can be consumed by ffmpeg through the local Telegram range proxy.
        # This avoids downloading the whole archived file into the Heroku dyno.
        if os.getenv('ARCHIVE_REMOTE_PLAY', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}:
            media = getattr(message, 'audio', None) or getattr(message, 'document', None)
            size = int(getattr(media, 'file_size', 0) or 0)
            filename = getattr(media, 'file_name', None) or f'{video_id}.audio'
            if size > 0:
                try:
                    from utils.tg_media_proxy import create_media_proxy
                    proxy_url = await create_media_proxy(client, message, size=size, filename=filename)
                    return proxy_url
                except Exception:
                    pass
        file_id = _media_file_id(message, video)
        if file_id:
            path = await client.download_media(file_id, file_name=f'/tmp/melody_archive_{video_id}_{("v" if video else "a")}')
        else:
            path = await client.download_media(message, file_name=f'/tmp/melody_archive_{video_id}_{("v" if video else "a")}')
        return path if path and os.path.isfile(path) and (os.path.getsize(path) > 0) else None
    except Exception:
        return None

def forget_archive(video_id: str, video: bool=False) -> None:
    try:
        with _db() as conn:
            conn.execute('DELETE FROM archive WHERE video_id=? AND variant=?', (video_id, 'v' if video else 'a'))
    except Exception:
        pass
__all__ = ['archive_channel_id', 'archive_completed_file', 'restore_archived_file', 'forget_archive', 'has_archive']
