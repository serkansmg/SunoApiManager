"""
Suno API Client — Direct communication with Suno's internal API.

Replaces the Node.js suno-api proxy by handling Clerk authentication,
cookie/JWT management, and all Suno API calls directly from Python.

Usage:
    client = SunoClient(cookie_string)
    await client.init()
    credits = await client.get_credits()
"""

import asyncio
import logging
import os
import random
import uuid
from http.cookies import SimpleCookie
from typing import Callable, Optional

import aiohttp

logger = logging.getLogger("suno-manager")

# ─── User Agent Pool (macOS Chrome) ─────────────────────────
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]


class SunoClient:
    """Direct client for Suno's internal API with Clerk authentication."""

    BASE_URL = "https://studio-api.prod.suno.com"
    CLERK_URL = "https://clerk.suno.com"
    CLERK_VERSION = "5.15.0"
    DEFAULT_MODEL = "chirp-v3-5"

    def __init__(self, cookie_string: str):
        """Initialize with raw cookie string from Suno browser session.

        Args:
            cookie_string: Full cookie string containing at minimum __client token.
                           Format: "__client=eyJ...;__cf_bm=abc;ajs_anonymous_id=uuid"
        """
        # Parse cookies — filter out Set-Cookie attributes (expires, Path, etc.)
        _cookie_attrs = {
            "expires", "max-age", "domain", "path", "secure",
            "httponly", "samesite", "partitioned",
        }
        self.cookies: dict[str, str] = {}
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                key = key.strip()
                if key.lower() not in _cookie_attrs:
                    self.cookies[key] = value.strip()
            # Skip bare attributes like "Secure" or "HttpOnly" (no '=')

        if "__client" not in self.cookies:
            raise ValueError("Cookie string must contain __client token for Clerk auth")

        # Device ID: use ajs_anonymous_id cookie or generate random UUID
        self.device_id = self.cookies.get("ajs_anonymous_id", str(uuid.uuid4()))

        # Auth state
        self.sid: str | None = None       # Clerk session ID
        self.token: str | None = None     # JWT Bearer token
        self._token_refreshed_at: float = 0  # timestamp of last token refresh
        self._token_ttl: int = 50         # seconds before refreshing (JWT typically lasts 60s)
        self._initialized = False

        # Random user agent (picked once per instance)
        self.user_agent = random.choice(_USER_AGENTS)

        # Default headers sent with every Suno API request
        self._default_headers = {
            "Affiliate-Id": "undefined",
            "Device-Id": f'"{self.device_id}"',  # Wrapped in literal quotes
            "x-suno-client": "Android prerelease-4nt180t 1.0.42",
            "X-Requested-With": "com.suno.android",
            "sec-ch-ua": '"Chromium";v="130", "Android WebView";v="130", "Not?A_Brand";v="99"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "User-Agent": self.user_agent,
        }

        # aiohttp session (created lazily)
        self._session: aiohttp.ClientSession | None = None

        # CAPTCHA solver (lazy-initialized)
        self._captcha_solver = None

        logger.info(f"SunoClient created (device_id={self.device_id[:8]}...)")

    # ─── Session Management ──────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _serialize_cookies(self) -> str:
        """Serialize cookies dict to header string."""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def _merge_set_cookies(self, response: aiohttp.ClientResponse):
        """Merge set-cookie headers from response into our cookie jar."""
        for header_value in response.headers.getall("set-cookie", []):
            sc = SimpleCookie()
            sc.load(header_value)
            for key, morsel in sc.items():
                self.cookies[key] = morsel.value

    # ─── Clerk Authentication ────────────────────────────────

    async def init(self) -> "SunoClient":
        """Initialize authentication: get session ID and JWT token.

        Must be called once before making API calls.
        Returns self for chaining.
        """
        await self._get_auth_token()
        await self._keep_alive()
        self._initialized = True
        logger.info(f"SunoClient initialized (sid={self.sid[:12]}...)")
        return self

    async def _get_auth_token(self):
        """Get Clerk session ID from the __client token.

        GET https://clerk.suno.com/v1/client?_is_native=true&_clerk_js_version=5.15.0
        Authorization: <raw __client cookie value> (NOT Bearer)
        """
        session = await self._get_session()
        url = f"{self.CLERK_URL}/v1/client"
        params = {"_is_native": "true", "_clerk_js_version": self.CLERK_VERSION}
        headers = {
            "Authorization": self.cookies["__client"],
            "User-Agent": self.user_agent,
            "Cookie": self._serialize_cookies(),
        }

        async with session.get(url, params=params, headers=headers) as resp:
            self._merge_set_cookies(resp)
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Clerk auth failed ({resp.status}): {text[:200]}")
            data = await resp.json()

        sid = data.get("response", {}).get("last_active_session_id")
        if not sid:
            raise Exception(
                "Failed to get session ID from Clerk. "
                "Cookie may be expired — update SUNO_COOKIE."
            )
        self.sid = sid
        logger.debug(f"Got Clerk session ID: {sid[:12]}...")

    async def _keep_alive(self):
        """Refresh the JWT token from Clerk.

        POST https://clerk.suno.com/v1/client/sessions/{sid}/tokens
        Authorization: <raw __client cookie value> (NOT Bearer)
        """
        if not self.sid:
            raise Exception("Cannot refresh token: no session ID (call init first)")

        session = await self._get_session()
        url = f"{self.CLERK_URL}/v1/client/sessions/{self.sid}/tokens"
        params = {"_is_native": "true", "_clerk_js_version": self.CLERK_VERSION}
        headers = {
            "Authorization": self.cookies["__client"],
            "User-Agent": self.user_agent,
            "Cookie": self._serialize_cookies(),
            "Content-Type": "application/json",
        }

        async with session.post(url, params=params, headers=headers, json={}) as resp:
            self._merge_set_cookies(resp)
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Clerk token refresh failed ({resp.status}): {text[:200]}")
            data = await resp.json()

        jwt = data.get("jwt")
        if not jwt:
            raise Exception("No JWT in Clerk token response")
        self.token = jwt
        self._token_refreshed_at = asyncio.get_event_loop().time()
        logger.debug("JWT token refreshed")

    # ─── CAPTCHA ────────────────────────────────────────────

    @property
    def captcha_solver(self):
        """Lazy-initialized CaptchaSolver instance."""
        if self._captcha_solver is None:
            from captcha_solver import CaptchaSolver
            self._captcha_solver = CaptchaSolver(self)
        return self._captcha_solver

    async def _get_captcha_token(self) -> str | None:
        """Get a CAPTCHA token if required, using cached token or solving new one."""
        return await self.captcha_solver.get_token()

    # ─── Core HTTP Request ───────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        base_url: str | None = None,
        timeout: int = 10,
        retry_auth: bool = True,
        **kwargs,
    ) -> dict | list:
        """Make an authenticated request to the Suno API.

        Automatically:
        - Adds Authorization: Bearer <jwt> header
        - Adds serialized cookies
        - Merges set-cookie from responses
        - Retries once with token refresh on 401/403

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g. "/api/billing/info/")
            base_url: Override base URL (default: studio-api.prod.suno.com)
            timeout: Request timeout in seconds
            retry_auth: If True, retry with fresh token on 401/403
            **kwargs: Passed to aiohttp request (json, params, etc.)
        """
        if not self._initialized:
            raise Exception("SunoClient not initialized — call await client.init() first")

        # Refresh token only when stale (JWT ~60s TTL, refresh at 50s)
        now = asyncio.get_event_loop().time()
        if now - self._token_refreshed_at > self._token_ttl:
            await self._keep_alive()

        session = await self._get_session()
        url = f"{base_url or self.BASE_URL}{path}"

        headers = {**self._default_headers}
        headers["Authorization"] = f"Bearer {self.token}"
        headers["Cookie"] = self._serialize_cookies()

        # Merge any extra headers from kwargs
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        client_timeout = aiohttp.ClientTimeout(total=timeout)

        async with session.request(
            method, url, headers=headers, timeout=client_timeout, **kwargs
        ) as resp:
            self._merge_set_cookies(resp)

            # Handle auth errors with one retry (401, 403, 422 token validation)
            if resp.status in (401, 403, 422) and retry_auth:
                logger.warning(f"Got {resp.status} for {path}, refreshing token and retrying...")
                await self._keep_alive()
                return await self._request(
                    method, path, base_url=base_url, timeout=timeout,
                    retry_auth=False, **kwargs
                )

            if resp.status == 204:
                return {"status": 204, "message": "No content"}

            if resp.status not in (200, 201):
                text = await resp.text()
                logger.error(f"Suno API error ({resp.status}) {method} {path}: {text[:300]}")
                raise Exception(f"Suno API error ({resp.status}): {text[:300]}")

            return await resp.json()

    # ─── Generation API ──────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        make_instrumental: bool = False,
        model: str = "chirp-v3-5",
    ) -> list[dict]:
        """Simple generation: describe the music, Suno writes lyrics.

        POST https://studio-api.prod.suno.com/api/generate/v2/
        """
        captcha_token = await self._get_captcha_token()
        payload = {
            "gpt_description_prompt": prompt,
            "prompt": "",
            "generation_type": "TEXT",
            "make_instrumental": make_instrumental,
            "mv": model,
            "token": captcha_token,
        }
        logger.info(f"generate: prompt='{prompt[:60]}...', model={model}")
        try:
            data = await self._request(
                "POST", "/api/generate/v2/", json=payload, timeout=30, retry_auth=False
            )
        except Exception as e:
            if "422" in str(e) and "token" in str(e).lower():
                logger.warning("Generate 422 — CAPTCHA token invalid, re-solving...")
                self.captcha_solver.invalidate_token()
                captcha_token = await self.captcha_solver.get_token(force=True)
                payload["token"] = captcha_token
                data = await self._request(
                    "POST", "/api/generate/v2/", json=payload, timeout=30, retry_auth=False
                )
            else:
                raise
        clips = data.get("clips", [])
        return [self._map_audio_info(c) for c in clips]

    async def custom_generate(
        self,
        prompt: str,
        tags: str,
        title: str,
        negative_tags: str = "",
        make_instrumental: bool = False,
        model: str = "chirp-v3-5",
    ) -> list[dict]:
        """Custom generation: provide full lyrics, style tags, and title.

        POST https://studio-api.prod.suno.com/api/generate/v2/
        """
        captcha_token = await self._get_captcha_token()
        payload = {
            "prompt": prompt,
            "tags": tags,
            "title": title,
            "negative_tags": negative_tags,
            "generation_type": "TEXT",
            "make_instrumental": make_instrumental,
            "mv": model,
            "token": captcha_token,
        }
        logger.info(f"custom_generate: title='{title}', model={model}, tags='{tags[:50]}'")
        try:
            data = await self._request(
                "POST", "/api/generate/v2/", json=payload, timeout=30, retry_auth=False
            )
        except Exception as e:
            if "422" in str(e) and "token" in str(e).lower():
                logger.warning("Generate 422 — CAPTCHA token invalid, re-solving...")
                self.captcha_solver.invalidate_token()
                captcha_token = await self.captcha_solver.get_token(force=True)
                payload["token"] = captcha_token
                data = await self._request(
                    "POST", "/api/generate/v2/", json=payload, timeout=30, retry_auth=False
                )
            else:
                raise
        clips = data.get("clips", [])
        result = [self._map_audio_info(c) for c in clips]
        logger.info(f"custom_generate success: {len(result)} clips, ids={[r['id'] for r in result]}")
        return result

    async def extend_audio(
        self,
        audio_id: str,
        prompt: str = "",
        continue_at: float = 0,
        tags: str = "",
        negative_tags: str = "",
        title: str = "",
        model: str = "chirp-v3-5",
    ) -> list[dict]:
        """Extend an existing clip from a specific timestamp.

        POST https://studio-api.prod.suno.com/api/generate/v2/
        """
        captcha_token = await self._get_captcha_token()
        payload = {
            "prompt": prompt,
            "tags": tags,
            "title": title,
            "negative_tags": negative_tags,
            "generation_type": "TEXT",
            "make_instrumental": False,
            "mv": model,
            "continue_clip_id": audio_id,
            "continue_at": continue_at,
            "task": "extend",
            "token": captcha_token,
        }
        logger.info(f"extend_audio: clip={audio_id[:8]}, continue_at={continue_at}")
        try:
            data = await self._request(
                "POST", "/api/generate/v2/", json=payload, timeout=30, retry_auth=False
            )
        except Exception as e:
            if "422" in str(e) and "token" in str(e).lower():
                logger.warning("Extend 422 — CAPTCHA token invalid, re-solving...")
                self.captcha_solver.invalidate_token()
                captcha_token = await self.captcha_solver.get_token(force=True)
                payload["token"] = captcha_token
                data = await self._request(
                    "POST", "/api/generate/v2/", json=payload, timeout=30, retry_auth=False
                )
            else:
                raise
        clips = data.get("clips", [])
        return [self._map_audio_info(c) for c in clips]

    async def concatenate(self, clip_id: str) -> dict:
        """Concatenate extension clips into one complete song.

        POST https://studio-api.prod.suno.com/api/generate/concat/v2/
        """
        logger.info(f"concatenate: clip_id={clip_id[:8]}")
        data = await self._request("POST", "/api/generate/concat/v2/", json={"clip_id": clip_id}, timeout=15)
        return data

    async def generate_lyrics(self, prompt: str) -> dict:
        """Generate lyrics from a text prompt (two-phase: submit + poll).

        Phase 1: POST /api/generate/lyrics/ → {id}
        Phase 2: GET /api/generate/lyrics/{id} → poll until status=complete
        """
        logger.info(f"generate_lyrics: prompt='{prompt[:60]}'")
        submit = await self._request("POST", "/api/generate/lyrics/", json={"prompt": prompt})
        lyrics_id = submit.get("id")
        if not lyrics_id:
            raise Exception("No lyrics task ID returned")

        # Poll for completion
        for attempt in range(30):  # 30 * 2s = 60s max
            data = await self._request("GET", f"/api/generate/lyrics/{lyrics_id}")
            if data.get("status") == "complete":
                logger.info(f"generate_lyrics complete: title='{data.get('title', '?')}'")
                return data
            await asyncio.sleep(2)

        raise Exception(f"Lyrics generation timed out after 60s (id={lyrics_id})")

    # ─── Feed / Query API ────────────────────────────────────

    async def get_audio_info(self, ids: list[str] | None = None, page: int | None = None) -> list[dict]:
        """Get audio clip info by IDs, or paginated library.

        GET https://studio-api.prod.suno.com/api/feed/v2?ids=...&page=...
        """
        params = {}
        if ids:
            params["ids"] = ",".join(ids)
        if page is not None:
            params["page"] = str(page)

        data = await self._request("GET", "/api/feed/v2", params=params)
        clips = data.get("clips", []) if isinstance(data, dict) else data
        return [self._map_audio_info(c) for c in clips]

    async def get_feed_page(self, page: int = 0) -> dict:
        """Get a raw feed page with pagination metadata.

        Returns dict with 'clips' (mapped), 'raw_clips', 'num_total', 'current_page'.
        """
        data = await self._request("GET", "/api/feed/v2", params={"page": str(page)})
        raw_clips = data.get("clips", []) if isinstance(data, dict) else data
        return {
            "clips": [self._map_audio_info(c) for c in raw_clips],
            "raw_clips": raw_clips,
            "num_total": data.get("num_total", 0) if isinstance(data, dict) else len(raw_clips),
            "current_page": page,
        }

    async def get_clip(self, clip_id: str) -> dict:
        """Get raw clip data by ID.

        GET https://studio-api.prod.suno.com/api/clip/{clip_id}
        """
        return await self._request("GET", f"/api/clip/{clip_id}")

    # ─── Billing ─────────────────────────────────────────────

    async def get_credits(self) -> dict:
        """Get account credits (summary).

        GET https://studio-api.prod.suno.com/api/billing/info/
        """
        data = await self._request("GET", "/api/billing/info/")
        return {
            "credits_left": data.get("total_credits_left", 0),
            "period": data.get("period"),
            "monthly_limit": data.get("monthly_limit"),
            "monthly_usage": data.get("monthly_usage"),
        }

    async def get_billing_info(self) -> dict:
        """Get full billing/subscription info including models, features, plans.

        GET https://studio-api.prod.suno.com/api/billing/info/
        """
        return await self._request("GET", "/api/billing/info/")

    # ─── Models ──────────────────────────────────────────────

    async def get_models(self) -> list[dict]:
        """Get available generation models from billing/info endpoint.

        GET https://studio-api.prod.suno.com/api/billing/info/
        Extracts the 'models' array from the billing response.
        """
        data = await self._request("GET", "/api/billing/info/")
        raw_models = data.get("models", [])
        return [
            {
                "external_key": m.get("external_key", ""),
                "name": m.get("name", ""),
                "description": m.get("description", ""),
                "major_version": m.get("major_version", 0),
                "is_default": m.get("is_default_model", False),
                "is_default_free": m.get("is_default_free_model", False),
                "badges": m.get("badges", []),
                "can_use": m.get("can_use", True),
                "max_prompt_length": m.get("max_lengths", {}).get("prompt", 3000),
                "max_tags_length": m.get("max_lengths", {}).get("tags", 200),
                "capabilities": m.get("capabilities", []),
                "features": m.get("features", []),
            }
            for m in raw_models
        ]

    # ─── WAV Conversion ─────────────────────────────────────

    async def convert_wav(self, clip_id: str) -> dict:
        """Trigger WAV conversion for a clip (must call before get_wav_url).

        POST https://studio-api.prod.suno.com/api/gen/{clip_id}/convert_wav/
        """
        logger.info(f"convert_wav: {clip_id}")
        return await self._request("POST", f"/api/gen/{clip_id}/convert_wav/", json={}, timeout=30)

    async def get_wav_url(self, clip_id: str) -> str | None:
        """Get WAV file CDN URL. Returns None if conversion is still in progress.

        GET https://studio-api.prod.suno.com/api/gen/{clip_id}/wav_file/
        """
        data = await self._request("GET", f"/api/gen/{clip_id}/wav_file/", timeout=30)
        return data.get("wav_file_url")

    async def download_wav(
        self,
        clip_id: str,
        file_path: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> str:
        """Full WAV download flow: trigger conversion, poll URL, download.

        1. POST convert_wav (trigger)
        2. Poll get_wav_url until available (max 60s)
        3. Download from CDN
        """
        # Step 1: Trigger conversion
        if progress_callback:
            progress_callback(0.0, "Triggering WAV conversion...")
        await self.convert_wav(clip_id)

        # Step 2: Poll for URL
        if progress_callback:
            progress_callback(0.05, "Waiting for WAV conversion...")
        wav_url = None
        for attempt in range(30):  # 30 * 2s = 60s
            wav_url = await self.get_wav_url(clip_id)
            if wav_url:
                break
            logger.info(f"WAV not ready for {clip_id}, attempt {attempt + 1}/30")
            if progress_callback:
                progress_callback(0.05 + (attempt / 30) * 0.15, f"Converting... ({attempt + 1}s)")
            await asyncio.sleep(2)

        if not wav_url:
            raise Exception(f"WAV conversion timed out for {clip_id} after 60s")

        logger.info(f"WAV URL ready for {clip_id}: {wav_url}")

        # Step 3: Download
        if progress_callback:
            progress_callback(0.2, "Downloading WAV...")

        def _download_progress(p: float, msg: str):
            if progress_callback:
                if p < 0:
                    progress_callback(-1, f"Downloading WAV... {msg}")
                else:
                    progress_callback(0.2 + p * 0.8, msg)

        return await self.download_file(wav_url, file_path, progress_callback=_download_progress)

    # ─── File Download ───────────────────────────────────────

    async def download_file(
        self,
        url: str,
        file_path: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> str:
        """Download a file from URL to local path (CDN download, no auth needed).

        Args:
            url: Direct URL to the file (CDN)
            file_path: Local path to save the file
            progress_callback: Optional callback(progress: 0.0-1.0, message: str)
        """
        logger.info(f"Downloading: {url}")
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as dl_session:
            async with dl_session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"Download failed ({resp.status})")
                os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
                total = resp.content_length
                downloaded = 0
                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            if total:
                                progress = min(downloaded / total, 1.0)
                                msg = f"{downloaded // 1024}KB / {total // 1024}KB"
                            else:
                                progress = -1
                                msg = f"{downloaded // 1024}KB downloaded"
                            progress_callback(progress, msg)

        if progress_callback:
            size_kb = os.path.getsize(file_path) // 1024
            progress_callback(1.0, f"Complete ({size_kb}KB)")
        logger.info(f"Downloaded: {file_path} ({os.path.getsize(file_path) // 1024}KB)")
        return file_path

    # ─── Response Mapping ────────────────────────────────────

    @staticmethod
    def _map_audio_info(clip: dict) -> dict:
        """Map raw Suno clip object to a flat AudioInfo dict."""
        metadata = clip.get("metadata", {}) or {}
        return {
            "id": clip.get("id", ""),
            "title": clip.get("title"),
            "image_url": clip.get("image_url"),
            "audio_url": clip.get("audio_url"),
            "video_url": clip.get("video_url"),
            "status": clip.get("status", "unknown"),
            "duration": metadata.get("duration") or clip.get("duration"),
            "model_name": clip.get("model_name"),
            "tags": metadata.get("tags") or clip.get("tags"),
            "prompt": metadata.get("prompt"),
            "gpt_description_prompt": metadata.get("gpt_description_prompt"),
            "error_message": metadata.get("error_message"),
            "created_at": clip.get("created_at"),
            "lyric": _parse_lyrics(metadata.get("prompt")),
        }


def _parse_lyrics(text: str | None) -> str | None:
    """Clean up lyrics text: remove blank lines."""
    if not text:
        return None
    lines = [line for line in text.split("\n") if line.strip()]
    return "\n".join(lines) if lines else None


# ─── Module-level singleton ──────────────────────────────────
# Managed by suno_router.py — initialized once at startup

_client: SunoClient | None = None


async def get_client() -> SunoClient:
    """Get or create the global SunoClient singleton.

    Cookie is read from config.yaml → suno_api.cookie, or from
    the SUNO_COOKIE environment variable.
    """
    global _client
    if _client is not None and _client._initialized:
        return _client

    # Read cookie from config or env
    cookie = _load_cookie()
    if not cookie:
        raise Exception(
            "SUNO_COOKIE not configured. Set it in config.yaml (suno_api.cookie) "
            "or as SUNO_COOKIE environment variable."
        )

    _client = SunoClient(cookie)
    await _client.init()
    return _client


def reset_client():
    """Reset the global client (e.g. after cookie update)."""
    global _client
    if _client:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_client.close())
            else:
                loop.run_until_complete(_client.close())
        except Exception:
            pass
    _client = None


def _load_cookie() -> str:
    """Load cookie from config.yaml or SUNO_COOKIE env variable."""
    try:
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f) or {}
            cookie = cfg.get("suno_api", {}).get("cookie", "")
            if cookie and "__client" in cookie:
                return cookie
    except Exception:
        pass

    cookie = os.getenv("SUNO_COOKIE", "")
    if cookie and "__client" in cookie:
        return cookie

    return ""
