import html
import os
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from melody import bot
from utils.decorators import owner_only, error_handler
from utils.formatters import quote_html
from utils.picture_assets import save_picture_file_id, schedule_picture_backup
ASSETS = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'assets')
BG_START = os.path.join(ASSETS, 'bg_start.png')
GH_START_PATH = 'assets/bg_start.png'

def _ensure_assets():
    os.makedirs(ASSETS, exist_ok=True)

@bot.on_message(filters.command('setpic') & filters.private)
@owner_only
@error_handler
async def setpic_cmd(client: Client, message: Message):
    photo = None
    if message.photo:
        photo = message.photo
    if not photo and message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo
    if not photo:
        return await message.reply(quote_html('🖼️ <b>Set Start Picture</b>\n\nSend a photo with caption <code>/setpic</code>\nor reply to a photo with <code>/setpic</code>'), parse_mode=enums.ParseMode.HTML)
    msg = await message.reply(quote_html('⏳ Saving picture...'), parse_mode=enums.ParseMode.HTML)
    try:
        await save_picture_file_id('start', photo.file_id)
    except Exception as e:
        await msg.edit(quote_html(f'❌ <b>Failed to save image.</b>\n<code>{html.escape(str(e))}</code>'), parse_mode=enums.ParseMode.HTML)
        return
    schedule_picture_backup(client, key='start', file_id=photo.file_id, local_path=BG_START, mongo_key=GH_START_PATH)
    await msg.edit(quote_html('✅ <b>Start picture updated!</b>\n\nThe new image will be shown on the next <code>/start</code> command.\n📦 Background backup bhi start ho gaya hai.'), parse_mode=enums.ParseMode.HTML)

@bot.on_message(filters.command('delpic') & filters.private)
@owner_only
@error_handler
async def delpic_cmd(client: Client, message: Message):
    if os.path.exists(BG_START):
        os.remove(BG_START)
        await message.reply(quote_html('🗑️ <b>Start picture removed.</b>\nThe bot will now use text-only start messages.'), parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply(quote_html('ℹ️ No custom start picture is set.'), parse_mode=enums.ParseMode.HTML)
