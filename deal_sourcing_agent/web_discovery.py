from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any


@dataclass
class Article:
    title: str
    url: str
    published_at: str
    domain: str
    text: str


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            cleaned = " ".join(data.split())
            if cleaned:
                self.parts.append(cleaned)

    def text(self) -> str:
        return " ".join(self.parts)


def _get_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "DailyDealRadar/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "DailyDealRadar/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        raw = response.read(1_500_000).decode("utf-8", errors="replace")
    if "html" not in content_type and "<html" not in raw.lower():
        return " ".join(raw.split())
    parser = TextExtractor()
    parser.feed(raw)
    return parser.text()


def discover_articles(config: dict[str, Any]) -> list[Article]:
    max_articles = int(config.get("max_articles", 20))
    lookback_days = int(config["recent_round_days"])
    seen_urls: set[str] = set()
    articles: list[Article] = []

    print(f"[DEBUG] Starting article discovery: max_articles={max_articles}, lookback_days={lookback_days}")

    for query in config.get("search_queries", []):
        print(f"[DEBUG] Searching for articles with query: {query}")
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": max_articles,
            "sort": "DateDesc",
            "timespan": f"{lookback_days}d",
        }
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
        try:
            payload = _get_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"[DEBUG] Error fetching articles for query '{query}': {e}")
            continue

        for item in payload.get("articles", []):
            article_url = item.get("url")
            if not article_url or article_url in seen_urls:
                continue
            seen_urls.add(article_url)
            try:
                text = _get_text(article_url)
            except (urllib.error.URLError, TimeoutError, UnicodeError) as e:
                print(f"[DEBUG] Error fetching text from {article_url}: {e}")
                continue
            if len(text) < 500:
                print(f"[DEBUG] Article too short ({len(text)} chars): {article_url}")
                continue
            articles.append(
                Article(
                    title=item.get("title", ""),
                    url=article_url,
                    published_at=str(item.get("seendate", ""))[:8],
                    domain=item.get("domain", ""),
                    text=text[: int(config.get("article_text_chars", 12000))],
                )
            )
            print(f"[DEBUG] Added article: {item.get('title', 'Unknown')}")
            if len(articles) >= max_articles:
                print(f"[DEBUG] Reached max_articles limit ({max_articles})")
                return articles

    print(f"[DEBUG] Article discovery complete: found {len(articles)} articles")
    return articles


def _openai_chat(system_prompt: str, user_prompt: str, config: dict[str, Any]) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Add it as a GitHub Actions secret.")

    model_name = os.environ.get(
        "OPENAI_MODEL",
        config.get("openai_model", "gpt-4o-mini"),
    )
    body = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    print(f"[DEBUG] Using OpenAI model: {model_name}")

    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        timeout = int(config.get("openai_timeout_seconds", 60))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API returned HTTP {exc.code}: {detail}") from exc
    return payload["choices"][0]["message"]["content"]


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {"deals": []}
        return json.loads(match.group(0))


def extract_deals_from_article(article: Article, config: dict[str, Any]) -> list[dict[str, Any]]:
    print(f"[DEBUG] Extracting deals from article: {article.title[:50]}...")
    
    system_prompt = """You extract Indian private-market funding deals for a growth fund.
Use only the article text and metadata provided by the user.
Do not infer missing values.
Only include deals where the article explicitly reports a post-money valuation.
Return strict JSON with this schema:
{"deals":[{"company_name":"","overview":"","country":"","round_date":"YYYY-MM-DD or empty","round_type":"","deal_size_inr_cr":null,"post_money_valuation_inr_cr":null,"investors":[],"source_url":""}]}
If no qualifying deal is explicit in the article, return {"deals":[]}."""
    user_prompt = f"""Article title: {article.title}
Article URL: {article.url}
Seen date: {article.published_at}
Domain: {article.domain}

Article text:
{article.text}
"""
    
    # Add delay before calling OpenAI API to respect rate limits
    print(f"[DEBUG] Waiting 2 seconds before OpenAI API call...")
    time.sleep(2)

    print(f"[DEBUG] Calling OpenAI API...")
    content = _openai_chat(system_prompt, user_prompt, config)
    parsed = _json_from_text(content)
    deals = parsed.get("deals", [])
    print(f"[DEBUG] Extracted {len(deals)} deals from article")
    return [deal for deal in deals if isinstance(deal, dict)]


def fetch_web_companies(config: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    print(f"[DEBUG] Starting fetch_web_companies")
    companies: list[dict[str, Any]] = []
    articles = discover_articles(config)
    print(f"[DEBUG] Processing {len(articles)} articles")
    
    for i, article in enumerate(articles, 1):
        print(f"[DEBUG] Processing article {i}/{len(articles)}")
        for deal in extract_deals_from_article(article, config):
            companies.append(
                {
                    "name": deal.get("company_name") or "Unknown company",
                    "website": "",
                    "country": deal.get("country") or "India",
                    "city": "",
                    "ownership_status": "private",
                    "sector": "Unknown",
                    "description": deal.get("overview") or "",
                    "latest_round": {
                        "date": deal.get("round_date") or as_of.isoformat(),
                        "type": deal.get("round_type") or "Unknown",
                        "amount_inr": float(deal["deal_size_inr_cr"]) * 10_000_000 if deal.get("deal_size_inr_cr") is not None else None,
                        "post_money_valuation_inr": float(deal["post_money_valuation_inr_cr"]) * 10_000_000
                        if deal.get("post_money_valuation_inr_cr") is not None
                        else None,
                        "investors": deal.get("investors") if isinstance(deal.get("investors"), list) else [],
                    },
                    "signals": {},
                    "sources": [deal.get("source_url") or article.url],
                    "source_title": deal.get("source_title") or article.title,
                }
            )
    
    print(f"[DEBUG] fetch_web_companies complete: found {len(companies)} companies")
    return companies
