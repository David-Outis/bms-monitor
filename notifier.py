"""
Notification dispatcher for the BookMyShow monitor.

Channels supported:
  - ntfy.sh (Dedicated topic: bms-doomsday)
"""

from __future__ import annotations

import logging
import os
import requests

logger = logging.getLogger("bms_notifier")

NTFY_BASE = "https://ntfy.sh"


def _ntfy_topic(env_key: str, default: str = "bms-doomsday") -> str:
    return os.getenv(env_key, "").strip() or default


def send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    tags: list[str] | None = None,
    click_url: str | None = None,
    sound: str | None = None,
    persistent: bool = False,
) -> bool:
    """
    POST a notification to ntfy.sh.
    Ensures HTTP headers remain strictly ASCII-compliant to avoid header encoding errors.
    """
    clean_title = title.encode("ascii", "ignore").decode("ascii").strip()
    headers: dict[str, str] = {
        "Title": clean_title or "BMS Monitor Alert",
        "Priority": priority,
        "Tags": ",".join((tags or []) + (["loudspeaker"] if persistent else [])),
    }

    if sound:
        headers["Sound"] = sound

    if click_url:
        headers["Click"] = click_url
        headers["Actions"] = f"view, Select Seats, {click_url}"
    elif persistent:
        headers["Require-Interaction"] = "1"
        headers["Actions"] = "view, Open BookMyShow, https://in.bookmyshow.com/"

    ntfy_token = os.getenv("BMS_NTFY_TOKEN", "").strip()
    if ntfy_token:
        headers["Authorization"] = f"Bearer {ntfy_token}"

    url = f"{NTFY_BASE}/{topic}"
    try:
        response = requests.post(
            url,
            data=message.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        if response.status_code == 200:
            logger.info("ntfy sent -> %s [%s]", topic, priority)
            return True
        logger.warning(
            "ntfy HTTP %s for topic %s: %s",
            response.status_code,
            topic,
            response.text[:200],
        )
        return False
    except requests.RequestException as exc:
        logger.error("ntfy request failed: %s", exc)
        return False


def notify_tickets_found(
    shows: list,
    movie_title: str,
) -> None:
    """
    Send a persistent, high-priority ntfy alarm to bms-doomsday when tickets are found.
    Re-sent every run while sessions are live.
    """
    topic = _ntfy_topic("BMS_NTFY_TOPIC_ALERT", "bms-doomsday")

    lines = []
    seen = set()
    for show in sorted(shows, key=lambda s: (s["date"], s["theatre_name"], s["time"])):
        key = (show["theatre_name"], show["format_label"], show["date"], show["session_id"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"🎬 {show['theatre_name']} | {show['format_label']}\n"
            f"   {show['date']} @ {show['time']}\n"
            f"   {show.get('booking_url', '')}"
        )

    body = f"🎟 {len(shows)} session(s) live!\n\n" + "\n\n".join(lines)
    first_url = shows[0].get("booking_url", "https://in.bookmyshow.com/") if shows else "https://in.bookmyshow.com/"

    send_ntfy(
        topic=topic,
        title=f"BMS ALERT: {movie_title} - BOOK NOW",
        message=body,
        priority="urgent",
        tags=["rotating_light", "ticket", "loudspeaker"],
        click_url=first_url,
        sound="alarm",
        persistent=True,
    )


def notify_heartbeat(movie_title: str, run_summary: str) -> None:
    """
    Send a low-priority heartbeat to bms-doomsday.
    """
    topic = _ntfy_topic("BMS_NTFY_TOPIC_HEARTBEAT", "bms-doomsday")

    send_ntfy(
        topic=topic,
        title=f"BMS Monitor alive - {movie_title}",
        message=run_summary,
        priority="min",
        tags=["white_check_mark"],
        persistent=False,
    )
