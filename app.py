from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask

app = FastAPI(title="Media-taker API", version="1.5.0")

allowed_hosts = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
    "instagram.com", "www.instagram.com",
}

def _env_origins() -> list[str]:
    raw = os.getenv("FRONTEND_ORIGINS", "*")
    return [item.strip() for item in raw.split(",") if item.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_env_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class DownloadRequest(BaseModel):
    url: HttpUrl
    type: Literal["mp4", "mp3"] = "mp4"


def _host_is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        return False
    return host in allowed_hosts or any(host.endswith(f".{allowed}") for allowed in allowed_hosts)


def _cleanup_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _has_audio_stream(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and "audio" in result.stdout.lower()


def _find_downloaded_file(output_dir: Path) -> Path | None:
    files = [
        path for path in output_dir.iterdir()
        if path.is_file() and not path.name.endswith(".part") and path.stat().st_size > 0
    ]
    return max(files, key=lambda path: path.stat().st_size) if files else None


def _convert_to_mp3(source: Path, output_dir: Path) -> Path:
    output = output_dir / "audio.mp3"
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn",
         "-map", "0:a:0", "-codec:a", "libmp3lame", "-q:a", "2", str(output)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown FFmpeg error"
        raise RuntimeError(f"Audio conversion failed: {detail}")
    return output


def _youtube_cookie_file(url: str) -> Path | None:
    """Return an explicitly configured server-side cookie file for YouTube.

    The cookie file is never read from the repository and is never exposed by
    an API response. Set YOUTUBE_COOKIES_FILE to a Render Secret File path.
    """
    host = (urlparse(url).hostname or "").lower()
    if not ("youtube" in host or host == "youtu.be"):
        return None
    configured = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_file():
        raise RuntimeError("The configured YouTube cookie file was not found on the server.")
    if not os.access(path, os.R_OK):
        raise RuntimeError("The configured YouTube cookie file is not readable by the service.")
    return path


def _download_media(url: str, media_type: str, output_dir: Path) -> Path:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("FFmpeg and FFprobe must be installed on the server.")

    common = {
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
    }
    # Use a broad format selector: best available video+audio combo,
    # merged into MP4. A narrow ext=mp4-only selector fails whenever the
    # chosen player client doesn't expose a combined mp4 format.
    ydl_opts = {
        **common,
        "format": "bestaudio/best" if media_type == "mp3" else "bv*+ba/b",
        "merge_output_format": "mp4",
    }

    parsed_host = (urlparse(url).hostname or "").lower()
    if "youtube" in parsed_host or parsed_host == "youtu.be":
        cookie_file = _youtube_cookie_file(url)
        if cookie_file is not None:
            ydl_opts["cookiefile"] = str(cookie_file)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        message = str(exc)
        if "Sign in to confirm" in message or "not a bot" in message:
            raise RuntimeError(
                "YouTube rejected the server request. Check that the server-side cookie file is valid and current."
            ) from exc
        if "Requested format is not available" in message:
            raise RuntimeError(
                "This public post does not expose a compatible downloadable format to the server. Try another public URL."
            ) from exc
        raise RuntimeError(
            "The media could not be downloaded. It may be private, unavailable, restricted, or unsupported."
        ) from exc

    source = _find_downloaded_file(output_dir)
    if source is None:
        raise RuntimeError("The downloader produced no valid output file.")
    if not _has_audio_stream(source):
        raise RuntimeError("The selected media has no audio track, so it cannot be exported with sound.")
    return _convert_to_mp3(source, output_dir) if media_type == "mp3" else source


async def _download_response(request: DownloadRequest):
    url = str(request.url)
    if not _host_is_allowed(url):
        raise HTTPException(status_code=400, detail="Only HTTPS YouTube and Instagram links are supported.")

    output_dir = Path(tempfile.mkdtemp(prefix="media-taker-"))
    try:
        file_path = await asyncio.to_thread(_download_media, url, request.type, output_dir)
        return FileResponse(
            path=file_path,
            media_type="audio/mpeg" if request.type == "mp3" else "video/mp4",
            filename=file_path.name,
            background=BackgroundTask(_cleanup_directory, output_dir),
        )
    except RuntimeError as exc:
        _cleanup_directory(output_dir)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _cleanup_directory(output_dir)
        raise HTTPException(status_code=500, detail="Unexpected download failure.") from exc


@app.exception_handler(HTTPException)
async def http_error_handler(_, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "ffmpeg": "ok" if shutil.which("ffmpeg") else "missing",
        "ffprobe": "ok" if shutil.which("ffprobe") else "missing",
        "version": app.version,
    }


@app.post("/api/download")
async def download_media(request: DownloadRequest):
    return await _download_response(request)


@app.post("/api/convert/mp3")
async def legacy_mp3_download(request: DownloadRequest):
    request.type = "mp3"
    return await _download_response(request)


@app.get("/")
def root():
    index_path = Path(__file__).with_name("Index.html")
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse({
        "message": "Media-taker backend is running. Use POST /api/download with a YouTube or Instagram URL."
    })
    
