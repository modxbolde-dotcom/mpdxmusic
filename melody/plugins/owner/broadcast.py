import asyncio
from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message
from melody import bot
from utils.database import get_all_chats, get_all_users, remove_user
from utils.decorators import error_handler, owner_only
_FLAGS = ('-user', '-onlyuser', '-pin', '-pinloud', '-forward')
_running = asyncio.Lock()

def _strip_flags(text: str) -> str:
    return ' '.join((w for w in text.split() if w.lower() not in _FLAGS)).strip()

async def _deliver(chat_id: int, message: Message, payload: str, forward: bool, pin: str) -> bool:
    for _ in range(2):
        try:
            if message.reply_to_message and forward:
                sent = await message.reply_to_message.forward(chat_id)
            elif message.reply_to_message:
                sent = await message.reply_to_message.copy(chat_id)
            else:
                sent = await bot.send_message(chat_id, payload, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            if pin and sent:
                try:
                    await sent.pin(disable_notification=pin == 'silent')
                except Exception:
                    pass
            return True
        except FloodWait as exc:
            await asyncio.sleep(int(getattr(exc, 'value', 5)) + 1)
            continue
        except Exception:
            return False
    return False

@bot.on_message(filters.command(['broadcast', 'gcast']))
@owner_only
@error_handler
async def broadcast_cmd(client: Client, message: Message):
    raw = message.text or ''
    lower = raw.lower()
    to_users = '-user' in lower or '-onlyuser' in lower
    only_users = '-onlyuser' in lower
    forward = '-forward' in lower
    pin = 'loud' if '-pinloud' in lower else 'silent' if '-pin' in lower else ''
    payload = _strip_flags(raw.split(None, 1)[1]) if len(raw.split(None, 1)) > 1 else ''
    if not payload and (not message.reply_to_message):
        return await message.reply('📢 <b>Broadcast</b>\n\n• <code>/broadcast &lt;message&gt;</code>\n• ya kisi message pe reply karke <code>/broadcast</code>\n\n<b>Flags:</b> <code>-user</code> <code>-onlyuser</code> <code>-pin</code> <code>-pinloud</code> <code>-forward</code>', parse_mode=enums.ParseMode.HTML)
    if _running.locked():
        return await message.reply('⏳ Ek broadcast already chal raha hai — wait karo.')
    status = await message.reply('📢 Broadcast shuru...')
    sent_chats = failed_chats = sent_users = failed_users = 0
    async with _running:
        targets: list = []
        if not only_users:
            try:
                targets = [int(c['chat_id']) for c in await get_all_chats() if c.get('chat_id')]
            except Exception:
                targets = []
        for index, chat_id in enumerate(targets, 1):
            if await _deliver(chat_id, message, payload, forward, pin):
                sent_chats += 1
            else:
                failed_chats += 1
            await asyncio.sleep(0.25)
            if index % 25 == 0:
                try:
                    await status.edit(f'📢 Groups: <b>{sent_chats}</b> ✅ / <b>{failed_chats}</b> ❌ ({index}/{len(targets)})', parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
        if to_users:
            try:
                users = [int(u['user_id']) for u in await get_all_users() if u.get('user_id')]
            except Exception:
                users = []
            for index, user_id in enumerate(users, 1):
                if await _deliver(user_id, message, payload, forward, pin):
                    sent_users += 1
                else:
                    failed_users += 1
                    try:
                        await remove_user(user_id)
                    except Exception:
                        pass
                await asyncio.sleep(0.25)
                if index % 25 == 0:
                    try:
                        await status.edit(f'📢 Users: <b>{sent_users}</b> ✅ / <b>{failed_users}</b> ❌ ({index}/{len(users)})', parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass
    summary = f'✅ <b>Broadcast complete</b>\n\n• Groups: <b>{sent_chats}</b> sent, <b>{failed_chats}</b> failed\n'
    if to_users:
        summary += f'• Users: <b>{sent_users}</b> sent, <b>{failed_users}</b> failed\n'
    try:
        await status.edit(summary, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await message.reply(summary, parse_mode=enums.ParseMode.HTML)
