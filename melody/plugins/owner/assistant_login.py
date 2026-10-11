from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery

from melody import bot
from melody.config import Config
from utils.assistant_auth import (
    begin_phone_login, cancel, login_stage, submit_code, submit_password,
    submit_phone, submit_session,
)


def _owner(uid: int) -> bool:
    return bool(Config.OWNER_ID and uid == Config.OWNER_ID)


@bot.on_callback_query(filters.regex('^owner_assistant_login$'))
async def assistant_login_menu(client: Client, cb: CallbackQuery):
    if not _owner(cb.from_user.id):
        return await cb.answer('❌ Owner only!', show_alert=True)
    from utils.buttons import ikb
    from strings.themes import BLUE, RED, btn
    from pyrogram.types import InlineKeyboardMarkup
    markup = InlineKeyboardMarkup([
        [ikb(btn('📱 Login via phone + OTP', BLUE), callback_data='assistant_login_phone')],
        [ikb(btn('🔑 Login via session string', BLUE), callback_data='assistant_login_session')],
        [ikb(btn('✵ BACK ✵', RED), callback_data='owner_panel')],
    ])
    await cb.message.edit_text(
        '<blockquote>🎧 <b>Assistant Login</b></blockquote>\n\n'
        'Environment me <code>STRING_SESSION</code> ki zaroorat nahi hai.\n'
        'Phone login ke baad session MongoDB me save hoga aur restart ke baad restore hoga.\n\n'
        '⚠️ OTP, 2FA password ya session string kisi aur ko forward mat karna.',
        parse_mode=enums.ParseMode.HTML, reply_markup=markup,
    )
    await cb.answer()


@bot.on_callback_query(filters.regex('^assistant_login_phone$'))
async def assistant_login_phone(client: Client, cb: CallbackQuery):
    if not _owner(cb.from_user.id):
        return await cb.answer('❌ Owner only!', show_alert=True)
    text = await begin_phone_login(cb.from_user.id)
    await cb.message.edit_text(text, parse_mode=enums.ParseMode.HTML)
    await cb.answer()


@bot.on_callback_query(filters.regex('^assistant_login_session$'))
async def assistant_login_session(client: Client, cb: CallbackQuery):
    if not _owner(cb.from_user.id):
        return await cb.answer('❌ Owner only!', show_alert=True)
    from utils.assistant_auth import _login_states
    _login_states[cb.from_user.id] = {'stage': 'session', 'created': __import__('time').monotonic()}
    await cb.message.edit_text(
        '🔑 <b>Session login</b>\n\nApna assistant session string yahan bhejo.\n'
        'Message process hone ke baad turant delete kar diya jayega.\n\n'
        'Cancel: <code>/cancelassistant</code>',
        parse_mode=enums.ParseMode.HTML,
    )
    await cb.answer()


@bot.on_message(filters.private & filters.text)
async def assistant_login_input(client: Client, message: Message):
    if not message.from_user or not _owner(message.from_user.id):
        return
    owner_id = message.from_user.id
    text = (message.text or '').strip()
    if text.lower() in {'/cancelassistant', '/cancel_login'}:
        await message.reply(await cancel(owner_id))
        return
    stage = login_stage(owner_id)
    if not stage:
        return
    # Never leave OTP, 2FA password, or session strings in the chat history.
    try:
        await message.delete()
    except Exception:
        pass
    if stage == 'phone':
        reply = await submit_phone(owner_id, text)
    elif stage == 'code':
        reply = await submit_code(owner_id, text)
    elif stage == 'password':
        reply = await submit_password(owner_id, text)
    elif stage == 'session':
        reply = await submit_session(owner_id, text)
    else:
        reply = '⚠️ Login state invalid hai. Panel se dobara start karo.'
    await message.reply(reply, parse_mode=enums.ParseMode.HTML)
