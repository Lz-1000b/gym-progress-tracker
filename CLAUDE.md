# Frontend design conventions

Scope: `templates/*.html`, `static/style.css`. Read this before touching any UI.

## Visual language

- Dark theme only, no light mode. Every color is a custom property defined once in `static/style.css` (`:root`, top of file): `--ink`, `--muted`, `--line`, `--paper`, `--white`, `--pink`, `--pink-deep`, `--blue`, `--blue-soft`.
- **Never introduce a new color or hardcode a new hex value.** Reuse one of the existing tokens. If nothing fits, ask the user first — color/theme changes need explicit sign-off on this project, they don't get decided mid-task.
- Fonts: `'Manrope'` for headings, numbers, and anything meant to stand out (h1/h2, stat values, workout dates); `'DM Sans'` for body text. Both come from the single Google Fonts `@import` at the top of `style.css` — don't add another font.
- No CSS framework (no Tailwind/Bootstrap/etc.) and no JS framework/build step. Everything is hand-rolled in one `static/style.css` and inline `<script>` tags. Keep it that way unless the user asks to change it.

## Page structure (every template follows this shape)

1. `<head>`: charset, viewport meta, `<link rel="icon">` to `favicon.ico`, then the stylesheet link. Copy this block verbatim from an existing template for a new page rather than retyping it.
2. `<header class="topbar">`: brand logo linking to the dashboard, current user's name, and a "Switch user" form posting to `clear_user`. Every page needs this except `pick_user.html` (shown before a profile is picked).
3. `<main class="page-shell">`: centered max-width container holding everything else.
4. `.welcome-row`: page title (`<h1>`) + one primary action link/button.
5. Flashed messages block (`get_flashed_messages`) — copy this verbatim too, it's identical on every page.
6. Page-specific content.
7. `.page-footer`.

## Reusable components — use these, don't invent new ones

- `.section-block.log-section` — the one card style (bordered, rounded, `var(--white)` background) used for every discrete block of content (forms, exercise lists, records).
- `.form-row` — standard two-column label+input grid for short forms (name + date, etc.).
- `.save-button` — the one primary-action button style, used for submits, repeat-workout chips, everything. Don't style a new button variant for "primary action" — reuse this class.
- `.primary-link` — secondary/header-level action link (e.g. "+ Log a workout", "Back to dashboard").

## Mobile rules — hard requirements, not suggestions

These came from real bugs hit and fixed while building this app; check every new interactive element against both before calling a change done.

- **Every text/number input must be 16px font-size or larger.** Anything smaller triggers iOS Safari's auto-zoom-on-focus. See `.form-row input`, `.next-set-row input`, `.add-exercise-row input`.
- **Every interactive control needs a 44px minimum tap target** (`min-height: 44px`, with padding to match if the visible content is smaller). See `.save-button`, `.log-set-btn`, `.workout-actions button`.
- Two breakpoints exist: `800px` (tablet — multi-column layouts collapse to one) and `560px` (phone — tighter spacing/type). Add rules to these existing media queries at the bottom of `style.css` rather than inventing a new breakpoint.

## JavaScript conventions

- Vanilla JS only, inline `<script>` at the bottom of the template's `<body>`. No npm, no bundler.
- AJAX (see `log_workout.html`) is plain `fetch()` with JSON request/response bodies. The app has no CSRF protection anywhere — don't add fetch headers assuming a token exists.
- Dynamic UI updates use **event delegation on `document`** keyed off `data-*` attributes (`data-log-set`, `data-undo-set`, `data-remove-exercise`, etc.), not per-element `addEventListener` calls. This means elements inserted later by JS (e.g. a newly added exercise block) work immediately with no extra wiring. Follow this pattern for any new dynamic behavior.
