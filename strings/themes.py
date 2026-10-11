_FANCY_MAP: dict[str, str] = {}
for _i, _c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
    _FANCY_MAP[_c] = chr(119912 + _i)
for _i, _c in enumerate('abcdefghijklmnopqrstuvwxyz'):
    _FANCY_MAP[_c] = chr(119938 + _i)
for _i, _c in enumerate('0123456789'):
    _FANCY_MAP[_c] = chr(120782 + _i)

def fancy(text: str) -> str:
    return ''.join((_FANCY_MAP.get(ch, ch) for ch in text))
BLUE = 'blue'
RED = 'red'
GREEN = 'green'

def btn(label: str, color: str=None) -> str:
    return label
COLORS = {'saffron': '#FF6600', 'gold': '#FFD700', 'green': '#009246', 'red': '#CE2B37', 'white': '#FFFFFF', 'dark': '#1A0500'}
E = {'play': '▶️', 'pause': '⏸', 'skip': '⏭', 'stop': '⏹', 'loop': '🔁', 'shuffle': '🔀', 'vol_up': '🔊', 'vol_dn': '🔉', 'mute': '🔇', 'lyrics': '🎵', 'queue': '📋', 'search': '🔍', 'owner': '♛', 'group': '🏛', 'user': '🙋', 'bot': '🎶', 'error': '❌', 'ok': '✅', 'warn': '⚠️', 'info': 'ℹ️', 'fire': '🔥', 'star': '⭐', 'crown': '👑'}
STRINGS = {'error_generic': 'Something went wrong 🌸', 'not_in_vc': 'Voice chat band hai, pehle VC kholo 📢', 'no_song': 'Song nahi mili 🌸', 'queue_empty': 'Queue empty hai 🎵', 'nothing_playing': 'Kuch nahi chal raha abhi ❌', 'too_long': 'Song bahut lamba hai! Max {max} allowed ⏱', 'banned': 'Aap banned hain is bot se 🚫', 'admin_only': 'Sirf admins ya authorized users use kar sakte hain ⚠️'}
