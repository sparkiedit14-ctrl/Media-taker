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

app = FastAPI(title="Media-taker API", version="1.4.0")

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
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and "audio" in result.stdout.lower()


def _find_downloaded_file(output_dir: Path) -> Path | None:
    files = [
        path for path in output_dir.iterdir()
        if path.is_file() and not path.name.endswith(".part") and path.stat().st_size > 0
    ]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_size)


def _convert_to_mp3(source: Path, output_dir: Path) -> Path:
    output = output_dir / f"{source.stem}.mp3"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(source),
            "-vn", "-codec:a", "libmp3lame", "-q:a", "0",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown FFmpeg error"
        raise RuntimeError(f"Audio conversion failed: {detail}")
    return output


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

    if media_type == "mp3":
        # Download the source first and run our own FFmpeg conversion.
        # This avoids yt-dlp's FFmpegExtractAudio codec-probing failure
        # on some Instagram media streams.
        ydl_opts = {
            **common,
            "format": "bestaudio/best",
        }
    else:
        # Prefer a combined MP4 stream containing both video and audio.
        ydl_opts = {
            **common,
            "format": "best[ext=mp4][acodec!=none]/best[acodec!=none]/best",
            "merge_output_format": "mp4",
        }

    parsed_host = (urlparse(url).hostname or "").lower()
    if "youtube" in parsed_host or parsed_host == "youtu.be":
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["android_vr", "web_safari"]}
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        message = str(exc)
        if "Sign in to confirm" in message or "not a bot" in message:
            raise RuntimeError(
                "YouTube did not allow this server request. Only publicly accessible media can be downloaded."
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

    if media_type == "mp3":
        if not _has_audio_stream(source):
            raise RuntimeError("The downloaded media does not contain an audio track.")
        return _convert_to_mp3(source, output_dir)

    if not _has_audio_stream(source):
        raise RuntimeError("The selected media stream contains no audio track. Try another public URL.")
    return source


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
