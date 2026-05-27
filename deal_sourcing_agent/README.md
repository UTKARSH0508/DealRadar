# Daily Deal Radar

Daily Deal Radar is a GitHub Actions job that searches the web for Indian funding announcements, uses NVIDIA NIM (free API catalog) to extract deal facts, filters for reported post-money valuations, dedupes previously emailed deals, and sends a concise email report.

## Flow

1. GitHub Actions runs daily at 9:00 AM Asia/Kolkata.
2. `web_discovery.py` queries GDELT for recent funding-news articles from the past 30 days.
3. Python fetches article text from each source URL (no LLM).
4. NVIDIA NIM extracts structured deal facts from each article (LLM).
5. `agent.py` keeps only private Indian companies with explicitly reported post-money valuation.
6. Deals already present in `output/seen_deals.json` are suppressed.
7. The final email shows company name, overview, investors, deal size, post-money valuation, and source.

If no qualifying unseen deals are found, the report says:

```text
No new deals found.
```

## Run Locally

```bash
cd deal_sourcing_agent
export NVIDIA_API_KEY=your-nvidia-api-key
python3 agent.py
```

To send email too:

```bash
export DEAL_AGENT_EMAIL_TO=you@example.com
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USERNAME=you@example.com
export SMTP_PASSWORD=your-app-password-or-smtp-token
export SMTP_FROM=you@example.com
python3 agent.py --email
```

The report is written to:

```text
output/deal_sourcing_report.md
```

## LLM setup (free)

1. Sign up at [build.nvidia.com](https://build.nvidia.com).
2. Open any model page → **Get API Key** (starts with `nvapi-`).
3. Default model: `meta/llama-3.1-8b-instruct` (demo-friendly, free catalog tier).

Scraping (GDELT + HTML fetch) is pure Python. The LLM only reads downloaded article text and returns JSON.

## GitHub Actions Setup

Add these repository secrets:

```text
NVIDIA_API_KEY
SMTP_PASSWORD
```

The workflow is preconfigured to:

- send email to `mehtautkarsh5@gmail.com`
- use Gmail SMTP from `mehtautkarsh5@gmail.com`
- run at 9:00 AM Asia/Kolkata
- commit `output/seen_deals.json` back to the repo after successful runs so old deals are not emailed again

## Configuration

Edit `config.json` for:

- `recent_round_days`: lookback window, currently 30 days
- `minimum_post_money_valuation_inr_cr` / `maximum_post_money_valuation_inr_cr`
- `search_queries`: GDELT search queries
- `nvidia_model`: NVIDIA catalog model id (default: `meta/llama-3.1-8b-instruct`)
- `max_articles`: articles per run (default: `1` for demo)
- `article_text_chars`: max chars sent to the LLM per article (default: `4000`)

## Important Limitation

This is an internet-discovery workflow, not a paid database. It only includes deals where the fetched article explicitly reports post-money valuation. Coverage will be lower than Tracxn, Venture Intelligence, PrivateCircle, or PitchBook, but it is a good free/low-cost starting point.
