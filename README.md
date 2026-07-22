# Corded Steel

A web version of the Corded Steel spreadsheet: one shared table of daily
push-ups, pull-ups and miles, behind a single password that everyone shares.
Anyone who gets in can edit any cell, and edits save as soon as you leave a cell.

- `app.py` — the Streamlit app
- `db.py` — data layer; plain SQLite locally, Turso when configured
- `style.py` — the black/steel/hazard-tape look, and the theme switch
- `build_db.py` — one-off script that turns the `.xlsx` into `cordedsteel.db`

## Run it locally

```bash
pip install -r requirements.txt
CORDED_STEEL_LOCAL=1 streamlit run app.py
```

`CORDED_STEEL_LOCAL=1` forces the local `cordedsteel.db` file. **Use it once
Turso is set up** — otherwise, because `secrets.toml` sits in this folder, every
local test run edits the live database your friends are using.

The password is **poop** — change it from the armory once you're in.

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

## The look, and the theme switch

The app opens black. **Blackout / Daylight** in the top right switches themes for
**you only**, in that browser — one person going light doesn't drag everyone
else with them. Switching reloads the page, which means logging back in; that's
the cost of the mechanism, and it's why the button is a rare-use control rather
than something you'd flip constantly.

Two constraints shaped how this is built, both learned the hard way:

- **`.streamlit/config.toml` has no `[theme]` block, on purpose.** Streamlit
  treats *any* theme entry — even fonts alone — as an authoritative "Custom
  Theme" and then ignores the viewer's own choice, which silently breaks the
  switch. So the base colours come from Streamlit's Dark preset, and everything
  else lives in `style.py` as CSS that works on top of either preset.
- **The editable grid is a `<canvas>`.** No stylesheet can reach inside it, so
  its colours can only come from Streamlit's real theme. That's why the switch
  stores a preset rather than injecting CSS — it's the only approach where the
  grid follows along instead of staying white in a black page.

The CSS in `style.py` targets Streamlit's internal `data-testid` attributes,
which are undocumented and can change between versions. If an upgrade ever makes
the app look plain, that's the cause — the app keeps working, it just loses its
styling. One rule worth keeping: `[data-testid="stIconMaterial"]` must keep its
own font, or Streamlit's icons render as the literal text `keyboard_arrow_right`.

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
