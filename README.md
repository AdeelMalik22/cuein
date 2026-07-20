# Cuein

Cuein is a lightweight lead follow-up tool for small service businesses. It helps a team capture leads, assign an owner, move opportunities through a fixed pipeline, record each interaction, and keep the next action visible so opportunities are not forgotten.

The application has two first-class interfaces:

- A responsive, server-rendered Django workspace for day-to-day lead and follow-up work.
- A JWT-protected REST API under `/api/v1/` for integrations and programmatic access.

## What is implemented

- Multi-tenant businesses with owner, manager, and salesperson roles, including per-business memberships and workspace switching for shared staff.
- Email-confirmed business registration: no `Business` or owner `User` is created until the owner enters the expiring six-digit email code.
- Password recovery by emailed, expiring six-digit reset code; codes are hashed, single-use, and locked after five incorrect attempts.
- A seven-stage lead pipeline: New inquiry, Contacted, Site visit, Quotation sent, Negotiation, Won, and Lost.
- Lead quick-add, search/filtering, assignment, editing, activity timeline, and validated lost reasons.
- Profile editing with optional profile photos, plus a photo-and-name assignee picker with a fallback avatar.
- A Kanban board with desktop drag-and-drop, internally scrollable columns, and role-scoped visibility.
- Follow-up tasks with pending, overdue, done, and cancelled states; complete, reschedule, and cancel workflows.
- A notification centre for overdue follow-ups, with All/Unread filters, read state, and an unread navigation badge.
- Repeatable site-visit appointments with date/time, optional address, assigned salesperson, completed/cancelled status, and an optional reminder task one hour before the visit.
- A role-scoped day/week visit calendar: owners and managers see the active business; salespeople see only their own appointments.
- Celery-backed follow-up scheduling plus overdue-task and active-membership stale-lead sweeps.
- Business-local due-date filtering with validated IANA time zones, and transactional task completion, rescheduling, and cancellation that keep task history and alerts in sync.
- Owner/manager dashboard, source-conversion and stage analytics, reports, and responsive navigation with a collapsible desktop sidebar.
- Production baseline configuration: WhiteNoise static files, local development media storage, Gunicorn, Docker Compose, health/readiness probes, and request-ID-aware logging.

## Multi-tenancy and workspaces

One running application serves independent businesses. Each `Business` is a tenant.

- Every tenant-owned model has a required `business` relationship through `TenantScopedModel`.
- A global `User` can have one or more active `Membership` records. A membership carries that person's role for one business.
- Web requests resolve the active workspace from a validated server-side session value. The sidebar switcher only lists active memberships and every switch is re-validated server-side.
- Owners can create another business directly from that switcher; Cuein creates an owner membership and enters the new workspace without changing the legacy `User.business` bridge.
- JWTs are scoped to exactly one business. A person with multiple memberships must include `business_id` when requesting a token; the API never trusts a mutable client-supplied workspace header.
- API and web queries begin with the resolved active business, not the legacy `User.business` compatibility field.
- Related products, leads, tasks, and users are validated against the same business.
- Stale-lead reminders select an active owner or manager membership in the selected business. They never rely on a legacy user-to-business field to choose a recipient.
- Owner-level membership changes are transactional and lock the workspace's membership rows so concurrent requests cannot demote, deactivate, or remove the last active owner.
- Cross-tenant object lookups return `404` rather than exposing another tenant’s data or object existence.

The `User.business` and `User.role` fields remain temporarily for a safe production rollout. Running `migrate` performs an idempotent membership backfill; operators can also run `python3 manage.py backfill_memberships` to verify or repeat it safely.

## Lead assignment

Owners and managers can choose an assignee while creating or editing a lead. The picker shows each eligible teammate's profile photo (or the default avatar), name, and email/username; it also works when JavaScript is unavailable.

Only active members of the **currently selected workspace** are eligible. If a person is not shown, first make them an active team member of that business; Cuein deliberately never assigns a lead across business boundaries. Salespeople create leads assigned to themselves and cannot reassign leads.

## Notifications, site visits, and business time

Overdue follow-ups create in-app notifications for the assigned person. The
Notifications page is scoped to the current workspace and signed-in recipient;
it provides All and Unread filters, lets a person mark an alert as read, and
shows the current unread total in the navigation.

Cuein stores timestamps in UTC, but uses each business's validated IANA time
zone when calculating calendar-based views such as follow-ups due today. Task
completion, rescheduling, and cancellation lock the open task and update its
status, alert state, and lead timeline together, so an incomplete action is not
partially saved.

Site visits are separate appointment records, so a lead can have an initial
inspection and later measurement visit without overwriting history. Moving a
lead to the Site visit stage prompts the user to schedule an appointment but
does not require one. Completing or cancelling a visit automatically records a
timeline entry; an optional reminder becomes a normal Follow-up task one hour
before the visit. External calendar sync, route planning, and customer
confirmation messages are intentionally outside this v1 workflow.

## API overview

| Area | Endpoint |
| --- | --- |
| Sign up | `POST /api/v1/auth/signup/` |
| Verify signup code | `POST /api/v1/auth/verify-email/` |
| Resend signup code | `POST /api/v1/auth/verify-email/resend/` |
| Request password-reset code | `POST /api/v1/auth/password-reset/request/` |
| Confirm password reset | `POST /api/v1/auth/password-reset/confirm/` |
| JWT login | `POST /api/v1/auth/token/` |
| Refresh JWT | `POST /api/v1/auth/token/refresh/` |
| Current user | `GET /api/v1/me/` |
| Current business | `GET/PATCH /api/v1/business/` |
| Team users | `/api/v1/users/` |
| Products/services | `/api/v1/products/` |
| Leads | `/api/v1/leads/` |
| Assign lead | `POST /api/v1/leads/{id}/assign/` |
| Change stage | `POST /api/v1/leads/{id}/transition/` |
| Request follow-up time | `POST /api/v1/leads/{id}/needs-time/` |
| Follow-up tasks | `/api/v1/follow-up-tasks/` |
| Site visits | `GET/POST /api/v1/site-visits/`, `POST /api/v1/site-visits/{id}/reschedule/`, `.../{id}/complete/`, `.../{id}/cancel/` |
| Notifications | `GET /api/v1/notifications/`, `POST /api/v1/notifications/{id}/read/` |

All API routes require JWT authentication except signup, email-code verification, password-reset request/confirmation, token creation, and token refresh. API signup creates only a temporary pending registration and returns a verification-required response; the business, owner account, and JWT access become available only after the owner enters the emailed six-digit code.

To reset a password through the API, request a code with an email address, then submit that email, the six-digit code, and the new password:

```json
POST /api/v1/auth/password-reset/confirm/
{
  "email": "owner@example.com",
  "code": "123456",
  "new_password": "your-new-strong-password"
}
```

For an account with more than one workspace, obtain a token for a specific business:

```json
{
  "username": "shared-user",
  "password": "your-password",
  "business_id": "workspace-uuid"
}
```

## Local setup

1. Ensure PostgreSQL is running and create the configured database/user.
2. Copy the safe example configuration, then replace its local PostgreSQL
   password and any values you need:

   ```bash
   cp .env.example .env
   ```

   The included Redis URLs work with either Redis or Valkey. Valkey uses the
   Redis protocol, so no code change is needed—point the URLs at your Valkey
   instance instead. A minimal root `.env` contains:

   ```text
   POSTGRES_DB=cuein
   POSTGRES_USER=your_user
   POSTGRES_PASSWORD=your_password
   POSTGRES_HOST=127.0.0.1
   POSTGRES_PORT=5432
   ```

   Uploaded profile pictures use the local `media/` directory in development.
   Django serves them at `/media/` while `DEBUG=true`; do not treat that local
   backend as production media storage.

   To deliver verification codes outside local development, add your SMTP provider settings. Use an app password or provider-issued SMTP credential—never a normal mailbox password—and keep this file out of version control:

   ```text
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   DEFAULT_FROM_EMAIL=Cuein <no-reply@yourdomain.com>
   EMAIL_HOST=smtp.your-provider.com
   EMAIL_PORT=587
   EMAIL_HOST_USER=your-smtp-user
   EMAIL_HOST_PASSWORD=your-smtp-app-password
   EMAIL_USE_TLS=true
   EMAIL_USE_SSL=false
   EMAIL_VERIFICATION_TIMEOUT=86400
   EMAIL_VERIFICATION_RESEND_COOLDOWN=60
   PASSWORD_RESET_TIMEOUT=900
   PASSWORD_RESET_RESEND_COOLDOWN=60
   ```

   Signup verification codes expire after 24 hours by default; password-reset codes expire after 15 minutes. A recipient can receive a replacement code once per minute by default. Change the values in seconds if needed. In local development, email uses Django’s console backend by default, so the verification code is printed in the server terminal.

   For production, configure the following as well. Setting `DJANGO_ENV=production` disables debug mode, requires a unique secret and `DJANGO_ALLOWED_HOSTS`, and enables HTTPS-only cookies, HTTPS redirect, HSTS, and secure response headers. The host list is read from that environment variable rather than hard-coded in settings:

   ```text
   DJANGO_ENV=production
   DJANGO_SECRET_KEY=generate-a-long-unique-secret
   DJANGO_ALLOWED_HOSTS=app.yourdomain.com
   DJANGO_CSRF_TRUSTED_ORIGINS=https://app.yourdomain.com
   DJANGO_BEHIND_PROXY=true
   ```

   Set `DJANGO_BEHIND_PROXY=true` only when a trusted proxy terminates TLS and supplies `X-Forwarded-Proto` and `X-Forwarded-For`. To enable the conditional CAPTCHA after repeated failed logins, add Cloudflare Turnstile keys:

   ```text
   TURNSTILE_SITE_KEY=your-turnstile-site-key
   TURNSTILE_SECRET_KEY=your-turnstile-secret-key
   ```

   HSTS is enabled for production by default. Set `DJANGO_HSTS_INCLUDE_SUBDOMAINS=true` and `DJANGO_HSTS_PRELOAD=true` only if every current and future subdomain is HTTPS-ready.

   Refresh tokens are blacklisted after password changes. Schedule `python3.10 manage.py flushexpiredtokens` once per day in production to remove expired blacklist records.

3. Install dependencies, migrate, and validate the project:

   ```bash
   python3.10 -m pip install -r requirements.txt
   python3.10 manage.py migrate
   python3.10 manage.py check
   python3.10 manage.py test
   ```

4. Start the web application:

   ```bash
   python3.10 manage.py runserver
   ```

   The workspace is available at `http://127.0.0.1:8000/` and the API at `http://127.0.0.1:8000/api/v1/`.

   Deployment probes can use `GET /healthz/` for Django process liveness and
   `GET /readyz/` for readiness. The readiness probe returns `503` until both
   PostgreSQL and the shared Redis/Valkey cache are reachable.

5. For asynchronous follow-ups, start Redis and run a Celery worker and scheduler in separate terminals:

   ```bash
   python3.10 -m celery -A cuein worker --loglevel=INFO
   python3.10 -m celery -A cuein beat --loglevel=INFO
   ```

   Celery uses `redis://127.0.0.1:6379/0` by default. Override `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` in `.env` when Redis runs elsewhere.

## Production server

Do not use `manage.py runserver` in production. After installing the pinned
dependencies, running migrations, and collecting static files, run Django with
Gunicorn behind a TLS-terminating reverse proxy or load balancer:

```bash
python3.10 manage.py migrate --noinput
python3.10 manage.py collectstatic --noinput
python3.10 -m gunicorn --bind 127.0.0.1:8000 --workers 3 \
  --access-logfile - --error-logfile - cuein.wsgi:application
```

Keep Gunicorn bound to loopback unless the host's network policy and a trusted
TLS proxy protect it. Configure the proxy to use `GET /healthz/` for liveness
and `GET /readyz/` for dependency readiness. WhiteNoise serves collected static
assets; serve uploaded `MEDIA_ROOT` files through the proxy or object storage in
production, because Django only serves media files directly while `DEBUG=true`.

Production logs use compact JSON by default and every Django response includes
an `X-Request-ID` header. Send that ID with a support report to correlate it
with server logs. Set `DJANGO_LOG_FORMAT=plain` for human-readable local logs,
or adjust `DJANGO_LOG_LEVEL` when investigating a problem.

## Container stack

For a repeatable single-host stack, copy `.env.example` to `.env`, set a real
PostgreSQL password, then run:

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f web worker beat
```

Compose starts PostgreSQL and Valkey first, runs migrations and `collectstatic`
once, then starts Gunicorn, Celery worker, and Celery Beat. PostgreSQL and
Valkey are intentionally private to the Compose network; only Gunicorn is
published, and it is bound to `127.0.0.1:8000` for a host reverse proxy. This
also means a containerized Valkey installation is checked with `docker compose
ps`, not `systemctl status redis`.

Use a production `.env` with the required `DJANGO_ENV`, secret, host, and SMTP
settings from the local setup section before exposing the proxy publicly.
The Compose files are deployment configuration only; they do not start or
manage services until an operator runs the commands above.

## Important development rule

Never hand-write a Django migration. After model changes, generate it with:

```bash
python3.10 manage.py makemigrations
```

See `PRD-FollowUpCRM.md` for the product requirements, `plan.md` for delivery status, `CLAUDE.md` for the repository working guide, and `understanding.md` for the multi-business architecture and request flows.

## Progress so far

- The tenant-safe lead and follow-up workflow is usable end-to-end through both the web workspace and REST API.
- Automation services, dashboards, reports, and role-scoped management views are implemented.
- Workspace switching, owner-created businesses, business-scoped JWTs, profile photos, and reliable visual lead assignment are implemented.
- Dashboard analytics and the Smith LLC demo dataset make it possible to inspect realistic pipeline and follow-up states locally.
- The notification centre, business-timezone due-date calculation, active-membership stale reminders, and transactional task actions are implemented.
- Site visits support multiple appointments per lead, optional one-hour follow-up reminders, activity history, and role-scoped day/week calendar views.
- Static/media storage, environment-driven allowed hosts, Gunicorn, WhiteNoise, Docker Compose, health/readiness probes, and request-ID-aware logs are configured.
- Current hardening priorities are broader automated coverage, CI and live-deployment verification, and pilot validation of reminder defaults.
