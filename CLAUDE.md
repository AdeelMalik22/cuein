# Cuein — AI Working Guide

## Project purpose

Cuein is a lightweight lead follow-up and sales-memory tool for small service businesses. Its core promise is that an active lead should never be silently forgotten.

The project is a Django multi-tenant application with both a server-rendered web workspace and a JWT-protected REST API. Keep the product focused on its main loop: capture a lead, move it through the pipeline, record activity, and make the next follow-up visible.

## Current stack

- Python 3.10, Django 5.2, Django REST Framework, and Simple JWT.
- PostgreSQL through `psycopg`.
- Server-rendered Django templates with a shared CSS file and small progressive JavaScript enhancements; there is no React/Vite frontend.
- Celery with Redis or Valkey for asynchronous follow-up scheduling and periodic overdue/stale-lead sweeps.
- WhiteNoise for collected static assets and a local `media/` storage backend for uploaded profile pictures in development. Production deployments must provide their own media serving or object storage.
- Gunicorn for production WSGI serving, plus Docker Compose configuration for PostgreSQL, Valkey, web, worker, and Beat processes.
- Request-ID middleware and configurable plain/JSON logging for operational troubleshooting.
- Faker for development/demo data.

## Repository layout

```text
cuein/          Django settings, Celery configuration, and root URLs
core/           Business tenant, custom User, auth/team APIs, permissions
leads/          Product, Lead, SiteVisit, Activity models and tenant-scoped APIs
followups/      Follow-up tasks, notifications, rules, services, Celery tasks
web/            Server-rendered workspace views, templates, CSS, and JS
compose.yaml    Single-host PostgreSQL, Valkey, Gunicorn, Celery worker, and Beat configuration
Dockerfile      Production-oriented Gunicorn image definition
.env.example    Safe configuration template for local and production setup
plan.md         Delivery plan and current implementation status
understanding.md  Multi-business architecture and request-flow guide
PRD-FollowUpCRM.md  Product requirements
```

## Local commands

Run commands from the repository root using the Python environment that has the project dependencies installed:

```bash
python3.10 manage.py check
python3.10 manage.py makemigrations
python3.10 manage.py migrate
python3.10 manage.py test
python3.10 manage.py runserver
python3.10 manage.py collectstatic --noinput
```

The local PostgreSQL connection values belong in the root `.env` file. Do not commit `.env` or copy its secrets into source code, docs, or logs.

## Tenant and workspace rules — non-negotiable

- `Business` is the tenant boundary. Every tenant-owned model inherits `core.models.TenantScopedModel`, which provides its required `business` foreign key.
- `User` is a global login identity. `Membership` links a user to a business and carries the role and active status for that specific workspace. One user may have several memberships with different roles.
- `User.business` and `User.role` are temporary legacy compatibility fields only. Do not use either field to authorize new work or select a workspace.
- For web requests, `TenantWebMixin` resolves `request.session['active_business_id']` through a live, active membership. Scope web queries with `self.get_business()` / `self.get_role()`.
- For API requests, the access token contains one selected `business_id`; authentication re-validates that membership and exposes the resolved context through `core.tenancy.active_business(request)` and `active_role(request)`.
- Never accept a client-provided `business_id` from an API body, query string, URL, or mutable header as the tenant source. It is permitted only when requesting a token or switching a workspace, and must be validated against the authenticated user's active memberships server-side.
- Scope API and web querysets from the resolved active business, for example `Lead.objects.for_business(active_business(request))`.
- A newly assigned lead/task user must be an active member of the current business. Historical records may retain a removed member for audit/history.
- `SiteVisit` is tenant-owned and must be scoped exactly like leads and tasks. Salespeople can view and action only their assigned visits; owners and managers can view all visits in the active business.
- Stale-lead reminders must select an active owner or manager membership in the target business; never select a recipient from the legacy user fields or another workspace.
- Only a workspace owner can change owner-level membership details. Membership role, activation, and removal operations must lock the relevant workspace memberships inside a transaction so concurrent requests cannot remove the final active owner.
- Cross-tenant detail lookups must return `404`; never leak object existence.
- Celery/background jobs must receive and validate `business_id` explicitly because they do not have request context.
- Every new tenant-owned endpoint needs a cross-tenant isolation test; new workspace behavior also needs switch/rejection coverage.

## Roles and visibility

Roles are membership-scoped. Never use a global user role for an active-workspace permission check.

- **Owner:** manages the business, team, services, and all tenant leads/tasks.
- **Manager:** sees all tenant leads/tasks, dashboard data, and reports; owner-only settings and team management remain restricted in the web workspace.
- **Salesperson:** sees and edits only assigned leads/tasks and cannot reassign them.

## Current product surfaces

- Public landing page, signup, onboarding, login/logout, profile editing, and business settings.
- A workspace switcher that shows the active business; owners can create and enter another business from it.
- Lead quick-add with a photo-and-name assignee dropdown, fallback avatar, editable detail view, activity timeline, search/filtering, stage changes, and a drag-and-drop Kanban board.
- Follow-up list with due, overdue, complete, reschedule, and cancellation workflows.
- Site-visit scheduling on a lead, with optional address and one-hour reminder task, lifecycle history, and role-scoped day/week calendar views.
- Notification centre for overdue follow-ups, with All/Unread filters, read state, and an unread navigation badge.
- Owner/manager dashboard with task attention, pipeline value, team pulse, stage distribution, source conversion, and win-rate analytics.
- Reports for conversion by source/salesperson, time to close by service, and lost reasons.
- Responsive UI with a scrollable, collapsible desktop navigation sidebar. Its state is stored in browser local storage.

## API conventions

- All API routes start with `/api/v1/` and use trailing slashes.
- API authentication is business-scoped JWT. Use `/api/v1/auth/token/` to obtain a token; a user with multiple active memberships must provide the chosen `business_id`.
- Lists are paginated, and filtering/search/ordering stay server-side.
- Use dedicated actions for workflow changes, such as `POST /api/v1/leads/{id}/transition/`, `POST /api/v1/leads/{id}/needs-time/`, and task complete/reschedule actions; do not hide state transitions in generic PATCH behavior.
- Notifications are recipient- and tenant-scoped: use `GET /api/v1/notifications/` and `POST /api/v1/notifications/{id}/read/`. A normal list endpoint must never reveal another person's alert.
- Site visits use `/api/v1/site-visits/` with dedicated `reschedule`, `complete`, and `cancel` actions. Do not encode visit lifecycle changes in a generic PATCH.

## Follow-ups, time, and notifications

- Store timestamps in UTC. Use `Business.timezone` for business-local date boundaries such as the follow-up `due=today` filter; validate IANA timezone values in models, forms, and API serializers.
- Use the shared lead-activity and follow-up service functions so timeline events stay consistent and are always written with the task or lead's business.
- Complete, reschedule, and cancel actions must lock the selected open task and run its status change, notification resolution, successor task creation where applicable, and activity record in one `transaction.atomic()` block.
- Resolve outstanding notifications when their task is completed, rescheduled, or cancelled. Keep notification pages and APIs recipient-scoped as well as business-scoped.
- A scheduled site visit may create one optional `FollowUpTask` due one hour before the appointment. Use its deterministic per-visit rule key so reschedules move the existing reminder and visit completion/cancellation resolves it.
- Site visits can repeat for one lead. Their schedule, assignee, and status belong on `SiteVisit`, not mutable fields on `Lead`; completion and cancellation must create an `Activity` entry in the same transaction.

## Production and observability rules

- Keep `STATIC_ROOT`/WhiteNoise static handling separate from uploaded media. The `STORAGES` setting must retain both the `default` media backend and the `staticfiles` backend; omitting the former breaks profile-picture URLs.
- Read production hosts from `DJANGO_ALLOWED_HOSTS`; never replace that setting with a hard-coded host list. Keep production secrets only in environment or secret management.
- Use Gunicorn behind a trusted TLS-terminating proxy in production, not `manage.py runserver`. The proxy can use `GET /healthz/` for process liveness and `GET /readyz/` for PostgreSQL and Redis/Valkey readiness.
- Every response carries `X-Request-ID`; keep that value in logs. Production defaults to JSON logs, while `DJANGO_LOG_FORMAT=plain` is available for local troubleshooting.
- Compose keeps PostgreSQL and Valkey private to its network and exposes Gunicorn only on `127.0.0.1:8000`. Treat it as deployment configuration: operators still need to supply production environment values and run the stack.

## Coding rules

- Read this file before starting work.
- Build one small, working change at a time and state the immediate goal in plain language before editing.
- Prefer simple, explicit code over abstractions that hide tenant ownership.
- Keep tenant ownership visible in models, querysets, serializers, permissions, views, background jobs, and tests.
- Keep browser workspace selection in the session and API workspace selection in the signed token; never introduce a client-trusted workspace header.
- When changing team or assignment code, query through `users_for_business(self.get_business())` in web views or `users_for_business(active_business(request))` in API code, and preserve the active-membership check. The visual assignee card must include its real form control so it remains usable with and without JavaScript.
- Preserve case-insensitive uniqueness for usernames, email addresses, and per-business product names. Normalize and validate these values in both browser forms and API serializers rather than relying only on a friendly UI error.
- When touching task actions or lead history, extend the existing shared service and transaction boundary instead of duplicating state changes in a view, serializer, or signal.
- Make focused changes; do not rewrite unrelated work or overwrite user changes.
- Create migrations only with Django commands (`manage.py makemigrations`); never hand-write migration files.
- Run `manage.py check` after Django changes and the focused tests when PostgreSQL is available.
- Preserve the server-rendered/progressive-enhancement approach unless the user explicitly requests a frontend architecture change.
- Do not expose passwords, tokens, `.env` values, or seeded credentials in source code, docs, or logs.

## Progress so far

- Multi-tenant business, user, product, lead, activity, task, and notification models are in place with tenant-scoped APIs and isolation tests.
- The lead workflow, seven-stage board, activity timeline, task workflows, and role-aware web workspace are implemented.
- Celery task wrappers, idempotent follow-up scheduling, overdue marking, active-membership stale-lead escalation, and notification creation are implemented.
- The notification page/API, unread navigation badge, business-timezone due-date filtering, and transactional complete/reschedule/cancel workflows are implemented.
- Site visits support repeatable appointments, free-text location, optional one-hour reminders, Activity history, tenant/role-scoped APIs, and responsive day/week calendar views.
- Dashboard/reporting views now include operational metrics plus pipeline and source-conversion analytics.
- The workspace includes membership-scoped switching, owner business creation, profile photos, and a functional fallback-avatar assignee picker alongside responsive navigation and a targeted, idempotent Smith LLC demo-data seed.
- Production baseline configuration includes media/static storage, environment-driven allowed hosts, WhiteNoise, Gunicorn, Compose, health/readiness probes, and request-ID-aware logging.
