#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from smtplib import SMTPAuthenticationError
from pathlib import Path
from typing import Any

from web_discovery import fetch_web_companies


@dataclass
class Candidate:
    company: dict[str, Any]
    valuation_inr_cr: float
    valuation_basis: str
    score: float
    reasons: list[str]
    risks: list[str]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def money_to_inr_cr(value: float | int | None, currency: str, config: dict[str, Any]) -> float | None:
    if value is None:
        return None
    amount = float(value)
    if currency.upper() == "INR":
        return amount / 10_000_000
    if currency.upper() == "USD":
        return amount * float(config["usd_to_inr"]) / 10_000_000
    return None


def format_inr_cr(value: float | None) -> str:
    if value is None:
        return "undisclosed"
    return f"INR {value:,.1f} cr"


def round_amount_inr_cr(company: dict[str, Any], config: dict[str, Any]) -> float | None:
    round_info = company.get("latest_round", {})
    amount_inr = money_to_inr_cr(round_info.get("amount_inr"), "INR", config) if round_info.get("amount_inr") else None
    if amount_inr is not None:
        return amount_inr
    if "usd_to_inr" not in config:
        return None
    return money_to_inr_cr(round_info.get("amount_usd"), "USD", config)


def post_money_valuation_inr_cr(company: dict[str, Any], config: dict[str, Any]) -> tuple[float | None, str]:
    round_info = company.get("latest_round", {})
    explicit_inr = round_info.get("post_money_valuation_inr")
    if explicit_inr:
        return money_to_inr_cr(explicit_inr, "INR", config), "reported post-money valuation"

    explicit_usd = round_info.get("post_money_valuation_usd")
    if explicit_usd:
        return money_to_inr_cr(explicit_usd, "USD", config), "reported post-money valuation converted from USD"

    return None, "missing reported post-money valuation"


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

    valuation, basis = post_money_valuation_inr_cr(company, config)
    if valuation is None:
        risks.append("reported post-money valuation is unavailable")
        return False, reasons, risks, None, basis

    reasons.append(f"reported post-money valuation: {format_inr_cr(valuation)}")

    round_type = company.get("latest_round", {}).get("type")
    if round_type in config["preferred_rounds"]:
        reasons.append(f"round type fits growth mandate: {round_type}")
    else:
        risks.append(f"round type is less aligned with growth mandate: {round_type}")

    return True, reasons, risks, valuation, basis


def score_company(company: dict[str, Any], config: dict[str, Any], valuation_inr_cr: float, reasons: list[str], risks: list[str]) -> float:
    return round(max(0, min(100, 50 + min(valuation_inr_cr / 100, 10) - len(risks) * 3)), 1)


def build_candidates(companies: list[dict[str, Any]], config: dict[str, Any], as_of: date) -> list[Candidate]:
    candidates: list[Candidate] = []
    for company in companies:
        ok, reasons, risks, valuation, basis = qualifies(company, config, as_of)
        if not ok or valuation is None:
            continue
        score = score_company(company, config, valuation, reasons, risks)
        candidates.append(Candidate(company, valuation, basis, score, reasons, risks))

    return sorted(candidates, key=lambda c: c.score, reverse=True)


def deal_key(candidate: Candidate, config: dict[str, Any]) -> str:
    company = candidate.company
    round_info = company.get("latest_round", {})
    raw_key = "|".join(
        [
            str(company.get("name", "")).strip().lower(),
            str(round_info.get("date", "")).strip(),
            str(round_info.get("type", "")).strip().lower(),
            str(round_amount_inr_cr(company, config) or ""),
        ]
    )
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def load_seen_deals(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = load_json(path)
    if isinstance(data, list):
        return set(str(item) for item in data)
    return set(str(item) for item in data.get("deal_keys", []))


def save_seen_deals(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"deal_keys": sorted(seen)}, indent=2), encoding="utf-8")


def filter_new_candidates(candidates: list[Candidate], seen: set[str], config: dict[str, Any]) -> list[Candidate]:
    return [candidate for candidate in candidates if deal_key(candidate, config) not in seen]


def candidate_to_markdown(candidate: Candidate) -> str:
    company = candidate.company
    round_info = company["latest_round"]
    investors = ", ".join(round_info.get("investors", [])) or "undisclosed"
    deal_size = format_inr_cr(round_amount_inr_cr(company, {"usd_to_inr": 83.0}))
    sources = ", ".join(company.get("sources", [])) or "not provided"

    return f"""## {company['name']}

**Overview:** {company.get('description') or 'No overview available.'}  
**Investors in round:** {investors}  
**Deal size:** {deal_size}  
**Post-money valuation:** {format_inr_cr(candidate.valuation_inr_cr)}  
**Source:** {sources}  
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
    parser = argparse.ArgumentParser(description="Find Indian private companies with recent funding deals in the past month.")
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--output", default="output/deal_sourcing_report.md", type=Path)
    parser.add_argument("--seen-file", default="output/seen_deals.json", type=Path)
    parser.add_argument("--ignore-seen", action="store_true", help="Include deals even if they were already reported before.")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--email-to", default=os.environ.get("DEAL_AGENT_EMAIL_TO"))
    parser.add_argument("--email", action="store_true", help="Send the report by email using SMTP_* environment variables.")
    args = parser.parse_args()

    config = load_json(args.config)
    as_of = parse_date(args.as_of)
    try:
        companies = fetch_web_companies(config, as_of)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc
    candidates = build_candidates(companies, config, as_of)
    seen = load_seen_deals(args.seen_file)
    new_candidates = candidates if args.ignore_seen else filter_new_candidates(candidates, seen, config)
    
    # Limit to top 5 companies
    top_5_candidates = new_candidates[:5]

    body = [
        "# Daily Deal Radar",
        "",
        f"As of: {as_of.isoformat()}",
        f"Screen: private Indian companies with funding in the past {config['recent_round_days']} days",
        "",
    ]
    if not top_5_candidates:
        body.append("No new deals found.")
    else:
        body.append(f"Top {len(top_5_candidates)} deals:")
        body.append("")
        body.extend(candidate_to_markdown(candidate) for candidate in top_5_candidates)

    report = "\n".join(body)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    if top_5_candidates and not args.ignore_seen:
        seen.update(deal_key(candidate, config) for candidate in top_5_candidates)
    if not args.ignore_seen:
        save_seen_deals(args.seen_file, seen)
    print(f"Wrote {args.output} with {len(top_5_candidates)} new candidates")

    if args.email:
        if not args.email_to:
            raise RuntimeError("Email delivery requested, but no recipient was provided. Set DEAL_AGENT_EMAIL_TO or pass --email-to.")
        subject = f"Daily Deal Radar: {len(top_5_candidates)} new deals - {as_of.isoformat()}"
        send_email(subject, report, args.email_to, args.output)
        print(f"Sent email report to {args.email_to}")


if __name__ == "__main__":
    main()
