"""Video conversion with ffmpeg (async subprocess, resource-guarded).

- video -> video_note : center-crop to square, 640x640, H.264 + AAC, <= 60s
- video_note -> video : re-encode to a regular H.264 MP4 (even dimensions)

Keeps CPU/RAM low for free hosting: `veryfast` preset, limited threads,
per-conversion timeout, and strict input validation before heavy work.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil

from config import settings

log = logging.getLogger(__name__)


class ConvertError(Exception):
    """Raised when probing or converting a video fails (user-safe message)."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


async def _run(cmd: list[str], timeout: int) -> str:
    """Run a command asynchronously, return stderr on failure wrapped."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise ConvertError("⏳ تبدیل خیلی طول کشید؛ لطفاً ویدیوی کوتاه‌تری بفرست.")
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", "ignore")[-600:]
            log.error("ffmpeg failed (rc=%s): %s", proc.returncode, tail)
            raise ConvertError(
                "❌ نتونستم این ویدیو رو تبدیل کنم. مطمئن شو فایل سالمه و دوباره تلاش کن."
            )
        return (stderr or b"").decode("utf-8", "ignore")
    except FileNotFoundError:
        raise ConvertError("❌ موتور تبدیل ویدیو روی سرور نصب نیست. (ffmpeg)")


async def _probe_with_ffmpeg(path: str) -> dict:
    """Fallback probe via `ffmpeg -i` when ffprobe is not installed."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise ConvertError("❌ نتونستم اطلاعات ویدیو رو بخونم.")
    text = (stderr or b"").decode("utf-8", "ignore")
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", text)
    if not match:
        raise ConvertError("❌ فایل ویدیویی معتبر نیست.")
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    width = height = 0
    m = re.search(r"Video:.*?[,\s](\d{2,5})x(\d{2,5})", text)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
    return {"duration": duration, "size": size, "width": width, "height": height}


async def probe(path: str) -> dict:
    """Return {'duration', 'size', 'width', 'height'} for a media file."""
    if not ffprobe_available():
        if ffmpeg_available():
            return await _probe_with_ffmpeg(path)
        raise ConvertError("❌ موتور تبدیل ویدیو روی سرور نصب نیست. (ffmpeg)")
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size:stream=width,height",
        "-select_streams", "v:0",
        "-of", "json", path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise ConvertError("❌ نتونستم اطلاعات ویدیو رو بخونم.")
    try:
        data = json.loads((stdout or b"{}").decode("utf-8", "ignore"))
        fmt = data.get("format", {})
        streams = data.get("streams") or [{}]
        stream = streams[0] if streams else {}
        return {
            "duration": float(fmt.get("duration") or 0),
            "size": int(float(fmt.get("size") or 0)),
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
        }
    except (ValueError, TypeError, AttributeError):
        raise ConvertError("❌ فایل ویدیویی معتبر نیست.")


async def to_video_note(src: str, dst: str) -> dict:
    """Convert a regular video to a Telegram round video-note (MP4).

    Returns probe info of the *output* file.
    """
    max_sec = settings.note_max_seconds
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-t", str(max_sec),          # video-notes are max ~60s; trim the rest
        "-map", "0:v:0", "-map", "0:a?",
        "-vf",
        "crop='min(iw,ih)':'min(iw,ih)',"
        "scale=640:640,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "1",
        "-threads", "2",             # be gentle on free-tier CPUs
        "-movflags", "+faststart",
        dst,
    ]
    await _run(cmd, timeout=settings.ffmpeg_timeout)
    return await probe(dst)


def _delogo_filter(info: dict) -> str:
    """Build an ffmpeg delogo filter from settings.note_delogo (percent based).

    NOTE_DELOGO is "x,y,w,h" in percents of the frame (0-100).
    Empty/invalid value disables watermark removal.
    """
    raw = (settings.note_delogo or "").strip()
    if not raw:
        return ""
    try:
        px, py, pw, ph = [float(part) for part in raw.split(",")]
    except (ValueError, TypeError):
        return ""
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if width <= 0 or height <= 0:
        return ""
    x = max(1, min(width - 3, int(width * px / 100)))
    y = max(1, min(height - 3, int(height * py / 100)))
    w = max(2, min(width - x - 1, int(width * pw / 100)))
    h = max(2, min(height - y - 1, int(height * ph / 100)))
    if w < 2 or h < 2:
        return ""
    return f"delogo=x={x}:y={y}:w={w}:h={h}"


def _normal_video_filters(info: dict) -> str:
    filters = []
    delogo = _delogo_filter(info)
    if delogo:
        filters.append(delogo)
    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    filters.append("format=yuv420p")
    return ",".join(filters)


async def to_normal_video(src: str, dst: str) -> dict:
    """Convert a round video-note to a regular (rectangular-container) video.

    The pixels stay square (that's the source), but the file becomes a normal
    H.264 video message instead of a round video-note.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", _normal_video_filters(await probe(src)),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-threads", "2",
        "-movflags", "+faststart",
        dst,
    ]
    await _run(cmd, timeout=settings.ffmpeg_timeout)
    return await probe(dst)
