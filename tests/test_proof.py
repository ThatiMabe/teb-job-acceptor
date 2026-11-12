"""Tests extension/proof_stdbank.js (opens "Jobs in progress") on mocks of the Standard Bank dashboard, and rehearses
the agent's handling of reference pictures. Nothing real is touched.   .venv\\Scripts\\python.exe tests\\test_proof.py"""
import base64
import pathlib
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "extension" / "proof_stdbank.js").read_text(encoding="utf-8")

TILES = """<html><body><div class="tiles">
 <div class="tile" id="new" onclick="window.opened='new'"><b>01</b><span>New Jobs</span></div>
 <div class="tile" id="prog" onclick="window.opened='progress'"><b>02</b><span>Jobs in Progress</span></div>
 <button id="signout" onclick="window.stray++">SIGN OUT</button></div><script>window.stray=0;window.opened=null;</script></body></html>"""

MENU = """<html><body><button aria-label="Open menu" onclick="document.getElementById('nav').style.display='block'">=</button>
 <ul id="nav" style="display:none"><li>Dashboard</li><li>New Jobs</li><li onclick="window.opened='progress'">Jobs in progress</li></ul>
 <button id="signout" onclick="window.stray++">SIGN OUT</button><script>window.stray=0;window.opened=null;</script></body></html>"""

NOTHING = "<html><body><p>Welcome</p><button onclick=\"window.stray++\">SIGN OUT</button><script>window.stray=0;window.opened=null;</script></body></html>"


NAV_SRC = (ROOT / "extension" / "nav_stdbank.js").read_text(encoding="utf-8")

NJ_TILES = """<html><body><div class="tiles">
 <div class="tile" onclick="window.opened='new'"><b>00</b><span>New Jobs</span></div>
 <div class="tile" onclick="window.opened='unassigned'"><b>01</b><span>Unassigned Jobs</span></div>
 <div class="tile" onclick="window.opened='progress'"><b>02</b><span>Jobs in Progress</span></div>
 <button onclick="window.stray++">SIGN OUT</button></div><script>window.stray=0;window.opened=null;</script></body></html>"""

NJ_MENU = """<html><body><button aria-label="Open menu">=</button>
 <ul style="position:absolute;left:-300px;top:0;width:200px"><li>Dashboard</li>
 <li onclick="window.opened='new'">New Jobs 00</li><li onclick="window.opened='unassigned'">Unassigned Jobs 01</li>
 <li onclick="window.opened='progress'">Jobs in progress 02</li></ul>
 <button onclick="window.stray++">SIGN OUT</button><script>window.stray=0;window.opened=null;</script></body></html>"""

NJ_NONE = "<html><body><h1>UnassignedJobs</h1><button onclick=\"window.stray++\">ASSIGN</button><script>window.stray=0;window.opened=null;</script></body></html>"


def main():
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for name, html, expect_clicked, expect_open in [
            ("back to New Jobs: dashboard tile '00 New Jobs' (not Unassigned, not In progress)", NJ_TILES, True, "new"),
            ("back to New Jobs: off-screen menu entry 'New Jobs 00'", NJ_MENU, True, "new"),
            ("no New Jobs link on the page: clicks nothing (never ASSIGN)", NJ_NONE, False, None),
        ]:
            page = browser.new_page()
            page.set_content(html)
            page.add_script_tag(content=NAV_SRC)
            res = page.evaluate("() => openNewJobs()")
            opened, stray = page.evaluate("[window.opened, window.stray]")
            ok = res["clicked"] == expect_clicked and opened == expect_open and stray == 0
            failed += not ok
            print(("PASS " if ok else "FAIL ") + name, "| result:", res, "| stray clicks:", stray)
            page.close()
        for name, html, expect_clicked, expect_open in [
            ("dashboard tile '02 Jobs in Progress' is clicked", TILES, True, "progress"),
            ("collapsed menu: opens it, then clicks 'Jobs in progress'", MENU, True, "progress"),
            ("no such link: clicks nothing", NOTHING, False, None),
        ]:
            page = browser.new_page()
            page.set_content(html)
            page.add_script_tag(content=SRC)
            res = page.evaluate("() => openJobsInProgress()")
            opened, stray = page.evaluate("[window.opened, window.stray]")
            ok = res["clicked"] == expect_clicked and opened == expect_open and stray == 0
            failed += not ok
            print(("PASS " if ok else "FAIL ") + name, "| result:", res, "| stray clicks:", stray)
            page.close()
        browser.close()

    # the agent's side, with an in-memory state and Telegram stubbed
    sys.path.insert(0, str(ROOT))
    import agent_server as a
    a.CAPTURES = pathlib.Path(tempfile.mkdtemp())
    mem = {"_proof_now": ["stdbank"]}
    a.load_state, a.save_state = (lambda: mem), (lambda s: None)
    sent = []
    a.telegram = lambda msg, photo=None: sent.append((msg, photo.name if photo else None))
    jpg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xd9").decode()
    a.handle_recon({"site": "stdbank", "label": "Reference after accepting Job 1: your dashboard", "i": 0, "total": 2, "image": jpg})
    a.handle_recon({"site": "stdbank", "label": "Reference after accepting Job 1: Jobs in progress list", "i": 1, "total": 2, "image": jpg})
    ok = (mem["_proof_now"] == [] and [s[1] for s in sent] == ["01.jpg", "02.jpg"]
          and "_recon_left" not in mem)
    failed += not ok
    print(("PASS " if ok else "FAIL ") + "agent: reference pictures saved and sent, request cleared | ", sent)
    sent.clear()
    a.handle_recon({"site": "stdbank", "label": "Reference on request", "i": 0, "total": 1, "note": True})
    ok = len(sent) == 1 and "No screenshot was possible" in sent[0][0]
    failed += not ok
    print(("PASS " if ok else "FAIL ") + "agent: says so when no picture was possible | ", sent[0][0][:70] if sent else None)
    print("\n%d failed" % failed if failed else "\nall passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
