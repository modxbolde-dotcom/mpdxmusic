import os
from dotenv import load_dotenv
load_dotenv()

def _env_int(name: str, default: int=0) -> int:
    raw = (os.environ.get(name) or '').strip().strip('"').strip("'")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default

def _env_bool(name: str, default: bool=False) -> bool:
    raw = (os.environ.get(name) or '').strip().strip('"').strip("'").lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on', 'y')

def _env_str(name: str, default: str='') -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and (raw[0] in ("'", '"')):
        raw = raw[1:-1].strip()
    return raw or default

def _mongo_uri(name: str) -> str:
    value = _env_str(name)
    prefix = f'{name}='
    if value.startswith(prefix):
        value = value[len(prefix):].strip().strip('"\'')
    return value

def _validate_mongo_uri(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f'{name} is empty. Set the complete MongoDB URI beginning with mongodb:// or mongodb+srv:// in your deployment environment variables.')
    if not value.startswith(('mongodb://', 'mongodb+srv://')):
        raise RuntimeError(f'{name} is invalid. Paste only the complete MongoDB URI beginning with mongodb:// or mongodb+srv:// (not a label, dashboard URL, or placeholder).')
    hosts = value.split('@', 1)[-1].split('?', 1)[0]
    if any((not host.strip() for host in hosts.split(','))):
        raise RuntimeError(f'{name} contains an empty host or extra comma. Copy the complete Atlas connection string again without editing its host list.')
    return value

class Config:
    API_ID: int = _env_int('API_ID')
    API_HASH: str = _env_str('API_HASH')
    BOT_TOKEN: str = _env_str('BOT_TOKEN')
    MONGO_DB_URI: str = _mongo_uri('MONGO_DB_URI')
    BOT_NAME: str = _env_str('BOT_NAME', 'Music Bot')
    BOT_TAGLINE: str = _env_str('BOT_TAGLINE', 'High quality music, zero lag 🎧')
    OWNER_ID: int = _env_int('OWNER_ID')
    OWNER_NAME: str = _env_str('OWNER_NAME', 'Music Bot')
    LOG_GROUP_ID: int = _env_int('LOG_GROUP_ID')
    YT_COOKIES: str = _env_str('YT_COOKIES')
    YOUTUBE_API_KEY: str = _env_str('YOUTUBE_API_KEY') or _env_str('YT_API_KEY')
    GENIUS_API_TOKEN: str = _env_str('GENIUS_API_TOKEN')
    AUTOPLAY: bool = _env_bool('AUTOPLAY', True)
    MUSIC_ONLY_MODE: bool = _env_bool('MUSIC_ONLY_MODE', False)
    BOT_USERNAME: str = _env_str('BOT_USERNAME').lstrip('@')
    WELCOME_STICKER: str = _env_str('WELCOME_STICKER')
    MEMORY_LIMIT_MB: int = _env_int('MEMORY_LIMIT_MB', 1024 if os.getenv('DYNO') else 512)
    _LOW_MEMORY_PROFILE: bool = MEMORY_LIMIT_MB <= 768
    STARTUP_WARMUPS: bool = _env_bool('STARTUP_WARMUPS', False) and (not _LOW_MEMORY_PROFILE)
    BGUTIL_STARTUP_WARMUP: bool = _env_bool('BGUTIL_STARTUP_WARMUP', MEMORY_LIMIT_MB > 768) and (not _LOW_MEMORY_PROFILE)
    PLAYBACK_RECOVERY: bool = _env_bool('PLAYBACK_RECOVERY', False)
    GITHUB_TOKEN: str = _env_str('GITHUB_TOKEN')
    GITHUB_REPO: str = _env_str('GITHUB_REPO')
    GITHUB_REPO_USERNAME: str = _env_str('GITHUB_REPO_USERNAME')
    SONG_DUMP_CHAT_ID: int = _env_int('SONG_DUMP_CHAT_ID')
    COLORS = {'saffron': '#FF6600', 'gold': '#FFD700', 'green': '#009246', 'red': '#CE2B37', 'white': '#FFFFFF', 'dark': '#1A0500', 'overlay': 'rgba(0,0,0,0.55)'}
