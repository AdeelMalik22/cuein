# Cuein — AI Working Guide

## Project purpose

Cuein is a lightweight follow-up and sales-memory tool for small service businesses. Its core promise is that an active lead should never be silently forgotten.

This project is deliberately being built step by step so the user can understand how a Django multi-tenant application works from scratch.

## Stack

- Python 3.10 and Django 5.2
- Django REST Framework
- Simple JWT for API authentication
- PostgreSQL with `psycopg`
- Redis/Celery are planned for reminders, but are not implemented yet.

## Repository layout

```text
cuein/          Django project settings and root URLs
core/           Business tenant, custom User, auth, team APIs, permissions
leads/          Product and Lead models and tenant-scoped APIs
plan.md         Product delivery plan
PRD-FollowUpCRM.md  Product requirements
```

## Local commands

Run commands from the repository root using the existing virtual environment:

```bash
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations
venv/bin/python manage.py migrate
venv/bin/python manage.py test
venv/bin/python manage.py runserver
```

The local PostgreSQL connection values belong in the root `.env` file. Do not commit `.env` or copy its secrets into source code.

## Multi-tenant rules — non-negotiable

- `Business` is the tenant boundary.
- Every tenant-owned model must inherit `core.models.TenantScopedModel`; this creates its required `business` foreign key.
- `Business` itself and the custom `User` do not inherit from the tenant base. A User has its own direct `business` relationship.
- Never accept `business_id` from an API request body, query string, or URL as the source of tenant identity.
- Scope API querysets from `request.user.business` first, for example: `Lead.objects.for_business(request.user.business)`.
- Related objects such as Products and assigned Users must be validated as belonging to the same business.
- A cross-tenant object lookup must return `404`, never leak data or object existence.
- Celery/background jobs added later must receive and validate `business_id` explicitly; they cannot rely on request context.
- Every new tenant-owned endpoint needs a cross-tenant isolation test.

## Current roles

- **Owner:** manages the business, team, products, and all tenant leads.
- **Manager:** manages products and all tenant leads, including assignments.
- **Salesperson:** sees and edits only assigned leads; cannot reassign or delete them.

## Current API conventions

- All API routes start with `/api/v1/` and use trailing slashes.
- API authentication is JWT. Use `/api/v1/auth/token/` to obtain tokens.
- Use dedicated actions for stateful workflow changes, such as `POST /api/v1/leads/{id}/transition/`; do not add stage changes to generic PATCH behavior.
- Lists are paginated. Keep filtering, ordering, and search server-side.

## Coding rules

- Read this file before starting work.
- Build one small, working change at a time. Explain the immediate goal in plain language before changing code.
- Prefer simple, explicit code over abstractions that hide tenant ownership.
- Keep tenant ownership visible in models, querysets, serializers, permissions, views, and tests.
- Make focused, surgical changes; do not rewrite unrelated code.
- Create migrations only with Django commands (`manage.py makemigrations`). Never hand-write migration files.
- Run `manage.py check` and migration checks after model/API changes. Run relevant tests when PostgreSQL is available.
- Do not create files beyond the user’s requested scope.
- Do not create or rewrite implementation documents unless the user explicitly asks. `CLAUDE.md` and `README.md` may be updated when requested.
- Do not expose passwords, tokens, or values from `.env` in code, logs, or documentation.

