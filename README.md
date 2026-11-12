# TEB Job Acceptor

**A 24/7 agent that watches two job platforms, accepts work by simple rules, and tells the business owner on Telegram, with before and after screenshots as proof.**

This repository is a living case study: the code, the decisions behind it, and an honest record of what went wrong while building it. It was built for TEB Construction, a small repairs business that receives job offers on two online platforms, over about one week in November 2026.

> Status: running on the owner's PC. Automatic accepting has been **proven on a live job on one platform** (2 accepted, confirmed by the platform's own message). On the second platform the first live attempt stopped safely on a pop-up the agent had never seen; that was fixed from the owner's screenshots and is **awaiting its next live job**. See [Results](#results-so-far).

---

## The problem

Job offers arrive at any hour and are usually given to whoever accepts first. The owner cannot watch two websites all day and night, and each missed offer is lost revenue. The goal:

1. Check both platforms **every minute, 24/7**.
2. **Accept** new jobs automatically, by the owner's rules.
3. **Tell the owner immediately** (and a second person, an admin) on Telegram, with proof.
4. **Keep a record** in Notion, and send a daily report at 18:00 that also proves the agent was actually running.

## What it does

- Reloads each platform's "new jobs" page every 60 seconds, exactly as a person would press refresh.
- Recognises the real "new jobs" page (and ignores every other page, such as "unassigned" or "my jobs").
- Opens a new job, applies the owner's rule (skip any job mentioning a banned word), clicks **Accept**, and confirms the platform's pop-up.
- Sends Telegram messages: new job, accepted (with before/after screenshots), skipped by rule, needs-the-owner, signed out, checks stopped.
- Logs every job to a Notion database and a CSV, and sends a daily 18:00 report with check counts and coverage.

## How it works

```
 Normal Chrome window (not automated)          one tab per platform, signed in by the owner
   |  Chrome extension (Manifest V3)           every minute: reload the tab, wait for the page,
   |                                           read it, screenshot it, (accept if rules say so)
   v
 Local agent (Python, 127.0.0.1:8765)          classifies the page, decides what to tell whom
   |-- Telegram  ......................... alerts + screenshots to the owner and the admin
   |-- Notion  ........................... one row per event, shared by all TEB agents
   |-- DeepSeek (via OpenCode Go)  ....... reads job text only; makes no decisions
   '-- Windows scheduled tasks  .......... run 24/7, restart on failure, 18:00 report
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full picture.

## Key decisions

| Decision | Why |
|---|---|
| **Code decides, AI only reads.** DeepSeek V4.1 Flash lists the jobs on a page; every accept/skip decision is fixed code. | Accepting jobs is an action with consequences. Fixed rules are fast (seconds matter), free to run every minute, and testable. |
| **A Chrome extension in a normal browser, not a browser robot.** | One platform sits behind bot protection that refused to let an automated browser sign in. A normal Chrome with an extension looks like a person. See [lessons](docs/LESSONS-LEARNED.md). |
| **Page-specific markers, never menu text.** | Both sites show the same menu on every page. Using it caused false "new job" alerts. |
| **Stop and ask when unsure.** | Any unfamiliar pop-up, missing button, or price near a button makes the agent stop and message the owner with screenshots. |
| **Every accept carries proof.** | When someone disputes who accepted a job, the before/after screenshots and the platform's own confirmation text settle it. |
| **Test on mock pages first.** | 34 automated tests drive replicas of both sites in a headless browser. The real sites are never touched by tests. |

## Safety rules (built in and tested)

- The only clicks it can make on the marketplace: the job tile, **Accept Job**, and the single "Yes, I can do this job" button. On the supplier portal: only **ACCEPT**. Never Decline, Call, Quote, Message or Assign.
- It **never clicks anything that looks like it costs money** (a price, credits, "buy", "unlock").
- It **never confirms a pop-up about messaging, quotes, calls or invoices**.
- It acts **only on the real "new jobs" page** and never twice on the same job.
- It never sends anything to a customer. Client names, phones and emails are kept out of Telegram and Notion.

Details and limits: [docs/SAFETY-AND-LIMITS.md](docs/SAFETY-AND-LIMITS.md).

## Results so far

| Date | What happened |
|---|---|
| Early Nov | First version watches both sites and alerts. Several false alarms and missed jobs, each traced to a root cause (below). |
| 9 Nov | **First two live accepts** on the supplier portal, confirmed by the portal's own message ("Job Id-... has been accepted"). |
| 9 Nov | A false alarm: the agent misread a different list as new jobs. Fixed the same day with page-specific detection. |
| 10 Nov | Marketplace accept stopped safely on an unseen pop-up. Reproduced in a test, fixed, and the real flow added from the owner's screenshots. |

Honest numbers: 34 automated tests pass; the laptop it ran on went to sleep often enough to miss jobs (one day recorded only 11 checks), which is why "keep it awake" is a first-class part of the setup.

## What went wrong, and what I changed

The most useful part of this project. Each row is a real incident, traced to a cause and fixed. Full write-ups with symptoms and fixes: [docs/LESSONS-LEARNED.md](docs/LESSONS-LEARNED.md).

| Incident | Root cause | Fix |
|---|---|---|
| Sign-in loops forever under automation | Bot protection detects automated browsers | Extension in a normal Chrome |
| Sign-in lost when the window closed | Session lives in per-tab storage | Keep the window open; never close tabs |
| "New job" alert with no job | Page read before it finished loading | Wait for a page-specific marker; send a correction if the AI finds nothing |
| Other lists treated as new jobs | Same menu text on every page | Page-specific markers; click back to the right page; refuse to act elsewhere |
| Jobs missed overnight | The PC went to sleep | Never-sleep settings; coverage shown in the daily report |
| Accept click "did nothing" | The pop-up was plain markup, not a standard dialog | Detect any newly appeared buttons; reproduce the bug in a test first |
| Repeated "No tab with id" | Chrome's memory saver swapped the tab | Follow replaced tabs; mark tabs non-discardable |
| Extension failed to start once | A helper file failed to load | Retry; keep running; alert the owner |
| Tests failed right after a change | A shell quoting slip wrote invisible characters into patterns | Tests caught it in seconds; added a scan for control characters |

## Run it yourself

See [docs/SETUP.md](docs/SETUP.md). In short: copy `sites.example.json` to `sites.json`, put your keys in `.env`, run `setup.cmd`, load the `extension` folder in Chrome, sign in to each site in that window, and set the PC never to sleep.

Run the tests (no real sites involved):

```bash
pip install -r requirements.txt
python tests/test_accept.py
python tests/test_accept_kandua.py
python tests/test_proof.py
```

## Repository layout

```
extension/        the Chrome extension: checks, accept routines, navigation helpers
agent_server.py   the local agent: classification, alerts, Notion, watchdog
watcher.py        shared helpers: settings, Telegram, the DeepSeek call, logging
notion_log.py     the Notion activity database
daily_report.py   the 18:00 report, on-demand status
tests/            mock-page tests for every accept flow and safeguard
docs/             architecture, lessons learned, setup, safety
sites.example.json   the settings template (copy to sites.json)
setup.ps1 / install_task.ps1 / keep-awake.cmd / status.cmd   Windows helpers
AGENTS.md         handover notes for any AI coding assistant or developer
```

## Roadmap

- Prove the marketplace accept on its next live job.
- Automatic sign-in after a sign-out (using the browser's own saved passwords; the agent never stores a password).
- A separate agent for a second account on the same platform (one browser profile can hold only one login).
- Move from a laptop to an always-on machine.

## A note on responsibility

This automates the owner's **own accounts** on platforms they are legitimately signed in to. Automating a third-party site can conflict with its terms of use: check them before you run anything like this, and keep a human accountable for what is accepted. This repository contains no credentials, no customer data, and no recordings.
