# Safety and limits

## What it can click

| Site | Allowed clicks | Never |
|---|---|---|
| Marketplace | The job tile (only tiles saying "Available"), **Accept Job**, one pop-up button reading "Yes, I can do this job" (or a plain Yes/Confirm) | Go Back, Call Customer, Create & Send Quote, anything about messages |
| Supplier portal | A button reading exactly **ACCEPT** on the New Jobs page, and a plain Yes/Confirm pop-up | Decline, Assign, View More, anything else |
| Navigation helpers | A menu entry or tile whose whole text is "New Jobs" (read-only navigation) | Anything on a job card |

## Guards (each has an automated test)

- **Right page only.** Acts only on the real New Jobs page; on any other page it does nothing and reads nothing.
- **Once per job.** Job ids are remembered for 7 days; a job is never clicked twice.
- **Owner's rule.** A job mentioning a configured word (for example one appliance type) is not accepted. The owner gets a "skipped" message.
- **No money.** If a price, credit, "buy" or "unlock" sits next to the Accept button, or in a confirmation pop-up, it stops. The check looks at the button's immediate surroundings only, so a job description that says "good price" does not trigger it.
- **No messaging.** A pop-up about messages, quotes, calls or invoices is never confirmed.
- **Stop and ask.** An unrecognised pop-up, a missing button, a pop-up asking for input, or a page that does not change after a click all end with a message to the owner and screenshots. It never retries in a loop.
- **Evidence.** Every accept sends before and after screenshots and quotes the platform's own confirmation text.

## Privacy

- No customer data in the repository, in Telegram, or in Notion: client names, phone numbers and emails are not extracted.
- No credentials in the repository. Keys live in a local `.env` that is ignored by git. The agent never stores or sends a site password; the owner signs in by hand in the browser.
- Screenshots and logs stay on the PC (`captures/`, `logs/` are git-ignored).
- The agent talks only to: the local machine, Telegram, Notion, and the AI provider (which receives page text for reading jobs).

## Limits you should know

- **It cannot work while the PC sleeps or has no internet.** It checks once a minute, so a job taken by someone else within that minute is gone before the next check.
- **Sign-ins are tied to the open window.** After a restart, sign in again (the agent tells you).
- **Screenshots need a drawn window.** Accepting does not.
- **One login per browser profile.** A second account on the same site needs a separate agent.
- **Sites change.** Page markers and buttons are matched by text and address; a redesign can stop an accept, which then fails safe (stops and asks) rather than clicking the wrong thing.
- **Terms of use.** Automating a platform you do not own may conflict with its terms. Check them and keep a human responsible for what is accepted.
