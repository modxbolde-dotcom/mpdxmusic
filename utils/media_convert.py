from __future__ import annotations
import asyncio
import json
import os
import shutil
import tempfile
from melody.logging import LOGGER
NATIVE_AUDIO_EXT = {'.mp3', '.m4a', '.aac', '.ogg', '.oga', '.opus', '.flac', '.wav'}
NATIVE_VIDEO_EXT = {'.mp4', '.mov'}
MP4_SAFE_VIDEO = {'h264', 'avc1'}
MP4_SAFE_AUDIO = {'aac', 'mp3'}
_FFMPEG = shutil.which('ffmpeg') or 'ffmpeg'
_FFPROBE = shutil.which('ffprobe') or 'ffprobe'
_CONVERT_TIMEOUT = 900

def _write_bytes(path: str, data: bytes) -> None:
    with open(path, 'wb') as fh:
        fh.write(data)

async def _run(*args: str, timeout: int=_CONVERT_TIMEOUT) -> 'tuple[int, bytes]':
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    return (proc.returncode or 0, out or b'')

async def probe(path: str) -> dict:
    try:
        code, out = await _run(_FFPROBE, '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', path, timeout=60)
        if code != 0:
            return {}
        return json.loads(out.decode('utf-8', 'ignore') or '{}')
    except Exception as exc:
        LOGGER.debug('ffprobe failed for %s: %s', path, exc)
        return {}

def _streams(info: dict, kind: str) -> list:
    return [s for s in info.get('streams', []) if s.get('codec_type') == kind]

async def media_meta(path: str) -> dict:
    info = await probe(path)
    meta = {'duration': 0, 'width': 0, 'height': 0, 'has_video': False}
    try:
        meta['duration'] = int(float(info.get('format', {}).get('duration') or 0))
    except Exception:
        pass
    video = _streams(info, 'video')
    real = [s for s in video if s.get('codec_name') not in ('mjpeg', 'png', 'bmp', 'gif')]
    if real:
        meta['has_video'] = True
        meta['width'] = int(real[0].get('width') or 0)
        meta['height'] = int(real[0].get('height') or 0)
    return meta

def _out_path(src: str, ext: str) -> str:
    base = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(tempfile.gettempdir(), f'{base}_tg{ext}')

async def to_telegram_audio(src: str, title: str='', performer: str='', cover: 'str | None'=None) -> str:
    ext = os.path.splitext(src)[1].lower()
    if ext in NATIVE_AUDIO_EXT and ext != '.opus':
        return src
    dst = _out_path(src, '.mp3')
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return dst
    has_cover = bool(cover and os.path.isfile(cover))
    args = [_FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-i', src]
    if has_cover:
        args += ['-i', cover, '-map', '0:a:0', '-map', '1:v:0', '-c:v', 'mjpeg', '-disposition:v:0', 'attached_pic', '-metadata:s:v', 'title=Album cover']
    else:
        args += ['-map', '0:a:0', '-vn']
    args += ['-c:a', 'libmp3lame', '-b:a', '192k', '-ar', '44100']
    if title:
        args += ['-metadata', f'title={title}']
    if performer:
        args += ['-metadata', f'artist={performer}']
    args += [dst]
    code, out = await _run(*args)
    if code != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        LOGGER.warning('audio convert failed (%s): %s', code, out[-400:].decode('utf-8', 'ignore'))
        return src
    return dst

async def to_telegram_video(src: str) -> str:
    ext = os.path.splitext(src)[1].lower()
    info = await probe(src)
    vcodec = (_streams(info, 'video') or [{}])[0].get('codec_name', '')
    acodec = (_streams(info, 'audio') or [{}])[0].get('codec_name', '')
    if ext in NATIVE_VIDEO_EXT and vcodec in MP4_SAFE_VIDEO:
        return src
    dst = _out_path(src, '.mp4')
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return dst
    copyable = vcodec in MP4_SAFE_VIDEO
    args = [_FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-i', src]
    if copyable:
        args += ['-c:v', 'copy']
    else:
        args += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p']
    if acodec in MP4_SAFE_AUDIO and copyable:
        args += ['-c:a', 'copy']
    else:
        args += ['-c:a', 'aac', '-b:a', '160k']
    args += ['-movflags', '+faststart', dst]
    code, out = await _run(*args)
    if code != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        LOGGER.warning('video convert failed (%s): %s', code, out[-400:].decode('utf-8', 'ignore'))
        return src
    return dst

async def fetch_thumb(url: 'str | None', tag: str) -> 'str | None':
    if not url or not isinstance(url, str) or (not url.startswith('http')):
        return None
    dst = os.path.join(tempfile.gettempdir(), f'melody_thumb_{tag}.jpg')
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return dst
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            raw = resp.content
    except Exception as exc:
        LOGGER.debug('thumbnail fetch failed: %s', exc)
        return None
    tmp = dst + '.src'
    try:
        await asyncio.to_thread(_write_bytes, tmp, raw)
        code, _ = await _run(_FFMPEG, '-y', '-hide_banner', '-loglevel', 'error', '-i', tmp, '-vf', 'scale=320:-2', '-q:v', '5', dst, timeout=60)
        if code != 0 or not os.path.isfile(dst):
            return None
        return dst
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
