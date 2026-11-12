"""Tests the 'internet blipped' safety net: retry, text-only fallback, a queue that is sent later, and Notion rows.
    .venv\Scripts\python.exe tests\test_outbox.py   (the network, Telegram and Notion are faked; nothing real is sent)"""
import json
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import requests  # noqa: E402
import watcher as w  # noqa: E402
import notion_log as n  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print(("PASS " if ok else "FAIL ") + name, detail)


class Resp:
    def __init__(self, code=200, text="ok"):
        self.status_code, self.text, self.ok = code, text, 200 <= code < 300

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code}", response=self)


tmp = pathlib.Path(tempfile.mkdtemp())
w.OUTBOX = tmp / "outbox"
w.ENV["TELEGRAM_BOT_TOKEN"] = "TEST-TOKEN"
w.ENV["TELEGRAM_HOME_CHANNEL"] = "111"
w.ENV["TELEGRAM_EXTRA_CHAT_IDS"] = ""
n.CONFIG = tmp / "notion.json"
n.CONFIG.write_text(json.dumps({"database_id": "db1"}))
n._headers = lambda: {}
photo = tmp / "after.jpg"
photo.write_bytes(b"\xff\xd8fake-jpeg")

calls = []          # (kind, text-or-name)
mode = {"photo": "ok", "text": "ok", "notion": "ok"}   # ok | down | 403 | 400 | 500


def fake_post(url, data=None, files=None, json=None, headers=None, timeout=None, **k):
    if "api.notion.com" in url:
        calls.append(("notion", json["properties"]["Name"]["title"][0]["text"]["content"], json["properties"]["When"]["date"]["start"]))
        m = mode["notion"]
        if m == "down":
            raise requests.ConnectionError("SSL EOF")
        return Resp(400 if m == "400" else 500 if m == "500" else 200)
    kind = "photo" if url.endswith("sendPhoto") else "text"
    calls.append((kind, (data or {}).get("caption") or (data or {}).get("text")))
    m = mode[kind]
    if m == "down":
        raise requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING")
    if m == "403":
        return Resp(403, "Forbidden: bot was blocked")
    if m == "500":
        return Resp(502, "bad gateway")
    return Resp(200)


requests.post = fake_post
w.time.sleep = lambda s: None


def queued(kind="telegram"):
    d = w.OUTBOX / kind
    return sorted(d.glob("*.json")) if d.exists() else []


def reset(**m):
    calls.clear()
    mode.update({"photo": "ok", "text": "ok", "notion": "ok"})
    mode.update(m)
    for k in ("telegram", "notion"):
        for f in queued(k):
            f.unlink()


# 1) everything fine: sent once, nothing queued
reset()
r = w.telegram("hello", photo)
check("normal send: delivered, nothing queued", r == {"111": True} and not queued() and [c[0] for c in calls] == ["photo"], calls)

# 2) the picture cannot be uploaded (the real incident): the text still gets through at once, the picture is queued
reset(photo="down")
r = w.telegram("ACCEPTED job 1", photo)
check("picture fails: the text is delivered immediately", r == {"111": True} and [c[0] for c in calls] == ["photo", "text"], calls)
q = queued()
check("picture fails: the picture is queued for later (copied, so it cannot be overwritten)", len(q) == 1 and json.loads(q[0].read_text())["photo"]
      and (w.OUTBOX / "photos" / json.loads(q[0].read_text())["photo"]).exists())
photo.write_bytes(b"\xff\xd8changed-later")
calls.clear(); mode["photo"] = "ok"
sent = w.flush_outbox()
check("flush: the queued picture arrives with a 'sent late' caption and the queue is empty",
      sent == 1 and calls and calls[0][0] == "photo" and "sent late" in calls[0][1] and not queued() and not list((w.OUTBOX / "photos").glob("*")), calls)

# 3) everything down: queued, then sent once the internet is back, marked as delayed
reset(photo="down", text="down")
r = w.telegram("NEW JOB on Standard Bank", photo)
check("internet down: not delivered, but queued (not lost)", r == {"111": False} and len(queued()) == 1)
calls.clear()
sent = w.flush_outbox()
check("flush while still down: stays queued, no spam", sent == 0 and len(queued()) == 1 and len(calls) == 1, calls)
item = json.loads(queued()[0].read_text()); item["ts"] -= 400; queued()[0].write_text(json.dumps(item))   # it has waited 400 s
mode["photo"] = "ok"; calls.clear()
sent = w.flush_outbox()
check("flush after the internet is back: sent, labelled DELAYED, queue empty",
      sent == 1 and "DELAYED" in calls[0][1] and "NEW JOB on Standard Bank" in calls[0][1] and not queued(), calls)

# 4) a permanent refusal (403, never pressed Start) is not queued: retrying cannot help
reset(text="403")
r = w.telegram("hi")
check("403 (person never pressed Start): not queued", r == {"111": False} and not queued())

# 5) callers that retry by themselves do not create duplicates
reset(text="down")
w.telegram("back online", queue=False)
check("queue=False: nothing queued", not queued())

# 6) very old items are dropped, not sent
reset(text="down")
w.telegram("ancient")
item = json.loads(queued()[0].read_text()); item["ts"] -= 25 * 3600; queued()[0].write_text(json.dumps(item))
mode["text"] = "ok"; calls.clear()
w.flush_outbox()
check("older than 24 h: dropped, not sent", not queued() and not calls)

# 7) the queue never grows without limit
reset(text="down")
w.OUTBOX_MAX_ITEMS = 5
for i in range(9):
    w.telegram(f"m{i}")
check("queue is capped (oldest dropped)", len(queued()) == 5, len(queued()))
w.OUTBOX_MAX_ITEMS = 200

# 8) Notion: a row that cannot be written is queued with its ORIGINAL time, and written later
reset(notion="down")
res = n.add_event("Standard Bank: accepted Electrical", "Accepted", site="Standard Bank", status="Accepted",
                  job={"title": "Electrical", "job_id": "1"})
check("Notion down: the row is queued, not lost", res is False and len(queued("notion")) == 1)
when_queued = json.loads(queued("notion")[0].read_text())["args"]["when"]
mode["notion"] = "ok"; calls.clear()
sent = w.flush_outbox()
check("Notion back: the row is written with its original time, queue empty",
      sent == 1 and calls and calls[0][1] == "Standard Bank: accepted Electrical" and calls[0][2] == when_queued and not queued("notion"), calls)

# 9) Notion refuses the row for good (400): not queued
reset(notion="400")
res = n.add_event("bad row", "Accepted")
check("Notion 400: not queued", res is None and not queued("notion"))

# 10) Notion still down at flush time: stays queued
reset(notion="down")
n.add_event("row", "Accepted")
w.flush_outbox()
check("Notion still down at flush: stays queued", len(queued("notion")) == 1)

print("\nall passed" if not failed else f"\n{failed} failed")
sys.exit(1 if failed else 0)
