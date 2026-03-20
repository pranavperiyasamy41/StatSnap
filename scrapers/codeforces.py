from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import httpx

from scrapers.common import PlatformFetchError

async def fetch_codeforces(handle: str) -> list[dict[str, Any]]:
    """
    Fetch Codeforces rating history and solved problems per contest.

    Returns a list of dicts shaped for ContestResult insert:
      - platform, contest_name, contest_date, rating, problems_solved
    """
    handle = (handle or "").strip()
    if not handle:
        raise PlatformFetchError("codeforces", "Handle not found")

    rating_url = "https://codeforces.com/api/user.rating"
    status_url = "https://codeforces.com/api/user.status"
    params = {"handle": handle}

    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "CP-Tracker"}) as client:
        try:
            # Fetch both rating history and submission status in parallel
            rating_task = client.get(rating_url, params=params)
            status_task = client.get(status_url, params=params)
            rating_resp, status_resp = await asyncio.gather(rating_task, status_task)

            rating_resp.raise_for_status()
            status_resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                raise PlatformFetchError("codeforces", "Handle not found") from e
            raise PlatformFetchError("codeforces", "Could not fetch data — try syncing again") from e
        except httpx.RequestError as e:
            raise PlatformFetchError("codeforces", "Could not fetch data — try syncing again") from e

    try:
        rating_payload = rating_resp.json()
        status_payload = status_resp.json()
    except ValueError as e:
        raise PlatformFetchError("codeforces", "Could not fetch data — try syncing again") from e

    if rating_payload.get("status") != "OK":
        comment = str(rating_payload.get("comment") or "")
        if "not found" in comment.lower():
            raise PlatformFetchError("codeforces", "Handle not found")
        raise PlatformFetchError("codeforces", "Could not fetch data — try syncing again")

    # Map contestId -> set of solved problem indexes
    solved_per_contest: dict[int, set[str]] = {}
    if status_payload.get("status") == "OK":
        for sub in status_payload.get("result") or []:
            if sub.get("verdict") == "OK":
                contest_id = sub.get("contestId")
                problem = sub.get("problem", {})
                index = problem.get("index")
                if contest_id and index:
                    if contest_id not in solved_per_contest:
                        solved_per_contest[contest_id] = set()
                    solved_per_contest[contest_id].add(index)

    results: list[dict[str, Any]] = []
    for row in rating_payload.get("result") or []:
        contest_id = row.get("contestId")
        try:
            ts = int(row.get("ratingUpdateTimeSeconds"))
            contest_dt = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date()
        except (ValueError, TypeError):
            contest_dt = None

        problems_count = len(solved_per_contest.get(contest_id, [])) if contest_id else None

        results.append(
            {
                "platform": "codeforces",
                "contest_name": str(row.get("contestName") or "").strip() or "Unknown Contest",
                "contest_date": contest_dt,
                "rating": int(row["newRating"]) if row.get("newRating") is not None else None,
                "problems_solved": problems_count if problems_count is not None else 0,
            }
        )

    results.sort(key=lambda r: (r["contest_date"] is None, r["contest_date"]))
    return results

