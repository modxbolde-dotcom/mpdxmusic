import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def _clean(name: str, default: str='') -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and (raw[0] in ("'", '"')):
        raw = raw[1:-1].strip()
    return raw or default
BOT_NAME: str = _clean('BOT_NAME', 'Music Bot')
BOT_TAGLINE: str = _clean('BOT_TAGLINE', 'High quality music, zero lag 🎧')
__all__ = ['BOT_NAME', 'BOT_TAGLINE']
