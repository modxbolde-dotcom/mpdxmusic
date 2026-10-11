from utils.branding import BOT_NAME
import io
import os
import time
import aiohttp
from PIL import Image, ImageDraw, ImageFont
ASSETS = os.path.join(os.path.dirname(__file__), '..', 'assets')
FONTS = os.path.join(ASSETS, 'fonts')
SAFFRON = '#FF6600'
GOLD = '#FFD700'
GREEN = '#009246'
WHITE = '#FFFFFF'
DARK = '#1A0500'
CHAKRA_BLUE = '#000080'
_bot_dp_cache: dict = {'path': None, 'tried': False}
_SYSTEM_FONT_FALLBACKS = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', '/nix/store/*/share/fonts/**/DejaVuSans-Bold.ttf']

def _get_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = os.path.join(FONTS, name)
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        pass
    for fallback in _SYSTEM_FONT_FALLBACKS:
        try:
            if '*' in fallback:
                import glob
                matches = glob.glob(fallback, recursive=True)
                if not matches:
                    continue
                fallback = matches[0]
            return ImageFont.truetype(fallback, size)
        except Exception:
            continue
    return ImageFont.load_default()

async def _fetch_image_from_url(url: str) -> 'Image.Image | None':
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(io.BytesIO(data)).convert('RGBA')
    except Exception:
        pass
    return None

def _load_image_any(source: str) -> 'Image.Image | None':
    try:
        if source and os.path.exists(source):
            return Image.open(source).convert('RGBA')
    except Exception:
        pass
    return None

async def fetch_dp(client, user_id: int) -> 'str | None':
    try:
        photo = None
        async for p in client.get_chat_photos(user_id, limit=1):
            photo = p
            break
        if not photo:
            return None
        dest = f'/tmp/melody_dp_{user_id}_{int(time.time() * 1000)}.jpg'
        path = await client.download_media(photo.file_id, file_name=dest)
        return path
    except Exception:
        return None

async def get_bot_dp(client) -> 'str | None':
    if _bot_dp_cache['tried']:
        return _bot_dp_cache['path']
    _bot_dp_cache['tried'] = True
    try:
        me = await client.get_me()
        path = await fetch_dp(client, me.id)
        _bot_dp_cache['path'] = path
        return path
    except Exception:
        return None
_bot_identity_cache: dict = {'username': None, 'name': None, 'tried': False}

async def get_bot_identity(client) -> tuple[str | None, str]:
    live_me = getattr(client, 'me', None)
    if live_me is not None:
        live_username = getattr(live_me, 'username', None)
        live_name = getattr(live_me, 'first_name', None) or f'{BOT_NAME}'
        if not _bot_identity_cache['tried'] or live_username != _bot_identity_cache['username'] or live_name != _bot_identity_cache['name']:
            _bot_identity_cache.update({'username': live_username, 'name': live_name, 'tried': True})
        return (_bot_identity_cache['username'], _bot_identity_cache['name'])
    if _bot_identity_cache['tried']:
        return (_bot_identity_cache['username'], _bot_identity_cache['name'])
    _bot_identity_cache['tried'] = True
    try:
        me = await client.get_me()
        _bot_identity_cache['username'] = getattr(me, 'username', None)
        _bot_identity_cache['name'] = getattr(me, 'first_name', None) or f'{BOT_NAME}'
    except Exception:
        _bot_identity_cache['name'] = f'{BOT_NAME}'
    return (_bot_identity_cache['username'], _bot_identity_cache['name'])

def _circle_crop(img: 'Image.Image', size: int, ring_color=None, ring_width: int=0) -> 'Image.Image':
    img = img.convert('RGBA').resize((size, size), Image.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    output = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    output.paste(img, (0, 0), mask)
    if ring_color and ring_width > 0:
        canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        canvas.paste(output, (0, 0), output)
        ring_draw = ImageDraw.Draw(canvas)
        ring_draw.ellipse((ring_width // 2, ring_width // 2, size - ring_width // 2, size - ring_width // 2), outline=ring_color, width=ring_width)
        return canvas
    return output

def _dual_ring_crop(img: 'Image.Image', size: int) -> 'Image.Image':
    img = img.convert('RGBA').resize((size, size), Image.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    output = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    output.paste(img, (0, 0), mask)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.paste(output, (0, 0), output)
    ring_width = max(4, size // 26)
    ring_draw = ImageDraw.Draw(canvas)
    box = (ring_width // 2, ring_width // 2, size - ring_width // 2, size - ring_width // 2)
    ring_draw.arc(box, start=225, end=45, fill=SAFFRON, width=ring_width)
    ring_draw.arc(box, start=45, end=225, fill=GREEN, width=ring_width)
    return canvas

def _placeholder_avatar(size: int) -> 'Image.Image':
    img = Image.new('RGBA', (size, size), DARK)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        ratio = y / size
        r = int(int(SAFFRON[1:3], 16) * (1 - ratio) + int(GREEN[1:3], 16) * ratio)
        g = int(int(SAFFRON[3:5], 16) * (1 - ratio) + int(GREEN[3:5], 16) * ratio)
        b = int(int(SAFFRON[5:7], 16) * (1 - ratio) + int(GREEN[5:7], 16) * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))
    note_w = size * 0.34
    stem_x = size * 0.5 + note_w * 0.32
    stem_top = size * 0.28
    stem_bottom = size * 0.68
    draw.line([(stem_x, stem_top), (stem_x, stem_bottom)], fill=WHITE, width=max(3, int(size * 0.035)))
    draw.polygon([(stem_x, stem_top), (stem_x + note_w * 0.3, stem_top + note_w * 0.12), (stem_x + note_w * 0.3, stem_top + note_w * 0.32), (stem_x, stem_top + note_w * 0.22)], fill=WHITE)
    head_r = size * 0.11
    draw.ellipse([(stem_x - head_r * 2, stem_bottom - head_r), (stem_x, stem_bottom + head_r)], fill=WHITE)
    return img

def _initials_avatar(name: str, size: int, color: str) -> 'Image.Image':
    img = Image.new('RGBA', (size, size), color)
    d = ImageDraw.Draw(img)
    letter = (name or '?').strip()[:1].upper() or '?'
    f = _get_font('Poppins-Bold.ttf', int(size * 0.45))
    bbox = d.textbbox((0, 0), letter, font=f)
    tw, th = (bbox[2] - bbox[0], bbox[3] - bbox[1])
    d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), letter, font=f, fill=WHITE)
    return img

def _truncate_to_width(draw: 'ImageDraw.ImageDraw', text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    ellipsis = '…'
    while text and draw.textlength(text + ellipsis, font=font) > max_w:
        text = text[:-1]
    return text + ellipsis

def _draw_control_icon(draw: 'ImageDraw.ImageDraw', kind: str, cx: float, cy: float, size: int, color) -> None:
    h = size / 2
    if kind == 'playpause':
        draw.polygon([(cx - h * 0.6, cy - h), (cx - h * 0.6, cy + h), (cx + h * 0.9, cy)], fill=color)
    elif kind == 'prev':
        draw.polygon([(cx + h * 0.2, cy - h), (cx + h * 0.2, cy + h), (cx - h * 0.9, cy)], fill=color)
        draw.rectangle([(cx - h * 1.1, cy - h), (cx - h * 0.85, cy + h)], fill=color)
    elif kind == 'next':
        draw.polygon([(cx - h * 0.2, cy - h), (cx - h * 0.2, cy + h), (cx + h * 0.9, cy)], fill=color)
        draw.rectangle([(cx + h * 0.85, cy - h), (cx + h * 1.1, cy + h)], fill=color)
    elif kind == 'shuffle':
        draw.line([(cx - h, cy - h * 0.5), (cx + h, cy + h * 0.5)], fill=color, width=2)
        draw.line([(cx - h, cy + h * 0.5), (cx + h, cy - h * 0.5)], fill=color, width=2)
        for sx, sy in ((cx + h, cy + h * 0.5), (cx + h, cy - h * 0.5)):
            draw.polygon([(sx, sy - 4), (sx, sy + 4), (sx + 6, sy)], fill=color)
    elif kind == 'repeat':
        bbox = [cx - h, cy - h, cx + h, cy + h]
        draw.arc(bbox, start=20, end=340, fill=color, width=2)
        ax = cx + h * 0.6
        ay = cy - h * 0.75
        draw.polygon([(ax - 6, ay - 5), (ax - 6, ay + 5), (ax + 5, ay)], fill=color)

def _blurred_backdrop(cover: 'Image.Image | None', w: int, h: int) -> 'Image.Image':
    from PIL import ImageFilter
    if cover is None:
        cover = _placeholder_avatar(max(w, h))
    src = cover.convert('RGB')
    src_ratio = src.width / src.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_h = h
        new_w = int(h * src_ratio)
    else:
        new_w = w
        new_h = int(w / src_ratio)
    src = src.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    src = src.crop((left, top, left + w, top + h))
    src = src.filter(ImageFilter.GaussianBlur(18))
    dark = Image.new('RGB', (w, h), (0, 0, 0))
    backdrop = Image.blend(src, dark, 0.55)
    return backdrop.convert('RGBA')

async def make_thumbnail(song_title: str, artist: str, duration: str, requester_name: str, group_name: str, owner_name: str, yt_thumbnail_url: str, requester_dp_path: str=None, bot_dp_path: str=None) -> str:
    cover = await _fetch_image_from_url(yt_thumbnail_url)
    import asyncio as _asyncio
    return await _asyncio.get_running_loop().run_in_executor(None, _render_thumbnail_sync_professional, song_title, artist, duration, requester_name, group_name, owner_name, cover, requester_dp_path, bot_dp_path)

def _render_thumbnail_sync(song_title: str, artist: str, duration: str, requester_name: str, group_name: str, owner_name: str, cover, requester_dp_path: str=None, bot_dp_path: str=None) -> str:
    W, H = (1280, 720)
    PAD = 56
    bg = _blurred_backdrop(cover, W, H)
    edge = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    edge_draw = ImageDraw.Draw(edge)
    edge_draw.rectangle([(0, 0), (W, 8)], fill=SAFFRON)
    edge_draw.rectangle([(0, H - 8), (W, H)], fill=GREEN)
    bg = Image.alpha_composite(bg, edge)
    draw = ImageDraw.Draw(bg)
    font_title = _get_font('Poppins-Bold.ttf', 48)
    font_channel = _get_font('Poppins-Bold.ttf', 30)
    font_meta = _get_font('Poppins-Regular.ttf', 24)
    font_small = _get_font('Poppins-Regular.ttf', 20)
    thumb_size = int(H * 0.52)
    cover_circle = _dual_ring_crop(cover if cover else _placeholder_avatar(thumb_size), thumb_size)
    thumb_x = PAD
    thumb_y = (H - thumb_size) // 2
    bg.paste(cover_circle, (thumb_x, thumb_y), cover_circle)
    info_x = thumb_x + thumb_size + 52
    info_w = W - info_x - PAD
    block_top = thumb_y + int(thumb_size * 0.06)
    title_y = block_top
    safe_title = _truncate_to_width(draw, song_title, font_title, info_w)
    draw.text((info_x, title_y), safe_title, font=font_title, fill=WHITE)
    channel_y = title_y + 66
    safe_channel = _truncate_to_width(draw, artist, font_channel, info_w)
    draw.text((info_x, channel_y), safe_channel, font=font_channel, fill=GOLD)
    meta_y = channel_y + 48
    meta_line = f'🏠 {group_name}'
    safe_meta = _truncate_to_width(draw, meta_line, font_meta, info_w)
    draw.text((info_x, meta_y), safe_meta, font=font_meta, fill=GOLD)
    bar_y = meta_y + 56
    bar_h = 12
    bar_w = info_w
    third = bar_w // 3
    draw.rounded_rectangle([(info_x, bar_y), (info_x + bar_w, bar_y + bar_h)], radius=bar_h // 2, fill='#3A3A3A')
    filled_w = int(bar_w * 0.28)
    for i in range(filled_w):
        if i < third:
            r, g, b = (255, int(153 + 100 * (i / third)), int(51 + 150 * (i / third)))
        else:
            r = int(255 - 117 * ((i - third) / max(third, 1)))
            g = int(255 - 119 * ((i - third) / max(third, 1)))
            b = int(255 - 247 * ((i - third) / max(third, 1)))
        draw.line([(info_x + i, bar_y), (info_x + i, bar_y + bar_h)], fill=(max(r, 0), max(g, 0), max(b, 0)))
    knob_x = info_x + filled_w
    draw.ellipse([(knob_x - 10, bar_y - 5), (knob_x + 10, bar_y + bar_h + 5)], fill=GOLD)
    time_y = bar_y + bar_h + 14
    draw.text((info_x, time_y), '00:00', font=font_small, fill='#DDDDDD')
    dur_w = draw.textlength(duration, font=font_small)
    draw.text((info_x + bar_w - dur_w, time_y), duration, font=font_small, fill='#DDDDDD')
    controls_y = time_y + 40
    icon_size = 26
    kinds = ['shuffle', 'prev', 'playpause', 'next', 'repeat']
    gap = info_w / (len(kinds) + 1)
    cy = controls_y + icon_size // 2
    for idx, kind in enumerate(kinds, 1):
        cx = info_x + gap * idx
        color = WHITE if kind in ('prev', 'playpause', 'next') else GOLD
        _draw_control_icon(draw, kind, cx, cy, icon_size, color)
    badge_size = 92
    bot_img = _load_image_any(bot_dp_path) if bot_dp_path else None
    user_img = _load_image_any(requester_dp_path) if requester_dp_path else None
    user_circle = _circle_crop(user_img if user_img else _initials_avatar(requester_name, badge_size, '#8B0000'), badge_size, ring_color=GOLD, ring_width=4)
    badge_x = W - PAD - badge_size
    badge_y = PAD // 2
    bg.paste(user_circle, (badge_x, badge_y), user_circle)
    bot_badge_size = 38
    bot_circle = _circle_crop(bot_img if bot_img else _initials_avatar(f'{BOT_NAME}', bot_badge_size, '#B8860B'), bot_badge_size, ring_color=WHITE, ring_width=2)
    bg.paste(bot_circle, (badge_x + badge_size - bot_badge_size + 8, badge_y + badge_size - bot_badge_size + 8), bot_circle)
    req_label = _truncate_to_width(draw, requester_name, font_small, 180)
    label_w = draw.textlength(req_label, font=font_small)
    draw.text((badge_x - label_w - 14, badge_y + badge_size // 2 - 10), req_label, font=font_small, fill='#EEEEEE')
    wm_draw = ImageDraw.Draw(bg)
    wm_draw.text((PAD, H - PAD // 2 - 10), f'{BOT_NAME} · {owner_name[:18]}', font=font_small, fill=(255, 255, 255, 170))
    out_path = f'/tmp/melody_thumb_{int(time.time() * 1000)}.png'
    bg.convert('RGB').save(out_path, 'PNG')
    return out_path

def _render_thumbnail_sync_professional(song_title: str, artist: str, duration: str, requester_name: str, group_name: str, owner_name: str, cover, requester_dp_path: str=None, bot_dp_path: str=None) -> str:
    from PIL import ImageFilter, ImageOps
    W, H = (1280, 720)
    PAD = 48
    bg = _blurred_backdrop(cover, W, H)
    tint = Image.new('RGBA', (W, H), (7, 13, 27, 88))
    bg = Image.alpha_composite(bg, tint)
    draw = ImageDraw.Draw(bg)
    draw.rectangle((0, 0, W, 7), fill=SAFFRON)
    draw.rectangle((0, H - 7, W, H), fill=GREEN)
    panel = Image.new('RGBA', (W - PAD * 2, H - 130), (10, 18, 36, 224))
    panel = panel.filter(ImageFilter.GaussianBlur(0.2))
    bg.alpha_composite(panel, (PAD, 82))
    draw.rounded_rectangle((PAD, 82, W - PAD, H - 48), radius=34, outline=(255, 255, 255, 42), width=2)
    font_kicker = _get_font('Poppins-Bold.ttf', 20)
    font_title = _get_font('Poppins-Bold.ttf', 52)
    font_artist = _get_font('Poppins-Regular.ttf', 28)
    font_meta = _get_font('Poppins-Regular.ttf', 22)
    font_small = _get_font('Poppins-Regular.ttf', 18)
    font_control = _get_font('Poppins-Bold.ttf', 20)
    draw.rounded_rectangle((PAD + 38, 112, PAD + 220, 150), radius=19, fill=(255, 102, 0, 225))
    draw.text((PAD + 58, 120), 'NOW PLAYING', font=font_kicker, fill=WHITE)
    cover_size = 330
    cover_src = cover if cover else _placeholder_avatar(cover_size)
    cover_src = ImageOps.fit(cover_src.convert('RGB'), (cover_size, cover_size), method=Image.LANCZOS, centering=(0.5, 0.5))
    cover_src = cover_src.filter(ImageFilter.UnsharpMask(radius=1.2, percent=115, threshold=3))
    cover_rgba = cover_src.convert('RGBA')
    cover_mask = Image.new('L', (cover_size, cover_size), 0)
    ImageDraw.Draw(cover_mask).rounded_rectangle((0, 0, cover_size - 1, cover_size - 1), radius=28, fill=255)
    cover_rgba.putalpha(cover_mask)
    cover_x, cover_y = (PAD + 42, 184)
    draw.rounded_rectangle((cover_x - 7, cover_y - 7, cover_x + cover_size + 7, cover_y + cover_size + 7), radius=35, outline=GOLD, width=5)
    bg.alpha_composite(cover_rgba, (cover_x, cover_y))
    info_x = cover_x + cover_size + 54
    info_w = W - PAD - info_x - 42
    title = ' '.join((song_title or 'Unknown track').split())
    title_lines: list[str] = []
    current = ''
    for word in title.split():
        candidate = f'{current} {word}'.strip()
        if current and draw.textlength(candidate, font=font_title) > info_w:
            title_lines.append(current)
            current = word
            if len(title_lines) == 2:
                break
        else:
            current = candidate
    if len(title_lines) < 2 and current:
        title_lines.append(current)
    if len(title_lines) > 2:
        title_lines = title_lines[:2]
    if len(title_lines) == 2 and title_lines[-1] != current and current:
        title_lines[-1] = _truncate_to_width(draw, title_lines[-1], font_title, info_w)
    if not title_lines:
        title_lines = ['Unknown track']
    for idx, line in enumerate(title_lines[:2]):
        draw.text((info_x, 188 + idx * 62), _truncate_to_width(draw, line, font_title, info_w), font=font_title, fill=WHITE)
    artist_y = 188 + min(len(title_lines), 2) * 62 + 10
    draw.text((info_x, artist_y), _truncate_to_width(draw, artist or 'Unknown artist', font_artist, info_w), font=font_artist, fill=GOLD)
    draw.text((info_x, artist_y + 48), _truncate_to_width(draw, group_name or 'Music room', font_meta, info_w), font=font_meta, fill=(220, 226, 239, 235))
    draw.text((info_x, artist_y + 83), _truncate_to_width(draw, f"Requested by {requester_name or 'listener'}", font_small, info_w), font=font_small, fill=(165, 177, 198, 235))
    bar_y = 470
    bar_h = 12
    draw.rounded_rectangle((info_x, bar_y, info_x + info_w, bar_y + bar_h), radius=bar_h // 2, fill=(89, 101, 124, 180))
    filled = max(36, int(info_w * 0.24))
    for x in range(filled):
        ratio = x / max(filled - 1, 1)
        if ratio < 0.5:
            c = (255, int(102 + ratio * 2 * 153), int(0 + ratio * 2 * 215), 255)
        else:
            c = (int(255 - (ratio - 0.5) * 2 * 255), 255, int(215 - (ratio - 0.5) * 2 * 215), 255)
        draw.line((info_x + x, bar_y, info_x + x, bar_y + bar_h), fill=c, width=bar_h)
    draw.ellipse((info_x + filled - 11, bar_y - 5, info_x + filled + 11, bar_y + bar_h + 5), fill=GOLD)
    draw.text((info_x, bar_y + 24), '00:00', font=font_small, fill=(190, 198, 213, 230))
    duration = duration or '--:--'
    dur_w = draw.textlength(duration, font=font_small)
    draw.text((info_x + info_w - dur_w, bar_y + 24), duration, font=font_small, fill=(190, 198, 213, 230))
    controls_y = 572
    control_gap = max(94, min(132, info_w // 6))
    control_kinds = [('shuffle', 'SHUFFLE'), ('prev', 'BACK'), ('playpause', 'PLAY'), ('next', 'NEXT'), ('repeat', 'REPEAT')]
    start_x = info_x + (info_w - control_gap * (len(control_kinds) - 1)) // 2
    for idx, (kind, label) in enumerate(control_kinds):
        cx = start_x + idx * control_gap
        color = GOLD if kind in ('shuffle', 'repeat') else WHITE
        _draw_control_icon(draw, kind, cx, controls_y, 28, color)
        label_w = draw.textlength(label, font=font_control)
        draw.text((cx - label_w / 2, controls_y + 24), label, font=font_control, fill=(160, 174, 196, 230))
    badge_size = 62
    user_img = _load_image_any(requester_dp_path) if requester_dp_path else None
    bot_img = _load_image_any(bot_dp_path) if bot_dp_path else None
    user_circle = _circle_crop(user_img if user_img else _initials_avatar(requester_name, badge_size, '#8B0000'), badge_size, ring_color=GOLD, ring_width=3)
    badge_x, badge_y = (W - PAD - 42 - badge_size, 105)
    bg.paste(user_circle, (badge_x, badge_y), user_circle)
    bot_circle = _circle_crop(bot_img if bot_img else _initials_avatar('M', 28, '#B8860B'), 28, ring_color=WHITE, ring_width=2)
    bg.paste(bot_circle, (badge_x + 42, badge_y + 42), bot_circle)
    watermark = f"{BOT_NAME}  ·  {owner_name or 'Music bot'}"
    draw.text((PAD + 42, H - 36), _truncate_to_width(draw, watermark, font_small, 280), font=font_small, fill=(180, 190, 208, 205))
    out_path = f'/tmp/melody_thumb_{int(time.time() * 1000)}.png'
    bg.convert('RGB').save(out_path, 'PNG', optimize=True)
    return out_path
