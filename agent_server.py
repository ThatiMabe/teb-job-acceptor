"""Work Acceptor agent: receives page reports from the Chrome extension, decides what
they mean, asks DeepSeek to list jobs, and sends Telegram alerts.

Runs on 127.0.0.1:8765. Also keeps the agent's Chrome window open (plain Chrome,
nothing attached, so bot protection such as Standard Bank's Imperva sees a normal browser).

  agent_server.py          run (what the scheduled task starts)
"""
import hashlib
import json
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from watcher import (CAPTURES, CHROME, PROFILE, ROOT, ask_deepseek, keep_session_cookies,
                     load_config, load_state, log, save_state, telegram)

PORT = 8765
LOCK = threading.Lock()
STARTED = time.time()


def site_by_id(cfg, sid):
    return next((s for s in cfg["sites"] if s["id"] == sid), None)


def classify(site, url, text):
    u, t = (url or "").lower(), (text or "").lower()
    if any(p.lower() in u for p in site["login_url_parts"]) or any(x.lower() in t for x in site["login_texts"]):
        return "login_needed"
    part = site.get("new_jobs_url_part")
    if part and part.lower() not in u:
        return "wrong_page"   # e.g. Kandua "My Jobs": its bottom menu says "New Jobs" on every page, so use the address
    if any(x.lower() in t for x in site["empty_texts"]):
        return "no_jobs"
    if "loading spinner" in t:
        return "loading"  # list not loaded yet: say nothing, try again next minute
    if any(x.lower() in t for x in site["loaded_texts"]):
        return "jobs_found"
    if any(x.lower() in t for x in site.get("settled_texts", [])):
        # signed in, but NOT on the New Jobs list (Unassigned, Jobs in progress, the dashboard...): never new jobs
        return "wrong_page"
    return "unknown"


JOBS_LOG = ROOT / "jobs_log.csv"
LOG_FIELDS = ["seen_at", "site", "title", "location", "date_time", "pay", "details",
              "verdict", "reason", "capture_folder"]


def record_jobs(site, result, text, folder):
    """Append every job seen to jobs_log.csv (opens in Excel)."""
    import csv
    new_file = not JOBS_LOG.exists()
    jobs = (result or {}).get("jobs") or [{"title": "(not read automatically)",
                                           "details": " ".join(text.split())[:300]}]
    with open(JOBS_LOG, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for j in jobs:
            w.writerow({**{k: j.get(k, "") for k in LOG_FIELDS},
                        "seen_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "site": site["name"], "capture_folder": str(folder)})


LATEST = CAPTURES / "latest"


def save_screenshot(sid, data_url):
    """Keep the newest picture per site (captures/latest/<site>.jpg); None if there isn't one."""
    import base64
    if not data_url or "," not in data_url:
        return None
    LATEST.mkdir(parents=True, exist_ok=True)
    path = LATEST / f"{sid}.jpg"
    path.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
    return path


def notion_async(*args, **kwargs):
    """Log to Notion without holding up the checks."""
    from notion_log import add_event
    threading.Thread(target=add_event, args=args, kwargs=kwargs, daemon=True).start()


def parse_std_card(card_text):
    """Best-effort fields from a Standard Bank job card. Client name, phone and email are
    deliberately NOT extracted: they stay out of Telegram and Notion."""
    t = " ".join((card_text or "").split())
    def grab(pattern):
        m = re.search(pattern, t, re.I)
        return m.group(1).strip() if m else ""
    return {
        "title": grab(r"Job\s*Id\s*-?\s*\d+\s+(.*?)\s+Loss Class"),
        "loss": grab(r"Loss Class\s+(.*?)\s+Sub Loss Class"),
        "emergency": grab(r"Emergency Service\s+(.*?)\s+Date Of Loss"),
        "location": grab(r"Risk Address\s+(.*?)\s+Email"),
        "date_time": grab(r"Appointment Date\s+(.*?)\s+Policy Holder"),
    }


def _fields(item):
    """Job fields for messages: Kandua sends them ready-made; Standard Bank cards are parsed.
    Client name, phone and email are deliberately never included."""
    f = item.get("fields") or parse_std_card(item.get("card_text", ""))
    return {k: (f.get(k) or "") for k in ("title", "loss", "emergency", "date_time", "location", "description")}


def _job_text(site, jid, f):
    lines = []
    head = f["title"] or "Job"
    lines.append(f"Job {jid}: {head}" if jid and not str(jid).startswith("k-") else head)
    if f["loss"] or f["emergency"]:
        lines.append(f"Loss: {f['loss']} | Emergency: {f['emergency']}")
    if f["date_time"]:
        lines.append(f"Appointment: {f['date_time']}")
    if f["location"]:
        lines.append(f"Address: {f['location']}")
    if f["description"] and site.get("accept_kind") == "kandua":
        lines.append("Details: " + f["description"][:300])
    return "\n".join(lines)


def handle_accept(site, acc, r, url, text, shot, d):
    """The extension clicked ACCEPT (or tried to, or skipped a job by the owner's rule).
    Tell the owner with pictures, log it, record it."""
    folder = CAPTURES / site["id"] / (datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_accept")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "page.txt").write_text(f"{url}\n\n{text}\n\n--- after:\n{acc.get('after_text','')}", encoding="utf-8")
    (folder / "page.html").write_text(r.get("html", ""), encoding="utf-8")
    (folder / "accept.json").write_text(json.dumps(acc, indent=2), encoding="utf-8")
    before = save_screenshot(f"{site['id']}_before", r.get("screenshot_before"))
    if before:
        (folder / "before.jpg").write_bytes(before.read_bytes())
    if shot:
        (folder / "after.jpg").write_bytes(shot.read_bytes())
    kandua = site.get("accept_kind") == "kandua"

    for a in acc.get("accepted", []):
        f = _fields(a)
        job = {"title": f["title"] or "Job", "job_id": "" if str(a["job_id"]).startswith("k-") else a["job_id"],
               "location": f["location"], "date_time": f["date_time"], "pay": "", "verdict": "accept",
               "details": f"{f['description'][:300] or 'Loss: ' + f['loss'] + '. Emergency: ' + f['emergency'] + '.'} Accepted automatically."}
        d["jobs"] += 1
        d["accepted"] = d.get("accepted", 0) + 1
        if before:
            telegram(f"📸 {site['name']}: the job before accepting", before)
        verb = "Clicked ACCEPT (check the screenshot)" if kandua else "ACCEPTED automatically"
        extra = ""
        if kandua and a.get("verify_text"):
            extra = f"\nPage afterwards: {a['verify_text'][:200]}"
        elif a.get("dialog_text"):
            extra = f"\n(pop-up handled: {a['dialog_text'][:150]})"
        if site.get("accept_kind") == "standardbank":
            m = re.search(r"Job\s*Id\s*-?\s*\d+\s+has been accepted by [A-Za-z ]{3,40}", acc.get("after_text", ""))
            extra += (f"\nStandard Bank said: \"{m.group(0).strip()}\"" if m else
                      "\n(I did not see Standard Bank's confirmation message: please check Unassigned Jobs.)")
            extra += ("\nIt has left New Jobs. Find it under Unassigned Jobs or Jobs in progress "
                      "(Standard Bank may still want it assigned to a team leader).")
        if not shot:
            extra += ("\n(No screenshot was possible: the agent's Chrome window was minimised or the screen was off.)")
        telegram(f"✅ {verb} on {site['name']}\n{_job_text(site, a['job_id'], f)}{extra}", shot)
        record_jobs(site, {"jobs": [{**job, "reason": "accepted automatically"}]}, text, folder)
        notion_async(f"{site['name']}: accepted {job['title']}", "Accepted", site=site["name"],
                     status="Accepted", job=job, link=site["jobs_url"])
        log.info("%-8s ACCEPTED %s", site["id"], a["job_id"])

    for sj in acc.get("skipped_jobs", []):
        f = _fields(sj)
        job = {"title": f["title"] or "Job", "job_id": "" if str(sj["job_id"]).startswith("k-") else sj["job_id"],
               "location": f["location"], "date_time": f["date_time"], "pay": "", "verdict": "skip",
               "details": f"Not accepted: {sj.get('reason', '')}"}
        d["jobs"] += 1
        telegram(f"⏭️ SKIPPED on {site['name']} (your rule: {sj.get('reason', '')})\n{_job_text(site, sj['job_id'], f)}\n"
                 f"Not accepted. Open it if you want it: {site['jobs_url']}", before or shot)
        record_jobs(site, {"jobs": [{**job, "reason": sj.get("reason", "")}]}, text, folder)
        notion_async(f"{site['name']}: skipped {job['title']}", "New job", site=site["name"],
                     status="Skipped", job=job, details=sj.get("reason", ""), link=site["jobs_url"])
        log.info("%-8s SKIPPED %s (%s)", site["id"], sj["job_id"], sj.get("reason"))

    nu = acc.get("needs_user")
    if nu:
        f = _fields(nu)
        d["jobs"] += 1
        job = {"title": f["title"] or "Job", "job_id": "" if str(nu.get("job_id") or "").startswith("k-") else (nu.get("job_id") or ""),
               "location": f["location"], "date_time": f["date_time"], "verdict": "unsure", "details": nu.get("reason", "")}
        msg = (f"⚠️ ACCEPT NEEDS YOU on {site['name']}\n{_job_text(site, nu.get('job_id'), f)}\n"
               f"What happened: {nu.get('reason')}\n")
        if nu.get("dialog_text"):
            msg += f"The pop-up said: {nu['dialog_text'][:300]}\n"
        if nu.get("buttons"):
            msg += f"Buttons on the page: {', '.join(nu['buttons'][:15])}\n"
        msg += f"Open it now: {nu.get('url') or site['jobs_url']}"
        telegram(msg, shot)
        record_jobs(site, {"jobs": [{**job, "reason": nu.get("reason", "")}]}, text, folder)
        notion_async(f"{site['name']}: accept needs you ({job['title']})", "Accept needs you",
                     site=site["name"], status="Awaiting approval", job=job,
                     details=(nu.get("reason", "") + " " + (nu.get("dialog_text") or ""))[:1500], link=site["jobs_url"])
        log.warning("%-8s ACCEPT NEEDS USER: %s: %s", site["id"], nu.get("job_id"), nu.get("reason"))


def handle_report(r):
    cfg = load_config()
    site = site_by_id(cfg, r.get("site"))
    if not site:
        return
    with LOCK:
        state = load_state()
        s = state.setdefault(site["id"], {})
        if r.get("load_errors") and time.time() - state.get("_load_alert_ts", 0) > 3600:
            # part of the extension could not be read from disk: checking carries on, but accepting may be off
            state["_load_alert_ts"] = time.time()
            telegram("⚠️ The Work Acceptor extension could not load part of itself: "
                     + "; ".join(r["load_errors"])[:300] + "\nChecking carries on, but accepting may be OFF until you reload "
                     "the extension (chrome://extensions, the reload arrow).")
        text, url = r.get("text", ""), r.get("url", "")
        status = "error" if r.get("error") else classify(site, url, text)
        prev = s.get("status")
        s.update(status=status, checked=datetime.now().isoformat(timespec="seconds"), url=url,
                 last_report_ts=time.time())
        log.info("%-8s %s  %s%s", site["id"], status, url[:80],
                 ("  ERROR: " + str(r.get("error"))[:300]) if r.get("error") else "")
        shot = save_screenshot(site["id"], r.get("screenshot"))
        s["last_text"] = " ".join(text.split())[:800]  # what the page said, for checking which account it is
        if r.get("who"):
            s["signed_in_as"] = r["who"]
        if shot is None:
            log.warning("%-8s no screenshot this check (window minimised, screen off or locked?)", site["id"])
            d_ss = s.setdefault("no_screenshot_since", datetime.now().strftime("%H:%M"))
        else:
            s.pop("no_screenshot_since", None)

        # per-day counters for the 18:00 report (proves the agent kept checking)
        day = state.setdefault("_daily", {}).setdefault(datetime.now().strftime("%Y-%m-%d"), {})
        d = day.setdefault(site["id"], {"checks": 0, "no_jobs": 0, "jobs": 0, "signed_out": 0,
                                         "errors": 0, "unknown": 0, "first": None, "last": None})
        d["checks"] += 1
        key = {"no_jobs": "no_jobs", "login_needed": "signed_out", "error": "errors",
               "unknown": "unknown"}.get(status)
        if key:
            d[key] += 1
        d["first"] = d["first"] or datetime.now().strftime("%H:%M")
        d["last"] = datetime.now().strftime("%H:%M")

        acc = r.get("accept") or {}
        if acc.get("attempted") or acc.get("needs_user") or acc.get("skipped_jobs"):
            handle_accept(site, acc, r, url, text, shot, d)
            # the accept messages replace the usual new-job alert for this page
            s["last_fingerprint"] = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
            s["errors"] = 0
            save_state(state)
            return

        if status == "login_needed":
            if prev != "login_needed" or time.time() - s.get("login_alert_ts", 0) > 7200:
                telegram(f"🔐 {site['name']}: signed out. Sign in on its tab in the Work Acceptor "
                         "Chrome window on the PC (leave the window open).", shot)
                s["login_alert_ts"] = time.time()
                notion_async(f"{site['name']} signed out", "Signed out", site=site["name"],
                             status="Problem", link=site.get("login_url") or site["jobs_url"])
        elif status in ("jobs_found", "unknown"):
            fp = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
            if fp != s.get("last_fingerprint"):
                s["last_fingerprint"] = fp
                folder = CAPTURES / site["id"] / datetime.now().strftime("%Y-%m-%d_%H%M%S")
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "page.txt").write_text(f"{url}\n\n{text}", encoding="utf-8")
                (folder / "page.html").write_text(r.get("html", ""), encoding="utf-8")
                if shot:
                    (folder / "screen.jpg").write_bytes(shot.read_bytes())
                if status == "unknown":
                    telegram(f"❓ {site['name']}: the page looks different from usual ({url}). "
                             "Saved a copy of the page so it can be reviewed.", shot)
                    notion_async(f"{site['name']} page changed", "Page changed", site=site["name"],
                                 status="Problem", details=f"Capture: {folder}", link=url)
                else:
                    # 1) instant phone alert, before the slower AI read
                    telegram(f"🚨 NEW JOB on {site['name']}! Open it now: {site['jobs_url']}", shot)
                    result = ask_deepseek(site, text, None)
                    (folder / "deepseek.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                    if result is not None and not result.get("jobs"):
                        # DeepSeek read the page and found no job: the instant alert was wrong
                        telegram(f"↩️ Correction for {site['name']}: false alarm. Looking closer, "
                                 "there is no new job on the page. Nothing to do.", shot)
                        log.info("%-8s false alarm corrected (DeepSeek found 0 jobs)", site["id"])
                        s["last_fingerprint"] = None
                        save_state(state)
                        return
                    record_jobs(site, result, text, folder)
                    jobs = (result or {}).get("jobs") or [{"title": "(not read automatically)",
                                                           "details": " ".join(text.split())[:500]}]
                    d["jobs"] += len(jobs)
                    for j in jobs:
                        notion_async(f"{site['name']}: {j.get('title') or 'New job'}", "New job",
                                     site=site["name"], status="New", job=j, link=site["jobs_url"])
                    # 2) follow-up with the details
                    lines = [f"📋 {site['name']} job details (" + ("accepting did not run, please open it" if site.get("accept_kind") else "this site is not accepted automatically yet, please open it") + ")"]
                    for j in (result or {}).get("jobs", [])[:8]:
                        lines.append(f"• {j.get('title') or 'Job'} | {j.get('location','')} | "
                                     f"{j.get('date_time','')} | {j.get('pay','')} → "
                                     f"{(j.get('verdict') or '?').upper()}: {j.get('reason','')}")
                    if not result:
                        lines.append("(couldn't read the jobs automatically; open the site to see them)")
                    telegram("\n".join(lines))
        elif status == "no_jobs":
            s["last_fingerprint"] = None
        elif status == "wrong_page":
            # nothing is read or recorded from these pages. If the extension cannot get back to New Jobs, say so.
            s["wrong_page"] = s.get("wrong_page", 0) + 1
            if s["wrong_page"] in (5, 60):
                telegram(f"⚠️ {site['name']}: the tab has been on a page other than New Jobs for {s['wrong_page']} "
                         f"checks and I could not click back to it. Please open New Jobs on that tab. ({url[:80]})", shot)
        if status != "wrong_page":
            s["wrong_page"] = 0

        if status == "error":
            s["errors"] = s.get("errors", 0) + 1
            if s["errors"] in (5, 60):
                telegram(f"⚠️ {site['name']}: {s['errors']} checks in a row failed: {r.get('error','')[:200]}")
        else:
            s["errors"] = 0
        save_state(state)


def recon_default(site):
    return int(site.get("recon_first_jobs", 0) or 0)


def handle_recon(r):
    """A picture of a job page (or pop-up) from the extension. Save it, send it to Telegram.
    Only the first `recon_first_jobs` jobs per site are photographed; the counter drops when a job's
    first picture arrives."""
    import base64
    sid, label = r.get("site", "unknown"), r.get("label", "page")
    i, total = int(r.get("i") or 0), int(r.get("total") or 1)
    data = r.get("image") or ""
    cfg = load_config()
    site = site_by_id(cfg, sid) or {"name": sid}
    if label.startswith("Reference") and i == 0:                       # the requested reference has arrived
        with LOCK:
            state = load_state()
            state["_proof_now"] = [x for x in state.get("_proof_now", []) if x != sid]
            save_state(state)
    if r.get("note"):                                                  # the extension could not take a picture
        telegram(f"⚠️ {site['name']}: {label}\nNo screenshot was possible: the agent's Chrome window was minimised, "
                 "the screen was off or the PC was locked. Keep that window open and not minimised "
                 "(behind other windows is fine).")
        return
    if "," not in data:
        return
    with LOCK:
        state = load_state()
        rec = state.setdefault("_recon", {}).setdefault(sid, {})
        if (label == "Kandua job page" or label.startswith("Reference")) and i == 0:   # a new set: new folder
            suffix = "_jobpage" if label == "Kandua job page" else "_reference"
            rec["folder"] = str(CAPTURES / sid / (datetime.now().strftime("%Y-%m-%d_%H%M%S") + suffix))
        if label == "Kandua job page" and i == 0:                      # one fewer job to photograph
            left = state.setdefault("_recon_left", {})
            left[sid] = left.get(sid, recon_default(site)) - 1
        folder = pathlib.Path(rec.get("folder") or (CAPTURES / sid / (datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_jobpage")))
        rec["folder"] = str(folder)
        folder.mkdir(parents=True, exist_ok=True)
        n = len(list(folder.glob("*.jpg"))) + 1
        path = folder / f"{n:02d}.jpg"
        path.write_bytes(base64.b64decode(data.split(",", 1)[1]))
        save_state(state)
    telegram(f"📸 {site['name']}: {label} ({i + 1}/{total})", path)
    log.info("%-8s recon picture saved: %s", sid, path.name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/config":
            cfg = load_config()
            # the extension only needs where to go and what a login page looks like
            st = load_state()
            left, proof = st.get("_recon_left", {}), st.get("_proof_now", [])
            sites = [{**{k: s.get(k) for k in ("id", "name", "jobs_url", "login_url", "login_url_parts",
                                               "loaded_texts", "empty_texts", "login_texts",
                                               "accept_kind", "auto_accept", "landing_url",
                                               "settled_texts", "nav_new_jobs", "proof_after_accept", "new_jobs_url_part")},
                      "recon": left.get(s["id"], recon_default(s)) > 0,   # photograph the job page this time?
                      "proof": s["id"] in proof}                         # reference pictures requested now?
                     for s in cfg["sites"]]
            self._send(200, json.dumps({"sites": sites, "skip_words": cfg.get("skip_if_contains", [])}).encode())
        elif self.path.startswith("/proof"):
            # ask for reference pictures (dashboard + Jobs in progress) on the next check:  /proof?site=stdbank
            from urllib.parse import urlparse, parse_qs
            sid = (parse_qs(urlparse(self.path).query).get("site") or [""])[0]
            if site_by_id(load_config(), sid):
                with LOCK:
                    st = load_state()
                    st["_proof_now"] = sorted(set(st.get("_proof_now", [])) | {sid})
                    save_state(st)
                self._send(200, json.dumps({"ok": True, "site": sid}).encode())
            else:
                self._send(404)
        elif self.path == "/status":
            self._send(200, json.dumps(load_state(), indent=2).encode())
        else:
            self._send(404)

    def do_POST(self):
        if self.path not in ("/report", "/recon"):
            return self._send(404)
        n = int(self.headers.get("Content-Length", 0))
        try:
            r = json.loads(self.rfile.read(n) or b"{}")
            handler = handle_recon if self.path == "/recon" else handle_report
            threading.Thread(target=handler, args=(r,), daemon=True).start()
            self._send(200, b"{}")
        except Exception as e:
            log.exception("bad report: %s", e)
            self._send(400)


# ---------- keep Chrome open, and notice if checks stop ----------

def agent_chrome_running():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                          "Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
                          + str(PROFILE).replace("'", "''") + "') } | Measure-Object).Count"],
                         capture_output=True, text=True, creationflags=0x08000000).stdout.strip()
    return out not in ("", "0")


def start_chrome():
    keep_session_cookies()
    cfg = load_config()
    urls = [s.get("login_url") or s["jobs_url"] for s in cfg["sites"]]
    subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--no-default-browser-check", "--window-size=1280,900",
                      # keep drawing pages when the window is covered or the screen is off,
                      # so screenshots still work while you're away
                      "--disable-features=CalculateNativeWinOcclusion",
                      "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                      *urls])
    log.info("Started the agent's Chrome")


def watchdog():
    alerted = 0.0
    while True:
        time.sleep(60)
        try:
            if not agent_chrome_running():
                start_chrome()
                telegram("🔁 The Work Acceptor Chrome window was closed, so I reopened it. "
                         "Standard Bank will need you to sign in again on the PC.")
                continue
            state = load_state()
            cfg = load_config()
            stale = [s["name"] for s in cfg["sites"]
                     if time.time() - state.get(s["id"], {}).get("last_report_ts", 0) > 300]
            if stale and time.time() - STARTED > 300 and time.time() - alerted > 3600:
                telegram("⚠️ No checks received for 5+ minutes from: " + ", ".join(stale) +
                         ". Is the Work Acceptor extension switched on in its Chrome window?")
                notion_async("Checks stopped: " + ", ".join(stale), "Checks stopped",
                             site="All sites", status="Problem")
                alerted = time.time()
        except Exception as e:
            log.exception("watchdog: %s", e)


def main():
    # single instance: if the port is taken, another copy is running
    with socket.socket() as sck:
        if sck.connect_ex(("127.0.0.1", PORT)) == 0:
            print("Agent already running")
            return
    ThreadingHTTPServer.allow_reuse_address = False  # on Windows, reuse would let a 2nd copy share the port
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print("Agent already running")
        return
    log.info("Agent listening on %s", PORT)
    if not agent_chrome_running():
        start_chrome()
    threading.Thread(target=watchdog, daemon=True).start()
    if "--quiet" not in sys.argv:
        telegram("✅ Work Acceptor started: checking Kandua and Standard Bank every minute (watch mode).")
    server.serve_forever()


if __name__ == "__main__":
    main()
