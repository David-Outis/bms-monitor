"""BookMyShow monitor configuration.

ACTIVE_PROFILE:
    SANITY_TEST -> Ramayana / Telugu
    REAL_DATA   -> Avengers: Doomsday / English

Only theatre codes in active_theatres_for_alert are monitored.
"""

# ============================================================
# GENERAL SETTINGS
# ============================================================

ACTIVE_PROFILE = "REAL_DATA"

active_theatres_for_alert = [
    # Hyderabad
    "AC", "PCX", "ART", "AAA", "AMB",
    # Visakhapatnam (Vizag)
    "VIZ_VARUN", "VIZ_JAGD", "VIZ_CMR", "VIZ_CHIT", "VIZ_MIRAJ"
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
    "check_all_hyderabad_theatres": True,
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
    "AMB": [
        {
            "target_id": "amb_cinemas",
            "theatre_name": "AMB Cinemas: Gachibowli (HYD)",
            "venue_code": "AMBC",
            "format_label": "Laser / Premium",
            "match_type": "ALL",
            "region_code": "HYD",
            "region_slug": "hyderabad",
        },
    ],

    # ── Visakhapatnam (Vizag) ──────────────────────────────────
    "VIZ_VARUN": [
        {
            "target_id": "inox_varun_beach",
            "theatre_name": "INOX: Varun Beach, Beach Road (Vizag)",
            "venue_code": "INVB",
            "format_label": "Inox Varun Beach",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_JAGD": [
        {
            "target_id": "jagadamba_complex",
            "theatre_name": "Jagadamba Complex 4K Dolby Atmos (Vizag)",
            "venue_code": "JGDM",
            "format_label": "Jagadamba 4K Dolby Atmos",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_CMR": [
        {
            "target_id": "inox_cmr_central",
            "theatre_name": "INOX: CMR Central, Maddilapalem (Vizag)",
            "venue_code": "INCM",
            "format_label": "Inox CMR Central",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_CHIT": [
        {
            "target_id": "inox_chitralaya",
            "theatre_name": "INOX: Chitralaya Mall (Vizag)",
            "venue_code": "INVC",
            "format_label": "Inox Chitralaya",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
    "VIZ_MIRAJ": [
        {
            "target_id": "miraj_surya_central",
            "theatre_name": "Miraj Cinemas: Surya Central (Vizag)",
            "venue_code": "MBSJ",
            "format_label": "Miraj Surya Central",
            "match_type": "ALL",
            "region_code": "VIZ",
            "region_slug": "vizag",
        },
    ],
}
