# Secure Facility Portal

A web portal for tracking personnel checking in and out of a secure facility. FastAPI + SQLite + Jinja, packaged as a Docker container with a named volume for persistence.

## Quick start

```bash
docker compose up -d --build
# open http://127.0.0.1:8590
```

First run seeds an admin account (`admin` / `admin123` — change it after first login) and a demo roster of 10 contractors across 5 firms.

## Features

- **Live dashboard** — count of people currently inside, auto-refreshes every 30 s (pauses while a form field has unsaved changes or the check-in modal is open).
- **Roster** — personnel prepopulated with name, ID number, and contractor firm; add/edit/delete from the dashboard.
- **Check in / check out** — check-in is only possible for active roster members; a popup asks for the visitor's badge number, which is stored per visit and shown on the Check Out button and in History.
- **Search** — strict substring match on name, ID, or firm, with a Clear button. "Inside now" is independent of the search filter and sorted newest check-in first.
- **History** — full audit trail of visits (ID, visitor badge, name, firm, check-in, check-out, dwell time).
- **Deletion rule** — a person can only be deleted if they have no check-in history; otherwise deactivate them. Deactivate is hidden for people currently inside.
- **Roles** — permission-based, enforced server-side on every route and reflected in the UI:

| Role | Check in/out | Add/edit/delete/deactivate people | Manage users | View dashboard/history |
|---|---|---|---|---|
| admin | yes | yes | yes | yes |
| police | yes | no | no | yes |
| security | no | yes | no | yes |
| viewer | no | no | no | yes |

- **Auth** — PBKDF2 (SHA-256, 200k iterations) password hashing, signed HttpOnly session cookies with 12 h expiry, session secret generated on first run.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `TZ` | `America/New_York` | Timestamps displayed in this timezone; stored as UTC. |
| `DATA_DIR` | `/data` | Where `portal.db` and `secret.key` live (named volume `portal_data`). |

Port mapping is `8590:8590` in `docker-compose.yml` — change it if the port is occupied.

## Project layout

```
facility-portal/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── app/
    ├── main.py          # FastAPI app: DB, auth, roles, routes
    └── templates/
        ├── base.html    # layout, nav, styles
        ├── login.html
        ├── dashboard.html
        ├── history.html
        ├── users.html
        └── edit.html
```

## Database schema

- `users` — id, username, password_hash, role (`admin|viewer|police|security`)
- `personnel` — id, badge (unique ID), name, firm, active flag
- `visits` — id, personnel_id, badge, name, firm, visitor_badge, checked_in_at, checked_out_at (open visit = `checked_out_at IS NULL`)

Older databases are migrated additively on startup (missing columns/role constraints are rebuilt without data loss).

## Notes

- The seeded `admin123` password is a demo convenience — change it immediately via the Users page in any real deployment.
- Single-file app, no ORM, no external services: everything runs inside the container against the SQLite file on the volume.
