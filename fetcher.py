"""
Self-sufficient, rate-limit-resilient BookMyShow API client.

Strategy:
  1. Warm up a real browser session by visiting the homepage first
     so we carry genuine cookies and headers on the API call.
  2. Rotate through several User-Agent strings to prevent fingerprint
     blocking.
  3. Exponential back-off + jitter on 429 / 5xx responses.
  4. Hard timeout + retry on connection failures.
"""

from __future__ import annotations

import json
import logging
import random
import time
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("bms_fetcher")

# ---------------------------------------------------------------------------
# User-Agent pool – rotate per request to reduce fingerprint similarity.
# ---------------------------------------------------------------------------
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
]

BMS_HOME = "https://in.bookmyshow.com/"


def _pick_ua() -> str:
    return random.choice(_USER_AGENTS)


def _build_session(retries: int = 4, backoff_factor: float = 2.5) -> requests.Session:
    """Return a session with connection-level retry (network errors only)."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        # Only retry on connection/read errors at the urllib3 level;
        # HTTP 429 / 5xx are handled manually so we can log them.
        status_forcelist=[],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _warm_session(session: requests.Session, ua: str) -> None:
    """
    Visit the BMS homepage to acquire genuine cookies and trigger any
    Cloudflare / Akamai edge-token handshake before hitting the API.
    Failure here is non-fatal – we log and continue.
    """
    headers = {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        resp = session.get(BMS_HOME, headers=headers, timeout=15)
        logger.debug("Homepage warm-up: HTTP %s", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Homepage warm-up failed (non-fatal): %s", exc)


def _api_headers(ua: str, region_code: str = "HYD") -> dict:
    return {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://in.bookmyshow.com",
        "Referer": "https://in.bookmyshow.com/",
        "Sec-Ch-Ua": (
            '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
        ),
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-Region-Code": region_code,
        "X-Region-Slug": "hyderabad",
        "Connection": "keep-alive",
    }


def fetch_json(
    target_url: str,
    params: dict | None = None,
    timeout: int = 25,
    max_retries: int = 4,
    backoff_factor: float = 2.5,
) -> dict:
    """
    Fetch *target_url* from BookMyShow and return parsed JSON.

    Handles:
    - Homepage session warm-up for cookie / edge-token seeding.
    - User-Agent rotation.
    - Exponential back-off + jitter on HTTP 429, 403, 5xx.
    - Falls back to a fresh session on repeated 403 (edge block reset).
    """
    region_code = (params or {}).get("regionCode", "HYD")

    for attempt in range(1, max_retries + 1):
        ua = _pick_ua()
        session = _build_session(retries=2, backoff_factor=1.0)

        # Fresh warm-up each time we start a new attempt cycle.
        _warm_session(session, ua)

        # Small random pre-request jitter (0.5–2 s) to avoid burst patterns.
        time.sleep(random.uniform(0.5, 2.0))

        headers = _api_headers(ua, region_code)

        try:
            logger.info(
                "[Attempt %d/%d] GET %s params=%s",
                attempt,
                max_retries,
                target_url,
                params,
            )
            response = session.get(
                target_url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            wait = backoff_factor ** attempt + random.uniform(1, 3)
            logger.warning(
                "Network error on attempt %d: %s — retrying in %.1fs",
                attempt,
                exc,
                wait,
            )
            if attempt == max_retries:
                raise RuntimeError(
                    f"Network failure after {max_retries} attempts: {exc}"
                ) from exc
            time.sleep(wait)
            continue

        status = response.status_code
        print(f"BMS HTTP status: {status} (attempt {attempt}/{max_retries})")

        # ── Success ────────────────────────────────────────────────────────
        if status == 200:
            try:
                data = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"BMS returned non-JSON (status 200). "
                    f"Preview: {response.text[:300]!r}"
                ) from exc

            if not isinstance(data, dict):
                raise RuntimeError(
                    "BMS response root is not a JSON object."
                )
            return data

        # ── Rate limit / transient block ───────────────────────────────────
        if status in (429, 503):
            wait = backoff_factor ** attempt * 2 + random.uniform(2, 6)
            print(f"Rate-limited ({status}). Backing off {wait:.1f}s...")
            if attempt == max_retries:
                raise RuntimeError(
                    f"Still rate-limited after {max_retries} attempts (HTTP {status})."
                )
            time.sleep(wait)
            continue

        # ── Edge / geo block – longer pause before fresh session ───────────
        if status == 403:
            wait = backoff_factor ** attempt * 3 + random.uniform(5, 10)
            print(
                f"Blocked (403). Will retry with fresh session in {wait:.1f}s..."
            )
            if attempt == max_retries:
                raise RuntimeError(
                    f"Persistent 403 block after {max_retries} attempts. "
                    "Response preview: "
                    + response.text[:300]
                )
            time.sleep(wait)
            continue

        # ── Any other HTTP error ───────────────────────────────────────────
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if attempt == max_retries:
                raise RuntimeError(
                    f"HTTP {status} after {max_retries} attempts."
                ) from exc
            wait = backoff_factor ** attempt + random.uniform(1, 3)
            print(f"HTTP {status} — retrying in {wait:.1f}s...")
            time.sleep(wait)

    raise RuntimeError(f"Exhausted all {max_retries} retries for {target_url}.")
