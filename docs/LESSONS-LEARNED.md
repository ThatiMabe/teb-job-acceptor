# Lessons learned

Every item below is a real incident from building and running the agent. Format: what we saw, the cause, the fix, and the lesson.

## 1. The sign-in that never finished (bot protection)

- **Saw:** Signing in to the supplier portal from an automated browser showed a "something went wrong" pop-up, then an endless loading spinner. The same sign-in worked in a normal Chrome window.
- **Cause:** The portal is behind bot protection (cookies and behaviour gave it away). Both a launched and an attached automated browser were detected, even in the owner's real Chrome profile.
- **Fix:** Stop automating the browser from outside. A **Chrome extension** runs inside a normal Chrome window and talks to a local agent. To the site it is just the owner using Chrome.
- **Lesson:** If a site fights automation, change the architecture, not the tricks.

## 2. Signed out whenever the window closed

- **Saw:** After closing the window, the portal asked for a sign-in again, even though "remember me" was ticked.
- **Cause:** The portal keeps its sign-in in per-tab session storage, which is deleted when the tab closes. Reloading the tab keeps it.
- **Fix:** Keep the agent's window open for good, only ever reload tabs, and alert the owner when a sign-out happens.
- **Lesson:** Find out where a session lives before designing around it.

## 3. One browser profile, one login

- **Saw:** Two tabs for two accounts on the same site both showed the first account.
- **Cause:** The site's sign-in is shared by the whole browser profile.
- **Fix:** Keep one account per agent. A second account becomes a separate agent with its own profile.
- **Lesson:** Isolation boundaries are set by the platform, not by tabs.

## 4. "New job!" with no job

- **Saw:** An instant new-job alert, but the page showed no jobs.
- **Cause:** The page was read while it still said "Loading", and "page loaded but not marked empty" was treated as "has jobs".
- **Fix:** Wait for a page-specific marker; treat a page still loading as silent; if the AI then finds no job, send a **correction** message.
- **Lesson:** Single-page apps lie while loading. "Not empty" is not "full".

## 5. Other lists mistaken for new jobs

- **Saw:** An alert saying accepting "did not run" on a page that held already-accepted jobs; later, the marketplace's "My Jobs" page opened as if it were new work.
- **Cause:** Both sites show the same menu on every page ("Home, My Jobs, New Jobs ..."), and the detection looked for the text "New Jobs". It matched everywhere.
- **Fix:** Page-specific detection: a subtitle unique to the New Jobs page for one site, the page address for the other. Any other page is "wrong page": silent, nothing read or recorded. The extension clicks back to the right page, and every accept routine refuses to act anywhere else.
- **Lesson:** Never use navigation text as a page marker. Make "act only on the right page" a property of the action itself, not just of the detection.

## 6. The PC that went to sleep

- **Saw:** A real job arrived at 06:19 and was missed. One whole Sunday recorded 11 checks.
- **Cause:** The laptop slept after 5 minutes of inactivity (3 on battery). A sleeping PC checks nothing, and cannot report that it is asleep.
- **Fix:** Never-sleep settings (`keep-awake.cmd`), lid and update settings, and a **daily coverage figure** in the 18:00 report so gaps are visible. Longer term: an always-on machine.
- **Lesson:** An always-on system needs an always-on host. Build the proof that it was running into the report.

## 7. Accepting needs no window, screenshots do

- **Saw:** A job was accepted while the window was minimised, but its screenshots were missing.
- **Cause:** Clicks happen inside the page and need no visible window; a screenshot can only photograph a drawn window.
- **Fix:** For job pictures only, restore a minimised window without taking keyboard focus. Routine per-minute pictures never pop the window up. If a picture is impossible, the message says so.
- **Lesson:** Know which capabilities depend on the display.

## 8. The pop-up the agent could not see

- **Saw:** "Clicked Accept but the page did not change", with a pop-up still open on screen.
- **Cause:** The routine looked for standard dialog markup. The marketplace's pop-up is plain markup.
- **Fix:** Detect a pop-up as any set of buttons that were not on the page before the click, and add the exact "Yes, I can do this job" button. The failure was **reproduced first** in a mock page, then fixed; with the old detection switched off the same test fails with the same message.
- **Lesson:** Reproduce a bug in a test before fixing it, so you know the fix is the fix.

## 9. The tab that vanished

- **Saw:** Repeated "No tab with id" errors on one site.
- **Cause:** Chrome's memory saver swapped the background tab for a new one with a new id.
- **Fix:** Follow replaced tabs, mark the agent's tabs non-discardable, and retry once on this error.

## 10. The extension that failed to start once

- **Saw:** "importScripts ... failed to load" in the extension's error list.
- **Cause:** A helper file could not be read at start-up (most likely a security scanner locking a freshly edited file).
- **Fix:** Retry each helper, keep running if one is missing, disable only what depends on it, and tell the owner.
- **Lesson:** Make start-up tolerant; a flaky file read should not stop a 24/7 system.

## 11. The invisible typo

- **Saw:** After a change, every accept test failed at once.
- **Cause:** A shell quoting slip wrote backspace characters into two patterns instead of word boundaries. Nothing matched.
- **Fix:** Fixed the characters, then added a scan for stray control characters. Patches now build backslashes from their character code.
- **Lesson:** This is why the mock-page tests exist. They caught it in seconds, before any real job was at risk.

## 12. Who accepted the job?

- **Saw:** A job showed as accepted, the owner could not find it, and an admin said they had accepted it.
- **Cause:** The owner looked at the "before" screenshot; accepted jobs also move to another list. Several people were using the same account.
- **Fix:** Every accept message now quotes the platform's own confirmation text and carries before and after pictures; the message says the job has left the New Jobs list and where to look.
- **Lesson:** Automation that acts on someone's behalf must produce evidence, not just claims.

## 13. Hard-coded folder name

- **Saw (while preparing this repository):** The agent found its own Chrome window by searching for the text of the original folder name.
- **Cause:** An early shortcut. Anyone cloning the repository into another folder would have got a new browser window every minute.
- **Fix:** Match on the real profile path. A reminder to review a project as a stranger would before publishing it.

---

## Timeline (November 2026)

| Day | Milestone |
|---|---|
| 3 to 4 | Idea, plan and first watcher. Browser-robot approach tried and abandoned. |
| 4 | Extension plus local agent; Telegram alerts; Notion activity database; daily report. |
| 5 | First real jobs seen; a job missed overnight (PC asleep). |
| 7 | Accept routines built from the owner's screenshots; skip rule; first-jobs picture mode; second Telegram recipient supported. |
| 9 | First two live accepts on the supplier portal. False alarm from another list, fixed by page-specific detection. |
| 10 | Marketplace pop-up fixed from the owner's screenshots; safeguards added; repository published. |
