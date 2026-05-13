# 🎴 Chub AI Character Downloader

A friendly, batteries-included Windows tool for snagging public Chub.ai character cards. Search, filter by tag, grab a creator's whole catalog, or chase event drops — all from a comfy CMD menu, with a live dashboard and parallel downloads.

> ⚠️ **Only public cards** — this tool fetches what Chub serves publicly. NSFL gating still applies to your account settings.

---

## ✨ Features

- 🖱️ **Click-driven menu** — pick a mode, fill in some prompts, that's it.
- ⚡ **Parallel batch downloads** — grab 1-20 cards at a time. Default 4.
- 📊 **Live dashboard** — main window shows elapsed seconds, files saved, cards/min, bandwidth, total bytes, and more — updating in place without flicker.
- 🪟 **Second console window** — auto-spawns alongside the dashboard to tail per-card activity (`[page 18 cards 5-8/20]`, `OK Saved ...`, etc.).
- 🔁 **Smart deduplication** — already-downloaded cards are skipped before any network call, so re-running a search is cheap.
- ♻️ **Infinite mode** — pass `-1` for pages to keep harvesting forever; it'll politely sleep between empty passes.
- 🏷️ **Search modes**: single character, free-text search, creator, tag(s).
- 💾 **PNG card / JSON / Both** — PNG embeds the card JSON inside a tEXt chunk (SillyTavern / Risu / Agnai compatible).
- 📓 **CSV manifest** — every download appended to `downloads_manifest.csv` for easy auditing.
- 🌐 **Real Chrome under the hood** — uses your actual logged-in session (saved profile), so NSFL works once you've toggled it on your account.

---

## 📦 Installation

### Prerequisites

- 🪟 **Windows 10 / 11**
- 🐍 **Python 3.10+** (with "Add python.exe to PATH" enabled at install time) — grab it at [python.org/downloads/windows](https://www.python.org/downloads/windows/)
- 🌍 **Google Chrome** — the installer can fetch it for you via `winget` if it's missing

### Steps

1. Download / clone this repo.
2. Double-click **`install.bat`**.
   - It checks Python, installs `playwright`, and offers to install Chrome if it's not found.
3. Done! Run **`run_downloader.bat`** to start.

---

## 🚀 Quick start

```text
================================================
   Chub AI Character Downloader
================================================

  [1]  Download single character
  [2]  Search and download
  [3]  Creator download
  [4]  Tag download
  [5]  Preview search
  [6]  Preview tag
  [7]  Login / setup Chub profile
  [8]  Exit
```

🥇 **First time?** Pick **[7] Login** first — Chrome opens to chub.ai, log in with Google (or whatever), enable NSFL/NSFW if you want, then press Enter back in the CMD. Your profile is saved locally and reused on every future run.

---

## 🎛️ Menu walkthrough

### [1] Download single character 🎯
Paste any chub.ai/characters/... URL or `creator/slug` path.

### [2] Search and download 🔎
Free-text search. First prompt is always **batch size** (1-20, default 4).

### [3] Creator download 👤
Grab every (public) card from one creator. Paste their profile URL or just their handle.

### [4] Tag download 🏷️
One tag, or comma-separated list (`"Love,Human"`). Pick **all tags** (AND) or **any tag** (OR) when prompted.

### [5] / [6] Previews 👀
Lists matching results without downloading anything — great for testing a query before committing.

### [7] Login 🔐
Opens real Chrome with the saved profile so you can sign in / toggle account content settings.

### [8] Exit 🚪

---

## 📊 The Live Dashboard

When you start a download (modes 2-4), **two windows** appear:

### Main window — Dashboard 🖥️
Updates twice a second, in place, no scrolling:

```
==============================================================================
  Chub Downloader - Live Dashboard           (Ctrl+C in this window to stop)
==============================================================================

  Mode     : Tag download                    Target  : Love,Human
  Page     : 18                              Batch   : 4 cards / batch
  Elapsed  : 3m 42s                          Seconds : 222
  Action   : Page 18 cards 5-8/20

  Files saved      : 287         Total bytes : 354.8 MB
  Cards attempted  : 152         Skipped (exists) : 14
  Failed           : 1           Skipped (dupes)  : 6

  Recent (60s) :   48.0 cards/min   0.80 cards/sec   1.9 MB/sec
  Overall      :   41.0 cards/min   1.6 MB/sec average

  Log file : C:\...\downloader.log
             (live mirror in the 2nd console window)
==============================================================================
```

### Second window — Per-card log 📝
Auto-spawned PowerShell window that tails `downloader.log`. Shows everything that used to scroll past:

```
[page 18 cards 5-8/20]
    OK Saved main_meimei-succubus-sucks-at-her-job_spec_v2.png
    OK Saved main_illyasviel-vo_spec_v2.png
    ...
```

Close it any time — the log file persists.

---

## ⚡ Batch size (concurrency)

After picking a download mode, you'll be asked:

```
    Batch size - cards downloaded at once [default 4, pick 1-20]:
```

- 🐢 `1` = slowest, gentlest on Chub
- 🐎 `4` = sane default
- 🚀 `8-12` = fast, fine for most connections
- 🏎️ `20` = full throttle (your wifi may complain)

Batches happen browser-side via `Promise.all` — one Python↔browser roundtrip per batch, not per card. Expected speedup vs sequential: roughly N×.

---

## 📂 Output folder structure

```
character exports/
  CreatorName/                        # by creator for single/search/creator/event modes
    main_some-character_spec_v2.png
    main_some-character_spec_v2.json
  Love + Human/                       # by tag combo for tag mode
    main_x_spec_v2.png
    main_y_spec_v2.png
```

- 📄 `*.json` = card JSON straight from Chub's card.json file
- 🖼️ `*.png` = the character card image with embedded JSON metadata
- 📋 `downloads_manifest.csv` records every save (name, path, source, timestamp)

---

## 💻 CLI usage (advanced)

Skip the menu and call Python directly:

```bash
py chub_downloader.py search "vampire" --pages 5 --sort latest --format both --concurrency 8
py chub_downloader.py tag "Love,Human" --pages 2 --match all --format png --concurrency 12
py chub_downloader.py creator "SomeCreator" --pages 3 --format both --concurrency 4
py chub_downloader.py single "https://chub.ai/characters/creator/slug" --format both
py chub_downloader.py preview "robot maid" --sort popularity
py chub_downloader.py login
```

Flags available on download subcommands: `--pages`, `--sort`, `--format`, `--topics`, `--match`, `--include-forks`, `--concurrency`, `--per-page`, `--branch`, `--overwrite`, `--visible`.

---

## 🔐 Login & NSFL

NSFL cards are **account-gated** on Chub — even with a valid login, results come back empty until you flip the content toggle in your Chub account settings.

1. Run menu option **[7]**.
2. Chrome opens. Log in.
3. Settings → enable the NSFL toggle.
4. Wait a moment for Chub to apply it (sometimes takes a few seconds).
5. Press Enter in the CMD to save the profile.

After that, all future runs use the saved session.

---

## 🐛 Troubleshooting

### "Could not find Chrome"
Set `CHROME_PATH` in your environment to the full path of `chrome.exe`, or install Chrome from [google.com/chrome](https://www.google.com/chrome/).

### "Chrome debug port already open"
Means the script is reusing an already-running debug Chrome. Totally fine — this is the intended path on repeat runs.

### NSFL queries return zero results
You're not signed in, or the account toggle isn't flipped. See **Login & NSFL** above.

### Dashboard text is duplicating / scrolling
This shouldn't happen anymore (the dashboard uses the Win32 console API directly), but if you're on a non-standard terminal that doesn't honor cursor positioning, the fallback is `cls`-based and may flicker briefly. Running from a normal `cmd.exe` (which is what `run_downloader.bat` opens) gives the smoothest result.

### Failed downloads
Check `downloader.log` for the per-card error. Transient failures are normal — just re-run the same mode and the already-saved cards will be skipped, only retries will fetch.

### "No characters found on page X"
Usually means you've reached the end of available results, or your filters are too narrow. The script handles this gracefully in infinite mode (waits and retries).

---

## 📝 Notes & limitations

- 🤝 This tool respects Chub's public API. It does not bypass anything that requires login — it reuses your real, logged-in browser session.
- 💤 Built-in jittered sleeps between requests / pages keep you off the angry-bot radar.
- 🗃️ The in-run "duplicate guard" prevents re-downloading anything already in `downloads_manifest.csv`.
- 🌳 Forks are skipped by default. Use CLI flag `--include-forks` to grab them.
- 🪪 The "Default public token" baked into the script is the same token Chub ships in their public JS bundle. The script auto-refreshes it from chub.ai on each run; the hardcoded value is only a fallback.

---

## 🙌 Have fun!

Bug reports / feature ideas welcome via Issues. Pull requests double-welcome. 🍻
