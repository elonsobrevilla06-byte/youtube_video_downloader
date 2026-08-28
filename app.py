#!/usr/bin/env python3
"""
Flask backend for the YouTube Downloader web app.

Requirements:
    pip install flask yt-dlp
    ffmpeg must be installed and on PATH (for audio extraction / merging).

Run:
    python app.py
Then open:
    http://127.0.0.1:5000
"""

import os
import re
import uuid
import threading
import time
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, Response

import yt_dlp

app = Flask(__name__)

# Domains we knowingly support. yt-dlp itself supports hundreds of sites,
# but we deliberately restrict the UI to these so users get a clear,
# friendly error instead of a confusing yt-dlp stack trace for something
# like a random news site or a private/members-only page.
SUPPORTED_DOMAINS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "m.youtube.com": "YouTube",
    "instagram.com": "Instagram",
    "www.instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "www.facebook.com": "Facebook",
    "m.facebook.com": "Facebook",
    "web.facebook.com": "Facebook",
    "fb.watch": "Facebook",
}


def detect_platform(url: str):
    """Returns the platform name if url's host is supported, else None."""
    try:
        host = urlparse(url).netloc.lower()
        host = re.sub(r"^www\.", "", host) if host not in SUPPORTED_DOMAINS else host
    except Exception:
        return None
    return SUPPORTED_DOMAINS.get(host) or SUPPORTED_DOMAINS.get(re.sub(r"^www\.", "", host))

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Render mounts Secret Files as read-only. yt-dlp needs to WRITE to the
# cookie file (to save rotated/refreshed cookies), so copy it into a
# writable location once at startup and use that copy instead.
import shutil

COOKIE_SOURCE = "/etc/secrets/cookies.txt"
COOKIE_PATH = os.path.join(DOWNLOAD_DIR, "cookies.txt")

if os.path.exists(COOKIE_SOURCE):
    shutil.copy(COOKIE_SOURCE, COOKIE_PATH)

# Safety net: if a file finishes but is never actually downloaded (user
# closes the tab, etc.), remove it after this long so it doesn't sit
# on the server forever.
ABANDONED_FILE_CLEANUP_SECONDS = 30 * 60  # 30 minutes

# In-memory job store: {job_id: {status, percent, filename, error, title}}
JOBS = {}


def schedule_cleanup(job_id, filepath):
    def _cleanup():
        time.sleep(ABANDONED_FILE_CLEANUP_SECONDS)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except OSError:
            pass
        JOBS.pop(job_id, None)

    threading.Thread(target=_cleanup, daemon=True).start()


def build_format_string(quality: str, audio_only: bool) -> str:
    """
    Always returns a format selector that matches *something*.
    Resolution preference is handled separately via format_sort so a
    missing exact-height match never causes a hard failure.
    """
    if audio_only:
        return "bestaudio/best"
    return "bv*+ba/b"


def build_format_sort(quality: str):
    """
    Returns a yt-dlp format_sort list that nudges format selection toward
    the requested resolution, without eliminating other options if an
    exact match isn't available for the client/video in question.
    """
    if quality == "best":
        return None
    height = quality.rstrip("p")
    return [f"res:{height}"]


def make_progress_hook(job_id):
    def hook(d):
        job = JOBS.get(job_id)
        if not job:
            return
        if d["status"] == "downloading":
            percent_str = d.get("_percent_str", "0%").strip().replace("%", "")
            try:
                job["percent"] = float(percent_str)
            except ValueError:
                pass
            job["speed"] = d.get("_speed_str", "").strip()
            job["eta"] = d.get("_eta_str", "").strip()
            job["status"] = "downloading"
        elif d["status"] == "finished":
            job["status"] = "processing"
            job["percent"] = 100
    return hook


def run_download(job_id, url, quality, audio_only, playlist):
    job = JOBS[job_id]
    outtmpl = os.path.join(DOWNLOAD_DIR, f"{job_id}_%(title)s.%(ext)s")

    ydl_opts = {
        "format": build_format_string(quality, audio_only),
        "outtmpl": outtmpl,
        "noplaylist": not playlist,
        "progress_hooks": [make_progress_hook(job_id)],
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web", "tv", "mweb"],
                "formats": ["missing_pot"],
            }
        },
    }

    format_sort = build_format_sort(quality)
    if format_sort:
        ydl_opts["format_sort"] = format_sort

    if os.path.exists(COOKIE_PATH):
        ydl_opts["cookiefile"] = COOKIE_PATH

    if not audio_only:
        ydl_opts["merge_output_format"] = "mp4"

    if audio_only:
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            job["title"] = info.get("title", "video")
            ydl.download([url])

            # Figure out the resulting filename on disk
            base = f"{job_id}_"
            ext = "mp3" if audio_only else "mp4"
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(base) and f.endswith(f".{ext}"):
                    job["filename"] = f
                    break

            job["status"] = "done"
            job["percent"] = 100

            if job.get("filename"):
                schedule_cleanup(job_id, os.path.join(DOWNLOAD_DIR, job["filename"]))
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    platform = detect_platform(url)
    if not platform:
        return jsonify({
            "error": "Unsupported link. Please paste a YouTube, Instagram, or Facebook URL."
        }), 400

    quality = data.get("quality", "best")
    audio_only = bool(data.get("audio_only", False))
    playlist = bool(data.get("playlist", False))

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "starting",
        "percent": 0,
        "speed": "",
        "eta": "",
        "title": "",
        "platform": platform,
        "filename": None,
        "error": None,
    }

    thread = threading.Thread(
        target=run_download,
        args=(job_id, url, quality, audio_only, playlist),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id"}), 404
    return jsonify(job)


@app.route("/api/file/<job_id>")
def get_file(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done" or not job.get("filename"):
        return jsonify({"error": "File not ready"}), 404

    filename = job["filename"]
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    if not os.path.exists(filepath):
        return jsonify({"error": "File not ready"}), 404

    # Read the whole file into memory and fully close the handle BEFORE
    # deleting it. On Windows a file can't be removed while anything still
    # has it open, so deleting after streaming (the old approach) could
    # silently fail and leave the file sitting in downloads/ forever.
    with open(filepath, "rb") as f:
        data = f.read()

    try:
        os.remove(filepath)
    except OSError:
        pass
    JOBS.pop(job_id, None)

    download_name = filename.split("_", 1)[-1]
    mimetype = "audio/mpeg" if download_name.lower().endswith(".mp3") else "video/mp4"

    # Content-Disposition headers must be Latin-1 encodable. Video titles
    # can contain characters (Japanese, Korean, emoji, etc.) that aren't,
    # so we send an ASCII-safe fallback name plus an RFC 5987 UTF-8
    # encoded filename*= for browsers that support it (all modern ones do).
    import re
    from urllib.parse import quote

    ascii_fallback = download_name.encode("ascii", "ignore").decode("ascii").strip()
    ascii_fallback = re.sub(r'[\\/*?:"<>|]', "", ascii_fallback) or "download"
    utf8_quoted = quote(download_name)

    response = Response(data, mimetype=mimetype)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{utf8_quoted}"
    )
    return response


if __name__ == "__main__":
    app.run(debug=True, port=5000)