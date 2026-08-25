# Google Maps Club Scraper

Scrapes club listings from Google Maps across US states, enriches them with
contact details from each club's own website, and upserts the results into a
Google Sheet.

## For teammates: getting started

Download the app and open it. Nothing to install first — no Python, no Git.

1. Open the [Releases page](https://github.com/rakshithvikramraj/gmap-scraper/releases)
   and download the file for your machine. No GitHub account needed:

   | Your machine | File |
   |---|---|
   | Mac, 2021 or newer | `club-scraper-macos-arm64.zip` |
   | Mac, 2020 or older | `club-scraper-macos-intel.zip` |
   | Windows | `club-scraper-windows-x64.zip` |

   Not sure which Mac you have? Apple menu → About This Mac. A chip named
   "Apple M1" or later is the first row; "Intel" is the second.

2. Unzip it and open **Club Scraper**.

3. **The first open is blocked. This is expected, and it happens once.** The
   app is not signed with a paid Apple or Microsoft certificate, so both
   systems hold anything they have not seen before:

   - **macOS** — open it, let it be blocked, then go to **System Settings →
     Privacy & Security**, scroll down, and click **Open Anyway** next to the
     message about Club Scraper. Open the app again and it starts.
   - **Windows** — click **More info**, then **Run anyway**.

   Every open after that is an ordinary double-click.

Results are saved to a **Club Scraper** folder inside your Documents.

The download is around 320MB because the app carries its own browser. That is
what lets it run with nothing installed beforehand.

### Running from source instead

Useful for development, and as a fallback if a download is blocked. It needs
nothing installed either — the setup script fetches its own Python.

**macOS**

```bash
git clone https://github.com/rakshithvikramraj/gmap-scraper.git
cd gmap-scraper
./setup.command      # once, takes a few minutes
./run.command        # opens the app
```

**Windows**

Install Git first from <https://git-scm.com/download/win> — Windows does not
ship with it. Then, in Command Prompt or PowerShell:

```
git clone https://github.com/rakshithvikramraj/gmap-scraper.git
cd gmap-scraper
.\setup.bat
.\run.bat
```

The `.\` prefix is required in PowerShell and harmless in Command Prompt, so
the block above works pasted into either.

Setup downloads roughly 180MB once: Python, the libraries, and the browser the
scraper drives. After that both scripts start instantly. Run from source and
results land in `data/` next to the code, not in Documents.

To get later fixes, run `git pull` and then `./setup.command` (or `.\setup.bat`)
again.

**If you were sent a zip of the source instead of a repo link**

macOS quarantines everything unpacked from a downloaded archive, so
`./setup.command` refuses to run. Clear it once, from inside the project
folder:

```bash
xattr -dr com.apple.quarantine .
```

### Using it

Add your search terms and press **Start scrape**. There is no state picker:
every run covers all 50 states. A full run takes a few hours, so it paces
itself and saves as it goes — you can close the window and press Start again
later to carry on from where it stopped.

For a quick trial run, tick **Stop after N clubs per state** — that caps each
state at a handful of listings and finishes in minutes.

Results are written to `results.csv` — in the **Club Scraper** folder in
your Documents if you downloaded the app, or in `data/` if you are running
from source. It opens in Excel or imports straight into Google Sheets.

### If something looks wrong

The panel at the end of a run shows how complete each column is. If a column
that is normally full drops to near zero, Google has changed its page layout
and the app needs updating — tell whoever maintains this.

## If your company blocks uv

The setup scripts above are the supported path. Only if `uv`'s installer is
blocked on your network, set the environment up by hand instead:

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium
```

## Google Sheets access

The script authenticates as *you* through Application Default Credentials. No
key file is ever downloaded into the project, and because you already own the
sheet there is no sharing step.

1. Install the gcloud CLI:

   ```bash
   brew install --cask google-cloud-sdk
   ```

2. Create a project at <https://console.cloud.google.com/projectcreate>, then
   log in and select it:

   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

3. Enable both APIs. Enabling only Sheets is the most common mistake, because
   opening a sheet by URL goes through Drive:

   ```bash
   gcloud services enable sheets.googleapis.com drive.googleapis.com
   ```

4. Authorize the scopes the script needs. **The scopes must be granted at
   login time** - user credentials cannot be re-scoped afterwards, so omitting
   them here produces a permission error at write time, not at login:

   ```bash
   gcloud auth application-default login \
     --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/drive.readonly
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```

   A browser opens once; approve with the account that owns the sheet. The
   token is stored under `~/.config/gcloud/`, outside this project. The
   `set-quota-project` line is optional - if it errors, skip it.

5. Verify:

   ```bash
   python scrape.py --check-auth
   ```

If the credentials are later revoked or expire, re-run step 4.

## Configure

Edit the `CONFIG` block at the top of `scrape.py`:

- `SEARCH_TERMS` - the phrases to search. Each one is run against every state.
- `STATES` - defaults to all 50. Narrow it for a faster run.
- `SHEET_URL`, `WORKSHEET` - where results land.
- `ENRICH_SITES` - set `False` to skip website enrichment.

## Run

```bash
python scrape.py                      # everything
python scrape.py --states Texas --limit 5   # smoke test
python scrape.py --no-enrich          # Maps only
python scrape.py --sheets-only        # push the existing cache
python scrape.py --headed             # visible browser, to solve a CAPTCHA
python scrape.py --force              # re-scrape cached queries
```

Progress is written to `data/cache.jsonl` as it happens, so an interrupted run
resumes where it stopped. `data/results.csv` is always written as a backup.

## Reading the output

Each run ends with a fill-rate table. If a column that is normally populated
drops to near 0%, Google has changed its markup and the matching entry in the
`SELECTORS` block in `scrape.py` needs updating. That is the expected
maintenance point.

`owner_name` and `owner_phone` are populated only when a club publishes a name
next to a phone number under an ownership title. Expect 20-35% coverage; the
rest of the rows rely on `phone`, `emails` and `other_phones`.

A per-state count of 118 or more means that state hit Google's roughly
120-result cap and is undersampled. Re-run it with narrower searches, for
example `--terms "padel club Dallas","padel club Houston" --states Texas`.

## Building the packages

GitHub Actions builds all three downloads. Push a tag and it cuts a release:

```bash
git tag v1.1.0
git push origin v1.1.0
```

The workflow runs the tests first, then builds on `macos-14` (Apple Silicon),
`macos-13` (Intel) and `windows-latest`, and attaches the three zips to a new
release. To try a build without releasing anything, run it by hand from the
Actions tab — the zips come out as workflow artifacts instead.

Actions minutes are free on this repo's standard runners, so a release costs
nothing but time — roughly 10-15 minutes per platform, most of it downloading
Chromium. The three platforms build in parallel.

To build locally on your own machine:

```bash
uv sync
PLAYWRIGHT_BROWSERS_PATH="$PWD/build/pw-browsers" uv run playwright install chromium
uv run pyinstaller club-scraper.spec --noconfirm
uv run python package.py --browsers build/pw-browsers --name club-scraper-local
```

`package.py` is a required step, not a convenience. PyInstaller cannot collect
the browsers itself — it rewrites the signature of every Mach-O file it copies
and fails on Chromium's signed nested `.app` — so `package.py` adds them
afterwards, re-signs the macOS bundle, and archives it with `ditto` (plain
`zip` flattens the symlinks inside Chromium's framework and the browser will
not start). It fails loudly rather than shipping an app that cannot scrape.

You can only build for the machine you are on; there is no cross-compiling.

## Tests

```bash
pytest -v
```

Tests cover every parsing function and never touch the network or a browser.

## Caveats

Scraping google.com/maps is against Google's Terms of Service. The script
paces itself conservatively and makes no attempt to defeat anti-bot measures,
but Google may still serve a CAPTCHA. Re-run with `--headed` to solve it by
hand; the cache means nothing already scraped is lost.
