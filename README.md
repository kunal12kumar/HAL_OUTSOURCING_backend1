# HAL Tejas Outsourcing: backend API

Django REST API and rules engine for the outsourcing workflow, with a PostgreSQL database. It serves two
separate APIs: `/api/internal/` for HAL staff and `/api/vendor/` for vendors. The frontend is a separate project.

## Run with Docker

```bash
cp .env.example .env            # set POSTGRES_PASSWORD and DJANGO_SECRET_KEY
docker compose up -d --build
docker compose exec api python manage.py bootstrap_admin --username admin --email you@example.com
curl http://127.0.0.1:8765/api/health/        # {"status": "ok"}
```

`bootstrap_admin` asks for a password (at least 10 characters) and creates the first HAL Administrator, who must
change it at first sign-in. For a throwaway demo database only, `docker compose exec api python manage.py seed_demo --force`
loads demo users and records.

Generate the two secrets with, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Settings (`.env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | required | Database password. Only applied the first time the database volume is created. |
| `DJANGO_SECRET_KEY` | required | Signs login tokens. Changing it signs everyone out. Keep it secret and stable. |
| `API_BIND` | `127.0.0.1` | Interface the API port is published on. Use `0.0.0.0` only when the frontend runs on another machine. |
| `API_PORT` | `8765` | Host port for the API. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host names the API answers to. **Add the name or IP the frontend uses to reach it**, or requests get HTTP 400. |
| `DJANGO_SSL_REDIRECT` | `0` | Leave `0` when the API is behind a TLS proxy or on a private network. |
| `VENDOR_STALE_DAYS` | `7` | Days without an update before a vendor is flagged as silent. |
| `COMPOSE_PROJECT_NAME` | `hal-backend` | Names the containers and volumes. |

## Connecting the frontend

The frontend calls this API **from its own server**, never from the browser, so no CORS configuration is needed.
In the frontend project set `BACKEND_URL` to this API, for example `http://10.0.0.5:8765`.

Same machine: keep the defaults. Separate machines:

1. Set `API_BIND=0.0.0.0` and add the API host name or IP to `DJANGO_ALLOWED_HOSTS`.
2. Allow the API port only from the frontend server in the firewall. Do not expose it to the internet.
3. Ideally terminate TLS in front of the API, or run both on a private network.

The only public endpoint is `GET /api/health/`. Everything else needs a login, and the two APIs refuse each
other's users.

## Data and backups

Data lives in two Docker volumes: `pgdata` (the database) and `media` (uploaded drawings and reports). Back up both.

```bash
docker compose exec -T db pg_dump -U hal hal > backup-$(date +%F).sql          # database
docker compose exec -T db psql -U hal hal < backup-2026-11-01.sql              # restore into an empty database
```

Reusing data from the earlier combined compose file: copy its `POSTGRES_PASSWORD` and `DJANGO_SECRET_KEY` into `.env`
and set `COMPOSE_PROJECT_NAME=hal_outsourcing`, after running `docker compose down` in the old project.

## Upgrading

```bash
git pull
docker compose up -d --build        # migrations run automatically when the API starts
```

## Run without Docker (development)

```bash
python -m venv .venv && . .venv/Scripts/activate      # Windows Git Bash; use .venv/bin/activate elsewhere
pip install -r requirements.txt
echo "DJANGO_DEBUG=1" > .env                           # uses SQLite when DATABASE_URL is not set
python manage.py migrate
python manage.py seed_demo                             # optional demo data
python manage.py runserver 8765
```

## Tests

```bash
DJANGO_DEBUG=1 python manage.py test apps
```

34 tests cover the full requirement-to-closed-order flow, role and sequence rules, the award and PNC rules, dispatch
limits, and vendor isolation. They also run inside the container:
`docker compose run --rm api python manage.py test apps`.

## Layout

| Path | Contents |
| --- | --- |
| `apps/accounts` | Users and roles |
| `apps/vendors` | Vendor register |
| `apps/workflow/services.py` | Every workflow step with its role and state checks |
| `apps/workflow/actions.py` | Which role may do what in which state |
| `apps/workflow/serializers_vendor.py` | The only fields a vendor can ever see |
| `config/settings.py` | Environment-driven settings |
