FROM python:3.11.9-slim

# ffmpeg: needed for yt-dlp audio extraction / video+audio merging
# curl/unzip: needed to install deno
# deno: JS runtime yt-dlp uses to decrypt YouTube's signature/n-challenges,
#       without which more formats get dropped as SABR-only / unavailable
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    curl -fsSL https://deno.land/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.deno/bin:${PATH}"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade yt-dlp

COPY . .

# -w 1: single worker process so the in-memory JOBS dict stays shared
#       across all requests (2+ workers = separate JOBS dicts = "unknown
#       job id" errors on status polling)
# --threads 4: lets that one worker still handle multiple concurrent
#       HTTP requests (status polling, file downloads) without blocking
# Render injects $PORT at runtime — don't hardcode a port number
CMD gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT app:app