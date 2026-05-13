# Chub AI Character Downloader

A Windows tool for downloading public Chub.ai character cards. It supports single-character downloads, search downloads, creator downloads, tag downloads, previews, a saved Chrome login profile, and a live download dashboard.

> This only downloads public cards that Chub serves to your account. NSFL/NSFW results still depend on your Chub account settings.

## Features

- Single character, search, creator, and tag download modes.
- Preview search and tag results before downloading.
- Parallel downloads with a configurable batch size from 1 to 20.
- PNG card, JSON, or both. PNG files include embedded card JSON for SillyTavern/Risu/Agnai ect...
- `downloads_manifest.csv` tracks saved cards and helps skip cards that were already downloaded.
- Real Chrome session support, so you can log in once and reuse that profile.

## Install

Requirements:

- Windows 10 or 11
- Python 3.10+ 
- Google Chrome

Steps:

1. Download or unzip this folder.
2. Double-click `install.bat`.
3. Double-click `run_downloader.bat`.
4. First time using it, pick `Login / setup Chub profile`, sign in to Chub, enable the account content settings you want, then press Enter in the CMD window.

## Menu

```text
[1]  Download single character
[2]  Search and download
[3]  Creator download
[4]  Tag download
[5]  Preview search
[6]  Preview tag
[7]  Login / setup Chub profile
[8]  Exit
```

## Output

Downloaded files are saved under `character exports/`.

```text
character exports/
  CreatorName/
    main_some-character_spec_v2.png
    main_some-character_spec_v2.json
  Love + Human/
    main_x_spec_v2.png
```

`downloads_manifest.csv` records what has already been saved. If you want to rerun a category from scratch, delete `downloads_manifest.csv` first so the duplicate guard does not skip the old entries.

## Advanced CLI

```bash
py chub_downloader.py search "vampire" --pages 5 --sort latest --format both --concurrency 8
py chub_downloader.py tag "Love,Human" --pages 2 --match all --format png --concurrency 12
py chub_downloader.py creator "SomeCreator" --pages 3 --format both --concurrency 4
py chub_downloader.py single "https://chub.ai/characters/creator/slug" --format both
py chub_downloader.py preview "robot maid" --sort popularity
py chub_downloader.py preview-tag "Love,Human" --match all
py chub_downloader.py login
```

## Troubleshooting

- If Chrome is missing, install it from <https://www.google.com/chrome/> or set `CHROME_PATH` to the full path of `chrome.exe`.
- If NSFL/NSFW queries return nothing, run the login option again and check your Chub account content settings.
- If downloads fail, check `downloader.log`, then rerun the same mode. Saved cards are skipped automatically unless you delete `downloads_manifest.csv`.
- Forks are skipped by default. Use `--include-forks` from the CLI if you want them.

If you see any bugs, please let me know.
