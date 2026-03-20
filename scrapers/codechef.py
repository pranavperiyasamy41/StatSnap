from __future__ import annotations

import datetime as dt
import re
import json
from typing import Any

import httpx
from bs4 import BeautifulSoup

from scrapers.common import PlatformFetchError


def _parse_date(text: str) -> dt.date | None:
    s = (text or "").strip()
    if not s:
        return None

    # Normalize common separators.
    s = re.sub(r"\s+", " ", s)

    for fmt in (
        "%d %b %Y",  # 18 Feb 2026
        "%d %B %Y",  # 18 February 2026
        "%d-%m-%Y",  # 18-02-2026
        "%d/%m/%Y",  # 18/02/2026
        "%d.%m.%Y",  # 18.02.2026
        "%Y-%m-%d",  # 2026-02-18
    ):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    # Sometimes contests include date in parentheses like "(18.02.2026)"
    m = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})", s)
    if m:
        for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return dt.datetime.strptime(m.group(1), fmt).date()
            except ValueError:
                pass

    return None


async def fetch_codechef(handle: str) -> list[dict[str, Any]]:
    """
    Scrape CodeChef user page for rating history.
    """
    handle = (handle or "").strip()
    if not handle:
        raise PlatformFetchError("codechef", "Handle not found")

    url = f"https://www.codechef.com/users/{handle}"

    timeout = httpx.Timeout(25.0, connect=10.0)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CP-Tracker",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                raise PlatformFetchError("codechef", "Handle not found")
            resp.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            raise PlatformFetchError("codechef", "Could not fetch data — try syncing again") from e

    html = resp.text or ""
    if "Access Denied" in html or "captcha" in html.lower():
        raise PlatformFetchError("codechef", "Access denied by CodeChef — try again later")

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []

    # CodeChef stores rating history in a JSON blob inside a script tag for Highcharts
    # We look for "var all_rating = "
    script_tags = soup.find_all("script")
    history_data = None
    for script in script_tags:
        if script.string and "var all_rating =" in script.string:
            try:
                # Extract the JSON array from the script string
                match = re.search(r"var all_rating\s*=\s*(\[.*?\]);", script.string, re.DOTALL)
                if match:
                    history_data = json.loads(match.group(1))
                    break
            except (ValueError, json.JSONDecodeError):
                continue

    if history_data:
        for entry in history_data:
            # entry format: {"name":"...", "code":"...", "rating":"...", "rank":"...", "get_date":"18 Feb 2026", ...}
            contest_name = entry.get("name") or entry.get("code") or "Unknown Contest"
            rating = entry.get("rating")
            date_str = entry.get("get_date")
            
            results.append({
                "platform": "codechef",
                "contest_name": contest_name,
                "contest_date": _parse_date(date_str) if date_str else None,
                "rating": int(rating) if rating and str(rating).isdigit() else None,
                "problems_solved": None, # CodeChef history JSON doesn't include solved count
            })
    else:
        # Fallback to current rating if history parsing fails
        rating_link = soup.find("a", href=re.compile(r"/ratings/all", re.I))
        rating_value = None
        if rating_link:
            m = re.search(r"(-?\d+)", rating_link.get_text(" ", strip=True).replace(",", ""))
            if m: rating_value = int(m.group(1))

        if rating_value is not None:
            results.append({
                "platform": "codechef",
                "contest_name": "Current Rating (Snapshot)",
                "contest_date": dt.date.today(),
                "rating": rating_value,
                "problems_solved": None,
            })

    if not results:
        raise PlatformFetchError("codechef", "Could not find rating data")

    results.sort(key=lambda r: (r["contest_date"] is None, r["contest_date"]))
    return results
