# Suno Manager API Reference

Base URL: `http://localhost:8080`

Swagger UI: **http://localhost:8080/docs**

---

## Suno API Endpoints (`/suno/...`)

Direct access to Suno's internal API. Clerk JWT authentication is managed automatically.

### Song Generation

#### POST `/suno/generate`

Simple mode: describe the music you want, Suno writes lyrics and generates audio. Returns 2 clips.

```json
// Request
{
  "prompt": "A happy pop song about sunshine",
  "make_instrumental": false,
  "model": "chirp-crow"
}

// Response: AudioInfo[]
[
  {
    "id": "abc123...",
    "title": "Sunshine Days",
    "status": "submitted",
    "audio_url": null,
    "duration": null,
    "model_name": "chirp-crow",
    "tags": "pop, happy",
    "created_at": "2026-02-17T..."
  },
  { ... }
]
```

#### POST `/suno/custom-generate`

Custom mode: provide your own lyrics, style tags, and title. Returns 2 clips.

```json
// Request
{
  "prompt": "[Verse]\nHello world\n[Chorus]\nLa la la",
  "tags": "pop, upbeat, male vocals",
  "title": "Hello World",
  "negative_tags": "autotune, screaming",
  "make_instrumental": false,
  "model": "chirp-crow"
}

// Response: AudioInfo[]
```

**Lyrics Format:**
```
[Verse]
First verse here

[Chorus]
Chorus here

[Bridge]
Bridge section

[Outro]
Outro section
```

#### POST `/suno/extend`

Extend an existing song from a specific timestamp. Returns 2 clips.

```json
// Request
{
  "audio_id": "abc123-def456...",
  "prompt": "[Verse 3]\nNew lyrics here",
  "continue_at": 120.5,
  "tags": "pop, upbeat",
  "negative_tags": "",
  "title": "Extended Song",
  "model": "chirp-crow"
}

// Response: AudioInfo[]
```

#### POST `/suno/concat`

Concatenate extension clips into a single complete song.

```json
// Request
{ "clip_id": "abc123-def456..." }

// Response: Suno raw response
```

#### POST `/suno/lyrics`

Generate lyrics from a text prompt. Polls until complete (max 60s).

```json
// Request
{ "prompt": "A love song about the ocean" }

// Response
{
  "id": "lyrics-id...",
  "text": "[Verse]\nWaves crash...\n[Chorus]\nOcean blue...",
  "title": "Ocean Love",
  "status": "complete"
}
```

---

### Querying

#### GET `/suno/feed`

Fetch clip metadata by IDs or as a paginated library.

| Parameter | Type | Description |
|-----------|------|-------------|
| `ids` | string | Comma-separated clip IDs |
| `page` | int | Library page number |

```
GET /suno/feed?ids=abc123,def456
GET /suno/feed?page=1
```

**Response:** `AudioInfo[]`

#### GET `/suno/clip/{clip_id}`

Get full details for a single clip (unmapped Suno raw response).

```
GET /suno/clip/abc123-def456
```

---

### Account & Billing

#### GET `/suno/credits`

Get remaining account credits.

```json
// Response
{
  "credits_left": 9475,
  "period": "monthly",
  "monthly_limit": 10000,
  "monthly_usage": 525
}
```

#### GET `/suno/billing-info`

Get full billing data including subscription, models, plans, and limits.

```
GET /suno/billing-info
```

**Response:** Suno raw billing object

#### GET `/suno/models`

List available generation models for your account. Falls back to a cached list if the API is unreachable.

```json
// Response
[
  {
    "external_key": "chirp-crow",
    "name": "v5",
    "description": "Authentic vocals, superior audio quality",
    "major_version": 5,
    "is_default": true,
    "badges": ["pro", "beta"],
    "can_use": true,
    "max_prompt_length": 3000,
    "max_tags_length": 200,
    "capabilities": ["generate", "extend"],
    "features": ["lyrics", "instrumental"]
  },
  ...
]
```

---

### WAV Conversion

Downloading WAV files requires a 2-step process:

#### POST `/suno/convert-wav?id={clip_id}`

Trigger server-side WAV conversion. Must be called before `/wav-url`.

```json
// Response
{ "status": 204, "message": "WAV conversion triggered" }
```

#### GET `/suno/wav-url?id={clip_id}`

Get the CDN URL for the WAV file. Returns `null` if conversion is still in progress. Poll until non-null.

```json
// Response
{ "wav_file_url": "https://cdn.suno.ai/.../file.wav" }
// or if conversion is still in progress:
{ "wav_file_url": null }
```

---

## Manager API Endpoints (`/api/...`)

Internal management API for Suno Manager. Handles bulk operations, downloads, settings, etc.

### Song Management

#### POST `/api/upload-excel`

Upload an Excel/CSV file and get preview data.

- **Content-Type:** `multipart/form-data`
- **file:** `.xlsx`, `.xls`, or `.csv` file

```json
// Response
{
  "songs": [
    {
      "row_num": 1,
      "title": "Song Title",
      "lyrics": "[Verse]\n...",
      "tags": "pop, rock",
      "negative_tags": "",
      "make_instrumental": false,
      "model": "chirp-crow"
    }
  ],
  "count": 15,
  "filename": "my_songs.xlsx"
}
```

#### POST `/api/save-songs`

Save parsed songs to the database.

```json
// Request
{
  "songs": [ { "title": "...", "lyrics": "...", "tags": "..." } ],
  "batch_name": "batch_20260217"
}

// Response
{ "saved": 15, "batch_name": "batch_20260217", "ids": [1, 2, 3, ...] }
```

#### GET `/api/sample-excel`

Download the sample Excel template.

#### GET `/api/song/{song_id}`

Get details for a single song (JSON).

#### DELETE `/api/song/{song_id}`

Delete a song and its related generations. Downloaded files are also removed.

```json
// Response
{ "message": "Song deleted", "deleted_files": 2 }
```

#### POST `/api/delete-batch`

Delete multiple songs at once.

```json
// Request
{ "song_ids": [1, 2, 3] }

// Response
{ "message": "3 songs deleted", "deleted_files": 5 }
```

---

### Generation & Status

#### POST `/api/start-generation`

Start generation for all queued (pending) songs.

```json
// Response
{ "message": "Generation started for 15 songs", "count": 15 }
```

#### POST `/api/poll-status`

Query Suno API for status updates on incomplete generations. Automatically downloads newly completed songs (if `auto_download` is enabled).

```json
// Response
{
  "message": "Updated 5 generations",
  "updated": 5,
  "auto_download": 2,
  "updated_song_ids": [1, 3, 7],
  "auto_download_suno_ids": ["abc123", "def456"]
}
```

#### POST `/api/retry/{song_id}`

Retry generation for a failed song.

#### POST `/api/retry-all-failed`

Retry all failed songs.

#### GET `/api/failed-song-ids`

Get all failed song IDs.

```json
// Response
{ "ids": [4, 8, 12], "count": 3 }
```

---

### Downloads

#### POST `/api/download-completed`

Download all completed songs that pass the duration filter.

```json
// Response
{ "message": "Downloading 8 songs", "count": 8 }
```

#### POST `/api/download-single/{suno_id}`

Download a single generation.

```json
// Response
{ "message": "Download started" }
```

#### POST `/api/redownload/{suno_id}`

Re-download in a different format. Deletes the old file first.

```json
// Request
{ "format": "wav" }  // "mp3", "wav", "both"

// Response
{ "message": "Re-downloading as WAV" }
```

#### GET `/api/serve-file/{suno_id}`

Serve a downloaded file for browser download.

#### GET `/api/download-progress/{suno_id}`

Download progress stream (Server-Sent Events).

```
GET /api/download-progress/abc123

data: {"status":"downloading","progress":0.45,"message":"15360KB / 34000KB"}
data: {"status":"analyzing","progress":-1,"message":"Analyzing silence..."}
data: {"status":"complete","progress":1.0,"message":"Download complete"}
```

**Status values:**
| Status | Description |
|--------|-------------|
| `converting` | Waiting for WAV conversion |
| `downloading` | File is being downloaded |
| `analyzing` | Running silence analysis |
| `complete` | Completed |
| `error` | An error occurred |
| `timeout` | Timed out |

---

### Silence Analysis

#### POST `/api/reanalyze-silence/{suno_id}`

Re-run silence analysis with custom threshold values.

```json
// Request
{ "threshold": -35, "min_length": 800 }

// Response
{
  "has_silence": true,
  "silence_count": 2,
  "total_silence_sec": 3.45,
  "duration_sec": 245.6,
  "avg_dbfs": -18.3,
  "details": [
    { "start": 45.2, "end": 47.1, "duration": 1.9 },
    { "start": 180.5, "end": 182.05, "duration": 1.55 }
  ]
}
```

#### GET `/api/silence-details/{suno_id}`

Get existing silence analysis results.

---

### Settings & Configuration

#### GET `/api/stats`

Dashboard statistics (total, completed, processing, errors, credits).

```json
// Response
{
  "total": 50,
  "completed": 35,
  "pending": 5,
  "errors": 3,
  "total_gens": 100,
  "completed_gens": 70,
  "processing_gens": 10,
  "error_gens": 6,
  "credits_left": 9475
}
```

#### GET `/api/credits`

Get Suno account credit info.

#### POST `/api/settings`

Save settings.

```json
// Request
{
  "default_model": "chirp-crow",
  "min_duration_filter": "180",
  "polling_interval": "10",
  "auto_download": "true",
  "download_dir": "./downloads",
  "download_format": "wav",
  "silence_threshold": "-40",
  "min_silence_length": "1000"
}
```

#### POST `/api/test-connection`

Test the Suno API connection.

```json
// Response (success)
{ "status": "connected", "credits": { "credits_left": 9475 } }

// Response (failure)
{ "status": "disconnected", "error": "..." }
```

#### GET `/api/cookie`

Read the current Suno cookie value.

#### POST `/api/cookie`

Update the cookie and restart the API client.

```json
// Request
{ "cookie": "__client=eyJ..." }

// Response
{ "message": "Cookie updated. API client will reconnect with new cookie." }
```

---

### Suno History

#### GET `/api/suno-history`

Fetch clips from your Suno library (paginated).

| Parameter | Type | Description |
|-----------|------|-------------|
| `page` | int | Page number (0-based, default 0) |

```json
// Response
{
  "clips": [
    {
      "id": "abc123...",
      "title": "My Song",
      "audio_url": "https://cdn1.suno.ai/...",
      "image_url": "https://cdn2.suno.ai/...",
      "image_large_url": "https://cdn2.suno.ai/...",
      "status": "complete",
      "duration": 245.6,
      "model_name": "chirp-crow",
      "tags": "pop, rock",
      "prompt": "[Verse]\n...",
      "lyric": "Full lyrics...",
      "play_count": 42,
      "upvote_count": 5,
      "is_public": false,
      "is_liked": true,
      "created_at": "2026-02-17T..."
    }
  ],
  "total": 150,
  "page": 0,
  "has_more": true
}
```

#### POST `/api/download-from-history`

Download a single clip from Suno history. Creates a DB record if the clip doesn't exist locally.

```json
// Request
{ "suno_id": "abc123..." }

// Response
{ "message": "Download started for: My Song" }
```

#### POST `/api/download-from-history/batch`

Batch download multiple clips from Suno history. All downloads run sequentially in a single background task (rate-limit safe).

```json
// Request
{ "suno_ids": ["abc123...", "def456...", "ghi789..."] }

// Response
{
  "message": "Batch download started (3 clips)",
  "count": 3,
  "skipped": 0
}
```

---

### CAPTCHA

#### GET `/api/captcha/status`

Check if CAPTCHA verification is required.

```json
// Response
{
  "required": false,
  "has_valid_token": true,
  "is_solving": false
}
```

#### POST `/api/captcha/solve`

Start the CAPTCHA solver. Opens a browser window for the user to solve the challenge. Result is sent via WebSocket `captcha_update` event.

```json
// Response
{ "message": "CAPTCHA solver started" }
```

---

### AJAX Helper

#### GET `/api/song-row/{song_id}?index=0`

Returns a rendered HTML fragment for a single song row (used for AJAX row refresh).

---

## WebSocket (`/ws`)

WebSocket connection for real-time updates.

### Connection

```javascript
var ws = new WebSocket('ws://localhost:8080/ws');
```

### Client -> Server

```json
{ "action": "ping" }
```

### Server -> Client Events

#### `pong`
Ping response.
```json
{ "event": "pong" }
```

#### `progress`
Download/conversion progress update.
```json
{
  "event": "progress",
  "suno_id": "abc123...",
  "status": "downloading",
  "progress": 0.65,
  "message": "22000KB / 34000KB"
}
```

#### `generation_update`
Song generation status change.
```json
{
  "event": "generation_update",
  "song_id": 42,
  "suno_id": "abc123...",
  "status": "complete"
}
```

```json
{
  "event": "generation_update",
  "song_id": 42,
  "status": "submitted",
  "suno_ids": ["abc123...", "def456..."]
}
```

```json
{
  "event": "generation_update",
  "song_id": 42,
  "status": "error",
  "error": "Rate limit exceeded"
}
```

#### `captcha_update`
CAPTCHA solver status change.
```json
{
  "event": "captcha_update",
  "status": "solved",
  "message": "CAPTCHA solved successfully"
}
```
```json
{
  "event": "captcha_update",
  "status": "failed",
  "message": "CAPTCHA solving failed"
}
```

---

## Models

Available Suno models (dynamically loaded via `/suno/models`):

| Key | Name | Description | Tier |
|-----|------|-------------|------|
| `chirp-crow` | v5 | Latest, highest quality vocals | Pro |
| `chirp-bluejay` | v4.5+ | Advanced creation methods | Pro |
| `chirp-auk` | v4.5 | Intelligent prompts | Pro |
| `chirp-auk-turbo` | v4.5-all | Best free model | Free |
| `chirp-v4` | v4 | Improved audio quality | Pro |
| `chirp-v3-5` | v3.5 | Basic song structure | Free |

> Models may vary based on your account and subscription. Get the current list from the `/suno/models` endpoint.

---

## Error Codes

| HTTP | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad request (missing parameter, invalid format) |
| 404 | Resource not found (song, generation, file) |
| 500 | Server error / Suno API error |
| 503 | Suno API unavailable (invalid cookie, connection issue) |
