import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-development-key")
app.permanent_session_lifetime = timedelta(days=365)
DATABASE = Path(__file__).with_name("gymtrack.db")

USERS = {"leaha": "Leaha", "uzair": "Uzair"}
WEIGHT_UNITS = {"leaha": "kg", "uzair": "lb"}
KG_PER_LB = 0.45359237

HAS_LOGGED_SETS = """EXISTS (
    SELECT 1 FROM exercise_sets es
    JOIN workout_exercises we ON we.id = es.workout_exercise_id
    WHERE we.workout_id = w.id
)"""


def current_user():
    """Return the session's chosen user key, or None if not picked yet."""
    key = session.get("current_user")
    return key if key in USERS else None


def kg_to_display(weight_kg, unit):
    """Convert a weight stored in kg to the given display unit."""
    return weight_kg / KG_PER_LB if unit == "lb" else weight_kg


def display_to_kg(weight, unit):
    """Convert a weight entered in the given display unit back to kg for storage."""
    return weight * KG_PER_LB if unit == "lb" else weight


def format_weight(value):
    """Format a weight without trailing zeros, matching the old SQL printf('%g', ...)."""
    return "%g" % value


def connect_to_database():
    """Open a connection and return rows that can be read by column name."""
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def migrate_legacy_exercises(connection):
    """One-time migration from the old single-row-per-exercise schema to per-set rows."""
    old_rows = connection.execute(
        "SELECT id, workout_id, name, sets, reps, weight FROM exercises ORDER BY workout_id, id"
    ).fetchall()

    position_by_workout = {}
    for row in old_rows:
        position = position_by_workout.get(row["workout_id"], 0)
        exercise = connection.execute(
            "INSERT INTO workout_exercises (workout_id, name, position) VALUES (?, ?, ?)",
            (row["workout_id"], row["name"], position),
        )
        position_by_workout[row["workout_id"]] = position + 1
        connection.executemany(
            """INSERT INTO exercise_sets (workout_exercise_id, set_number, reps, weight)
               VALUES (?, ?, ?, ?)""",
            [
                (exercise.lastrowid, set_number, row["reps"], row["weight"])
                for set_number in range(1, row["sets"] + 1)
            ],
        )

    connection.execute("DROP TABLE exercises")


def initialize_database():
    """Create the workout tables and migrate older schemas."""
    with connect_to_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                workout_date TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'leaha'
            );

            CREATE TABLE IF NOT EXISTS workout_exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                position INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS exercise_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_exercise_id INTEGER NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
                set_number INTEGER NOT NULL,
                reps INTEGER NOT NULL,
                weight REAL NOT NULL
            );
            """
        )
        try:
            connection.execute(
                "ALTER TABLE workouts ADD COLUMN user_id TEXT NOT NULL DEFAULT 'leaha'"
            )
        except sqlite3.OperationalError:
            pass  # column already exists on a database created before multi-user support

        legacy_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'exercises'"
        ).fetchone()
        if legacy_table:
            migrate_legacy_exercises(connection)


def get_current_streak(workout_dates):
    """Count consecutive calendar days with a workout, starting today or yesterday."""
    recorded_days = {date.fromisoformat(workout_date) for workout_date in workout_dates}
    streak_day = date.today()
    if streak_day not in recorded_days:
        streak_day -= timedelta(days=1)

    streak = 0
    while streak_day in recorded_days:
        streak += 1
        streak_day -= timedelta(days=1)
    return streak


def recent_distinct_workouts(connection, user, limit=3):
    """This user's most recent workouts, one per distinct name, newest first."""
    rows = connection.execute(
        f"""SELECT id, name, workout_date FROM workouts AS w
            WHERE user_id = ? AND {HAS_LOGGED_SETS}
            ORDER BY workout_date DESC, id DESC""",
        (user,),
    ).fetchall()

    seen_names = set()
    recent = []
    for row in rows:
        key = row["name"].lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        recent.append(row)
        if len(recent) >= limit:
            break
    return recent


def get_set_prefill(connection, user, exercise_name, sets_logged_so_far):
    """Reps/weight (in kg) to suggest for an exercise's next set."""
    if sets_logged_so_far:
        last = sets_logged_so_far[-1]
        return last["reps"], last["weight"]

    row = connection.execute(
        """SELECT es.reps, es.weight
           FROM exercise_sets AS es
           JOIN workout_exercises AS we ON we.id = es.workout_exercise_id
           JOIN workouts AS w ON w.id = we.workout_id
           WHERE w.user_id = ? AND LOWER(we.name) = LOWER(?)
           ORDER BY w.workout_date DESC, es.id DESC
           LIMIT 1""",
        (user, exercise_name),
    ).fetchone()
    if row:
        return row["reps"], row["weight"]
    return 8, 0.0


def get_owned_workout(connection, user, workout_id):
    return connection.execute(
        "SELECT id, name, workout_date FROM workouts WHERE id = ? AND user_id = ?",
        (workout_id, user),
    ).fetchone()


def get_owned_exercise(connection, user, workout_id, exercise_id):
    return connection.execute(
        """SELECT we.id, we.name FROM workout_exercises AS we
           JOIN workouts AS w ON w.id = we.workout_id
           WHERE we.id = ? AND we.workout_id = ? AND w.user_id = ?""",
        (exercise_id, workout_id, user),
    ).fetchone()


@app.route("/switch-user", methods=["POST"])
def switch_user():
    user_key = request.form.get("user", "")
    if user_key not in USERS:
        flash("Choose a valid profile.", "error")
        return redirect(url_for("dashboard"))

    session.permanent = True
    session["current_user"] = user_key
    return redirect(url_for("dashboard"))


@app.route("/switch-user/clear", methods=["POST"])
def clear_user():
    session.pop("current_user", None)
    return redirect(url_for("dashboard"))


@app.route("/workouts/<int:workout_id>/delete", methods=["POST"])
def delete_workout(workout_id):
    user = current_user()
    if not user:
        return redirect(url_for("dashboard"))

    with connect_to_database() as connection:
        result = connection.execute(
            "DELETE FROM workouts WHERE id = ? AND user_id = ?",
            (workout_id, user),
        )

    if result.rowcount:
        flash("Workout deleted.", "success")
    else:
        flash("Workout not found.", "error")
    return redirect(url_for("dashboard", _anchor="history"))


@app.route("/log", methods=["GET", "POST"])
def new_workout():
    user = current_user()
    if not user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        workout_name = request.form.get("workout_name", "").strip()
        workout_date = request.form.get("workout_date", "").strip()
        repeat_workout_id = request.form.get("repeat_workout_id", "")

        if not workout_name or len(workout_name) > 80:
            flash("Enter a workout name.", "error")
            return redirect(url_for("new_workout"))
        try:
            date.fromisoformat(workout_date)
        except ValueError:
            flash("Choose a valid date.", "error")
            return redirect(url_for("new_workout"))

        with connect_to_database() as connection:
            workout = connection.execute(
                "INSERT INTO workouts (name, workout_date, user_id) VALUES (?, ?, ?)",
                (workout_name, workout_date, user),
            )
            new_workout_id = workout.lastrowid

            if repeat_workout_id:
                source_workout = get_owned_workout(connection, user, repeat_workout_id)
                if source_workout:
                    source_exercises = connection.execute(
                        """SELECT name, position FROM workout_exercises
                           WHERE workout_id = ? ORDER BY position""",
                        (repeat_workout_id,),
                    ).fetchall()
                    connection.executemany(
                        """INSERT INTO workout_exercises (workout_id, name, position)
                           VALUES (?, ?, ?)""",
                        [(new_workout_id, ex["name"], ex["position"]) for ex in source_exercises],
                    )

        return redirect(url_for("log_workout", workout_id=new_workout_id))

    with connect_to_database() as connection:
        recent_workouts = recent_distinct_workouts(connection, user)

    return render_template(
        "start_workout.html",
        recent_workouts=recent_workouts,
        today=date.today().isoformat(),
        current_user_name=USERS[user],
    )


@app.route("/log/<int:workout_id>", methods=["GET", "POST"])
def log_workout(workout_id):
    user = current_user()
    if not user:
        return redirect(url_for("dashboard"))
    unit = WEIGHT_UNITS[user]

    with connect_to_database() as connection:
        workout = get_owned_workout(connection, user, workout_id)
        if not workout:
            flash("Workout not found.", "error")
            return redirect(url_for("dashboard", _anchor="history"))

        if request.method == "POST":
            workout_name = request.form.get("workout_name", "").strip()
            workout_date = request.form.get("workout_date", "").strip()
            if not workout_name or len(workout_name) > 80:
                flash("Enter a workout name.", "error")
            else:
                try:
                    date.fromisoformat(workout_date)
                except ValueError:
                    flash("Choose a valid date.", "error")
                else:
                    connection.execute(
                        "UPDATE workouts SET name = ?, workout_date = ? WHERE id = ?",
                        (workout_name, workout_date, workout_id),
                    )
                    flash("Workout updated.", "success")
            return redirect(url_for("log_workout", workout_id=workout_id))

        exercise_rows = connection.execute(
            "SELECT id, name FROM workout_exercises WHERE workout_id = ? ORDER BY position, id",
            (workout_id,),
        ).fetchall()

        exercises = []
        for exercise_row in exercise_rows:
            set_rows = connection.execute(
                """SELECT id, set_number, reps, weight FROM exercise_sets
                   WHERE workout_exercise_id = ? ORDER BY set_number""",
                (exercise_row["id"],),
            ).fetchall()
            prefill_reps, prefill_weight = get_set_prefill(
                connection, user, exercise_row["name"], set_rows
            )
            exercises.append(
                {
                    "id": exercise_row["id"],
                    "name": exercise_row["name"],
                    "sets": [
                        {
                            "id": s["id"],
                            "set_number": s["set_number"],
                            "reps": s["reps"],
                            "weight": format_weight(kg_to_display(s["weight"], unit)),
                        }
                        for s in set_rows
                    ],
                    "prefill_reps": prefill_reps,
                    "prefill_weight": format_weight(kg_to_display(prefill_weight, unit)),
                }
            )

    return render_template(
        "log_workout.html",
        workout=workout,
        exercises=exercises,
        weight_unit=unit,
        current_user_name=USERS[user],
    )


@app.route("/log/<int:workout_id>/exercises", methods=["POST"])
def add_exercise(workout_id):
    user = current_user()
    if not user:
        return jsonify(error="Not signed in."), 403
    unit = WEIGHT_UNITS[user]

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name or len(name) > 80:
        return jsonify(error="Enter an exercise name."), 400

    with connect_to_database() as connection:
        if not get_owned_workout(connection, user, workout_id):
            return jsonify(error="Workout not found."), 404

        position = connection.execute(
            "SELECT COUNT(*) FROM workout_exercises WHERE workout_id = ?", (workout_id,)
        ).fetchone()[0]
        exercise = connection.execute(
            "INSERT INTO workout_exercises (workout_id, name, position) VALUES (?, ?, ?)",
            (workout_id, name, position),
        )
        prefill_reps, prefill_weight = get_set_prefill(connection, user, name, [])

    return jsonify(
        id=exercise.lastrowid,
        name=name,
        prefill_reps=prefill_reps,
        prefill_weight=format_weight(kg_to_display(prefill_weight, unit)),
    )


@app.route("/log/<int:workout_id>/exercises/<int:exercise_id>", methods=["DELETE"])
def remove_exercise(workout_id, exercise_id):
    user = current_user()
    if not user:
        return jsonify(error="Not signed in."), 403

    with connect_to_database() as connection:
        if not get_owned_exercise(connection, user, workout_id, exercise_id):
            return jsonify(error="Exercise not found."), 404
        connection.execute("DELETE FROM workout_exercises WHERE id = ?", (exercise_id,))

    return jsonify(ok=True)


@app.route("/log/<int:workout_id>/exercises/<int:exercise_id>/sets", methods=["POST"])
def add_set(workout_id, exercise_id):
    user = current_user()
    if not user:
        return jsonify(error="Not signed in."), 403
    unit = WEIGHT_UNITS[user]

    data = request.get_json(silent=True) or {}
    try:
        reps = int(data.get("reps"))
        weight = display_to_kg(float(data.get("weight")), unit)
        if reps < 1 or weight < 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(error="Enter valid reps and weight."), 400

    with connect_to_database() as connection:
        if not get_owned_exercise(connection, user, workout_id, exercise_id):
            return jsonify(error="Exercise not found."), 404

        set_number = (
            connection.execute(
                "SELECT COUNT(*) FROM exercise_sets WHERE workout_exercise_id = ?",
                (exercise_id,),
            ).fetchone()[0]
            + 1
        )
        new_set = connection.execute(
            """INSERT INTO exercise_sets (workout_exercise_id, set_number, reps, weight)
               VALUES (?, ?, ?, ?)""",
            (exercise_id, set_number, reps, weight),
        )

    return jsonify(
        id=new_set.lastrowid,
        set_number=set_number,
        reps=reps,
        weight=format_weight(kg_to_display(weight, unit)),
    )


@app.route("/log/<int:workout_id>/exercises/<int:exercise_id>/sets/<int:set_id>", methods=["DELETE"])
def remove_set(workout_id, exercise_id, set_id):
    user = current_user()
    if not user:
        return jsonify(error="Not signed in."), 403

    with connect_to_database() as connection:
        if not get_owned_exercise(connection, user, workout_id, exercise_id):
            return jsonify(error="Exercise not found."), 404

        last_set = connection.execute(
            """SELECT id FROM exercise_sets WHERE workout_exercise_id = ?
               ORDER BY set_number DESC LIMIT 1""",
            (exercise_id,),
        ).fetchone()
        if not last_set or last_set["id"] != set_id:
            return jsonify(error="Only the most recent set can be removed."), 400

        connection.execute("DELETE FROM exercise_sets WHERE id = ?", (set_id,))

    return jsonify(ok=True)


@app.route("/")
def dashboard():
    user = current_user()
    if not user:
        return render_template("pick_user.html", users=USERS)
    unit = WEIGHT_UNITS[user]

    search_term = request.args.get("search", "").strip()
    search_pattern = f"%{search_term}%"

    with connect_to_database() as connection:
        workout_rows = connection.execute(
            f"""SELECT w.id, w.name, w.workout_date
               FROM workouts AS w
               WHERE w.user_id = ? AND {HAS_LOGGED_SETS} AND (? = '' OR w.name LIKE ? OR EXISTS (
                   SELECT 1 FROM workout_exercises AS match_e
                   WHERE match_e.workout_id = w.id AND match_e.name LIKE ?
               ))
               ORDER BY w.workout_date DESC, w.id DESC
               LIMIT 20""",
            (user, search_term, search_pattern, search_pattern),
        ).fetchall()

        exercise_order = {}
        exercise_data = {}
        if workout_rows:
            workout_ids = [row["id"] for row in workout_rows]
            placeholders = ",".join("?" * len(workout_ids))
            for row in connection.execute(
                f"""SELECT we.workout_id, we.id AS exercise_id, we.name,
                           es.reps, es.weight
                    FROM workout_exercises AS we
                    JOIN exercise_sets AS es ON es.workout_exercise_id = we.id
                    WHERE we.workout_id IN ({placeholders})
                    ORDER BY we.workout_id, we.position, we.id, es.set_number""",
                workout_ids,
            ):
                exercise_id = row["exercise_id"]
                if exercise_id not in exercise_data:
                    exercise_data[exercise_id] = {"name": row["name"], "sets": []}
                    exercise_order.setdefault(row["workout_id"], []).append(exercise_id)
                exercise_data[exercise_id]["sets"].append((row["reps"], row["weight"]))

        def build_exercise_details(workout_id):
            parts = []
            for exercise_id in exercise_order.get(workout_id, []):
                exercise = exercise_data[exercise_id]
                set_descriptions = ", ".join(
                    f"{reps}x{format_weight(kg_to_display(weight, unit))}"
                    for reps, weight in exercise["sets"]
                )
                parts.append(f"{exercise['name']} ({set_descriptions} {unit})")
            return ", ".join(parts)

        workouts = [
            {
                "id": row["id"],
                "name": row["name"],
                "workout_date": row["workout_date"],
                "exercise_details": build_exercise_details(row["id"]),
            }
            for row in workout_rows
        ]

        record_rows = connection.execute(
            """SELECT we.name, MAX(es.weight) AS best_weight
               FROM exercise_sets AS es
               JOIN workout_exercises AS we ON we.id = es.workout_exercise_id
               JOIN workouts AS w ON w.id = we.workout_id
               WHERE w.user_id = ?
               GROUP BY LOWER(we.name)
               ORDER BY best_weight DESC, we.name COLLATE NOCASE
               LIMIT 3""",
            (user,),
        ).fetchall()
        personal_records = [
            {"name": row["name"], "best_weight": kg_to_display(row["best_weight"], unit)}
            for row in record_rows
        ]
        all_workout_dates = [
            row[0]
            for row in connection.execute(
                f"""SELECT DISTINCT workout_date FROM workouts AS w
                    WHERE user_id = ? AND {HAS_LOGGED_SETS}
                    ORDER BY workout_date DESC""",
                (user,),
            ).fetchall()
        ]
        month = date.today().strftime("%Y-%m")
        month_stats = connection.execute(
            f"""SELECT COUNT(*) AS workouts,
                      COUNT(DISTINCT workout_date) AS visits
               FROM workouts AS w
               WHERE user_id = ? AND {HAS_LOGGED_SETS} AND substr(workout_date, 1, 7) = ?""",
            (user, month),
        ).fetchone()
        workout_count = connection.execute(
            f"SELECT COUNT(*) FROM workouts AS w WHERE user_id = ? AND {HAS_LOGGED_SETS}",
            (user,),
        ).fetchone()[0]

    return render_template(
        "index.html",
        current_user_key=user,
        current_user_name=USERS[user],
        weight_unit=unit,
        workouts=workouts,
        personal_records=personal_records,
        workouts_this_month=month_stats["workouts"],
        visits_this_month=month_stats["visits"],
        current_streak=get_current_streak(all_workout_dates),
        total_workouts=workout_count,
        search_term=search_term,
        today=date.today().isoformat(),
    )


initialize_database()


if __name__ == "__main__":
    app.run(debug=True)
