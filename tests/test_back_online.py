"""Tests the 'back online' notice: what it says, when it speaks, and that it keeps trying until Telegram works.
    .venv\\Scripts\\python.exe tests\\test_back_online.py   (Telegram, Notion and sleeping are stubbed out)"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import agent_server as a  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print(("PASS " if ok else "FAIL ") + name, detail)


now = time.time()

# 1) wording: a restart after a long gap
msg, dur = a.back_online_message(now - 6.5 * 3600, now, boot=now - 6.3 * 3600)
check("restart: says the PC restarted, the duration, and asks for a fresh sign-in",
      "restarted at" in msg and "6 h 30 min" == dur and "fresh sign-in" in msg and "NOT seen" in msg, f"({dur})")
# 2) wording: no restart (asleep or offline)
msg2, dur2 = a.back_online_message(now - 1800, now, boot=now - 5 * 86400)
check("no restart: says asleep/off/no internet, no sign-in demand",
      "asleep, switched off or had no internet" in msg2 and "fresh sign-in" not in msg2 and dur2 == "30 min", f"({dur2})")

# stubs for the behaviour tests
sent, notes = [], []
state = {}
a.load_state = lambda: state
a.notion_async = lambda *x, **k: notes.append(x[0])
a.time.sleep = lambda s: None            # do not really wait between retries

# 3) short gap: stays quiet (a normal restart of the agent)
state.clear(); state["kandua"] = {"last_report_ts": now - 120}
a.telegram = lambda m, photo=None: (sent.append(m) or {"1": True})
sent.clear(); notes.clear(); a.announce_return()
check("gap of 2 minutes: says nothing", not sent and not notes)

# 4) no history at all (first ever start): stays quiet
state.clear(); sent.clear(); a.announce_return()
check("first ever start: says nothing", not sent)

# 5) long gap: speaks once, and records it
state.clear(); state["kandua"] = {"last_report_ts": now - 7200}; state["stdbank"] = {"last_report_ts": now - 7300}
state["_daily"] = {}
sent.clear(); notes.clear(); a.announce_return()
check("gap of 2 hours: one Telegram message and one Notion row", len(sent) == 1 and len(notes) == 1 and "2 h 0 min" in notes[0], notes)

# 6) the internet is not up yet: keeps retrying, then succeeds
tries = {"n": 0}
def flaky(m, photo=None):
    tries["n"] += 1
    return {"1": tries["n"] >= 4}          # fails 3 times, then works
a.telegram = flaky
sent.clear(); notes.clear(); a.announce_return()
check("internet down at first: retries until it gets through", tries["n"] == 4 and len(notes) == 1, f"({tries['n']} attempts)")

# 7) the boot-time helper returns a sensible moment in the past
bt = a.boot_time()
check("boot time is readable and in the past", bt is not None and bt < time.time(), f"({time.strftime('%Y-%m-%d %H:%M', time.localtime(bt))})" if bt else "")

print("\nall passed" if not failed else f"\n{failed} failed")
sys.exit(1 if failed else 0)
