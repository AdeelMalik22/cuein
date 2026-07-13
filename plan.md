# Implementation Plan — Follow-Up Lead Management Tool

**Companion to:** PRD-FollowUpCRM.md **Status:** Draft v1.0 **Last updated:** July 13, 2026

This document translates the PRD into a concrete, buildable plan: tech stack, architecture, data model, folder structure, and a phased build order. It's meant to be followed top-to-bottom during implementation.

---

## 1\. Tech Stack Decisions

| Layer | Choice | Reasoning |
| :---- | :---- | :---- |
| Backend framework | **Django \+ Django REST Framework (DRF)** | Batteries-included (auth, admin, ORM), fast to build multi-tenant CRUD-heavy apps, matches existing experience. |
| Background jobs / scheduling | **Celery** \+ **Redis** (broker \+ result backend) | Needed for stage-triggered reminders, daily "no activity" escalation sweeps, and warranty reminders. Celery Beat handles periodic checks. |
| Database | **PostgreSQL** | Strong relational integrity for tenant-scoped FKs, good indexing support, JSONField available if needed for flexible notes/metadata. |
| Frontend | **React (Vite) \+ Tailwind**, mobile-responsive | Lightweight, fast to iterate, no native app needed for v1. Kanban board \+ forms are straightforward with a component library. |
| Auth | **DRF Token/JWT auth** (e.g., `djangorestframework-simplejwt`) | Standard, supports mobile-friendly stateless auth. |
| Notifications (v1) | **Email** (e.g., via SMTP/SendGrid) \+ **in-app notification feed** | Lowest integration cost to ship MVP. WhatsApp Business API evaluated in Phase 2 (see Open Questions in PRD). |
| Hosting (initial) | Single VPS or small managed Postgres \+ app server (e.g., Railway/Render/DigitalOcean) | Avoid over-engineering infra before there are paying tenants. |
| Task queue infra | Redis (single instance to start) | Doubles as Celery broker; can add persistence/replication later. |

---

## 2\. Multi-Tenancy Implementation Plan

Per the PRD (Section 12.1), tenancy \= shared DB/schema \+ `business` FK on every scoped model.

**Step-by-step:**

1. Create a `Business` model as the tenant root (name, industry type, created\_at, is\_active, plan/tier placeholder).  
2. Create a `Membership`/custom `User` model linking Django's auth user to a `Business` \+ `role` (`owner`, `manager`, `salesperson`).  
3. Build a `TenantScopedModel` abstract base class: adds `business = models.ForeignKey(Business, on_delete=models.CASCADE)` and a custom manager.  
4. Build `TenantScopedManager` / `TenantScopedQuerySet`: exposes `.for_business(business)` and is wired into a DRF permission/mixin so every ViewSet auto-filters by `request.user.business`.  
5. Add a DRF `IsSameTenant` permission class \+ a `TenantScopedViewSetMixin` that all lead/activity/task viewsets inherit — no viewset should manually write `.filter(business=...)`; it should be automatic and impossible to forget.  
6. Add middleware or a DRF authentication step that attaches `request.business` from the authenticated user (never from client-supplied params).  
7. All Celery tasks accept `business_id` explicitly as an argument and re-scope their queries — no reliance on request context.  
8. Add composite indexes: `(business_id, stage)`, `(business_id, assigned_user_id, due_date)`, `(business_id, created_at)`.  
9. Write cross-tenant isolation tests **before** building UI features — e.g., "User from Business A cannot fetch/update a Lead belonging to Business B" (expect 404, not data).

---

## 3\. Data Model (Concrete Schema Sketch)

Business

  \- id

  \- name

  \- industry (choice: solar, cctv, ac\_installation, real\_estate, construction, furniture, other)

  \- created\_at

  \- is\_active

User (extends Django auth user or a Profile model)

  \- id

  \- business (FK \-\> Business)

  \- role (choice: owner, manager, salesperson)

  \- phone

  \- is\_active

Product

  \- id

  \- business (FK \-\> Business)

  \- name

  \- description (optional)

Lead

  \- id

  \- business (FK \-\> Business)

  \- customer\_name

  \- phone

  \- email (optional)

  \- source (choice: referral, facebook, walk\_in, website, call, other)

  \- product (FK \-\> Product, nullable)

  \- stage (choice: new\_inquiry, contacted, site\_visit, quotation\_sent, negotiation, won, lost)

  \- quoted\_price (nullable decimal)

  \- assigned\_user (FK \-\> User)

  \- lost\_reason (nullable text, required if stage \== lost)

  \- created\_at

  \- updated\_at

Activity  (the "timeline")

  \- id

  \- business (FK \-\> Business)

  \- lead (FK \-\> Lead)

  \- type (choice: call, note, site\_visit, stage\_change, quotation\_sent, email, system)

  \- content (text)

  \- created\_by (FK \-\> User)

  \- created\_at

Task (the "reminder" / next action)

  \- id

  \- business (FK \-\> Business)

  \- lead (FK \-\> Lead)

  \- assigned\_user (FK \-\> User)

  \- due\_date

  \- description

  \- status (choice: pending, done, overdue, escalated)

  \- created\_by\_rule (nullable — tags which automation rule created it, for traceability)

  \- created\_at

  \- completed\_at (nullable)

FollowUpRule (per-business configurable automation — Phase 1.5, can hardcode defaults first)

  \- id

  \- business (FK \-\> Business)

  \- trigger (choice: stage\_quotation\_sent, customer\_requested\_time, inactivity\_10\_days, deal\_won)

  \- offset\_days (integer)

  \- notify\_role (choice: assigned\_salesperson, sales\_manager)

**Notes:**

- `lost_reason` should be enforced at the serializer/validation layer: cannot set `stage=lost` without a `lost_reason`.  
- Every `stage` transition on `Lead` should auto-create an `Activity` record (`type=stage_change`) — this is what powers the timeline view without extra manual logging.  
- `FollowUpRule` can be hardcoded as Python constants for the very first MVP milestone, then migrated into a real per-business configurable table in Phase 1.5 — don't block MVP on building a rules engine.

---

## 4\. Core Automation Logic (Celery Task Design)

| Event | Trigger mechanism | Task |
| :---- | :---- | :---- |
| Lead moved to `quotation_sent` | Django signal (`post_save` on Lead, stage changed) → enqueues Celery task | `schedule_quote_followup(lead_id, business_id)` creates a `Task` due in 2 days |
| Customer says "thinking about it" | Manual action in UI (button: "Customer needs time") → same signal pattern | `schedule_delayed_followup(lead_id, business_id)` creates a `Task` due in 7 days |
| No activity for 10 days | **Celery Beat periodic task** (runs daily) | `check_stale_leads()` — scans all businesses' leads with `last_activity > 10 days ago` and no `Won`/`Lost` stage, creates escalation `Task` assigned to the sales manager role |
| Deal marked `Won` | Signal on stage change to `won` | `schedule_warranty_reminder(lead_id, business_id)` creates a `Task` due in 11 months (or per-product override later) |
| Task becomes overdue | **Celery Beat periodic task** (runs daily/hourly) | `flag_overdue_tasks()` — updates `status=overdue` on tasks past `due_date`, surfaces them on the dashboard |

**Design principle:** Signals handle "instant, event-driven" scheduling (stage changes). Celery Beat periodic tasks handle "time-based sweep" logic (stale leads, overdue flags) — these can't be triggered by a signal since nothing "happens" to cause them; they're detected by the passage of time.

---

## 5\. API Surface (v1 endpoints, illustrative)

Auth

  POST   /api/auth/login/

  POST   /api/auth/refresh/

Leads

  GET    /api/leads/                  (filtered to request.user.business automatically)

  POST   /api/leads/

  GET    /api/leads/{id}/

  PATCH  /api/leads/{id}/             (stage changes go through here)

  GET    /api/leads/{id}/timeline/    (returns Activities, chronological)

Activities

  POST   /api/leads/{id}/activities/  (log a call/note manually)

Tasks

  GET    /api/tasks/?due=today

  GET    /api/tasks/?overdue=true

  PATCH  /api/tasks/{id}/             (mark done, reassign)

Dashboard

  GET    /api/dashboard/summary/      (today's tasks, overdue count, pipeline value, stalled leads, per-salesperson stats)

Reports

  GET    /api/reports/conversion-by-source/

  GET    /api/reports/conversion-by-salesperson/

  GET    /api/reports/avg-time-to-close/

  GET    /api/reports/lost-reasons/

---

## 6\. Phased Build Order

### Phase 0 — Foundations (before any feature work)

- Repo setup, Django project scaffold, Postgres \+ Redis running locally/docker-compose.  
- `Business`, `User`/`Membership`, tenant-scoping base classes and mixins.  
- Auth (login/JWT).  
- Cross-tenant isolation test suite scaffold (write these tests early — they should fail loudly if scoping is ever broken).

### Phase 1 — Core Lead Pipeline (MVP walking skeleton)

- `Lead` model \+ CRUD API, scoped to tenant.  
- Kanban board UI: view leads by stage, move between stages.  
- `Activity` model \+ auto-logging on stage change \+ manual "add note/call" logging.  
- Lead detail/timeline view (frontend).

### Phase 2 — Automation Engine

- `Task` model.  
- Signal-based task creation for: quotation sent → 2-day reminder, "customer needs time" → 7-day reminder, won → warranty reminder.  
- Celery \+ Redis wired up; Celery Beat periodic tasks: stale-lead escalation, overdue flagging.  
- In-app notification feed for due/overdue tasks.

### Phase 3 — Owner/Manager Dashboard

- Dashboard summary endpoint \+ frontend view: today's tasks, overdue follow-ups, pipeline value, per-salesperson activity, stalled leads.  
- Role-based visibility (salesperson sees own leads; owner/manager sees all).

### Phase 4 — Reporting & Pattern Insights

- Conversion rate by source, by salesperson.  
- Average time-to-close by product.  
- Lost-reason breakdown.  
- (Keep as straightforward aggregate queries/charts — no ML needed for v1.)

### Phase 5 — Polish & Pilot Readiness

- Onboarding flow (business signup → invite team → done in \<15 min, per PRD goal).  
- Email notification delivery for reminders (or evaluate WhatsApp Business API feasibility — see PRD Open Questions).  
- Basic per-business settings (follow-up day offsets, if time allows — otherwise defer to Phase 1.5/6).  
- Pilot with 2–3 real businesses; collect feedback on stage names, reminder timing defaults, and missing fields.

### Phase 6+ (Post-pilot, per PRD Section 7\)

- WhatsApp Business API lead capture integration.  
- Configurable pipeline stages per business.  
- Quotation builder/PDF generator.  
- AI-generated weekly summaries.  
- Native mobile app / push notifications.

---

## 7\. Suggested Folder Structure (Django \+ DRF)

project/

├── config/                  \# settings, urls, celery.py, wsgi/asgi

├── core/                    \# Business, User/Membership, TenantScopedModel, mixins/permissions

├── leads/                   \# Lead, Product models \+ serializers/viewsets

├── activities/              \# Activity model \+ serializers/viewsets

├── tasks/                   \# Task model, Celery tasks, signals

├── dashboard/                \# dashboard summary aggregation views

├── reports/                  \# reporting/aggregate query views

├── notifications/            \# in-app \+ email delivery

└── tests/

    ├── test\_tenant\_isolation.py   \# cross-tenant access tests (priority \#1)

    ├── test\_lead\_pipeline.py

    ├── test\_automation\_tasks.py

    └── test\_dashboard.py

frontend/

├── src/

│   ├── pages/ (Dashboard, Pipeline/Kanban, LeadDetail, Reports)

│   ├── components/ (KanbanBoard, LeadCard, Timeline, TaskList)

│   ├── api/ (API client wrappers)

│   └── styles/ (Tailwind config — minimal black/white/gray theme)

---

## 8\. Testing Priorities (in order)

1. **Cross-tenant isolation** — non-negotiable, write first, run on every CI build.  
2. **Stage transition → activity/task automation** — verify each automation rule fires correctly and only once.  
3. **Celery Beat periodic tasks** — stale-lead detection and overdue flagging correctness (use time-freezing in tests, e.g., `freezegun`).  
4. **Dashboard aggregation accuracy** — pipeline value sums, overdue counts match underlying data.  
5. **Role-based access** — salesperson cannot see/edit another salesperson's leads unless owner/manager.

---

## 9\. Definition of Done for MVP (ties back to PRD Success Metrics)

- A lead can move through all 7 stages with every transition logged automatically to its timeline.  
- Quotation-sent, delayed-response, and won-stage automations correctly create tasks with the right due dates.  
- Stale leads (10+ days no activity) are automatically escalated to the manager role.  
- Owner dashboard shows: today's tasks, overdue count, pipeline value, per-salesperson stats — refreshed in real time (or near-real-time).  
- Onboarding a new business (create tenant → invite team → log first lead) takes under 15 minutes with zero external help.  
- Cross-tenant isolation test suite passes with 100% coverage on all tenant-scoped models.

---

## 10\. Open Implementation Questions (carry over from PRD, technical framing)

1. Email vs. WhatsApp for v1 notifications — decide before Phase 2, since it affects the `notifications/` module design.  
2. Should `FollowUpRule` be a real configurable table in MVP, or hardcoded constants until Phase 1.5? (Recommendation: hardcode first, avoid building a rules engine before real usage data justifies it.)  
3. Pilot vertical selection (solar/CCTV/real estate/construction) — affects sample data, field choices (e.g., `source`, `product`), and copy/language used during onboarding.

