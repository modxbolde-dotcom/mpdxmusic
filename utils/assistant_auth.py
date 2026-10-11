from __future__ import annotations

"""Owner-only assistant authentication via phone OTP/2FA or session string.

The bot token remains the only required startup credential.  A successful
assistant login is persisted in MongoDB so the worker can restore it after a
restart without putting STRING_SESSION in the deployment environment.
"""

import asyncio
import inspect
import time
from typing import Any

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

from melody.config import Config
from melody.logging import LOGGER

_SESSION_KEY = 'assistant_session'
_LOGIN_TTL = 600.0
_login_states: dict[int, dict[str, Any]] = {}
_login_lock = asyncio.Lock()


def _valid_owner(owner_id: int) -> bool:
    return bool(Config.OWNER_ID and int(owner_id) == int(Config.OWNER_ID))


async def load_saved_session() -> str | None:
    """Read the persisted assistant session, if one exists."""
    try:
        from utils.database import get_global
        value = await get_global(_SESSION_KEY, '')
        value = str(value or '').strip()
        return value or None
    except Exception as exc:
        LOGGER.warning('Could not load saved assistant session: %s', exc)
        return None


async def save_session(session: str) -> None:
    from utils.database import set_global
    await set_global(_SESSION_KEY, session)


async def clear_saved_session() -> None:
    from utils.database import set_global
    await set_global(_SESSION_KEY, '')


def _new_login_client(owner_id: int) -> Client:
    return Client(
        f'AssistantLogin_{owner_id}',
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        in_memory=True,
        workers=1,
        max_concurrent_transmissions=1,
    )


def _state(owner_id: int) -> dict[str, Any] | None:
    state = _login_states.get(owner_id)
    if state and time.monotonic() - float(state.get('created', 0)) > _LOGIN_TTL:
        _login_states.pop(owner_id, None)
        return None
    return state


async def begin_phone_login(owner_id: int) -> str:
    if not _valid_owner(owner_id):
        return '❌ Owner only.'
    async with _login_lock:
        old = _login_states.pop(owner_id, None)
        if old and old.get('client'):
            try:
                await old['client'].disconnect()
            except Exception:
                pass
        client = _new_login_client(owner_id)
        await client.connect()
        phone = ''
        _login_states[owner_id] = {'stage': 'phone', 'client': client, 'created': time.monotonic(), 'phone': phone}
    return ('📱 <b>Assistant phone login</b>\n\n'
            'Apna phone number international format me bhejo, example:\n'
            '<code>+919876543210</code>\n\n'
            'Cancel karne ke liye <code>/cancelassistant</code> bhejo.')


async def submit_phone(owner_id: int, phone: str) -> str:
    state = _state(owner_id)
    if not state or state.get('stage') != 'phone':
        return '⚠️ Pehle Owner Panel se <b>Login via phone</b> select karo.'
    phone = phone.strip().replace(' ', '')
    if not phone.startswith('+') or len(phone) < 8 or len(phone) > 16 or not phone[1:].isdigit():
        return '❌ Phone number invalid hai. International format use karo, example <code>+919876543210</code>.'
    try:
        sent = await state['client'].send_code(phone)
        state.update({'stage': 'code', 'phone': phone, 'phone_code_hash': sent.phone_code_hash})
        return ('✅ OTP Telegram app/SMS par bhej diya gaya.\n\n'
                'Ab sirf OTP digits bhejo. Spaces allowed hain.\n'
                'Agar 2-Step Verification enabled hai to uske baad password bhi maanga jayega.')
    except Exception as exc:
        await _discard(owner_id)
        return f'❌ OTP send nahi hua: <code>{type(exc).__name__}</code>. Number/check karke dobara try karo.'


async def submit_code(owner_id: int, code: str) -> str:
    state = _state(owner_id)
    if not state or state.get('stage') != 'code':
        return '⚠️ Abhi OTP login pending nahi hai.'
    code = ''.join(code.split())
    if not code.isdigit() or len(code) < 3:
        return '❌ OTP sirf digits me bhejo.'
    try:
        await state['client'].sign_in(state['phone'], state['phone_code_hash'], code)
    except SessionPasswordNeeded:
        state['stage'] = 'password'
        return '🔐 OTP correct hai. Ab Telegram 2-Step Verification password bhejo.'
    except Exception as exc:
        return f'❌ OTP reject hua: <code>{type(exc).__name__}</code>. Naya login start karo.'
    return await _finish(owner_id)


async def submit_password(owner_id: int, password: str) -> str:
    state = _state(owner_id)
    if not state or state.get('stage') != 'password':
        return '⚠️ Abhi 2FA password pending nahi hai.'
    if not password.strip():
        return '❌ Password empty nahi ho sakta.'
    try:
        await state['client'].check_password(password.strip())
    except Exception as exc:
        return f'❌ 2FA password reject hua: <code>{type(exc).__name__}</code>.'
    return await _finish(owner_id)


async def submit_session(owner_id: int, session: str) -> str:
    if not _valid_owner(owner_id):
        return '❌ Owner only.'
    session = session.strip()
    if len(session) < 30 or len(session) > 4096:
        return '❌ Session string invalid/too short hai.'
    client = Client(
        f'AssistantSessionLogin_{owner_id}',
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        session_string=session,
        workers=1,
        max_concurrent_transmissions=1,
    )
    try:
        await client.start()
        me = await client.get_me()
        await client.stop()
        await _activate(session)
        await save_session(session)
        return f'✅ Session login successful: <b>{me.first_name or "Assistant"}</b> (ID <code>{me.id}</code>).'
    except Exception as exc:
        try:
            await client.stop()
        except Exception:
            pass
        return f'❌ Session login failed: <code>{type(exc).__name__}</code>. Session check karke dobara bhejo.'


async def _finish(owner_id: int) -> str:
    state = _login_states.get(owner_id)
    if not state:
        return '❌ Login session expire ho gaya.'
    client = state['client']
    try:
        result = client.export_session_string()
        session = await result if inspect.isawaitable(result) else result
        me = await client.get_me()
        await client.disconnect()
        await _activate(session)
        await save_session(session)
        _login_states.pop(owner_id, None)
        return f'✅ Phone login successful: <b>{me.first_name or "Assistant"}</b> (ID <code>{me.id}</code>). Voice chat assistant active hai.'
    except Exception as exc:
        LOGGER.exception('Assistant phone login activation failed')
        return f'❌ Login hua, lekin assistant activate nahi hua: <code>{type(exc).__name__}</code>.'


async def _activate(session: str) -> None:
    import melody
    from melody import set_assistant_session
    assistant = set_assistant_session(session)
    await assistant.start()
    melody.ASSISTANT_READY = True
    from melody.core.call import start_call_py
    await start_call_py()
    LOGGER.info('Assistant activated through owner panel login.')


async def _discard(owner_id: int) -> None:
    state = _login_states.pop(owner_id, None)
    if state and state.get('client'):
        try:
            await state['client'].disconnect()
        except Exception:
            pass


async def cancel(owner_id: int) -> str:
    if not _valid_owner(owner_id):
        return '❌ Owner only.'
    await _discard(owner_id)
    return '✅ Assistant login cancel kar diya gaya.'


def login_stage(owner_id: int) -> str | None:
    state = _state(owner_id)
    return str(state['stage']) if state else None
