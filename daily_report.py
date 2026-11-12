"""18:00 daily report for the Job Acceptor (TEB Construction).

Sends a Telegram summary and adds a "Daily report" row to the Notion database
"TEB Construction Agent Activity". It reports activity AND health (how many checks
ran, how much of the day was covered), so a quiet day can be told apart from a
broken agent. Trial: 2026-11-05 to 2026-11-11.

  daily_report.py            report for today
  daily_report.py --dry-run  print only
"""
import json
import sys
from datetime import date, datetime

from notion_log import add_event
from watcher import load_config, load_state, log, telegram

TRIAL_START = date(2026, 11, 5)
TRIAL_DAYS = 7


def site_health(d, now):
    """Healthy = checked in the last 10 minutes and covered most minutes since the first check."""
    if not d or not d.get("checks"):
        return False, "no checks at all today", 0
    first = datetime.combine(now.date(), datetime.strptime(d["first"], "%H:%M").time())
    last = datetime.combine(now.date(), datetime.strptime(d["last"], "%H:%M").time())
    span = max(1, int((now - first).total_seconds() // 60))
    coverage = min(100, round(100 * d["checks"] / span))
    gap = int((now - last).total_seconds() // 60)
    if gap > 10:
        return False, f"last check {gap} min ago ({d['last']})", coverage
    if coverage < 80:
        return False, f"only {coverage}% of minutes checked", coverage
    return True, "ok", coverage


def send_screens(prefix="📸"):
    """Send the newest screenshot of each site, so you can see what the agent sees."""
    import time
    from watcher import ROOT
    cfg, state = load_config(), load_state()
    for s in cfg["sites"]:
        shot = ROOT / "captures" / "latest" / f"{s['id']}.jpg"
        st = state.get(s["id"], {})
        if shot.exists() and time.time() - shot.stat().st_mtime < 900:
            taken = datetime.fromtimestamp(shot.stat().st_mtime).strftime("%H:%M")
            nice = {"no_jobs": "no new jobs", "jobs_found": "NEW JOBS", "login_needed": "signed out",
                    "unknown": "page looks different", "error": "error"}.get(st.get("status"), st.get("status"))
            telegram(f"{prefix} {s['name']} at {taken}: {nice}", shot)
        else:
            telegram(f"{prefix} {s['name']}: no recent screenshot. Is the agent's Chrome window minimised? "
                     "It must stay open and not minimised for screenshots (behind other windows is fine).")


def main(dry_run=False):
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    cfg = load_config()
    state = load_state()
    day = state.get("_daily", {}).get(today, {})

    total_checks = total_jobs = 0
    all_ok = True
    lines, detail = [], []
    for s in cfg["sites"]:
        d = day.get(s["id"], {})
        ok, why, cov = site_health(d, now)
        all_ok &= ok
        total_checks += d.get("checks", 0)
        total_jobs += d.get("jobs", 0)
        icon = "✅" if ok else "⚠️"
        lines.append(f"{icon} {s['name']}: {d.get('checks', 0)} checks ({cov}% of the day since "
                     f"{d.get('first') or '-'}), {d.get('jobs', 0)} new jobs, {d.get('accepted', 0)} accepted, "
                     f"{d.get('signed_out', 0)} signed-out checks" + ("" if ok else f". Problem: {why}"))
        detail.append(json.dumps({"site": s["name"], **d, "coverage_pct": cov, "health": why}))

    # days since any job was seen (the trial's "nothing for 7 days = something is wrong" test)
    job_days = sorted(k for k, v in state.get("_daily", {}).items()
                      if any(x.get("jobs", 0) for x in v.values() if isinstance(x, dict)))
    since = (now.date() - datetime.strptime(job_days[-1], "%Y-%m-%d").date()).days if job_days else None

    trial_day = (now.date() - TRIAL_START).days + 1
    trial = f"Trial day {trial_day} of {TRIAL_DAYS}. " if 1 <= trial_day <= TRIAL_DAYS else ""
    if total_jobs:
        status, headline = "Activity", f"{total_jobs} new job(s) today"
    elif all_ok:
        status, headline = "Nothing new", "No new jobs today; the agent checked all day"
    else:
        status, headline = "Problem", "The agent missed checks today"
    if since is None or since >= 7:
        nojob = ("no job seen since the agent started" if since is None else f"no job seen for {since} days")
        lines.append(f"🔎 {nojob}. If that's unusual for these sites, check them by hand.")
    else:
        lines.append(f"Last job seen {since} day(s) ago.")

    msg = f"📊 Job Acceptor daily report, {now:%a %d %b}\n{trial}{headline}.\n" + "\n".join(lines)
    print(msg)
    if dry_run:
        return
    telegram(msg)
    send_screens()
    add_event(f"Daily report {today}", "Daily report", site="All sites", status=status if all_ok or total_jobs else "Problem",
              checks=total_checks, jobs_seen=total_jobs,
              details=trial + headline + ". " + " | ".join(lines) + "\n" + "\n".join(detail))
    log.info("Daily report sent: %s", headline)


if __name__ == "__main__":
    if "--proof" in sys.argv:            # ask for reference pictures (dashboard + Jobs in progress) on the next check
        import requests
        sid = sys.argv[sys.argv.index("--proof") + 1] if len(sys.argv) > sys.argv.index("--proof") + 1 else "stdbank"
        print(requests.get(f"http://127.0.0.1:8765/proof?site={sid}", timeout=10).text)
    elif "--now" in sys.argv:              # on-demand status check, any time
        st, cfg = load_state(), load_config()
        today = datetime.now().strftime("%Y-%m-%d")
        nice = {"no_jobs": "no new jobs", "jobs_found": "NEW JOBS", "login_needed": "signed out",
                "unknown": "page looks different", "error": "error"}
        lines = [f"🔎 Status check, {datetime.now():%H:%M}"]
        for s in cfg["sites"]:
            x = st.get(s["id"], {})
            d = st.get("_daily", {}).get(today, {}).get(s["id"], {})
            last = (x.get("checked") or "")[11:16] or "never"
            lines.append(f"• {s['name']}: {nice.get(x.get('status'), x.get('status'))} "
                         f"(last checked {last}; {d.get('checks', 0)} checks today, {d.get('jobs', 0)} new jobs)")
        telegram("\n".join(lines))
        send_screens("📸")
    elif "--screens" in sys.argv:          # test: just send what the agent sees now
        send_screens("📸 TEST:")
    else:
        main(dry_run="--dry-run" in sys.argv)
