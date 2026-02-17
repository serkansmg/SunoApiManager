# Suno Manager

Bulk music generation management tool powered by the Suno API. Upload songs from Excel, generate in bulk, auto-download, and analyze silence.

**Single service** — no Node.js proxy needed. Connects directly to the Suno API from Python (Clerk JWT auth).

## Quick Start

### 1. Create a `config.yaml`

```yaml
suno_api:
  cookie: "__client=eyJ..."

generation:
  default_model: "chirp-crow"
  min_duration_filter: 180
  polling_interval: 10
  auto_download: true
  auto_analyze_silence: true

download:
  directory: "./downloads"
  format: "mp3"

silence_analysis:
  threshold: -40
  min_length: 1000

server:
  host: "0.0.0.0"
  port: 8080
```

### 2. Create a `docker-compose.yml`

```yaml
services:
  suno-manager:
    image: smgteknik/sunoapimanager:latest
    container_name: suno-manager
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./downloads:/app/downloads
      - ./uploads:/app/uploads
      - ./logs:/app/logs
      - ./suno_manager.db:/app/suno_manager.db
    environment:
      - TZ=Europe/Istanbul
```

### 3. Run

```bash
docker compose up -d
```

Open in your browser: **http://localhost:8080**

## Volumes

| Mount | Description |
|-------|-------------|
| `./config.yaml` | Main configuration file (read-only) |
| `./downloads` | Downloaded audio files (MP3/WAV) |
| `./uploads` | Uploaded Excel files |
| `./logs` | Application logs |
| `./suno_manager.db` | SQLite database (songs, generations, settings) |

## Ports

| Port | Description |
|------|-------------|
| `8080` | Web UI + API + Swagger (`/docs`) |

## Getting the Cookie

A valid Suno `__client` cookie is required for authentication.

1. Go to [suno.com](https://suno.com) and log in
2. Open DevTools (F12) > **Application** > **Cookies** > `https://suno.com`
3. Copy the `__client` cookie value
4. Paste it into `config.yaml` under `suno_api.cookie`

Or start the app and enter the cookie from the **Settings** page.

## Features

- Bulk song upload from Excel/CSV
- Automatic generation with batch processing
- MP3 / WAV download
- Silence detection and waveform visualization
- Suno History browser with mini player and batch download
- CAPTCHA detection and solving
- Cookie helper with step-by-step guide
- Real-time progress via WebSocket
- Swagger UI at `/docs`

## Useful Commands

```bash
# Start
docker compose up -d

# Stop
docker compose down

# View logs
docker logs -f suno-manager

# Update to latest version
docker compose pull && docker compose up -d

# Check health
docker inspect --format='{{.State.Health.Status}}' suno-manager
```

## Source Code

GitHub: [https://github.com/serkansmg/SunoApiManager](https://github.com/serkansmg/SunoApiManager)
