from pyrogram import Client, filters, enums
from pyrogram.types import Message
from melody import bot
from melody.core.call import set_playback_speed, get_speed
from melody.core.queue import get_current
from utils.decorators import admin_or_auth, error_handler
from utils.formatters import quote_html

@bot.on_message(filters.command('speed') & filters.group)
@error_handler
@admin_or_auth
async def speed_cmd(client: Client, message: Message):
    args = message.command
    if len(args) < 2:
        current_speed = get_speed(message.chat.id)
        await message.reply(quote_html(f'⚡ **Current speed:** `{current_speed}x`\n\n**Usage:** `/speed <0.5-2.0>`\nExample: `/speed 1.5`'), parse_mode=enums.ParseMode.HTML)
        return
    try:
        speed = float(args[1].rstrip('xX'))
    except ValueError:
        await message.reply(quote_html('❌ Invalid speed value. Use a number like `1.5`'), parse_mode=enums.ParseMode.HTML)
        return
    if not 0.5 <= speed <= 2.0:
        await message.reply(quote_html('❌ Speed must be between **0.5** and **2.0**'), parse_mode=enums.ParseMode.HTML)
        return
    if not get_current(message.chat.id):
        await message.reply(quote_html('❌ Nothing is playing right now.'), parse_mode=enums.ParseMode.HTML)
        return
    if await set_playback_speed(message.chat.id, speed):
        await message.reply(quote_html(f'⚡ **Playback speed set to** `{speed}x`'), parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply(quote_html('⚠️ **Speed change nahi ho paya.**\n\nVoice chat me assistant ka connection issue lag raha hai — `/play` dobara try karo.'), parse_mode=enums.ParseMode.HTML)
