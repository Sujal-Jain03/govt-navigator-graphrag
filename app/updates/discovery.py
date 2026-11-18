"""Tavily discovery: find new official notifications.

Source-of-truth rule: Tavily snippets are never used as content —
they only point at PDFs, and only PDFs on a source's official
domains are trusted. Free tier: 1,000 credits/month, no card;
basic search costs 1 credit per request (8 sources x 2 queries
= 16/day, ~480/month).

Precision filter (added after the first live run, 2026-11-18):
official sites also host tenders, roll lists, answer keys and
other exams' adverts, and Tavily returns them. Two rules, checked
in order: (1) exclusion keywords — a candidate matching one is
rejected whatever else it says; (2) exam keywords — the title/URL
must name the source's exam. Both are visible in the report as
skip reasons, never silent.
"""
import re
from urllib.parse import urlparse

import requests

TAVILY_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
TIME_RANGE = "month"
SEARCH_DEPTH = "basic"  # 1 credit/request; advanced costs 2

# Substring match on the lowercased title + URL. Checked first:
# "results of the Civil Services" is still a result, not a notice.
EXCLUDED_KEYWORDS = [
    "tender", "roll list", "rolllist", "result", "admit card",
    "answer key", "cut-off", "cutoff", "merit list",
    "interview schedule", "hall ticket", "syllabus", "auction",
    "circular",
]


class TavilyError(Exception):
    pass


def tavily_search(api_key, query, domains, post=None):
    """One Tavily search. Returns the raw results list. post is
    injectable for testing."""
    post = post or requests.post
    resp = post(
        TAVILY_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"query": query, "search_depth": SEARCH_DEPTH,
              "max_results": MAX_RESULTS, "time_range": TIME_RANGE,
              "include_domains": domains},
        timeout=30,
    )
    if resp.status_code != 200:
        raise TavilyError(f"Tavily {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("results", [])


def is_pdf_url(url):
    """True if the URL path ends in .pdf (query strings ignored)."""
    return urlparse(url).path.lower().endswith(".pdf")


def is_trusted(url, domains):
    """True if the URL host is one of the official domains (or a
    subdomain of one). A lookalike like ibps.in.evil.com fails."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def classify(title, url):
    """corrigendum if the title/URL says so, else notification."""
    hay = f"{title} {url}".lower()
    if "corrigendum" in hay or "addendum" in hay:
        return "corrigendum"
    return "notification"


def filter_candidate(candidate, source):
    """Precision filter. Returns a skip reason, or None if the
    candidate passes. Exclusions win over keyword matches."""
    text = f"{candidate.get('title', '')} {candidate.get('url', '')}".lower()
    for kw in EXCLUDED_KEYWORDS:
        if kw in text:
            return f"excluded keyword: {kw}"
    patterns = source.get("keywords", [])
    if patterns and not any(re.search(p, text, re.IGNORECASE) for p in patterns):
        return "no exam keyword in title/URL"
    return None


def discover(source, api_key, search_fn=None):
    """Run a source's queries; return deduped candidate dicts."""
    search = search_fn or tavily_search
    candidates = []
    seen_urls = set()
    for query_kind, query in source["queries"].items():
        for r in search(api_key, query, source["domains"]):
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = r.get("title", "")
            cand = {
                "url": url, "title": title, "score": r.get("score"),
                "source": source["key"], "query_kind": query_kind,
                "kind": classify(title, url),
                "trusted": is_trusted(url, source["domains"]),
                "is_pdf": is_pdf_url(url),
            }
            cand["filter_reason"] = filter_candidate(cand, source)
            candidates.append(cand)
    return candidates
