from calendar import Calendar
from collections import Counter
from datetime import date, timedelta

from users import format_weight, kg_to_display
from workouts import HAS_LOGGED_SETS


WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TREND_WINDOW_DAYS = 90
TREND_SESSIONS = 8


def start_of_week(day):
    """The Monday of the week containing the given day."""
    return day - timedelta(days=day.weekday())


def format_display_weight(weight_kg, unit):
    """A stored kg weight as display text in the user's unit, to one decimal place."""
    return format_weight(round(kg_to_display(weight_kg, unit), 1))


def count_workouts(connection, user):
    return connection.execute(
        f"SELECT COUNT(*) FROM workouts AS w WHERE user_id = ? AND {HAS_LOGGED_SETS}",
        (user,),
    ).fetchone()[0]


def get_training_days(connection, user):
    """Every calendar day on which this user logged at least one set."""
    return {
        date.fromisoformat(row[0])
        for row in connection.execute(
            f"""SELECT DISTINCT workout_date FROM workouts AS w
                WHERE user_id = ? AND {HAS_LOGGED_SETS}""",
            (user,),
        )
    }


def get_last_workout(connection, user, today):
    """Name of this user's most recent logged workout and how many days ago it was."""
    row = connection.execute(
        f"""SELECT name, workout_date FROM workouts AS w
            WHERE user_id = ? AND workout_date <= ? AND {HAS_LOGGED_SETS}
            ORDER BY workout_date DESC, id DESC LIMIT 1""",
        (user, today.isoformat()),
    ).fetchone()
    if not row:
        return None
    return {
        "name": row["name"],
        "days_ago": (today - date.fromisoformat(row["workout_date"])).days,
    }


def week_overview(training_days, goal, today):
    """This week's Mon-Sun strip, progress to the weekly goal, and the week streak.

    The streak counts consecutive weeks that reached the goal. The current week
    adds to it once the goal is reached, and never breaks it while in progress.
    """
    week_start = start_of_week(today)
    days_per_week = Counter(start_of_week(day) for day in training_days)

    days = []
    for offset, label in enumerate(WEEKDAY_LABELS):
        day = week_start + timedelta(days=offset)
        days.append(
            {
                "label": label,
                "trained": day in training_days,
                "is_today": day == today,
                "is_future": day > today,
            }
        )

    days_trained = days_per_week[week_start]
    streak = 1 if days_trained >= goal else 0
    earlier_week = week_start - timedelta(days=7)
    while days_per_week[earlier_week] >= goal:
        streak += 1
        earlier_week -= timedelta(days=7)

    return {
        "days": days,
        "days_trained": days_trained,
        "goal": goal,
        "remaining": max(goal - days_trained, 0),
        "streak": streak,
    }


def month_calendar(training_days, today):
    """The current month as Mon-Sun weeks, with training days marked."""
    weeks = [
        [
            {
                "day": day.day,
                "in_month": day.month == today.month,
                "trained": day in training_days,
                "is_today": day == today,
                "is_future": day > today,
            }
            for day in week
        ]
        for week in Calendar(firstweekday=0).monthdatescalendar(today.year, today.month)
    ]
    return {
        "title": today.strftime("%B %Y"),
        "weeks": weeks,
        "days_trained": sum(
            1 for day in training_days if (day.year, day.month) == (today.year, today.month)
        ),
    }


def describe_change(current, previous, as_percent=False):
    """Direction and text for how a weekly number moved against last week."""
    if current == previous:
        return {"direction": "same", "text": "Same as last week"}
    direction = "up" if current > previous else "down"
    arrow = "▲" if direction == "up" else "▼"
    if not as_percent:
        amount = format_weight(abs(current - previous))
    elif previous:
        amount = f"{abs(current - previous) / previous * 100:.0f}%"
    else:
        return {"direction": "up", "text": "▲ Nothing logged last week"}
    return {"direction": direction, "text": f"{arrow} {amount} vs last week"}


def get_week_comparison(connection, user, unit, today):
    """Training days, sets and volume for this week, each compared with last week."""
    this_week = start_of_week(today)
    last_week = this_week - timedelta(days=7)
    rows = connection.execute(
        """SELECT w.workout_date, COUNT(es.id) AS sets, SUM(es.reps * es.weight) AS volume
           FROM exercise_sets AS es
           JOIN workout_exercises AS we ON we.id = es.workout_exercise_id
           JOIN workouts AS w ON w.id = we.workout_id
           WHERE w.user_id = ? AND w.workout_date >= ? AND w.workout_date < ?
           GROUP BY w.workout_date""",
        (user, last_week.isoformat(), (this_week + timedelta(days=7)).isoformat()),
    ).fetchall()

    totals = {this_week: {"days": 0, "sets": 0, "volume": 0.0}, last_week: {"days": 0, "sets": 0, "volume": 0.0}}
    for row in rows:
        week = totals[start_of_week(date.fromisoformat(row["workout_date"]))]
        week["days"] += 1
        week["sets"] += row["sets"]
        week["volume"] += row["volume"]

    current, previous = totals[this_week], totals[last_week]
    current_volume = round(kg_to_display(current["volume"], unit))
    previous_volume = round(kg_to_display(previous["volume"], unit))
    return [
        {
            "label": "TRAINING DAYS",
            "value": current["days"],
            "unit": "days",
            "change": describe_change(current["days"], previous["days"]),
        },
        {
            "label": "SETS LOGGED",
            "value": current["sets"],
            "unit": "sets",
            "change": describe_change(current["sets"], previous["sets"]),
        },
        {
            "label": "WEIGHT LIFTED",
            "value": f"{current_volume:,}",
            "unit": unit,
            "change": describe_change(current_volume, previous_volume, as_percent=True),
        },
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


def get_exercise_daily_bests(connection, user):
    """Each exercise's heaviest set per training day, oldest first.

    Returns {lowercased name: {"name": display name, "sessions": [(day, best weight in kg)]}}.
    """
    exercises = {}
    for row in connection.execute(
        """SELECT we.name, w.workout_date, MAX(es.weight) AS best_weight
           FROM exercise_sets AS es
           JOIN workout_exercises AS we ON we.id = es.workout_exercise_id
           JOIN workouts AS w ON w.id = we.workout_id
           WHERE w.user_id = ?
           GROUP BY LOWER(we.name), w.workout_date
           ORDER BY w.workout_date""",
        (user,),
    ):
        exercise = exercises.setdefault(row["name"].lower(), {"name": row["name"], "sessions": []})
        exercise["name"] = row["name"]
        exercise["sessions"].append((date.fromisoformat(row["workout_date"]), row["best_weight"]))
    return exercises


def recent_records(daily_bests, unit, limit=5):
    """The latest time each exercise beat its previous best, newest first.

    An exercise's first session sets a baseline and is not counted as a record.
    """
    records = []
    for exercise in daily_bests.values():
        best_so_far = None
        latest = None
        for day, weight in exercise["sessions"]:
            if best_so_far is not None and weight > best_so_far:
                latest = {
                    "name": exercise["name"],
                    "day": day,
                    "date_text": f"{day:%b} {day.day}",
                    "weight": format_display_weight(weight, unit),
                    "previous": format_display_weight(best_so_far, unit),
                }
            if best_so_far is None or weight > best_so_far:
                best_so_far = weight
        if latest:
            records.append(latest)

    records.sort(key=lambda record: record["day"], reverse=True)
    return records[:limit]


def sparkline_points(values, width=100, height=30, padding=3):
    """SVG polyline points that spread the values across a width x height box."""
    low, high = min(values), max(values)
    points = []
    for index, value in enumerate(values):
        x = width * index / (len(values) - 1)
        if high == low:
            y = height / 2
        else:
            y = height - padding - (value - low) / (high - low) * (height - 2 * padding)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def exercise_trends(daily_bests, unit, today, limit=4):
    """Best weight per session for the exercises trained most often lately."""
    window_start = today - timedelta(days=TREND_WINDOW_DAYS)
    candidates = []
    for exercise in daily_bests.values():
        recent_count = sum(1 for day, _ in exercise["sessions"] if day >= window_start)
        if recent_count and len(exercise["sessions"]) >= 2:
            candidates.append((recent_count, exercise["sessions"][-1][0], exercise))
    candidates.sort(key=lambda candidate: candidate[:2], reverse=True)

    trends = []
    for _, _, exercise in candidates[:limit]:
        sessions = exercise["sessions"][-TREND_SESSIONS:]
        weights = [round(kg_to_display(weight, unit), 1) for _, weight in sessions]
        change = round(weights[-1] - weights[0], 1)
        if change > 0:
            direction, change_text = "up", f"▲ {format_weight(change)}"
        elif change < 0:
            direction, change_text = "down", f"▼ {format_weight(-change)}"
        else:
            direction, change_text = "same", "No change"
        trends.append(
            {
                "name": exercise["name"],
                "latest": format_weight(weights[-1]),
                "first": format_weight(weights[0]),
                "session_count": len(sessions),
                "points": sparkline_points(weights),
                "direction": direction,
                "change_text": change_text,
            }
        )
    return trends
