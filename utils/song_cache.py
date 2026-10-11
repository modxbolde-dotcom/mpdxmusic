from __future__ import annotations
import asyncio
import json
import os
import sqlite3
import threading
import time
from typing import Any
try:
    from motor.motor_asyncio import AsyncIOMotorGridFSBucket
except Exception:
    AsyncIOMotorGridFSBucket = None
try:
    from utils.database import db
except Exception:
    db = None
song_cache_col = db['song_cache'] if db is not None else None
_gridfs_bucket = None
_gridfs_upload_lock = asyncio.Lock()

def _get_gridfs_bucket():
    global _gridfs_bucket
    if _gridfs_bucket is not None:
        return _gridfs_bucket
    if db is None or AsyncIOMotorGridFSBucket is None:
        return None
    try:
        _gridfs_bucket = AsyncIOMotorGridFSBucket(db, bucket_name='song_audio')
    except Exception:
        return None
    return _gridfs_bucket

def _tag(video: bool) -> str:
    return 'v' if video else 'a'

def _safe_video_id(video_id: str) -> str:
    value = str(video_id or '')
    return value if value and all((c.isalnum() or c in '_-' for c in value)) else 'unknown'

def _target(video_id: str, video: bool) -> str:
    tag = _tag(video)
    target = f'/tmp/melody_{video_id}_{tag}.cache'
    return target if _safe_video_id(video_id) == str(video_id) else f'/tmp/melody_{_safe_video_id(video_id)}_{tag}.cache'

async def remember_song(video_id: str, video: bool, file_id: str) -> None:
    if song_cache_col is None or not video_id or (not file_id):
        return
    try:
        await song_cache_col.update_one({'video_id': str(video_id), 'variant': _tag(video)}, {'$set': {'video_id': str(video_id), 'variant': _tag(video), 'file_id': str(file_id), 'updated_at': time.time()}}, upsert=True)
    except Exception:
        return

async def restore_song(client, video_id: str, video: bool) -> str | None:
    if song_cache_col is None or not client or (not video_id):
        return None
    target = _target(video_id, video)
    partial = f'{target}.restore.part'
    try:
        row = await song_cache_col.find_one({'video_id': str(video_id), 'variant': _tag(video)})
        file_id = (row or {}).get('file_id')
        gridfs_id = (row or {}).get('gridfs_id')
        if not file_id and (not gridfs_id):
            return None
        try:
            os.unlink(partial)
        except FileNotFoundError:
            pass
        if file_id:
            downloaded = await client.download_media(file_id, file_name=partial)
            if not downloaded or not os.path.isfile(partial):
                return None
        else:
            bucket = _get_gridfs_bucket()
            if bucket is None:
                return None
            stream = await bucket.open_download_stream(gridfs_id)
            with open(partial, 'wb') as handle:
                while True:
                    chunk = await stream.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
        if os.path.getsize(partial) <= 0:
            return None
        os.replace(partial, target)
        return target
    except Exception:
        try:
            os.unlink(partial)
        except OSError:
            pass
        return None

async def remember_completed_file(video_id: str, video: bool, filepath: str) -> bool:
    bucket = _get_gridfs_bucket()
    if bucket is None or song_cache_col is None or (not filepath) or (not os.path.isfile(filepath)):
        return False
    filename = f'melody_{_safe_video_id(video_id)}_{_tag(video)}.cache'
    upload = None
    lock = _gridfs_upload_lock
    try:
        async with lock:
            upload = bucket.open_upload_stream(filename, metadata={'video_id': str(video_id), 'variant': _tag(video)})
            with open(filepath, 'rb') as handle:
                while True:
                    chunk = await asyncio.to_thread(handle.read, 1024 * 1024)
                    if not chunk:
                        break
                    await upload.write(chunk)
            await upload.close()
            await song_cache_col.update_one({'video_id': str(video_id), 'variant': _tag(video)}, {'$set': {'video_id': str(video_id), 'variant': _tag(video), 'gridfs_id': upload._id, 'updated_at': time.time()}}, upsert=True)
        return True
    except Exception:
        if upload is not None:
            try:
                await upload.close()
            except Exception:
                pass
        return False

async def ensure_indexes() -> None:
    if song_cache_col is None:
        return
    try:
        await song_cache_col.create_index([('video_id', 1), ('variant', 1)], unique=True, background=True)
    except Exception:
        return
_META_DB_PATH = '/tmp/melody_meta_cache.sqlite3'
_meta_lock = threading.Lock()
_meta_conn: sqlite3.Connection | None = None

def _get_meta_conn() -> sqlite3.Connection:
    global _meta_conn
    if _meta_conn is None:
        _meta_conn = sqlite3.connect(_META_DB_PATH, check_same_thread=False)
        _meta_conn.execute('CREATE TABLE IF NOT EXISTS meta_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL)')
        _meta_conn.commit()
    return _meta_conn

def get_persistent_meta(key: str, ttl: float=259200.0) -> dict | None:
    if not key:
        return None
    try:
        with _meta_lock:
            row = _get_meta_conn().execute('SELECT data, ts FROM meta_cache WHERE key = ?', (key,)).fetchone()
        if not row:
            return None
        data_raw, ts = row
        if time.time() - ts > ttl:
            return None
        value = json.loads(data_raw)
        return value if isinstance(value, dict) else None
    except Exception:
        return None

def put_persistent_meta(key: str, data: dict[str, Any]) -> None:
    if not key or not data:
        return
    try:
        with _meta_lock:
            conn = _get_meta_conn()
            conn.execute('INSERT INTO meta_cache (key, data, ts) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET data = excluded.data, ts = excluded.ts', (key, json.dumps(data), time.time()))
            conn.commit()
    except Exception:
        return
__all__ = ['ensure_indexes', 'get_persistent_meta', 'put_persistent_meta', 'remember_completed_file', 'remember_song', 'restore_song', 'song_cache_col']
