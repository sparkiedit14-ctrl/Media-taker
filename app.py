from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask

app = FastAPI(title="Media-taker API", version="1.0.0")

allowed_hosts = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "instagram.com",
    "www.instagram.com",
}

origins_raw = os.getenv("FRONTEND_ORIGINS", "*")
origins = [item.strip() for item in origins_raw.split(",") if item.strip()]
if not origins:
    origins = ["*"]

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
    if not parsed.scheme or parsed.scheme.lower() != "https":
        return False
    if host in allowed_hosts:
        return True
    return any(host.endswith(f".{allowed_host}") for allowed_host in allowed_hosts)

def _cleanup_directory(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass

def _download_media(url: str, media_type: str, output_dir: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed on the server.")

    if media_type == "mp3":
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "restrictfilenames": False,
            "prefer_ffmpeg": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }
            ],
        }
    else:
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "restrictfilenames": False,
            "prefer_ffmpeg": True,
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as exc:
        raise RuntimeError(
            "The media could not be downloaded. It may be private, unavailable, restricted, or unsupported."
        ) from exc

    pattern = "*.mp3" if media_type == "mp3" else "*.mp4"
    matches = sorted(output_dir.glob(pattern), key=lambda p: p.stat().st_size, reverse=True)
    if not matches:
        raise RuntimeError("The downloader produced no valid output file.")
    return matches[0]

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.post("/api/download")
async def download_media(request: DownloadRequest):
    url = str(request.url)
    if not _host_is_allowed(url):
        raise HTTPException(status_code=400, detail="Only HTTPS YouTube and Instagram links are supported.")

    output_dir = Path(tempfile.mkdtemp(prefix="media-taker-"))
    try:
        file_path = await asyncio.to_thread(_download_media, url, request.type, output_dir)
        media_type = "audio/mpeg" if request.type == "mp3" else "video/mp4"
        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=file_path.name,
            background=BackgroundTask(_cleanup_directory, output_dir),
        )
    except RuntimeError as exc:
        _cleanup_directory(output_dir)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        _cleanup_directory(output_dir)
        raise HTTPException(status_code=500, detail="Unexpected download failure.") from exc

@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Media-taker backend is running. Use POST /api/download with a YouTube or Instagram URL."}
