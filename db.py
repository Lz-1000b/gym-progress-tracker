import sqlite3
from pathlib import Path


DATABASE = Path(__file__).with_name("gymtrack.db")


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

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                weekly_goal INTEGER NOT NULL
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
