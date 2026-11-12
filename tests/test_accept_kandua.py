"""Tests extension/accept_kandua.js against a mock of the Kandua Pro New Jobs site (a single-page app).
Runs in a separate headless Chrome: never touches the real site.
    .venv\\Scripts\\python.exe tests\\test_accept_kandua.py
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SRC = (Path(__file__).resolve().parent.parent / "extension" / "accept_kandua.js").read_text(encoding="utf-8")

HTML = """<html><body style="font-family:sans-serif"><div id="app"></div><script>
const SCEN = "__SCENARIO__"; const JOBS = __JOBS__;
window.stray = 0; window.declined = 0; window.clicked = [];
const root = document.getElementById('app'); const done = new Set();
function list() {
  root.innerHTML = '<p class="page-header__title">New Jobs</p><div class="jobs-grid"></div><nav><button onclick="window.stray++">Home</button><button onclick="window.stray++">Hub</button></nav>';
  const g = root.querySelector('.jobs-grid');
  JOBS.forEach((j, i) => { if (done.has(i)) return;
    const d = document.createElement('div'); d.className = 'kandua-job-card';
    d.innerHTML = `<div class="kandua-job-card__body"><div><p class="kandua-job-card__title">${j.title}</p><span class="kandua-job-card__time-ago">19 min ago</span></div>
      <p class="kandua-job-card__customer">${j.customer}</p><p class="kandua-job-card__date">${j.date}</p>
      <div class="kandua-job-card__location"><span>${j.loc}</span></div></div><div class="kandua-job-card__footer">Marketplace <b>${SCEN === 'taken' ? 'Taken' : 'Available'}</b></div>`;
    d.onclick = () => detail(i, true); g.appendChild(d); });
}
function finish(i) { done.add(i); window.clicked.push(i); root.innerHTML = '<h2>Job accepted</h2><p>You have accepted this job.</p>'; }
function detail(i, push) {
  if (push) history.pushState({}, '', '/marketplace-jobs/' + i);
  const j = JOBS[i];
  root.innerHTML = `<h2>${j.title}</h2><p>${j.desc}</p>${SCEN === 'tall' ? '<div style="height:2500px">long job description</div>' : ''}<div class="actions"></div>`;
  const a = root.querySelector('.actions');
  if (SCEN === 'noaccept') {
    a.innerHTML = '<button id="q">SEND QUOTE</button> <button id="d">DECLINE</button>';
    a.querySelector('#q').onclick = () => window.stray++; a.querySelector('#d').onclick = () => window.declined++; return; }
  if (SCEN === 'money') a.innerHTML = '<span>Lead price R50 will be charged</span> ';
  const REAL = SCEN === 'real' || SCEN === 'unknownpopup' || SCEN === 'msgpopup';
  const b = document.createElement('button'); a.appendChild(b);
  if (REAL) b.innerHTML = '<svg width="12" height="12"></svg> Accept Job'; else b.textContent = 'ACCEPT';
  if (REAL) { const call = document.createElement('button'); call.textContent = 'Call Customer'; call.disabled = true; a.prepend(call); }
  b.onclick = () => {
    if (REAL) {   // Kandua's real pop-up: plain divs, no dialog markup
      const o = document.createElement('div'); o.className = 'overlay';
      o.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;background:rgba(0,0,0,.5)';
      o.innerHTML = '<div class="sheet" style="background:#fff;margin:100px auto;width:380px;padding:20px"><h3>Have You Read The Job Summary?</h3>'
        + '<p>Please make sure you have read the full job summary.</p>'
        + (SCEN === 'msgpopup' ? '<p>Send a message to the customer now?</p><button class="yes">Confirm</button> <a href="#" class="later">Skip</a>' : '')
        + (SCEN === 'msgpopup' ? '' : SCEN === 'unknownpopup' ? '<button class="maybe">Maybe</button> <a href="#" class="later">Later</a>'
                                   : '<button class="yes">Yes, I can do this job</button> <a href="#" class="back">Go Back</a>') + '</div>';
      document.body.appendChild(o);
      const yes = o.querySelector('.yes'); if (yes) yes.onclick = () => { if (SCEN === 'msgpopup') { window.stray++; return; } o.remove(); finish(i); };
      for (const c of ['back', 'maybe', 'later']) { const x = o.querySelector('.' + c); if (x) x.onclick = e => { e.preventDefault(); window.stray++; }; }
      return;
    }
    if (SCEN === 'confirm' || SCEN === 'declaration') {
      const m = document.createElement('div'); m.setAttribute('role', 'dialog');
      m.style.cssText = 'position:fixed;top:80px;left:80px;background:#fff;border:2px solid #000;padding:20px';
      m.innerHTML = SCEN === 'confirm' ? '<p>Accept this job?</p><button class="no">CANCEL</button> <button class="yes">CONFIRM</button>'
        : '<p>Declaration</p><label><input type="checkbox"> I can attend</label> <button class="yes">CONFIRM</button>';
      document.body.appendChild(m);
      m.querySelector('.yes').onclick = () => { if (SCEN === 'confirm') { m.remove(); finish(i); } };
      const no = m.querySelector('.no'); if (no) no.onclick = () => { window.stray++; m.remove(); };
    } else if (SCEN !== 'nochange') finish(i);
  };
}
window.onpopstate = () => list();
list();
</script></body></html>"""


def job(title, desc):
    return {"title": title, "customer": "Test Customer", "date": "06 November 2026", "loc": "Testville", "desc": desc}


ELEC = job("Electrical fixtures repair", "Need a light fitting replaced in the lounge.")
MICRO = job("Electrical appliance repair", "My microwave stopped heating. Please fix the microwave.")
GATE = job("Gate motor repair", "Gate motor not opening.")
WIRING = job("Electrical Design And Wiring", "The customer needs rewiring around their distribution board in Sampletown. "
             "The electrician must have ECA and DOL registration. Make sure you have the right qualifications before you quote.")
PRICEY = job("Electrical Design And Wiring", "Rewiring near the DB. Make sure you have the right qualifications before you quote. "
             "Give a good price to win this job.")

CASES = [
    ("opens the job, clicks ACCEPT", "plain", [ELEC], [],
     lambda r, w: len(r["accepted"]) == 1 and w["clicked"] == [0] and not r["needs_user"]),
    ("microwave job is NOT accepted", "plain", [MICRO], [],
     lambda r, w: not r["accepted"] and len(r["skipped_jobs"]) == 1 and w["clicked"] == []),
    ("microwave skipped, next job accepted (goes back to the list)", "plain", [MICRO, GATE], [],
     lambda r, w: len(r["skipped_jobs"]) == 1 and len(r["accepted"]) == 1 and w["clicked"] == [1]),
    ("pop-up Confirm/Cancel: confirms, never cancels", "confirm", [ELEC], [],
     lambda r, w: len(r["accepted"]) == 1 and w["stray"] == 0),
    ("pop-up with a tick box: stops and asks", "declaration", [ELEC], [],
     lambda r, w: not r["accepted"] and r["needs_user"] and "input" in r["needs_user"]["reason"]),
    ("a price next to Accept: NOT clicked", "money", [ELEC], [],
     lambda r, w: not r["accepted"] and w["clicked"] == [] and "costs money" in r["needs_user"]["reason"]),
    ("no Accept button: stops, lists the buttons, clicks nothing", "noaccept", [ELEC], [],
     lambda r, w: not r["accepted"] and r["needs_user"]["buttons"] == ["SEND QUOTE", "DECLINE"]
     and w["stray"] == 0 and w["declined"] == 0),
    ("Accept does nothing: stops and asks", "nochange", [ELEC], [],
     lambda r, w: not r["accepted"] and "did not change" in r["needs_user"]["reason"]),
    ("job already tried: not opened again", "plain", [ELEC], None,
     lambda r, w: not r["attempted"] and w["clicked"] == []),
    ("My Jobs page (not /new-jobs): nothing opened, nothing clicked", "wrongpath", [ELEC], [],
     lambda r, w: r.get("not_new_jobs_page") is True and not r["attempted"] and w["clicked"] == []),
    ("tile that does not say Available: never opened or clicked", "taken", [ELEC], [],
     lambda r, w: not r["attempted"] and w["clicked"] == [] and w["stray"] == 0),
    ("pop-up about sending a message to the customer: NOT confirmed", "msgpopup", [WIRING], [],
     lambda r, w: not r["accepted"] and w["clicked"] == [] and w["stray"] == 0 and "messaging" in r["needs_user"]["reason"]),
    # Kandua's real flow (owner's screenshots, 2026-11-10)
    ("REAL FLOW: 'Accept Job' -> 'Have You Read The Job Summary?' -> 'Yes, I can do this job' (never 'Go Back')",
     "real", [WIRING], [],
     lambda r, w: len(r["accepted"]) == 1 and w["clicked"] == [0] and w["stray"] == 0 and not r["needs_user"]
     and "Have You Read" in (r["accepted"][0]["dialog_text"] or "")),
    ("REAL FLOW, job text says 'good price' and 'quote': not mistaken for a cost on the button", "real", [PRICEY], [],
     lambda r, w: len(r["accepted"]) == 1 and w["clicked"] == [0] and not r["needs_user"]),
    ("unknown pop-up buttons (Maybe / Later): nothing clicked, asks the owner", "unknownpopup", [WIRING], [],
     lambda r, w: not r["accepted"] and w["clicked"] == [] and w["stray"] == 0
     and "do not recognise" in r["needs_user"]["reason"]),
    # recon (pictures): name, scenario, jobs, tried, check, recon
    ("long job page: scrolls, takes pictures top to bottom, then accepts", "tall", [ELEC], [],
     lambda r, w: len(r["accepted"]) == 1 and w["clicked"] == [0]
     and len([s for s in w["snaps"] if s["label"] == "Kandua job page"]) >= 3
     and [s["i"] for s in w["snaps"] if s["label"] == "Kandua job page"] == list(range(
         len([s for s in w["snaps"] if s["label"] == "Kandua job page"])))
     and any(s["label"] == "Screen after pressing Accept" for s in w["snaps"]) and w["scrolltop"] == 0, True),
    ("pop-up gets its own picture before it is confirmed", "confirm", [ELEC], [],
     lambda r, w: len(r["accepted"]) == 1 and any(s["label"].startswith("Pop-up") for s in w["snaps"]), True),
    ("pictures switched off: none requested, still accepts", "plain", [ELEC], [],
     lambda r, w: len(r["accepted"]) == 1 and w["snaps"] == [], False),
]


def main():
    failed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for case in CASES:
            name, scenario, jobs, tried, check = case[:5]
            recon = case[5] if len(case) > 5 else False
            page = browser.new_page()
            path = "/my-jobs" if scenario == "wrongpath" else "/new-jobs"
            # stand-in for the extension: records every "take a picture" request
            page.add_init_script("window.snaps = []; window.chrome = {runtime: {sendMessage: (m, cb) => { window.snaps.push(m); if (cb) cb({ok: true}); }}};")
            html = HTML.replace("__SCENARIO__", scenario).replace("__JOBS__", json.dumps(jobs))
            def serve(route, *_args, _html=html):
                route.fulfill(body=_html, content_type="text/html")
            page.route("http://mock.test/**", serve)
            page.goto("http://mock.test" + path)
            page.add_script_tag(content=SRC)
            if tried is None:  # learn the job's key from a first run, then run again with it as already tried
                first = page.evaluate("([t, w, c]) => acceptKandua(t, w, c)", [[], ["microwave"], False])
                tried = first["attempted"]
                page.goto("http://mock.test" + path)
                page.add_script_tag(content=SRC)
            res = page.evaluate("([t, w, c]) => acceptKandua(t, w, c)", [tried, ["microwave"], recon])
            w = page.evaluate("({stray: window.stray, declined: window.declined, clicked: window.clicked, snaps: window.snaps, scrolltop: (document.scrollingElement || document.documentElement).scrollTop})")
            ok = bool(check(res, w))
            failed += not ok
            print(("PASS " if ok else "FAIL ") + name, "| stray:", w["stray"], "| declined:", w["declined"],
                  "|", "accepted=%d skipped=%d" % (len(res["accepted"]), len(res["skipped_jobs"])),
                  "needs_user=%s" % ((res["needs_user"] or {}).get("reason")))
            page.close()
        browser.close()
    print("\n%d of %d passed" % (len(CASES) - failed, len(CASES)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
