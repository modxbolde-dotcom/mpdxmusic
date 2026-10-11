import os
from pyrogram import Client, enums
from melody.logging import LOGGER

def _memory_limit_mb() -> int:
    for name in ('MEMORY_LIMIT_MB', 'MEMORY_AVAILABLE_MB', 'DYNO_RAM_MB'):
        raw = (os.getenv(name) or '').strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                continue
            if value > 0:
                return value
    return 512
_CLIENT_MEMORY_LIMIT_MB = _memory_limit_mb()

def _worker_count(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, '') or 0)
    except ValueError:
        value = 0
    hard_max = default if _CLIENT_MEMORY_LIMIT_MB <= 768 else maximum
    return max(1, min(hard_max, value or default))
bot: 'Client | None' = None
assistant: 'Client | None' = None
ASSISTANT_READY: bool = True

def _build_assistant(session_string: str | None = None):
    from melody.config import Config
    kwargs = {'api_id': Config.API_ID, 'api_hash': Config.API_HASH, 'sleep_threshold': 15, 'workers': _worker_count('ASSISTANT_WORKERS', 2, 4), 'max_concurrent_transmissions': _worker_count('ASSISTANT_TRANSMISSIONS', 1, 2), 'parse_mode': enums.ParseMode.HTML}
    if session_string:
        kwargs['session_string'] = session_string
    else:
        kwargs['in_memory'] = True
    return Client('MusicBotAssistant', **kwargs)

def set_assistant_session(session_string: str):
    global assistant
    assistant = _build_assistant(session_string)
    return assistant

def create_clients(bot_session_string: str | None = None) -> tuple:
    global bot, assistant
    from melody.config import Config
    bot_kwargs = {'api_id': Config.API_ID, 'api_hash': Config.API_HASH, 'parse_mode': enums.ParseMode.HTML, 'sleep_threshold': 15, 'workers': _worker_count('BOT_WORKERS', 4, 8), 'max_concurrent_transmissions': _worker_count('BOT_TRANSMISSIONS', 1, 2), 'in_memory': True}
    if bot_session_string:
        # A durable bot session avoids a fresh Telegram authorization on every
        # Heroku dyno restart. The token is only needed for first bootstrap.
        bot_kwargs['session_string'] = bot_session_string
    else:
        bot_kwargs['bot_token'] = Config.BOT_TOKEN
    bot = Client('MusicBot', **bot_kwargs)
    # Assistant is authenticated later from MongoDB or the owner panel.
    assistant = None
    from utils.emoji_patch import apply_emoji_patch
    apply_emoji_patch()
    from utils.command_patch import apply_command_patch
    apply_command_patch()
    from utils.callback_patch import apply_callback_patch
    apply_callback_patch()
    _keep_background_tasks_alive()
    return (bot, assistant)
_BG_TASKS: set = set()

def _keep_background_tasks_alive() -> None:
    import asyncio
    if getattr(asyncio.create_task, '_melody_patched', False):
        return
    original = asyncio.create_task

    def create_task(coro, **kwargs):
        task = original(coro, **kwargs)
        _BG_TASKS.add(task)

        def _done(t):
            _BG_TASKS.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is None:
                return
            try:
                from melody.core.ytdl import is_download_cancelled
                if is_download_cancelled(exc):
                    return
            except Exception:
                pass
            LOGGER.error('Background task failed: %s', exc)
        task.add_done_callback(_done)
        return task
    create_task._melody_patched = True
    asyncio.create_task = create_task
