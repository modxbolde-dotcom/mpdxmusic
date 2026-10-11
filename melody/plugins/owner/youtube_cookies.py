from __future__ import annotations

import time
import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery

from melody import bot
from melody.config import Config

_COOKIE_INPUT_TTL = 600.0
_cookie_input: dict[int, float] = {}
_cookie_buffers: dict[int, str] = {}


def _owner(user_id: int) -> bool:
    return bool(Config.OWNER_ID and user_id == Config.OWNER_ID)


@bot.on_callback_query(filters.regex('^owner_add_cookies$'))
async def add_cookies_menu(client: Client, cb: CallbackQuery):
    if not _owner(cb.from_user.id):
        return await cb.answer('❌ Owner only!', show_alert=True)
    _cookie_input[cb.from_user.id] = time.monotonic()
    from utils.buttons import ikb
    from strings.themes import RED, btn
    from pyrogram.types import InlineKeyboardMarkup
    markup = InlineKeyboardMarkup([[ikb(btn('✵ BACK ✵', RED), callback_data='owner_panel')]])
    await cb.message.edit_text(
        '<blockquote>🍪 <b>Add YouTube Cookies</b></blockquote>\n\n'
        'YouTube cookies ka text paste karo ya <b>kisi bhi naam ki file</b> bhejo (JSON/TXT/base64/Netscape). Bot content khud detect karega.\n\n'
        'Cookies sirf owner ke liye accept hongi, message/file process ke baad delete ho jayega, aur MongoDB me pool me add hongi.\n'
        'Multiple cookies text ya files se ek-ek karke bhej sakte ho; har cookie 3 seconds verify hokar pool me add hogi. Purani cookies replace nahi hongi.\n\n'
        '⚠️ Ye cookies sensitive hoti hain—kisi public chat me forward mat karna.\n'
        'Cancel: <code>/cancelcookies</code>',
        parse_mode=enums.ParseMode.HTML, reply_markup=markup,
    )
    await cb.answer()



@bot.on_callback_query(filters.regex('^owner_cookie_status$'))
async def cookie_status_menu(client: Client, cb: CallbackQuery):
    if not _owner(cb.from_user.id):
        return await cb.answer('❌ Owner only!', show_alert=True)
    from melody.core.ytdl import cookie_pool_status
    from utils.buttons import ikb
    from strings.themes import BLUE, RED, GREEN, btn
    from pyrogram.types import InlineKeyboardMarkup
    status = cookie_pool_status()
    total = status['total']
    available = status['available']
    cooling = status['cooling_down']
    if not total:
        state = '🔴 <b>No cookies added</b>'
    elif available:
        state = '🟢 <b>Pool ready</b>'
    else:
        state = '🟡 <b>All cookies temporarily cooling down</b>'
    retry = ''
    if status['next_retry_seconds']:
        retry = f"\n⏱ Next retry in: <code>{status['next_retry_seconds']}s</code>"
    text = (
        '<blockquote>🍪 <b>YouTube Cookie Status</b></blockquote>\n\n'
        f'{state}\n\n'
        f'📦 Total added: <code>{total}</code>\n'
        f'✅ Available/working pool: <code>{available}</code>\n'
        f'⏳ Cooling down: <code>{cooling}</code>{retry}\n\n'
        '<i>Bot har retry par next cookie automatically try karta hai.\n'
        'YouTube ki asli cookie expiry ko software se permanent nahi banaya ja sakta; expired cookie ko fresh export se add karo.</i>'
    )
    markup = InlineKeyboardMarkup([
        [ikb(btn('➕ Add More Cookies', GREEN), callback_data='owner_add_cookies')],
        [ikb(btn('🗑 Remove All Cookies', RED), callback_data='owner_remove_cookies')],
        [ikb(btn('🔄 Refresh Status', BLUE), callback_data='owner_cookie_status')],
        [ikb(btn('✵ BACK ✵', RED), callback_data='owner_panel')],
    ])
    await cb.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    await cb.answer()

@bot.on_callback_query(filters.regex('^owner_remove_cookies$'))
async def remove_cookies_menu(client: Client, cb: CallbackQuery):
    if not _owner(cb.from_user.id):
        return await cb.answer('❌ Owner only!', show_alert=True)
    from melody.core.ytdl import remove_runtime_cookies
    from utils.database import set_global
    remove_runtime_cookies()
    await set_global('youtube_cookies', [])
    await cookie_status_menu(client, cb)

@bot.on_message(filters.private & (filters.text | filters.document))
async def receive_cookies(client: Client, message: Message):
    if not message.from_user or not _owner(message.from_user.id):
        return
    owner_id = message.from_user.id
    started = _cookie_input.get(owner_id)
    if not started or time.monotonic() - started > _COOKIE_INPUT_TTL:
        _cookie_input.pop(owner_id, None)
        return
    if message.text and message.text.strip().lower() == '/cancelcookies':
        _cookie_input.pop(owner_id, None)
        _cookie_buffers.pop(owner_id, None)
        await message.reply('✅ Cookies input cancel kar diya gaya.')
        return
    try:
        # Accept text or any document filename; validate the content, not the
        # extension. This supports renamed .txt/.json files and base64 files.
        if message.document:
            if message.document.file_size and message.document.file_size > 2 * 1024 * 1024:
                await message.reply('❌ Cookie file 2 MB se chhoti honi chahiye.')
                return
            blob = await message.download(in_memory=True)
            raw = blob.getvalue().decode('utf-8', errors='replace') if blob else ''
        else:
            raw = (message.text or '').strip()
        if not raw:
            await message.reply('❌ Cookie content empty hai. Text ya valid cookie file bhejo.')
            return
        # Delete the sensitive message immediately, then validate for 3 seconds
        # before activating it. The Add Cookies mode remains open so multiple
        # text payloads or files can be submitted one after another.
        try:
            await message.delete()
        except Exception:
            pass
        pending = _cookie_buffers.get(owner_id, '')
        candidate = pending + raw
        verifying = await message.reply('⏳ Cookie text receive ho gaya. 3 seconds verify/decode kar raha hoon…')
        await asyncio.sleep(3)
        from melody.core.ytdl import set_runtime_cookies, cookie_pool_status
        if not set_runtime_cookies(candidate, append=True):
            # Large base64/JSON/Netscape pastes can exceed Telegram's message
            # limit. Keep likely partial payloads and join the next text.
            looks_partial = bool(pending or len(candidate) >= 1000 or candidate.startswith(('#', '[', '{')))
            if looks_partial and len(candidate) <= 2 * 1024 * 1024:
                _cookie_buffers[owner_id] = candidate
                await verifying.edit_text('⏳ Cookie text abhi incomplete lag raha hai. Baaki text next message me bhejo; bot automatically jodkar verify karega.')
            else:
                _cookie_buffers.pop(owner_id, None)
                await verifying.edit_text('❌ Cookie content decode/verify nahi hua. Netscape text, JSON, ya base64 text/file bhejo.')
            return
        _cookie_buffers.pop(owner_id, None)
        from utils.database import set_global
        from melody.core.ytdl import cookie_pool_snapshot
        # Save normalized active cookies, not the raw paste. This keeps the
        # restart pool identical and excludes already-expired entries.
        await set_global('youtube_cookies', cookie_pool_snapshot())
        status = cookie_pool_status()
        await verifying.edit_text(
            '✅ <b>Cookie verified aur pool me add ho gayi.</b>\n\n'
            f'📦 Total cookies: <code>{status["total"]}</code>\n'
            'Aur cookie add karni ho to next cookie ka text ya file bhejo.\n'
            'Band karne ke liye: <code>/cancelcookies</code>',
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:

        _cookie_input.pop(owner_id, None)
        _cookie_buffers.pop(owner_id, None)
        await message.reply(f'❌ Cookies save nahi hui: <code>{type(exc).__name__}</code>.')
