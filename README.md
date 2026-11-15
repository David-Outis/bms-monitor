# BookMyShow Monitor (Self-Sufficient Version)

Direct BookMyShow monitor that identifies premium screens (Dolby Cinema, PCX, EPIQ, LED), generates seat-layout booking links, tracks changes via persistent state, and delivers instant Gmail notifications. Runs directly on GitHub Actions without any third-party scraper proxy subscriptions.

## Repository Layout

```text
├── .github/
│   └── workflows/
│       └── bms-monitor.yml   # 15-minute GitHub Actions cron workflow
├── .env.example              # Local template for Gmail credentials
├── config.py                 # Profile, movie targets, dates & theatre screens
├── fetcher.py                # Direct resilient BMS API client
├── main.py                   # Parsing, matching, state diffing & email alerting
├── requirements.txt          # Python dependencies
├── state_store.py            # Append-only persistent session archive
└── README.md
```

## Setup & Local Testing

1. Create and activate a Python virtual environment:
   ```bash
   python -m venv .venv
   # Windows:
   .\.venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your Gmail app password:
   ```bash
   cp .env.example .env
   ```
4. Verify `config.py`:
   - Set `ACTIVE_PROFILE = "SANITY_TEST"` to test against current listings, or switch to `"REAL_DATA"` for upcoming releases (e.g. *Avengers: Doomsday*).
5. Run locally:
   ```bash
   python main.py
   ```

## GitHub Actions Deployment

1. Copy [bms-monitor.yml](file:///c:/Users/david/AppData/Local/Temp/bms-monitor.yml) to your repository under `.github/workflows/bms-monitor.yml`.
2. Add your repository secrets in **Settings → Secrets and variables → Actions**:
   - `BMS_EMAIL_SENDER`: Your Gmail address.
   - `BMS_EMAIL_RECIPIENTS`: Destination email addresses (separated by commas).
   - `BMS_GMAIL_APP_PASSWORD`: 16-character Google App Password (generated from Google Account → Security → 2-Step Verification → App Passwords).
3. Enable Git Write Permissions for the runner:
   - Go to **Settings → Actions → General → Workflow permissions**.
   - Check **Read and write permissions** and click **Save** (this allows the bot to record and commit state to the `monitor-state` branch).
4. Go to **Actions → BookMyShow Monitor → Run workflow** to execute manually or let the 15-minute schedule run automatically.
