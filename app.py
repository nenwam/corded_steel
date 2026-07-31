"""Corded Steel — a shared, password-gated workout tracker.

A web version of the Corded Steel spreadsheet: one editable grid of daily
numbers per person per exercise, plus the Total / % of Goal / Avg. footer and a
cumulative progress chart.

Rendering safety: every value shown on the page goes through a native Streamlit
element (`st.metric`, `st.dataframe`, `st.data_editor`, Altair), all of which
escape their content. The raw HTML in `style.py` is a static stylesheet plus
literal chrome, so there is no path for stored markup to execute — and names
are allowlisted on the way in besides.
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import db
import style

MAX_ATTEMPTS = 8
LOCKOUT_SECONDS = 60

# The program's day-rollover follows this zone, not the host's. Streamlit
# Community Cloud runs its containers in UTC, so pin "today" explicitly here
# rather than relying on the server clock.
APP_TZ = ZoneInfo("America/Los_Angeles")

# A colourblind-safe categorical ramp, stepped separately for each mode. The
# order is the safety mechanism — assign slots in sequence, never shuffle them.
# Orange leads because it is the most aggressive hue that still clears every
# separation check against this app's near-black background.
SERIES_LIGHT = [
    "#eb6834", "#2a78d6", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]
SERIES_DARK = [
    "#d95926", "#3987e5", "#199e70", "#c98500",
    "#d55181", "#008300", "#9085e9", "#e66767",
]
MUTED = "#898781"
# Chart text wears ink, never a series colour — the words already say who it is.
INK_DARK, INK_LIGHT = "#ededed", "#0b0b0b"
CHART_HEIGHT = 320


st.set_page_config(page_title="CORDED STEEL", page_icon="💀", layout="wide")

theme_mode = style.current_mode()
style.inject()


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

@st.cache_resource(show_spinner=False)
def get_connection():
    conn = db.connect()
    db.init_schema(conn)
    return conn


def reconnect():
    get_connection.clear()
    return get_connection()


conn = get_connection()


# --------------------------------------------------------------------------- #
# Password gate
# --------------------------------------------------------------------------- #

def locked_out() -> float:
    """Seconds remaining on the lockout, or 0 when the user may try again."""
    until = st.session_state.get("locked_until", 0.0)
    return max(0.0, until - time.time())


def authenticate(password: str) -> bool:
    """Check the password, reconnecting once if the cached Turso socket died.

    `load_board` already self-heals a dropped HTTP connection, but the login
    gate runs before it — so without the same retry here a stale connection
    raises at the door and nobody can reach the board that would have healed it.
    """
    global conn
    try:
        return db.verify_password(conn, password)
    except Exception:
        conn = reconnect()
        return db.verify_password(conn, password)


def login_screen() -> None:
    style.ensure_default_dark()
    style.kicker("Members only")
    st.title("Corded Steel")
    style.tape()
    style.creed("Push-ups · Pull-ups · Miles. Nothing else counts.")
    st.caption("Say the word and the gates open.")

    remaining = locked_out()
    if remaining:
        st.error(f"Locked out. Cool off for {int(remaining) + 1}s.")
        st.stop()

    with st.form("login", clear_on_submit=True):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Let me in", type="primary")

    if submitted:
        if authenticate(password):
            st.session_state.authenticated = True
            st.session_state.attempts = 0
            st.rerun()
        else:
            st.session_state.attempts = st.session_state.get("attempts", 0) + 1
            if st.session_state.attempts >= MAX_ATTEMPTS:
                st.session_state.locked_until = time.time() + LOCKOUT_SECONDS
                st.session_state.attempts = 0
                st.rerun()
            st.error("Wrong. Try again.")

    st.stop()


if not st.session_state.get("authenticated"):
    login_screen()


# --------------------------------------------------------------------------- #
# Load the board
# --------------------------------------------------------------------------- #

def load_board():
    """Read everything the page needs, reconnecting once if the socket died.

    Turso connections go over HTTP and can be dropped between reruns, which
    otherwise surfaces as a hard error on an idle tab.
    """
    global conn
    try:
        return _read_board()
    except Exception:
        conn = reconnect()
        return _read_board()


def _read_board():
    return (
        db.list_participants(conn),
        db.list_exercises(conn),
        db.get_goals(conn),
        db.get_entries(conn),
        db.challenge_days(conn),
        db.get_meta(conn, "title", "Corded Steel"),
    )


def format_day(value, pattern="%b %d") -> str:
    """`strftime` without a leading zero on the day, portably."""
    return value.strftime(pattern).replace(" 0", " ")


def drop_stale(key, options) -> None:
    """Forget a radio selection that no longer exists (e.g. a removed person)."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]


participants, exercises, goals, entries, days, title = load_board()

if not participants or not exercises:
    st.title("Corded Steel")
    st.warning(
        "This database has no participants or exercises yet. "
        "Run `python3 build_db.py` to seed it from the spreadsheet."
    )
    st.stop()

start_day, end_day = days[0], days[-1]

today = datetime.now(APP_TZ).date()
days_elapsed = min(max((today - start_day).days + 1, 0), len(days))
days_left = max(len(days) - days_elapsed, 0)

if today < start_day:
    countdown = (start_day - today).days
    when = f"starts in {countdown} day{'s' if countdown != 1 else ''}"
elif today > end_day:
    when = "finished"
else:
    when = f"day {days_elapsed} of {len(days)}"


def column_label(participant, exercise, include_person: bool) -> str:
    return f"{participant['name']} · {exercise['name']}" if include_person else exercise["name"]


def totals_for(participant_id, exercise_id) -> float:
    return sum(
        value
        for (pid, eid, _day), value in entries.items()
        if pid == participant_id and eid == exercise_id
    )


palette = SERIES_DARK if theme_mode == "dark" else SERIES_LIGHT
ink = INK_DARK if theme_mode == "dark" else INK_LIGHT
# Slots are handed out in a fixed order and stay with the person, so everyone
# keeps the same colour in the scoreboard and the progress chart.
names = [participant["name"] for participant in participants][: len(palette)]
series_color = alt.Color(
    "Who:N",
    scale=alt.Scale(domain=names, range=palette[: len(names)]),
    legend=alt.Legend(title=None, orient="top"),
)


# --------------------------------------------------------------------------- #
# Header and scoreboard
# --------------------------------------------------------------------------- #

header, controls = st.columns([3, 1])
with header:
    style.kicker(f"{len(days)} days")
    st.title(title)
    st.caption(
        f"{format_day(start_day)} – {format_day(end_day, '%b %d, %Y')} · {when}"
    )
with controls:
    st.write("")
    if st.button("Refresh", width="stretch", help="Pull in everyone else's latest"):
        st.rerun()

    going_light = theme_mode == "dark"
    if st.button(
        "Daylight" if going_light else "Blackout",
        width="stretch",
        help="Switches the theme for you only, on this browser",
    ):
        style.apply_theme("light" if going_light else "dark")

    if st.button("Log out", width="stretch"):
        st.session_state.clear()
        st.rerun()

# A prod for anyone freshly added to the roster who has not yet logged a rep:
# it clears itself the moment Myles enters a single non-zero number anywhere.
myles = next((p for p in participants if p["name"] == "Myles"), None)
if myles is not None and not any(
    value != 0
    for (pid, _eid, _day), value in entries.items()
    if pid == myles["id"]
):
    st.error(
        "**Status of Myles: Rot**  \n"
        "This message will disappear once Myles enters data."
    )

style.tape()

# A single compact chart rather than three, so the editable table stays near the
# top of the page. Plotting % of goal puts push-ups, pull-ups and miles on one
# axis honestly — the raw numbers are in the totals table further down.
standings = pd.DataFrame(
    [
        {
            "Exercise": exercise["name"],
            "Who": participant["name"],
            "Percent": (total / goal * 100) if goal else 0.0,
            "Total": f"{total:,.{exercise['decimals']}f}",
            "Label": f"{total:,.{exercise['decimals']}f} of {goal:,.{exercise['decimals']}f}",
        }
        for exercise in exercises
        for participant in participants
        for total, goal in [
            (
                totals_for(participant["id"], exercise["id"]),
                goals.get((participant["id"], exercise["id"]), 0.0),
            )
        ]
    ]
)

standings_base = alt.Chart(standings).encode(
    y=alt.Y("Exercise:N", sort=[e["name"] for e in exercises], title=None),
    yOffset=alt.YOffset("Who:N", sort=names),
    x=alt.X(
        "Percent:Q",
        title="% of goal",
        scale=alt.Scale(nice=False),
        axis=alt.Axis(tickCount=6, gridOpacity=0.4),
    ),
)

bars = standings_base.mark_bar(cornerRadiusEnd=3).encode(
    color=series_color,
    tooltip=[
        alt.Tooltip("Who:N", title="Who"),
        alt.Tooltip("Exercise:N", title="Exercise"),
        alt.Tooltip("Label:N", title="Progress"),
        alt.Tooltip("Percent:Q", title="% of goal", format=".1f"),
    ],
)

# The raw number at the end of each bar: the percentage axis makes the three
# exercises comparable, but push-ups are still counted in push-ups.
bar_labels = standings_base.mark_text(
    align="left", dx=6, fontSize=11, color=ink
).encode(text="Total:N")

goal_rule = (
    alt.Chart(pd.DataFrame({"Percent": [100.0]}))
    .mark_rule(strokeWidth=1.5, strokeDash=[4, 4], color=MUTED)
    .encode(x="Percent:Q")
)

st.altair_chart(
    alt.layer(bars, goal_rule, bar_labels)
    .properties(
        width="container",
        height=26 * len(exercises) * len(participants) + 30,
        padding={"right": 52},
    )
    .configure_view(strokeWidth=0)
)
st.caption(
    "Dashed line is the goal · "
    + " · ".join(
        f"{exercise['name']} "
        f"{max(goals.get((p['id'], exercise['id']), 0.0) for p in participants):,.{exercise['decimals']}f}"
        for exercise in exercises
    )
)

st.divider()


# --------------------------------------------------------------------------- #
# The editable grid
# --------------------------------------------------------------------------- #

st.subheader("The log")

view_options = ["Everyone"] + [p["name"] for p in participants]
drop_stale("view", view_options)
view = st.radio(
    "Show", view_options, horizontal=True, label_visibility="collapsed", key="view"
)

visible = (
    participants
    if view == "Everyone"
    else [p for p in participants if p["name"] == view]
)

# Map each grid column back to its ids so the label is never parsed back apart.
column_map = {}
for participant in visible:
    for exercise in exercises:
        column_map[column_label(participant, exercise, view == "Everyone")] = (
            participant["id"],
            exercise["id"],
            exercise,
        )

grid = pd.DataFrame({"Date": days})
for label, (participant_id, exercise_id, exercise) in column_map.items():
    grid[label] = [
        entries.get((participant_id, exercise_id, day.isoformat()), 0.0) for day in days
    ]

column_config = {
    "Date": st.column_config.DateColumn("Date", format="ddd D MMM", pinned=True),
}
for label, (_pid, _eid, exercise) in column_map.items():
    decimals = exercise["decimals"]
    column_config[label] = st.column_config.NumberColumn(
        label,
        min_value=0.0,
        step=0.25 if decimals else 1,
        format=f"%.{decimals}f" if decimals else "%d",
        help=f"{exercise['name']} ({exercise['unit']})" if exercise["unit"] else None,
    )


# One editor per view, so switching views can never replay an edit against a
# different set of columns.
grid_key = f"grid_{view}"


def save_edits() -> None:
    """Persist just the cells that changed, then clear the pending delta.

    Writing per cell rather than per row means two people editing the board at
    the same time only collide if they touch the very same cell. Re-applying a
    delta would be harmless anyway — every write is an idempotent upsert.
    """
    state = st.session_state.get(grid_key) or {}
    edited_rows = state.get("edited_rows") or {}

    cells = []
    for row_index, changes in edited_rows.items():
        try:
            day = days[int(row_index)]
        except (ValueError, IndexError):
            continue
        for label, value in changes.items():
            target = column_map.get(label)
            if target is None:
                continue  # the Date column, or a stale label after a view switch
            participant_id, exercise_id, _exercise = target
            cells.append((participant_id, exercise_id, day, value))

    if cells:
        db.set_entries(conn, cells)
    edited_rows.clear()


st.data_editor(
    grid,
    key=grid_key,
    on_change=save_edits,
    column_config=column_config,
    disabled=["Date"],
    num_rows="fixed",
    hide_index=True,
    height=min(38 * len(days) + 40, 620),
)
st.caption(
    "Edits save the moment you leave a cell. Hit refresh to pull in "
    "everyone else's changes."
)


# --------------------------------------------------------------------------- #
# Totals footer — the Total / % of Goal / Avg. rows from the spreadsheet
# --------------------------------------------------------------------------- #

footer = {}
for label, (participant_id, exercise_id, exercise) in column_map.items():
    total = totals_for(participant_id, exercise_id)
    goal = goals.get((participant_id, exercise_id), 0.0)
    decimals = max(exercise["decimals"], 1)
    footer[label] = [
        f"{total:,.{exercise['decimals']}f}",
        f"{(total / len(days)) if days else 0:,.{decimals}f}",
        f"{(total / goal * 100) if goal else 0:.1f}%",
    ]

st.dataframe(
    pd.DataFrame(footer, index=["Total", "Avg. / day", "% of Goal"]),
    column_config={label: st.column_config.TextColumn(label) for label in footer},
)


# --------------------------------------------------------------------------- #
# Cumulative progress
# --------------------------------------------------------------------------- #

st.divider()
st.subheader("The climb")

exercise_names = [item["name"] for item in exercises]
drop_stale("focus_exercise", exercise_names)
chosen = st.radio(
    "Exercise",
    exercise_names,
    horizontal=True,
    label_visibility="collapsed",
    key="focus_exercise",
)
exercise = next(item for item in exercises if item["name"] == chosen)

# Stop the cumulative lines at today. Running them to the end of the challenge
# would draw a flat line across days nobody has had the chance to log yet, which
# reads as "gave up" rather than "hasn't happened". Anything already logged past
# today still gets drawn — hiding someone's entries would be worse.
logged = [
    day
    for day in days
    if any(
        entries.get((p["id"], exercise["id"], day.isoformat()))
        for p in participants
    )
]
last_plot_day = max([min(max(today, start_day), end_day)] + logged)
plot_days = [day for day in days if day <= last_plot_day] or days[:1]

records = []
for participant in participants:
    running = 0.0
    for day in plot_days:
        running += entries.get(
            (participant["id"], exercise["id"], day.isoformat()), 0.0
        )
        records.append({"Date": day, "Who": participant["name"], "Total": running})
progress = pd.DataFrame(records)

progress = progress[progress["Who"].isin(names)]

lines = (
    alt.Chart(progress)
    .mark_line(strokeWidth=2, point=len(plot_days) < 3)
    .encode(
        x=alt.X("Date:T", title=None),
        y=alt.Y("Total:Q", title=f"Cumulative {exercise['name'].lower()}"),
        color=series_color,
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %-d %b"),
            alt.Tooltip("Who:N", title="Who"),
            alt.Tooltip("Total:Q", title="Total", format=f",.{exercise['decimals']}f"),
        ],
    )
)

# A single dashed pace line, only when everyone is chasing the same number.
layers = [lines]
target = None
exercise_goals = {goals.get((p["id"], exercise["id"]), 0.0) for p in participants}
if len(exercise_goals) == 1 and exercise_goals != {0.0}:
    target = exercise_goals.pop()
    pace = pd.DataFrame(
        {"Date": [days[0], days[-1]], "Total": [target / len(days), target]}
    )
    layers.insert(
        0,
        alt.Chart(pace)
        .mark_line(strokeWidth=1.5, strokeDash=[4, 4], color=MUTED)
        .encode(x="Date:T", y="Total:Q"),
    )

# Identity comes from the colour key at the top; names are not printed on the
# lines themselves, since the wide date axis would crowd them at the left edge.
st.altair_chart(
    alt.layer(*layers)
    .properties(width="container", height=CHART_HEIGHT, padding={"right": 72})
    .configure_view(strokeWidth=0)
)
st.caption("Dashed line is the pace needed to finish on time.")

# What each person still has to do per day — the number the pace line implies.
decimals = exercise["decimals"]
for column, participant in zip(st.columns(len(participants)), participants):
    total = totals_for(participant["id"], exercise["id"])
    goal = goals.get((participant["id"], exercise["id"]), 0.0)
    remaining = max(goal - total, 0.0)
    with column:
        if remaining <= 0:
            st.metric(participant["name"], "Done 💀", border=True)
        else:
            per_day = remaining / days_left if days_left else remaining
            st.metric(
                participant["name"],
                f"{per_day:,.{max(decimals, 1)}f} / day",
                help=f"{remaining:,.{decimals}f} to go"
                + (f" over {days_left} days" if days_left else ", and no days left"),
                border=True,
            )


# --------------------------------------------------------------------------- #
# The debt — how far below a flat daily pace each person sits, per exercise
# --------------------------------------------------------------------------- #

st.divider()
st.subheader("debt")
st.caption(
    f"How far each person trails a flat daily pace as of day {days_elapsed} "
    f"of {len(days)}. The pace is each goal spread evenly across the "
    "challenge — the same dashed line as the climb. Zero means on or ahead."
)

# Expected-by-now is daily_rate * days_elapsed, which is exactly the pace line's
# height at today, so the debt is literally the gap below that dashed line.
debt = {}
for exercise_item in exercises:
    column = []
    for participant in participants:
        goal = goals.get((participant["id"], exercise_item["id"]), 0.0)
        expected = (goal / len(days) * days_elapsed) if days else 0.0
        actual = totals_for(participant["id"], exercise_item["id"])
        column.append(f"{max(expected - actual, 0.0):,.{exercise_item['decimals']}f}")
    debt[exercise_item["name"]] = column

st.dataframe(
    pd.DataFrame(debt, index=[p["name"] for p in participants]),
    column_config={
        exercise_item["name"]: st.column_config.TextColumn(exercise_item["name"])
        for exercise_item in exercises
    },
)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

with st.expander("The armory"):
    st.caption(f"Connected to {db.backend_name()}.")

    st.markdown("**Goals**")
    goal_columns = st.columns(len(exercises))
    for column, exercise_item in zip(goal_columns, exercises):
        with column:
            st.caption(exercise_item["name"])
            whole = exercise_item["decimals"] == 0
            for participant in participants:
                current = goals.get((participant["id"], exercise_item["id"]), 0.0)
                updated = st.number_input(
                    participant["name"],
                    min_value=0 if whole else 0.0,
                    value=int(current) if whole else float(current),
                    step=50 if whole else 0.5,
                    format="%d" if whole else "%.2f",
                    key=f"goal_{participant['id']}_{exercise_item['id']}",
                )
                if float(updated) != current:
                    db.set_goal(
                        conn, participant["id"], exercise_item["id"], float(updated)
                    )
                    st.rerun()

    st.divider()
    st.markdown("**People**")
    add_column, remove_column = st.columns(2)

    with add_column:
        with st.form("add_person", clear_on_submit=True):
            new_name = st.text_input("Add someone", max_chars=db.NAME_MAX_LEN)
            if st.form_submit_button("Add"):
                try:
                    template = participants[0]["id"]
                    db.add_participant(
                        conn,
                        new_name,
                        {
                            item["id"]: goals.get((template, item["id"]), 0.0)
                            for item in exercises
                        },
                    )
                    st.rerun()
                except ValueError as error:
                    st.error(str(error))

    with remove_column:
        with st.form("remove_person"):
            victim = st.selectbox("Remove someone", [p["name"] for p in participants])
            confirm = st.checkbox("Yes, delete their entries too")
            if st.form_submit_button("Remove"):
                if not confirm:
                    st.error("Tick the box to confirm.")
                else:
                    match = next(
                        (p for p in participants if p["name"] == victim), None
                    )
                    if match:
                        db.remove_participant(conn, match["id"])
                        st.rerun()

    st.divider()
    st.markdown("**Password**")
    with st.form("change_password", clear_on_submit=True):
        current_password = st.text_input("Current password", type="password")
        next_password = st.text_input("New password", type="password")
        repeat_password = st.text_input("Repeat new password", type="password")
        if st.form_submit_button("Change password"):
            if not db.verify_password(conn, current_password):
                st.error("Current password is wrong.")
            elif len(next_password) < 4:
                st.error("Use at least 4 characters.")
            elif next_password != repeat_password:
                st.error("The new passwords don't match.")
            else:
                db.set_password(conn, next_password)
                st.success("Password changed.")
