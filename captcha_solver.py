"""
CAPTCHA Solver for Suno — Handles hCaptcha challenges using Playwright.

When Suno requires CAPTCHA verification for generation, this module:
1. Checks if CAPTCHA is required via /api/c/check
2. Opens a real browser window for the user to solve the challenge
3. Intercepts the generate request to capture the hCaptcha token
4. Returns the token for use in API calls

The token is cached and reused until it expires or a new CAPTCHA is required.
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("suno-manager")


class CaptchaSolver:
    """Manages hCaptcha token acquisition for Suno API generate calls."""

    def __init__(self, suno_client):
        """Initialize with a reference to the SunoClient instance.

        Args:
            suno_client: Initialized SunoClient with valid cookies/JWT
        """
        self.suno_client = suno_client
        self._cached_token: Optional[str] = None
        self._token_time: float = 0
        self._token_ttl: float = 120  # hCaptcha tokens typically valid ~2 min
        self._solving: bool = False
        self._solve_event: Optional[asyncio.Event] = None

    @property
    def is_solving(self) -> bool:
        return self._solving

    @property
    def has_valid_token(self) -> bool:
        if not self._cached_token:
            return False
        return (time.time() - self._token_time) < self._token_ttl

    async def check_captcha_required(self) -> bool:
        """Check if Suno requires CAPTCHA for generation.

        POST https://studio-api.prod.suno.com/api/c/check
        """
        try:
            data = await self.suno_client._request(
                "POST", "/api/c/check", json={"ctype": "generation"}, timeout=10
            )
            required = data.get("required", False)
            logger.info(f"CAPTCHA check: required={required}")
            return required
        except Exception as e:
            logger.warning(f"CAPTCHA check failed: {e} — assuming required")
            return True

    async def get_token(self, force: bool = False) -> Optional[str]:
        """Get an hCaptcha token, solving if necessary.

        Args:
            force: If True, solve a new CAPTCHA even if cached token exists

        Returns:
            hCaptcha token string, or None if not required
        """
        # Return cached token if still valid
        if not force and self.has_valid_token:
            logger.info("Using cached CAPTCHA token")
            return self._cached_token

        # Check if CAPTCHA is actually required
        if not force:
            required = await self.check_captcha_required()
            if not required:
                logger.info("CAPTCHA not required for generation")
                self._cached_token = None
                return None

        # If another solve is in progress, wait for it
        if self._solving and self._solve_event:
            logger.info("CAPTCHA solve already in progress, waiting...")
            await self._solve_event.wait()
            return self._cached_token

        # Solve CAPTCHA
        return await self._solve_captcha()

    async def _solve_captcha(self) -> Optional[str]:
        """Launch browser and let user solve hCaptcha manually."""
        self._solving = True
        self._solve_event = asyncio.Event()

        try:
            token = await self._browser_solve()
            if token:
                self._cached_token = token
                self._token_time = time.time()
                logger.info(f"CAPTCHA token acquired ({len(token)} chars)")
            return token
        except Exception as e:
            logger.error(f"CAPTCHA solve failed: {e}")
            raise
        finally:
            self._solving = False
            if self._solve_event:
                self._solve_event.set()

    async def _browser_solve(self) -> Optional[str]:
        """Open browser, navigate to suno.com/create, capture hCaptcha token."""
        try:
            from playwright.async_api import async_playwright  # type: ignore[import-not-found]
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        logger.info("Launching browser for CAPTCHA solving...")

        token_future: asyncio.Future = asyncio.get_running_loop().create_future()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                ],
            )

            context = await browser.new_context(
                user_agent=self.suno_client._default_headers.get(
                    "User-Agent",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36",
                ),
                viewport={"width": 1280, "height": 900},
            )

            # Inject cookies
            cookies = []
            # Add __session (JWT) cookie
            if self.suno_client.token:
                cookies.append({
                    "name": "__session",
                    "value": self.suno_client.token,
                    "domain": ".suno.com",
                    "path": "/",
                    "sameSite": "Lax",
                })
            # Add all parsed cookies from SunoClient
            for name, value in self.suno_client.cookies.items():
                cookies.append({
                    "name": name,
                    "value": str(value),
                    "domain": ".suno.com",
                    "path": "/",
                    "sameSite": "Lax",
                })
            await context.add_cookies(cookies)

            page = await context.new_page()

            # Intercept generate request to capture hCaptcha token
            async def handle_route(route):
                try:
                    request = route.request
                    post_data = request.post_data_json
                    if post_data and "token" in post_data and post_data["token"]:
                        captured_token = post_data["token"]
                        logger.info("hCaptcha token captured from generate request!")

                        # Also refresh JWT from the browser's auth header
                        auth_header = request.headers.get("authorization", "")
                        if auth_header.startswith("Bearer "):
                            new_jwt = auth_header[7:]
                            if new_jwt and new_jwt != self.suno_client.token:
                                self.suno_client.token = new_jwt
                                self.suno_client._token_refreshed_at = (
                                    asyncio.get_running_loop().time()
                                )
                                logger.info("JWT also refreshed from browser session")

                        if not token_future.done():
                            token_future.set_result(captured_token)
                    # Abort the actual generate request (we just needed the token)
                    await route.abort()
                except Exception as e:
                    logger.error(f"Route handler error: {e}")
                    if not token_future.done():
                        token_future.set_exception(e)
                    await route.abort()

            await page.route("**/api/generate/v2/**", handle_route)

            # Navigate to suno.com/create
            logger.info("Navigating to suno.com/create...")
            try:
                await page.goto(
                    "https://suno.com/create",
                    referer="https://www.google.com/",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
            except Exception as e:
                logger.warning(f"Page load issue (may still work): {e}")

            # Wait for the page to fully load (song list API call)
            try:
                await page.wait_for_response(
                    lambda resp: "/api/project/" in resp.url, timeout=30000
                )
                logger.info("Suno interface loaded")
            except Exception:
                logger.warning("Timed out waiting for project API — page may still be usable")

            # Close any popups
            try:
                close_btn = page.get_by_label("Close")
                await close_btn.click(timeout=2000)
            except Exception:
                pass

            logger.info(
                "Browser is open. Please solve the CAPTCHA:\n"
                "  1. Type something in the prompt box\n"
                "  2. Click 'Create'\n"
                "  3. Solve the hCaptcha challenge\n"
                "  4. The token will be captured automatically"
            )

            # Wait for the token (user solves CAPTCHA manually)
            try:
                token = await asyncio.wait_for(token_future, timeout=300)  # 5 min
            except asyncio.TimeoutError:
                logger.error("CAPTCHA solve timed out after 5 minutes")
                token = None

            # Clean up
            try:
                await browser.close()
            except Exception:
                pass

            return token

    def invalidate_token(self):
        """Mark the current token as invalid (e.g. after a 422 response)."""
        self._cached_token = None
        self._token_time = 0
        logger.info("CAPTCHA token invalidated")
