import sqlite3
from datetime import date, timedelta
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for


app = Flask(__name__)
app.secret_key = "local-development-key"
DATABASE = Path(__file__).with_name("gymtrack.db")


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
                workout_date TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                sets INTEGER NOT NULL,
                reps INTEGER NOT NULL,
                weight REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL
            );
            """
        )


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


@app.route("/profile", methods=["POST"])
def save_profile():
    name = request.form.get("name", "").strip()
    if not name or len(name) > 40:
        flash("Enter a name up to 40 characters.", "error")
        return redirect(url_for("dashboard"))

    with connect_to_database() as connection:
        connection.execute(
            """INSERT INTO user_profile (id, name) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET name = excluded.name""",
            (name,),
        )
    return redirect(url_for("dashboard"))


@app.route("/workouts/<int:workout_id>/delete", methods=["POST"])
def delete_workout(workout_id):
    with connect_to_database() as connection:
        result = connection.execute(
            "DELETE FROM workouts WHERE id = ?",
            (workout_id,),
        )

    if result.rowcount:
        flash("Workout deleted.", "success")
    else:
        flash("Workout not found.", "error")
    return redirect(url_for("dashboard", _anchor="history"))


@app.route("/workouts/<int:workout_id>/edit", methods=["GET", "POST"])
def edit_workout(workout_id):
    with connect_to_database() as connection:
        workout = connection.execute(
            "SELECT id, name, workout_date FROM workouts WHERE id = ?",
            (workout_id,),
        ).fetchone()
        exercise_rows = connection.execute(
            """SELECT name, sets, reps, weight FROM exercises
               WHERE workout_id = ? ORDER BY id""",
            (workout_id,),
        ).fetchall()

    if not workout:
        flash("Workout not found.", "error")
        return redirect(url_for("dashboard", _anchor="history"))

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
                weight = float(weight)
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
            )

        with connect_to_database() as connection:
            connection.execute(
                "UPDATE workouts SET name = ?, workout_date = ? WHERE id = ?",
                (workout_name, workout_date, workout_id),
            )
            connection.execute("DELETE FROM exercises WHERE workout_id = ?", (workout_id,))
            connection.executemany(
                """INSERT INTO exercises (workout_id, name, sets, reps, weight)
                   VALUES (?, ?, ?, ?, ?)""",
                [(workout_id, *exercise) for exercise in exercises_to_save],
            )

        flash("Workout updated.", "success")
        return redirect(url_for("dashboard", _anchor="history"))

    return render_template("edit_workout.html", workout=workout, exercises=exercise_rows)


@app.route("/", methods=["GET", "POST"])
def dashboard():
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
                weight = float(weight)
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
                "INSERT INTO workouts (name, workout_date) VALUES (?, ?)",
                (workout_name, workout_date),
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
        profile = connection.execute(
            "SELECT name FROM user_profile WHERE id = 1"
        ).fetchone()
        workout_rows = connection.execute(
            """SELECT w.id, w.name, w.workout_date,
                      GROUP_CONCAT(
                          e.name || ' (' || e.sets || 'x' || e.reps || ' @ ' ||
                          printf('%g', e.weight) || ' kg)', ', '
                      ) AS exercise_details
               FROM workouts AS w
               JOIN exercises AS e ON e.workout_id = w.id
               WHERE (? = '' OR w.name LIKE ? OR EXISTS (
                   SELECT 1 FROM exercises AS match_e
                   WHERE match_e.workout_id = w.id AND match_e.name LIKE ?
               ))
               GROUP BY w.id
               ORDER BY w.workout_date DESC, w.id DESC
               LIMIT 20""",
            (search_term, search_pattern, search_pattern),
        ).fetchall()

        exercise_rows = connection.execute(
            """SELECT e.name, MAX(e.weight) AS best_weight
               FROM exercises AS e
               GROUP BY LOWER(e.name)
               ORDER BY best_weight DESC, e.name COLLATE NOCASE
               LIMIT 3"""
        ).fetchall()
        all_workout_dates = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT workout_date FROM workouts ORDER BY workout_date DESC"
            ).fetchall()
        ]
        month = date.today().strftime("%Y-%m")
        month_stats = connection.execute(
            """SELECT COUNT(*) AS workouts,
                      COUNT(DISTINCT workout_date) AS visits
               FROM workouts WHERE substr(workout_date, 1, 7) = ?""",
            (month,),
        ).fetchone()
        workout_count = connection.execute("SELECT COUNT(*) FROM workouts").fetchone()[0]

    return render_template(
        "index.html",
        profile_name=profile["name"] if profile else None,
        workouts=workout_rows,
        personal_records=exercise_rows,
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