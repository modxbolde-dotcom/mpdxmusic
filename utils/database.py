import logging
import time
from urllib.parse import urlsplit
_SETTINGS_TTL = 45.0
_settings_cache: dict[tuple[int, str], tuple[float, object]] = {}
import motor.motor_asyncio
from melody.config import Config, _validate_mongo_uri
_mongo_uri = _validate_mongo_uri('MONGO_DB_URI', Config.MONGO_DB_URI)

def _mongo_target(uri: str) -> str:
    try:
        return urlsplit(uri).hostname or '<unknown>'
    except ValueError:
        return '<invalid>'
_MONGO_DATABASE = 'MusicBotDB'
logging.getLogger('MusicBot').info('Mongo runtime target: %s (database=%s)', _mongo_target(_mongo_uri), _MONGO_DATABASE)
client = motor.motor_asyncio.AsyncIOMotorClient(_mongo_uri, maxPoolSize=100, minPoolSize=8, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=15000, retryWrites=True, compressors='zlib')
db = client[_MONGO_DATABASE]
chats_col = db['chats']
users_col = db['users']
banned_col = db['banned']
gban_col = db['gban']
auth_col = db['auth']
history_col = db['history']
settings_col = db['settings']

async def add_chat(chat_id: int, title: str='', owner_id: int=None, owner_name: str=None):
    update = {'$set': {'chat_id': chat_id, 'title': title}}
    if owner_id is not None:
        update['$setOnInsert'] = {'owner_id': owner_id, 'owner_name': owner_name or ''}
    await chats_col.update_one({'chat_id': chat_id}, update, upsert=True)

async def get_all_chats() -> list:
    return await chats_col.find({}, {'_id': 0, 'chat_id': 1, 'title': 1}).to_list(None)

async def get_chats_page(limit: int=30, offset: int=0) -> list:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    cursor = chats_col.find({}, {'_id': 0, 'chat_id': 1, 'title': 1}).sort('chat_id', 1).skip(offset).limit(limit)
    return await cursor.to_list(length=limit)

async def get_chat_count() -> int:
    return int(await chats_col.count_documents({}))

async def add_user(user_id: int, name: str='', username: str=''):
    await users_col.update_one({'user_id': int(user_id)}, {'$set': {'user_id': int(user_id), 'name': name, 'username': username}}, upsert=True)

async def get_all_users() -> list:
    return await users_col.find({}, {'_id': 0, 'user_id': 1}).to_list(None)

async def get_user_count() -> int:
    return int(await users_col.count_documents({}))

async def remove_user(user_id: int):
    await users_col.delete_one({'user_id': int(user_id)})

async def get_chat_owner(chat_id: int) -> 'dict | None':
    doc = await chats_col.find_one({'chat_id': chat_id}, {'_id': 0, 'owner_id': 1, 'owner_name': 1})
    if doc and doc.get('owner_id'):
        return doc
    return None
_auth_cache: dict = {}
_AUTH_TTL = 120

def invalidate_auth_cache(chat_id: int | None=None) -> None:
    if chat_id is None:
        _auth_cache.clear()
    else:
        _auth_cache.pop(chat_id, None)

async def auth_user(chat_id: int, user_id: int):
    await auth_col.update_one({'chat_id': chat_id}, {'$addToSet': {'users': user_id}}, upsert=True)
    invalidate_auth_cache(chat_id)

async def unauth_user(chat_id: int, user_id: int):
    await auth_col.update_one({'chat_id': chat_id}, {'$pull': {'users': user_id}})
    invalidate_auth_cache(chat_id)

async def get_auth_users(chat_id: int) -> list:
    cached = _auth_cache.get(chat_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    doc = await auth_col.find_one({'chat_id': chat_id})
    users = doc.get('users', []) if doc else []
    _auth_cache[chat_id] = (time.monotonic() + _AUTH_TTL, users)
    return users

async def ban_user(user_id: int, reason: str=''):
    await banned_col.update_one({'user_id': user_id}, {'$set': {'user_id': user_id, 'reason': reason}}, upsert=True)

async def unban_user(user_id: int):
    await banned_col.delete_one({'user_id': user_id})

async def is_banned(user_id: int) -> bool:
    return bool(await banned_col.find_one({'user_id': user_id}))

async def gban_user(user_id: int, reason: str=''):
    await gban_col.update_one({'user_id': user_id}, {'$set': {'user_id': user_id, 'reason': reason}}, upsert=True)

async def ungban_user(user_id: int):
    await gban_col.delete_one({'user_id': user_id})

async def is_gbanned(user_id: int) -> bool:
    return bool(await gban_col.find_one({'user_id': user_id}))

async def add_history(chat_id: int, video_id: str, title: str=''):
    await history_col.update_one({'chat_id': chat_id}, {'$push': {'history': {'$each': [{'id': video_id, 'title': title}], '$slice': -15}}}, upsert=True)

async def get_history(chat_id: int) -> list:
    doc = await history_col.find_one({'chat_id': chat_id})
    return doc.get('history', []) if doc else []

async def get_setting(chat_id: int, key: str, default=None):
    cache_key = (int(chat_id), str(key))
    cached = _settings_cache.get(cache_key)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]
    doc = await settings_col.find_one({'chat_id': chat_id})
    value = doc.get(key, default) if doc else default
    _settings_cache[cache_key] = (now + _SETTINGS_TTL, value)
    return value

async def set_setting(chat_id: int, key: str, value):
    await settings_col.update_one({'chat_id': chat_id}, {'$set': {key: value}}, upsert=True)
    _settings_cache[int(chat_id), str(key)] = (time.monotonic() + _SETTINGS_TTL, value)

async def get_stats() -> dict:
    total_chats = await chats_col.count_documents({})
    total_banned = await banned_col.count_documents({})
    total_gbanned = await gban_col.count_documents({})
    return {'chats': total_chats, 'banned': total_banned, 'gbanned': total_gbanned}
blacklist_col = db['blacklist_chats']
globals_col = db['globals']

async def get_gbanned_users() -> list:
    return await gban_col.find({}, {'_id': 0, 'user_id': 1, 'reason': 1}).to_list(None)

async def get_banned_users() -> list:
    return await banned_col.find({}, {'_id': 0, 'user_id': 1, 'reason': 1}).to_list(None)

async def blacklist_chat(chat_id: int, reason: str=''):
    await blacklist_col.update_one({'chat_id': chat_id}, {'$set': {'chat_id': chat_id, 'reason': reason}}, upsert=True)

async def whitelist_chat(chat_id: int):
    await blacklist_col.delete_one({'chat_id': chat_id})

async def is_blacklisted_chat(chat_id: int) -> bool:
    return bool(await blacklist_col.find_one({'chat_id': chat_id}))

async def get_blacklisted_chats() -> list:
    return await blacklist_col.find({}, {'_id': 0, 'chat_id': 1, 'reason': 1}).to_list(None)

async def get_global(key: str, default=None):
    doc = await globals_col.find_one({'_id': 'config'})
    if doc:
        return doc.get(key, default)
    return default

async def set_global(key: str, value):
    await globals_col.update_one({'_id': 'config'}, {'$set': {key: value}}, upsert=True)
