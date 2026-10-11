import html
from pyrogram import Client, filters, ContinuePropagation
from pyrogram.types import Message
from melody import bot
from melody.logging import log_activity, LOGGER
from utils.tasks import spawn
ALL_COMMANDS = ['start', 'help', 'about', 'ping', 'stats', 'play', 'vplay', 'playforce', 'vplayforce', 'playlist', 'pause', 'resume', 'skip', 's', 'stop', 'queue', 'q', 'np', 'volume', 'mute', 'unmute', 'loop', 'loopall', 'noloop', 'shuffle', 'clearqueue', 'remove', 'seek', 'seekback', 'rewind', 'speed', 'search', 'lyrics', 'autoplay', 'auth', 'unauth', 'authlist', 'ban', 'unban', 'gban', 'ungban', 'broadcast', 'chatlist', 'maintenance', 'reboot', 'restart', 'reload', 'update', 'logs', 'panel', 'activevc', 'setpic', 'delpic', 'eval', 'py', 'shell', 'sh', 'bash', 'exec', 'gcban', 'gcunban', 'kick', 'dkick', 'punch', 'gcmute', 'gcunmute', 'vcmute', 'vcunmute', 'tmute', 'promote', 'fpromote', 'fullpromote', 'demote', 'dpromote', 'purge', 'spurge', 'del', 'delete', 'cleanall', 'clearchat', 'nuke', 'purgeme', 'kickme', 'zombies', 'pin', 'unpin', 'unpinall', 'warn', 'warns', 'resetwarns', 'rmwarns', 'admins', 'adminlist', 'staff', 'report', 'reportuser', 'tagall', 'all', 'mention', 'cancel', 'canceltag', 'stoptag', 'invitelink', 'link', 'info', 'whois', 'lock', 'unlock', 'muteall', 'unmuteall', 'banall', 'unbanall', 'protection', 'guard', 'antispam', 'channelplay', 'cplaylink', 'alive', 'setstartpic', 'setwelcomepic', 'delstartpic', 'welcome', 'greetings', 'greeting', 'goodbye', 'leftmsg', 'left', 'setwelcome', 'setgoodbye', 'resetwelcome', 'clearwelcome', 'resetgoodbye', 'cleargoodbye', 'resetgreetings', 'resetgreeting', 'cleanwelcome', 'cleangoodbye', 'cleanservice', 'cleanservicemsg', 'welcomecard', 'welcomethumb', 'goodbyecard', 'goodbyethumb', 'setwelcomeimage', 'setwelcomepicgc', 'delwelcomeimage', 'rmwelcomeimage', 'welcometest', 'testwelcome', 'goodbyetest', 'testgoodbye', 'rapproveall', 'requestapproveall', 'approveallrequests', 'rdeclineall', 'requestdeclineall', 'declineallrequests', 'rpending', 'joinrequests', 'pendingrequests', 'assistant', 'oa', 'ownerassistant', 'oaset', 'assistantset', 'oareset', 'assistantreset']
CHANNEL_COMMANDS = ['cplay', 'cvplay', 'cpause', 'cresume', 'cskip', 'cs', 'cstop']

async def _safe_delete(message: Message):
    try:
        await message.delete()
    except Exception as exc:
        LOGGER.debug('auto-delete: could not remove message %s: %s', message.id, exc)

@bot.on_message(filters.command(ALL_COMMANDS) & filters.group, group=-1)
async def _delete_and_log_command(client: Client, message: Message):
    spawn(_safe_delete(message))
    user = message.from_user
    chat = message.chat
    user_label = f"{html.escape(user.first_name or 'Unknown')} (<code>{user.id}</code>)" if user else 'Unknown'
    chat_label = f"{html.escape(chat.title or 'Private')} (<code>{chat.id}</code>)"
    cmd_text = html.escape(message.text or '')
    spawn(log_activity(f'🧾 <b>Command</b>\n• Cmd: <code>{cmd_text}</code>\n• User: {user_label}\n• Chat: {chat_label}'))
    raise ContinuePropagation
