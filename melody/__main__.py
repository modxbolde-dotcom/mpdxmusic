from utils.branding import BOT_NAME
import asyncio
import importlib
import pkgutil
import platform
import os
import uvloop
from melody.logging import LOGGER
from melody.config import Config
from utils.database import get_all_chats
from utils.tasks import spawn

def _patch_pyrogram_peer_errors():
    try:
        import pyrogram.client as _pyro_client
        _original_handle_updates = _pyro_client.Client.handle_updates

        async def _safe_handle_updates(self, updates):
            try:
                await _original_handle_updates(self, updates)
            except ValueError as exc:
                if 'Peer id invalid' in str(exc):
                    pass
                else:
                    raise
        _pyro_client.Client.handle_updates = _safe_handle_updates
        LOGGER.debug('Pyrogram handle_updates patched to suppress peer-id errors.')
    except Exception as exc:
        LOGGER.warning('Could not patch Pyrogram handle_updates: %s', exc)

def validate_config():
    required = ['API_ID', 'API_HASH', 'BOT_TOKEN', 'MONGO_DB_URI']
    missing = [k for k in required if not getattr(Config, k, None)]
    if missing:
        LOGGER.critical('Missing required env vars: %s', ', '.join(missing))
        raise SystemExit(1)

def load_plugins():
    import melody.plugins
    loaded = 0
    failed = 0
    failed_names = []
    music_only = bool(getattr(Config, 'MUSIC_ONLY_MODE', False))
    allowed_prefix = 'melody.plugins.music.'
    for _finder, module_name, is_pkg in pkgutil.walk_packages(path=melody.plugins.__path__, prefix=melody.plugins.__name__ + '.', onerror=lambda name: LOGGER.warning('Plugin walk error: %s', name)):
        if is_pkg:
            continue
        if music_only and (not module_name.startswith(allowed_prefix)):
            continue
        try:
            importlib.import_module(module_name)
            LOGGER.debug('Loaded plugin: %s', module_name)
            loaded += 1
        except Exception as exc:
            LOGGER.error('Failed to load plugin %s: %s', module_name, exc)
            failed += 1
            failed_names.append(module_name.split('.')[-1])
    LOGGER.info('Plugins loaded: %d OK, %d failed%s', loaded, failed, ' (music-only profile)' if music_only else '')
    return (loaded, failed, failed_names)

async def warm_peer_cache(client, label: str):
    try:
        count = 0
        async for _ in client.get_dialogs():
            count += 1
        LOGGER.info('%s: warmed peer cache for %d chats', label, count)
    except Exception as exc:
        LOGGER.warning('%s: could not warm peer cache: %s', label, exc)

async def warm_bot_peer_cache(bot):
    chats = None
    for attempt in range(1, 4):
        try:
            chats = await get_all_chats()
            break
        except Exception as exc:
            LOGGER.warning('bot: could not load chats from DB to warm cache (attempt %d/3): %s', attempt, exc)
            if attempt < 3:
                await asyncio.sleep(5 * attempt)
    if chats is None:
        LOGGER.warning("bot: giving up warming peer cache — DB unreachable after 3 attempts. Commands in groups the bot hasn't seen yet this run may not respond until MongoDB connectivity is restored.")
        return
    warmed = 0
    for chat in chats:
        try:
            await bot.resolve_peer(chat['chat_id'])
            warmed += 1
        except Exception:
            pass
    LOGGER.info('bot: warmed peer cache for %d/%d known chats', warmed, len(chats))

async def warm_recovery_peers(assistant):
    try:
        from utils.playback_state import load_snapshots
        snapshots = await load_snapshots(active_only=True)
    except Exception as exc:
        LOGGER.debug('Could not load snapshots to warm recovery peers: %s', exc)
        return
    for snapshot in snapshots:
        chat_id = snapshot.get('chat_id')
        if not chat_id:
            continue
        try:
            await assistant.resolve_peer(int(chat_id))
        except Exception:
            pass

async def register_slash_commands(bot):
    from pyrogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault
    group_commands = [BotCommand('play', '▶️ Play a song from YouTube'), BotCommand('vplay', '📹 Play a video stream'), BotCommand('pause', '⏸ Pause playback'), BotCommand('resume', '▶️ Resume playback'), BotCommand('skip', '⏭ Skip current song'), BotCommand('stop', '⏹ Stop music & clear queue'), BotCommand('end', '⏹ Stop music & clear queue'), BotCommand('queue', '📋 Show current queue'), BotCommand('np', '🎵 Now playing info'), BotCommand('volume', '🔊 Set volume (1-200)'), BotCommand('mute', '🔇 Mute playback'), BotCommand('unmute', '🔊 Unmute playback'), BotCommand('loop', '🔂 Loop current song'), BotCommand('loopall', '🔁 Loop entire queue'), BotCommand('noloop', '➡️ Disable loop'), BotCommand('shuffle', '🔀 Shuffle queue'), BotCommand('clearqueue', '🗑 Clear the queue'), BotCommand('remove', '❌ Remove song from queue'), BotCommand('playforce', '⚡ Force-play now (skips queue)'), BotCommand('vplayforce', '⚡ Force-play video now'), BotCommand('playlist', '📃 Queue an entire playlist'), BotCommand('seek', '⏩ Seek to position (seconds)'), BotCommand('seekback', '⏪ Seek backward (seconds)'), BotCommand('speed', '⚡ Set playback speed (0.5-2.0)'), BotCommand('search', '🔍 Search YouTube'), BotCommand('dl', '⬇️ Download song/video as file'), BotCommand('autoplay', '🤖 Toggle autoplay'), BotCommand('ping', '🏓 Check bot latency'), BotCommand('stats', '📊 Bot statistics'), BotCommand('help', '📖 Help menu'), BotCommand('playmode', '🎛 Access and search mode'), BotCommand('queuepos', '🔢 Show queue position'), BotCommand('addplaylist', '➕ Save a playlist'), BotCommand('myplaylist', '📃 Show saved playlists'), BotCommand('delplaylist', '🗑 Delete a saved playlist'), BotCommand('playplaylist', '▶️ Play a saved playlist'), BotCommand('live', '📻 Play a live stream'), BotCommand('radio', '📻 Play radio stream'), BotCommand('replay', '🔄 Replay current stream'), BotCommand('channelplay', '📺 Link channel voice chat')]
    channel_commands = [BotCommand('cplay', "▶️ Play in this channel's voice chat"), BotCommand('cvplay', "🎬 Play video in this channel's voice chat"), BotCommand('cpause', '⏸ Pause (channel)'), BotCommand('cresume', '▶️ Resume (channel)'), BotCommand('cskip', '⏭ Skip (channel)'), BotCommand('cstop', '⏹ Stop (channel)'), BotCommand('cend', '⏹ Stop (channel)'), BotCommand('cqueue', '📋 Channel queue'), BotCommand('cnp', '🎵 Channel now playing'), BotCommand('cseek', '⏩ Seek channel stream'), BotCommand('cseekback', '⏪ Rewind channel stream'), BotCommand('cspeed', '⚡ Channel playback speed'), BotCommand('cvolume', '🔊 Channel volume'), BotCommand('cmute', '🔇 Mute channel stream'), BotCommand('cunmute', '🔊 Unmute channel stream'), BotCommand('cshuffle', '🔀 Shuffle channel queue'), BotCommand('cloop', '🔂 Loop channel song'), BotCommand('cloopall', '🔁 Loop channel queue'), BotCommand('cnoloop', '➡️ Disable channel loop'), BotCommand('help', '📖 Help menu')]
    private_commands = [BotCommand('start', f'🎶 Start {BOT_NAME}'), BotCommand('help', '📖 Help & command list'), BotCommand('ping', '🏓 Check bot latency'), BotCommand('stats', '📊 Bot statistics'), BotCommand('about', f'ℹ️ About {BOT_NAME}'), BotCommand('alive', '💚 Check uptime')]
    owner_commands = [BotCommand('panel', '🛠 Owner control panel'), BotCommand('logs', '📜 View bot logs'), BotCommand('chatlist', '👥 List stored chats'), BotCommand('maintenance', '🔧 Maintenance tools'), BotCommand('restart', '🔄 Restart bot'), BotCommand('reboot', '♻️ Reboot runtime'), BotCommand('update', '⬆️ Update bot code'), BotCommand('eval', '🧪 Run owner evaluation'), BotCommand('shell', '💻 Run owner shell'), BotCommand('sysinfo', '📊 System information'), BotCommand('speedtest', '🌐 Network speed test'), BotCommand('logger', '📝 Logging controls'), BotCommand('emojiid', '🙂 Inspect custom emoji ID'), BotCommand('setpic', '🖼 Set start picture'), BotCommand('delpic', '🗑 Delete start picture'), BotCommand('setwelcomepic', '🖼 Set welcome picture'), BotCommand('delwelcomepic', '🗑 Delete welcome picture'), BotCommand('setsource', '🔗 Set source link'), BotCommand('ocmds', '📚 Owner command vault')]
    if Config.MUSIC_ONLY_MODE:
        music_commands = {'play', 'vplay', 'pause', 'resume', 'skip', 'stop', 'end', 'queue', 'np', 'volume', 'mute', 'unmute', 'loop', 'loopall', 'noloop', 'shuffle', 'clearqueue', 'remove', 'playlist', 'seek', 'seekback', 'speed', 'search', 'dl', 'autoplay'}
        group_commands = [command for command in group_commands if command.command in music_commands]
        channel_commands = [command for command in channel_commands if command.command in {'cplay', 'cvplay', 'cpause', 'cresume', 'cskip', 'cstop', 'cend'}]
        private_commands = []
    try:
        await bot.set_bot_commands(group_commands[:100], scope=BotCommandScopeAllGroupChats())
        await bot.set_bot_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
        if Config.OWNER_ID:
            await bot.set_bot_commands(private_commands + owner_commands, scope=BotCommandScopeChat(int(Config.OWNER_ID)))
        await bot.set_bot_commands(channel_commands, scope=BotCommandScopeDefault())
        LOGGER.info('Slash commands registered: %d group, %d private, %d owner, %d channel', len(group_commands), len(private_commands), len(owner_commands), len(channel_commands))
    except Exception as exc:
        LOGGER.warning('Could not register slash commands: %s', exc)

async def sync_log_group_peer(bot, assistant):
    if not Config.LOG_GROUP_ID:
        return
    try:
        from pyrogram.raw.types import InputPeerChannel
        peer = await assistant.resolve_peer(Config.LOG_GROUP_ID)
        if isinstance(peer, InputPeerChannel):
            base = (Config.LOG_GROUP_ID, peer.access_hash, 'channel')
            last_exc = None
            for extra in ((None, None), (None,), ()):
                try:
                    await bot.storage.update_peers([base + extra])
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
            LOGGER.info('bot: synced LOG_GROUP_ID peer (access_hash injected) from assistant cache')
        else:
            LOGGER.debug('bot: log group is a plain group — no peer injection needed')
    except Exception as exc:
        LOGGER.warning('bot: could not sync log group peer from assistant: %s', exc)

async def send_startup_log(bot, assistant, loaded: int, failed: int, failed_names: list):
    if not Config.LOG_GROUP_ID and (not Config.OWNER_ID):
        return
    try:
        me = await bot.get_me()
        py_ver = platform.python_version()
        import pyrogram
        pyrogram_ver = pyrogram.__version__
        status_line = f'✅ {loaded} plugins OK' + (f", ❌ {failed} failed: {'  '.join(failed_names)}" if failed else '')
        text = f'╔══════════════════════════════╗\n║   🎶  <b>MELODY IS LIVE!</b>     ║\n╚══════════════════════════════╝\n\n🤖 <b>Bot:</b> @{me.username} (<code>{me.id}</code>)\n🐍 <b>Python:</b> <code>{py_ver}</code>\n📦 <b>Pyrogram:</b> <code>{pyrogram_ver}</code>\n\n🔌 <b>Plugins:</b> {status_line}\n🌐 <b>Platform:</b> <code>{platform.system()} {platform.release()}</code>\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ All systems operational. Ready to receive commands!'
        if Config.LOG_GROUP_ID:
            try:
                await assistant.send_message(Config.LOG_GROUP_ID, text)
                LOGGER.info('Startup log sent via assistant → LOG_GROUP_ID')
                return
            except Exception as e1:
                LOGGER.debug('assistant→LOG_GROUP_ID failed: %s', e1)
        if Config.LOG_GROUP_ID:
            try:
                await bot.send_message(Config.LOG_GROUP_ID, text)
                LOGGER.info('Startup log sent via bot → LOG_GROUP_ID')
                return
            except Exception as e2:
                LOGGER.debug('bot→LOG_GROUP_ID failed: %s', e2)
        if Config.OWNER_ID:
            try:
                owner_note = ''
                if Config.LOG_GROUP_ID:
                    owner_note = f'\n\n⚠️ <b>Note:</b> Startup log sent to your DM because neither bot nor assistant could send to LOG_GROUP_ID <code>{Config.LOG_GROUP_ID}</code>. Check that the bot/assistant is a member of that channel.'
                await bot.send_message(Config.OWNER_ID, text + owner_note)
                LOGGER.info('Startup log sent via bot → OWNER_ID DM (LOG_GROUP_ID unreachable)')
                return
            except Exception as e3:
                LOGGER.warning('Startup log could not be delivered anywhere. LOG_GROUP_ID error: see debug logs. OWNER_ID DM error: %s', e3)
    except Exception as exc:
        LOGGER.warning('Could not send startup log: %s', exc)

async def main():
    validate_config()
    try:
        from utils.ffmpeg_runtime import ensure_ffmpeg_runtime
        await asyncio.to_thread(ensure_ffmpeg_runtime)
    except Exception as exc:
        LOGGER.warning('ffmpeg runtime check failed: %s', exc)
    try:
        from utils.github_assets import restore_persistent_assets
        import os as _os
        _assets_dir = _os.path.join(_os.path.dirname(__file__), '..', 'assets')
        await restore_persistent_assets([(_os.path.join(_assets_dir, 'bg_start.png'), 'assets/bg_start.png'), (_os.path.join(_assets_dir, 'bg_welcome.png'), 'assets/bg_welcome.png')])
    except Exception as exc:
        LOGGER.warning('Could not restore persistent assets from GitHub: %s', exc)
    _patch_pyrogram_peer_errors()
    from utils.chat_cache import install_chat_cache
    install_chat_cache()
    from utils.database import get_global
    saved_bot_session = await get_global('bot_session', '')
    from melody import create_clients
    create_clients(saved_bot_session or None)
    if saved_bot_session:
        LOGGER.info('Saved bot session found; skipping fresh bot authorization.')
    # Restore a panel-authenticated assistant session from MongoDB before
    # importing plugins, so modules receive the live assistant object.
    from utils.assistant_auth import load_saved_session
    saved_assistant_session = await load_saved_session()
    if saved_assistant_session:
        from melody import set_assistant_session
        set_assistant_session(saved_assistant_session)
        LOGGER.info('Saved assistant session found; restoring it.')
    else:
        LOGGER.info('No assistant session saved yet; owner panel login is available.')
    try:
        saved_youtube_cookies = await get_global('youtube_cookies', '')
        if saved_youtube_cookies:
            from melody.core.ytdl import cookie_pool_snapshot, set_runtime_cookies
            cookie_items = [saved_youtube_cookies] if isinstance(saved_youtube_cookies, str) else list(saved_youtube_cookies)
            restored = 0
            for cookie_item in cookie_items:
                if set_runtime_cookies(cookie_item, append=True):
                    restored += 1
            # Persist the normalized pool so exported cookies are removed only
            # after their real expiry, while valid cookies survive restarts.
            from utils.database import set_global
            await set_global('youtube_cookies', cookie_pool_snapshot())
            if restored:
                LOGGER.info('Saved YouTube cookie pool restored from MongoDB (%d file(s)).', restored)
            else:
                LOGGER.warning('Saved YouTube cookies were invalid; use Owner Panel → Add YouTube Cookies.')
    except Exception as exc:
        LOGGER.warning('Could not restore saved YouTube cookies: %s', exc)
    LOGGER.info('Pyrogram clients created.')
    loaded, failed, failed_names = load_plugins()
    LOGGER.info('All plugins loaded.')
    from melody import bot, assistant
    from melody.core.call import recover_playback, start_call_py
    LOGGER.info(f'Starting {BOT_NAME}...')
    # Telegram can temporarily rate-limit bot authorization after repeated
    # restarts. Sleep for Telegram's requested duration instead of crashing the
    # Heroku worker and creating another restart loop.
    from pyrogram.errors import FloodWait
    while True:
        try:
            await bot.start()
            break
        except FloodWait as exc:
            wait_for = max(1, int(getattr(exc, 'value', 60)) + 5)
            LOGGER.warning('Bot authorization is rate-limited; retrying in %ss.', wait_for)
            await asyncio.sleep(wait_for)
    LOGGER.info('Bot client started.')
    try:
        bot_session = await bot.export_session_string()
        if bot_session:
            await get_global('bot_session', '')
            from utils.database import set_global
            await set_global('bot_session', bot_session)
            LOGGER.info('Bot session persisted in MongoDB for restart-safe authorization.')
    except Exception as exc:
        LOGGER.warning('Could not persist bot session; next Heroku restart may authorize again: %s', type(exc).__name__)
    import melody as _melody_pkg
    assistant_ok = bool(assistant and saved_assistant_session)
    try:
        if not assistant_ok:
            raise RuntimeError('No assistant session configured; use Owner Panel → Assistant Login')
        await assistant.start()
        LOGGER.info('Assistant client started.')
    except Exception as exc:
        assistant_ok = False
        name = type(exc).__name__
        if name in {'AuthKeyDuplicated', 'AuthKeyUnregistered', 'AuthKeyInvalid', 'SessionRevoked', 'SessionExpired', 'UserDeactivated', 'UserDeactivatedBan'}:
            LOGGER.error('❌ ASSISTANT SESSION INVALID (%s).\n   Owner panel me Assistant Login se phone/OTP ya session login dobara karo.\n   Bot text commands ke liye chalu rahega, lekin voice chat (/play) tab tak kaam nahi karega.', name)
        else:
            LOGGER.error('❌ Assistant client start failed: %s: %s', name, exc)
    _melody_pkg.ASSISTANT_READY = assistant_ok
    if assistant_ok:
        try:
            await start_call_py()
            LOGGER.info('PyTgCalls started.')
        except Exception as exc:
            assistant_ok = False
            _melody_pkg.ASSISTANT_READY = False
            LOGGER.error('PyTgCalls failed to start: %s', exc)
    else:
        LOGGER.warning('⚠️ PyTgCalls skipped — assistant session invalid. Bot online hai, par VC playback disabled.')
    if assistant_ok:
        try:
            from utils.playback_state import ensure_indexes as ensure_playback_indexes
            await ensure_playback_indexes()
        except Exception as exc:
            LOGGER.warning('Playback-state index setup skipped: %s', exc)

        async def _recover_in_background():
            try:
                await warm_recovery_peers(assistant)
                recovered = await recover_playback()
                LOGGER.info('Recovered playback in %d chat(s).', recovered)
            except Exception as exc:
                LOGGER.warning('Playback-state recovery skipped: %s', exc)
        if Config.PLAYBACK_RECOVERY:
            spawn(_recover_in_background())
        else:
            LOGGER.info('Playback recovery disabled by default; fresh /play owns playback.')
    if not Config.MUSIC_ONLY_MODE:
        from utils.emoji_map import resolve_emoji_map

        async def _resolve_emoji_map_bg():
            try:
                await resolve_emoji_map(bot)
            except Exception as exc:
                LOGGER.warning('Premium emoji map resolution failed: %s', exc)
        spawn(_resolve_emoji_map_bg())
        try:
            from utils import emoji_search
            emoji_search.start(assistant if assistant_ok else bot)
        except Exception as exc:
            LOGGER.warning('Universal premium-emoji resolver failed to start: %s', exc)
    # Loading every dialog/chat into Pyrogram's in-memory peer cache can exceed a
    # 512 MB dyno before the first command. Resolve peers lazily on small plans.
    warm_peers = (not Config._LOW_MEMORY_PROFILE) or Config.STARTUP_WARMUPS or (
        (os.getenv('WARM_PEER_CACHE', 'false') or '').lower() in {'1', 'true', 'yes', 'on'}
    )
    assistant_warm_task = spawn(warm_peer_cache(assistant, 'assistant'), name='assistant-peer-warm') if assistant_ok and warm_peers else None
    if warm_peers:
        spawn(warm_bot_peer_cache(bot), name='bot-peer-warm')
    else:
        LOGGER.info('Low-memory mode: Telegram peer-cache warm-up disabled; peers resolve lazily.')
    try:
        from utils.song_cache import ensure_indexes as ensure_song_cache_indexes
        await ensure_song_cache_indexes()
    except Exception as exc:
        LOGGER.warning('Song-cache index build skipped: %s', exc)
    if not Config._LOW_MEMORY_PROFILE or Config.STARTUP_WARMUPS:
        from melody.core.ytdl import warm_up_bgutil_server
        try:
            await warm_up_bgutil_server(timeout=15.0)
        except Exception as exc:
            LOGGER.warning('bgutil startup warmup skipped: %s', exc)
    if Config.STARTUP_WARMUPS:
        from melody.core.ytdl import warm_popular_metadata
        spawn(warm_popular_metadata())
    if not Config._LOW_MEMORY_PROFILE or Config.STARTUP_WARMUPS:

        async def _warm_direct_resolver() -> None:
            try:
                from melody.core.ytdl import resolve_stream_urls
                await resolve_stream_urls('dQw4w9WgXcQ', force=True)
                LOGGER.info('✅ direct-CDN resolver pre-warmed (first /play stays fast)')
            except Exception as exc:
                LOGGER.info('direct-CDN resolver warmup skipped: %s', exc)
        spawn(_warm_direct_resolver())
    from utils.memguard import memory_guard
    spawn(memory_guard())
    from utils.diskguard import disk_guard
    spawn(disk_guard())
    from melody.core.call import auto_leave_watchdog
    spawn(auto_leave_watchdog())
    await register_slash_commands(bot)

    async def _finish_secondary_startup():
        if assistant_warm_task is not None:
            try:
                await assistant_warm_task
            except Exception:
                LOGGER.debug('assistant peer warm-up failed', exc_info=True)
        await sync_log_group_peer(bot, assistant)
        await send_startup_log(bot, assistant, loaded, failed, failed_names)
    spawn(_finish_secondary_startup(), name='startup-log-sync')
    LOGGER.info(f'🎶 {BOT_NAME} is live!')
    try:
        await asyncio.Event().wait()
    finally:
        from utils.tasks import cancel_all
        await cancel_all()
if __name__ == '__main__':
    uvloop.install()
    asyncio.run(main())
