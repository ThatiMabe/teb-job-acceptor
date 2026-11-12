# Architecture

## Pieces

| Piece | Where | Job |
|---|---|---|
| Chrome extension | `extension/` | Every minute, for each site: reload its tab, wait for the page, read it, screenshot it, run the accept routine if a job is there, report to the agent. |
| Local agent | `agent_server.py` | A small HTTP server on `127.0.0.1:8765`. Classifies each page, sends alerts, writes records, watches over the browser. |
| Shared helpers | `watcher.py` | Settings, Telegram sending (to several people), the DeepSeek call, logging. |
| Notion log | `notion_log.py` | Writes one row per event to a shared "agent activity" database. |
| Daily report | `daily_report.py` | 18:00 summary with check counts and coverage; `--now` gives an on-demand status. |
| Windows tasks | `install_task.ps1` | "Work Acceptor" runs at logon with a 5-minute watchdog; a second task runs the 18:00 report. |

## One check, step by step

1. The extension's alarm fires. It asks the agent for the settings (`GET /config`).
2. For each site it finds the site's tab (each site owns one tab, remembered by id) and reloads it, or moves it back to the right address.
3. It waits, up to 40 seconds, until a known page marker is on screen and the text has stopped changing (these sites are single-page apps that draw after loading).
4. For the supplier portal, if it is on the wrong list, it clicks back to "New Jobs" and reads again.
5. It reads the page text and takes a screenshot (`POST /report`).
6. If the page shows a job and accepting is on, it runs the site's accept routine inside the page and reports the result with before and after screenshots.
7. The agent classifies the report, sends the right Telegram message, writes Notion and CSV rows, and updates the day's counters.

## Page classes

`no_jobs`, `jobs_found`, `login_needed`, `loading`, `wrong_page`, `unknown`, `error`.

Only `jobs_found` can lead to an alert or an accept. `wrong_page` (signed in, but not on the New Jobs list) is silent and nothing is read from it. `loading` is silent too. These two classes exist because of real false alarms; see the lessons.

## The agent's endpoints (all local)

| Endpoint | Purpose |
|---|---|
| `GET /config` | Settings for the extension: sites, markers, skip words, which pictures to take. |
| `POST /report` | One check's result: page text, screenshot, accept result. |
| `POST /recon` | Extra pictures (job pages, pop-ups) that the extension forwards. |
| `GET /proof?site=...` | Ask for reference pictures on the next check. |
| `GET /status` | Current state for debugging. |

## Where AI is used

Only to **read**: DeepSeek V4.1 Flash (through OpenCode Go) turns page text into a list of jobs for the alert message. It never decides to click anything. Every accept, skip or stop is fixed code, which keeps it fast, free per check, and testable.

## Accept routines (inside the page)

Each is a self-contained function injected into the page:

- `accept_stdbank.js`: supplier portal. Finds a card headed "Job Id", applies the skip rule, clicks the button that reads exactly **ACCEPT**, handles a plain Yes/Confirm pop-up, verifies the job left the list.
- `accept_kandua.js`: marketplace. Opens the job tile, reads the job page, applies the skip rule, clicks **Accept Job**, then the pop-up's **"Yes, I can do this job"**. Takes pictures of the job page, scrolling top to bottom, for the first few jobs.
- `nav_stdbank.js`, `proof_stdbank.js`: read-only navigation helpers.

All routines share the same shape: act only on the right page, once per job, and **stop and ask** on anything unfamiliar.

## Notion activity database

One row per event, shared by all agents in the business: Name, When, Agent, Company, Event (New job, Accepted, Signed out, Checks stopped, Daily report ...), Site, Status, Job, Job ID, Location, Appointment, AI verdict, Checks, Jobs seen, Details, Link.

## Always-on behaviour

The agent reopens Chrome if it is closed, warns by Telegram if no checks arrive for 5 minutes, and the 18:00 report shows how much of the day was actually covered, so a quiet day can be told apart from a broken agent.
