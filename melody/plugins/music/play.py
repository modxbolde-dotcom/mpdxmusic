from utils.branding import BOT_NAME
import asyncio
import time as _time
import html
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup
from melody import bot
from melody.logging import LOGGER
from melody.config import Config
from melody.core.ytdl import download_replied_media, get_video_info, has_playable_media, tagged_media_limit_reason
from melody.core.queue import set_last_user_track, Track, is_autoplay_on, get_queue
from melody.core.call import play_stream, force_play_stream, abort_prejoin_if_idle, ensure_assistant_peer, pre_join
from melody.logging import log_activity
from utils.database import add_history
from utils.decorators import channel_admin_or_auth, error_handler, playmode_gate
from utils.formatters import format_duration, quote_html
from utils.thumbnails import make_thumbnail, fetch_dp, get_bot_dp, get_bot_identity
from utils.animation import AnimatedStatus
from utils.tasks import spawn
_bg_downloads: set = set()

def get_play_buttons(chat_title: str, autoplay_on: bool=False, bot_username: 'str | None'=None, bot_name: str=f'{BOT_NAME} Music', _chat_type: 'enums.ChatType | None'=None, chat_id: int=0, paused: bool=False) -> InlineKeyboardMarkup:
    from utils.inline import inline
    return inline.play_card(chat_id=chat_id, chat_title=chat_title, autoplay_on=autoplay_on, paused=paused, bot_username=bot_username, bot_name=bot_name)

async def _play_core(client: Client, message: Message, video: bool=False, force: bool=False, stream_chat=None):
    import melody as _melody_pkg
    if not getattr(_melody_pkg, 'ASSISTANT_READY', True):
        return await message.reply('<blockquote>⚠️ <b>Aꜱꜱɪꜱᴛᴀɴᴛ ᴏғғʟɪɴᴇ</b>\n\n🔑 <b>Assistant login active nahi hai</b>, isliye voice chat join nahi ho sakta.\n🛠 Owner: <code>/panel</code> → <b>Assistant Login</b> se phone/OTP ya session login complete karo.</blockquote>', parse_mode=enums.ParseMode.HTML)
    query = ' '.join(message.command[1:]) if len(message.command) > 1 else None
    chat = stream_chat or message.chat
    user = message.from_user
    replied = message.reply_to_message
    tagged = has_playable_media(replied)
    if tagged:
        limit_reason = tagged_media_limit_reason(replied, video=video)
        if limit_reason:
            await message.reply(quote_html(limit_reason), parse_mode=enums.ParseMode.HTML)
            return
    if not query and (not tagged):
        usage_cmd = '/cplay' if chat.type == enums.ChatType.CHANNEL else '/play'
        from melody.plugins.misc.pics import send_with_pic
        await send_with_pic(client, message.chat.id, 'play', quote_html(f'🎵 <b>Song ka naam ya YouTube link bhejo</b>\n\n<code>{usage_cmd} &lt;song name / URL&gt;</code>\nAudio/video ko reply karke <code>{usage_cmd}</code> bhi chala sakte ho.'), reply_to=message.id)
        return
    if query and (not tagged) and (not force) and (not query.lower().startswith(('http://', 'https://'))):
        try:
            from melody.plugins.music.playmode import get_search_mode
            _mode = await get_search_mode(chat.id)
        except Exception:
            _mode = 'direct'
        if _mode == 'inline':
            from melody.core.ytdl import search_youtube
            from utils.buttons import ikb
            from strings.themes import BLUE, RED, GREEN, btn
            picker = await message.reply(quote_html('🔎 <b>Searching...</b>'), parse_mode=enums.ParseMode.HTML)
            results = await search_youtube(query, limit=5)
            if not results:
                await picker.edit(quote_html('❌ <b>Kuch match nahi hua.</b>'), parse_mode=enums.ParseMode.HTML)
                return
            colors = [RED, BLUE, GREEN]
            text = f'🔎 <b>“{html.escape(query[:48])}”</b> — koi ek chuno 👇'
            buttons = [[ikb(btn(f"{i}. {r['title'][:30]}", colors[(i - 1) % len(colors)]), callback_data=f"play_search_{r['id']}")] for i, r in enumerate(results[:5], 1) if r.get('id')]
            await picker.edit(quote_html(text), parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
            return
    direct_id = None
    if query and (not tagged):
        from melody.core.ytdl import extract_video_id, is_valid_video_id
        direct_id = extract_video_id(query) or (query.strip() if is_valid_video_id(query.strip()) else None)
        from melody.core.ytdl import cancel_lower_priority_downloads
        cancel_lower_priority_downloads(max_priority=0, exclude_video_id=direct_id)
    if not (query and (not tagged)):
        from melody.core.ytdl import cancel_lower_priority_downloads
        cancel_lower_priority_downloads(max_priority=0)
    requester_id = user.id if user else chat.id
    requester_name = user.first_name if user else chat.title or 'Channel'
    requester_mention = user.mention if user else html.escape(requester_name)
    _t0 = _time.monotonic()

    def _lap() -> float:
        return round(_time.monotonic() - _t0, 2)
    spawn(ensure_assistant_peer(chat.id), name=f'peer-ready-{chat.id}')
    if not video:
        spawn(pre_join(chat.id), name=f'vc-prejoin-{chat.id}')
    bot_dp_task = asyncio.create_task(get_bot_dp(client))
    user_dp_task = asyncio.create_task(fetch_dp(client, requester_id))
    if tagged:
        info_task = asyncio.create_task(download_replied_media(client, replied, video=video))
    else:
        info_task = asyncio.create_task(get_video_info(query))
    processing_task = asyncio.create_task(message.reply(quote_html('🔥 Getting your vibe ready...'), parse_mode=enums.ParseMode.HTML))

    async def _start_anim():
        return AnimatedStatus(await processing_task, 'Getting your vibe ready').start()
    anim_task = asyncio.create_task(_start_anim())
    anim = None
    try:
        info = await info_task
        _t_info = _lap()
        if not info:
            processing = await processing_task
            anim = await anim_task
            await abort_prejoin_if_idle(chat.id)
            await anim.stop()
            if tagged:
                await processing.edit(quote_html('❌ Ye tagged file play nahi ho payi 🌸'), parse_mode=enums.ParseMode.HTML)
                return
            suggestions = []
            try:
                from melody.core.query_fix import find_suggestions
                suggestions = await asyncio.wait_for(find_suggestions(query, limit=5), timeout=8.0)
            except Exception as exc:
                LOGGER.debug('suggestion lookup failed for %r: %s', (query or '')[:40], exc)
            if suggestions:
                from utils.buttons import ikb
                from strings.themes import BLUE, RED, GREEN, btn
                colors = [RED, BLUE, GREEN]
                text = f"🔎 <b>“{html.escape((query or '')[:48])}”</b> exactly nahi mili 🌸\nShayad in me se koi chahiye? Tap karo aur baj jayegi 👇"
                buttons = [[ikb(btn(f"{i}. {r.get('title', 'Unknown')[:30]}", colors[(i - 1) % len(colors)]), callback_data=f"play_search_{r['id']}")] for i, r in enumerate(suggestions[:5], 1) if r.get('id')]
                await processing.edit(quote_html(text), parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
                return
            await processing.edit(quote_html('❌ <b>Kuch bhi match nahi hua</b> 🌸\nNaam thoda alag likh ke ya artist ka naam jod ke try karo.'), parse_mode=enums.ParseMode.HTML)
            return
        from melody.core.ytdl import cached_file_path, download_audio, is_tg_media_id, on_cloud_host, resolve_stream_urls, should_try_direct_stream

        async def _warm_sources(video_id: str, want_video: bool):
            if cached_file_path(video_id, audio_only=not want_video):
                return []
            if not is_tg_media_id(video_id):
                try:
                    from melody import bot as _archive_bot
                    from utils.telegram_archive import restore_archived_file
                    archived = await restore_archived_file(_archive_bot, video_id, video=want_video)
                    if archived:
                        return [archived]
                except Exception:
                    pass
            if is_tg_media_id(video_id):
                return []
            if not should_try_direct_stream():
                return await asyncio.gather(download_audio(video_id, audio_only=not want_video, priority=0, owner=chat.id), return_exceptions=True)
            try:
                await resolve_stream_urls(video_id, want_video=want_video)
                return []
            except Exception as exc:
                LOGGER.info('warm resolve failed for %s (%s) — playback will own fallback download', video_id, exc)
                if on_cloud_host():
                    return []
                return await asyncio.gather(download_audio(video_id, audio_only=not want_video, priority=0, owner=chat.id), return_exceptions=True)
        spawn(_warm_sources(info['id'], video), name=f"warm-src-{info['id']}")
        track = Track(video_id=info['id'], title=info['title'], duration=info['duration'], stream_url=info['stream_url'], thumbnail=info['thumbnail'], uploader=info['uploader'], requester_id=requester_id, requester_name=requester_name, requested_in=chat.id, video=video, source_query=query if query and (not tagged) and (not query.lower().startswith(('http://', 'https://'))) else '')
        _t_join = _lap()
        if force:
            playing_now = await force_play_stream(chat.id, track, video=video, prejoin=False)
        else:
            playing_now = await play_stream(chat.id, track, video=video, prejoin=False)
        _total_elapsed = _lap()
        _stream_elapsed = max(0.0, _total_elapsed - _t_join) if playing_now else 0.0
        queued = track in get_queue(chat.id)
        if not playing_now and (force or not queued):
            processing = await processing_task
            anim = await anim_task
            await anim.stop()
            await processing.edit(quote_html('❌ <b>Gana play nahi ho paya.</b>\nYouTube stream unavailable ho sakti hai ya VC handoff complete nahi hua.\nVC permissions tabhi check karo jab VC-related message aaye.'), parse_mode=enums.ParseMode.HTML)
            spawn(log_activity(f"❌ <b>Play Failed</b>\n• Song: <code>{html.escape(info['title'][:60])}</code>\n• Chat: {html.escape(chat.title or 'Private')} (<code>{chat.id}</code>)\n• ⏱ Time: <b>{_total_elapsed:.2f}s</b>"))
            LOGGER.warning('playback handoff failed | force=%s chat=%s video=%s total=%.2fs', force, chat.id, info['id'], _total_elapsed)
            return
        _outcome = 'playing' if playing_now else 'queued'
        LOGGER.info('⏱ play timings | outcome=%s search=%.2fs join=%.2fs stream=%.2fs total=%.2fs | %s', _outcome, _t_info, _t_join - _t_info, _stream_elapsed, _total_elapsed, info['id'])
        await add_history(chat.id, info['id'], info['title'])
        set_last_user_track(chat.id, info['id'], video=video)
        processing = await processing_task
        anim = await anim_task
        activity_label = 'Force Played' if force else 'Now Playing' if playing_now else 'Queued'
        spawn(log_activity(f"🎵 <b>{activity_label}</b>\n• Song: <code>{html.escape(info['title'][:60])}</code>\n• Requested by: {html.escape(requester_name or 'Unknown')} (<code>{requester_id}</code>)\n• Chat: {html.escape(chat.title or 'Private')} (<code>{chat.id}</code>)\n• ⏱ Time: <b>{_total_elapsed:.2f}s</b> (search {_t_info:.2f}s · stream {_stream_elapsed:.2f}s){(' ✅' if _total_elapsed <= 5.0 else ' ⚠️ slow')}"))
        status_label = 'Force Played' if force else 'Now Playing' if playing_now else 'Added to Queue'
        safe_title = html.escape(info['title'][:50])
        safe_uploader = html.escape(info['uploader'])
        safe_duration = html.escape(format_duration(info['duration']))
        bot_dp_path, user_dp_path = await asyncio.gather(bot_dp_task, user_dp_task)
        bot_username, bot_name = await get_bot_identity(client)
        autoplay_on = await is_autoplay_on(chat.id)
        play_buttons = get_play_buttons(chat.title or '', autoplay_on, bot_username, bot_name, chat.type, chat_id=chat.id)
        caption = f"<blockquote>🎶 <b>{html.escape(status_label)}</b> · 🔁 AutoPlay: <b>{('ON' if autoplay_on else 'OFF')}</b>\n<b>{safe_title}</b>\n👤 {safe_uploader} · ⏱ {safe_duration}\n🙋 {requester_mention}</blockquote>"
        try:
            thumb_path = await make_thumbnail(song_title=info['title'], artist=info['uploader'], duration=format_duration(info['duration']), requester_name=requester_name, group_name=chat.title or 'Private', owner_name=Config.OWNER_NAME, yt_thumbnail_url=info['thumbnail'], requester_dp_path=user_dp_path, bot_dp_path=bot_dp_path)
            await anim.stop()
            await message.reply_photo(thumb_path, caption=caption, parse_mode=enums.ParseMode.HTML, reply_markup=play_buttons)
            await processing.delete()
        except Exception as thumb_exc:
            from melody.logging import send_error_log
            _is_photo_forbidden = type(thumb_exc).__name__ == 'ChatSendPhotosForbidden' or 'CHAT_SEND_PHOTOS_FORBIDDEN' in str(thumb_exc)
            if not _is_photo_forbidden:
                await send_error_log(f'make_thumbnail / reply_photo failed in {chat.id}', thumb_exc, context={'chat_id': chat.id, 'chat_title': chat.title, 'user_id': requester_id, 'user_name': requester_name, 'song_title': info['title'] if info else None, 'video_id': info['id'] if info else None})
            status = 'Force Played ⚡' if force else 'Now Playing ▶️' if playing_now else 'Added to Queue 📋'
            await anim.stop()
            try:
                await processing.edit(quote_html(f"🎵 <b>{html.escape(status)}</b>\n\n<code>{safe_title}</code>\n👤 <code>{safe_uploader}</code>  ⏱ <code>{safe_duration}</code>\n🏠 {html.escape((chat.title or 'Private')[:60])}"), parse_mode=enums.ParseMode.HTML, reply_markup=play_buttons)
            except Exception:
                await message.reply(quote_html(f"🎵 <b>{html.escape(status)}</b>\n\n<code>{safe_title}</code>\n👤 <code>{safe_uploader}</code>  ⏱ <code>{safe_duration}</code>\n🏠 {html.escape((chat.title or 'Private')[:60])}"), parse_mode=enums.ParseMode.HTML, reply_markup=play_buttons)
    finally:
        try:
            if anim is None:
                anim = await anim_task
            await anim.stop()
        except Exception:
            pass

@bot.on_message(filters.command('play', prefixes=['/', '.', '!', '$']) & filters.group)
@error_handler
@playmode_gate
async def play_cmd(client: Client, message: Message):
    await _play_core(client, message, video=False)

@bot.on_message(filters.command('vplay', prefixes=['/', '.', '!', '$']) & filters.group)
@error_handler
@playmode_gate
async def vplay_cmd(client: Client, message: Message):
    await _play_core(client, message, video=True)

@bot.on_message(filters.command('playforce', prefixes=['/', '.', '!', '$']) & filters.group)
@error_handler
async def playforce_cmd(client: Client, message: Message):
    await _play_core(client, message, video=False, force=True)

@bot.on_message(filters.command('vplayforce', prefixes=['/', '.', '!', '$']) & filters.group)
@error_handler
async def vplayforce_cmd(client: Client, message: Message):
    await _play_core(client, message, video=True, force=True)

@bot.on_message(filters.command('cplay') & filters.channel)
@error_handler
@channel_admin_or_auth
async def cplay_cmd(client: Client, message: Message):
    await _play_core(client, message, video=False)

@bot.on_message(filters.command('cvplay') & filters.channel)
@error_handler
@channel_admin_or_auth
async def cvplay_cmd(client: Client, message: Message):
    await _play_core(client, message, video=True)
