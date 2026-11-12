// Standard Bank "accept the job" routine. It runs INSIDE the page (background.js injects it).
//
// Built from the owner's screenshots of the New Jobs page: each job is a card headed "Job Id-<number>"
// with two buttons, DECLINE and ACCEPT. What happens AFTER ACCEPT has not been seen yet, so:
//   - it clicks ONLY a button whose text is exactly "ACCEPT" (never DECLINE, never anything else);
//   - at most once per job id (alreadyTried comes from the extension's memory);
//   - if a pop-up appears that has no inputs and exactly one plain affirmative button
//     (Yes / Confirm / OK / Continue ...), it clicks that;
//   - anything else (checkboxes, date pickers, text boxes, no change on the page): STOP and report
//     needs_user, so the owner is told and the job stays for a human. It never loops or retries.
// Self-contained on purpose: executeScript serialises this function into the page.
async function acceptStdBank(alreadyTried, skipWords) {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const label = el => (el.innerText || el.textContent || el.value || '').replace(/\s+/g, ' ').trim().toUpperCase();
  const clickables = root =>
    [...root.querySelectorAll('button, [role="button"], a, input[type="button"], input[type="submit"]')].filter(vis);
  const ID_RE = /Job\s*Id\s*-?\s*(\d{6,})/gi;
  const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return (h >>> 0).toString(16); };

  // Every ACCEPT button on the page, with the job card it belongs to.
  const findAccepts = () => {
    const found = [];
    for (const b of clickables(document)) {
      if (label(b) !== 'ACCEPT') continue;
      let card = b.parentElement, jobId = null, cardText = '', ambiguous = false;
      while (card && card !== document.body.parentElement) {
        const t = card.innerText || '';
        const ids = [...new Set([...t.matchAll(ID_RE)].map(m => m[1]))];
        if (ids.length === 1) { jobId = ids[0]; cardText = t; break; }
        if (ids.length > 1) { ambiguous = true; break; }  // climbed past this card into the list
        card = card.parentElement;
      }
      if (ambiguous) { found.push({ btn: b, jobId: null, cardText: '', ambiguous: true }); continue; }
      if (!jobId) { cardText = (b.parentElement && b.parentElement.innerText) || ''; jobId = 'card-' + hash(cardText); }
      found.push({ btn: b, jobId, cardText, ambiguous: false });
    }
    return found;
  };

  const out = { accepted: [], attempted: [], skipped: [], skipped_jobs: [], needs_user: null, after_text: '' };
  // Only ever act on the "New Jobs" list. Its subtitle is unique to it; "Unassigned Jobs" and "Jobs in progress"
  // lists have other subtitles and other buttons (ASSIGN...), and must never be touched.
  if (!/Jobs has been assigned but not yet accepted/i.test(document.body.innerText || '')) {
    out.not_new_jobs_page = true;
    return out;
  }
  const tried = new Set(alreadyTried || []);
  const YES = ['YES', 'CONFIRM', 'OK', 'OKAY', 'ACCEPT', 'SUBMIT', 'CONTINUE', 'PROCEED', 'DONE',
               'YES, ACCEPT', 'YES ACCEPT', 'CONFIRM ACCEPT', 'ACCEPT JOB'];
  const DIALOG = '[role="dialog"], [role="alertdialog"], .modal.show, .modal[style*="display: block"], ' +
    '.mat-mdc-dialog-container, .mat-dialog-container, .cdk-overlay-pane, .swal2-popup, .p-dialog, .ant-modal';

  for (let round = 0; round < 6; round++) {
    const next = findAccepts().find(a => a.ambiguous || !tried.has(a.jobId));
    if (!next) break;
    if (next.ambiguous) {
      out.needs_user = { job_id: null, reason: 'Could not tell which job card an ACCEPT button belongs to.' };
      break;
    }
    const { btn, jobId, cardText } = next;
    tried.add(jobId);
    out.attempted.push(jobId);
    // Owner's rule: jobs mentioning a skip word (for example "microwave") are NOT accepted.
    const hit = (skipWords || []).find(w => cardText.toLowerCase().includes(String(w).toLowerCase()));
    if (hit) {
      out.skipped_jobs.push({ job_id: jobId, card_text: cardText.slice(0, 1500), reason: 'mentions "' + hit + '"' });
      continue;
    }
    btn.click();

    // Wait for a confirmation pop-up (up to 8 s)
    let dlg = null;
    for (let i = 0; i < 16 && !dlg; i++) {
      await wait(500);
      dlg = [...document.querySelectorAll(DIALOG)].find(d => vis(d) && label(d).length > 0) || null;
      if (!dlg && !findAccepts().some(a => a.jobId === jobId)) break;  // card already gone, no pop-up
    }
    let dialogText = null;
    if (dlg) {
      dialogText = (dlg.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 600);
      const inputs = [...dlg.querySelectorAll('input:not([type="hidden"]):not([type="button"]):not([type="submit"]), select, textarea')].filter(vis);
      const buttons = clickables(dlg);
      const yes = buttons.filter(b => YES.includes(label(b)));
      if (inputs.length === 0 && yes.length === 1) {
        yes[0].click();
        await wait(1500);
      } else {
        out.needs_user = {
          job_id: jobId, card_text: cardText.slice(0, 1500),
          reason: inputs.length ? 'A pop-up after ACCEPT asks for input (tick box, date or text).'
                                : 'A pop-up after ACCEPT has buttons I do not recognise.',
          dialog_text: dialogText, buttons: buttons.map(label)
        };
        break;
      }
    }

    // Did the job leave the New Jobs list? (up to 8 s)
    let gone = false;
    for (let i = 0; i < 16 && !gone; i++) {
      await wait(500);
      gone = !findAccepts().some(a => a.jobId === jobId);
    }
    if (gone) {
      out.accepted.push({ job_id: jobId, card_text: cardText.slice(0, 1500), dialog_text: dialogText });
    } else {
      out.needs_user = {
        job_id: jobId, card_text: cardText.slice(0, 1500), dialog_text: dialogText,
        reason: 'Clicked ACCEPT but the job is still on the New Jobs list.'
      };
      break;
    }
  }
  out.skipped = findAccepts().filter(a => !a.ambiguous && tried.has(a.jobId) && !out.attempted.includes(a.jobId)).map(a => a.jobId);
  out.after_text = (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 1500);
  return out;
}
