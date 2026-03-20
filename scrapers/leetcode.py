from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from scrapers.common import PlatformFetchError


LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"


def _to_date_from_seconds(seconds: int | None) -> dt.date | None:
    if not seconds:
        return None
    try:
        return dt.datetime.fromtimestamp(int(seconds), tz=dt.timezone.utc).date()
    except Exception:
        return None


async def fetch_leetcode(handle: str) -> list[dict[str, Any]]:
    """
    Fetch LeetCode contest history using GraphQL.

    Returns a list of dicts shaped for ContestResult insert:
      - platform, contest_name, contest_date, rating, problems_solved
    """
    handle = (handle or "").strip()
    if not handle:
        raise PlatformFetchError("leetcode", "Handle not found")

    query = """
    query userContestRankingHistory($username: String!) {
      userContestRankingHistory(username: $username) {
        contest {
          title
          startTime
        }
        rating
        problemsSolved
        finishTimeInSeconds
      }
    }
    """

    payload = {"query": query, "variables": {"username": handle}}
    timeout = httpx.Timeout(25.0, connect=10.0)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CP-Tracker",
        "Referer": "https://leetcode.com",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        try:
            resp = await client.post(LEETCODE_GRAPHQL_URL, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise PlatformFetchError(
                "leetcode", "Could not fetch data — try syncing again"
            ) from e
        except httpx.RequestError as e:
            raise PlatformFetchError(
                "leetcode", "Could not fetch data — try syncing again"
            ) from e

    try:
        body = resp.json()
    except ValueError as e:
        raise PlatformFetchError("leetcode", "Could not fetch data — try syncing again") from e

    if body.get("errors"):
        msg = " ".join(str(err.get("message") or "") for err in body["errors"])
        if "not found" in msg.lower() or "does not exist" in msg.lower():
            raise PlatformFetchError("leetcode", "Handle not found")
        raise PlatformFetchError("leetcode", "Could not fetch data — try syncing again")

    data = body.get("data") or {}
    history = data.get("userContestRankingHistory")

    if history is None:
        # For nonexistent users, LeetCode often returns null history without an error.
        raise PlatformFetchError("leetcode", "Handle not found")

    results: list[dict[str, Any]] = []
    for row in history or []:
        contest = row.get("contest") or {}
        title = str(contest.get("title") or "").strip() or "Unknown Contest"

        # Prefer finish time for the contest date; fallback to contest start time.
        contest_date = _to_date_from_seconds(row.get("finishTimeInSeconds")) or _to_date_from_seconds(
            contest.get("startTime")
        )

        rating = row.get("rating")
        problems = row.get("problemsSolved")

        results.append(
            {
                "platform": "leetcode",
                "contest_name": title,
                "contest_date": contest_date,
                "rating": int(rating) if isinstance(rating, (int, float)) else None,
                "problems_solved": int(problems) if isinstance(problems, (int, float)) else None,
            }
        )

    results.sort(key=lambda r: (r["contest_date"] is None, r["contest_date"]))
    return results

