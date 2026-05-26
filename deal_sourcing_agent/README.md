# Indian Growth Deal Sourcing Agent

This is a starter agent for a late-stage/growth fund looking for Indian private-sector companies that recently raised capital and are valued at $50M+.

## What It Does

The agent screens companies for:

- India-headquartered and private ownership
- latest funding round inside a configurable recency window
- reported or conservatively inferred post-money valuation of at least $50M
- growth-fund fit based on round stage, sector, growth signals, and source quality

The output is an investment-sourcing report with ranked candidates, reasons, risks, and diligence questions.

## Recommended Data Sources

Use at least one structured paid data source plus public corroboration:

- Tracxn: funding rounds, VC/PE-backed deals, funded companies, and valuation filters.
- Venture Intelligence: India-focused PE/VC database with funding, investor, and valuation data.
- PrivateCircle: India private-market data, company filings, ownership, and API access.
- Crunchbase or PitchBook: global funding rounds and investor graph.
- MCA filings, company websites, press releases, LinkedIn hiring, Tracxn/VI/PrivateCircle notes, and news articles for verification.

The prototype uses `data/sample_companies.json` so you can run it offline. Replace that file with normalized records from your chosen source.

## Agent Architecture

1. **Ingestion agent**
   Pulls new funding rounds daily from data vendors, news/RSS, and press releases.

2. **Entity resolution agent**
   Deduplicates company names, domains, CINs, founders, and investor names.

3. **Eligibility agent**
   Applies hard filters: India, private sector, recent round, $50M+ valuation, not public/government-owned.

4. **Valuation agent**
   Uses reported post-money valuation when available. If valuation is undisclosed, it estimates a conservative lower bound from round size and assumed dilution.

5. **Research agent**
   Gathers corroborating evidence: company description, traction, financial filings, hiring, customers, investors, founder background, and cap table clues.

6. **Scoring agent**
   Ranks companies by mandate fit, growth signal strength, round quality, valuation confidence, investor quality, and sourcing urgency.

7. **Memo agent**
   Produces a concise sourcing note with why-now, key risks, warm-intro paths, and next diligence questions.

## Run the Prototype

```bash
cd /Users/utkarsh/Documents/Codex/2026-05-25/help-me-make-an-agent-which/deal_sourcing_agent
python3 agent.py --as-of 2026-05-25
```

The report is written to:

```text
/Users/utkarsh/Documents/Codex/2026-05-25/help-me-make-an-agent-which/deal_sourcing_agent/output/deal_sourcing_report.md
```

## Daily Email Setup

The agent can send the report by SMTP. Copy `.env.example` into your scheduler or secret manager and provide real values:

```bash
export DEAL_AGENT_EMAIL_TO=you@example.com
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USERNAME=you@example.com
export SMTP_PASSWORD=your-app-password-or-smtp-token
export SMTP_FROM=you@example.com
```

Then run:

```bash
python3 agent.py --email
```

For a 9 AM daily job, schedule `run_daily.sh` with your scheduler of choice. In production, use a cloud scheduler, GitHub Actions, Airflow, Prefect, Dagster, or a cron job on a small VM.

## Tracxn API Connector

Daily Deal Radar can use Tracxn as the source of truth instead of the sample JSON file:

```bash
python3 agent.py --source tracxn --email
```

Required environment variable:

```text
TRACXN_ACCESS_TOKEN
```

Optional Tracxn settings:

```text
TRACXN_API_BASE_URL=https://platform.tracxn.com/api/2.2
TRACXN_TRANSACTIONS_PATH=/transactions
TRACXN_MAX_RESULTS=20
TRACXN_PAYLOAD_FILE=/path/to/custom_payload.json
```

The default connector posts a conservative India/private/recent-funding payload to the configured transactions endpoint, writes the raw API response to `output/tracxn_raw_response.json`, normalizes records into the DealRadar schema, and then runs the same valuation and scoring filters.

Because Tracxn field names and endpoint permissions can vary by account, use `TRACXN_PAYLOAD_FILE` or `TRACXN_TRANSACTIONS_PATH` if Tracxn gives you a specific endpoint contract.

## GitHub Actions Setup

This repository includes `.github/workflows/daily-deal-radar.yml`, which runs every day at 9:00 AM Asia/Kolkata and can also be triggered manually from the GitHub Actions tab.

Add these repository secrets in GitHub:

```text
TRACXN_ACCESS_TOKEN
SMTP_PASSWORD
```

In GitHub, go to **Settings -> Secrets and variables -> Actions -> New repository secret**.

The workflow is preconfigured to use Gmail SMTP from `mehtautkarsh5@gmail.com`; only the Gmail app password should be stored as a secret.

The workflow also uploads the generated Markdown report as a run artifact, so you can inspect the report even if email delivery fails.

Daily emails are currently addressed to `mehtautkarsh5@gmail.com`.

## Production Notes

- Prefer vendor APIs for the first version; scraping should only supplement missing public evidence.
- Store all raw source documents and timestamps so investment teams can audit each recommendation.
- Treat inferred valuations as lower-confidence candidates and route them to manual verification.
- Use a CRM sink such as Affinity, Attio, Salesforce, or Airtable once a company crosses the score threshold.
- Add weekly digests for the investment team and same-day alerts for large rounds, high-priority sectors, or known investor syndicates.

## Suggested Candidate Schema

```json
{
  "name": "Company",
  "website": "https://example.com",
  "country": "India",
  "city": "Bengaluru",
  "ownership_status": "private",
  "sector": "Fintech",
  "description": "What the company does",
  "latest_round": {
    "date": "2026-05-12",
    "type": "Series C",
    "amount_usd": 22000000,
    "post_money_valuation_usd": 145000000,
    "investors": ["Investor A", "Investor B"]
  },
  "signals": {
    "revenue_growth_yoy_pct": 95,
    "employee_growth_6m_pct": 22,
    "notable_customers": ["Customer segment"],
    "founder_background": "Founder context"
  },
  "sources": ["press release", "data vendor"]
}
```
