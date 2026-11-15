"""
Self-sufficient, rate-limit & Cloudflare resilient BookMyShow API client.

Strategy:
  1. Uses `curl_cffi` (with real browser/mobile TLS fingerprint impersonation)
     when available, with fallback to standard `requests`.
  2. Mobile App API Headers: targets BookMyShow's native /mobile/showtimes API
     using official Android/Chrome client signatures.
  3. No 'br' in Accept-Encoding unless supported, preventing compressed garbage data.
  4. Exponential backoff and jitter on 429 / 5xx responses.
"""

from __future__ import annotations

import json
import logging
import random
import time
from urllib.parse import urlencode

logger = logging.getLogger("bms_fetcher")

# Try importing curl_cffi for native Chrome/Android TLS fingerprint impersonation
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CURL_CFFI = False

# Mobile app headers that BookMyShow's API gateway trusts without web challenges
MOBILE_HEADERS = {
    "User-Agent": "BookMyShow/Android/14.9.2 (Linux; Android 14; Pixel 8 Pro; Build/UQ1A.240205.004)",
    "x-app-code": "ANDROID",
    "x-app-platform": "ANDROID",
    "x-app-version": "14.9.2",
    "x-device-type": "mobile",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-IN,en;q=0.9",
    "Connection": "keep-alive",
}

DESKTOP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Origin": "https://in.bookmyshow.com",
    "Referer": "https://in.bookmyshow.com/",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
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
    Fetch showtimes JSON from BookMyShow with anti-blocking resilience.
    """
    region_code = (params or {}).get("regionCode", "HYD")
    region_slug = (params or {}).get("regionSlug", "hyderabad")

    headers_list = [
        # Strategy 1: Native Mobile App Client (Primary for API endpoints)
        dict(MOBILE_HEADERS, **{
            "x-region-code": region_code,
            "x-region-slug": region_slug,
        }),
        # Strategy 2: Official Web Desktop Client
        dict(DESKTOP_HEADERS, **{
            "X-Region-Code": region_code,
            "X-Region-Slug": region_slug,
        }),
    ]

    for attempt in range(1, max_retries + 1):
        # Alternate between mobile client signature and desktop signature
        headers = headers_list[(attempt - 1) % len(headers_list)]
        time.sleep(random.uniform(0.5, 1.5))

        try:
            logger.info(
                "[Attempt %d/%d] GET %s params=%s",
                attempt,
                max_retries,
                target_url,
                params,
            )

            if HAS_CURL_CFFI:
                # Impersonate authentic Chrome TLS fingerprint to bypass Cloudflare
                impersonate = "chrome131" if "Sec-Ch-Ua" in headers else "chrome124"
                response = cffi_requests.get(
                    target_url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                    impersonate=impersonate,
                )
            else:
                response = cffi_requests.get(
                    target_url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )

        except Exception as exc:
            wait = backoff_factor ** attempt + random.uniform(1, 3)
            logger.warning(
                "Network/Client error on attempt %d: %s — retrying in %.1fs",
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
            except Exception as exc:
                preview = response.text[:300] if hasattr(response, "text") else str(response.content[:300])
                raise RuntimeError(
                    f"BMS returned non-JSON (status 200). Preview: {preview!r}"
                ) from exc

            if not isinstance(data, dict):
                raise RuntimeError("BMS response root is not a JSON object.")
            return data

        # ── Rate limit / transient block ───────────────────────────────────
        if status in (429, 503):
            wait = backoff_factor ** attempt * 2 + random.uniform(2, 5)
            print(f"Rate-limited ({status}). Backing off {wait:.1f}s...")
            if attempt == max_retries:
                raise RuntimeError(f"Still rate-limited after {max_retries} attempts (HTTP {status}).")
            time.sleep(wait)
            continue

        # ── Cloudflare 403 Challenge ──────────────────────────────────────
        if status == 403:
            wait = backoff_factor ** attempt * 2 + random.uniform(3, 6)
            print(f"Received 403 block. Switching signature and retrying in {wait:.1f}s...")
            if attempt == max_retries:
                preview = response.text[:300] if hasattr(response, "text") else str(response.content[:300])
                raise RuntimeError(f"Persistent 403 block after {max_retries} attempts. Response preview: {preview}")
            time.sleep(wait)
            continue

        # ── Any other HTTP error ───────────────────────────────────────────
        if attempt == max_retries:
            raise RuntimeError(f"HTTP {status} after {max_retries} attempts.")
        wait = backoff_factor ** attempt + random.uniform(1, 3)
        time.sleep(wait)

    raise RuntimeError(f"Exhausted all {max_retries} retries for {target_url}.")
