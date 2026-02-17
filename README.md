# Suno Manager

Bulk music generation management tool powered by the Suno API. Upload songs from Excel, generate in bulk, auto-download, and analyze silence.

**Single service** — no Node.js proxy needed. Connects directly to the Suno API from Python (Clerk JWT auth).

## Features

- Bulk song upload from Excel/CSV (title, lyrics, tags, model)
- Automatic song generation with batch processing (configurable batch size & delay)
- MP3 / WAV download (WAV requires Suno Pro)
- Silence detection and analysis (pydub + ffmpeg)
- Waveform visualizer with silence region highlighting
- Real-time progress tracking via WebSocket
- **Suno History browser** — browse your entire Suno library with pagination, detail panels, mini audio player with seek, and batch download
- **CAPTCHA solver** — automatic detection and browser-based solving
- **Cookie helper** — step-by-step guide with animated demo in Settings
- Swagger UI (`/docs`) with all Suno API endpoints
- Dynamic model list (automatically loaded from your Suno account)

## Requirements

- **Python 3.10+** (3.11 or 3.12 recommended)
- **ffmpeg** (for silence analysis)
- **Suno account** with a valid session cookie

## Installation

### 1. Clone the repository

```bash
git clone <repo-url> suno-manager
cd suno-manager
```

### 2. Quick Setup (recommended)

Run the one-click installer:

```bash
chmod +x install.sh
./install.sh
```

This will automatically:
1. Install **Miniconda** if conda is not found (macOS & Linux)
2. Create a conda environment `suno-manager` with Python 3.12
3. Install **ffmpeg** via conda-forge
4. Install Python dependencies from `requirements.txt`
5. Install **Playwright + Chromium** (for CAPTCHA solving)
6. Create a default `config.yaml` if missing
7. Create required directories (`downloads/`, `uploads/`, `logs/`)

### Alternative: Manual Setup with Conda

```bash
# Create environment
conda create -n suno-manager python=3.11 -y
conda activate suno-manager

# Install dependencies
pip install -r requirements.txt

# Install ffmpeg (if not already installed)
conda install -c conda-forge ffmpeg -y
# or: brew install ffmpeg (macOS)
# or: sudo apt install ffmpeg (Ubuntu/Debian)
```

### Alternative: Manual Setup without Conda (venv)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate    # Linux/macOS
# venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# ffmpeg must be installed on the system:
# macOS:  brew install ffmpeg
# Ubuntu: sudo apt install ffmpeg
# Windows: download from https://ffmpeg.org/download.html and add to PATH

# Create required directories
mkdir -p downloads uploads logs
```

## Configuration

### config.yaml (main configuration file)

Create a `config.yaml` file in the project root:

```yaml
# Suno API connection
suno_api:
  # Suno session cookie (required)
  # See the "Getting the Cookie" section below
  cookie: "__client=eyJ..."

  # (optional) Path to legacy suno-api .env file — for backward compatibility
  # env_path: "/path/to/suno-api/.env"

# Song generation settings
generation:
  default_model: "chirp-crow"       # Default model (can be changed from Settings page)
  min_duration_filter: 180           # Songs shorter than this (in seconds) won't be downloaded
  polling_interval: 10               # Status check interval (in seconds)
  auto_download: true                # Auto-download songs when generation completes
  auto_analyze_silence: true         # Auto-analyze downloaded songs for silence

# Download settings
download:
  directory: "./downloads"           # Download directory
  format: "mp3"                      # mp3, wav, or both

# Silence analysis
silence_analysis:
  threshold: -40                     # dBFS — audio below this level is considered silence
  min_length: 1000                   # ms — minimum silence duration to detect

# Server
server:
  host: "0.0.0.0"
  port: 8080
```

> All settings can also be changed from the Settings page in the web UI.

### Getting the Cookie

Suno Manager connects directly to Suno's internal API. A valid `__client` cookie is required for authentication.

**Steps:**

1. Go to [suno.com](https://suno.com) in your browser and log in
2. Open DevTools (F12 or Cmd+Option+I)
3. Navigate to **Application** tab > **Cookies** > `https://suno.com`
4. Find the `__client` cookie value and copy it (it's a long JWT token)
5. Paste it into the `suno_api.cookie` field in `config.yaml`

Alternatively: Start the app and enter the cookie from the Settings page.

> **Note:** Cookies can expire. If the connection drops, get a new cookie and update it from Settings.

### .env file (alternative / legacy method)

A `.env` file can also be used instead of or alongside `config.yaml`:

```env
DOWNLOAD_DIR=./downloads
SILENCE_THRESHOLD=-40
MIN_SILENCE_LENGTH=1000
MIN_DURATION_FILTER=180
POLLING_INTERVAL=10
DEFAULT_MODEL=chirp-crow
AUTO_DOWNLOAD=true
AUTO_ANALYZE_SILENCE=true
```

> If `config.yaml` exists, it takes priority.

## Running

### Quick Start (recommended)

```bash
./start.sh
```

This will automatically:
- Find and activate the `suno-manager` conda environment
- Read the port from `config.yaml` (default: 8080)
- Check if the port is already in use (offers to kill the existing process)
- Start the server with hot-reload enabled

**Options:**

```bash
./start.sh                # Start with defaults (port from config.yaml, hot-reload on)
./start.sh --port 9090    # Override port
./start.sh --no-reload    # Disable hot-reload (production)
```

### Manual Start

```bash
# If using Conda:
conda activate suno-manager

# Or if using venv:
# source venv/bin/activate

# Start the application
python app.py

# Or run directly with uvicorn (with hot-reload):
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

Open in your browser: **http://localhost:8080**

## Usage

### 1. Upload Songs from Excel

- Go to the **Upload** page
- Download the sample Excel file (Download Sample)
- Fill in the Excel:

| Column | Description | Required |
|--------|-------------|----------|
| A - Title | Song title | Yes |
| B - Lyrics | Song lyrics | Yes |
| C - Tags | Style tags (comma-separated) | Yes |
| D - Negative Tags | Styles to avoid | No |
| E - Instrumental | true/false | No |
| F - Model | Model name (empty = default) | No |

- Upload the file, review the preview, and click **Save to Queue**

### 2. Start Generation

- Click **Start Generation** from the **Dashboard** page
- Track progress from the **Songs** page
- Real-time status updates are delivered via WebSocket

### 3. Auto-Download

When `auto_download: true`, completed songs are automatically downloaded. Download progress is shown via WebSocket + SSE.

### 4. Silence Analysis

Downloaded songs are automatically analyzed for silence. Songs with detected silence are highlighted in red. Silence regions are visually displayed on the waveform.

## API Documentation

Swagger UI: **http://localhost:8080/docs**

See [API.md](API.md) for the complete API reference.

### Main Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard |
| `/songs` | GET | Song list |
| `/history` | GET | Suno History browser |
| `/settings` | GET | Settings |
| `/upload` | GET | Excel upload |
| `/api/start-generation` | POST | Start generation |
| `/api/poll-status` | POST | Update statuses |
| `/api/download-completed` | POST | Download completed songs |
| `/api/suno-history` | GET | Fetch Suno library (paginated) |
| `/api/download-from-history/batch` | POST | Batch download from history |
| `/api/captcha/status` | GET | Check CAPTCHA status |
| `/api/captcha/solve` | POST | Start CAPTCHA solver |
| `/ws` | WebSocket | Real-time updates |

### Suno API Endpoints (`/suno/...`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/suno/generate` | POST | Generate from prompt |
| `/suno/custom-generate` | POST | Generate with custom lyrics/tags |
| `/suno/extend` | POST | Extend a song |
| `/suno/concat` | POST | Concatenate clips |
| `/suno/lyrics` | POST | Generate lyrics |
| `/suno/feed` | GET | Clip info |
| `/suno/clip/{id}` | GET | Single clip details |
| `/suno/credits` | GET | Credit info |
| `/suno/models` | GET | Available models |
| `/suno/convert-wav` | POST | Start WAV conversion |
| `/suno/wav-url` | GET | Get WAV file URL |
| `/suno/billing-info` | GET | Full subscription info |

## Project Structure

```
suno-manager/
├── app.py              # Main FastAPI application (routes, WS, background tasks)
├── suno_api.py         # SunoClient — direct Suno API communication (Clerk JWT auth)
├── suno_models.py      # Pydantic request/response models
├── suno_router.py      # /suno/* FastAPI router (Swagger endpoints)
├── database.py         # SQLite operations (songs, generations, settings)
├── audio_analyzer.py   # Silence analysis (pydub + ffmpeg)
├── captcha_solver.py   # CAPTCHA detection and browser-based solving
├── install.sh          # One-click installer (Miniconda, conda env, dependencies)
├── start.sh            # One-click launcher (conda activate + uvicorn)
├── config.yaml         # Main configuration file
├── requirements.txt    # Python dependencies
├── .env                # Environment variables (alternative config)
├── templates/          # Jinja2 HTML templates
│   ├── base.html       # Base layout (navbar, toast, WebSocket client)
│   ├── dashboard.html  # Dashboard + statistics
│   ├── songs.html      # Song list + waveform + download
│   ├── _song_row.html  # Single song row (for AJAX refresh)
│   ├── history.html    # Suno History browser + mini player
│   ├── settings.html   # Settings page + cookie helper
│   └── upload.html     # Excel upload
├── static/             # Static files
│   └── sample_songs.xlsx  # Sample Excel template
├── downloads/          # Downloaded audio files
├── uploads/            # Uploaded Excel files
├── logs/               # Application logs
└── suno_manager.db     # SQLite database (auto-created)
```

## Troubleshooting

### "Suno API not available" error
- Make sure your cookie is valid
- Check connection status via Settings > Test Connection
- Get a new cookie and update it from Settings

### WAV download fails
- Suno Pro subscription is required for WAV
- Changing the format to "mp3" will download MP3 instead
- If WAV fails, it automatically falls back to MP3

### Silence analysis not working
- Make sure ffmpeg is installed: `ffmpeg -version`
- pydub uses ffmpeg; it must be on your PATH

### WebSocket not connecting
- Check browser console for `[WS] Connected` message
- Auto-reconnects in 3 seconds
- Verify the port if using a custom one

### Cookie expiring
- Suno cookies expire after a period of time
- A 401/403 error means a new cookie is needed
- Get a fresh `__client` value from your browser

## Tech Stack

- **Backend:** Python 3.11, FastAPI, uvicorn, aiohttp
- **Frontend:** Jinja2, Tailwind CSS (CDN), vanilla JavaScript
- **Database:** SQLite
- **Real-time:** WebSocket + SSE (download progress)
- **Audio Analysis:** pydub + ffmpeg
- **API Auth:** Clerk JWT (automatic token refresh)
