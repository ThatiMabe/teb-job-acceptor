# Setup

Windows 10 or 11. These steps assume you own the accounts you are automating.

## 1. Get the code

```bash
git clone https://github.com/<your-username>/teb-job-acceptor.git
cd teb-job-acceptor
```

## 2. Your private settings (never committed)

1. Copy `sites.example.json` to `sites.json`. Set `skip_if_contains` (words that make a job be skipped) and, per site, `auto_accept`.
2. Create a file named `.env` in the project folder with your own keys:

```
OPENCODE_GO_API_KEY=...        # the AI that reads job text (DeepSeek through OpenCode Go)
TELEGRAM_BOT_TOKEN=...         # from Telegram's BotFather
TELEGRAM_HOME_CHANNEL=...      # your numeric Telegram id
TELEGRAM_EXTRA_CHAT_IDS=...    # optional: more people, comma separated
NOTION_API_KEY=...             # optional, for the activity database
NOTION_PARENT_PAGE_ID=...      # optional, the Notion page to create the database under
```

Each Telegram recipient must open your bot and press **Start** once, or Telegram will not deliver to them. To find someone's numeric id, ask them to message `@userinfobot`.

## 3. Install

Double-click **`setup.cmd`**. It installs Python and Chrome if missing, installs the packages, checks your keys, creates the Windows tasks (24/7 agent and the 18:00 report) and opens the agent's Chrome window.

## 4. Load the extension

In the agent's Chrome window open `chrome://extensions`, switch on **Developer mode**, click **Load unpacked** and choose the `extension` folder (or drag the folder onto the page).

## 5. Sign in

Sign in to each site **in that window**, on its own tab. Leave the window open. It can sit behind other windows; do not minimise it.

## 6. Keep the PC awake

A sleeping PC checks nothing. Run `keep-awake.cmd` (sets "never sleep when plugged in") and set:

- **Lid close:** Do nothing (Control Panel, Power Options).
- **Windows Update:** pause updates, and set active hours so it does not restart by itself.
- **Wi-Fi adapter:** untick "allow the computer to turn off this device to save power".
- Keep it plugged in. For power cuts, a UPS or the phone hotspot keeps the router alive.

## 7. Check it is working

- Double-click `status.cmd`: a Telegram message and a screenshot per site.
- The 18:00 report shows checks per site and how much of the day was covered.

## Tests

```bash
pip install -r requirements.txt
python tests/test_accept.py
python tests/test_accept_kandua.py
python tests/test_proof.py
```

They run replicas of the sites in a headless Chrome and never contact the real ones.

## After a restart

The agent starts at logon and reopens Chrome. Sign-ins that live in tab storage are lost when Chrome closes, so sign in again in the agent's window; you will get a Telegram message saying which site needs it.
