# Google Maps Club Scraper

Scrapes club listings from Google Maps across US states, enriches them with
contact details from each club's own website, and upserts the results into a
Google Sheet.

## Install

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
