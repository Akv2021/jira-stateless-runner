"""One-shot cleanup: delete all Runner-named duplicate custom fields.

This is not part of the provisioner contract — it exists solely to
normalise a Jira site where earlier provisioner runs (prior to the
pagination fix in ensure_fields) leaked duplicate custom fields under
the 16 canonical Runner names. After this runs cleanly the next
``provision_jira.py`` invocation re-creates a single copy of each.

Safe to delete this file once the provisioner contract has matured.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_URL = os.environ["JIRA_URL"].rstrip("/")
_USER = os.environ["JIRA_USER"]
_TOKEN = os.environ["JIRA_TOKEN"]
_AUTH = base64.b64encode(f"{_USER}:{_TOKEN}".encode()).decode()
_HDR = {"Authorization": f"Basic {_AUTH}", "Accept": "application/json"}

RUNNER_NAMES = {
    "Stage",
    "Work Type",
    "Lifecycle",
    "Difficulty",
    "Revision Target",
    "Revision Done",
    "Outcome",
    "Has Had Test",
    "Last Worked At",
    "Last Transitioned At",
    "Paused At",
    "Last Processed Changelog Id",
    "Last Successful Poll At",
    "Last Stale Scan At",
    "Runner Version",
    "Open Alert Issue Url",
}


async def main() -> None:
    async with httpx.AsyncClient(timeout=60) as c:
        all_fields: list[dict[str, object]] = []
        start_at = 0
        while True:
            r = await c.get(
                f"{_URL}/rest/api/3/field/search?maxResults=50&startAt={start_at}",
                headers=_HDR,
            )
            r.raise_for_status()
            data = r.json()
            all_fields.extend(data.get("values", []))
            total = data.get("total", 0)
            if start_at + len(data.get("values", [])) >= total:
                break
            start_at += 50
        to_delete = [
            f
            for f in all_fields
            if f.get("name") in RUNNER_NAMES
            and isinstance(f.get("id"), str)
            and str(f["id"]).startswith("customfield_")
        ]
        print(f"Found {len(to_delete)} Runner-named custom fields to delete:")
        for f in to_delete:
            print(f'  {f["id"]:25s} {f["name"]!r}')
        print()
        for f in to_delete:
            r = await c.delete(f"{_URL}/rest/api/3/field/{f['id']}", headers=_HDR)
            print(f"DELETE {f['id']} ({f['name']}): {r.status_code}")


if __name__ == "__main__":
    asyncio.run(main())
