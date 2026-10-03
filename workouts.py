from datetime import date, timedelta

from users import format_weight, kg_to_display


HAS_LOGGED_SETS = """EXISTS (
    SELECT 1 FROM exercise_sets es
    JOIN workout_exercises we ON we.id = es.workout_exercise_id
    WHERE we.workout_id = w.id
)"""


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


def copy_exercises(connection, user, source_workout_id, new_workout_id):
    """Copy the exercise list (no sets) from one of this user's workouts into a new one."""
    if not get_owned_workout(connection, user, source_workout_id):
        return

    source_exercises = connection.execute(
        """SELECT name, position FROM workout_exercises
           WHERE workout_id = ? ORDER BY position""",
        (source_workout_id,),
    ).fetchall()
    connection.executemany(
        """INSERT INTO workout_exercises (workout_id, name, position)
           VALUES (?, ?, ?)""",
        [(new_workout_id, ex["name"], ex["position"]) for ex in source_exercises],
    )


def get_workout_exercises(connection, user, workout_id, unit):
    """A workout's exercises with their logged sets and next-set prefill, in display units."""
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
    return exercises


def search_workouts(connection, user, search_term, unit):
    """This user's 20 most recent logged workouts matching a workout or exercise name."""
    search_pattern = f"%{search_term}%"
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

    return [
        {
            "id": row["id"],
            "name": row["name"],
            "workout_date": row["workout_date"],
            "exercise_details": build_exercise_details(row["id"]),
        }
        for row in workout_rows
    ]


def get_personal_records(connection, user, unit):
    """This user's three heaviest lifts, one per exercise, in display units."""
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
    return [
        {"name": row["name"], "best_weight": kg_to_display(row["best_weight"], unit)}
        for row in record_rows
    ]


def get_dashboard_stats(connection, user):
    """Month counts, total count, and current streak over this user's logged workouts."""
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

    return {
        "workouts_this_month": month_stats["workouts"],
        "visits_this_month": month_stats["visits"],
        "current_streak": get_current_streak(all_workout_dates),
        "total_workouts": workout_count,
    }
