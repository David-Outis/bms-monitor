"""
BookMyShow premium-screen monitor.

Notifications:
  - ntfy.sh (topic: bms-doomsday)
  - Persistent alert while tickets are live
  - Periodic heartbeat ping
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
import unicodedata
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
)
from state_store import MonitorState

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
    show_details = data.get("ShowDetails")
    if not isinstance(show_details, list) or not show_details:
        raise RuntimeError("Invalid response: ShowDetails missing or empty.")

    matching = [
        d for d in show_details
        if isinstance(d, dict) and str(d.get("Date", "")) == target_date
    ]

    if not matching:
        returned = [d.get("Date") for d in show_details if isinstance(d, dict)]
        print(
            f"Note: Date {target_date} not yet scheduled at {venue_code or 'venue'} "
            f"(active dates on BMS: {returned})."
        )
        return []

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
    """
    Check if event matches target movie with exact, alias, token-overlap,
    and fuzzy near-matching.
    """
    titles = [profile["movie_title"], *profile.get("movie_aliases", [])]
    candidates = [
        normalize(c)
        for c in [
            parent_event.get("EventTitle", ""),
            child_event.get("EventName", ""),
        ]
        if c
    ]
    if not candidates:
        return False

    # 1. Exact or Substring match on configured title / aliases
    for t in titles:
        norm_t = normalize(t)
        if not norm_t:
            continue
        for cand in candidates:
            if norm_t in cand or cand in norm_t:
                return True

    # 2. Token overlap & spacing variations (e.g. "Dooms Day" vs "Doomsday", "Avengers 5")
    for cand in candidates:
        cand_words = set(cand.split())
        cand_text_no_space = cand.replace(" ", "")

        has_avengers = any("avenger" in w for w in cand_words) or "avenger" in cand_text_no_space
        has_doomsday = (
            any("doom" in w for w in cand_words)
            or "doomsday" in cand_text_no_space
            or "dooms" in cand_words
        )
        if has_avengers and has_doomsday:
            return True

        # 3. Fuzzy near-matching via SequenceMatcher (similarity >= 0.75 or sliding window >= 0.80)
        for t in titles:
            norm_t = normalize(t)
            if not norm_t:
                continue
            if difflib.SequenceMatcher(None, norm_t, cand).ratio() >= 0.75:
                return True
            cand_tokens = cand.split()
            target_token_count = len(norm_t.split())
            if len(cand_tokens) >= target_token_count:
                for i in range(len(cand_tokens) - target_token_count + 1):
                    window = " ".join(cand_tokens[i:i + target_token_count])
                    if difflib.SequenceMatcher(None, norm_t, window).ratio() >= 0.80:
                        return True

    return False


def language_matches(child_event: dict, configured_language: str, profile: dict | None = None) -> bool:
    """
    Check if event matches target language, handling variations like English, Eng, EN,
    and occurrences in EventLanguage or EventName (e.g. '(Eng)', '(English)').
    """
    target_langs = {normalize(configured_language)}
    if profile:
        target_langs.update(normalize(a) for a in profile.get("language_aliases", []) if a)

    norm_expected = normalize(configured_language)
    if "english" in norm_expected:
        target_langs.update(["english", "eng", "en"])
    elif "telugu" in norm_expected:
        target_langs.update(["telugu", "tel"])
    elif "hindi" in norm_expected:
        target_langs.update(["hindi", "hin"])

    # 1. Check EventLanguage field explicitly
    raw_event_lang = normalize(child_event.get("EventLanguage", ""))
    if raw_event_lang:
        event_lang_words = set(raw_event_lang.split())
        if any(tl in event_lang_words or tl == raw_event_lang for tl in target_langs):
            return True

    # 2. Check EventName tokens (e.g. "Avengers (Eng)", "Avengers (English)")
    raw_event_name = normalize(child_event.get("EventName", ""))
    if raw_event_name:
        name_words = set(raw_event_name.split())
        if any(tl in name_words for tl in target_langs):
            return True

    return False


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
    if mt == "ALL":
        return True
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
                if not language_matches(child_event, profile["language"], profile):
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
    interval = getattr(config, "HEARTBEAT_INTERVAL_SECONDS", 21600)
    if interval <= 0:
        return True
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

    all_shows: list = []
    successful_scopes: set = set()
    failed_scopes: list = []
    inter_delay = getattr(config, "INTER_REQUEST_DELAY", 2.0)

    for target_date in target_dates:
        print(f"\n{'=' * 65}\nCHECKING DATE: {target_date}\n{'=' * 65}")

        for venue_code in venue_codes:
            scope_label = f"{venue_code} / {target_date}"
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

            except Exception as exc:
                failed_scopes.append({
                    "venue_code": venue_code,
                    "date": target_date,
                    "error": str(exc),
                })
                print(f"\nWARNING: {scope_label} failed — {exc}")
                print("Previous state preserved. Continuing with other venues.")

    # ── Dynamic City-Wide Discovery for New Theatres ───────────────────
    if profile.get("check_all_hyderabad_theatres") and all_shows:
        known_event_codes = {s["event_code"] for s in all_shows if s.get("event_code")}
        for event_code in known_event_codes:
            for target_date in target_dates:
                try:
                    time.sleep(inter_delay)
                    byevent_url = "https://in.bookmyshow.com/api/v3/mobile/showtimes/byevent"
                    params = {
                        "regionCode": config.REGION_CODE,
                        "eventCode": event_code,
                        "dateCode": target_date,
                    }
                    ev_data = fetch_json(byevent_url, params=params, timeout=getattr(config, "REQUEST_TIMEOUT", 25))
                    for sd in ev_data.get("ShowDetails", []):
                        if str(sd.get("Date", "")) != target_date:
                            continue
                        for venue in sd.get("Venues", []):
                            v_code = venue.get("VenueCode")
                            v_name = venue.get("VenueName", "New Theatre")
                            for show in venue.get("ShowTimes", []):
                                s_id = show.get("SessionId")
                                if not s_id:
                                    continue
                                all_shows.append({
                                    "target_id": f"theatre_{v_code.lower()}",
                                    "theatre_name": v_name,
                                    "venue_code": v_code,
                                    "format_label": show.get("ScreenName") or show.get("Attributes") or "Standard",
                                    "movie": movie_title,
                                    "date": target_date,
                                    "time": show.get("ShowTimeDisplay", ""),
                                    "session_id": s_id,
                                    "event_code": event_code,
                                    "booking_url": f"https://in.bookmyshow.com/movies/{config.REGION_SLUG}/seat-layout/{event_code}/{v_code}/{s_id}/{target_date}",
                                })
                except Exception as exc:
                    logging.getLogger("bms_monitor").debug("Dynamic discovery error: %s", exc)

    if not successful_scopes:
        details = "; ".join(
            f"{i['venue_code']}/{i['date']}: {i['error']}"
            for i in failed_scopes
        )
        raise RuntimeError(
            f"All scopes failed. State not modified. Failures: {details}"
        )

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

    tz_name = os.getenv("BMS_TIMEZONE", "Asia/Kolkata")
    try:
        now = datetime.now(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, KeyError):
        now = datetime.now()

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

    # Attach booking URLs
    for show in current_shows:
        show["booking_url"] = get_booking_url(show)

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
        print("\nSending heartbeat ntfy to bms-doomsday...")
        notify_heartbeat(movie_title, run_summary)
        state.record_heartbeat(profile_key, now)

    # ── Ticket-found alerts ───────────────────────────────────────────
    if result["pending_changes"]:
        print(
            f"\nChanges detected: {len(result['pending_changes'])} event(s). "
            "Sending alert to bms-doomsday..."
        )
        notify_tickets_found(current_shows, movie_title)
        state.clear_pending_changes(profile_key)
        print("Pending changes cleared after alert dispatch.")

    elif current_shows:
        print(
            f"\n{len(current_shows)} session(s) still live. "
            "Re-pinging ntfy bms-doomsday so alert stays on-screen..."
        )
        notify_tickets_found(current_shows, movie_title)

    else:
        print("\nNo matching sessions live. No notification sent.")


if __name__ == "__main__":
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
    except OSError as exc:
        print(f"\nState/IO error: {exc}")
        raise SystemExit(1)
