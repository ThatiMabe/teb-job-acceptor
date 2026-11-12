"""Tests extension/accept_stdbank.js against a mock of the Standard Bank New Jobs page.
Runs in a separate headless Chrome: never touches the real portal.
    .venv\\Scripts\\python.exe tests\\test_accept.py
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = (Path(__file__).resolve().parent.parent / "extension" / "accept_stdbank.js").read_text(encoding="utf-8")

PAGE = """
<html><body style="font-family:sans-serif">
<header><button id="signout" onclick="window.stray++">SIGN OUT</button> <button onclick="window.stray++">SEARCH</button></header>
<h1>NewJobs</h1><p id="sub"></p><div id="list"></div><div id="modal-root"></div>
<script>
window.declined = 0; window.stray = 0; window.cancelled = 0; window.confirmed = 0;
const SCENARIO = "%(scenario)s";
document.getElementById('sub').textContent = SCENARIO === 'wrongpage'
  ? 'Jobs has been accepted but not yet assigned to Service provider Team leader'   // the Unassigned Jobs page
  : 'Jobs has been assigned but not yet accepted';                                  // the real New Jobs page
const JOBS = %(jobs)s;
function card(id, kind) {
  const d = document.createElement('div'); d.className = 'job-card'; d.dataset.id = id;
  d.style.cssText = 'border:1px solid #ccc;margin:10px;padding:10px';
  d.innerHTML = `<div><b>New</b><h2>Job Id-${id}</h2><div>${kind}</div></div>
    <div>Loss Class Lightning Damage  Appointment Date 25-Jun-2026 Before 09:00 am</div>
    <div>Policy Holder Details  Name Surname TEST PERSON  Risk Address 1 Test Street, GAUTENG</div>
    <div><a href="#" onclick="window.stray++;return false">VIEW MORE</a>
      <button class="decline" onclick="window.declined++">DECLINE</button>
      <button class="accept">ACCEPT</button></div>`;
  d.querySelector('.accept').onclick = () => onAccept(d);
  return d;
}
function onAccept(d) {
  if (SCENARIO === 'plain' || SCENARIO === 'two') setTimeout(() => d.remove(), 300);
  if (SCENARIO === 'nochange') { /* the site does nothing */ }
  if (SCENARIO === 'confirm' || SCENARIO === 'declaration') {
    const m = document.createElement('div'); m.setAttribute('role', 'dialog');
    m.style.cssText = 'position:fixed;top:100px;left:100px;background:#fff;border:2px solid #000;padding:20px';
    m.innerHTML = SCENARIO === 'confirm'
      ? `<p>Are you sure you want to accept this job?</p>
         <button class="cancel">CANCEL</button> <button class="yes">CONFIRM</button>`
      : `<p>Declaration</p><label><input type="checkbox"> I declare I can attend</label>
         <button class="yes">CONFIRM</button> <button class="cancel">CANCEL</button>`;
    document.getElementById('modal-root').appendChild(m);
    m.querySelector('.cancel').onclick = () => { window.cancelled++; m.remove(); };
    m.querySelector('.yes').onclick = () => {
      if (SCENARIO === 'confirm') { window.confirmed++; m.remove(); d.remove(); }
    };
  }
}
const list = document.getElementById('list');
const KIND = id => String(id).startsWith('9') ? 'Electrical repair: Microwave oven' : 'Electronic Repairs - Gate Motors';
JOBS.forEach(id => list.appendChild(card(id, KIND(id))));
</script></body></html>
"""

CASES = [
    # name, scenario, jobs, already_tried, checks
    ("plain accept, no pop-up", "plain", ["1000000001"], [],
     lambda r, w: r["accepted"] and r["accepted"][0]["job_id"] == "1000000001" and not r["needs_user"]),
    ("pop-up with CONFIRM / CANCEL", "confirm", ["1000000001"], [],
     lambda r, w: len(r["accepted"]) == 1 and w["confirmed"] == 1 and w["cancelled"] == 0 and not r["needs_user"]),
    ("pop-up with a tick box: stops and asks the owner", "declaration", ["1000000001"], [],
     lambda r, w: not r["accepted"] and r["needs_user"] and "input" in r["needs_user"]["reason"]),
    ("site does nothing after ACCEPT: stops and asks", "nochange", ["1000000001"], [],
     lambda r, w: not r["accepted"] and r["needs_user"] and "still on" in r["needs_user"]["reason"]),
    ("two jobs: accepts both, by id", "two", ["1000000001", "1000000002"], [],
     lambda r, w: [a["job_id"] for a in r["accepted"]] == ["1000000001", "1000000002"]),
    ("microwave job skipped, the other accepted", "two", ["9000000001", "1000000002"], [],
     lambda r, w: [x["job_id"] for x in r["skipped_jobs"]] == ["9000000001"]
     and [a["job_id"] for a in r["accepted"]] == ["1000000002"] and not r["needs_user"]),
    ("Unassigned Jobs page (other subtitle): nothing is clicked, nothing attempted", "wrongpage", ["1000000001"], [],
     lambda r, w: r.get("not_new_jobs_page") is True and not r["attempted"] and not r["accepted"]),
    ("job already tried: never clicked twice", "plain", ["1000000001"], ["1000000001"],
     lambda r, w: not r["attempted"] and r["skipped"] == ["1000000001"]),
]


def main():
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for name, scenario, jobs, tried, check in CASES:
            page = browser.new_page()
            page.set_content(PAGE % {"scenario": scenario, "jobs": str(jobs).replace("'", '"')})
            page.add_script_tag(content=SRC)
            res = page.evaluate("([t, w]) => acceptStdBank(t, w)", [tried, ["microwave"]])
            w = page.evaluate("({declined: window.declined, stray: window.stray, cancelled: window.cancelled, confirmed: window.confirmed})")
            ok = bool(check(res, w)) and w["declined"] == 0 and w["stray"] == 0
            failed += not ok
            print(("PASS " if ok else "FAIL ") + name,
                  "| declined clicks:", w["declined"], "| stray clicks:", w["stray"],
                  "|", "accepted=%s needs_user=%s" % ([a["job_id"] for a in res["accepted"]],
                                                       (res["needs_user"] or {}).get("reason")))
            page.close()
        browser.close()
    print("\n%d of %d passed" % (len(CASES) - failed, len(CASES)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
