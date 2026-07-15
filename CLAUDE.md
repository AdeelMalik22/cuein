# Cuein — AI Working Guide

## Project purpose

Cuein is a lightweight lead follow-up and sales-memory tool for small service businesses. Its core promise is that an active lead should never be silently forgotten.

The project is a Django multi-tenant application with both a server-rendered web workspace and a JWT-protected REST API. Keep the product focused on its main loop: capture a lead, move it through the pipeline, record activity, and make the next follow-up visible.

## Current stack

- Python 3.10, Django 5.2, Django REST Framework, and Simple JWT.
- PostgreSQL through `psycopg`.
- Server-rendered Django templates with a shared CSS file and small progressive JavaScript enhancements; there is no React/Vite frontend.
- Celery and Redis for asynchronous follow-up scheduling and periodic overdue/stale-lead sweeps.
- Faker for development/demo data.

## Repository layout

```text
cuein/          Django settings, Celery configuration, and root URLs
core/           Business tenant, custom User, auth/team APIs, permissions
leads/          Product, Lead, Activity models and tenant-scoped APIs
followups/      Follow-up tasks, notifications, rules, services, Celery tasks
web/            Server-rendered workspace views, templates, CSS, and JS
seed.py         Generic seed plus targeted Smith LLC dashboard demo data
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
```

The local PostgreSQL connection values belong in the root `.env` file. Do not commit `.env` or copy its secrets into source code, docs, or logs.

For demo data, `python3.10 seed.py --smith-llc-demo` adds an idempotent batch of 2,000 realistic leads to the existing `Smith LLC` tenant only. Do not run `seed.py` without this flag when you only need the Smith LLC data: the legacy default seed creates new sample businesses.

## Tenant and workspace rules — non-negotiable

- `Business` is the tenant boundary. Every tenant-owned model inherits `core.models.TenantScopedModel`, which provides its required `business` foreign key.
- `User` is a global login identity. `Membership` links a user to a business and carries the role and active status for that specific workspace. One user may have several memberships with different roles.
- `User.business` and `User.role` are temporary legacy compatibility fields only. Do not use either field to authorize new work or select a workspace.
- For web requests, `TenantWebMixin` resolves `request.session['active_business_id']` through a live, active membership. Scope web queries with `self.get_business()` / `self.get_role()`.
- For API requests, the access token contains one selected `business_id`; authentication re-validates that membership and exposes the resolved context through `core.tenancy.active_business(request)` and `active_role(request)`.
- Never accept a client-provided `business_id` from an API body, query string, URL, or mutable header as the tenant source. It is permitted only when requesting a token or switching a workspace, and must be validated against the authenticated user's active memberships server-side.
- Scope API and web querysets from the resolved active business, for example `Lead.objects.for_business(active_business(request))`.
- A newly assigned lead/task user must be an active member of the current business. Historical records may retain a removed member for audit/history.
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
- Follow-up list with due, overdue, complete, and reschedule workflows.
- Owner/manager dashboard with task attention, pipeline value, team pulse, stage distribution, source conversion, and win-rate analytics.
- Reports for conversion by source/salesperson, time to close by service, and lost reasons.
- Responsive UI with a scrollable, collapsible desktop navigation sidebar. Its state is stored in browser local storage.

## API conventions

- All API routes start with `/api/v1/` and use trailing slashes.
- API authentication is business-scoped JWT. Use `/api/v1/auth/token/` to obtain a token; a user with multiple active memberships must provide the chosen `business_id`.
- Lists are paginated, and filtering/search/ordering stay server-side.
- Use dedicated actions for workflow changes, such as `POST /api/v1/leads/{id}/transition/`, `POST /api/v1/leads/{id}/needs-time/`, and task complete/reschedule actions; do not hide state transitions in generic PATCH behavior.

## Coding rules

- Read this file before starting work.
- Build one small, working change at a time and state the immediate goal in plain language before editing.
- Prefer simple, explicit code over abstractions that hide tenant ownership.
- Keep tenant ownership visible in models, querysets, serializers, permissions, views, background jobs, and tests.
- Keep browser workspace selection in the session and API workspace selection in the signed token; never introduce a client-trusted workspace header.
- When changing team or assignment code, query through `users_for_business(self.get_business())` in web views or `users_for_business(active_business(request))` in API code, and preserve the active-membership check. The visual assignee card must include its real form control so it remains usable with and without JavaScript.
- Make focused changes; do not rewrite unrelated work or overwrite user changes.
- Create migrations only with Django commands (`manage.py makemigrations`); never hand-write migration files.
- Run `manage.py check` after Django changes and the focused tests when PostgreSQL is available.
- Preserve the server-rendered/progressive-enhancement approach unless the user explicitly requests a frontend architecture change.
- Do not expose passwords, tokens, `.env` values, or seeded credentials in source code, docs, or logs.

## Progress so far

- Multi-tenant business, user, product, lead, activity, task, and notification models are in place with tenant-scoped APIs and isolation tests.
- The lead workflow, seven-stage board, activity timeline, task workflows, and role-aware web workspace are implemented.
- Celery task wrappers, idempotent follow-up scheduling, overdue marking, stale-lead escalation, and notification creation are implemented.
- Dashboard/reporting views now include operational metrics plus pipeline and source-conversion analytics.
- The workspace includes membership-scoped switching, owner business creation, profile photos, and a functional fallback-avatar assignee picker alongside responsive navigation and a targeted, idempotent Smith LLC demo-data seed.
