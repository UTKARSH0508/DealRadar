from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


@dataclass
class TracxnSettings:
    access_token: str
    base_url: str
    transactions_path: str
    max_results: int
    timeout_seconds: int


def _first_present(record: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        value = record
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value not in (None, "", []):
            return value
    return default


def _as_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = _first_present(value, ["amount", "value", "usd", "USD", "amountUSD", "amount_usd"])
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, dict):
        value = _first_present(value, ["date", "announcedDate", "publishedDate", "value"])
    if isinstance(value, str):
        return value[:10]
    return None


def _as_names(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [values]
    if isinstance(values, dict):
        values = values.get("items") or values.get("data") or values.get("investors") or [values]
    names: list[str] = []
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, dict):
                name = _first_present(value, ["name", "investorName", "organization.name", "profile.name"])
                if name:
                    names.append(str(name))
    return names


def _extract_records(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if not isinstance(response, dict):
        return []

    for key in ["result", "results", "data", "items", "docs", "transactions", "companies"]:
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_records(value)
            if nested:
                return nested
    return []


def _default_payload(as_of: date, recent_round_days: int, max_results: int) -> dict[str, Any]:
    from_date = as_of - timedelta(days=recent_round_days)
    return {
        "filter": {
            "country": ["India"],
            "fundingDate": {
                "min": from_date.isoformat(),
                "max": as_of.isoformat(),
            },
            "companyStatus": ["Private"],
        },
        "sort": [
            {
                "field": "fundingDate",
                "order": "desc",
            }
        ],
        "from": 0,
        "size": max_results,
    }


def _load_payload(as_of: date, recent_round_days: int, max_results: int) -> dict[str, Any]:
    payload_path = os.environ.get("TRACXN_PAYLOAD_FILE")
    if payload_path:
        return json.loads(Path(payload_path).read_text(encoding="utf-8"))
    return _default_payload(as_of, recent_round_days, max_results)


def _settings() -> TracxnSettings:
    token = os.environ.get("TRACXN_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("Missing TRACXN_ACCESS_TOKEN. Add it as a GitHub Actions secret before using --source tracxn.")

    return TracxnSettings(
        access_token=token,
        base_url=os.environ.get("TRACXN_API_BASE_URL", "https://platform.tracxn.com/api/2.2").rstrip("/"),
        transactions_path=os.environ.get("TRACXN_TRANSACTIONS_PATH", "/transactions"),
        max_results=int(os.environ.get("TRACXN_MAX_RESULTS", "20")),
        timeout_seconds=int(os.environ.get("TRACXN_TIMEOUT_SECONDS", "30")),
    )


def _post_json(url: str, payload: dict[str, Any], settings: TracxnSettings) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Tracxn API returned HTTP {exc.code}: {detail}") from exc


def normalize_tracxn_record(record: dict[str, Any]) -> dict[str, Any]:
    company = _first_present(record, ["company", "organization", "companyInfo"], {})
    if not isinstance(company, dict):
        company = {}

    name = _first_present(record, ["companyName", "name", "company.name", "organization.name"], "Unknown company")
    round_date = _as_date(_first_present(record, ["fundingDate", "date", "announcedDate", "roundDate", "latestRound.date"]))
    amount = _as_number(_first_present(record, ["amountUSD", "amount_usd", "fundingAmountUSD", "fundingAmount", "amount", "roundAmount"]))
    valuation = _as_number(
        _first_present(
            record,
            [
                "postMoneyValuationUSD",
                "post_money_valuation_usd",
                "valuationUSD",
                "valuation",
                "company.postMoneyValuationUSD",
                "latestRound.postMoneyValuationUSD",
            ],
        )
    )

    return {
        "name": name,
        "website": _first_present(record, ["website", "domain", "company.website", "company.domain"], ""),
        "country": _first_present(record, ["country", "company.country", "location.country"], "India"),
        "city": _first_present(record, ["city", "company.city", "location.city"], ""),
        "ownership_status": str(_first_present(record, ["ownershipStatus", "companyStatus", "status"], "private")).lower(),
        "sector": _first_present(record, ["sector", "industry", "practiceArea", "company.sector"], "Unknown"),
        "description": _first_present(record, ["description", "shortDescription", "company.description"], ""),
        "latest_round": {
            "date": round_date or date.today().isoformat(),
            "type": _first_present(record, ["roundType", "stage", "fundingRound", "latestRound.type"], "Unknown"),
            "amount_usd": amount,
            "post_money_valuation_usd": valuation,
            "investors": _as_names(_first_present(record, ["investors", "leadInvestors", "participatingInvestors"])),
        },
        "signals": {
            "revenue_growth_yoy_pct": _as_number(_first_present(record, ["revenueGrowthYoY", "metrics.revenueGrowthYoY"])) or 0,
            "employee_growth_6m_pct": _as_number(_first_present(record, ["employeeGrowth6M", "metrics.employeeGrowth6M"])) or 0,
            "notable_customers": [],
            "founder_background": _first_present(record, ["founders", "founderBackground"], ""),
        },
        "sources": [
            f"Tracxn record {_first_present(record, ['id', 'transactionId', 'roundId', 'companyId'], 'unknown-id')}"
        ],
    }


def fetch_tracxn_companies(config: dict[str, Any], as_of: date, raw_output: Path | None = None) -> list[dict[str, Any]]:
    settings = _settings()
    payload = _load_payload(as_of, int(config["recent_round_days"]), settings.max_results)
    url = f"{settings.base_url}{settings.transactions_path}"
    response = _post_json(url, payload, settings)

    if raw_output:
        raw_output.parent.mkdir(parents=True, exist_ok=True)
        raw_output.write_text(json.dumps(response, indent=2, sort_keys=True), encoding="utf-8")

    return [normalize_tracxn_record(record) for record in _extract_records(response)]
