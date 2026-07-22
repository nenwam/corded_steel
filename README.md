# Corded Steel

A web version of the Corded Steel spreadsheet: one shared table of daily
push-ups, pull-ups and miles, behind a single password that everyone shares.
Anyone who gets in can edit any cell, and edits save as soon as you leave a cell.

- `app.py` — the Streamlit app
- `db.py` — data layer; plain SQLite locally, Turso when configured
- `build_db.py` — one-off script that turns the `.xlsx` into `cordedsteel.db`

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

It opens `cordedsteel.db` in this folder. The password is **poop** — change it
from the Settings expander once you're in.

To rebuild the database from the spreadsheet (this wipes any entries):

```bash
pip install openpyxl
python3 build_db.py "Corded Steel 2026.xlsx"
```

## Deploy

### 1. Push the database to Turso

```bash
turso auth login
turso db create cordedsteel --from-file cordedsteel.db
turso db show cordedsteel --url      # libsql://cordedsteel-you.turso.io
turso db tokens create cordedsteel   # the auth token
```

### 2. Put the repo on GitHub

`.gitignore` already keeps `cordedsteel.db` and `.streamlit/secrets.toml` out of
git. That's deliberate: Turso holds the live data, and a database committed to a
repo would drift out of sync with it immediately.

### 3. Point Streamlit Cloud at it

Create the app from the repo with `app.py` as the entrypoint, then paste this
into **App settings → Secrets**:

```toml
[turso]
url = "libsql://cordedsteel-you.turso.io"
auth_token = "..."
```

With those secrets present the app talks to Turso; without them it falls back to
the local file. `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` environment variables
work too, and take precedence.

If you see "This database has no participants or exercises yet" after deploying,
the secrets aren't being read — the app created a fresh empty local file instead
of connecting to Turso.

## How it handles several people at once

Only the cells you actually changed get written, one `INSERT … ON CONFLICT`
statement each. Two people editing different cells both win, even if one of them
is looking at a stale page. Two people editing the *same* cell in the same
minute is last-write-wins — hit **Refresh** to pull in everyone else's changes.

## Security

It's a shared password for a group of friends, not a bank. Within that:

- **The password is never stored in clear.** It's a salted PBKDF2-SHA256 digest
  (240k iterations) in the `app_meta` table, compared in constant time. Eight
  wrong guesses locks that browser session out for a minute.
- **SQL injection**: every statement is parameterised — no user value is ever
  interpolated into SQL, and table/column names are hard-coded literals.
- **XSS**: numbers are the only thing users can type into the grid, and they're
  parsed as floats and clamped to `[0, 1_000_000]`. Names are held to
  `[A-Za-z0-9 ._'-]`, max 32 characters, so markup can't be stored in the first
  place. Everything on the page renders through native Streamlit elements, which
  escape their content; the one `unsafe_allow_html` block is a static stylesheet
  with nothing interpolated into it.

What it does *not* do: identify who made an edit (everyone shares one password),
or survive a page reload without logging in again.

## Changing the challenge

The Settings expander handles goals, adding and removing people, and the
password. Dates live in `app_meta` as `start_date` / `end_date`; the grid draws
one row per day between them.
