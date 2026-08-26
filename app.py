
import os
import uuid
import threading
import time
from flask import Flask, render_template, request, jsonify, Response

import yt_dlp

app = Flask(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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
    if audio_only:
        return "bestaudio/best"
    if quality == "best":
        return "bestvideo+bestaudio/best"
    height = quality.rstrip("p")
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"


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
            "youtube": {"player_client": ["android"]}
        },
    }

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

    response = Response(data, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return response


if __name__ == "__main__":
    app.run(debug=True, port=5000)