from utils.branding import BOT_NAME
import psutil
import time
from pyrogram import Client, filters
from pyrogram.types import Message
from melody import bot
from utils.decorators import error_handler
from utils.formatters import send_quote, premium_emoji, PREMIUM_EMOJI_IDS
from strings.themes import fancy
_start_time = time.time()

@bot.on_message(filters.command('stats'))
@error_handler
async def stats_cmd(client: Client, message: Message):
    uptime_sec = int(time.time() - _start_time)
    hours = uptime_sec // 3600
    minutes = uptime_sec % 3600 // 60
    secs = uptime_sec % 60
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    text = f"<blockquote>{premium_emoji(PREMIUM_EMOJI_IDS['stats'], '📊')} <b>{fancy(f'{BOT_NAME} Statistics')}</b></blockquote>\n\n<blockquote>🕒 <b>Uptime:</b> <code>{hours}h {minutes}m {secs}s</code>\n\n💻 <b>System:</b>\n  🔸 CPU:  <code>{cpu}%</code>\n  🔸 RAM:  <code>{ram.percent}%</code> <i>({ram.used // 1024 // 1024}MB / {ram.total // 1024 // 1024}MB)</i>\n  🔸 Disk: <code>{disk.percent}%</code> <i>({disk.used // 1024 // 1024 // 1024}GB / {disk.total // 1024 // 1024 // 1024}GB)</i></blockquote>"
    await send_quote(message, text, client=client)
