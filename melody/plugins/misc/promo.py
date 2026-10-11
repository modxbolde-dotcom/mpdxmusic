from utils.branding import BOT_NAME
import random
import time
from pyrogram import Client, ContinuePropagation, enums, filters
from pyrogram.types import InlineKeyboardMarkup, Message
from utils.buttons import ikb
from melody import bot
from melody.config import Config
from utils.admin_tools import auto_delete, card, mention
from utils.decorators import error_handler, owner_only
from utils.gc_db import get_global, set_global
_CHANCE = 60
_COOLDOWN = 1800
_last_promo: dict = {}
_LINES = (f'Apne group me bhi {BOT_NAME} bulao — HD music, zero lag 🎧', f'Tumhare group me gaane kaun bajata hai? {BOT_NAME} try karo 🎶', 'Ek click me apne group ka DJ set karo 🎛', f'Voice chat sunsan hai? {BOT_NAME} ko add karo 🔥')

def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[ikb(f'➕ Aᴅᴅ {BOT_NAME} ᴛᴏ Yᴏᴜʀ Gʀᴏᴜᴘ', url=f"https://t.me/{Config.BOT_USERNAME.lstrip('@')}?startgroup=true")], [ikb('📖 Hᴇʟᴘ', callback_data='help_main'), ikb('✵ CLOSE ✵', callback_data='gc_close')]])

@bot.on_message(filters.group & ~filters.service, group=11)
async def random_promo(client: Client, message: Message):
    await _random_promo(client, message)
    raise ContinuePropagation

async def _random_promo(client: Client, message: Message):
    try:
        if not message.from_user or message.from_user.is_bot:
            return
        if not await get_global('promo', True):
            return
        now = time.time()
        if now - _last_promo.get(message.chat.id, 0) < _COOLDOWN:
            return
        if random.randint(1, _CHANCE) != 1:
            return
        _last_promo[message.chat.id] = now
        sent = await message.reply(card(f'{BOT_NAME} Iɴᴠɪᴛᴇ', f'👋 {mention(message.from_user)}\n\n🎵 <i>{random.choice(_LINES)}</i>'), parse_mode=enums.ParseMode.HTML, reply_markup=_kb())
        await auto_delete(sent, 180)
    except Exception:
        pass

@bot.on_message(filters.command('promoglobal'))
@owner_only
@error_handler
async def promo_global_cmd(client: Client, message: Message):
    arg = message.command[1].lower() if len(message.command) > 1 else 'status'
    if arg in ('on', 'off'):
        await set_global('promo', arg == 'on')
    state = await get_global('promo', True)
    await message.reply(card('Gʟᴏʙᴀʟ Pʀᴏᴍᴏ', f"🌍 <b>Sᴛᴀᴛᴜs :</b> {('🟢 ON' if state else '🔴 OFF')}"), parse_mode=enums.ParseMode.HTML)
