FROM python:3.11.9-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render sets $PORT at runtime — don't hardcode 5000
CMD gunicorn -w 1 --threads 4 -b 0.0.0.0:$PORT app:app