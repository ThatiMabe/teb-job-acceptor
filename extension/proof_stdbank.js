// Standard Bank "proof" helper. Runs INSIDE the page (background.js injects it).
// Opens the "Jobs in progress" list by clicking its tile or menu entry. Read-only navigation: it clicks nothing
// else, and never a button. Used to photograph where an accepted job ends up.
// Self-contained on purpose: executeScript serialises this function into the page.
async function openJobsInProgress() {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  // elements whose whole text is "Jobs in progress" (optionally with a count in front, as on the dashboard tile)
  const find = () => [...document.querySelectorAll('a, button, [role="button"], li, div, span, p')]
    .filter(e => vis(e) && /^\d*\s*jobs in progress$/i.test(norm(e.innerText)) && norm(e.innerText).length < 40)
    .sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return ra.width * ra.height - rb.width * rb.height;
    });
  let hits = find();
  if (!hits.length) {  // the left menu may be collapsed: open it, then look again
    const burger = [...document.querySelectorAll('button, [role="button"], a')]
      .find(e => vis(e) && (/menu|burger|nav/i.test(e.getAttribute('aria-label') || '') ||
                            /menu|burger|nav/i.test(e.className || '')));
    if (burger) { burger.click(); await wait(1000); hits = find(); }
  }
  if (!hits.length) return { clicked: false };
  hits[0].click();
  await wait(1500);
  return { clicked: true, text: norm(hits[0].innerText) };
}
