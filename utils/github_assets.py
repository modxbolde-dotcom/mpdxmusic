import asyncio
import os
import pathlib
from melody.config import Config
from melody.logging import LOGGER
try:
    from utils.database import db
    _assets_col = db['assets']
except Exception:
    _assets_col = None
_ASSET_DB_TIMEOUT = 20.0

def _enabled() -> bool:
    return bool(Config.MONGO_DB_URI) and _assets_col is not None

async def push_to_github(local_path: str, gh_path: str, commit_message: str='') -> tuple[bool, str]:
    if not _enabled():
        return (False, 'MONGO_DB_URI not set — skipping persistent asset save.')
    try:
        data = await asyncio.to_thread(pathlib.Path(local_path).read_bytes)
    except OSError as e:
        return (False, f'Could not read local file: {e}')
    try:
        await asyncio.wait_for(_assets_col.update_one({'_id': gh_path}, {'$set': {'data': data, 'name': os.path.basename(local_path)}}, upsert=True), timeout=_ASSET_DB_TIMEOUT)
        return (True, 'Image saved to database ✅')
    except Exception as exc:
        LOGGER.warning('Could not save %s to MongoDB: %s', gh_path, exc)
        return (False, f'Database error: {exc}')

async def pull_from_github(local_path: str, gh_path: str) -> bool:
    if not _enabled():
        return False
    try:
        doc = await asyncio.wait_for(_assets_col.find_one({'_id': gh_path}), timeout=_ASSET_DB_TIMEOUT)
        if not doc or not doc.get('data'):
            return False
        os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
        await asyncio.to_thread(pathlib.Path(local_path).write_bytes, bytes(doc['data']))
        return True
    except Exception as exc:
        LOGGER.warning('Could not restore %s from MongoDB: %s', gh_path, exc)
        return False

async def restore_persistent_assets(assets: list[tuple[str, str]]) -> None:
    if not _enabled():
        return
    restored = 0
    for local_path, gh_path in assets:
        if os.path.exists(local_path):
            continue
        if await pull_from_github(local_path, gh_path):
            restored += 1
            LOGGER.info('Restored %s from database (%s)', local_path, gh_path)
    if restored:
        LOGGER.info('Restored %d persistent asset(s) from MongoDB.', restored)
