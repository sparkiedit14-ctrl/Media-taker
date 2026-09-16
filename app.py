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

app = FastAPI(title="Media-taker API", version="1.2.0")

allowed_hosts = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
    "instagram.com", "www.instagram.com",
}

origins_raw = os.getenv("FRONTEND_ORIGINS", "*")
origins = [item.strip() for item in origins_raw.split(",") if item.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
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
    }
    if media_type == "mp3":
        ydl_opts = {
            **common,
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }],
        }
    else:
        ydl_opts = {
            **common,
            "format": "bestvideo+bestaudio/best[acodec!=none]",
            "merge_output_format": "mp4",
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        raise RuntimeError(
            "The media could not be downloaded. It may be private, unavailable, restricted, or unsupported."
        ) from exc

    extension = ".mp3" if media_type == "mp3" else ".mp4"
    matches = sorted(
        (path for path in output_dir.glob(f"*{extension}") if path.is_file()),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    if not matches:
        raise RuntimeError("The downloader produced no valid output file.")

    result = matches[0]
    if result.stat().st_size <= 0:
        raise RuntimeError("The downloader produced an empty file.")
    if media_type == "mp4" and not _has_audio_stream(result):
        raise RuntimeError("No audio stream was available, so a silent MP4 was not returned.")
    return result


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
    }


@app.post("/api/download")
async def download_media(request: DownloadRequest):
    return await _download_response(request)


@app.post("/api/convert/mp3")
async def legacy_mp3_download(request: DownloadRequest):
    """Compatibility endpoint for the current frontend's MP3 request."""
    request.type = "mp3"
    return await _download_response(request)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Media-taker backend is running. Use POST /api/download with a YouTube or Instagram URL."}
