"""
BookMyShow premium-screen monitor.

What it does each run:
  1. Fetches showtime JSON for every configured venue/date.
  2. Identifies sessions matching the active movie, language & screen format.
  3. Diffs against the persistent archive in state.json.
  4. If NEW / REAPPEARED / UPDATED sessions found → send ntfy alert (persistent,
     high-priority) + Gmail email.
  5. If tickets are CURRENTLY LIVE → always resend ntfy alert (persistent) so
     the notification stays on-screen until you manually dismiss it.
  6. Every HEARTBEAT_INTERVAL_SECONDS → send a quiet ntfy ping so you know
     the monitor is still running.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
import html
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

import config
from fetcher import fetch_json
from notifier import (
    notify_heartbeat,
    notify_tickets_found,
    send_success_email,
)
from state_store import MonitorState


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_FILE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bms_monitor")


# ============================================================
# CONFIGURATION HELPERS
# ============================================================

def get_active_profile() -> dict:
    """Return the selected movie profile dict from config.py."""
    name = config.ACTIVE_PROFILE.strip().upper()
    if name not in {"SANITY_TEST", "REAL_DATA"}:
        raise ValueError('ACTIVE_PROFILE must be "SANITY_TEST" or "REAL_DATA".')
    profile = getattr(config, name, None)
    if not isinstance(profile, dict):
        raise ValueError(f"{name} must be a dict in config.py.")
    required = {"movie_title", "movie_aliases", "language", "target_dates"}
    missing = required - set(profile)
    if missing:
        raise ValueError(f"{name} is missing keys: {sorted(missing)}")
    return profile


def get_active_targets() -> list:
    """Expand active theatre codes into their screen targets."""
    codes = config.active_theatres_for_alert
    if not isinstance(codes, list) or not codes:
        raise ValueError("active_theatres_for_alert must be a non-empty list.")
    targets = []
    for code in codes:
        if code not in config.THEATRE_TARGETS:
            raise ValueError(
                f"Unknown theatre code {code!r}. "
                f"Valid: {sorted(config.THEATRE_TARGETS)}"
            )
        targets.extend(config.THEATRE_TARGETS[code])
    if not targets:
        raise ValueError("No theatre targets configured.")
    return targets


# ============================================================
# DATE VALIDATION
# ============================================================

def validate_date_format(date_code: str) -> bool:
    try:
        datetime.strptime(date_code, "%Y%m%d")
        return True
    except (TypeError, ValueError):
        return False


def validate_response_date(
    data: dict, target_date: str, venue_code: str | None = None
) -> list:
    """
    Return ShowDetails entries matching target_date.
    Raises RuntimeError on date/venue mismatch or missing Event list.
    """
    show_details = data.get("ShowDetails")
    if not isinstance(show_details, list) or not show_details:
        raise RuntimeError("Invalid response: ShowDetails missing or empty.")

    matching = [
        d for d in show_details
        if isinstance(d, dict) and str(d.get("Date", "")) == target_date
    ]

    if not matching:
        returned = [d.get("Date") for d in show_details if isinstance(d, dict)]
        raise RuntimeError(
            f"Date mismatch. Requested {target_date}, "
            f"response had {returned}. Rejecting fallback data."
        )

    for detail in matching:
        if not isinstance(detail.get("Event"), list):
            raise RuntimeError(
                f"Invalid response for {venue_code or 'venue'}/{target_date}: "
                "ShowDetails entry missing Event list."
            )
        if venue_code:
            venue = detail.get("Venues")
            returned_code = venue.get("VenueCode") if isinstance(venue, dict) else None
            if str(returned_code or "").upper() != venue_code.upper():
                raise RuntimeError(
                    f"Venue mismatch. Requested {venue_code}, "
                    f"response had {returned_code!r}. Rejecting."
                )

    return matching


# ============================================================
# BOOKMYSHOW API
# ============================================================

def fetch_showtimes(target_date: str, venue_code: str) -> dict:
    """Fetch one venue/date and return raw BMS JSON."""
    params = {
        "dateCode": target_date,
        "venueCode": venue_code,
        "regionCode": config.REGION_CODE,
    }
    print(f"Fetching {venue_code} / {target_date} from BookMyShow...")
    data = fetch_json(
        target_url=config.API_URL,
        params=params,
        timeout=getattr(config, "REQUEST_TIMEOUT", 25),
    )
    print(f"Received JSON for {venue_code} / {target_date}.")
    return data


# ============================================================
# TEXT NORMALIZATION & MATCHING
# ============================================================

def normalize(value) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = value.casefold()
    value = re.sub(r"[^\w]+", " ", value)
    return " ".join(value.split())


def movie_matches(parent_event: dict, child_event: dict, profile: dict) -> bool:
    titles = [profile["movie_title"], *profile.get("movie_aliases", [])]
    candidates = [
        normalize(c)
        for c in [
            parent_event.get("EventTitle", ""),
            child_event.get("EventName", ""),
        ]
        if c
    ]
    return any(
        normalize(t) in cand
        for t in titles
        for cand in candidates
        if normalize(t)
    )


def language_matches(child_event: dict, configured_language: str) -> bool:
    expected = normalize(configured_language)
    if not expected:
        return False
    return any(
        expected in normalize(c)
        for c in [
            child_event.get("EventLanguage", ""),
            child_event.get("EventName", ""),
        ]
        if c
    )


# ============================================================
# THEATRE-SPECIFIC SCREEN MATCHING
# ============================================================

def matches_target(
    target: dict, parent_event: dict, child_event: dict, show: dict
) -> bool:
    mt = target["match_type"]
    attributes = normalize(show.get("Attributes"))
    dimension = normalize(child_event.get("EventDimension"))
    event_name = normalize(child_event.get("EventName"))
    screen_name = normalize(show.get("ScreenName"))

    if mt == "DOLBY_CINEMA":
        return (
            attributes == normalize("DOLBY CINEMA")
            or "dolby cinema" in dimension
            or "dolby cinema" in event_name
        )
    if mt == "PCX":
        return attributes == normalize("PCX Screen")
    if mt == "HDR_BY_BARCO":
        return "hdr by barco" in dimension or "hdr by barco" in event_name
    if mt == "EPIQ":
        if attributes and "epiq" not in attributes:
            return False
        return "epiq" in attributes or "epiq" in dimension or "epiq" in screen_name
    if mt == "LED":
        return "led" in attributes.split()
    raise ValueError(f"Unknown match_type: {mt}")


# ============================================================
# SHOW IDENTIFICATION
# ============================================================

def identify_shows(
    matching_details: list, target_date: str, target: dict, profile: dict
) -> list:
    identified = []
    for detail in matching_details:
        for parent_event in detail.get("Event", []):
            if not isinstance(parent_event, dict):
                continue
            for child_event in parent_event.get("ChildEvents", []):
                if not isinstance(child_event, dict):
                    continue
                if not movie_matches(parent_event, child_event, profile):
                    continue
                if not language_matches(child_event, profile["language"]):
                    continue
                for show in child_event.get("ShowTimes", []):
                    if not isinstance(show, dict):
                        continue
                    if str(show.get("ShowDateCode", "")) != target_date:
                        continue
                    if not matches_target(target, parent_event, child_event, show):
                        continue
                    session_id = show.get("SessionId")
                    event_code = child_event.get("EventCode")
                    if not session_id or not event_code:
                        print("Skipping show: missing SessionId or EventCode.")
                        continue
                    identified.append({
                        "target_id": target["target_id"],
                        "theatre_name": target["theatre_name"],
                        "venue_code": target["venue_code"],
                        "format_label": target["format_label"],
                        "movie": (
                            parent_event.get("EventTitle")
                            or child_event.get("EventName")
                            or profile["movie_title"]
                        ),
                        "language": (
                            child_event.get("EventLanguage") or profile["language"]
                        ),
                        "dimension": child_event.get("EventDimension", ""),
                        "event_code": event_code,
                        "session_id": session_id,
                        "date": str(show.get("ShowDateCode")),
                        "time": show.get("ShowTime", "Time not provided"),
                        "screen": show.get("ScreenName", "Screen not provided"),
                        "attributes": show.get("Attributes", ""),
                    })
    return identified


# ============================================================
# BOOKING URL & CONSOLE OUTPUT
# ============================================================

def get_booking_url(show: dict) -> str:
    return (
        "https://in.bookmyshow.com/movies/"
        f"{config.REGION_SLUG}/seat-layout/"
        f"{show['event_code']}/{show['venue_code']}/"
        f"{show['session_id']}/{show['date']}"
    )


def print_results(shows: list) -> None:
    if not shows:
        print("\nNo matching sessions found.")
        return
    print(f"\nFound {len(shows)} matching session(s):\n")
    for i, show in enumerate(shows, 1):
        print(f"--- Session {i} ---")
        for label, key in [
            ("Theatre", "theatre_name"), ("Format", "format_label"),
            ("Movie", "movie"), ("Language", "language"),
            ("Dimension", "dimension"), ("Date", "date"),
            ("Time", "time"), ("Screen", "screen"),
            ("Event", "event_code"), ("Session", "session_id"),
        ]:
            print(f"{label:10}: {show[key]}")
        print(f"{'Seat URL':10}: {get_booking_url(show)}")
        print()


# ============================================================
# EMAIL BODY BUILDERS
# ============================================================

def group_shows(shows: list) -> dict:
    grouped: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for show in shows:
        screen_key = f"{show['screen']} ({show['format_label']})"
        grouped[show["theatre_name"]][show["date"]][screen_key].append(show)
    return grouped


def build_email_body(shows: list, movie_title: str) -> str:
    grouped = group_shows(shows)
    lines = [
        f"🚨 BOOKMYSHOW ALERT: {movie_title}",
        f"FOUND {len(shows)} SESSION(S) ACROSS {len(grouped)} THEATRE(S).",
        "", "Matching sessions:", "",
    ]
    for theatre in sorted(grouped):
        lines += ["=" * 58, f"THEATRE: {theatre}", "=" * 58]
        for date_code in sorted(grouped[theatre]):
            try:
                fmt_date = datetime.strptime(date_code, "%Y%m%d").strftime("%d %b %Y")
            except ValueError:
                fmt_date = date_code
            lines += ["", f"DATE: {fmt_date}"]
            for screen in sorted(grouped[theatre][date_code]):
                lines += ["", f"  SCREEN: {screen}"]
                for show in sorted(
                    grouped[theatre][date_code][screen], key=lambda s: str(s["time"])
                ):
                    lines += [
                        f"    Movie: {show['movie']}",
                        f"    Language: {show['language']}",
                        f"    Format: {show['dimension'] or show['format_label']}",
                        f"    Showtime: {show['time']}",
                        f"    Link: {get_booking_url(show)}",
                        "",
                    ]
    lines += [
        "-" * 58,
        "Automated BookMyShow alert. Open link to verify availability.",
    ]
    return "\n".join(lines)


def build_email_html(shows: list, movie_title: str) -> str:
    grouped = group_shows(shows)
    parts = [
        "<html><body>",
        f"<h2>🚨 BOOKMYSHOW ALERT: {html.escape(movie_title)}</h2>",
        f"<p><strong>Found {len(shows)} session(s) across {len(grouped)} theatre(s).</strong></p>",
    ]
    for theatre in sorted(grouped):
        parts.append(f"<h2>{html.escape(theatre)}</h2>")
        for date_code in sorted(grouped[theatre]):
            try:
                fmt_date = datetime.strptime(date_code, "%Y%m%d").strftime("%d %b %Y")
            except ValueError:
                fmt_date = date_code
            parts.append(f"<h3>{html.escape(fmt_date)}</h3>")
            for screen in sorted(grouped[theatre][date_code]):
                parts.append(f"<h4>{html.escape(screen)}</h4><ul>")
                for show in sorted(
                    grouped[theatre][date_code][screen], key=lambda s: str(s["time"])
                ):
                    url = html.escape(get_booking_url(show), quote=True)
                    parts.append(
                        "<li>"
                        f"<strong>{html.escape(str(show['time']))}</strong>"
                        f" — {html.escape(str(show['movie']))}"
                        f" ({html.escape(str(show['language']))})"
                        f" — {html.escape(str(show['dimension']))}"
                        f' — <a href="{url}">BookMyShow seat layout</a>'
                        "</li>"
                    )
                parts.append("</ul>")
    parts += [
        "<hr>",
        "<p>Automated BookMyShow alert.</p>",
        "<p>Open link to verify seat availability.</p>",
        "</body></html>",
    ]
    return "\n".join(parts)


# ============================================================
# PROFILE STATE KEY
# ============================================================

def _profile_state_key(profile: dict, targets: list) -> str:
    payload = {
        "movie_title": normalize(profile["movie_title"]),
        "language": normalize(profile["language"]),
        "target_ids": sorted(t["target_id"] for t in targets),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


# ============================================================
# HEARTBEAT LOGIC
# ============================================================

def _should_send_heartbeat(
    state: MonitorState,
    profile_key: str,
    now: datetime,
) -> bool:
    """Return True if HEARTBEAT_INTERVAL_SECONDS has elapsed since last heartbeat.
    If interval is 0 (default), always send on every run."""
    interval = getattr(config, "HEARTBEAT_INTERVAL_SECONDS", 0)
    if interval <= 0:
        return True  # Send every run
    last_raw = state.get_last_heartbeat(profile_key)
    if not last_raw:
        return True
    try:
        last_dt = datetime.fromisoformat(last_raw)
        if now.tzinfo and last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=now.tzinfo)
        return (now - last_dt).total_seconds() >= interval
    except (ValueError, TypeError):
        return True


# ============================================================
# MAIN MONITOR
# ============================================================

def main() -> None:
    profile = get_active_profile()
    targets = get_active_targets()

    movie_title = profile["movie_title"]
    target_dates = profile["target_dates"]

    if not target_dates:
        raise ValueError(f"{config.ACTIVE_PROFILE}.target_dates is empty.")

    for d in target_dates:
        if not validate_date_format(d):
            raise ValueError(f"Invalid date {d!r}. Use YYYYMMDD.")

    if len(target_dates) != len(set(target_dates)):
        raise ValueError("target_dates contains duplicates.")

    venue_codes = list(dict.fromkeys(t["venue_code"] for t in targets))

    print("\n" + "=" * 65)
    print(f"ACTIVE PROFILE : {config.ACTIVE_PROFILE}")
    print(f"MOVIE          : {movie_title}")
    print(f"LANGUAGE       : {profile['language']}")
    print(f"THEATRE CODES  : {config.active_theatres_for_alert}")
    print(f"TARGET DATES   : {target_dates}")
    print(f"VENUES         : {venue_codes}")
    print("=" * 65)

    # ── Fetch & identify ──────────────────────────────────────────────
    all_shows: list = []
    successful_scopes: set = set()
    failed_scopes: list = []
    inter_delay = getattr(config, "INTER_REQUEST_DELAY", 2.0)

    for target_date in target_dates:
        print(f"\n{'=' * 65}\nCHECKING DATE: {target_date}\n{'=' * 65}")

        for venue_code in venue_codes:
            scope_label = f"{venue_code} / {target_date}"

            # Polite inter-request delay
            time.sleep(inter_delay)

            try:
                data = fetch_showtimes(target_date, venue_code)
                matching_details = validate_response_date(data, target_date, venue_code)

                venue_targets = [t for t in targets if t["venue_code"] == venue_code]
                scope_shows: list = []
                for target in venue_targets:
                    found = identify_shows(matching_details, target_date, target, profile)
                    if found:
                        print(
                            f"\n{target['theatre_name']} – "
                            f"{target['format_label']}: "
                            f"{len(found)} session(s)"
                        )
                        print_results(found)
                    scope_shows.extend(found)

                successful_scopes.add((venue_code, target_date))
                all_shows.extend(scope_shows)
                print(
                    f"SUCCESS: {scope_label} — "
                    f"{len(scope_shows)} matching session(s)."
                )

            except Exception as exc:  # noqa: BLE001
                failed_scopes.append({
                    "venue_code": venue_code,
                    "date": target_date,
                    "error": str(exc),
                })
                print(f"\nWARNING: {scope_label} failed — {exc}")
                print("Previous state preserved. Continuing with other venues.")

    # ── Abort if everything failed ────────────────────────────────────
    if not successful_scopes:
        details = "; ".join(
            f"{i['venue_code']}/{i['date']}: {i['error']}"
            for i in failed_scopes
        )
        raise RuntimeError(
            f"All scopes failed. State not modified. Failures: {details}"
        )

    # ── Deduplicate ───────────────────────────────────────────────────
    unique_shows: list = []
    seen: set = set()
    for show in all_shows:
        key = (show["target_id"], show["date"], str(show["session_id"]))
        if key not in seen:
            seen.add(key)
            unique_shows.append(show)

    print(f"\n{'=' * 65}")
    print(f"TOTAL MATCHING SESSIONS : {len(unique_shows)}")
    print(f"SUCCESSFUL SCOPES       : {len(successful_scopes)}")
    print(f"FAILED SCOPES           : {len(failed_scopes)}")
    print("=" * 65)

    if failed_scopes:
        print("\nPARTIAL FETCH WARNING:")
        for item in failed_scopes:
            print(f"  - {item['venue_code']}/{item['date']}: {item['error']}")

    # ── Timestamps ────────────────────────────────────────────────────
    tz_name = os.getenv("BMS_TIMEZONE", "Asia/Kolkata")
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, KeyError):
        # tzdata package not installed (common on Windows without it)
        now = datetime.now()

    # ── Persistent state ──────────────────────────────────────────────
    state_file = Path(os.getenv("BMS_STATE_FILE", "state/state.json"))
    state = MonitorState(state_file)
    profile_key = _profile_state_key(profile, targets)

    result = state.record_partial_snapshot(
        profile_key=profile_key,
        shows=unique_shows,
        successful_scopes=successful_scopes,
        now=now,
    )

    print(f"\nState updated: {state_file}")
    print(f"Archived sessions       : {len(result['archive'])}")
    print(f"Pending change events   : {len(result['pending_changes'])}")

    # ── Active date filter for email/ntfy bodies ──────────────────────
    active_dates = {str(d) for d in target_dates}
    current_shows = sorted(
        [
            r for r in result["archive"]
            if r.get("status") == "currently_listed"
            and str(r.get("date", "")) in active_dates
        ],
        key=lambda s: (
            str(s.get("date", "")),
            str(s.get("theatre_name", "")),
            str(s.get("screen", "")),
            str(s.get("time", "")),
        ),
    )

    # ── Heartbeat ntfy (every HEARTBEAT_INTERVAL_SECONDS) ────────────
    if _should_send_heartbeat(state, profile_key, now):
        run_summary = (
            f"Run at {now.strftime('%d %b %Y %H:%M')} IST\n"
            f"Scopes checked: {len(successful_scopes)}/{len(successful_scopes) + len(failed_scopes)}\n"
            f"Currently live sessions: {len(current_shows)}\n"
            + (
                f"⚠️ {len(failed_scopes)} scope(s) failed this run."
                if failed_scopes else "All scopes healthy."
            )
        )
        print("\nSending heartbeat ntfy...")
        notify_heartbeat(movie_title, run_summary)
        state.record_heartbeat(profile_key, now)

    # ── Ticket-found alerts ───────────────────────────────────────────
    # Attach booking URLs to each show so notifier can include them.
    for show in current_shows:
        show["booking_url"] = get_booking_url(show)

    # Case A: New / reappeared / updated sessions → send change alert
    if result["pending_changes"]:
        print(
            f"\nChanges detected: {len(result['pending_changes'])} event(s). "
            "Sending alert..."
        )
        notify_tickets_found(current_shows, movie_title)
        try:
            send_success_email(
                current_shows,
                movie_title,
                get_booking_url,
                build_email_body,
                build_email_html,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Email delivery failed (ntfy alert still sent): {exc}")
        else:
            state.clear_pending_changes(profile_key)
            print("Pending changes cleared after successful email delivery.")

    # Case B: No new changes but tickets are STILL LIVE
    # → resend persistent ntfy every run so phone notification stays visible.
    elif current_shows:
        print(
            f"\n{len(current_shows)} session(s) still live. "
            "Re-pinging ntfy so alert stays on-screen..."
        )
        notify_tickets_found(current_shows, movie_title)

    # Case C: Nothing live, nothing changed.
    else:
        print("\nNo matching sessions live. No notification sent.")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    import smtplib

    try:
        main()
    except requests.exceptions.HTTPError as exc:
        print(f"\nHTTP error: {exc}")
        raise SystemExit(1)
    except requests.exceptions.RequestException as exc:
        print(f"\nNetwork error: {exc}")
        raise SystemExit(1)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"\nConfiguration/data error: {exc}")
        raise SystemExit(1)
    except (smtplib.SMTPException, OSError) as exc:
        print(f"\nEmail/state error: {exc}")
        raise SystemExit(1)
