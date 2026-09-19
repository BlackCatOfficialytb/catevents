# 🐱 CatEvents // Aggregate Trend Engine

Multi-network real-time trend aggregator. Scrapes Google Trends (global RSS), Reddit `r/popular`, and X/Twitter (Nitter mirrors + Camoufox stealth browser), ranks them with a custom quicksort, derives macro-trend keywords (classic semantic search or LLM), caches to disk + Hugging Face Datasets, and serves a dashboard + static frontend.

> Made with 💖 by [BlackCat](https://blackcatofficial.qzz.io) in Vietnam
> Frontend built by AI, backend built mostly without AI.

- **Backend:** Flask (`backend/`) — scraper, scheduler, admin dashboard, HF upload
- **Frontend:** static Tailwind (`frontend/`) — reads `trends.json` from Hugging Face
- **License:** Apache 2.0 (`LICENSE`)

---

## Features

- **Google Trends global feed** — aggregates `https://trends.google.com/trending/rss?geo={geo}` across ~40 geos, dedupes, ranks, keeps top-N.
- **Reddit `r/popular` RSS** — `top/.rss?sort=top&t=day&limit=10`.
- **TOML RSS feed adapters (`toml_config_reader.py`)** — zero-code XML adapters for news feeds (BBC, NYT, Guardian, CNN, DW, Al Jazeera, NPR) via `rss_feeds.toml` and per-feed `feeds/*.toml` configs parsed with stdlib `tomllib`.
- **X / Twitter via Nitter mirrors** — tries lightweight RSS mirrors first (`/search/rss`), falls back to Camoufox stealth Firefox for JS-rendered HTML (`/search?f=tweets`). Skips `xcancel.com` in RSS loop (no RSS endpoint), detects `whitelist` honeypots / `error-panel` blocks.
- **Custom ranking (`sorting.py`)** — hand-rolled iterative quicksort (Lomuto, middle pivot). Sort key: `(volume desc, abnormality z-score desc, ASCII-folded title asc)`. Parses `200,000+`, `1.5M`, `20K`, etc. Annotates each item with `volume` + `abnormality`.
- **Keyword intelligence (`ai.py` + `keywords.py`)**
  - `ai.enabled=false` → no LLM call; classic semantic extraction if `ai.semantic_search=true`, else static `x.query`.
  - `ai.enabled=true` + `use_ai_to_find_nitter_result=true` → LLM predicts Nitter search keywords from the day's Google+Reddit titles (OpenAI- or Anthropic-compatible API via plain `requests`).
  - Top keyword becomes the Nitter/Camoufox query; fallback is `x.query` (`#trending`).
- **Camoufox stealth scraper (`camoufox_scraper.py`)** — config-driven headless Firefox, humanize, resource blocking, retries, `wait_selector`, full troubleshooting bundles (tracebacks + page diagnostics). Standalone CLI + strict mode.
- **KDL config (`config.kdl` / `config.py`)** — dependency-free KDL parser, file → env → default resolution. `python config.py` prints resolved config (secrets masked).
- **Persistence** — local JSON cache (`trends_debug.json`) + upload to Hugging Face Datasets (`huggingface_hub`).
- **Background scheduler** — daemon thread, boot delay + interval, runs `execute_scrape_and_upload()`.
- **Admin dashboard** — `/admin` (repo status, token status, manual scrape button, visual cards + raw JSON), `/admin/login|logout` session auth, bearer-token auth for API.
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, CSP, `X-XSS-Protection: 0`.
- **Frontend** — dark GitHub-style theme, skeleton shimmer loading, live/offline status badge, refresh button, graceful offline notice linking to status page.

## Project structure

```
catevents/
├── backend/
│   ├── main.py                 # entrypoint: app.run(host, port, debug)
│   ├── scraper.py              # Flask app, scrapers, scheduler, routes
│   ├── config.py               # KDL loader + native Python config vars
│   ├── config.kdl.default      # committed template (copy to config.kdl)
│   ├── config.kdl              # your local copy (git-ignored, secrets go here)
│   ├── .env.example            # env var reference (copy to .env)
│   ├── rss_feeds.toml          # registry mapping feed IDs to TOML adapter configs
│   ├── feeds/                  # per-feed TOML XML adapter definitions (*.toml)
│   ├── toml_config_reader.py   # generic XML feed reader using stdlib tomllib
│   ├── ai.py                   # LLM Nitter-query predictor (openai/anthropic)
│   ├── keywords.py             # classic semantic keyword extraction
│   ├── sorting.py              # quicksort + volume/abnormality ranking
│   ├── camoufox_scraper.py     # stealth browser scraper + CLI
│   ├── standalone_scraper.py   # minimal Google+Reddit demo (no X/AI/scheduler)
│   ├── templates/
│   │   ├── admin.html          # dashboard
│   │   └── login.html          # admin login
│   ├── tests/                  # pytest suite (ai, keywords, sorting, config, scraper, camoufox, toml)
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── pytest.ini
│   └── trends_debug.json       # local cache (written at runtime)
└── frontend/
    ├── index.html              # Tailwind dashboard (Hot Topics + 3 columns)
    ├── script.js               # fetches HF raw trends.json, renders/skeletons/errors
    └── style.css               # dark theme + skeleton shimmer
```

## Quickstart

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# config (pick one or both):
copy config.kdl.default config.kdl   # then edit repo_id, tokens, flags
copy .env.example .env               # then fill HF_TOKEN, ADMIN_TOKEN, etc.

# optional: stealth browser binary (once)
python -m camoufox fetch

# run
python main.py
# → Starting self-hosted server on 0.0.0.0:5000...
```

Open `http://localhost:5000/` → redirects to `/admin` (or `/admin/login` if `ADMIN_TOKEN` is set).

### 2. Frontend

Static — no build. Point `frontend/script.js` at your dataset:

```js
const HF_DATA_URL = "https://huggingface.co/datasets/<USER>/<DATASET>/raw/main/trends.json";
```

Then serve the folder (e.g. VS Code Live Server, `npx serve frontend`, GitHub Pages) or open `index.html` directly.

### 3. Tests

```powershell
cd backend
pip install -r requirements-dev.txt
pytest
# or: pytest --cov=. --cov-report=term-missing
```

`pytest.ini`: `testpaths = tests`, `-q`.

## Configuration

Resolution order for every value: **`config.kdl` (or `$CATEVENTS_CONFIG`) → `config.kdl.default` → env var → `default=` → `ConfigError`**.

Secrets: `HF_TOKEN` env always wins over `huggingface.hf_token`; `AI_API_KEY` env always wins over `ai.api_key`. Never commit real tokens — put them in `config.kdl` (git-ignored) or env.

| Block | Key | Env override | Default | Notes |
|---|---|---|---|---|
| `huggingface` | `repo_id` | `REPO_ID` | `YOUR_USERNAME/YOUR_DATASET` | dataset for `trends.json` |
| | `remote_filename` | `HF_REMOTE_FILENAME` | `trends.json` | |
| | `hf_token` | `HF_TOKEN` | `YOUR_HF_TOKEN` → `None` | required for upload; placeholder = disabled |
| `ai` | `enabled` | `AI_ENABLED` | `#false` | master switch; `false` = no LLM call |
| | `use_ai_to_find_nitter_result` | `AI_USE_FOR_NITTER` | `#true` | LLM predicts Nitter keywords |
| | `semantic_search` | `AI_SEMANTIC_SEARCH` | `#true` | classic fallback when AI off/fails |
| | `api_type` | `AI_API_TYPE` | `openai` | `openai` or `anthropic` only |
| | `base_url` | `AI_BASE_URL` | `https://api.openai.com/v1` | include `/v1` for OpenAI; root for Anthropic |
| | `model` | `AI_MODEL` | `gpt-4o-mini` | |
| | `api_key` | `AI_API_KEY` | `YOUR_AI_API_KEY` → `""` | required when `enabled` |
| | `max_tokens` / `temperature` / `request_timeout` / `keyword_count` | `AI_MAX_TOKENS` etc. | `512` / `0.3` / `30` / `3` | |
| `cache` | `local_file` | `LOCAL_CACHE_FILE` | `trends_debug.json` | |
| `server` | `port` | `PORT` | `5000` | `PORT` wins (Render/Heroku) |
| | `host` | `SERVER_HOST` | `0.0.0.0` | |
| | `debug` | `SERVER_DEBUG` | `#false` | must stay `false` in prod |
| `scheduler` | `enabled` | `SCHEDULER_ENABLED` | `#true` | |
| | `boot_delay_seconds` | `SCHEDULER_BOOT_DELAY` | `5` | |
| | `interval_seconds` | `SCHEDULER_INTERVAL` | `300` | 5 min |
| `feed` | `item_limit` | `FEED_ITEM_LIMIT` | `10` | per-feed cap |
| `google_trends` | `enabled`, `url_template`, `request_timeout`, `result_limit`, `geos` | `GOOGLE_TRENDS_*` | see `config.kdl.default` | ~40 geos, `result_limit 15` |
| `reddit` | `enabled`, `url`, `user_agent`, `default_score`, `request_timeout` | `REDDIT_*` | `▲ Popular`, 10s | |
| `x` | `enabled` | `X_SCRAPING_ENABLED` | `#false` | master switch (currently off by default) |
| | `rss_checker_enabled` | `X_RSS_CHECKER_ENABLED` | `#true` | `false` = skip RSS, go straight to Camoufox |
| | `query`, `mirrors`, `user_agent`, `request_timeout`, `default_score` | `X_*` | `#trending`, 8 mirrors | |
| `camoufox` | `enabled`, `headless`, `humanize`, `default_selector`, `default_limit`, `timeout_ms`, `settle_ms`, `max_retries`, `block_resources`, `proxy`, `default_url`, `default_demo_selector` | `CAMOUFOX_*` | headless, `h1,h2,h3`, 10, 30s, 2s, 2, `image font media` | |
| `sorting` | `enabled`, `order`, `case_insensitive`, `use_abnormality`, `ascii_offset` | `SORTING_*` | `true`, `desc`, `true`, `true`, `32` | |

Extra env-only vars (no KDL): `ADMIN_TOKEN` (protects `/admin`, `/run-scrape`, `/debug/*`; unset = unprotected, dev only), `SESSION_SECRET_KEY` (Flask sessions; unset = random per-process), `CATEVENTS_CONFIG` (explicit config path).

Inspect resolved config:

```powershell
python config.py
```

Camoufox CLI:

```powershell
python camoufox_scraper.py                                        # demo: default_url + demo selector
python camoufox_scraper.py --url https://example.com --selector "article h2" --limit 5
python camoufox_scraper.py --url https://example.com --debug     # troubleshooting to stderr
```

## API reference

| Method | Route | Auth | Description |
|---|---|---|---|
| `GET`/`HEAD` | `/health` | none | liveness probe → `{"status":"ok","service":"catevents-scraper","x_scraping_enabled":…}` |
| `GET` | `/` | redirect | → `/admin`, or `/admin/login` when `ADMIN_TOKEN` set + not authed |
| `GET`/`POST` | `/admin/login` | token form | sets `admin_authed` session on correct `ADMIN_TOKEN` |
| `POST` | `/admin/logout` | session | clears session |
| `GET` | `/admin` | admin | dashboard: repo, token status, manual scrape, latest cache + raw JSON |
| `POST` | `/run-scrape` | admin | runs `execute_scrape_and_upload()` → `{"status":"Success"…}` or generic 500 (details in server logs) |
| `GET` | `/debug/x-troubleshooting` | admin | last failed Camoufox run: mirror count + tracebacks + page diagnostics |

Admin auth: session cookie **or** `Authorization: Bearer <ADMIN_TOKEN>`. If `ADMIN_TOKEN` is unset, endpoints are unprotected (warning logged once) — local dev only.

Scraped payload shape (`trends_debug.json` / HF `trends.json`):

```json
{
  "macro_trends": ["World Cup", "election results", "..."],
  "ai_keywords": ["..."],
  "keyword_source": "ai | semantic | none",
  "google": [{"title": "...", "score": "200,000+", "region": "US", "volume": 200000, "abnormality": 1.23}],
  "reddit": [{"title": "...", "score": "▲ Popular", "volume": 0, "abnormality": 0.0}],
  "x": [{"title": "...", "score": "Trending"}]
}
```

## Deployment notes

- Set `PORT`, `SERVER_HOST`, `SERVER_DEBUG=false`, `ADMIN_TOKEN`, `SESSION_SECRET_KEY`, `HF_TOKEN`, `REPO_ID` in the host env.
- Without `HF_TOKEN`/`REPO_ID`, the app still works — reads `trends_debug.json`, skips upload, dashboard shows “Offline / Debug Mode”.
- `requirements.txt`: `Flask`, `requests`, `huggingface_hub`, `gunicorn`, `waitress`, `camoufox[geoip]`, `playwright==1.60.0` (pinned — 1.61 breaks Camoufox per `daijro/camoufox#653`). Use `waitress` (Windows) or `gunicorn` (Linux) in prod instead of `python main.py`.
- Frontend `script.js` default `HF_DATA_URL` points at `civil384/scraped-trends-database` — change it to your dataset.

## Troubleshooting

- `ADMIN_TOKEN not set — admin endpoints are UNPROTECTED` → set `ADMIN_TOKEN` in prod.
- `Could not load cache from HF` → check `REPO_ID`/`HF_TOKEN`/`HF_REMOTE_FILENAME`; falls back to local cache, then `No scrape executed yet` placeholders.
- X returns `rate-limited`/`Offline` → mirrors are bot-blocked; check `/debug/x-troubleshooting` (admin) for per-mirror tracebacks and `error_panel` messages; try toggling `x.rss_checker_enabled` or `camoufox.enabled`, or update `x.mirrors`.
- `Camoufox import failed` → `pip install camoufox[geoip]` + `python -m camoufox fetch`.
- `ai.api_type must be 'openai' or 'anthropic'` → fix `ai.api_type` / `AI_API_TYPE`.
- `Missing required config '…'` → config file missing/corrupt and no env fallback; restore `config.kdl.default`.
