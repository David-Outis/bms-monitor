"""BookMyShow monitor configuration.

ACTIVE_PROFILE:
    SANITY_TEST -> Ramayana / Telugu
    REAL_DATA   -> Avengers: Doomsday / English

Strictly monitored theatres:
    Hyderabad:
        - Allu Cinemas: Kokapet (Dolby Cinema)
        - Prasads Multiplex (PCX & HDR by Barco)
        - ART Cinemas: Vanasthalipuram (EPIQ)
        - AAA Cinemas: Ameerpet (LED)
    Visakhapatnam:
        - Jagadamba Complex (4K Dolby Atmos)
        - Sri Melody (HDR Dolby Atmos)
        - Sangam Theatre (4K Dolby Atmos)
        - Sarat Theatre (4K Laser Dolby Atmos)
        - AAA Movies Vizag (Dolby Atmos / 4K)
"""

# ============================================================
# GENERAL SETTINGS
# ============================================================

ACTIVE_PROFILE = "REAL_DATA"

active_theatres_for_alert = [
    # Hyderabad (strictly the 4 original theatres)
    "AC", "PCX", "ART", "AAA",
    # Visakhapatnam (strictly the 5 requested theatres)
    "VIZ_JAGD", "VIZ_MELD", "VIZ_SNGM", "VIZ_SRTR", "VIZ_AAA"
]

REGION_CODE = "HYD"
REGION_SLUG = "hyderabad"

API_URL = "https://in.bookmyshow.com/api/v3/mobile/showtimes/byvenue"
REQUEST_TIMEOUT = 25             # Seconds for each BMS HTTP call

# Delay between consecutive venue/date requests (seconds).
# Keeps burst rate below BMS throttle thresholds.
INTER_REQUEST_DELAY = 2.0

# ============================================================
# NOTIFICATIONS
# ============================================================

# ntfy.sh topics are set via environment variables (see .env.example):
#   BMS_NTFY_TOPIC_ALERT      – urgent push when tickets go live
#   BMS_NTFY_TOPIC_HEARTBEAT  – quiet hourly "monitor is alive" ping
#
# Heartbeat sent once every 6 hours (21600 seconds).
HEARTBEAT_INTERVAL_SECONDS = 21600

# ============================================================
# SANITY TEST
# ============================================================

SANITY_TEST = {
    "movie_title": "Ramayana",
    "movie_aliases": [],
    "language": "Telugu",
    "target_dates": ["20261115"],
}


# ============================================================
# REAL MONITORING
# ============================================================

REAL_DATA = {
    "movie_title": "Avengers: Doomsday",
    "movie_aliases": [
        "Avengers Doomsday",
        "Avengers: Doomsday",
        "Avengers Dooms Day",
        "Avengers: Dooms Day",
        "Avengers - Doomsday",
        "Avengers 5",
        "Avengers 5: Doomsday",
        "Doomsday",
    ],
    "language": "English",
    "language_aliases": ["English", "Eng", "EN"],
    "target_dates": ["20261217", "20261218", "20261219", "20261220", "20261221"],
    "check_all_hyderabad_theatres": False,
}


# ============================================================
# THEATRE / PREMIUM FORMAT TARGETS
# ============================================================

THEATRE_TARGETS = {
    # ── Hyderabad ──────────────────────────────────────────────
    "AC": [
        {
            "target_id": "allu_dolby_cinema",
            "theatre_name": "Allu Cinemas: Kokapet (HYD)",
            "venue_code": "ALUC",
            "format_label": "Dolby Cinema",
            "match_type": "DOLBY_CINEMA",
            "region_code": "HYD",
            "region_slug": "hyderabad",
        },
    ],
    "PCX": [
        {
            "target_id": "prasads_pcx",
            "theatre_name": "Prasads Multiplex (HYD)",
            "venue_code": "PRHN",
            "format_label": "PCX",
            "match_type": "PCX",
            "region_code": "HYD",
            "region_slug": "hyderabad",
        },
        {
            "target_id": "prasads_hdr_by_barco",
            "theatre_name": "Prasads Multiplex (HYD)",
            "venue_code": "PRHN",
            "format_label": "HDR by Barco",
            "match_type": "HDR_BY_BARCO",
            "region_code": "HYD",
            "region_slug": "hyderabad",
        },
    ],
    "ART": [
        {
            "target_id": "art_epiq",
            "theatre_name": "ART Cinemas, Vanasthalipuram (HYD)",
            "venue_code": "ACEV",
            "format_label": "EPIQ",
            "match_type": "EPIQ",
            "region_code": "HYD",
            "region_slug": "hyderabad",
        },
    ],
    "AAA": [
        {
            "target_id": "aaa_led",
            "theatre_name": "AAA Cinemas, Ameerpet (HYD)",
            "venue_code": "ACAS",
            "format_label": "LED",
            "match_type": "LED",
            "region_code": "HYD",
            "region_slug": "hyderabad",
        },
    ],

    # ── Visakhapatnam (Vizag) ──────────────────────────────────
    "VIZ_JAGD": [
        {
            "target_id": "jagadamba_complex",
            "theatre_name": "Jagadamba Complex (Vizag)",
            "venue_code": "JGDM",
            "format_label": "4K Dolby Atmos",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_MELD": [
        {
            "target_id": "melody_theatre",
            "theatre_name": "Sri Melody Theatre (Vizag)",
            "venue_code": "MELD",
            "format_label": "HDR Dolby Atmos",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_SNGM": [
        {
            "target_id": "sangam_theatre",
            "theatre_name": "Sangam Theatre (Vizag)",
            "venue_code": "JTYS",
            "format_label": "4K Dolby Atmos",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_SRTR": [
        {
            "target_id": "sarat_theatre",
            "theatre_name": "Sarat Theatre (Vizag)",
            "venue_code": "STTR",
            "format_label": "4K Laser Dolby Atmos",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_AAA": [
        {
            "target_id": "aaa_movies_vizag",
            "theatre_name": "AAA Movies (Vizag)",
            "venue_code": "ACAS",
            "format_label": "Dolby Atmos / 4K",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
}
