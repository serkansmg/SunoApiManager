"""
Suno Manager - FastAPI Application
Bulk music generation manager using Suno API.
"""

import os
import json
import asyncio
import re
import logging
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
import openpyxl
import yaml

import database as db
from suno_api import get_client, reset_client
import audio_analyzer

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "suno_manager.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("suno-manager")

# ─── Config ───────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")


def load_config() -> dict:
    """Load config from config.yaml, fallback to .env / defaults."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


config = load_config()

app = FastAPI(title="Suno Manager")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")
templates = Jinja2Templates(directory="templates")

# ─── Suno API Direct Router (replaces Node.js proxy) ────────
from suno_router import router as suno_router
app.include_router(suno_router)


# ─── WebSocket Manager ──────────────────────────────────────

class WSManager:
    """Manages WebSocket connections and broadcasts events to all clients."""

    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        logger.info(f"WS connected ({len(self.connections)} total)")

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)
        logger.info(f"WS disconnected ({len(self.connections)} total)")

    async def broadcast(self, event: str, data: dict):
        """Broadcast a JSON event to all connected clients."""
        message = json.dumps({"event": event, **data})
        stale = []
        for ws in self.connections:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


ws_manager = WSManager()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time updates.

    Events sent to client:
      - progress: {event, suno_id, status, progress, message}
      - generation_update: {event, song_id, suno_id, status}
      - toast: {event, message, type}

    Commands from client:
      - {"action": "ping"} → responds with {"event": "pong"}
    """
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
                if action == "ping":
                    await ws.send_text(json.dumps({"event": "pong"}))
            except (json.JSONDecodeError, TypeError):
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ─── Download Progress State ─────────────────────────────────
# Key: suno_id, Value: {status, progress, message, updated_at}
download_progress: dict[str, dict] = {}


def _set_progress(suno_id: str, status: str, progress: float, message: str = ""):
    """Update download progress for a suno_id and broadcast via WebSocket."""
    download_progress[suno_id] = {
        "status": status,       # "converting", "downloading", "complete", "error"
        "progress": progress,   # 0.0-1.0, or -1 for indeterminate
        "message": message,
        "updated_at": time.time(),
    }
    # Broadcast to WebSocket clients (fire-and-forget)
    if ws_manager.connections:
        asyncio.ensure_future(ws_manager.broadcast("progress", {
            "suno_id": suno_id,
            "status": status,
            "progress": progress,
            "message": message,
        }))


def _cleanup_stale_progress(max_age: int = 120):
    """Remove progress entries older than max_age seconds."""
    now = time.time()
    stale = [k for k, v in download_progress.items() if now - v["updated_at"] > max_age]
    for k in stale:
        del download_progress[k]


# ─── Startup ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    db.init_db()

    # Read from config.yaml > .env > hardcoded defaults
    suno_cfg = config.get("suno_api", {})
    gen_cfg = config.get("generation", {})
    dl_cfg = config.get("download", {})
    silence_cfg = config.get("silence_analysis", {})

    defaults = {
        "download_dir": dl_cfg.get("directory") or os.getenv("DOWNLOAD_DIR", "./downloads"),
        "silence_threshold": str(silence_cfg.get("threshold", os.getenv("SILENCE_THRESHOLD", "-40"))),
        "min_silence_length": str(silence_cfg.get("min_length", os.getenv("MIN_SILENCE_LENGTH", "1000"))),
        "min_duration_filter": str(gen_cfg.get("min_duration_filter", os.getenv("MIN_DURATION_FILTER", "180"))),
        "polling_interval": str(gen_cfg.get("polling_interval", os.getenv("POLLING_INTERVAL", "10"))),
        "default_model": gen_cfg.get("default_model") or os.getenv("DEFAULT_MODEL", "chirp-crow"),
        "auto_download": str(gen_cfg.get("auto_download", os.getenv("AUTO_DOWNLOAD", "true"))).lower(),
        "auto_analyze_silence": str(gen_cfg.get("auto_analyze_silence", os.getenv("AUTO_ANALYZE_SILENCE", "true"))).lower(),
        "download_format": dl_cfg.get("format", os.getenv("DOWNLOAD_FORMAT", "mp3")),
        "batch_size": str(gen_cfg.get("batch_size", 5)),
        "batch_delay": str(gen_cfg.get("batch_delay", 30)),
    }
    for key, val in defaults.items():
        if db.get_setting(key) is None:
            db.set_setting(key, val)

    logger.info("Suno Manager started (direct API mode)")


# ─── Jinja2 Filters ──────────────────────────────────────────

def format_duration(seconds):
    """Convert seconds to mm:ss format."""
    if not seconds or seconds == 0:
        return "--:--"
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes}:{secs:02d}"


def timeago(dt_str):
    """Simple relative time. SQLite CURRENT_TIMESTAMP stores UTC, so always compare in UTC."""
    if not dt_str:
        return ""
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        # SQLite CURRENT_TIMESTAMP has no tzinfo but is UTC — treat it as UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        if diff.total_seconds() < 0:
            return "Just now"
        if diff.days > 7:
            return dt.strftime("%b %d, %Y")
        elif diff.days > 0:
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif diff.seconds > 60:
            mins = diff.seconds // 60
            return f"{mins} min ago"
        else:
            return "Just now"
    except Exception:
        return str(dt_str)[:16]


templates.env.filters["format_duration"] = format_duration
templates.env.filters["timeago"] = timeago


# ─── PAGE ROUTES ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = db.get_stats()
    credits = {"credits_left": "?"}
    try:
        client = await get_client()
        credits = await client.get_credits()
    except Exception:
        pass

    recent = db.get_recent_generations(limit=10)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "credits": credits,
        "generations": recent,
        "active_page": "dashboard",
    })


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "active_page": "upload",
    })


@app.get("/songs", response_class=HTMLResponse)
async def songs_page(request: Request, page: int = 1, status: str = "all", search: str = ""):
    songs_data = db.get_songs(status=status, page=page, per_page=20, search=search)
    settings = db.get_all_settings()
    return templates.TemplateResponse("songs.html", {
        "request": request,
        "songs_data": songs_data,
        "settings": settings,
        "current_status": status,
        "current_search": search,
        "active_page": "songs",
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    settings = db.get_all_settings()
    api_online = False
    try:
        client = await get_client()
        await client.get_credits()
        api_online = True
    except Exception:
        pass

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "api_online": api_online,
        "active_page": "settings",
    })


# ─── API ROUTES ───────────────────────────────────────────────

@app.post("/api/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    """Parse uploaded Excel/CSV and return preview data."""
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        return JSONResponse({"error": "Unsupported file format. Use .xlsx, .xls or .csv"}, status_code=400)

    contents = await file.read()
    tmp_path = f"uploads/{file.filename}"
    os.makedirs("uploads", exist_ok=True)
    with open(tmp_path, "wb") as f:
        f.write(contents)

    try:
        wb = openpyxl.load_workbook(tmp_path, read_only=True)
        ws = wb.active
        rows = []
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
            if not row[0]:  # skip empty rows
                continue
            rows.append({
                "row_num": i + 1,
                "title": str(row[0] or "").strip(),
                "lyrics": str(row[1] or "").strip(),
                "tags": str(row[2] or "").strip(),
                "negative_tags": str(row[3] or "").strip() if len(row) > 3 else "",
                "make_instrumental": str(row[4] or "").strip().lower() in ("true", "1", "yes") if len(row) > 4 else False,
                "model": str(row[5] or "").strip() if len(row) > 5 and row[5] else "",
            })
        wb.close()
        return JSONResponse({"songs": rows, "count": len(rows), "filename": file.filename})
    except Exception as e:
        return JSONResponse({"error": f"Failed to parse file: {str(e)}"}, status_code=400)


@app.post("/api/save-songs")
async def save_songs(request: Request):
    """Save parsed songs to database."""
    data = await request.json()
    songs = data.get("songs", [])
    batch_name = data.get("batch_name", datetime.now().strftime("batch_%Y%m%d_%H%M%S"))
    default_model = db.get_setting("default_model", "chirp-crow")

    saved_ids = []
    for song in songs:
        song_id = db.create_song(
            title=song["title"],
            lyrics=song["lyrics"],
            tags=song["tags"],
            negative_tags=song.get("negative_tags", ""),
            make_instrumental=song.get("make_instrumental", False),
            model=song.get("model") or default_model,
            batch_name=batch_name,
        )
        saved_ids.append(song_id)

    return JSONResponse({"saved": len(saved_ids), "batch_name": batch_name, "ids": saved_ids})


@app.get("/api/sample-excel")
async def sample_excel():
    """Download sample Excel template."""
    return FileResponse(
        "static/sample_songs.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="suno_manager_sample.xlsx"
    )


@app.post("/api/start-generation")
async def start_generation(background_tasks: BackgroundTasks):
    """Start generating all pending songs in batches."""
    pending = db.get_pending_songs()
    if not pending:
        return JSONResponse({"message": "No pending songs to generate", "count": 0})

    batch_size = int(db.get_setting("batch_size") or 5)
    batch_delay = int(db.get_setting("batch_delay") or 30)

    total = len(pending)
    batch_count = (total + batch_size - 1) // batch_size

    background_tasks.add_task(generate_songs_task, pending, batch_size, batch_delay)
    return JSONResponse({
        "message": f"Generation started: {total} songs in {batch_count} batches (batch_size={batch_size})",
        "count": total,
        "batch_size": batch_size,
        "batch_count": batch_count,
    })


async def generate_songs_task(songs: list, batch_size: int = 5, batch_delay: int = 30):
    """Background task to generate songs in batches to avoid rate limits."""
    client = await get_client()
    total = len(songs)

    for i, song in enumerate(songs):
        batch_num = i // batch_size + 1
        pos_in_batch = i % batch_size + 1

        try:
            db.update_song_status(song["id"], "submitted")
            results = await client.custom_generate(
                prompt=song["lyrics"],
                tags=song["tags"],
                title=song["title"],
                negative_tags=song.get("negative_tags", ""),
                make_instrumental=bool(song.get("make_instrumental", False)),
                model=song.get("model", "chirp-crow"),
            )
            for result in results:
                db.create_generation(
                    song_id=song["id"],
                    suno_id=result["id"],
                    suno_status=result.get("status", "submitted"),
                )
            logger.info(f"[{i+1}/{total}] Generated '{song['title']}' (batch {batch_num}, {pos_in_batch}/{batch_size})")
            await ws_manager.broadcast("generation_update", {
                "song_id": song["id"], "status": "submitted",
                "suno_ids": [r["id"] for r in results],
            })

            # Delay logic: short delay between songs, longer between batches
            if i < total - 1:
                if pos_in_batch == batch_size:
                    # End of batch — longer pause
                    logger.info(f"Batch {batch_num} complete. Waiting {batch_delay}s before next batch...")
                    await ws_manager.broadcast("generation_batch", {
                        "batch": batch_num,
                        "total_batches": (total + batch_size - 1) // batch_size,
                        "waiting": batch_delay,
                    })
                    await asyncio.sleep(batch_delay)
                else:
                    # Within batch — short delay
                    await asyncio.sleep(3)

        except Exception as e:
            error_msg = str(e)
            db.update_song_status(song["id"], "error", error_message=error_msg)
            logger.error(f"[{i+1}/{total}] Error generating '{song.get('title', '?')}': {e}")
            await ws_manager.broadcast("generation_update", {
                "song_id": song["id"], "status": "error", "error": error_msg,
            })
            # On rate limit errors, add extra delay
            if "429" in error_msg or "rate" in error_msg.lower():
                logger.warning("Rate limit detected, waiting 60s...")
                await asyncio.sleep(60)


@app.post("/api/poll-status")
async def poll_status(background_tasks: BackgroundTasks):
    """Check status of all incomplete generations."""
    incomplete = db.get_incomplete_generations()
    if not incomplete:
        return JSONResponse({"message": "No incomplete generations", "updated": 0,
                             "updated_song_ids": [], "auto_download_suno_ids": []})

    suno_ids = [g["suno_id"] for g in incomplete]
    updated = 0
    updated_song_ids = []  # Track which song IDs were updated (for AJAX row refresh)
    newly_completed = []  # Track generations that just became complete

    try:
        client = await get_client()
        # Query in batches of 20
        for i in range(0, len(suno_ids), 20):
            batch = suno_ids[i:i + 20]
            results = await client.get_audio_info(ids=batch)

            for result in results:
                suno_id = result["id"]
                old = db.get_generation_by_suno_id(suno_id)
                if not old:
                    continue

                update_data = {
                    "suno_status": result.get("status", ""),
                    "audio_url": result.get("audio_url", ""),
                    "image_url": result.get("image_url", ""),
                    "video_url": result.get("video_url", ""),
                }

                if result.get("duration"):
                    update_data["duration"] = float(result["duration"])

                if result.get("error_message"):
                    update_data["error_message"] = result["error_message"]

                db.update_generation(suno_id, **update_data)

                # Track updated song_id for AJAX refresh
                if old["song_id"] not in updated_song_ids:
                    updated_song_ids.append(old["song_id"])

                # Broadcast status change via WebSocket
                await ws_manager.broadcast("generation_update", {
                    "song_id": old["song_id"], "suno_id": suno_id,
                    "status": result.get("status", ""),
                })

                # Update parent song status
                if result.get("status") == "complete":
                    db.update_song_status(old["song_id"], "complete")
                    # Track for auto-download (only if not already downloaded)
                    if not old.get("downloaded") and result.get("audio_url"):
                        newly_completed.append({
                            **dict(old),
                            "audio_url": result.get("audio_url", ""),
                            "duration": float(result.get("duration", 0)),
                            "title": old.get("title", "unknown"),
                        })
                elif result.get("status") == "error":
                    db.update_song_status(old["song_id"], "error")

                updated += 1

    except Exception as e:
        logger.error(f"Poll status error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    # Auto-download newly completed generations
    auto_download = db.get_setting("auto_download", "true") == "true"
    auto_download_count = 0
    auto_download_suno_ids = []
    if auto_download and newly_completed:
        # Fill in song titles for generations that need them
        for gen in newly_completed:
            if not gen.get("title") or gen["title"] == "unknown":
                with db.get_db() as conn:
                    song = conn.execute("SELECT title FROM songs WHERE id = ?", (gen["song_id"],)).fetchone()
                    gen["title"] = song["title"] if song else "unknown"

        min_duration = float(db.get_setting("min_duration_filter", "180"))
        downloadable = [g for g in newly_completed if g["duration"] >= min_duration]
        if downloadable:
            background_tasks.add_task(download_songs_task, downloadable)
            auto_download_count = len(downloadable)
            auto_download_suno_ids = [g["suno_id"] for g in downloadable]
            logger.info(f"Auto-download triggered for {auto_download_count} newly completed generations")

    logger.info(f"Poll status: updated {updated} generations, auto-download: {auto_download_count}")
    return JSONResponse({
        "message": f"Updated {updated} generations",
        "updated": updated,
        "auto_download": auto_download_count,
        "updated_song_ids": updated_song_ids,
        "auto_download_suno_ids": auto_download_suno_ids,
    })


@app.post("/api/download-completed")
async def download_completed(background_tasks: BackgroundTasks):
    """Download all completed songs that meet duration filter."""
    min_duration = float(db.get_setting("min_duration_filter", "180"))
    downloadable = db.get_downloadable_generations(min_duration)

    if not downloadable:
        return JSONResponse({"message": "No songs to download", "count": 0})

    background_tasks.add_task(download_songs_task, downloadable)
    return JSONResponse({"message": f"Downloading {len(downloadable)} songs", "count": len(downloadable)})


async def download_songs_task(generations: list):
    """Background task to download songs with per-song folder structure.

    Each song gets its own folder:
        downloads/{Title}_{suno_id_short}/
            {Title}_{suno_id_short}.mp3
            {Title}_{suno_id_short}.wav
            cover.jpg
            info.txt       (lyrics + tags in INI format)
            metadata.json  (complete Suno API data)
    """
    download_dir = db.get_setting("download_dir", "./downloads")
    download_format = db.get_setting("download_format", "mp3")
    auto_analyze = db.get_setting("auto_analyze_silence", "true") == "true"
    silence_thresh = int(db.get_setting("silence_threshold", "-40"))
    min_silence_len = int(db.get_setting("min_silence_length", "1000"))
    client = await get_client()

    for gen in generations:
        sid = gen["suno_id"]
        try:
            safe_title = re.sub(r'[^\w\s-]', '', gen["title"]).strip()[:50] or "untitled"
            suno_id_short = sid[:8]
            folder_name = f"{safe_title}_{suno_id_short}"
            song_dir = os.path.join(download_dir, folder_name)
            os.makedirs(song_dir, exist_ok=True)

            audio_url = gen["audio_url"]  # e.g. https://cdn1.suno.ai/{id}.mp3
            downloaded_paths = []

            def _make_progress_cb(suno_id: str):
                """Create a progress callback bound to a specific suno_id."""
                def cb(progress: float, message: str):
                    _set_progress(suno_id, "downloading", progress, message)
                return cb

            # ── Download MP3 ──
            if download_format in ("mp3", "both"):
                _set_progress(sid, "downloading", 0.0, "Downloading MP3...")
                mp3_path = os.path.join(song_dir, f"{folder_name}.mp3")
                await client.download_file(audio_url, mp3_path, progress_callback=_make_progress_cb(sid))
                downloaded_paths.append(mp3_path)
                logger.info(f"Downloaded MP3: {gen['title']} -> {mp3_path}")

            # ── Download WAV ──
            if download_format in ("wav", "both"):
                wav_path = os.path.join(song_dir, f"{folder_name}.wav")
                try:
                    _set_progress(sid, "converting", 0.0, "Triggering WAV conversion...")
                    await client.download_wav(sid, wav_path, progress_callback=_make_progress_cb(sid))
                    downloaded_paths.append(wav_path)
                    logger.info(f"Downloaded WAV: {gen['title']} -> {wav_path}")
                except Exception as wav_err:
                    logger.warning(f"WAV download failed for {gen['title']}: {wav_err}")
                    if download_format == "wav":
                        _set_progress(sid, "downloading", 0.0, "WAV failed, falling back to MP3...")
                        mp3_path = os.path.join(song_dir, f"{folder_name}.mp3")
                        await client.download_file(audio_url, mp3_path, progress_callback=_make_progress_cb(sid))
                        downloaded_paths.append(mp3_path)
                        logger.info(f"Fallback to MP3: {gen['title']} -> {mp3_path}")

            # ── Fetch full metadata from Suno API (before cover — we need image_large_url) ──
            raw_clip = None
            try:
                raw_clip = await client.get_clip(sid)
            except Exception as clip_err:
                logger.warning(f"Could not fetch clip metadata for {sid}: {clip_err}")

            # ── Download cover image (prefer large version) ──
            image_url = ""
            if raw_clip:
                image_url = raw_clip.get("image_large_url") or raw_clip.get("image_url", "")
            if not image_url:
                image_url = gen.get("image_url", "")
            if image_url:
                try:
                    _set_progress(sid, "downloading", 0.9, "Downloading cover image...")
                    cover_ext = "jpeg"
                    if ".png" in image_url:
                        cover_ext = "png"
                    elif ".webp" in image_url:
                        cover_ext = "webp"
                    cover_path = os.path.join(song_dir, f"cover.{cover_ext}")
                    await client.download_file(image_url, cover_path)
                    logger.info(f"Downloaded cover: {gen['title']} -> {cover_path}")
                except Exception as img_err:
                    logger.warning(f"Cover download failed for {gen['title']}: {img_err}")

            # ── Get song info from local DB ──
            song_data = db.get_song(gen.get("song_id") or 0)

            # ── Write info.txt (INI format: lyrics + tags) ──
            try:
                _write_song_info(song_dir, gen, song_data, raw_clip)
            except Exception as info_err:
                logger.warning(f"Could not write info.txt for {gen['title']}: {info_err}")

            # ── Write metadata.json (complete raw data) ──
            try:
                _write_metadata_json(song_dir, gen, song_data, raw_clip)
            except Exception as meta_err:
                logger.warning(f"Could not write metadata.json for {gen['title']}: {meta_err}")

            # ── Store first downloaded path as the main file_path ──
            main_path = downloaded_paths[0] if downloaded_paths else ""
            db.update_generation(sid, downloaded=True, file_path=main_path)

            # ── Analyze silence ──
            if auto_analyze and main_path:
                _set_progress(sid, "analyzing", -1, "Analyzing silence...")
                loop = asyncio.get_event_loop()
                analysis = await loop.run_in_executor(
                    None,
                    audio_analyzer.analyze_silence,
                    main_path,
                    silence_thresh,
                    min_silence_len,
                )
                db.update_generation(
                    sid,
                    has_silence=analysis.get("has_silence"),
                    silence_details=json.dumps(analysis),
                )

            _set_progress(sid, "complete", 1.0, "Download complete")
        except Exception as e:
            _set_progress(sid, "error", 0.0, str(e))
            logger.error(f"Error downloading {gen.get('title', '?')} ({sid}): {e}")


def _write_song_info(song_dir: str, gen: dict, song_data: dict | None, raw_clip: dict | None):
    """Write info.txt with song details in a human-readable format."""
    info_path = os.path.join(song_dir, "info.txt")

    lines = []
    lines.append("[song]")
    lines.append(f"title = {gen.get('title', '')}")
    lines.append(f"suno_id = {gen.get('suno_id', '')}")

    # Duration
    duration = gen.get("duration") or (song_data or {}).get("duration") or 0
    if duration:
        mins = int(float(duration)) // 60
        secs = int(float(duration)) % 60
        lines.append(f"duration = {mins}:{secs:02d}")

    # Model
    model = ""
    if raw_clip:
        model = raw_clip.get("model_name", "")
    if not model and song_data:
        model = song_data.get("model", "")
    if model:
        lines.append(f"model = {model}")

    # Created at
    created = gen.get("created_at") or (song_data or {}).get("created_at", "")
    if created:
        lines.append(f"created_at = {created}")

    # Song URL (suno.com link)
    suno_id = gen.get("suno_id", "")
    if suno_id:
        lines.append(f"url = https://suno.com/song/{suno_id}")

    # Audio URL
    audio_url = gen.get("audio_url", "")
    if audio_url:
        lines.append(f"audio_url = {audio_url}")

    # Image URLs
    image_url = gen.get("image_url", "")
    image_large_url = ""
    if raw_clip:
        image_large_url = raw_clip.get("image_large_url", "")
        if not image_url:
            image_url = raw_clip.get("image_url", "")
    if image_large_url:
        lines.append(f"image_url = {image_large_url}")
    elif image_url:
        lines.append(f"image_url = {image_url}")

    lines.append("")
    lines.append("[tags]")

    # Tags from local DB or raw clip
    tags = ""
    if song_data:
        tags = song_data.get("tags", "")
    if not tags and raw_clip:
        meta = raw_clip.get("metadata", {}) or {}
        tags = meta.get("tags", "")
    if tags:
        lines.append(f"style = {tags}")

    # Negative tags
    neg_tags = ""
    if song_data:
        neg_tags = song_data.get("negative_tags", "")
    if neg_tags:
        lines.append(f"negative = {neg_tags}")

    lines.append("")
    lines.append("[lyrics]")

    # Lyrics from local DB
    lyrics = ""
    if song_data:
        lyrics = song_data.get("lyrics", "")
    if not lyrics and raw_clip:
        meta = raw_clip.get("metadata", {}) or {}
        lyrics = meta.get("prompt", "")
    if lyrics:
        for lyric_line in lyrics.split("\n"):
            lines.append(lyric_line)

    with open(info_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_metadata_json(song_dir: str, gen: dict, song_data: dict | None, raw_clip: dict | None):
    """Write metadata.json with complete information."""
    meta_path = os.path.join(song_dir, "metadata.json")

    metadata = {
        "suno_id": gen.get("suno_id", ""),
        "title": gen.get("title", ""),
        "audio_url": gen.get("audio_url", ""),
        "image_url": gen.get("image_url", ""),
        "video_url": gen.get("video_url", ""),
        "duration": gen.get("duration"),
        "created_at": gen.get("created_at", ""),
    }

    # Add local song data
    if song_data:
        metadata["song"] = {
            "id": song_data.get("id"),
            "title": song_data.get("title", ""),
            "lyrics": song_data.get("lyrics", ""),
            "tags": song_data.get("tags", ""),
            "negative_tags": song_data.get("negative_tags", ""),
            "model": song_data.get("model", ""),
            "make_instrumental": bool(song_data.get("make_instrumental")),
            "status": song_data.get("status", ""),
            "batch_name": song_data.get("batch_name", ""),
            "created_at": song_data.get("created_at", ""),
        }

    # Add raw Suno API data
    if raw_clip:
        metadata["suno_raw"] = raw_clip

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)


@app.post("/api/download-single/{suno_id}")
async def download_single(suno_id: str, background_tasks: BackgroundTasks):
    """Download a single generation by suno_id."""
    gen = db.get_generation_by_suno_id(suno_id)
    if not gen:
        return JSONResponse({"error": "Generation not found"}, status_code=404)
    if not gen["audio_url"]:
        return JSONResponse({"error": "No audio URL available"}, status_code=400)

    # Get song title
    with db.get_db() as conn:
        song = conn.execute("SELECT title FROM songs WHERE id = ?", (gen["song_id"],)).fetchone()
        title = song["title"] if song else "unknown"

    background_tasks.add_task(download_songs_task, [{**gen, "title": title}])
    return JSONResponse({"message": "Download started"})


@app.post("/api/download-from-history")
async def download_from_history(request: Request, background_tasks: BackgroundTasks):
    """Download a clip from Suno history — creates DB records if needed, then downloads."""
    data = await request.json()
    suno_id = data.get("suno_id")
    if not suno_id:
        return JSONResponse({"error": "suno_id required"}, status_code=400)

    # Check if already in our DB
    gen = db.get_generation_by_suno_id(suno_id)
    if gen:
        # Already exists — just trigger download
        with db.get_db() as conn:
            song = conn.execute("SELECT title FROM songs WHERE id = ?", (gen["song_id"],)).fetchone()
            title = song["title"] if song else "unknown"
        background_tasks.add_task(download_songs_task, [{**gen, "title": title}])
        return JSONResponse({"message": "Download started", "suno_id": suno_id})

    # Not in DB — fetch clip data from Suno and create records
    client = await get_client()
    clip = await client.get_clip(suno_id)
    if not clip:
        return JSONResponse({"error": "Clip not found on Suno"}, status_code=404)

    metadata = clip.get("metadata", {}) or {}
    title = clip.get("title") or "Untitled"
    lyrics = metadata.get("prompt") or ""
    tags = metadata.get("tags") or ""
    model = clip.get("model_name") or "unknown"
    audio_url = clip.get("audio_url") or ""
    image_url = clip.get("image_url") or ""
    video_url = clip.get("video_url") or ""
    duration = metadata.get("duration") or clip.get("duration") or 0
    status = clip.get("status") or "complete"

    if not audio_url:
        return JSONResponse({"error": "No audio URL available for this clip"}, status_code=400)

    # Create song + generation records
    song_id = db.create_song(title=title, lyrics=lyrics, tags=tags, model=model, batch_name="history")
    db.update_song_status(song_id, "complete")
    db.create_generation(song_id=song_id, suno_id=suno_id, suno_status=status)
    db.update_generation(
        suno_id,
        audio_url=audio_url, image_url=image_url, video_url=video_url,
        duration=duration, suno_status=status,
    )

    gen = db.get_generation_by_suno_id(suno_id)
    background_tasks.add_task(download_songs_task, [{**gen, "title": title}])
    logger.info(f"History download: created song '{title}' (suno_id={suno_id}) and started download")
    return JSONResponse({"message": "Download started", "suno_id": suno_id, "title": title})


@app.post("/api/download-from-history/batch")
async def download_from_history_batch(request: Request, background_tasks: BackgroundTasks):
    """Bulk download clips from Suno history — creates DB records if needed, downloads sequentially."""
    data = await request.json()
    suno_ids = data.get("suno_ids", [])
    if not suno_ids:
        return JSONResponse({"error": "suno_ids required"}, status_code=400)

    client = await get_client()
    to_download = []

    for suno_id in suno_ids:
        gen = db.get_generation_by_suno_id(suno_id)
        if gen:
            with db.get_db() as conn:
                song = conn.execute("SELECT title FROM songs WHERE id = ?", (gen["song_id"],)).fetchone()
                title = song["title"] if song else "unknown"
            to_download.append({**gen, "title": title})
            continue

        # Not in DB — fetch from Suno
        try:
            clip = await client.get_clip(suno_id)
            if not clip:
                logger.warning(f"Batch download: clip {suno_id} not found on Suno, skipping")
                continue

            metadata = clip.get("metadata", {}) or {}
            title = clip.get("title") or "Untitled"
            lyrics = metadata.get("prompt") or ""
            tags = metadata.get("tags") or ""
            model = clip.get("model_name") or "unknown"
            audio_url = clip.get("audio_url") or ""
            image_url = clip.get("image_url") or ""
            video_url = clip.get("video_url") or ""
            duration = metadata.get("duration") or clip.get("duration") or 0
            status = clip.get("status") or "complete"

            if not audio_url:
                logger.warning(f"Batch download: clip {suno_id} has no audio URL, skipping")
                continue

            song_id = db.create_song(title=title, lyrics=lyrics, tags=tags, model=model, batch_name="history")
            db.update_song_status(song_id, "complete")
            db.create_generation(song_id=song_id, suno_id=suno_id, suno_status=status)
            db.update_generation(
                suno_id,
                audio_url=audio_url, image_url=image_url, video_url=video_url,
                duration=duration, suno_status=status,
            )
            gen = db.get_generation_by_suno_id(suno_id)
            to_download.append({**gen, "title": title})
        except Exception as e:
            logger.error(f"Batch download: failed to fetch clip {suno_id}: {e}")
            continue

    if not to_download:
        return JSONResponse({"error": "No downloadable clips found"}, status_code=400)

    # Single background task handles all downloads sequentially (no rate limit issues)
    background_tasks.add_task(download_songs_task, to_download)
    logger.info(f"History batch download: {len(to_download)} clips queued")
    return JSONResponse({
        "message": f"Batch download started ({len(to_download)} clips)",
        "count": len(to_download),
        "skipped": len(suno_ids) - len(to_download),
    })


@app.post("/api/redownload/{suno_id}")
async def redownload(suno_id: str, request: Request, background_tasks: BackgroundTasks):
    """Re-download a generation. Removes old audio files, re-downloads with full folder structure."""
    data = await request.json()
    fmt = data.get("format", "wav")  # mp3, wav, both

    gen = db.get_generation_by_suno_id(suno_id)
    if not gen:
        return JSONResponse({"error": "Generation not found"}, status_code=404)
    if not gen.get("audio_url"):
        return JSONResponse({"error": "No audio URL available"}, status_code=400)

    # Remove old audio files from the song folder (keep cover, info, metadata)
    old_path = gen.get("file_path", "")
    if old_path:
        song_folder = os.path.dirname(old_path)
        if os.path.isdir(song_folder):
            for f in os.listdir(song_folder):
                if f.endswith((".mp3", ".wav")):
                    try:
                        os.remove(os.path.join(song_folder, f))
                        logger.info(f"Removed old audio file: {f}")
                    except Exception:
                        pass
        elif os.path.exists(old_path):
            # Legacy flat file — remove it
            try:
                os.remove(old_path)
            except Exception:
                pass

    # Reset download state
    db.update_generation(suno_id, downloaded=False, file_path="")

    # Get song title
    with db.get_db() as conn:
        song = conn.execute("SELECT title FROM songs WHERE id = ?", (gen["song_id"],)).fetchone()
        title = song["title"] if song else "unknown"

    # Use the main download_songs_task (handles folder, cover, info, metadata)
    # Override download_format via a temporary setting-like approach
    gen_with_title = {**dict(gen), "title": title}
    background_tasks.add_task(redownload_task, gen_with_title, fmt)
    return JSONResponse({"message": f"Re-downloading as {fmt.upper()}"})


async def redownload_task(gen: dict, fmt: str):
    """Background task to re-download a single song with full folder structure."""
    download_dir = db.get_setting("download_dir", "./downloads")
    auto_analyze = db.get_setting("auto_analyze_silence", "true") == "true"
    silence_thresh = int(db.get_setting("silence_threshold", "-40"))
    min_silence_len = int(db.get_setting("min_silence_length", "1000"))
    sid = gen["suno_id"]
    client = await get_client()

    def _progress_cb(progress: float, message: str):
        _set_progress(sid, "downloading", progress, message)

    try:
        safe_title = re.sub(r'[^\w\s-]', '', gen["title"]).strip()[:50] or "untitled"
        suno_id_short = sid[:8]
        folder_name = f"{safe_title}_{suno_id_short}"
        song_dir = os.path.join(download_dir, folder_name)
        os.makedirs(song_dir, exist_ok=True)

        audio_url = gen["audio_url"]
        downloaded_paths = []

        # ── Download MP3 ──
        if fmt in ("mp3", "both"):
            _set_progress(sid, "downloading", 0.0, "Downloading MP3...")
            mp3_path = os.path.join(song_dir, f"{folder_name}.mp3")
            await client.download_file(audio_url, mp3_path, progress_callback=_progress_cb)
            downloaded_paths.append(mp3_path)
            logger.info(f"Re-downloaded MP3: {gen['title']} -> {mp3_path}")

        # ── Download WAV ──
        if fmt in ("wav", "both"):
            wav_path = os.path.join(song_dir, f"{folder_name}.wav")
            try:
                _set_progress(sid, "converting", 0.0, "Triggering WAV conversion...")
                await client.download_wav(sid, wav_path, progress_callback=_progress_cb)
                downloaded_paths.append(wav_path)
                logger.info(f"Re-downloaded WAV: {gen['title']} -> {wav_path}")
            except Exception as wav_err:
                logger.warning(f"WAV re-download failed for {gen['title']}: {wav_err}")
                if fmt == "wav":
                    _set_progress(sid, "downloading", 0.0, "WAV failed, falling back to MP3...")
                    mp3_path = os.path.join(song_dir, f"{folder_name}.mp3")
                    await client.download_file(audio_url, mp3_path, progress_callback=_progress_cb)
                    downloaded_paths.append(mp3_path)
                    logger.info(f"Fallback to MP3: {gen['title']} -> {mp3_path}")

        # ── Fetch full metadata from Suno API ──
        raw_clip = None
        try:
            raw_clip = await client.get_clip(sid)
        except Exception as clip_err:
            logger.warning(f"Could not fetch clip metadata for {sid}: {clip_err}")

        # ── Download cover image (if not already present) ──
        has_cover = any(f.startswith("cover.") for f in os.listdir(song_dir))
        if not has_cover:
            image_url = ""
            if raw_clip:
                image_url = raw_clip.get("image_large_url") or raw_clip.get("image_url", "")
            if not image_url:
                image_url = gen.get("image_url", "")
            if image_url:
                try:
                    _set_progress(sid, "downloading", 0.9, "Downloading cover image...")
                    cover_ext = "jpeg"
                    if ".png" in image_url:
                        cover_ext = "png"
                    elif ".webp" in image_url:
                        cover_ext = "webp"
                    cover_path = os.path.join(song_dir, f"cover.{cover_ext}")
                    await client.download_file(image_url, cover_path)
                except Exception as img_err:
                    logger.warning(f"Cover download failed: {img_err}")

        # ── Get song info from local DB ──
        song_data = db.get_song(gen.get("song_id") or 0)

        # ── Write/update info.txt and metadata.json ──
        try:
            _write_song_info(song_dir, gen, song_data, raw_clip)
        except Exception:
            pass
        try:
            _write_metadata_json(song_dir, gen, song_data, raw_clip)
        except Exception:
            pass

        # ── Store path and mark downloaded ──
        main_path = downloaded_paths[0] if downloaded_paths else ""
        db.update_generation(sid, downloaded=True, file_path=main_path)

        # ── Analyze silence ──
        if auto_analyze and main_path:
            _set_progress(sid, "analyzing", -1, "Analyzing silence...")
            loop = asyncio.get_event_loop()
            analysis = await loop.run_in_executor(
                None,
                audio_analyzer.analyze_silence,
                main_path,
                silence_thresh,
                min_silence_len,
            )
            db.update_generation(
                sid,
                has_silence=analysis.get("has_silence"),
                silence_details=json.dumps(analysis),
            )

        _set_progress(sid, "complete", 1.0, "Download complete")
    except Exception as e:
        _set_progress(sid, "error", 0.0, str(e))
        logger.error(f"Re-download failed for {gen.get('title', '?')} ({sid}): {e}")


@app.get("/api/song-row/{song_id}", response_class=HTMLResponse)
async def song_row_html(song_id: int, request: Request, index: int = 0):
    """Return rendered HTML fragment for a single song row (AJAX refresh)."""
    song = db.get_song(song_id)
    if not song:
        return HTMLResponse("<div class='text-red-400 text-xs px-6 py-2'>Song not found</div>", status_code=404)

    settings = db.get_all_settings()
    return templates.TemplateResponse("_song_row.html", {
        "request": request,
        "song": song,
        "row_index": index or song_id,
        "settings": settings,
    })


@app.get("/api/stats")
async def api_stats():
    stats = db.get_stats()
    try:
        client = await get_client()
        credits = await client.get_credits()
        stats["credits_left"] = credits.get("credits_left", "?")
    except Exception:
        stats["credits_left"] = "?"
    return JSONResponse(stats)


@app.get("/api/credits")
async def api_credits():
    try:
        client = await get_client()
        return JSONResponse(await client.get_credits())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/settings")
async def save_settings(request: Request):
    data = await request.json()
    for key, value in data.items():
        db.set_setting(key, value)
    return JSONResponse({"message": "Settings saved"})


@app.post("/api/test-connection")
async def test_connection():
    try:
        client = await get_client()
        credits = await client.get_credits()
        return JSONResponse({"status": "connected", "credits": credits})
    except Exception as e:
        return JSONResponse({"status": "disconnected", "error": str(e)}, status_code=500)


@app.get("/api/song/{song_id}")
async def api_get_song(song_id: int):
    song = db.get_song(song_id)
    if not song:
        return JSONResponse({"error": "Song not found"}, status_code=404)
    return JSONResponse(song)


@app.post("/api/retry/{song_id}")
async def retry_song(song_id: int, background_tasks: BackgroundTasks):
    """Retry a failed song generation."""
    song = db.get_song(song_id)
    if not song:
        return JSONResponse({"error": "Song not found"}, status_code=404)

    db.update_song_status(song_id, "pending", error_message="")
    pending = [dict(song)]
    background_tasks.add_task(generate_songs_task, pending)
    return JSONResponse({"message": "Retry started", "song_id": song_id})


@app.get("/api/failed-song-ids")
async def failed_song_ids():
    """Get all failed song IDs (for batch operations)."""
    with db.get_db() as conn:
        rows = conn.execute("SELECT id FROM songs WHERE status = 'error'").fetchall()
        return JSONResponse({"ids": [r["id"] for r in rows], "count": len(rows)})


@app.post("/api/retry-all-failed")
async def retry_all_failed(background_tasks: BackgroundTasks):
    """Retry all failed songs."""
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM songs WHERE status = 'error'").fetchall()
        failed = [dict(r) for r in rows]

    if not failed:
        return JSONResponse({"message": "No failed songs to retry", "count": 0})

    for song in failed:
        db.update_song_status(song["id"], "pending", error_message="")

    background_tasks.add_task(generate_songs_task, failed)
    logger.info(f"Retrying {len(failed)} failed songs")
    return JSONResponse({"message": f"Retrying {len(failed)} failed songs", "count": len(failed)})


@app.delete("/api/song/{song_id}")
async def delete_song(song_id: int):
    """Delete a song and its generations. Also removes downloaded files."""
    song = db.get_song(song_id)
    if not song:
        return JSONResponse({"error": "Song not found"}, status_code=404)

    file_paths = db.delete_song(song_id)
    # Clean up downloaded files
    deleted_files = 0
    for fp in file_paths:
        try:
            if fp and os.path.exists(fp):
                os.remove(fp)
                deleted_files += 1
        except Exception as e:
            logger.warning(f"Could not delete file {fp}: {e}")

    logger.info(f"Deleted song id={song_id} title='{song.get('title', '?')}', removed {deleted_files} files")
    return JSONResponse({"message": f"Song deleted", "deleted_files": deleted_files})


@app.post("/api/delete-batch")
async def delete_batch(request: Request):
    """Delete multiple songs at once."""
    data = await request.json()
    song_ids = data.get("song_ids", [])
    if not song_ids:
        return JSONResponse({"error": "No song IDs provided"}, status_code=400)

    file_paths = db.delete_songs_batch(song_ids)
    deleted_files = 0
    for fp in file_paths:
        try:
            if fp and os.path.exists(fp):
                os.remove(fp)
                deleted_files += 1
        except Exception as e:
            logger.warning(f"Could not delete file {fp}: {e}")

    logger.info(f"Batch deleted {len(song_ids)} songs, removed {deleted_files} files")
    return JSONResponse({"message": f"{len(song_ids)} songs deleted", "deleted_files": deleted_files})


# ─── SSE Download Progress ───────────────────────────────────

@app.get("/api/download-progress/{suno_id}")
async def download_progress_sse(suno_id: str):
    """Server-Sent Events endpoint for download progress."""
    async def event_stream():
        last_data = None
        no_change_count = 0
        while True:
            _cleanup_stale_progress()
            info = download_progress.get(suno_id)
            if info:
                data = json.dumps(info)
                if data != last_data:
                    yield f"data: {data}\n\n"
                    last_data = data
                    no_change_count = 0

                    # Close stream on terminal states (after sending)
                    if info["status"] in ("complete", "error"):
                        break
                else:
                    no_change_count += 1
            else:
                no_change_count += 1

            # Timeout after 60s of no changes
            if no_change_count > 120:  # 120 * 0.5s = 60s
                yield f'data: {{"status":"timeout","progress":0,"message":"No progress update"}}\n\n'
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─── Silence Re-analysis ─────────────────────────────────────

@app.post("/api/reanalyze-silence/{suno_id}")
async def reanalyze_silence(suno_id: str, request: Request):
    """Re-analyze silence for a specific generation with custom threshold."""
    data = await request.json()
    threshold = int(data.get("threshold", -40))
    min_length = int(data.get("min_length", 1000))

    gen = db.get_generation_by_suno_id(suno_id)
    if not gen:
        return JSONResponse({"error": "Generation not found"}, status_code=404)
    if not gen.get("file_path") or not os.path.exists(gen["file_path"]):
        return JSONResponse({"error": "Audio file not found. Download the song first."}, status_code=400)

    try:
        analysis = audio_analyzer.analyze_silence(
            gen["file_path"],
            silence_thresh=threshold,
            min_silence_len=min_length,
        )
        db.update_generation(
            suno_id,
            has_silence=analysis.get("has_silence"),
            silence_details=json.dumps(analysis),
        )
        logger.info(f"Re-analyzed silence for {suno_id}: has_silence={analysis.get('has_silence')}, threshold={threshold}dB, min_len={min_length}ms")
        return JSONResponse(analysis)
    except Exception as e:
        logger.error(f"Silence re-analysis failed for {suno_id}: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/silence-details/{suno_id}")
async def silence_details(suno_id: str):
    """Get silence analysis details for a generation."""
    gen = db.get_generation_by_suno_id(suno_id)
    if not gen:
        return JSONResponse({"error": "Generation not found"}, status_code=404)

    if gen.get("silence_details"):
        try:
            details = json.loads(gen["silence_details"]) if isinstance(gen["silence_details"], str) else gen["silence_details"]
            return JSONResponse(details)
        except (json.JSONDecodeError, TypeError):
            pass

    return JSONResponse({
        "has_silence": gen.get("has_silence"),
        "silence_count": 0,
        "total_silence_sec": 0,
        "details": [],
    })


# ─── File Download (browser) ─────────────────────────────────

@app.get("/api/serve-file/{suno_id}")
async def serve_file(suno_id: str):
    """Serve a downloaded audio file for browser download."""
    gen = db.get_generation_by_suno_id(suno_id)
    if not gen:
        return JSONResponse({"error": "Generation not found"}, status_code=404)
    if not gen.get("file_path") or not os.path.exists(gen["file_path"]):
        return JSONResponse({"error": "File not found. Download the song first."}, status_code=404)

    file_path = gen["file_path"]
    filename = os.path.basename(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/wav" if ext == ".wav" else "application/octet-stream"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Cookie Management ───────────────────────────────────────

@app.get("/api/cookie")
async def get_cookie():
    """Read current cookie from config.yaml."""
    suno_cfg = config.get("suno_api", {})
    cookie = suno_cfg.get("cookie", "")
    if cookie:
        return JSONResponse({"cookie": cookie, "length": len(cookie)})
    return JSONResponse({"cookie": "", "length": 0, "message": "No cookie configured"})


@app.post("/api/cookie")
async def update_cookie(request: Request):
    """Update cookie in config.yaml and reset SunoClient."""
    data = await request.json()
    new_cookie = data.get("cookie", "").strip()
    if not new_cookie:
        return JSONResponse({"error": "Cookie value is required"}, status_code=400)

    try:
        # Update config.yaml
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        with open(config_path, "r") as f:
            cfg_content = f.read()

        # Replace existing cookie line or add it
        import re as re_mod
        if re_mod.search(r'^\s*cookie:', cfg_content, re_mod.MULTILINE):
            cfg_content = re_mod.sub(
                r'(^\s*cookie:).*$',
                f'\\1 "{new_cookie}"',
                cfg_content,
                count=1,
                flags=re_mod.MULTILINE,
            )
        else:
            cfg_content = cfg_content.replace(
                "suno_api:",
                f'suno_api:\n  cookie: "{new_cookie}"',
            )

        with open(config_path, "w") as f:
            f.write(cfg_content)

        # Update in-memory config
        if "suno_api" not in config:
            config["suno_api"] = {}
        config["suno_api"]["cookie"] = new_cookie

        # Reset SunoClient to pick up new cookie
        reset_client()
        logger.info(f"Cookie updated ({len(new_cookie)} chars), SunoClient reset")
        return JSONResponse({"message": "Cookie updated. API client will reconnect with new cookie."})
    except Exception as e:
        logger.error(f"Error updating cookie: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── CAPTCHA ───────────────────────────────────────────────────

@app.get("/api/captcha/status")
async def captcha_status():
    """Check CAPTCHA status: whether it's required, if we have a valid token, etc."""
    client = await get_client()
    solver = client.captcha_solver

    # Check if we have a cached token
    has_token = solver.has_valid_token
    is_solving = solver.is_solving

    # Check if Suno requires CAPTCHA
    required = None
    try:
        required = await solver.check_captcha_required()
    except Exception as e:
        logger.warning(f"CAPTCHA check failed: {e}")

    return JSONResponse({
        "required": required,
        "has_valid_token": has_token,
        "is_solving": is_solving,
    })


@app.post("/api/captcha/solve")
async def captcha_solve(background_tasks: BackgroundTasks):
    """Trigger CAPTCHA solving — opens a browser window for manual solving.

    This runs in the background. The browser will open and the user must:
    1. Type something in the prompt box
    2. Click 'Create'
    3. Solve the hCaptcha challenge
    4. The token is captured automatically
    """
    client = await get_client()
    solver = client.captcha_solver

    if solver.is_solving:
        return JSONResponse({"message": "CAPTCHA solve already in progress", "status": "solving"})

    # Run in background task
    async def solve_task():
        try:
            token = await solver.get_token(force=True)
            if token:
                await ws_manager.broadcast("captcha_update", {
                    "status": "solved",
                    "message": "CAPTCHA solved successfully!",
                })
            else:
                await ws_manager.broadcast("captcha_update", {
                    "status": "failed",
                    "message": "CAPTCHA solving failed or timed out",
                })
        except Exception as e:
            logger.error(f"CAPTCHA solve error: {e}")
            await ws_manager.broadcast("captcha_update", {
                "status": "error",
                "message": str(e),
            })

    # Start solving in a new task (not background_tasks — needs async)
    asyncio.create_task(solve_task())

    return JSONResponse({
        "message": "CAPTCHA solving started. A browser window will open — please solve the challenge.",
        "status": "solving",
    })


@app.post("/api/captcha/invalidate")
async def captcha_invalidate():
    """Invalidate the cached CAPTCHA token."""
    client = await get_client()
    client.captcha_solver.invalidate_token()
    return JSONResponse({"message": "CAPTCHA token invalidated"})


# ─── Suno History (browse remote library) ────────────────────

@app.get("/history")
async def history_page(request: Request, page: int = 1):
    """Browse Suno library history."""
    settings = db.get_all_settings()
    return templates.TemplateResponse("history.html", {
        "request": request, "settings": settings, "current_page": page,
        "active_page": "history",
    })


@app.get("/api/suno-history")
async def suno_history(page: int = 0):
    """Fetch a page of clips from Suno's library feed."""
    client = await get_client()
    feed = await client.get_feed_page(page=page)
    total = feed["num_total"]
    clips = feed["clips"]
    raw_clips = feed.get("raw_clips", [])

    # Enrich mapped clips with extra fields from raw data
    raw_by_id = {c.get("id"): c for c in raw_clips}
    for clip in clips:
        raw = raw_by_id.get(clip["id"], {})
        clip["image_large_url"] = raw.get("image_large_url") or clip.get("image_url")
        clip["is_public"] = raw.get("is_public", False)
        clip["play_count"] = raw.get("play_count", 0)
        clip["upvote_count"] = raw.get("upvote_count", 0)
        clip["is_liked"] = raw.get("is_liked", False)
        clip["type"] = raw.get("type", "")
        meta = raw.get("metadata", {}) or {}
        clip["negative_tags"] = meta.get("negative_tags", "")
        clip["concat_history"] = meta.get("concat_history")
        clip["history"] = meta.get("history")

        # Mark which clips already exist in our local DB
        gen = db.get_generation_by_suno_id(clip["id"])
        clip["in_local_db"] = gen is not None
        clip["local_downloaded"] = gen["downloaded"] if gen else False

    per_page = len(clips) if clips else 20
    # num_total may be 0 or unreliable — derive has_more from clip count
    has_more = len(clips) >= per_page
    total_pages = max(1, (total + per_page - 1) // per_page) if total > 0 else (page + 2 if has_more else page + 1)

    return JSONResponse({
        "clips": clips,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "has_more": has_more,
    })


# ─── MAIN ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)
