// Standard Bank: go back to the "New Jobs" list by clicking its menu entry or dashboard tile. Runs INSIDE the page.
// Read-only navigation: it clicks only an element whose whole text is "New Jobs" (optionally with a count), never a
// button and never anything on a job card. Used when the tab has ended up on another list (Unassigned, In progress...).
// Self-contained on purpose: executeScript serialises this function into the page.
async function openNewJobs() {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const find = () => [...document.querySelectorAll('a, button, [role="button"], li, div, span, p')]
    .filter(e => vis(e) && /^(\d+\s*)?new\s*jobs(\s*\d+)?$/i.test(norm(e.innerText)) && norm(e.innerText).length < 30
                 && !e.closest('.job-card, [class*="job-card"]'))
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
