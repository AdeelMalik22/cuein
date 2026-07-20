# Implementation Plan — Cuein Follow-Up Lead Management

**Companion to:** `PRD-FollowUpCRM.md`  
**Status:** Active implementation — core MVP workflows and the deployment baseline are built; operational verification and pilot readiness remain.
**Last updated:** July 20, 2026

## 1. Delivery Goal and MVP Boundary

Build a mobile-responsive web app for small service businesses that makes the next action for every active lead visible, owned, and scheduled.

The first releasable MVP includes:

- Tenant signup/bootstrap, JWT login, and team management.
- Fixed seven-stage pipeline; lead capture, assignment, and safe stage changes.
- An append-only lead timeline and a manually created or automated next-action task.
- Repeatable scheduled site visits with an optional one-hour reminder task and a role-scoped calendar.
- A salesperson work queue and an owner/manager dashboard.
- Three automations: quotation follow-up, delayed follow-up, warranty reminder; plus stale-lead and overdue-task sweeps.
- Strict tenant isolation and role-based visibility.

Explicitly defer configurable stages, configurable automation rules, WhatsApp/SMS, quotation PDFs, email delivery, native apps, and AI insights. In-app task notifications are the notification channel for MVP.

### MVP acceptance criteria

1. An active lead is flagged if it has no open next action. `Won` and `Lost` are excluded.
2. A salesperson sees only their assigned leads/tasks; an owner or manager sees all data in their business.
3. A user from another business gets a 404 for direct-object API access and cannot infer another tenant's data from list, report, or background-job results.
4. Every stage change is recorded once in the timeline, including actor, old stage, new stage, and timestamp.
5. Automatic reminders are idempotent: retries and repeated updates do not create duplicate open tasks for the same rule/lead.
6. The core workflow works on a 360px-wide viewport: add lead, log activity, move stage, and complete/re-schedule a task.

## 2. Decisions to Lock Before Scaffolding

| Area | Decision | Why |
| --- | --- | --- |
| Backend | Django + Django REST Framework | Admin, auth, ORM and validation suit a CRUD-heavy SaaS. |
| Database | PostgreSQL in every environment except isolated unit tests | Required for production-like constraints, indexes, and aggregates. |
| Async | Celery + Redis or Valkey + Celery Beat | Separates immediate event scheduling from time-based sweeps; Valkey uses the Redis protocol. |
| Frontend | Django templates + shared CSS + progressive JavaScript | Keeps the workflow fast to build, mobile-responsive, and close to Django’s auth/forms. |
| API auth | Django sessions for the web workspace; `djangorestframework-simplejwt` for the API | The workspace uses normal Django authentication while integrations use JWT. |
| Tenant model | Shared schema, `business_id` on every tenant-owned table | Appropriate operational complexity for SMB SaaS. |
| User model | Global custom Django `User` plus a `Membership` join model | One login can access several businesses; role and active status live on each membership. `User.business`/`User.role` remain a temporary migration bridge, not authorization state. |
| Notifications | In-app notification centre for overdue tasks | Keeps MVP focused; email/WhatsApp becomes an adapter, not a prerequisite. |
| Deployment | Gunicorn, WhiteNoise, environment configuration, and Docker Compose | Provides a production-ready baseline without changing the server-rendered architecture. |

Use `followups` as the Django app name rather than `tasks`, which is easily confused with Celery task modules and Python task concepts.

## 3. Architecture and Security Boundaries

```
Browser  →  Django web views/templates
                 │
                 ├── PostgreSQL
                 └── DRF API (JWT for integrations)
                        │
                        ├── Redis → Celery workers (event follow-ups)
                        └── Celery Beat → workers (daily/hourly sweeps)
```

Production runs Django through Gunicorn behind a TLS-terminating proxy. WhiteNoise
serves collected static assets; uploaded media is served by the deployment's
proxy or object storage. Docker Compose supplies PostgreSQL, Valkey, web,
worker, and Beat configuration for a single-host deployment.

### Tenant scoping

- `Business` is the tenant root. All tenant-owned models carry a non-null `business` FK.
- A global `User` reaches a business through an active `Membership`; the membership holds the workspace-specific role and status.
- Browser requests resolve the active business from `request.session['active_business_id']`, then validate the selected business against the signed-in user's active memberships. `TenantWebMixin` exposes the resulting business and role to web views.
- API access tokens are bound to exactly one business. The authentication layer re-validates the token's membership on every request before exposing the active business and role.
- Never accept a client-controlled `business_id` as the tenant source for a normal request. A business ID supplied to the workspace switch or token endpoint must be re-validated against the user's memberships before it is saved or signed.
- Each tenant-owned ViewSet scopes `get_queryset()` with the resolved active business; `perform_create()` assigns that business server-side. Keep this rule visible in every new ViewSet until a shared mixin provides the same clarity without hiding tenant ownership.
- Object lookups must use that scoped queryset, producing 404 rather than exposing cross-tenant object existence.
- Do **not** use a request/thread-local "current tenant" manager. It is fragile in admin, scripts, async work, and tests. Use explicit `.for_business(business)` querysets and consistently scoped ViewSet methods.
- Serializers validate that every related object (product, assignee, lead) belongs to the resolved active business; new assignees must have an active membership there.
- Celery tasks accept primitive IDs, then fetch with both primary key and `business_id`. Periodic tasks iterate businesses explicitly.
- Stale-lead escalations choose an active owner or manager membership from the business being swept. They must not use a legacy `User.business`/`User.role` value to choose a recipient.
- Only an owner can make owner-level membership changes. Updates, deactivation, and removal must lock that workspace's membership rows in a transaction so concurrent requests cannot leave it without an active owner.
- The Django admin must scope tenant-owned querysets and foreign-key choices to the admin user's business, or be restricted to a superuser-only internal console.

### Roles

| Capability | Owner | Manager | Salesperson |
| --- | --- | --- | --- |
| View business leads/tasks | all | all | assigned only |
| Create leads | yes | yes | yes (self-assigned by default) |
| Reassign leads/tasks | yes | yes | no |
| Manage team/settings | yes | optional | no |
| View reports/dashboard | all | all | personal queue only |

Keep role checks in named DRF permission classes and queryset filters, not only hidden frontend controls. Roles are membership-scoped; `User.role` must not authorize an active-workspace action.

## 4. Data Model and Invariants

### Core models

| Model | Important fields | Constraints / notes |
| --- | --- | --- |
| `Business` | name, industry, timezone, is_active, created_at | Tenant root; timezone is a validated IANA value used for business-local due-date boundaries. |
| `User` | username, password, email, phone, profile_picture, is_active | Global custom `AbstractUser` identity. Legacy `business` and `role` fields remain only as a safe rollout bridge. |
| `Membership` | user, business, role, is_active, joined_at | Unique `(user, business)` link. A user can have different roles in different workspaces. |
| `Product` | business, name, description, is_active | Unique name per business. |
| `Lead` | business, customer_name, phone, email, source, product, stage, quoted_price, assigned_user, lost_reason, created_at, updated_at, last_activity_at, closed_at | `lost_reason` required for Lost; `closed_at` set for Won/Lost. `last_activity_at` is denormalized for efficient stale checks. |
| `SiteVisit` | business, lead, scheduled_at, address, assigned_user, status, reminder_enabled, completed_at, cancelled_at | Repeatable appointment record. `status`: scheduled, completed, cancelled; lifecycle changes write a lead Activity. |
| `Activity` | business, lead, type, content, metadata, created_by, created_at | Append-only. `metadata` stores structured stage data such as `{from, to}`. |
| `FollowUpTask` | business, lead, assigned_user, due_at, description, status, rule_key, created_at, completed_at | `status`: pending, done, overdue, cancelled. `rule_key` supports idempotency. |
| `Notification` | business, recipient, task, read_at, created_at | Recipient-scoped in-app feed; create for overdue tasks and resolve it when the task is actioned. |

`FollowUpRule` is not a model in MVP. Keep default offsets in one versioned Python module (`followups/rules.py`) and record its stable `rule_key` on generated tasks. Add per-business configuration only after pilots validate the defaults.

### Required database indexes and constraints

- `Lead`: `(business, stage)`, `(business, assigned_user, last_activity_at)`, `(business, created_at)`.
- `FollowUpTask`: `(business, assigned_user, due_at, status)`, `(business, lead, status)`.
- `SiteVisit`: `(business, status, scheduled_at)`, `(business, assigned_user, scheduled_at)`, `(business, lead, scheduled_at)`.
- `Activity`: `(business, lead, created_at)`.
- `Product`: unique `(business, name)`.
- `Membership`: unique `(user, business)`, plus indexes for `(user, is_active)` and `(business, is_active)`.
- `User` and pending registration username/email values are case-insensitively unique; product names are case-insensitively unique within a business.
- Partial unique constraint for automated tasks: one non-terminal task per `(business, lead, rule_key)`. If the database/version makes the exact conditional constraint awkward, enforce with a transaction and document it; PostgreSQL partial uniqueness is preferred.
- Check constraints for non-negative quoted price and a valid `closed_at`/terminal stage relationship where practical. Keep the conditional Lost reason validation in the serializer/model `clean()` as it is text-based.

### State-transition policy

- The API owns transitions. `Lead.stage` cannot be modified through a generic serializer update; use `POST /api/leads/{id}/transition/` with `stage`, `lost_reason` when applicable, and optional note.
- Permit forward progression and terminal transitions in MVP; reopening a Won/Lost lead is owner/manager-only and must include a note. Record the allowed-transition matrix in code and test it.
- In one `transaction.atomic()` block: lock the lead, validate the transition, update the lead, create one stage activity, update `last_activity_at`, and register automation work with `transaction.on_commit()`.
- Use `on_commit()` rather than a `post_save` signal. Signals cannot reliably determine the previous stage and may enqueue work for rolled-back transactions.
- Logging a call, note, site visit, or quotation activity updates `last_activity_at`. A system-generated reminder does not count as customer activity.
- Complete, reschedule, and cancel actions lock the open task and use shared service code inside one transaction to change task state, resolve its notification, and write the timeline activity. Completion also creates the successor task in that same transaction.

## 5. Automation Design

| Rule key | Event | Result | Default |
| --- | --- | --- | --- |
| `quote_followup_v1` | transition to `quotation_sent` | Open task for assigned salesperson | due in 2 days |
| `delayed_followup_v1` | explicit “customer needs time” action | Open task for assigned salesperson | due in 7 days |
| `warranty_checkin_v1` | transition to `won` | Open task for assigned salesperson | due in 11 months |
| `stale_lead_escalation_v1` | daily sweep | Open task for an owner/manager | no activity for 10 days |
| `overdue_flag_v1` | hourly sweep | mark past-due open tasks overdue; create in-app notification | after `due_at` |

Implementation requirements:

- The synchronous API transaction enqueues a small Celery task after commit. The worker creates the task through an idempotent service function.
- A completed/cancelled task is terminal. Completing a task should prompt for either a logged outcome or a newly scheduled next action; the API must still flag the lead when none exists.
- Before scheduling a stale escalation, exclude terminal stages and leads that already have an open stale-escalation task.
- Store datetimes in UTC. Render them in each user/business timezone (start with an explicit `Business.timezone`, default `Asia/Karachi`). Define “today” using that timezone, and validate the timezone consistently in models, forms, and APIs.
- Periodic sweeps must be safe to run multiple times. Test task creation under retry and concurrent-worker conditions.
- A visit reminder is optional and uses a deterministic `site_visit_reminder:{visit_id}` task rule key. It is due one hour before the appointment; rescheduling moves the open reminder, while completing/cancelling the visit resolves it.

## 6. API Contract (MVP)

Use `/api/v1/`; paginate lists; return ISO-8601 UTC timestamps. List endpoints are scoped before filtering, ordering, or aggregation.

| Area | Endpoints |
| --- | --- |
| Auth | `POST /auth/signup`, `POST /auth/verify-email`, `POST /auth/token`, `POST /auth/token/refresh`, `GET /me` |
| Team | `GET/POST /users`, `PATCH /users/{id}` (owner/manager scope) |
| Products | `GET/POST /products`, `PATCH /products/{id}` |
| Leads | `GET/POST /leads`, `GET/PATCH /leads/{id}`, `POST /leads/{id}/transition`, `POST /leads/{id}/needs-time`, `GET /leads/{id}/timeline` |
| Activities | `POST /leads/{id}/activities` |
| Site visits | `GET/POST /site-visits`, `GET /site-visits/{id}`, `POST /site-visits/{id}/reschedule`, `POST /site-visits/{id}/complete`, `POST /site-visits/{id}/cancel` |
| Tasks | `GET /follow-up-tasks?due=today&status=pending`, `POST /follow-up-tasks`, `PATCH /follow-up-tasks/{id}` (ordinary edits/reassignment), `POST /follow-up-tasks/{id}/complete`, `POST /follow-up-tasks/{id}/reschedule`, `DELETE /follow-up-tasks/{id}` (cancel) |
| Notifications | `GET /notifications`, `GET /notifications/{id}`, `POST /notifications/{id}/read` |
| Dashboard | `GET /dashboard/summary` |
| Reports | `GET /reports/conversion-by-source`, `.../by-salesperson`, `.../time-to-close`, `.../lost-reasons` |

Define request/response examples in an OpenAPI schema and generate or validate the frontend API client from it. Error responses should be field-addressable (`{ "lost_reason": ["Required when stage is lost."] }`) and use consistent 401/403/404 semantics.

For a user with several active memberships, `POST /auth/token` requires a `business_id`. The server validates that membership before placing the business identifier in the signed refresh/access token; normal API requests never carry a mutable workspace header.

## 7. Frontend Scope

Deliver screens in workflow order:

1. Login and initial business/team setup.
2. “My day” task queue: due, overdue, complete, reschedule, and quick activity logging.
3. Notifications: All/Unread filters, a readable overdue-task summary, and a navigation unread badge.
4. Site visits: schedule one or more appointments from a lead; show a business-timezone day/week calendar with role-scoped visibility.
5. Lead quick-add and lead detail, with timeline and next-action status prominent.
6. Pipeline board: desktop drag/drop; mobile stage selector; assignment and basic filters.
7. Owner/manager dashboard: due/overdue, pipeline value, stalled leads, salesperson summary.
8. Reports with accessible tables first; charts only where they improve scanning.

Use optimistic UI only for reversible actions; refetch/rollback on failure. Never assume a role from client state—the API response is authoritative. Include loading, empty, error, and no-permission states for each primary screen.

## 8. Build Order, Status, and Next Steps

### Phase 0 — Project foundation — complete baseline; CI remains

- Implemented: PostgreSQL configuration, custom `User`, `Business`, membership-scoped JWT/session authentication, role permissions, tenant isolation fixtures, `.env.example`, health/readiness endpoints, request-ID-aware logging, and Docker Compose.
- Static assets collect to `staticfiles/` and are served by WhiteNoise; uploaded profile pictures use a configured local media backend in development. Production hosts come from `DJANGO_ALLOWED_HOSTS`, and Gunicorn is included for deployment.
- Remaining: CI for formatting, migration checks, and the full test suite, plus live-environment verification of the deployment configuration.

**Exit:** a user can authenticate; every tenant-scoped list/detail request is demonstrably isolated; CI, readiness checks, and deployment configuration are in place and verified in a real deployment.

### Phase 0.5 — Multi-business workspaces — complete

- `Membership` now links one global identity to one or more businesses, carrying a role and active status per workspace.
- Web sessions resolve a validated active workspace and the sidebar can switch workspaces. Invalid or inactive selections are rejected; a switch always starts at the selected business dashboard.
- JWTs are business-scoped and re-check the membership when used. A person with multiple memberships must choose a business when obtaining a token.
- Owners can create and immediately enter another business from the workspace switcher. Existing `User.business` and `User.role` values are preserved as a compatibility bridge, and migrations/backfill create legacy memberships idempotently.

**Exit:** a shared user can see only the selected business's data, holds the correct role in each business, and cannot switch to a business without a membership.

### Phase 1 — Lead workflow walking skeleton — complete

- Product, Lead, Activity, assignment, and stage-transition APIs are implemented with tenant validation.
- The web workspace includes quick-add, lead detail/editing, activity logging, follow-up creation, and a responsive Kanban board with desktop drag-and-drop. Owner/manager assignee choices use profile photos with a fallback avatar and include native radio controls, so selection works with or without JavaScript.
- `SiteVisit` supports multiple appointments per lead, optional address and one-hour reminder, transactional complete/cancel history, and a role-scoped day/week calendar. Moving a lead to Site visit prompts scheduling but does not force an appointment.
- Dashboard/task views surface active leads without open next actions.

**Exit:** a salesperson can create and progress a lead end-to-end, see a complete timeline, and cannot access a colleague’s lead; manager can reassign it.

### Phase 2 — Follow-up engine — implemented; operational verification remains

- `FollowUpTask`, `Notification`, rule constants, idempotent scheduling, Celery task wrappers, overdue marking, and stale-lead escalation are implemented.
- The web task queue supports complete and reschedule workflows; APIs preserve a new next action on task completion. Complete, reschedule, and cancellation paths are transactionally coupled to their timeline and notification updates.
- The web notification centre and API are recipient- and tenant-scoped, expose read/unread state, and show an unread navigation badge. Due-today task filtering uses each business's validated timezone.
- Remaining: production worker/Beat deployment verification and expanded frozen-time/timezone coverage.

**Exit:** all automation rules are verified under normal/retried execution and periodic sweeps have frozen-time/timezone coverage.

### Phase 3 — Management visibility — complete

- Role-aware dashboard, task queue, team pulse, quiet-lead watch list, pipeline value, and active-lead metrics are implemented.
- Dashboard analytics include a stage-distribution chart and source-conversion/win-rate graph; reports cover source, salesperson, product, and lost-reason patterns.
- Search/filtering, pagination, and model indexes are present. Query-count measurement for large tenants remains a hardening task.

**Exit:** owner sees accurate current metrics and salesperson sees only their personal work queue.

### Phase 4 — Reports and pilot hardening — in progress

- Implemented: onboarding, aggregate reports, admin configuration, and targeted demo data for `Smith LLC`.
- The Smith LLC seed creates 2,000 idempotent demo leads plus activities and a mix of upcoming, overdue, completed, and cancelled follow-ups.
- The deployment baseline now includes Gunicorn, WhiteNoise, Compose, environment-driven host configuration, health/readiness probes, and request-ID-aware logs. This configuration has not itself been proven by a production deployment.
- Remaining: audit-focused admin review, error monitoring, a full deployment/rollback runbook, and usability testing with pilot businesses.

**Exit:** first business can create a tenant, add a team member, and log its first lead in under 15 minutes without developer help.

## 9. Test Strategy

| Priority | Coverage |
| --- | --- |
| P0 | Cross-tenant reads, writes, related-object assignment, reports, admin scope, and Celery task scoping. |
| P0 | Role visibility and reassignment rules. |
| P0 | Multi-membership workspace switching, rejected workspace selection, business-scoped JWT issuance, and access-token membership revalidation. |
| P0 | Assignment accepts only an active member of the current business; rendered assignee choices include a real submitted form control. |
| P0 | Membership changes cannot remove the final active owner under concurrent requests; stale escalation recipients are active members of the correct business. |
| P0 | Transition validation, Lost reason, one timeline event per transition, transaction rollback behavior. |
| P0 | Automation idempotency, retries, timezone boundaries, stale/overdue sweeps, and recipient-scoped notifications. |
| P0 | Task complete/reschedule/cancel locking, notification resolution, and rollback behavior. |
| P0 | Site-visit tenant isolation, salesperson assignment visibility, reminder idempotency, lifecycle Activity records, and business-timezone calendar boundaries. |
| P1 | Profile-picture storage, health/readiness responses, request-ID propagation, and notification-page layout/read-state behavior. |
| P1 | Dashboard/report aggregate correctness and pagination/filter behavior. |
| P1 | Browser tests for quick-add → transition → task completion on mobile viewport. |
| P2 | Load tests for daily sweeps and dashboard with representative tenant data. |

Use factories that always require an explicit business. Do not create unscoped tenant models in tests. Test API behavior rather than only managers so the actual security boundary is covered.

Focused checks currently cover media storage, notification layout and scoping, health/readiness, and request-ID/logging behavior. Broader CI and production-service verification remain release work.

## 10. Operations, Privacy, and Release Readiness

- Secrets come only from environment/secret management; never commit `.env` files or JWT keys.
- Deploy membership changes with `manage.py migrate`; `manage.py backfill_memberships` is an idempotent safety check for legacy users.
- Back up PostgreSQL daily and test a restore before pilot. Redis is disposable broker state, not the system of record.
- Enable TLS, secure cookies where applicable, allowed-host/CORS configuration, rate limits on login, and production error monitoring. Configure hosts through `DJANGO_ALLOWED_HOSTS`, never a source-controlled deployment-specific list.
- WhiteNoise serves collected static files. Serve uploaded media through the reverse proxy or object storage in production; the local `media/` backend is for development.
- `GET /healthz/` is process liveness; `GET /readyz/` checks PostgreSQL and Redis/Valkey and returns `503` while either dependency is unavailable.
- Each response includes `X-Request-ID`; production defaults to JSON logging with that ID. Log request ID, user ID, business ID, and action outcome without storing notes/phone numbers. Treat lead data as customer personal data.
- Compose keeps PostgreSQL and Valkey private to its network, publishes Gunicorn only to `127.0.0.1:8000`, and coordinates migration/static collection before web, worker, and Beat start.
- Add a minimal audit record for assignment, stage, and task-status changes. The Activity timeline is product history, not a complete security audit trail.
- Publish a deployment runbook: migrate, collect static assets, start web/worker/beat, smoke-test login and scheduled jobs, and rollback procedure.

## 11. Decisions Deferred to Pilot Evidence

1. Whether v1.1 needs email, WhatsApp, or SMS—validate which channel pilot users actually act on.
2. Per-business configurable follow-up rules and pipeline stages.
3. Cross-business reporting, combined dashboards, and data transfers. Multiple business access is implemented, but each workspace remains isolated by design.
4. Industry-specific fields, warranty durations, and lead sources.
5. Retention/deletion policy and regional compliance requirements before broad commercial launch.
6. Google/Outlook calendar sync, route optimization, and customer appointment confirmation; these are intentionally separate from the v1 scheduling workflow.

## 12. Progress so far

- Core tenant boundaries, role-aware APIs, and cross-tenant tests are established.
- The server-rendered workspace supports onboarding, lead management, activities, a Kanban pipeline, task workflows, team/service management, settings, and reports.
- Follow-up services and Celery entry points exist for quote, delayed, warranty, stale, and overdue workflows.
- The dashboard now combines daily attention metrics with pipeline-stage and source-conversion analytics.
- A collapsible, scrollable desktop navigation and responsive mobile navigation are implemented.
- A user can hold multiple business memberships, switch the validated active workspace, and receive a business-scoped JWT; owners can create another business from the switcher.
- Profile photos and fallback-avatar assignment controls are available in the web workspace.
- `Smith LLC` has a reusable 2,000-record demo dataset for realistic dashboard, leads, and follow-up testing.
- The notification centre, business-timezone due-date calculation, active-membership stale reminders, transactional task actions, and case-insensitive account/product validation are implemented.
- Static/media storage, environment-driven production hosts, Gunicorn, WhiteNoise, Compose, health/readiness probes, and request-ID-aware logs are configured.
- Repeatable site-visit scheduling, optional reminder tasks, activity lifecycle history, and role-scoped day/week calendar views are implemented.
