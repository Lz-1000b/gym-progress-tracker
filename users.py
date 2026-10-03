from flask import session


USERS = {"leaha": "Leaha", "uzair": "Uzair"}
WEIGHT_UNITS = {"leaha": "kg", "uzair": "lb"}
KG_PER_LB = 0.45359237
DEFAULT_WEEKLY_GOAL = 3
WEEKLY_GOAL_CHOICES = range(1, 8)


def current_user():
    """Return the session's chosen user key, or None if not picked yet."""
    key = session.get("current_user")
    return key if key in USERS else None


def get_weekly_goal(connection, user):
    """How many days a week this user aims to train."""
    row = connection.execute(
        "SELECT weekly_goal FROM user_settings WHERE user_id = ?", (user,)
    ).fetchone()
    return row["weekly_goal"] if row else DEFAULT_WEEKLY_GOAL


def set_weekly_goal(connection, user, goal):
    connection.execute(
        """INSERT INTO user_settings (user_id, weekly_goal) VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET weekly_goal = excluded.weekly_goal""",
        (user, goal),
    )


def kg_to_display(weight_kg, unit):
    """Convert a weight stored in kg to the given display unit."""
    return weight_kg / KG_PER_LB if unit == "lb" else weight_kg


def display_to_kg(weight, unit):
    """Convert a weight entered in the given display unit back to kg for storage."""
    return weight * KG_PER_LB if unit == "lb" else weight


def format_weight(value):
    """Format a weight without trailing zeros, matching the old SQL printf('%g', ...)."""
    return "%g" % value
