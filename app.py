import os
from datetime import date, timedelta

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from db import connect_to_database, initialize_database
from stats import (
    count_workouts,
    exercise_trends,
    get_exercise_daily_bests,
    get_last_workout,
    get_personal_records,
    get_training_days,
    get_week_comparison,
    month_calendar,
    recent_records,
    week_overview,
)
from users import (
    USERS,
    WEEKLY_GOAL_CHOICES,
    WEIGHT_UNITS,
    current_user,
    display_to_kg,
    format_weight,
    get_weekly_goal,
    kg_to_display,
    set_weekly_goal,
)
from workouts import (
    copy_exercises,
    get_owned_exercise,
    get_owned_workout,
    get_set_prefill,
    get_workout_exercises,
    recent_distinct_workouts,
    search_workouts,
)


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-development-key")
app.permanent_session_lifetime = timedelta(days=365)


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


@app.route("/settings/weekly-goal", methods=["POST"])
def update_weekly_goal():
    user = current_user()
    if not user:
        return redirect(url_for("dashboard"))

    try:
        goal = int(request.form.get("weekly_goal", ""))
    except ValueError:
        goal = None
    if goal not in WEEKLY_GOAL_CHOICES:
        flash("Choose a weekly target from 1 to 7 days.", "error")
        return redirect(url_for("dashboard"))

    with connect_to_database() as connection:
        set_weekly_goal(connection, user, goal)

    flash(f"Weekly target set to {goal} {'day' if goal == 1 else 'days'}.", "success")
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
                copy_exercises(connection, user, repeat_workout_id, new_workout_id)

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

        exercises = get_workout_exercises(connection, user, workout_id, unit)

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
    today = date.today()

    with connect_to_database() as connection:
        workouts = search_workouts(connection, user, search_term, unit)
        personal_records = get_personal_records(connection, user, unit)
        total_workouts = count_workouts(connection, user)
        weekly_goal = get_weekly_goal(connection, user)
        training_days = get_training_days(connection, user)
        last_workout = get_last_workout(connection, user, today)
        week_comparison = get_week_comparison(connection, user, unit, today)
        daily_bests = get_exercise_daily_bests(connection, user)

    return render_template(
        "index.html",
        current_user_key=user,
        current_user_name=USERS[user],
        weight_unit=unit,
        workouts=workouts,
        personal_records=personal_records,
        total_workouts=total_workouts,
        week=week_overview(training_days, weekly_goal, today),
        weekly_goal_choices=WEEKLY_GOAL_CHOICES,
        last_workout=last_workout,
        week_comparison=week_comparison,
        calendar=month_calendar(training_days, today),
        recent_records=recent_records(daily_bests, unit),
        trends=exercise_trends(daily_bests, unit, today),
        search_term=search_term,
        today=today.isoformat(),
    )


initialize_database()


if __name__ == "__main__":
    app.run(debug=True)
