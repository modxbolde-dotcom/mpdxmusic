from __future__ import annotations
from pyrogram.types import ReplyParameters

def reply_params(message_id: 'int | None'):
    if not message_id:
        return None
    try:
        return ReplyParameters(message_id=int(message_id))
    except Exception:
        return None
