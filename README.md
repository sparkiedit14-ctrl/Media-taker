# Media-taker

FastAPI backend for downloading permitted YouTube and Instagram media as MP4 video or MP3 audio.

## Run locally

```bash
./run
```

The API runs at `http://localhost:8000`.

## API

- `GET /api/health` checks the API and FFmpeg/FFprobe.
- `POST /api/download` accepts `{"url":"...","type":"mp4"}` or `{"url":"...","type":"mp3"}`.
- `/api/convert/mp3` remains available for compatibility with the current frontend.

MP4 downloads are rejected if the resulting file does not contain an audio stream, so the API does not intentionally return silent MP4 files.

## Deployment

The Dockerfile installs FFmpeg and starts `app:app`. Set `FRONTEND_ORIGINS` to the public HTTPS frontend URL(s), comma-separated, instead of `*` in production.

The current frontend uses relative API URLs. Host the frontend and this API on the same public origin, or update its `backendURL` constant to the deployed API URL. Never use `localhost` in a deployed phone site: on a phone, `localhost` means the phone itself.

Use a server with enough temporary disk space and request time for video processing. Only download content you own or are authorized to download, and follow the source platform's terms.
