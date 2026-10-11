from utils.branding import BOT_NAME
import asyncio
import os
import time
from pyrogram import Client, filters, enums
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from melody import bot
from melody.core.ytdl import cached_file_path, download_audio, get_video_info, is_download_in_progress
from melody.logging import LOGGER
from utils.buttons import ikb, STYLE_DANGER, STYLE_SUCCESS, STYLE_PRIMARY
from utils.decorators import error_handler
from utils.formatters import format_duration, quote_html
from utils.media_convert import fetch_thumb, media_meta, to_telegram_audio, to_telegram_video
_pending: dict = {}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
_PROGRESS_INTERVAL = 5

def _safe_name(title: str) -> str:
    cleaned = ''.join((ch for ch in title or 'melody' if ch.isalnum() or ch in ' -_()[]')).strip()
    return (cleaned or 'melody')[:60]

def _dl_buttons(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[ikb('🎵 Audio', style=STYLE_SUCCESS, callback_data=f'dl_audio_{token}'), ikb('🎬 Video', style=STYLE_PRIMARY, callback_data=f'dl_video_{token}')], [ikb('✖ Cancel', style=STYLE_DANGER, callback_data=f'dl_cancel_{token}')]])

@bot.on_message(filters.command(['dl', 'download', 'song', 'video']))
@error_handler
async def dl_cmd(client: Client, message: Message):
    query = ' '.join(message.command[1:]).strip()
    if not query and message.reply_to_message:
        replied = message.reply_to_message
        query = (replied.text or replied.caption or '').strip()
    if not query:
        await message.reply(quote_html('🎧 <b>Usage:</b> <code>/dl [song name ya URL]</code>\n\n<i>Example: /dl Apna Bana Le</i>'), parse_mode=enums.ParseMode.HTML)
        return
    sent = await message.reply(quote_html(f'🔎 <b>{query[:60]}</b>\n\nKis format me chahiye?'), parse_mode=enums.ParseMode.HTML)
    token = f'{sent.chat.id}_{sent.id}'
    _pending[token] = {'user_id': message.from_user.id, 'query': query}
    try:
        await sent.edit_reply_markup(_dl_buttons(token))
    except Exception:
        await sent.edit(quote_html(f'🔎 <b>{query[:60]}</b>\n\nKis format me chahiye?'), parse_mode=enums.ParseMode.HTML, reply_markup=_dl_buttons(token))

def _find_downloaded_file(video_id: str, hint: 'str | None') -> 'str | None':
    dirs = []
    if hint:
        dirs.append(os.path.dirname(hint) or '.')
    for extra in ('downloads', 'cache', os.path.join('melody', 'downloads')):
        if extra not in dirs:
            dirs.append(extra)
    best, best_mtime = (None, -1.0)
    for directory in dirs:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if video_id not in name or name.endswith(('.part', '.ytdl', '.temp')):
                continue
            full = os.path.join(directory, name)
            try:
                if not os.path.isfile(full) or os.path.getsize(full) == 0:
                    continue
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            if mtime > best_mtime:
                best, best_mtime = (full, mtime)
    return best

@bot.on_callback_query(filters.regex('^dl_(audio|video|cancel)_(.+)$'))
async def dl_callback(client: Client, cb: CallbackQuery):
    action, token = (cb.matches[0].group(1), cb.matches[0].group(2))
    pending = _pending.get(token)
    if not pending:
        await cb.answer('⌛ Ye request expire ho gayi, dobara /dl bhejo.', show_alert=True)
        return
    if cb.from_user.id != pending['user_id']:
        await cb.answer('⚠️ Sirf request bhejne wala hi ye button use kar sakta hai.', show_alert=True)
        return
    if action == 'cancel':
        _pending.pop(token, None)
        await cb.answer('❌ Cancelled')
        try:
            await cb.message.edit(quote_html('❌ Download cancel kar diya gaya.'), parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
        return
    _pending.pop(token, None)
    audio_only = action == 'audio'
    await cb.answer('⬇️ Downloading...')
    query = pending['query']
    try:
        await cb.message.edit(quote_html(f'🔎 <b>{query[:60]}</b>\n\nSearching...'), parse_mode=enums.ParseMode.HTML)
        info = await get_video_info(query)
        if not info:
            await cb.message.edit(quote_html('❌ Kuch nahi mila. Spelling check karke phir try karo.'), parse_mode=enums.ParseMode.HTML)
            return
        video_id = info.get('id')
        title = info.get('title', 'Unknown')
        duration = info.get('duration', 0)
        thumb = info.get('thumbnail')
        path = cached_file_path(video_id, audio_only=audio_only)
        if not path:
            await cb.message.edit(quote_html(f'⬇️ <b>{title[:60]}</b>\n⏱ {format_duration(duration)}\n\nDownloading, max speed... 🚀'), parse_mode=enums.ParseMode.HTML)
            path = await download_audio(video_id, audio_only=audio_only)
            waited = 0.0
            while is_download_in_progress(video_id, audio_only=audio_only) and waited < 900:
                await asyncio.sleep(1)
                waited += 1
            final = cached_file_path(video_id, audio_only=audio_only)
            if final and os.path.isfile(final):
                path = final
            elif not (path and os.path.isfile(path)):
                path = _find_downloaded_file(str(video_id), path)
        if not path or not os.path.isfile(path):
            await cb.message.edit(quote_html('❌ Download fail ho gaya. Baad me phir try karo.'), parse_mode=enums.ParseMode.HTML)
            return
        await cb.message.edit(quote_html(f'🎛 <b>{title[:60]}</b>\n\nFormat convert ho raha hai...'), parse_mode=enums.ParseMode.HTML)
        thumb_path = await fetch_thumb(thumb, str(video_id))
        try:
            if audio_only:
                path = await to_telegram_audio(path, title=title, performer=str(info.get('uploader') or f'{BOT_NAME}'), cover=thumb_path)
            else:
                path = await to_telegram_video(path)
        except Exception as conv_err:
            LOGGER.warning('format conversion failed, sending original: %s', conv_err)
        meta = await media_meta(path)
        duration = meta.get('duration') or duration
        size = os.path.getsize(path)
        if size > _MAX_UPLOAD_BYTES:
            size_mb = size // (1024 * 1024)
            await cb.message.edit(quote_html(f'⚠️ Ye file bahut badi hai (~{size_mb}MB). Telegram par bhejna possible nahi hai, kripya koi choti clip try karo.'), parse_mode=enums.ParseMode.HTML)
            return
        await cb.message.edit(quote_html(f'📤 <b>{title[:60]}</b>\n\nUploading...'), parse_mode=enums.ParseMode.HTML)
        progress_state = {'last': 0.0}

        async def _progress(current: int, total: int):
            now = time.monotonic()
            if current != total and now - progress_state['last'] < _PROGRESS_INTERVAL:
                return
            progress_state['last'] = now
            pct = current / total * 100 if total else 0
            try:
                await cb.message.edit(quote_html(f'📤 <b>{title[:60]}</b>\n\nUploading... {pct:.0f}% ({current // (1024 * 1024)}MB/{total // (1024 * 1024)}MB)'), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
        caption = quote_html(f'🎶 <b>{title[:60]}</b>\n⏱ {format_duration(duration)}')
        try:
            if audio_only:
                await client.send_audio(cb.message.chat.id, audio=path, caption=caption, parse_mode=enums.ParseMode.HTML, title=title, performer=str(info.get('uploader') or f'{BOT_NAME}'), duration=duration, thumb=thumb_path, file_name=f'{_safe_name(title)}.mp3', progress=_progress)
            else:
                await client.send_video(cb.message.chat.id, video=path, caption=caption, parse_mode=enums.ParseMode.HTML, duration=duration, width=meta.get('width') or 0, height=meta.get('height') or 0, thumb=thumb_path, supports_streaming=True, file_name=f'{_safe_name(title)}.mp4', progress=_progress)
        except Exception as upload_err:
            msg = str(upload_err).lower()
            if 'too large' in msg or 'file_too_big' in msg or '413' in msg:
                await cb.message.edit(quote_html('⚠️ Ye file Telegram ki upload limit se badi hai (bot ~50MB tak hi bhej sakta hai jab tak local server na ho). Kripya kuch chota try karo.'), parse_mode=enums.ParseMode.HTML)
                return
            raise
        try:
            await cb.message.delete()
        except Exception:
            pass
    except Exception as e:
        LOGGER.error('dl_callback failed: %s', e)
        try:
            await cb.message.edit(quote_html('⚠️ Kuch galat ho gaya, thodi der baad try karo.'), parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
