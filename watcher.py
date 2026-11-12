"""Work Acceptor: watches job sites every minute and alerts on new work.

Commands (run with .venv\\Scripts\\python.exe):
  watcher.py login        open the sites in the agent's Chrome so you can sign in once
  watcher.py once         run one check of every site and print the result
  watcher.py run          check every minute, forever (what the scheduled task runs)

Mode "watch" (sites.json) never clicks Accept. It reads the page, saves a capture
when jobs appear, asks DeepSeek to list them against your rules, and sends a
Telegram alert. Clicking is added per site once we have seen a real job tile.
"""
import base64
import hashlib
import json
import logging
import os
import random
import sys
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "profile"            # one Chrome profile holds all site sign-ins
CAPTURES = ROOT / "captures"
STATE_FILE = ROOT / "state.json"
LOCK_FILE = ROOT / ".watcher.lock"
HERMES = Path(os.environ["LOCALAPPDATA"]) / "hermes"

(ROOT / "logs").mkdir(exist_ok=True)
log = logging.getLogger("watcher")
log.setLevel(logging.INFO)
_h = RotatingFileHandler(ROOT / "logs" / "watcher.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)
log.addHandler(logging.StreamHandler(sys.stdout))


# ---------- settings and secrets ----------

def load_config():
    # sites.json is your private settings; a fresh clone only has sites.example.json, so fall back to that
    p = ROOT / "sites.json"
    if not p.exists():
        p = ROOT / "sites.example.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_env():
    """Keys come from teb-job-acceptor\\.env (used on a PC without Hermes); on the build PC
    they fall back to the Hermes .env files. Later files win, so the local .env is last."""
    env = {}
    for f in (HERMES / ".env", HERMES / "profiles" / "operator" / ".env",
              HERMES / "profiles" / "chiefofstaff" / ".env", ROOT / ".env"):
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    if v.strip():
                        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
MODEL = "deepseek-v4.1-flash"
MODEL_URL = "https://opencode.ai/zen/go/v1/chat/completions"


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------- alerts ----------

def chat_ids():
    """Everyone who gets the alerts: TELEGRAM_HOME_CHANNEL plus TELEGRAM_EXTRA_CHAT_IDS
    (comma separated; one numeric id per person). Add people in teb-job-acceptor\\.env."""
    import re
    ids = []
    for key in ("TELEGRAM_HOME_CHANNEL", "TELEGRAM_EXTRA_CHAT_IDS"):
        for part in re.split(r"[,\s]+", ENV.get(key) or ""):
            if part and part not in ids:
                ids.append(part)
    return ids


OUTBOX = ROOT / "outbox"            # alerts and Notion rows that could not be sent yet (the internet blipped)
OUTBOX_MAX_AGE = 24 * 3600          # a queued item older than this is dropped (and logged)
OUTBOX_MAX_ITEMS = 200
_FLUSH_LOCK = threading.Lock()


def _tg_send(token, chat, text, photo, timeout):
    """One attempt. Returns ("ok" | "retry" | "fail", detail). "retry" = a network problem, a Telegram server
    error or a rate limit (worth trying again). "fail" = Telegram refused it for good (for example the person
    never pressed Start on the bot), so retrying cannot help."""
    try:
        if photo:
            with open(photo, "rb") as f:
                r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                                  data={"chat_id": chat, "caption": text[:1000]}, files={"photo": f}, timeout=timeout)
        else:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat, "text": text[:4000]}, timeout=timeout)
    except Exception as e:
        return "retry", f"{type(e).__name__}: {str(e)[:120]}"
    if r.ok:
        return "ok", ""
    detail = f"{r.status_code} {r.text[:160]}"
    return ("retry" if r.status_code == 429 or r.status_code >= 500 else "fail"), detail


def outbox_put(kind, payload):
    """Queue something to send later (atomic write). kind = "telegram" or "notion"."""
    d = OUTBOX / kind
    d.mkdir(parents=True, exist_ok=True)
    files = sorted(d.glob("*.json"))
    while len(files) >= OUTBOX_MAX_ITEMS:                 # never grow without limit: the oldest goes
        files.pop(0).unlink(missing_ok=True)
    name = f"{int(time.time() * 1000)}_{random.randint(100, 999)}.json"
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps({**payload, "ts": time.time()}), encoding="utf-8")
    tmp.replace(d / name)


def _tg_queue(chat, text, photo):
    """Queue a Telegram message; its picture is copied into the outbox so it cannot be overwritten meanwhile."""
    import shutil
    pname = None
    if photo and Path(photo).exists():
        (OUTBOX / "photos").mkdir(parents=True, exist_ok=True)
        pname = f"{int(time.time() * 1000)}_{random.randint(100, 999)}{Path(photo).suffix or '.jpg'}"
        shutil.copy2(photo, OUTBOX / "photos" / pname)
    outbox_put("telegram", {"chat": chat, "text": text, "photo": pname})


def _tg_deliver(token, chat, text, photo, queue):
    """Send to one person. A picture that will not go through is sent as plain text first (much smaller), and the
    picture follows later. Anything that still fails for a network reason is queued. True = the person got the text."""
    has_photo = bool(photo and Path(photo).exists())
    if has_photo:
        st, why = _tg_send(token, chat, text, photo, 25)
        if st == "ok":
            return True
        if st == "fail":
            log.error("Telegram to %s failed: %s", chat, why)
            return False
        log.warning("Telegram picture to %s did not go through (%s); sending the text on its own", chat, why[:100])
        st2, why2 = _tg_send(token, chat, text, None, 15)
        if st2 == "ok":
            if queue:
                _tg_queue(chat, "(The picture for the message above, sent late.)", photo)
            return True
        if st2 == "fail":
            log.error("Telegram to %s failed: %s", chat, why2)
            return False
        why = why2
    else:
        st, why = _tg_send(token, chat, text, None, 15)
        if st == "ok":
            return True
        if st == "fail":
            log.error("Telegram to %s failed: %s", chat, why)
            return False
    if queue:
        _tg_queue(chat, text, photo if has_photo else None)
        log.error("Telegram send to %s failed (%s); queued, it will be sent when the internet is back", chat, why[:100])
    else:
        log.error("Telegram send to %s failed: %s", chat, why)
    return False


def telegram(text, photo: Path | None = None, queue=True):
    """Send to every chat id. One person failing (for example they never pressed Start on the bot) never stops
    the others. A network failure is retried with a text-only fallback and then queued (queue=False: do not queue,
    for callers that retry themselves). Returns {chat_id: True/False}; False also covers "queued for later"."""
    token, chats = ENV.get("TELEGRAM_BOT_TOKEN"), chat_ids()
    if not token or not chats:
        log.warning("Telegram not configured; alert was: %s", text)
        return {}
    return {chat: _tg_deliver(token, chat, text, photo, queue) for chat in chats}


def flush_outbox():
    """Send what was queued while the internet was down: Telegram messages first, then Notion rows, oldest first.
    Stops at the first network failure (the internet is still down). Safe to call often. Returns how many were sent."""
    if not _FLUSH_LOCK.acquire(blocking=False):
        return 0
    sent = 0
    try:
        token = ENV.get("TELEGRAM_BOT_TOKEN")
        d = OUTBOX / "telegram"
        for f in (sorted(d.glob("*.json")) if d.exists() and token else []):
            try:
                item = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                f.unlink(missing_ok=True)
                continue
            photo = (OUTBOX / "photos" / item["photo"]) if item.get("photo") else None
            if photo is not None and not photo.exists():
                photo = None
            age = time.time() - item.get("ts", time.time())
            if age > OUTBOX_MAX_AGE:
                log.error("Dropped a queued Telegram message older than 24 h: %s", item.get("text", "")[:80])
                f.unlink(missing_ok=True)
                if photo:
                    photo.unlink(missing_ok=True)
                continue
            text = item.get("text", "")
            if age > 90 and not text.startswith("(The picture"):
                text = (f"⏰ DELAYED MESSAGE (meant for {datetime.fromtimestamp(item['ts']):%H:%M}, "
                        "the internet was down):\n" + text)
            st, why = _tg_send(token, item["chat"], text, photo, 25)
            if st == "retry":
                log.info("Outbox: still cannot reach Telegram (%s)", why[:80])
                return sent
            if st == "fail":
                log.error("Queued Telegram message could not be delivered for good: %s", why)
            else:
                sent += 1
                log.info("Outbox: sent a delayed Telegram message (queued %s)", datetime.fromtimestamp(item["ts"]).strftime("%H:%M"))
            f.unlink(missing_ok=True)
            if photo:
                photo.unlink(missing_ok=True)
        nd = OUTBOX / "notion"
        if nd.exists():
            from notion_log import add_event
            for f in sorted(nd.glob("*.json")):
                try:
                    item = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    f.unlink(missing_ok=True)
                    continue
                if time.time() - item.get("ts", time.time()) > OUTBOX_MAX_AGE:
                    log.error("Dropped a queued Notion row older than 24 h: %s", item.get("args", {}).get("name"))
                    f.unlink(missing_ok=True)
                    continue
                res = add_event(**item["args"], _replay=True)
                if res is False:                       # still cannot reach Notion
                    return sent
                if res:
                    sent += 1
                f.unlink(missing_ok=True)                # sent, or refused for good (already logged)
    except Exception as e:
        log.exception("flush_outbox: %s", e)
    finally:
        _FLUSH_LOCK.release()
    return sent


# ---------- DeepSeek ----------

def ask_deepseek(site, page_text, screenshot: Path | None):
    """List the jobs on the page and judge each against the site's rules. Never decides to click."""
    key = ENV.get("OPENCODE_GO_API_KEY")
    if not key:
        return None
    rules = site.get("accept_rules") or "(no rules written yet: say 'unsure' for every job)"
    prompt = (
        f"This is the new-jobs page of {site['name']}, a site where a contractor accepts work.\n"
        f"Accept rules: {rules}\n\n"
        "List every job offered. Reply with JSON only: "
        '{"jobs":[{"title":"","job_id":"","location":"","date_time":"","pay":"","details":"",'
        '"verdict":"accept|skip|unsure","reason":""}]}. '
        "Use only what is on the page; leave fields empty when not shown.\n\n"
        f"PAGE TEXT:\n{page_text[:12000]}"
    )
    content = [{"type": "text", "text": prompt}]
    if screenshot and screenshot.exists():
        b64 = base64.b64encode(screenshot.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    try:
        r = requests.post(MODEL_URL, timeout=90,
                          headers={"Authorization": f"Bearer {key}",
                                   # OpenCode Go rejects requests without a session id
                                   "x-opencode-session": f"workacceptor-{site['id']}-{os.urandom(4).hex()}"},
                          json={"model": MODEL, "messages": [{"role": "user", "content": content}],
                                "temperature": 0})
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"].strip()
        txt = txt[txt.find("{"): txt.rfind("}") + 1]
        return json.loads(txt)
    except Exception as e:
        log.error("DeepSeek call failed for %s: %s", site["id"], e)
        return None


# ---------- page reading ----------

def read_text(page):
    """Visible text. Kandua may be a Flutter app that paints to a canvas, so
    switch on its accessibility layer first; that puts the text into the page."""
    try:
        page.evaluate("""() => {
            const p = document.querySelector('flt-semantics-placeholder');
            if (p) p.click();
        }""")
    except Exception:
        pass
    try:
        text = page.evaluate("""() => {
            const parts = [document.body ? document.body.innerText : ''];
            document.querySelectorAll('[aria-label]').forEach(e => parts.push(e.getAttribute('aria-label')));
            return parts.join('\\n');
        }""")
    except Exception:
        text = ""
    return text or ""


def classify(site, page, text):
    url = page.url.lower()
    if any(p in url for p in site["login_url_parts"]) or any(t.lower() in text.lower() for t in site["login_texts"]):
        return "login_needed"
    if any(t.lower() in text.lower() for t in site["empty_texts"]):
        return "no_jobs"
    if any(t.lower() in text.lower() for t in site["loaded_texts"]):
        return "jobs_found"
    return "unknown"


def check_site(site, page, state, mode):
    sid = site["id"]
    s = state.setdefault(sid, {})
    try:
        if not page.url.startswith(site["jobs_url"].split("?")[0]):
            page.goto(site["jobs_url"], wait_until="domcontentloaded", timeout=45000)
        else:
            page.reload(wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        time.sleep(3)  # let app-style pages draw their tiles
        text = read_text(page)
        status = classify(site, page, text)
    except Exception as e:
        status, text = "error", str(e)

    now = datetime.now().isoformat(timespec="seconds")
    prev = s.get("status")
    s.update(status=status, checked=now)
    log.info("%-8s %s", sid, status)

    if status == "login_needed":
        # alert once, then at most every 2 hours
        last = s.get("login_alert_ts", 0)
        if prev != "login_needed" or time.time() - last > 7200:
            telegram(f"🔐 {site['name']}: signed out. Run 'Work Acceptor - sign in' on the PC and sign in.")
            s["login_alert_ts"] = time.time()
        return status

    if status in ("jobs_found", "unknown"):
        fingerprint = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
        if fingerprint == s.get("last_fingerprint"):
            return status  # same page as last time: already alerted
        s["last_fingerprint"] = fingerprint
        folder = CAPTURES / sid / datetime.now().strftime("%Y-%m-%d_%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        shot = folder / "page.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
            (folder / "page.html").write_text(page.content(), encoding="utf-8")
        except Exception as e:
            log.error("capture failed: %s", e)
        (folder / "page.txt").write_text(text, encoding="utf-8")

        if status == "unknown":
            # page did not look like the jobs page or the empty page: tell the user once
            telegram(f"❓ {site['name']}: page looks different from usual. Saved a capture.", shot)
            return status

        result = ask_deepseek(site, text, shot)
        (folder / "deepseek.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        lines = [f"🆕 {site['name']}: new work showing ({mode} mode, nothing clicked)"]
        for j in (result or {}).get("jobs", [])[:8]:
            lines.append(f"• {j.get('title') or 'Job'} | {j.get('location','')} | {j.get('date_time','')} | "
                         f"{j.get('pay','')} → {j.get('verdict','?').upper()}: {j.get('reason','')}")
        if not result:
            lines.append("(could not read the jobs automatically; see the screenshot)")
        telegram("\n".join(lines), shot)
        # mode "accept" + auto_accept: clicking is built per site after we see a real tile
        return status

    if status == "no_jobs":
        s["last_fingerprint"] = None
    if status == "error":
        s["errors"] = s.get("errors", 0) + 1
        if s["errors"] in (5, 60):
            telegram(f"⚠️ {site['name']}: {s['errors']} checks in a row failed. Last error: {text[:300]}")
    else:
        s["errors"] = 0
    return status


# ---------- browser ----------

def keep_session_cookies():
    """Chrome keeps 'session-only' sign-ins (Standard Bank uses them) across restarts
    only when the profile is set to 'Continue where you left off'."""
    prefs = PROFILE / "Default" / "Preferences"
    if not prefs.exists():
        return
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
        if data.get("session", {}).get("restore_on_startup") != 1:
            data.setdefault("session", {})["restore_on_startup"] = 1
            prefs.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        log.warning("could not set restore_on_startup: %s", e)


def _find_chrome():
    for p in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
              Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
              Path(os.environ["LOCALAPPDATA"])):
        exe = p / "Google" / "Chrome" / "Application" / "chrome.exe"
        if exe.exists():
            return str(exe)
    return r"C:\Program Files\Google\Chrome\Application\chrome.exe"


CHROME = _find_chrome()
CDP_PORT = 9333


def _cdp_up():
    try:
        return requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2).ok
    except Exception:
        return False


def _agent_chrome_running():
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                          "Where-Object { $_.CommandLine -and $_.CommandLine.Contains('"
                          + str(PROFILE).replace("'", "''") + "') } | Measure-Object).Count"],
                         capture_output=True, text=True).stdout.strip()
    return out not in ("", "0")


def open_browser(p, headless=False):
    """Start a plain Chrome (no automation flags) on the agent's profile and connect to it.
    Sites see an ordinary browser; Standard Bank's sign-in fails under Playwright's own launch.
    Chrome stays open between checks and if the watcher restarts, so sign-ins are kept."""
    import subprocess
    PROFILE.mkdir(exist_ok=True)
    if not _cdp_up():
        if _agent_chrome_running():
            raise RuntimeError("The agent's Chrome is open without the control port "
                               "(probably from sign-in). Close that window first.")
        keep_session_cookies()
        subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", f"--remote-debugging-port={CDP_PORT}",
                          "--remote-debugging-address=127.0.0.1", "--no-first-run",
                          "--no-default-browser-check", "--window-size=1280,900", "about:blank"])
        for _ in range(30):
            if _cdp_up():
                break
            time.sleep(1)
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    return browser.contexts[0]


def disconnect(ctx):
    """Leave Chrome running; just drop our connection."""
    try:
        ctx.browser.close()
    except Exception:
        pass


def pages_for(ctx, sites):
    pages = list(ctx.pages)
    out = {}
    for i, site in enumerate(sites):
        page = pages[i] if i < len(pages) else ctx.new_page()
        out[site["id"]] = page
    return out


def close_agent_chrome():
    """Close the agent's Chrome cleanly (so sign-ins are saved to disk)."""
    if _cdp_up():
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            try:
                b.new_browser_cdp_session().send("Browser.close")
            except Exception:
                pass
        for _ in range(20):
            if not _agent_chrome_running():
                break
            time.sleep(1)


def cmd_login():
    """Open a completely ordinary Chrome window (nothing attached) on the agent's profile."""
    import subprocess
    cfg = load_config()
    close_agent_chrome()
    keep_session_cookies()
    only = sys.argv[2] if len(sys.argv) > 2 else None
    urls = [s.get("login_url") or s["jobs_url"] for s in cfg["sites"] if only in (None, s["id"])]
    print("Sign in on each tab in the Chrome window that opened.\n"
          "Tick 'remember me' where offered, open each site's New Jobs page, then close the window.")
    subprocess.Popen([CHROME, f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--no-default-browser-check", *urls]).wait()
    while _agent_chrome_running():
        time.sleep(2)
    print("Window closed; sign-ins saved.")


def cmd_once():
    cfg = load_config()
    state = load_state()
    with sync_playwright() as p:
        ctx = open_browser(p)
        pages = pages_for(ctx, cfg["sites"])
        for site in cfg["sites"]:
            print(site["name"], "->", check_site(site, pages[site["id"]], state, cfg["mode"]),
                  "| at", pages[site["id"]].url)
        save_state(state)
        disconnect(ctx)


def cmd_run():
    # single instance
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        old = LOCK_FILE.read_text().strip()
        if old and _pid_alive(int(old)):
            log.info("Already running (pid %s); exiting", old)
            return
        LOCK_FILE.write_text(str(os.getpid()))
    log.info("Watcher started")
    telegram("✅ Work Acceptor started: checking Kandua and Standard Bank every minute (watch mode).")
    try:
        while True:
            try:
                _run_session()
            except Exception as e:
                log.exception("Browser session crashed: %s; restarting in 30 s", e)
                time.sleep(30)
    finally:
        LOCK_FILE.unlink(missing_ok=True)


def _run_session():
    with sync_playwright() as p:
        ctx = open_browser(p)
        try:
            while True:
                cfg = load_config()  # rules can be edited while it runs
                state = load_state()
                pages = pages_for(ctx, cfg["sites"])
                for site in cfg["sites"]:
                    check_site(site, pages[site["id"]], state, cfg["mode"])
                state["_heartbeat"] = datetime.now().isoformat(timespec="seconds")
                save_state(state)
                time.sleep(cfg["check_every_seconds"] + random.uniform(-5, 5))
        finally:
            disconnect(ctx)


def _pid_alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"
    {"login": cmd_login, "once": cmd_once, "run": cmd_run}[cmd]()
