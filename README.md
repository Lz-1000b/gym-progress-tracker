# GymTrack

GymTrack is a small workout log built with Python, Flask, SQLite, HTML, and CSS. This first version lets you save workouts with exercises, sets, reps, and weights, search your workout history by exercise, and see a few useful stats.

## Run it locally

1. Install Python 3.10 or newer.
2. From this folder, create and activate a virtual environment:

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

    On Windows, activate it with `.venv\\Scripts\\activate`.
3. Install Flask: `python -m pip install -r requirements.txt`.
4. Start the app: `python app.py`.
5. Open <http://127.0.0.1:5000> in your browser.

The app creates `gymtrack.db` in this folder the first time it runs. That file is your local workout database; keep it safe if you want to keep your saved data.

## How the code is organized

- `app.py` creates the Flask app and holds every route. Routes read the request, validate it, and call into the modules below for data.
- `db.py` opens SQLite connections, creates the tables, and migrates older database schemas.
- `users.py` holds the profiles, each profile's weight unit, and the kg/lb conversions.
- `workouts.py` holds the queries: workout search, personal records, dashboard stats, and set prefills.
- `templates/` has one HTML file per page. Flask fills the template placeholders with workouts and stats from the database.
- `static/style.css` controls the page layout, colors, and responsive behavior.
- `requirements.txt` lists the one Python package the app needs.

SQLite keeps this learning project self-contained: data is stored in a local file, so no separate database server or account system is needed. This MVP is for local use and does not include authentication, notifications, or AI recommendations yet.
