// Kandua Pro "accept the job" routine. It runs INSIDE the page (background.js injects it).
//
// Built from the owner's screenshot of the New Jobs list (a card per job: title, customer, date, place,
// "Marketplace" / "Available") and the owner's description: "click the job, then find the Accept button".
// The JOB PAGE AFTER OPENING A CARD HAS NEVER BEEN SEEN, so this is deliberately cautious:
//   - opens the card (only reads), then reads the job page;
//   - owner's rule: a job mentioning a skip word (for example "microwave") is NOT accepted;
//   - clicks ONLY a single button whose text is an accept label (ACCEPT, ACCEPT JOB, ...);
//   - NEVER clicks anything that looks like it costs money (price, credits, pay, buy, unlock, R amounts);
//   - handles one plain Yes/Confirm pop-up; anything else: STOP and report needs_user, with the page's
//     buttons listed, so the owner (or a maintainer) can see what Kandua really shows;
//   - once per job (key = title + customer + date + place), never loops.
// Self-contained on purpose: executeScript serialises this function into the page.
async function acceptKandua(alreadyTried, skipWords, recon) {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const label = el => norm(el.innerText || el.textContent || el.value || '').toUpperCase();
  const clickables = root =>
    [...root.querySelectorAll('button, [role="button"], a, input[type="button"], input[type="submit"]')].filter(vis);
  const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return (h >>> 0).toString(16); };
  const MONEY = /(\bR\s?\d)|\bZAR\b|credit|\bpay\b|payment|\bbuy\b|purchase|unlock|\bfee\b|\bprice\b|\bcost\b|top.?up|wallet|subscri/i;
  const ACCEPT = ['ACCEPT', 'ACCEPT JOB', 'ACCEPT REQUEST', 'ACCEPT THIS JOB', 'ACCEPT LEAD'];
  // Kandua's real flow (owner's screenshots, 2026-11-10): job page "Marketplace Job Details" -> button "Accept Job"
  // (bottom right) -> pop-up "Have You Read The Job Summary?" with "Yes, I can do this job" and "Go Back"
  // -> press "Yes, I can do this job". Never "Go Back".
  const YES = ['YES', 'CONFIRM', 'OK', 'OKAY', 'ACCEPT', 'CONTINUE', 'PROCEED', 'DONE', 'YES, ACCEPT', 'CONFIRM ACCEPT',
               'YES, I CAN DO THIS JOB', 'YES I CAN DO THIS JOB'];
  const isYes = b => YES.includes(label(b).replace(/[.!]+$/, ''));
  const DIALOG = '[role="dialog"], [role="alertdialog"], .modal.show, .modal[style*="display: block"], ' +
    '.mat-mdc-dialog-container, .mat-dialog-container, .cdk-overlay-pane, .swal2-popup, .p-dialog, .ant-modal';
  const q = (el, sel) => { const x = el.querySelector(sel); return x ? norm(x.innerText) : ''; };

  // "recon" mode: take pictures of the job page (scrolling down in steps) and of any pop-up, so the owner can
  // see what Kandua really shows. The page cannot photograph itself, so it asks the extension (background.js),
  // which captures the visible tab and forwards the picture to the agent.
  const askShot = msg => new Promise(res => {
    try { chrome.runtime.sendMessage(msg, r => { void chrome.runtime.lastError; res(r); }); } catch (e) { res(null); }
  });
  const scroller = () => {
    const se = document.scrollingElement || document.documentElement;
    if (se.scrollHeight > innerHeight + 40) return se;
    let best = null;
    for (const e of document.querySelectorAll('*')) {
      if (e.scrollHeight > e.clientHeight + 40 && /(auto|scroll)/.test(getComputedStyle(e).overflowY) && vis(e)
          && (!best || e.clientHeight > best.clientHeight)) best = e;
    }
    return best;
  };
  const snapAll = async (title, scroll) => {
    if (!recon || typeof chrome === 'undefined' || !chrome.runtime || !chrome.runtime.sendMessage) return;
    const el = scroll ? scroller() : null;
    const view = el ? (el === (document.scrollingElement || document.documentElement) ? innerHeight : el.clientHeight) : 0;
    const step = Math.max(200, Math.floor(view * 0.8));
    const total = el ? Math.min(8, Math.ceil((el.scrollHeight - view) / step) + 1) : 1;
    for (let i = 0; i < total; i++) {
      if (el) el.scrollTop = i * step;
      await wait(900);
      await askShot({ type: 'snap', label: title, i, total });
    }
    if (el) el.scrollTop = 0;
  };

  const cards = () => [...document.querySelectorAll('.kandua-job-card')].filter(vis);
  const info = c => {
    const f = {
      title: q(c, '.kandua-job-card__title'), customer: q(c, '.kandua-job-card__customer'),
      date_time: q(c, '.kandua-job-card__date'), location: q(c, '.kandua-job-card__location')
    };
    return { ...f, text: norm(c.innerText), key: 'k-' + hash([f.title, f.customer, f.date_time, f.location].join('|')) };
  };
  const goBack = async () => {
    history.back();
    for (let i = 0; i < 12; i++) { await wait(500); if (cards().length) return true; }
    return false;
  };

  const out = { accepted: [], attempted: [], skipped_jobs: [], skipped: [], needs_user: null, after_text: '' };
  // Only ever act on the "New Jobs" list. "My Jobs" shows jobs that are already accepted, with the same card markup.
  if (!location.pathname.startsWith('/new-jobs')) { out.not_new_jobs_page = true; return out; }
  const tried = new Set(alreadyTried || []);

  for (let round = 0; round < 4; round++) {
    // only tiles whose status says "Available" (a tile that says anything else is not open for accepting)
    const next = cards().map(c => ({ c, ...info(c) })).find(x => !tried.has(x.key) && /\bavailable\b/i.test(x.text));
    if (!next) break;
    tried.add(next.key);
    out.attempted.push(next.key);

    // 1) open the job (reading only)
    const startUrl = location.href;
    next.c.click();
    let opened = false;
    for (let i = 0; i < 16 && !opened; i++) { await wait(500); opened = location.href !== startUrl || cards().length === 0; }
    if (!opened) {
      out.needs_user = { job_id: next.key, fields: next, reason: 'Clicking the job card did not open the job.' };
      break;
    }
    let text = '', prev = '';
    for (let i = 0; i < 10; i++) { await wait(700); text = norm(document.body.innerText); if (text && text === prev) break; prev = text; }
    const detailUrl = location.href;
    const fields = { title: next.title, date_time: next.date_time, location: next.location, loss: '', emergency: '',
                     description: text.slice(0, 1500) };

    // 2) owner's rule
    const hay = (next.text + ' ' + text).toLowerCase();
    const hit = (skipWords || []).find(w => hay.includes(String(w).toLowerCase()));
    if (hit) {
      out.skipped_jobs.push({ job_id: next.key, fields, reason: 'mentions "' + hit + '"', url: detailUrl });
      if (!(await goBack())) break;
      continue;
    }

    // 2b) pictures of the whole job page, top to bottom (first few jobs only: the agent decides via `recon`)
    await snapAll('Kandua job page', true);

    // 3) find the accept button on the job page
    const controls = clickables(document);
    const accepts = controls.filter(b => ACCEPT.includes(label(b)));
    if (accepts.length !== 1) {
      out.needs_user = {
        job_id: next.key, fields, url: detailUrl, buttons: controls.map(label).filter(Boolean).slice(0, 30),
        reason: accepts.length ? 'The job page has more than one Accept button.' : 'No Accept button found on the job page.',
        page_text: text.slice(0, 1200)
      };
      break;
    }
    const btn = accepts[0];

    // 4) never spend money: look at the button and its immediate surroundings (only if that area is small, so a long
    //    job description that happens to say "price" does not trigger it)
    const nearText = btn.parentElement ? norm(btn.parentElement.innerText) : '';
    const near = nearText.length <= 150 ? nearText : '';
    if (MONEY.test(label(btn)) || MONEY.test(near)) {
      out.needs_user = {
        job_id: next.key, fields, url: detailUrl, buttons: controls.map(label).filter(Boolean).slice(0, 30),
        reason: 'The accept step looks like it costs money, so it was NOT clicked: "' + (near || label(btn)).slice(0, 160) + '"',
        page_text: text.slice(0, 1200)
      };
      break;
    }

    // 5) click Accept Job, then deal with the pop-up. The pop-up is found two ways: standard dialog markup, OR any
    //    buttons whose labels were not on the page before the click (Kandua's pop-up is plain markup).
    const labelsBefore = new Set(clickables(document).map(label));
    btn.click();
    let popup = null, fresh = [];
    for (let i = 0; i < 16 && !popup; i++) {
      await wait(500);
      const std = [...document.querySelectorAll(DIALOG)].find(d => vis(d) && label(d).length > 0) || null;
      fresh = clickables(document).filter(b => !labelsBefore.has(label(b)));
      if (std || fresh.length) {
        popup = std || fresh[0].closest('[role="dialog"], [class*="modal"], [class*="dialog"], [class*="popup"], [class*="sheet"], [class*="overlay"]')
                || fresh[0].parentElement;
        break;
      }
      if (!clickables(document).some(b => ACCEPT.includes(label(b)))) break;  // Accept button went away: no pop-up
    }
    let dialogText = null;
    if (popup) {
      await snapAll('Pop-up after pressing Accept', false);
      dialogText = norm(popup.innerText).slice(0, 600);
      const inputs = [...popup.querySelectorAll('input:not([type="hidden"]):not([type="button"]):not([type="submit"]), select, textarea')].filter(vis);
      const btns = [...new Set([...clickables(popup), ...fresh])];
      const yes = btns.filter(isYes);
      if (/\b(message|messages|sms|whatsapp|e-?mail|quote|quotation|invoice|call|notify|send)\b/i.test(dialogText)) {
        out.needs_user = { job_id: next.key, fields, url: detailUrl, dialog_text: dialogText, buttons: btns.map(label),
                           reason: 'A pop-up after ACCEPT is about messaging, quoting or calling, so it was NOT confirmed.' };
        break;
      }
      if (MONEY.test(dialogText)) {
        out.needs_user = { job_id: next.key, fields, url: detailUrl, dialog_text: dialogText, buttons: btns.map(label),
                           reason: 'A pop-up after ACCEPT mentions money, so it was NOT confirmed.' };
        break;
      }
      if (inputs.length === 0 && yes.length === 1) { yes[0].click(); await wait(1500); }
      else {
        out.needs_user = { job_id: next.key, fields, url: detailUrl, dialog_text: dialogText, buttons: btns.map(label),
                           reason: inputs.length ? 'A pop-up after ACCEPT asks for input (tick box, date or text).'
                                                 : 'A pop-up after ACCEPT has buttons I do not recognise.' };
        break;
      }
    }

    // 6) did it take? The Accept button is gone, the page moved on, or the page says the job was accepted
    await snapAll('Screen after pressing Accept', false);
    const HINT = /(job|request)\s+(has been\s+|was\s+)?accepted|you(\s+have|'ve)\s+accepted|accepted\s+successfully|successfully\s+accepted/i;
    let done = false;
    for (let i = 0; i < 16 && !done; i++) {
      await wait(500);
      done = !clickables(document).some(b => ACCEPT.includes(label(b))) || location.href !== detailUrl
             || HINT.test(norm(document.body.innerText));
    }
    const afterText = norm(document.body.innerText).slice(0, 600);
    if (done) {
      out.accepted.push({ job_id: next.key, fields, dialog_text: dialogText, url: detailUrl, verify_text: afterText });
    } else {
      out.needs_user = { job_id: next.key, fields, url: detailUrl, dialog_text: dialogText,
                         reason: 'Clicked ACCEPT but the page did not change.', page_text: afterText };
      break;
    }
    if (!(await goBack())) break;
  }
  out.after_text = norm(document.body.innerText).slice(0, 1500);
  return out;
}
