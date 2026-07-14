# Cuein

Cuein is a lightweight lead follow-up tool for small service businesses. It helps a team capture leads, assign an owner, move opportunities through a fixed pipeline, record each interaction, and keep the next action visible so opportunities are not forgotten.

The application has two first-class interfaces:

- A responsive, server-rendered Django workspace for day-to-day lead and follow-up work.
- A JWT-protected REST API under `/api/v1/` for integrations and programmatic access.

## What is implemented

- Multi-tenant businesses with owner, manager, and salesperson roles.
- Email-confirmed business registration: no `Business` or owner `User` is created until the owner enters the expiring six-digit email code.
- A seven-stage lead pipeline: New inquiry, Contacted, Site visit, Quotation sent, Negotiation, Won, and Lost.
- Lead quick-add, search/filtering, assignment, editing, activity timeline, and validated lost reasons.
- A Kanban board with desktop drag-and-drop, internally scrollable columns, and role-scoped visibility.
- Follow-up tasks with pending, overdue, done, and cancelled states; complete and reschedule workflows.
- Celery-backed follow-up scheduling plus overdue-task and stale-lead sweeps.
- Owner/manager dashboard, source-conversion and stage analytics, reports, and responsive navigation with a collapsible desktop sidebar.

## Multi-tenancy

One running application serves independent businesses. Each `Business` is a tenant.

- Every tenant-owned model has a required `business` relationship through `TenantScopedModel`.
- API and web queries begin with the authenticated user’s business.
- Related products, leads, tasks, and users are validated against the same business.
- Cross-tenant object lookups return `404` rather than exposing another tenant’s data or object existence.

## API overview

| Area | Endpoint |
| --- | --- |
| Sign up | `POST /api/v1/auth/signup/` |
| Verify signup code | `POST /api/v1/auth/verify-email/` |
| Resend signup code | `POST /api/v1/auth/verify-email/resend/` |
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
| Notifications | `/api/v1/notifications/` |

All API routes require JWT authentication except signup, email-code verification, token creation, and token refresh. API signup creates only a temporary pending registration and returns a verification-required response; the business, owner account, and JWT access become available only after the owner enters the emailed six-digit code.

## Local setup

1. Ensure PostgreSQL is running and create the configured database/user.
2. Create a root `.env` file with your local connection values:

   ```text
   POSTGRES_DB=cuein
   POSTGRES_USER=your_user
   POSTGRES_PASSWORD=your_password
   POSTGRES_HOST=127.0.0.1
   POSTGRES_PORT=5432
   ```

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
   ```

   Codes expire after 24 hours by default; change `EMAIL_VERIFICATION_TIMEOUT` in seconds if needed. In local development, email uses Django’s console backend by default, so the verification code is printed in the server terminal.

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

5. For asynchronous follow-ups, start Redis and run a Celery worker and scheduler in separate terminals:

   ```bash
   python3.10 -m celery -A cuein worker --loglevel=INFO
   python3.10 -m celery -A cuein beat --loglevel=INFO
   ```

   Celery uses `redis://127.0.0.1:6379/0` by default. Override `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` in `.env` when Redis runs elsewhere.

## Development data

The legacy command below creates new sample businesses:

```bash
python3.10 seed.py
```

To populate the existing `Smith LLC` tenant only, use the targeted and idempotent command:

```bash
python3.10 seed.py --smith-llc-demo
```

On its first run, it adds 2,000 realistic leads, activities, and a mix of upcoming, overdue, completed, and cancelled follow-up tasks. Re-running it does not duplicate that batch.

## Important development rule

Never hand-write a Django migration. After model changes, generate it with:

```bash
python3.10 manage.py makemigrations
```

See `PRD-FollowUpCRM.md` for the product requirements, `plan.md` for delivery status, and `CLAUDE.md` for the repository working guide.

## Progress so far

- The tenant-safe lead and follow-up workflow is usable end-to-end through both the web workspace and REST API.
- Automation services, dashboards, reports, and role-scoped management views are implemented.
- Dashboard analytics and the Smith LLC demo dataset make it possible to inspect realistic pipeline and follow-up states locally.
- Current hardening priorities are broader automated coverage, CI/deployment readiness, and pilot validation of reminder defaults.
