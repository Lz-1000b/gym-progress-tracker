import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-development-key")
app.permanent_session_lifetime = timedelta(days=365)
DATABASE = Path(__file__).with_name("gymtrack.db")

USERS = {"leaha": "Leaha", "uzair": "Uzair"}
WEIGHT_UNITS = {"leaha": "kg", "uzair": "lb"}
KG_PER_LB = 0.45359237


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


def initialize_database():
    """Create the workout tables the first time the app is started."""
    with connect_to_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                workout_date TEXT NOT NULL,
                user_id TEXT NOT NULL DEFAULT 'leaha'
            );

            CREATE TABLE IF NOT EXISTS exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                sets INTEGER NOT NULL,
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


@app.route("/workouts/<int:workout_id>/edit", methods=["GET", "POST"])
def edit_workout(workout_id):
    user = current_user()
    if not user:
        return redirect(url_for("dashboard"))
    unit = WEIGHT_UNITS[user]

    with connect_to_database() as connection:
        workout = connection.execute(
            "SELECT id, name, workout_date FROM workouts WHERE id = ? AND user_id = ?",
            (workout_id, user),
        ).fetchone()
        exercise_rows = connection.execute(
            """SELECT name, sets, reps, weight FROM exercises
               WHERE workout_id = ? ORDER BY id""",
            (workout_id,),
        ).fetchall()

    if not workout:
        flash("Workout not found.", "error")
        return redirect(url_for("dashboard", _anchor="history"))

    display_exercises = [
        {
            "name": row["name"],
            "sets": row["sets"],
            "reps": row["reps"],
            "weight": format_weight(kg_to_display(row["weight"], unit)),
        }
        for row in exercise_rows
    ]

    if request.method == "POST":
        workout_name = request.form.get("workout_name", "").strip()
        workout_date = request.form.get("workout_date", "").strip()
        names = request.form.getlist("exercise_name")
        sets_values = request.form.getlist("sets")
        reps_values = request.form.getlist("reps")
        weights = request.form.getlist("weight")
        submitted_exercises = [
            {"name": name, "sets": sets, "reps": reps, "weight": weight}
            for name, sets, reps, weight in zip(names, sets_values, reps_values, weights)
        ]
        exercises_to_save = []

        try:
            for name, sets, reps, weight in zip(names, sets_values, reps_values, weights):
                name = name.strip()
                if not name:
                    continue
                sets = int(sets)
                reps = int(reps)
                weight = display_to_kg(float(weight), unit)
                if len(name) > 80 or sets < 1 or reps < 1 or weight < 0:
                    raise ValueError
                exercises_to_save.append((name, sets, reps, weight))

            if not workout_name or len(workout_name) > 80 or not exercises_to_save:
                raise ValueError
            date.fromisoformat(workout_date)
        except ValueError:
            flash("Enter a workout name, date, and valid exercise details.", "error")
            return render_template(
                "edit_workout.html",
                workout={"id": workout_id, "name": workout_name, "workout_date": workout_date},
                exercises=submitted_exercises,
                weight_unit=unit,
            )

        with connect_to_database() as connection:
            connection.execute(
                "UPDATE workouts SET name = ?, workout_date = ? WHERE id = ? AND user_id = ?",
                (workout_name, workout_date, workout_id, user),
            )
            connection.execute("DELETE FROM exercises WHERE workout_id = ?", (workout_id,))
            connection.executemany(
                """INSERT INTO exercises (workout_id, name, sets, reps, weight)
                   VALUES (?, ?, ?, ?, ?)""",
                [(workout_id, *exercise) for exercise in exercises_to_save],
            )

        flash("Workout updated.", "success")
        return redirect(url_for("dashboard", _anchor="history"))

    return render_template(
        "edit_workout.html", workout=workout, exercises=display_exercises, weight_unit=unit
    )


@app.route("/", methods=["GET", "POST"])
def dashboard():
    user = current_user()
    if not user:
        return render_template("pick_user.html", users=USERS)
    unit = WEIGHT_UNITS[user]

    if request.method == "POST":
        workout_name = request.form.get("workout_name", "").strip()
        workout_date = request.form.get("workout_date", "").strip()
        exercise_names = request.form.getlist("exercise_name")
        sets_values = request.form.getlist("sets")
        reps_values = request.form.getlist("reps")
        weights = request.form.getlist("weight")

        exercises_to_save = []
        try:
            for name, sets, reps, weight in zip(exercise_names, sets_values, reps_values, weights):
                name = name.strip()
                if not name:
                    continue
                sets = int(sets)
                reps = int(reps)
                weight = display_to_kg(float(weight), unit)
                if sets < 1 or reps < 1 or weight < 0:
                    raise ValueError
                exercises_to_save.append((name, sets, reps, weight))
        except ValueError:
            flash("Enter positive sets and reps, and a weight of zero or more.", "error")
            return redirect(url_for("dashboard"))

        if not workout_name or not workout_date or not exercises_to_save:
            flash("Add a workout name, date, and at least one exercise.", "error")
            return redirect(url_for("dashboard"))

        try:
            date.fromisoformat(workout_date)
        except ValueError:
            flash("Choose a valid workout date.", "error")
            return redirect(url_for("dashboard"))

        with connect_to_database() as connection:
            workout = connection.execute(
                "INSERT INTO workouts (name, workout_date, user_id) VALUES (?, ?, ?)",
                (workout_name, workout_date, user),
            )
            connection.executemany(
                """INSERT INTO exercises (workout_id, name, sets, reps, weight)
                   VALUES (?, ?, ?, ?, ?)""",
                [(workout.lastrowid, *exercise) for exercise in exercises_to_save],
            )
        flash("Workout saved.", "success")
        return redirect(url_for("dashboard"))

    search_term = request.args.get("search", "").strip()
    search_pattern = f"%{search_term}%"

    with connect_to_database() as connection:
        workout_rows = connection.execute(
            """SELECT w.id, w.name, w.workout_date
               FROM workouts AS w
               WHERE w.user_id = ? AND (? = '' OR w.name LIKE ? OR EXISTS (
                   SELECT 1 FROM exercises AS match_e
                   WHERE match_e.workout_id = w.id AND match_e.name LIKE ?
               ))
               ORDER BY w.workout_date DESC, w.id DESC
               LIMIT 20""",
            (user, search_term, search_pattern, search_pattern),
        ).fetchall()

        exercises_by_workout = {}
        if workout_rows:
            workout_ids = [row["id"] for row in workout_rows]
            placeholders = ",".join("?" * len(workout_ids))
            for row in connection.execute(
                f"""SELECT workout_id, name, sets, reps, weight FROM exercises
                    WHERE workout_id IN ({placeholders}) ORDER BY id""",
                workout_ids,
            ):
                exercises_by_workout.setdefault(row["workout_id"], []).append(row)

        workouts = [
            {
                "id": row["id"],
                "name": row["name"],
                "workout_date": row["workout_date"],
                "exercise_details": ", ".join(
                    f"{ex['name']} ({ex['sets']}x{ex['reps']} @ "
                    f"{format_weight(kg_to_display(ex['weight'], unit))} {unit})"
                    for ex in exercises_by_workout.get(row["id"], [])
                ),
            }
            for row in workout_rows
        ]

        exercise_rows = connection.execute(
            """SELECT e.name, MAX(e.weight) AS best_weight
               FROM exercises AS e
               JOIN workouts AS w ON w.id = e.workout_id
               WHERE w.user_id = ?
               GROUP BY LOWER(e.name)
               ORDER BY best_weight DESC, e.name COLLATE NOCASE
               LIMIT 3""",
            (user,),
        ).fetchall()
        personal_records = [
            {"name": row["name"], "best_weight": kg_to_display(row["best_weight"], unit)}
            for row in exercise_rows
        ]
        all_workout_dates = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT workout_date FROM workouts WHERE user_id = ? ORDER BY workout_date DESC",
                (user,),
            ).fetchall()
        ]
        month = date.today().strftime("%Y-%m")
        month_stats = connection.execute(
            """SELECT COUNT(*) AS workouts,
                      COUNT(DISTINCT workout_date) AS visits
               FROM workouts WHERE user_id = ? AND substr(workout_date, 1, 7) = ?""",
            (user, month),
        ).fetchone()
        workout_count = connection.execute(
            "SELECT COUNT(*) FROM workouts WHERE user_id = ?", (user,)
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