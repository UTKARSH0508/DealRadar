#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from smtplib import SMTPAuthenticationError
from pathlib import Path
from typing import Any


@dataclass
class Candidate:
    company: dict[str, Any]
    valuation_usd: float
    valuation_basis: str
    score: float
    reasons: list[str]
    risks: list[str]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def infer_valuation_usd(company: dict[str, Any], config: dict[str, Any]) -> tuple[float | None, str]:
    round_info = company.get("latest_round", {})
    explicit = round_info.get("post_money_valuation_usd")
    if explicit:
        return float(explicit), "reported post-money valuation"

    amount = round_info.get("amount_usd")
    if not amount:
        return None, "missing round amount and valuation"

    # If valuation is undisclosed, estimate a range from typical dilution.
    # Use the conservative lower bound for thresholding.
    max_dilution = float(config["assumed_max_dilution"])
    min_dilution = float(config["assumed_min_dilution"])
    low = float(amount) / max_dilution
    high = float(amount) / min_dilution
    return low, f"inferred conservative post-money valuation range: ${low:,.0f}-${high:,.0f}"


def is_recent_round(company: dict[str, Any], as_of: date, days: int) -> bool:
    round_date = parse_date(company["latest_round"]["date"])
    return round_date >= as_of - timedelta(days=days)


def qualifies(company: dict[str, Any], config: dict[str, Any], as_of: date) -> tuple[bool, list[str], list[str], float | None, str]:
    reasons: list[str] = []
    risks: list[str] = []

    if company.get("country") != config["target_country"]:
        return False, reasons, risks, None, "wrong country"

    ownership = str(company.get("ownership_status", "")).lower()
    if ownership in config["excluded_ownership"]:
        return False, reasons, risks, None, "excluded ownership status"

    if not is_recent_round(company, as_of, int(config["recent_round_days"])):
        return False, reasons, risks, None, "round is not recent"
    reasons.append("latest round is recent")

    valuation, basis = infer_valuation_usd(company, config)
    if valuation is None:
        risks.append("valuation could not be inferred")
        return False, reasons, risks, None, basis

    if valuation < float(config["minimum_valuation_usd"]):
        return False, reasons, risks, valuation, basis
    reasons.append(f"valuation clears ${config['minimum_valuation_usd']:,.0f} threshold via {basis}")

    round_type = company.get("latest_round", {}).get("type")
    if round_type in config["preferred_rounds"]:
        reasons.append(f"round type fits growth mandate: {round_type}")
    else:
        risks.append(f"round type is less aligned with growth mandate: {round_type}")

    return True, reasons, risks, valuation, basis


def score_company(company: dict[str, Any], config: dict[str, Any], valuation_usd: float, reasons: list[str], risks: list[str]) -> float:
    score = 50.0
    sector = company.get("sector", "")
    score += float(config["sector_weights"].get(sector, 0))

    signals = company.get("signals", {})
    score += min(float(signals.get("revenue_growth_yoy_pct", 0)) / 10, 15)
    score += min(float(signals.get("employee_growth_6m_pct", 0)) / 5, 8)
    score += min(valuation_usd / 100_000_000, 10)

    if signals.get("notable_customers"):
        score += 5
    if "reported post-money" not in " ".join(reasons):
        score -= 6
        risks.append("valuation is inferred, not directly reported")

    score -= len(risks) * 3
    return round(max(0, min(score, 100)), 1)


def build_candidates(companies: list[dict[str, Any]], config: dict[str, Any], as_of: date) -> list[Candidate]:
    candidates: list[Candidate] = []
    for company in companies:
        ok, reasons, risks, valuation, basis = qualifies(company, config, as_of)
        if not ok or valuation is None:
            continue
        score = score_company(company, config, valuation, reasons, risks)
        candidates.append(Candidate(company, valuation, basis, score, reasons, risks))

    return sorted(candidates, key=lambda c: c.score, reverse=True)


def candidate_to_markdown(candidate: Candidate) -> str:
    company = candidate.company
    round_info = company["latest_round"]
    investors = ", ".join(round_info.get("investors", [])) or "undisclosed"
    reasons = "\n".join(f"- {reason}" for reason in candidate.reasons)
    risks = "\n".join(f"- {risk}" for risk in candidate.risks) or "- No major data-quality risk flagged"
    sources = ", ".join(company.get("sources", [])) or "not provided"

    return f"""## {company['name']} - score {candidate.score}/100

**Sector:** {company.get('sector')}  
**Location:** {company.get('city')}, {company.get('country')}  
**Website:** {company.get('website')}  
**Latest round:** {round_info.get('type')} on {round_info.get('date')} for ${round_info.get('amount_usd', 0):,.0f}  
**Investors:** {investors}  
**Valuation:** ${candidate.valuation_usd:,.0f} ({candidate.valuation_basis})  

**Why it is relevant**
{reasons}

**Risks / diligence questions**
{risks}

**Initial sourcing angle:** {company.get('description')}

**Sources:** {sources}
"""


def markdown_to_plain_email(markdown: str) -> str:
    replacements = {
        "# ": "",
        "## ": "",
        "**": "",
        "  \n": "\n",
    }
    text = markdown
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def send_email(subject: str, markdown_body: str, recipient: str, attachment_path: Path | None = None) -> None:
    required_env = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"]
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing email environment variables: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = recipient
    message.set_content(markdown_to_plain_email(markdown_body))

    if attachment_path:
        message.add_attachment(
            attachment_path.read_bytes(),
            maintype="text",
            subtype="markdown",
            filename=attachment_path.name,
        )

    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    password = os.environ["SMTP_PASSWORD"].replace(" ", "")
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        try:
            smtp.login(os.environ["SMTP_USERNAME"], password)
        except SMTPAuthenticationError as exc:
            raise RuntimeError(
                "Gmail rejected SMTP login. Set the GitHub secret SMTP_PASSWORD to a Gmail App Password "
                "for SMTP_USERNAME, not the normal Gmail password."
            ) from exc
        smtp.send_message(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Find Indian private companies with recent funding and $50M+ valuation.")
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--input", default="data/sample_companies.json", type=Path)
    parser.add_argument("--output", default="output/deal_sourcing_report.md", type=Path)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--email-to", default=os.environ.get("DEAL_AGENT_EMAIL_TO"))
    parser.add_argument("--email", action="store_true", help="Send the report by email using SMTP_* environment variables.")
    args = parser.parse_args()

    config = load_json(args.config)
    companies = load_json(args.input)
    as_of = parse_date(args.as_of)
    candidates = build_candidates(companies, config, as_of)

    body = [
        "# India Growth Deal Sourcing Report",
        "",
        f"As of: {as_of.isoformat()}",
        f"Screen: private Indian companies, latest funding within {config['recent_round_days']} days, valuation >= ${config['minimum_valuation_usd']:,.0f}",
        "",
    ]
    if not candidates:
        body.append("No qualifying companies found.")
    else:
        body.extend(candidate_to_markdown(candidate) for candidate in candidates)

    report = "\n".join(body)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output} with {len(candidates)} candidates")

    if args.email:
        if not args.email_to:
            raise RuntimeError("Email delivery requested, but no recipient was provided. Set DEAL_AGENT_EMAIL_TO or pass --email-to.")
        subject = f"India growth deal sourcing: {len(candidates)} candidates - {as_of.isoformat()}"
        send_email(subject, report, args.email_to, args.output)
        print(f"Sent email report to {args.email_to}")


if __name__ == "__main__":
    main()
