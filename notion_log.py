"""Writes Work Acceptor activity to the Notion database "TEB Construction Agent Activity".

The database lives under the Hermes page and is built to hold activity from every
TEB Construction agent ("employee"); the Job Acceptor is the first. Uses the Chief of
Staff Notion connection (NOTION_API_KEY in its Hermes .env), as the other Hermes tools do.

  notion_log.py create     one time: create the database, save its id to notion.json
"""
import json
import os
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "notion.json"
HERMES_PAGE = os.environ.get("NOTION_PARENT_PAGE_ID", "")   # the Notion page the activity database is created under
ENV_FILE = Path(os.environ["LOCALAPPDATA"]) / "hermes" / "profiles" / "chiefofstaff" / ".env"
API = "https://api.notion.com/v1"

AGENT = "Job Acceptor"
COMPANY = "TEB Construction"


def _token():
    # teb-job-acceptor\.env first (other PC), then the Chief of Staff .env (build PC)
    for f in (ROOT / ".env", ENV_FILE):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("NOTION_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("NOTION_API_KEY missing (teb-job-acceptor\\.env or Chief of Staff .env)")


def _headers():
    return {"Authorization": f"Bearer {_token()}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _sel(*names):
    return {"select": {"options": [{"name": n} for n in names]}}


def create_database():
    if CONFIG.exists():
        print("Already created:", json.loads(CONFIG.read_text())["database_id"])
        return
    body = {
        "parent": {"type": "page_id", "page_id": HERMES_PAGE},
        "icon": {"type": "emoji", "emoji": "🏗️"},
        "title": [{"type": "text", "text": {"content": "TEB Construction Agent Activity"}}],
        "properties": {
            "Name": {"title": {}},
            "When": {"date": {}},
            "Agent": _sel("Job Acceptor"),
            "Company": _sel("TEB Construction"),
            "Event": _sel("New job", "Daily report", "Signed out", "Checks stopped",
                          "Page changed", "Accepted", "Declined"),
            "Site": _sel("Kandua Pro", "Standard Bank Insurance", "All sites"),
            "Status": _sel("New", "Awaiting approval", "Accepted", "Skipped", "Missed",
                           "Nothing new", "Activity", "Healthy", "Problem"),
            "Job": {"rich_text": {}},
            "Job ID": {"rich_text": {}},
            "Location": {"rich_text": {}},
            "Appointment": {"rich_text": {}},
            "Pay": {"rich_text": {}},
            "AI verdict": _sel("accept", "skip", "unsure"),
            "Checks": {"number": {}},
            "Jobs seen": {"number": {}},
            "Details": {"rich_text": {}},
            "Link": {"url": {}},
        },
    }
    r = requests.post(f"{API}/databases", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    db = r.json()
    CONFIG.write_text(json.dumps({"database_id": db["id"], "url": db["url"]}, indent=2))
    print("Created:", db["url"])


def _txt(v):
    return {"rich_text": [{"type": "text", "text": {"content": str(v)[:1900]}}]} if v else {"rich_text": []}


def _retryable(e):
    """A network problem, a Notion server error or a rate limit is worth trying again; anything else is not."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return e.response.status_code == 429 or e.response.status_code >= 500
    return isinstance(e, requests.RequestException)


def add_event(name, event, site=None, status=None, job=None, details=None, link=None,
              checks=None, jobs_seen=None, when=None, _replay=False):
    """Add one row. Never raises: Notion being down must not stop the watcher.
    If Notion cannot be reached the row is queued (watcher.flush_outbox sends it later, with its original time).
    Returns True when written, False when queued or still unreachable, None when Notion refused it for good."""
    try:
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        when = when or datetime.now()
        saved = dict(name=name, event=event, site=site, status=status, job=job, details=details, link=link,
                     checks=checks, jobs_seen=jobs_seen, when=when.astimezone().isoformat(timespec="seconds"))
        db = json.loads(CONFIG.read_text())["database_id"]
        job = job or {}
        props = {
            "Name": {"title": [{"type": "text", "text": {"content": name[:200]}}]},
            "When": {"date": {"start": (when or datetime.now()).astimezone().isoformat(timespec="seconds")}},
            "Agent": {"select": {"name": AGENT}},
            "Company": {"select": {"name": COMPANY}},
            "Event": {"select": {"name": event}},
            "Job": _txt(job.get("title")),
            "Job ID": _txt(job.get("job_id")),
            "Location": _txt(job.get("location")),
            "Appointment": _txt(job.get("date_time")),
            "Pay": _txt(job.get("pay")),
            "Details": _txt(details or job.get("details")),
        }
        if site:
            props["Site"] = {"select": {"name": site}}
        if status:
            props["Status"] = {"select": {"name": status}}
        if job.get("verdict") in ("accept", "skip", "unsure"):
            props["AI verdict"] = {"select": {"name": job["verdict"]}}
        if link:
            props["Link"] = {"url": link}
        if checks is not None:
            props["Checks"] = {"number": checks}
        if jobs_seen is not None:
            props["Jobs seen"] = {"number": jobs_seen}
        r = requests.post(f"{API}/pages", headers=_headers(), timeout=30,
                          json={"parent": {"database_id": db}, "properties": props})
        r.raise_for_status()
        return True
    except Exception as e:
        retry = _retryable(e)
        try:
            from watcher import log, outbox_put
            if retry and not _replay:
                outbox_put("notion", {"args": saved})
                log.error("Notion write failed (%s): %s; queued to retry", name, e)
            else:
                log.error("Notion write failed (%s): %s", name, e)
        except Exception:
            pass
        return False if retry else None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "create":
        create_database()
