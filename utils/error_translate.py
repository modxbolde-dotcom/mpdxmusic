from __future__ import annotations
import asyncio
import html
_assistant_cache: 'dict[str, object]' = {}

async def assistant_identity() -> 'tuple[int | None, str | None, str]':
    try:
        from melody import assistant
        if assistant is None:
            return (None, None, 'assistant')
        me = getattr(assistant, 'me', None)
        if me is None:
            me = await asyncio.wait_for(assistant.get_me(), timeout=8)
        uname = getattr(me, 'username', None)
        display = f'@{uname}' if uname else getattr(me, 'first_name', None) or 'assistant'
        _assistant_cache.update({'id': me.id, 'username': uname, 'display': display})
        return (me.id, uname, display)
    except Exception:
        return (_assistant_cache.get('id'), _assistant_cache.get('username'), str(_assistant_cache.get('display') or 'assistant'))

def _blob(exc: BaseException) -> str:
    return f'{type(exc).__name__} {exc}'.upper()
_RULES: 'list[tuple[tuple[str, ...], str, str]]' = [(('CHATWRITEFORBIDDEN', 'CHAT_WRITE_FORBIDDEN', 'USERBANNEDINCHANNEL', 'USER_BANNED_IN_CHANNEL', 'CHAT_SEND_PLAIN_FORBIDDEN'), 'The bot/assistant account is muted or write-restricted in this chat, so Telegram refused the message.', '🔇 <b>Mujhe iss group me likhne ki permission nahi hai.</b>\nAdmins please mute/restriction hata do ya mujhe admin bana do — uske baad sab commands turant kaam karengi.'), (('USERKICKED', 'USER_KICKED', 'USERBLOCKED', 'CHANNELPRIVATE', 'CHANNEL_PRIVATE', 'USERNOTPARTICIPANT', 'USER_NOT_PARTICIPANT', 'USER_ALREADY_PARTICIPANT_BANNED'), 'The assistant account cannot stay in / rejoin this chat because it is banned or removed.', ''), (('CHATADMINREQUIRED', 'CHAT_ADMIN_REQUIRED', 'ADMIN_RIGHTS', 'RIGHT_FORBIDDEN'), 'Telegram needs admin rights for this action and the bot does not have them.', '🛡 <b>Iske liye mujhe admin banna padega.</b>\nGroup settings → Administrators → mujhe add karo (invite users + manage video chats permission ke saath). Phir dubara try karo.'), (('INVITEHASHEXPIRED', 'INVITE_HASH_EXPIRED', 'INVITEREQUESTSENT', 'INVITE_REQUEST_SENT'), "The assistant's join request is pending approval, so it could not enter the voice chat.", '📨 <b>Mera assistant join request bhej chuka hai.</b>\nKisi admin se request approve karwa do — ya group ko public karke dubara <code>/play</code> chalao.'), (('NOACTIVEGROUPCALL', 'NO_ACTIVE_GROUP_CALL', 'GROUPCALLINVALID', 'GROUP_CALL_INVALID'), 'There is no live voice chat in the group, so nothing can be played.', '🎙 <b>Voice chat band hai.</b>\nPehle group me voice chat start karo, uske baad <code>/play</code> bhejo.'), (('PARTICIPANTJOINMISSING', 'JOIN_AS_PEER_INVALID', 'GROUPCALLFULL', 'GROUP_CALL_FULL'), 'The voice chat is full or the assistant lost its seat in the call.', '🎧 <b>Voice chat full hai ya mera seat chala gaya.</b>\nEk slot khaali karke <code>/play</code> dubara bhejo.'), (('FLOODWAIT', 'FLOOD_WAIT', 'SLOWMODEWAIT', 'SLOWMODE_WAIT'), 'Telegram rate-limited the bot (too many requests / slow mode) and asked it to wait.', '⏳ <b>Telegram ne thoda rukne ko kaha hai.</b>\nKuch second wait karo, main khud dubara try kar lunga.'), (('PEERIDINVALID', 'PEER_ID_INVALID', 'USERNAMENOTOCCUPIED', 'USERNAME_NOT_OCCUPIED', 'USERNAMEINVALID', 'PEERFLOOD'), 'Telegram could not resolve that user/chat — usually the person never started the bot or the username is wrong.', '🔍 <b>Ye user/chat mujhe mil nahi raha.</b>\nUsername check karo, ya us user se ek baar mujhe <code>/start</code> bhejne ko kaho — phir sab chalega.'), (('MESSAGENOTMODIFIED', 'MESSAGE_NOT_MODIFIED', 'MESSAGEIDINVALID', 'MESSAGE_ID_INVALID', 'MESSAGEDELETEFORBIDDEN', 'QUERYIDINVALID', 'QUERY_ID_INVALID'), 'The message the bot tried to edit/delete is gone, unchanged, or the button expired.', '♻️ <b>Ye message purana ho gaya hai.</b>\nNaya command bhejo — main fresh card bana dunga.'), (('SERVERSELECTIONTIMEOUT', 'PYMONGO', 'MONGO', 'AUTOREconnect'.upper(), 'NETWORKTIMEOUT', 'OPERATIONFAILURE'), 'The database was unreachable or too slow, so the bot could not read/save data.', '🗄 <b>Mera database ek pal ke liye reply nahi kar raha.</b>\n10-15 second baad dubara try karo — data safe hai.'), (('TIMEOUT', 'CONNECTIONERROR', 'CLIENTCONNECTOR', 'SSL', 'DNS', 'TEMPORARYFAILURE', 'CONNECTIONRESET', 'READTIMEOUT'), 'A network call (Telegram or YouTube) timed out or the connection dropped.', '🌐 <b>Network ne dhoka de diya.</b>\nEk baar phir command bhejo — turant chal jayega.'), (('DOWNLOADERROR', 'YT_DLP', 'YTDLP', 'UNAVAILABLEVIDEO', 'SIGN IN TO CONFIRM', 'VIDEO UNAVAILABLE', 'PRIVATE VIDEO', 'AGE', 'COPYRIGHT', 'GEO'), 'YouTube refused this track (private / age-gated / region blocked / bot check).', '🚫 <b>Ye track YouTube se nahi mil raha</b> (private, age-restricted ya region blocked).\nDusra naam ya link try karo — baaki gaane theek chalenge.'), (('FILENOTFOUND', 'NOSUCHFILE', 'PERMISSIONERROR', 'OSERROR', 'NOSPACE', 'DISK'), 'A file the bot needed was missing or the disk/permissions blocked it.', '📁 <b>Ek file load nahi ho payi.</b>\nCommand dubara bhejo — na chale to owner ko batao, wo ek restart me theek ho jayega.'), (('KEYERROR', 'INDEXERROR', 'TYPEERROR', 'VALUEERROR', 'ATTRIBUTEERROR', 'NONETYPE'), "Unexpected/missing data inside the bot's own logic (a value was not in the shape the code expected).", '🧩 <b>Command adhoori info ke saath aayi.</b>\nSahi format me dubara bhejo (jaise <code>/play song name</code>) — main baaki sab sambhal lunga.')]
_FALLBACK_HUMAN = 'An unexpected problem interrupted this command.'
_FALLBACK_USER = '🌸 <b>Ye command abhi puri nahi ho payi.</b>\nThoda ruk kar dubara bhejo — problem meri side ki hai, aur mere owner ko iski poori detail already mil chuki hai.'

def _assistant_ban_text(a_id: 'int | None', a_display: str) -> str:
    id_part = f'<code>{a_id}</code>' if a_id else '<i>(id load nahi hui)</i>'
    cmd = f'<code>/unban {a_id}</code>' if a_id else '<code>/unban &lt;assistant id&gt;</code>'
    return f'🎧 <b>Music player iss group me ban hai.</b>\n\nMera voice-chat assistant <b>{html.escape(a_display)}</b> ({id_part}) yahan banned/removed hai, isliye gaana bajana mumkin nahi hai.\n\n🙏 <b>Admins, ek chhota sa favour :</b> ye account koi random user nahi — yahi meri awaaz hai. Ise unban karte hi group me 24×7 free music, HD quality aur instant controls wapas chalu ho jayenge.\n\n✅ <b>Bas ye command bhej do :</b> {cmd}\n➕ Ya manually: Group → Removed/Banned users → assistant → <b>Unban</b>, phir <code>/play</code> dubara bhejo.\n\n<i>Assistant sirf voice chat me gaana bajata hai — na messages padhta hai, na kisi ko disturb karta hai.</i>'

async def translate(exc: BaseException) -> 'dict[str, str]':
    blob = _blob(exc)
    for idx, (needles, human, advice) in enumerate(_RULES):
        if any((n in blob for n in needles)):
            if idx == 1 or 'USERKICKED' in blob or 'USER_KICKED' in blob:
                a_id, _u, a_display = await assistant_identity()
                advice = _assistant_ban_text(a_id, a_display)
            return {'human': human, 'user': advice or _FALLBACK_USER, 'tag': type(exc).__name__.lower()}
    return {'human': _FALLBACK_HUMAN, 'user': _FALLBACK_USER, 'tag': type(exc).__name__.lower()}

def human_only(exc: BaseException) -> str:
    blob = _blob(exc)
    for needles, human, _advice in _RULES:
        if any((n in blob for n in needles)):
            return human
    return _FALLBACK_HUMAN
