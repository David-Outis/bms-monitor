"""
Notification dispatcher for the BookMyShow monitor.

Channels supported:
  1. ntfy.sh  – instant push to phone/desktop (primary, free, no account needed)
  2. Gmail    – detailed HTML email with booking links

ntfy topics:
  BMS_NTFY_TOPIC_ALERT     – ticket-found alerts (persistent / high-priority)
  BMS_NTFY_TOPIC_HEARTBEAT – hourly "monitor is alive" pings (or same topic)

Both topics can be the same value if you only want one topic.
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage

import requests

logger = logging.getLogger("bms_notifier")

NTFY_BASE = "https://ntfy.sh"


# ============================================================
# NTFY
# ============================================================

def _ntfy_topic(env_key: str) -> str | None:
    return os.getenv(env_key, "").strip() or None


def send_ntfy(
    topic: str,
    title: str,
    message: str,
    priority: str = "default",
    tags: list[str] | None = None,
    persistent: bool = False,
) -> bool:
    """
    POST a notification to ntfy.sh.

    priority: min | low | default | high | urgent
    persistent: if True the notification stays on-screen until dismissed
                (uses ntfy 'require_interaction' header + 'persistent' tag).
    Returns True on success.
    """
    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
        "Tags": ",".join((tags or []) + (["loudspeaker"] if persistent else [])),
    }

    if persistent:
        # Keeps the notification visible until manually dismissed on Android.
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
            logger.info("ntfy sent → %s [%s]", topic, priority)
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
    full_body: str = "",
) -> None:
    """
    Send a compact, persistent ntfy alert when tickets are found.
    One line per session: Theatre | Format | Date | Time | Link
    Re-sent every run while sessions are live.
    """
    topic = _ntfy_topic("BMS_NTFY_TOPIC_ALERT")
    if not topic:
        logger.info("BMS_NTFY_TOPIC_ALERT not set — skipping ntfy ticket alert.")
        return

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

    send_ntfy(
        topic=topic,
        title=f"🚨 {movie_title} — BOOK NOW",
        message=body,
        priority="urgent",
        tags=["rotating_light", "ticket"],
        persistent=True,
    )


def notify_heartbeat(movie_title: str, run_summary: str) -> None:
    """
    Send a low-priority hourly heartbeat so you know the monitor is alive.
    """
    topic = _ntfy_topic("BMS_NTFY_TOPIC_HEARTBEAT")
    if not topic:
        logger.info("BMS_NTFY_TOPIC_HEARTBEAT not set — skipping heartbeat.")
        return

    send_ntfy(
        topic=topic,
        title=f"✅ BMS Monitor alive — {movie_title}",
        message=run_summary,
        priority="min",
        tags=["white_check_mark"],
        persistent=False,
    )


# ============================================================
# EMAIL (unchanged logic, now lives here)
# ============================================================

def send_success_email(
    shows: list,
    movie_title: str,
    get_booking_url_fn,
    build_body_fn,
    build_html_fn,
) -> None:
    sender = os.getenv("BMS_EMAIL_SENDER", "").strip()
    recipients_raw = os.getenv("BMS_EMAIL_RECIPIENTS", "")
    recipients = [
        a.strip()
        for a in re.split(r"[,;]", recipients_raw)
        if a.strip()
    ]
    app_password = os.getenv("BMS_GMAIL_APP_PASSWORD", "").replace(" ", "")

    if not sender or not recipients or not app_password:
        logger.warning(
            "Email credentials incomplete — skipping email. "
            "Set BMS_EMAIL_SENDER, BMS_EMAIL_RECIPIENTS, BMS_GMAIL_APP_PASSWORD."
        )
        return

    theatre_count = len({s["theatre_name"] for s in shows})
    msg = EmailMessage()
    msg["Subject"] = (
        f"🚨 SHOW FOUND: {movie_title} | "
        f"{len(shows)} session(s) | {theatre_count} theatre(s)"
    )
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(build_body_fn(shows, movie_title))
    msg.add_alternative(build_html_fn(shows, movie_title), subtype="html")

    print(f"Sending email to {len(recipients)} recipient(s)...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(sender, app_password)
        server.send_message(msg, from_addr=sender, to_addrs=recipients)
    print("SUCCESS: Email notification sent.")
