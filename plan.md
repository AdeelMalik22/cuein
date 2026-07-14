# Implementation Plan — Cuein Follow-Up Lead Management

**Companion to:** `PRD-FollowUpCRM.md`  
**Status:** Active implementation — core MVP workflows are built; hardening and pilot readiness remain.
**Last updated:** July 14, 2026

## 1. Delivery Goal and MVP Boundary

Build a mobile-responsive web app for small service businesses that makes the next action for every active lead visible, owned, and scheduled.

The first releasable MVP includes:

- Tenant signup/bootstrap, JWT login, and team management.
- Fixed seven-stage pipeline; lead capture, assignment, and safe stage changes.
- An append-only lead timeline and a manually created or automated next-action task.
- A salesperson work queue and an owner/manager dashboard.
- Three automations: quotation follow-up, delayed follow-up, warranty reminder; plus stale-lead and overdue-task sweeps.
- Strict tenant isolation and role-based visibility.

Explicitly defer configurable stages, configurable automation rules, WhatsApp/SMS, quotation PDFs, email delivery, native apps, and AI insights. In-app tasks are the notification channel for MVP.

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
| Async | Celery + Redis + Celery Beat | Separates immediate event scheduling from time-based sweeps. |
| Frontend | Django templates + shared CSS + progressive JavaScript | Keeps the workflow fast to build, mobile-responsive, and close to Django’s auth/forms. |
| API auth | Django sessions for the web workspace; `djangorestframework-simplejwt` for the API | The workspace uses normal Django authentication while integrations use JWT. |
| Tenant model | Shared schema, `business_id` on every tenant-owned table | Appropriate operational complexity for SMB SaaS. |
| User model | Custom Django `User`, created before the first migration | Avoids the high-cost migration of changing `AUTH_USER_MODEL` later. A user belongs to one business in v1. |
| Notifications | In-app task feed only | Keeps MVP focused; email/WhatsApp becomes an adapter, not a prerequisite. |

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

### Tenant scoping

- `Business` is the tenant root. All tenant-owned models carry a non-null `business` FK.
- The authenticated `request.user.business` is the only tenant source for HTTP requests. Never accept `business_id` from a client payload.
- Each tenant-owned ViewSet explicitly scopes `get_queryset()` with `request.user.business`; `perform_create()` assigns that business server-side. Keep this rule visible in every new ViewSet until a shared mixin provides the same clarity without hiding tenant ownership.
- Object lookups must use that scoped queryset, producing 404 rather than exposing cross-tenant object existence.
- Do **not** use a request/thread-local "current tenant" manager. It is fragile in admin, scripts, async work, and tests. Use explicit `.for_business(business)` querysets and consistently scoped ViewSet methods.
- Serializers validate that every related object (product, assignee, lead) belongs to the authenticated user's business.
- Celery tasks accept primitive IDs, then fetch with both primary key and `business_id`. Periodic tasks iterate businesses explicitly.
- The Django admin must scope tenant-owned querysets and foreign-key choices to the admin user's business, or be restricted to a superuser-only internal console.

### Roles

| Capability | Owner | Manager | Salesperson |
| --- | --- | --- | --- |
| View business leads/tasks | all | all | assigned only |
| Create leads | yes | yes | yes (self-assigned by default) |
| Reassign leads/tasks | yes | yes | no |
| Manage team/settings | yes | optional | no |
| View reports/dashboard | all | all | personal queue only |

Keep role checks in named DRF permission classes and queryset filters, not only hidden frontend controls.

## 4. Data Model and Invariants

### Core models

| Model | Important fields | Constraints / notes |
| --- | --- | --- |
| `Business` | name, industry, is_active, created_at | Tenant root. |
| `User` | business, role, phone, email, is_active | Custom `AbstractUser`; one business per user in v1. |
| `Product` | business, name, description, is_active | Unique name per business. |
| `Lead` | business, customer_name, phone, email, source, product, stage, quoted_price, assigned_user, lost_reason, created_at, updated_at, last_activity_at, closed_at | `lost_reason` required for Lost; `closed_at` set for Won/Lost. `last_activity_at` is denormalized for efficient stale checks. |
| `Activity` | business, lead, type, content, metadata, created_by, created_at | Append-only. `metadata` stores structured stage data such as `{from, to}`. |
| `FollowUpTask` | business, lead, assigned_user, due_at, description, status, rule_key, created_at, completed_at | `status`: pending, done, overdue, cancelled. `rule_key` supports idempotency. |
| `Notification` | business, recipient, task, read_at, created_at | MVP in-app feed; create when a task is due/overdue, not as a transport abstraction yet. |

`FollowUpRule` is not a model in MVP. Keep default offsets in one versioned Python module (`followups/rules.py`) and record its stable `rule_key` on generated tasks. Add per-business configuration only after pilots validate the defaults.

### Required database indexes and constraints

- `Lead`: `(business, stage)`, `(business, assigned_user, last_activity_at)`, `(business, created_at)`.
- `FollowUpTask`: `(business, assigned_user, due_at, status)`, `(business, lead, status)`.
- `Activity`: `(business, lead, created_at)`.
- `Product`: unique `(business, name)`.
- Partial unique constraint for automated tasks: one non-terminal task per `(business, lead, rule_key)`. If the database/version makes the exact conditional constraint awkward, enforce with a transaction and document it; PostgreSQL partial uniqueness is preferred.
- Check constraints for non-negative quoted price and a valid `closed_at`/terminal stage relationship where practical. Keep the conditional Lost reason validation in the serializer/model `clean()` as it is text-based.

### State-transition policy

- The API owns transitions. `Lead.stage` cannot be modified through a generic serializer update; use `POST /api/leads/{id}/transition/` with `stage`, `lost_reason` when applicable, and optional note.
- Permit forward progression and terminal transitions in MVP; reopening a Won/Lost lead is owner/manager-only and must include a note. Record the allowed-transition matrix in code and test it.
- In one `transaction.atomic()` block: lock the lead, validate the transition, update the lead, create one stage activity, update `last_activity_at`, and register automation work with `transaction.on_commit()`.
- Use `on_commit()` rather than a `post_save` signal. Signals cannot reliably determine the previous stage and may enqueue work for rolled-back transactions.
- Logging a call, note, site visit, or quotation activity updates `last_activity_at`. A system-generated reminder does not count as customer activity.

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
- Store datetimes in UTC. Render them in each user/business timezone (start with an explicit `Business.timezone`, default `Asia/Karachi`). Define “today” using that timezone.
- Periodic sweeps must be safe to run multiple times. Test task creation under retry and concurrent-worker conditions.

## 6. API Contract (MVP)

Use `/api/v1/`; paginate lists; return ISO-8601 UTC timestamps. List endpoints are scoped before filtering, ordering, or aggregation.

| Area | Endpoints |
| --- | --- |
| Auth | `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me` |
| Team | `GET/POST /users`, `PATCH /users/{id}` (owner/manager scope) |
| Products | `GET/POST /products`, `PATCH /products/{id}` |
| Leads | `GET/POST /leads`, `GET/PATCH /leads/{id}`, `POST /leads/{id}/transition`, `POST /leads/{id}/needs-time`, `GET /leads/{id}/timeline` |
| Activities | `POST /leads/{id}/activities` |
| Tasks | `GET /follow-up-tasks?due=today&status=pending`, `POST /follow-up-tasks`, `PATCH /follow-up-tasks/{id}` (complete, reschedule, reassign) |
| Dashboard | `GET /dashboard/summary` |
| Reports | `GET /reports/conversion-by-source`, `.../by-salesperson`, `.../time-to-close`, `.../lost-reasons` |

Define request/response examples in an OpenAPI schema and generate or validate the frontend API client from it. Error responses should be field-addressable (`{ "lost_reason": ["Required when stage is lost."] }`) and use consistent 401/403/404 semantics.

## 7. Frontend Scope

Deliver screens in workflow order:

1. Login and initial business/team setup.
2. “My day” task queue: due, overdue, complete, reschedule, and quick activity logging.
3. Lead quick-add and lead detail, with timeline and next-action status prominent.
4. Pipeline board: desktop drag/drop; mobile stage selector; assignment and basic filters.
5. Owner/manager dashboard: due/overdue, pipeline value, stalled leads, salesperson summary.
6. Reports with accessible tables first; charts only where they improve scanning.

Use optimistic UI only for reversible actions; refetch/rollback on failure. Never assume a role from client state—the API response is authoritative. Include loading, empty, error, and no-permission states for each primary screen.

## 8. Build Order, Status, and Next Steps

### Phase 0 — Project foundation — substantially complete

- Implemented: PostgreSQL configuration, custom `User`, `Business`, JWT API auth, session-authenticated web views, role permissions, and tenant isolation test fixtures.
- Remaining: Docker Compose, health/readiness endpoints, `.env.example`, structured logging, and CI for formatting, migration checks, and tests.

**Exit:** a user can authenticate; every tenant-scoped list/detail request is demonstrably isolated; CI, readiness checks, and deployment configuration are in place.

### Phase 1 — Lead workflow walking skeleton — complete

- Product, Lead, Activity, assignment, and stage-transition APIs are implemented with tenant validation.
- The web workspace includes quick-add, lead detail/editing, activity logging, follow-up creation, and a responsive Kanban board with desktop drag-and-drop.
- Dashboard/task views surface active leads without open next actions.

**Exit:** a salesperson can create and progress a lead end-to-end, see a complete timeline, and cannot access a colleague’s lead; manager can reassign it.

### Phase 2 — Follow-up engine — implemented; operational hardening remains

- `FollowUpTask`, `Notification`, rule constants, idempotent scheduling, Celery task wrappers, overdue marking, and stale-lead escalation are implemented.
- The web task queue supports complete and reschedule workflows; APIs preserve a new next action on task completion.
- Remaining: production worker/Beat deployment verification, frozen-time/timezone coverage, and a user-facing notification feed in the web workspace.

**Exit:** all automation rules are verified under normal/retried execution and periodic sweeps have frozen-time/timezone coverage.

### Phase 3 — Management visibility — complete

- Role-aware dashboard, task queue, team pulse, quiet-lead watch list, pipeline value, and active-lead metrics are implemented.
- Dashboard analytics include a stage-distribution chart and source-conversion/win-rate graph; reports cover source, salesperson, product, and lost-reason patterns.
- Search/filtering, pagination, and model indexes are present. Query-count measurement for large tenants remains a hardening task.

**Exit:** owner sees accurate current metrics and salesperson sees only their personal work queue.

### Phase 4 — Reports and pilot hardening — in progress

- Implemented: onboarding, aggregate reports, admin configuration, and targeted demo data for `Smith LLC`.
- The Smith LLC seed creates 2,000 idempotent demo leads plus activities and a mix of upcoming, overdue, completed, and cancelled follow-ups.
- Remaining: audit-focused admin review, error monitoring, deployment runbook, and usability testing with pilot businesses.

**Exit:** first business can create a tenant, add a team member, and log its first lead in under 15 minutes without developer help.

## 9. Test Strategy

| Priority | Coverage |
| --- | --- |
| P0 | Cross-tenant reads, writes, related-object assignment, reports, admin scope, and Celery task scoping. |
| P0 | Role visibility and reassignment rules. |
| P0 | Transition validation, Lost reason, one timeline event per transition, transaction rollback behavior. |
| P0 | Automation idempotency, retries, timezone boundaries, stale/overdue sweeps. |
| P1 | Dashboard/report aggregate correctness and pagination/filter behavior. |
| P1 | Browser tests for quick-add → transition → task completion on mobile viewport. |
| P2 | Load tests for daily sweeps and dashboard with representative tenant data. |

Use factories that always require an explicit business. Do not create unscoped tenant models in tests. Test API behavior rather than only managers so the actual security boundary is covered.

## 10. Operations, Privacy, and Release Readiness

- Secrets come only from environment/secret management; never commit `.env` files or JWT keys.
- Back up PostgreSQL daily and test a restore before pilot. Redis is disposable broker state, not the system of record.
- Enable TLS, secure cookies where applicable, allowed-host/CORS configuration, rate limits on login, and production error monitoring.
- Log request ID, user ID, business ID, and action outcome without storing notes/phone numbers in logs. Treat lead data as customer personal data.
- Add a minimal audit record for assignment, stage, and task-status changes. The Activity timeline is product history, not a complete security audit trail.
- Publish a deployment runbook: migrate, collect static assets, start web/worker/beat, smoke-test login and scheduled jobs, and rollback procedure.

## 11. Decisions Deferred to Pilot Evidence

1. Whether v1.1 needs email, WhatsApp, or SMS—validate which channel pilot users actually act on.
2. Per-business configurable follow-up rules and pipeline stages.
3. Multi-business memberships for consultants/franchise owners; current v1 intentionally supports one business per user.
4. Industry-specific fields, warranty durations, and lead sources.
5. Retention/deletion policy and regional compliance requirements before broad commercial launch.

## 12. Progress so far

- Core tenant boundaries, role-aware APIs, and cross-tenant tests are established.
- The server-rendered workspace supports onboarding, lead management, activities, a Kanban pipeline, task workflows, team/service management, settings, and reports.
- Follow-up services and Celery entry points exist for quote, delayed, warranty, stale, and overdue workflows.
- The dashboard now combines daily attention metrics with pipeline-stage and source-conversion analytics.
- A collapsible, scrollable desktop navigation and responsive mobile navigation are implemented.
- `Smith LLC` has a reusable 2,000-record demo dataset for realistic dashboard, leads, and follow-up testing.
