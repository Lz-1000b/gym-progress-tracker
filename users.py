from flask import session


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
