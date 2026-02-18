FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    pulseaudio \
    chromium-browser \
    ffmpeg \
    fonts-noto \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    dbus \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
