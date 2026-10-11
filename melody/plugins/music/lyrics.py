import asyncio
from melody.core.pools import IO_POOL
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from melody import bot
from melody.config import Config
from melody.core.queue import get_current
from utils.decorators import error_handler
from utils.formatters import quote_html

@bot.on_message(filters.command('lyrics') & filters.group)
@error_handler
async def lyrics_cmd(client: Client, message: Message):
    query = ' '.join(message.command[1:])
    if not query:
        current = get_current(message.chat.id)
        if not current:
            await message.reply(quote_html('❌ Nothing is playing. Use `/lyrics <song name>`'), parse_mode=enums.ParseMode.HTML)
            return
        query = current.title
    if not Config.GENIUS_API_TOKEN:
        await message.reply(quote_html('⚠️ Genius API token not configured.'), parse_mode=enums.ParseMode.HTML)
        return
    msg = await message.reply(quote_html(f'🔍 Fetching lyrics for: `{query}`...'), parse_mode=enums.ParseMode.HTML)
    try:
        import lyricsgenius
        genius = lyricsgenius.Genius(Config.GENIUS_API_TOKEN, verbose=False, remove_section_headers=True)
        loop = asyncio.get_running_loop()
        song = await loop.run_in_executor(IO_POOL, lambda: genius.search_song(query))
        if not song:
            await msg.edit(quote_html('❌ Lyrics not found 🌸'), parse_mode=enums.ParseMode.HTML)
            return
        lyrics = song.lyrics[:3500]
        header = f'**🎵 {song.title}** — _{song.artist}_\n\n'
        await msg.edit(quote_html(header) + quote_html(lyrics, expandable=True), parse_mode=enums.ParseMode.HTML)
    except Exception:
        await msg.edit(quote_html('❌ Could not fetch lyrics 🌸'), parse_mode=enums.ParseMode.HTML)
