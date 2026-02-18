#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# Required
# ---------------------------------------------------------------------------
: "${STREAM_URL:?STREAM_URL is required}"
: "${RTMP_URL:?RTMP_URL is required}"

# ---------------------------------------------------------------------------
# Configurable defaults
# ---------------------------------------------------------------------------
RESOLUTION="${RESOLUTION:-1920x1080}"
FRAMERATE="${FRAMERATE:-30}"
VIDEO_BITRATE="${VIDEO_BITRATE:-4500k}"
AUDIO_BITRATE="${AUDIO_BITRATE:-128k}"

WIDTH="${RESOLUTION%x*}"
HEIGHT="${RESOLUTION#*x}"

echo "=== Web Streamer ==="
echo "  URL:        $STREAM_URL"
echo "  RTMP:       ${RTMP_URL%%/*}//***masked***"
echo "  Resolution: ${WIDTH}x${HEIGHT} @ ${FRAMERATE}fps"
echo "  Video:      ${VIDEO_BITRATE}  Audio: ${AUDIO_BITRATE}"
echo "===================="

# ---------------------------------------------------------------------------
# 1. Virtual display
# ---------------------------------------------------------------------------
echo "[1/4] Starting Xvfb..."
Xvfb :99 -screen 0 "${WIDTH}x${HEIGHT}x24" -ac +extension GLX +render -noreset &
XVFB_PID=$!
export DISPLAY=:99
sleep 1

# ---------------------------------------------------------------------------
# 2. Virtual audio (PulseAudio)
# ---------------------------------------------------------------------------
echo "[2/4] Starting PulseAudio..."
pulseaudio -D --exit-idle-time=-1 --system=false --disallow-module-loading=false 2>/dev/null || true
sleep 1

# ---------------------------------------------------------------------------
# 3. Chromium (kiosk mode, autoplay enabled)
# ---------------------------------------------------------------------------
echo "[3/4] Launching Chromium..."
chromium-browser \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --disable-software-rasterizer \
    --autoplay-policy=no-user-gesture-required \
    --window-size="${WIDTH},${HEIGHT}" \
    --start-maximized \
    --kiosk \
    --no-first-run \
    --disable-translate \
    --disable-infobars \
    --disable-features=TranslateUI \
    --disable-extensions \
    --hide-scrollbars \
    --mute-audio=false \
    "$STREAM_URL" &
CHROME_PID=$!

echo "    Waiting for page to load..."
sleep 8

# ---------------------------------------------------------------------------
# 4. FFmpeg: capture screen + audio → RTMP
# ---------------------------------------------------------------------------
echo "[4/4] Starting FFmpeg → RTMP stream..."
exec ffmpeg \
    -loglevel warning \
    -f x11grab -framerate "$FRAMERATE" -video_size "${WIDTH}x${HEIGHT}" -draw_mouse 0 -i :99 \
    -f pulse -ac 2 -i default \
    -c:v libx264 -preset veryfast -tune zerolatency \
    -b:v "$VIDEO_BITRATE" -maxrate "$VIDEO_BITRATE" -bufsize "$(echo "$VIDEO_BITRATE" | sed 's/k//' | awk '{printf "%dk", $1*2}')" \
    -g "$((FRAMERATE * 2))" \
    -pix_fmt yuv420p \
    -c:a aac -b:a "$AUDIO_BITRATE" -ar 44100 \
    -f flv "$RTMP_URL"
