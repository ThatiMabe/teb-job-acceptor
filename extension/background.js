// Work Acceptor extension: every minute, refresh each job site's tab in THIS window,
// read the page, and send it to the local agent (agent_server.py on 127.0.0.1:8765).
// Accepting: on Standard Bank tabs (site.accept_kind === 'standardbank' and site.auto_accept)
// it clicks ACCEPT on new jobs via accept_stdbank.js. Kandua has no accept step yet.
// Helper scripts. Reading one can fail now and then (an antivirus lock on a freshly edited file, a disk hiccup).
// That must never kill the whole extension: each load is retried, and a failure is reported to the owner instead.
const LOAD_ERRORS = [];
for (const f of ['accept_stdbank.js', 'accept_kandua.js', 'proof_stdbank.js', 'nav_stdbank.js']) {
  let ok = false, last = null;
  for (let i = 0; i < 4 && !ok; i++) {
    try { importScripts(f); ok = true; }
    catch (e) { last = e; const t0 = Date.now(); while (Date.now() - t0 < 150) { /* short pause, then retry */ } }
  }
  if (!ok) LOAD_ERRORS.push(f + ': ' + ((last && last.message) || last));
}
const ACCEPTORS = {};
if (typeof acceptStdBank === 'function') ACCEPTORS.standardbank = acceptStdBank;
if (typeof acceptKandua === 'function') ACCEPTORS.kandua = acceptKandua;
const SERVER = 'http://127.0.0.1:8765';
let running = false;

// The alarm ticks every 30 s (the shortest Chrome allows). Each site has its own rhythm (check_every_seconds from the
// agent: Kandua 30 s, Standard Bank 60 s), and runChecks only starts the sites that are due.
const TICK_MIN = 0.5;
function schedule() { chrome.alarms.create('check', { periodInMinutes: TICK_MIN, delayInMinutes: 0.1 }); }
// alarms survive a restart of this background script; only (re)create it when it is missing or has the old 1-minute rhythm
chrome.alarms.get('check', a => { if (!a || a.periodInMinutes !== TICK_MIN) schedule(); });
chrome.runtime.onInstalled.addListener(async () => {
  await chrome.storage.local.remove('tabMap');  // re-claim tabs left to right after an update
  // After an update, forget the Kandua jobs it had given up on ("k-..." keys) so a job still sitting in New Jobs gets
  // another try with the new code. A job that has already been accepted is no longer in the list, so nothing happens.
  const tried = (await chrome.storage.local.get('tried')).tried || {};
  for (const k of Object.keys(tried)) if (k.startsWith('k-')) delete tried[k];
  await chrome.storage.local.set({ tried });
  schedule();
});
chrome.runtime.onStartup.addListener(async () => {
  await chrome.storage.local.remove('tabMap');  // new browser session: old tab ids mean nothing
  schedule();
});
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'check') runChecks(); });

// Chrome's Memory Saver can swap a background tab for a new one with a new id. Follow it, so the tab is not "lost"
// (this was behind the repeated "No tab with id" errors on Kandua).
chrome.tabs.onReplaced.addListener(async (addedId, removedId) => {
  const map = await getTabMap();
  for (const k of Object.keys(map)) if (map[k] === removedId) map[k] = addedId;
  await chrome.storage.local.set({ tabMap: map });
});
chrome.action.onClicked.addListener(() => runChecks(true));

// A job page asks for a picture of itself (accept_kandua.js, recon mode). Capture the tab and pass it to the agent.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.type !== 'snap' || !sender.tab) return false;
  (async () => {
    try {
      const map = await getTabMap();
      const siteId = Object.keys(map).find(k => map[k] === sender.tab.id) || 'unknown';
      const image = await snap(sender.tab, true);    // brings the tab to the front and photographs it
      if (image) {
        await net(SERVER + '/recon', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ site: siteId, label: msg.label, i: msg.i, total: msg.total, image, url: sender.tab.url })
        });
      }
      sendResponse({ ok: !!image });
    } catch (e) { sendResponse({ ok: false }); }
  })();
  return true;                                       // answer asynchronously
});

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Every site runs on its own: a slow Standard Bank page never holds Kandua back. Two things are shared and therefore
// taken in turns: taking pictures (only one tab can be photographed at a time) and accepting (while a job is being
// accepted, no other site may bring its own tab to the front, because a hidden tab runs slowly).
const siteRunning = new Set();
const lastStart = {};
let snapChain = Promise.resolve();
let accepting = 0, acceptChain = Promise.resolve();

// Nothing may wait forever: a check that hangs (the agent restarting mid-request, a page that never answers) would
// otherwise stop that site being checked for good. Each wait has a limit, and a stuck check is abandoned and retried.
let SITE_TIMEOUT_MS = 200000;       // one whole check of one site
let ACCEPT_TIMEOUT_MS = 150000;     // one accept routine (opening jobs, clicking, confirming)
const withTimeout = (p, ms, what) => Promise.race([
  p, new Promise((_, reject) => setTimeout(() => reject(new Error(what + ' timed out after ' + Math.round(ms / 1000) + ' s')), ms))]);
const net = (url, opts) => fetch(url, { ...(opts || {}), signal: AbortSignal.timeout(30000) });   // never wait forever on the agent

async function runChecks(force) {
  let cfg;
  try { cfg = await (await net(SERVER + '/config')).json(); }
  catch (e) { chrome.action.setBadgeText({ text: 'OFF' }); return; }  // agent not running
  chrome.action.setBadgeText({ text: '' });
  for (const site of cfg.sites) {
    const every = (site.check_every_seconds || 60) * 1000;
    if (siteRunning.has(site.id)) {
      if (Date.now() - (lastStart[site.id] || 0) < SITE_TIMEOUT_MS + 30000) continue;   // still busy with the last check
      siteRunning.delete(site.id);                                                       // stuck far too long: start afresh
    }
    if (!force && lastStart[site.id] && Date.now() - lastStart[site.id] < every - 5000) continue;   // not due yet
    siteRunning.add(site.id);
    lastStart[site.id] = Date.now();
    const mine = lastStart[site.id];
    withTimeout(runSite(site, cfg.skip_words || []), SITE_TIMEOUT_MS, 'check of ' + site.name)
      .catch(e => report(site, { url: '', text: '', html: '', error: String(e) }).catch(() => {}))
      .finally(() => { if (lastStart[site.id] === mine) siteRunning.delete(site.id); });
  }
}

async function runSite(site, skipWords) {
  let err = null;
  try { await checkSite(site, skipWords); }
  catch (e) {
    err = e;
    if (/No tab with id/i.test(String(e))) {            // the tab was swapped or closed under us: look again, once
      try { await checkSite(site, skipWords); err = null; } catch (e2) { err = e2; }
    }
  }
  if (err) await report(site, { url: '', text: '', html: '', error: String(err) });
  if (site.proof) {                       // the owner asked for a reference picture now
    try { const t = await findTab(site); if (t) await proofShots(t, site, 'on request'); } catch (e) { /* next check */ }
  }
}

// Run fn while no other site is accepting, and block other sites' pictures until it is done.
async function withAcceptLock(fn) {
  const prev = acceptChain;
  let release;
  acceptChain = new Promise(r => { release = r; });
  await prev;
  accepting++;
  try { return await withTimeout(fn(), ACCEPT_TIMEOUT_MS, 'accept'); } finally { accepting--; release(); }
}
async function whenNoAccept(maxMs) {
  const t0 = Date.now();
  while (accepting > 0 && Date.now() - t0 < (maxMs || 60000)) await sleep(200);
}

function hostOf(u) { try { return new URL(u).host; } catch { return ''; } }

// Each site owns one tab, remembered by tab id. Needed because two accounts share the
// same site (Standard Bank Gauteng + North West): same address, different tabs.
async function getTabMap() { return (await chrome.storage.local.get('tabMap')).tabMap || {}; }
async function setTab(siteId, tabId) {
  const map = await getTabMap();
  map[siteId] = tabId;
  await chrome.storage.local.set({ tabMap: map });
}

async function findTab(site) {
  const map = await getTabMap();
  const tabs = await chrome.tabs.query({});
  if (map[site.id]) {
    const mine = tabs.find(t => t.id === map[site.id]);
    // tab ids restart when Chrome restarts, so only trust one still on this site
    if (mine && hostOf(mine.url || mine.pendingUrl || '') === hostOf(site.jobs_url)) {
      chrome.tabs.update(mine.id, { autoDiscardable: false }).catch(() => {});   // do not let Chrome put it to sleep
      return mine;
    }
    delete map[site.id];
    await chrome.storage.local.set({ tabMap: map });
  }
  // not remembered (first run or Chrome restarted): take a tab on this site no other site owns
  const owned = new Set(Object.entries(map).filter(([k]) => k !== site.id)
    .map(([, v]) => v).filter(id => tabs.some(t => t.id === id)));
  const host = hostOf(site.jobs_url);
  const free = tabs.find(t => hostOf(t.url || t.pendingUrl || '') === host && !owned.has(t.id));
  if (free) {
    await setTab(site.id, free.id);
    chrome.tabs.update(free.id, { autoDiscardable: false }).catch(() => {});
  }
  return free;
}

function waitComplete(tabId, timeoutMs = 45000) {
  return new Promise(resolve => {
    const done = () => { chrome.tabs.onUpdated.removeListener(fn); clearTimeout(t); resolve(); };
    const fn = (id, info) => { if (id === tabId && info.status === 'complete') done(); };
    const t = setTimeout(done, timeoutMs);
    chrome.tabs.onUpdated.addListener(fn);
  });
}

function isLoginUrl(site, url) {
  return (site.login_url_parts || []).some(p => url.toLowerCase().includes(p.toLowerCase()));
}

async function checkSite(site, skipWords) {
  let tab = await findTab(site);
  if (!tab) {
    // a brand-new tab has no sign-in for sites like Standard Bank; open the login page
    tab = await chrome.tabs.create({ url: site.login_url || site.jobs_url, active: false });
    await setTab(site.id, tab.id);
    await waitComplete(tab.id);
  } else if (isLoginUrl(site, tab.url || '')) {
    // waiting for the user to sign in: do not reload under their typing
  } else if (!(tab.url || '').startsWith(site.jobs_url)) {
    // same tab, so the sign-in (kept per tab) survives the move
    await chrome.tabs.update(tab.id, { url: site.jobs_url });
    await waitComplete(tab.id);
  } else {
    await chrome.tabs.reload(tab.id);
    await waitComplete(tab.id);
  }
  const markers = [...(site.loaded_texts || []), ...(site.empty_texts || []), ...(site.login_texts || []),
                   ...(site.settled_texts || [])];
  let res;
  const read = () => chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readPage, args: [markers] });
  try { [res] = await read(); }
  catch (e1) {
    // "Cannot access contents of the page": the tab is on a Chrome error page, a block page or a
    // browser page. Load the jobs address again in the SAME tab (keeps its sign-in) and retry once.
    await chrome.tabs.update(tab.id, { url: site.jobs_url });
    await waitComplete(tab.id);
    try { [res] = await read(); }
    catch (e2) {
      const t2 = await chrome.tabs.get(tab.id).catch(() => ({}));
      throw new Error(`${e2.message || e2} | tab shows: url=${t2.url} title=${t2.title} status=${t2.status}`);
    }
  }
  // Kandua: if the tab ended up on another page (My Jobs after you accepted something, a job page...), go back to the
  // New Jobs address once and read again. Only the /new-jobs page counts as the New Jobs list.
  const onNewJobsUrl = r => !site.new_jobs_url_part || (r.result.url || '').toLowerCase().includes(site.new_jobs_url_part.toLowerCase());
  if (site.new_jobs_url_part && !onNewJobsUrl(res)) {
    const t1 = res.result.text || '';
    const out1 = isLoginUrl(site, res.result.url || '') || (site.login_texts || []).some(x => t1.toLowerCase().includes(x.toLowerCase()));
    if (!out1) {
      await chrome.tabs.update(tab.id, { url: site.jobs_url });
      await waitComplete(tab.id);
      try { [res] = await read(); } catch (e) { /* keep what we had */ }
    }
  }
  // Standard Bank: the tab may be on another list (Unassigned, In progress, the dashboard). Only the "New Jobs" list
  // counts, and its subtitle is unique to it. If we are elsewhere, click through to New Jobs and read again.
  const NEW_JOBS_PAGE = /Jobs has been assigned but not yet accepted/i;
  if (site.accept_kind === 'standardbank' && site.nav_new_jobs) {
    const t0 = res.result.text || '';
    const signedOut = isLoginUrl(site, res.result.url || '') || (site.login_texts || []).some(x => t0.toLowerCase().includes(x.toLowerCase()));
    if (!NEW_JOBS_PAGE.test(t0) && !signedOut && /sign out/i.test(t0)) {
      try { await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: openNewJobs }); } catch (e) { /* ignore */ }
      await sleep(2500);
      try { [res] = await read(); } catch (e) { /* keep what we had */ }
    }
  }
  let accept = null, screenshotBefore = null, screenshotAfter = null;
  const t = res.result.text || '';
  const hasJob = site.accept_kind === 'standardbank'
    ? NEW_JOBS_PAGE.test(t) && /Job\s*Id\s*-?\s*\d{6,}/i.test(t) && !/no new jobs found/i.test(t) && !/loading spinner/i.test(t)
    : site.accept_kind === 'kandua'
      ? t.trim().length > 0 && !/no new jobs yet/i.test(t) && !isLoginUrl(site, res.result.url || '') && onNewJobsUrl(res)
      : false;
  if (ACCEPTORS[site.accept_kind] && site.auto_accept && hasJob) {
    await withAcceptLock(async () => {
      screenshotBefore = await snap(tab, true);               // the job, before anything is clicked (restores a minimised window)
      const tried = await getTried();
      const [ar] = await chrome.scripting.executeScript({
        target: { tabId: tab.id }, func: ACCEPTORS[site.accept_kind], args: [Object.keys(tried), skipWords, !!site.recon] });
      accept = ar.result;
      for (const id of accept.attempted || []) tried[id] = Date.now();  // never click the same job twice
      await setTried(tried);
      screenshotAfter = await snap(tab, true);                // after a click, always get the picture
    });
  }
  const screenshot = accept ? screenshotAfter : await snap(tab, false);
  await report(site, { ...res.result, screenshot, screenshot_before: screenshotBefore, accept });
  // After an accepted Standard Bank job: photograph the dashboard and the "Jobs in progress" list, so the owner can
  // see where the job went (accepted jobs leave the New Jobs list).
  if (site.proof_after_accept && site.accept_kind === 'standardbank' && accept && (accept.accepted || []).length) {
    await proofShots(tab, site, 'after accepting Job ' + accept.accepted.map(a => a.job_id).join(', '));
  }
}

// Reference pictures: the dashboard, then the Jobs in progress list. Read-only; the next check moves the tab
// back to the New Jobs list. If no picture can be taken (window minimised), say so instead of staying silent.
async function proofShots(tab, site, title) {
  const send = async (label, i, total) => {
    const image = await snap(tab);
    if (!image) return false;
    await net(SERVER + '/recon', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site: site.id, label: `Reference ${title}: ${label}`, i, total, image, url: '' })
    });
    return true;
  };
  let ok1 = false, ok2 = false;
  try {
    await chrome.tabs.update(tab.id, { url: site.landing_url || site.jobs_url });
    await waitComplete(tab.id);
    await sleep(5000);                                   // let the dashboard tiles load
    ok1 = await send('your dashboard', 0, 2);
    try { await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: openJobsInProgress }); } catch (e) { /* ignore */ }
    await sleep(5000);
    ok2 = await send('Jobs in progress list', 1, 2);
  } catch (e) { /* fall through to the note below */ }
  if (!ok1 && !ok2) {
    try {
      await net(SERVER + '/recon', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ site: site.id, label: `Reference ${title}`, i: 0, total: 1, note: true })
      });
    } catch (e) { /* ignore */ }
  }
}

// Job ids already clicked, kept for a week, shared by all tabs (two tabs may show the same job).
async function getTried() {
  const tried = (await chrome.storage.local.get('tried')).tried || {};
  const cutoff = Date.now() - 7 * 86400000;
  for (const k of Object.keys(tried)) if (tried[k] < cutoff) delete tried[k];
  return tried;
}
async function setTried(tried) { await chrome.storage.local.set({ tried }); }

// Picture of what the agent sees. Chrome can only photograph the tab that is showing,
// so bring this site's tab to the front of the agent's window first. Not possible while
// the window is minimised.
async function snap(tab, force) {
  if (!force) await whenNoAccept();                    // routine pictures wait while a job is being accepted
  const run = () => snapNow(tab, force);
  const p = snapChain.then(run, run);                  // one picture at a time
  snapChain = p.catch(() => {});
  return p;
}
async function snapNow(tab, force) {
  try {
    if (!force && accepting > 0) return null;          // an accept started meanwhile: do not steal the front tab
    const win = await chrome.windows.get(tab.windowId);
    if (win.state === 'minimized') {
      if (!force) return null;                          // routine checks never pop the window up
      // job pictures matter more: bring the window back (without taking keyboard focus) so it can be photographed
      await chrome.windows.update(tab.windowId, { state: 'normal', focused: false });
      await sleep(1200);
    }
    const now = await chrome.tabs.get(tab.id);
    if (now.active) { await sleep(120); }               // already showing: only let it paint
    else { await chrome.tabs.update(tab.id, { active: true }); await sleep(350); }
    return await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: 60 });
  } catch (e) { return null; }
}

// Runs inside the page. Both sites are single-page apps that draw after loading,
// so wait (up to 40 s) until a known marker appears and the text stops changing.
async function readPage(markers) {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const grab = () => {
    const parts = [document.body ? document.body.innerText : ''];
    document.querySelectorAll('[aria-label]').forEach(e => parts.push(e.getAttribute('aria-label')));
    return parts.join('\n');
  };
  const lower = markers.map(m => m.toLowerCase());
  let text = '', prev = '';
  for (let i = 0; i < 40; i++) {
    await wait(1000);
    text = grab();
    const t = text.toLowerCase();
    const found = lower.some(m => t.includes(m));
    // Standard Bank shows "Loading Spinner" under NewJobs until the job list arrives
    const spinner = /loading spinner/i.test(text) || (/loading/i.test(text) && text.length < 200);
    if (found && !spinner && text === prev) break;  // marker shown and page settled
    prev = text;
  }
  // Which account is this tab signed in as? Standard Bank keeps a sign-in key per tab;
  // if it is a JWT, read only the user name/email fields from it (never send the key itself).
  let who = null;
  try {
    const key = sessionStorage.getItem('authkey') || '';
    const part = key.split('.')[1];
    if (part) {
      const p = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')));
      who = ['preferred_username', 'username', 'user_name', 'unique_name', 'email', 'upn', 'sub', 'name', 'region']
        .filter(k => p[k]).map(k => `${k}=${p[k]}`).join(', ') || null;
    } else if (key) {
      // opaque key: send only a short fingerprint, so two tabs can be compared
      const h = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
      who = 'session ' + [...new Uint8Array(h)].slice(0, 4).map(b => b.toString(16).padStart(2, '0')).join('');
    }
  } catch (e) { who = null; }
  return {
    who,
    url: location.href,
    title: document.title,
    text,
    html: document.documentElement.outerHTML.slice(0, 800000)
  };
}

async function report(site, data) {
  try {
    await net(SERVER + '/report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site: site.id, ...data, load_errors: LOAD_ERRORS.length ? LOAD_ERRORS : undefined })
    });
  } catch (e) { /* agent down; next minute retries */ }
}
