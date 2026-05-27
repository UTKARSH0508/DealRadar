# Daily Deal Radar

Daily Deal Radar is a GitHub Actions job that searches the web for Indian funding announcements, uses Google Gemini (free tier) to extract deal facts, filters for reported post-money valuations of INR 300-1000 cr, dedupes previously emailed deals, and sends a concise email report.

## Flow

1. GitHub Actions runs daily at 9:00 AM Asia/Kolkata.
2. `web_discovery.py` queries GDELT for recent funding-news articles from the past 30 days.
3. The script fetches article text from each source URL.
4. Gemini extracts structured deal facts from each article.
5. `agent.py` keeps only private Indian companies with explicitly reported post-money valuation of INR 300-1000 cr.
6. Deals already present in `output/seen_deals.json` are suppressed.
7. The final email shows only company name, brief overview, investors in the round, deal size, post-money valuation, and source.

If no qualifying unseen deals are found, the report says:

```text
No new deals found.
```

## Run Locally

```bash
cd /Users/utkarsh/Documents/Codex/2026-05-25/help-me-make-an-agent-which/deal_sourcing_agent
export GEMINI_API_KEY=your-gemini-api-key
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

Get a free API key from [Google AI Studio](https://aistudio.google.com/apikey) (no credit card required for the free tier). The default model is `gemini-2.0-flash`, which is suitable for demo runs.

## GitHub Actions Setup

Add these repository secrets:

```text
GEMINI_API_KEY
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
- `minimum_post_money_valuation_inr_cr`: currently 300
- `maximum_post_money_valuation_inr_cr`: currently 1000
- `search_queries`: GDELT search queries
- `gemini_model`: Gemini model used for extraction (default: `gemini-2.0-flash`, free tier)
- `max_articles`: maximum articles inspected per run

## Important Limitation

This is an internet-discovery workflow, not a paid database. It only includes deals where the fetched article explicitly reports post-money valuation. Coverage will be lower than Tracxn, Venture Intelligence, PrivateCircle, or PitchBook, but it is a good free/low-cost starting point.
