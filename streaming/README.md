# Web Streamer

Docker-based service that captures any web page (with audio) and pushes it as an RTMP live stream to YouTube Live or any RTMP-compatible endpoint.

## Prerequisites

- Docker (with Compose v2)

## Quick Start

### Build the image

```bash
cd streaming
docker build -t web-streamer .
```

### Stream a single page

```bash
docker run -d --name my-stream \
  -e STREAM_URL="http://your-server:8000/match/1?autoplay=true" \
  -e RTMP_URL="rtmp://a.rtmp.youtube.com/live2/YOUR_STREAM_KEY" \
  web-streamer
```

### Stream multiple pages

Edit `docker-compose.yml` with your URLs and stream keys, then:

```bash
docker compose up -d            # start all streams
docker compose logs -f stream-1 # tail logs for one stream
docker compose down             # stop all
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `STREAM_URL` | Yes | — | Web page URL to capture |
| `RTMP_URL` | Yes | — | Full RTMP URL including stream key |
| `RESOLUTION` | No | `1920x1080` | Capture resolution (WxH) |
| `FRAMERATE` | No | `30` | Video framerate |
| `VIDEO_BITRATE` | No | `4500k` | H.264 video bitrate |
| `AUDIO_BITRATE` | No | `128k` | AAC audio bitrate |

## How It Works

1. **Xvfb** creates a virtual display (no monitor needed)
2. **PulseAudio** creates a virtual audio sink
3. **Chromium** opens the target URL in kiosk mode with `--autoplay-policy=no-user-gesture-required` so audio plays without user interaction
4. **FFmpeg** captures the virtual display (x11grab) and audio (PulseAudio), encodes to H.264 + AAC, and pushes to the RTMP endpoint

## Tips

- **CricVox auto-play**: Append `?autoplay=true` to the CricVox match URL so commentary plays automatically.
- **Accessing localhost**: When the web app runs on the Docker host, use `http://host.docker.internal:PORT` as the `STREAM_URL`.
- **Resilience**: The compose file uses `restart: unless-stopped` so streams recover from transient failures.
- **Resource usage**: Each stream uses roughly 1.5 CPU cores and 500 MB RAM. A server with 8 vCPU / 8 GB RAM can comfortably run 5 streams.
- **YouTube setup**: Create a live broadcast in YouTube Studio, copy the stream key, and use `rtmp://a.rtmp.youtube.com/live2/YOUR_KEY` as `RTMP_URL`. Set `enableAutoStart` on the broadcast so it goes live when FFmpeg connects.
