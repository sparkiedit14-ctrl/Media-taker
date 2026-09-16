# Media-taker

FastAPI backend for downloading permitted YouTube and Instagram media as MP4 video or MP3 audio.

## Run locally

```bash
./run
```

The API runs at `http://localhost:8000`.

## API

- `GET /api/health` checks that the API is running.
- `POST /api/download` accepts JSON such as:

```json
{"url":"https://www.youtube.com/watch?v=...","type":"mp4"}
```

Use `"type":"mp3"` for audio. MP4 downloads are rejected if the resulting file does not contain an audio stream.

## Deployment

The included `Dockerfile` installs both FFmpeg and FFprobe and starts `app:app`. Set `FRONTEND_ORIGINS` to the public HTTPS URL of the frontend instead of `*` when deploying.

On a phone, the frontend must call the public HTTPS backend URL. Do not use `localhost` in the deployed frontend; on a phone, `localhost` means the phone itself.

Only download content you own or are authorized to download, and follow the source platform's terms.
