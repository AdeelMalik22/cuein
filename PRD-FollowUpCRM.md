# Product Requirements Document

## Product Name: cuein **Category:** Lightweight lead follow-up & sales memory tool for small service-based businesses **Author:** Adeel **Status:** Draft v1.0 **Last updated:** July 13, 2026

---

## 1\. Problem Statement

Small, service-based businesses (solar installers, furniture sellers, CCTV/security installers, AC technicians, real estate agents, construction contractors, etc.) lose sales not because of price or quality, but because of **forgotten follow-ups**.

The typical flow looks like this:

1. Customer calls or messages asking for a price.  
2. Owner/salesperson visits the site, understands requirements, sends a quotation.  
3. Customer says "I'll think about it."  
4. Nobody has a system to remember to follow up.  
5. Days turn into weeks. The customer buys from a competitor — not because the competitor was better, but because they stayed in touch.

These businesses currently manage customers using **WhatsApp chats, Excel sheets, sticky notes, or memory**. They don't need (and won't adopt) a full CRM like Salesforce or HubSpot — those are too complex, too expensive, and solve problems (marketing automation, support ticketing, email campaigns) that these businesses don't have.

**Core insight:** These businesses don't need a "CRM." They need a system that guarantees no lead is ever silently forgotten.

---

## 2\. Product Vision

"We'll help you remember every customer, every follow-up, and every opportunity — so you lose fewer sales simply because someone forgot to call back."

A dead-simple, single-purpose tool built around one core loop:

**Lead comes in → moves through stages → system auto-schedules the next action → nobody has to remember anything.**

Over time, the accumulated history becomes a source of insight — showing which leads convert, which salespeople perform well, and where the sales process leaks.

---

## 3\. Goals & Non-Goals

### Goals

- Ensure every lead has a clearly owned, scheduled "next action" at all times.  
- Give owners a morning dashboard that answers "what needs my attention today?" in under 10 seconds.  
- Preserve full interaction history per customer so any team member can pick up a conversation with full context.  
- Surface simple, actionable patterns (conversion by source, by salesperson, by product) without requiring the owner to build reports.  
- Be simple enough to onboard in under 15 minutes with zero training.

### Non-Goals (explicitly out of scope for v1)

- Marketing automation / email campaigns / drip sequences.  
- Customer support ticketing / helpdesk.  
- Complex permission hierarchies, territories, or enterprise workflow builders.  
- Invoicing, accounting, or payment processing (may integrate later, not build).  
- Multi-channel inbox (shared email/social inbox) — only structured lead \+ note logging in v1.  
- Native mobile apps in v1 (mobile-responsive web is sufficient initially).

---

## 4\. Target Users & Personas

### Persona 1: The Owner-Operator ("Imran")

Runs a small solar panel / AC installation / CCTV business with 2–8 salespeople. Currently tracks leads via WhatsApp and memory. Wants to know every morning who needs to be called and how much revenue is "in the pipeline." Not tech-savvy — needs something that works without training.

### Persona 2: The Field Salesperson ("Ahmed")

Visits sites, quotes prices, juggles 15–30 active leads at once. Needs quick logging (ideally from a phone) and reminders so he doesn't have to keep a mental list.

### Persona 3: The Sales Manager (may be same person as Owner in very small businesses)

Wants visibility into which leads are stalling, which salespeople are neglecting follow-ups, and which quotations are going cold.

---

## 5\. Core Concepts & Data Model (high-level)

| Entity | Description |
| :---- | :---- |
| **Business (Tenant)** | The root entity — represents one customer company (e.g., a solar installer or a CCTV business) using the product. Every other entity below belongs to exactly one Business. |
| **Lead** | A potential customer \+ their inquiry. Central object of the system. |
| **Stage** | Current position in the pipeline (see below). |
| **Activity/Timeline Event** | A logged interaction: call, note, site visit, quotation sent, status change. |
| **Task/Reminder** | An auto-generated or manually created "next action" with a due date and owner. |
| **Product/Service** | What the lead is interested in (used for pattern analysis). |
| **Salesperson** | User assigned to the lead. |
| **Source** | Where the lead came from (referral, Facebook, walk-in, call, website, etc.) |

### Default Pipeline Stages

1. **New Inquiry**  
2. **Contacted**  
3. **Site Visit**  
4. **Quotation Sent**  
5. **Negotiation**  
6. **Won**  
7. **Lost** (with mandatory "reason lost" field)

Stages should be **configurable per business** in later versions, but v1 can ship with this fixed set to keep scope tight.

---

## 6\. Key Features (MVP)

### 6.1 Lead Capture

- Manually add a lead (name, phone, source, product interest, notes).  
- Quick-add form optimized for mobile (salesperson adds a lead within seconds of a call ending).  
- Optional: WhatsApp/webform integration for auto-capturing inquiries (Phase 2).

### 6.2 Pipeline & Stage Management

- Visual kanban-style board showing leads grouped by stage.  
- Drag/move a lead between stages (or update via a simple dropdown on mobile).  
- Every stage change is timestamped and logged automatically.

### 6.3 Automated Follow-Up Scheduling (the core differentiator)

Rule-based automation tied to stage transitions and inactivity:

| Trigger | Default Action |
| :---- | :---- |
| Quotation Sent | Remind assigned salesperson in 2 days |
| Customer requested time ("thinking about it") | Remind in 7 days |
| No activity/response for 10 days | Escalate — notify sales manager |
| Deal marked Won | Create a task/reminder N months later (e.g., warranty check-in, referral ask) |
| Deal marked Lost | Prompt for "reason lost" (mandatory), archive with reason tagged |

- Rules should be **configurable** (numbers of days, who gets notified) — but ship with sensible defaults so no setup is required on day one.  
- Reminders delivered via in-app notification \+ WhatsApp/SMS/email (channel TBD by feasibility — see Open Questions).

### 6.4 Customer Timeline

- Every lead has a single-page view showing:  
  - First contact date  
  - Product(s) of interest  
  - Quoted price(s)  
  - All notes, calls, and stage changes in chronological order  
  - Outcome (won/lost) and reason  
- Any team member opening the record understands full history in seconds — no "let me check with whoever spoke to them last."

### 6.5 Owner/Manager Dashboard

Daily-use screen answering:

- Who do I need to contact today? (due/overdue tasks)  
- Which follow-ups are overdue? (highlighted in red)  
- Total potential revenue in pipeline (sum of quoted values by stage)  
- Which salesperson has stalled/neglected leads (leads with no activity beyond X days)  
- Which quotations have gone quiet (Quotation Sent stage, no response beyond threshold)

### 6.6 Basic Reporting / Pattern Insights

Once enough data accumulates, surface simple, human-readable insights such as:

- Average time-to-purchase by product ("Customers asking about Product A usually buy within 5 days")  
- Conversion rate by salesperson  
- Conversion rate by lead source (e.g., referrals vs. Facebook ads)  
- Most common reasons for lost deals  
- These can start as simple aggregate queries/charts in v1; "AI-generated insight sentences" can be a Phase 2 enhancement.

### 6.7 User & Role Management

- Every user belongs to exactly one Business (tenant); users never see or access data from another business.  
- Basic roles, scoped within the tenant: Owner/Manager (sees everything within their business) vs. Salesperson (sees only leads assigned to them within their business).  
- Lead assignment/reassignment (within the same business only).

---

## 7\. Phase 2+ Ideas (Not in MVP)

- WhatsApp Business API integration for auto-logging messages into a lead's timeline.  
- Configurable pipeline stages per business/industry template.  
- Quotation builder/PDF generator built into the tool (many users currently do this separately).  
- AI-generated weekly summary ("This week: 12 new leads, 3 overdue by 5+ days, Ahmed closed 4 deals").  
- Lead scoring based on historical conversion patterns.  
- Native mobile app (push notifications).  
- Multi-branch/multi-location support for slightly larger businesses.  
- Referral/warranty automation tied to "Won" deals (e.g., auto-generate a referral request 2 weeks after installation).

---

## 8\. Success Metrics

| Metric | Target (illustrative, refine after pilot) |
| :---- | :---- |
| % of leads with an overdue task older than 5 days | \< 10% (down from "unknown/uncontrolled" today) |
| Time for a new team member to understand a customer's history | \< 1 minute |
| Owner dashboard daily active usage | Owner opens dashboard 5+ days/week |
| Reported win-rate improvement after 90 days of use | Positive trend (self-reported by pilot businesses) |
| Time to onboard a new business (setup to first lead logged) | \< 15 minutes |

---

## 9\. User Stories (sample, not exhaustive)

- As a **salesperson**, after sending a quotation, I want the system to automatically remind me in 2 days so I don't have to remember to follow up manually.  
- As an **owner**, I want to open a dashboard each morning and immediately see who needs a call today, so I don't have to ask my team for status updates.  
- As a **sales manager**, I want to be notified when a lead has had no activity in 10 days, so I can step in before it's too late.  
- As a **new salesperson taking over a lead**, I want to see the full history of a customer in one place, so I don't ask questions the customer already answered.  
- As an **owner**, I want to see which lead sources convert best, so I know where to spend my advertising budget.  
- As an **owner**, I want a warranty/renewal reminder created automatically when a deal is won, so I can proactively reach out later (upsell/referral opportunity).

---

## 10\. Design Principles

- **Radically simple** — a small business owner should understand the whole product in under 5 minutes.  
- **Mobile-first for salespeople** — most updates happen from a phone, often right after a customer call.  
- **Zero configuration required to get value** — sensible defaults out of the box; configuration is optional, not required.  
- **Never let a lead go silent** — if there's no scheduled next action, that itself should be flagged as a problem.  
- **Clean, minimal aesthetic** — no clutter, no unnecessary modules; every screen should map to one of the six core goals of the dashboard.

---

## 11\. Competitive Positioning

|  | Salesforce / HubSpot | This Product |
| :---- | :---- | :---- |
| Setup time | Days to weeks | Minutes |
| Learning curve | Steep | Near-zero |
| Feature scope | Marketing, support, sales, custom objects | Follow-up & pipeline only |
| Target business size | Mid-market to enterprise | Solo operators to small teams (1–15 people) |
| Core value prop | "Manage your whole customer lifecycle" | "Never forget a follow-up again" |
| Pricing expectation | $$$/user/month | Low, flat, affordable for small businesses |

The goal is **not** to compete feature-for-feature with established CRMs — it's to win the segment that currently uses no CRM at all because existing options feel like overkill.

---

## 12\. Technical Considerations (implementation notes)

*(High-level, for planning — not a full architecture doc)*

- **Backend:** A straightforward REST API (e.g., Django/DRF or FastAPI) can model Leads, Stages, Activities, and Tasks as core resources. Given the automation/reminder requirements, a task queue (e.g., Celery with Redis/RabbitMQ as broker) is well-suited for scheduling stage-triggered reminders and the "no activity in N days" escalation checks (periodic beat tasks).  
- **Notifications:** Reminders likely need to go out via WhatsApp Business API, SMS gateway, or email — this is a key integration decision and should be validated with pilot users on which channel they actually check.  
- **Frontend:** A lightweight, mobile-responsive web app (kanban board \+ lead detail \+ dashboard) is sufficient for MVP; no native app needed initially.  
- **Data model simplicity:** Resist the urge to over-generalize the pipeline/stage model early — ship with the fixed 7-stage pipeline before building a stage-configuration engine.

### 12.1 Multi-Tenancy Architecture (Required — Not Optional)

Since multiple, unrelated businesses (solar company, CCTV installer, real estate agency, etc.) will use the same application instance, **the system must be multi-tenant from the very first line of code.** Retrofitting tenant isolation after real customer data exists is high-risk (data leakage between businesses is an unrecoverable trust failure) and expensive to fix later.

**Chosen model: Shared database, shared schema, with a `business_id` (tenant) column on every tenant-scoped table.**

| Model considered | Isolation strength | Ops complexity | Verdict |
| :---- | :---- | :---- | :---- |
| Shared DB \+ shared schema (`tenant_id` column) | Good, enforced in application layer | Low | ✅ **Selected for v1** |
| Shared DB \+ schema-per-tenant | Strong | Medium–High (per-schema migrations) | Reconsider later for enterprise-tier customers |
| Database-per-tenant | Strongest | High (ops overhead scales with customer count) | Not justified at this business size (1–15 users/tenant) |

**Rationale:** Matches the team's timeline and the target customer size (small businesses, typically 1–15 users). This is the standard, proven pattern for SMB B2B SaaS at this stage. It can be migrated to schema-per-tenant later for a specific large/enterprise customer if ever required — but that should not block v1.

**Implementation requirements:**

1. **Core tenant model:** A `Business` (tenant) model is the root of the hierarchy. Every user, lead, activity, task, and product belongs to exactly one `Business`.  
2. **Tenant column on every scoped table:** `Lead`, `Activity`, `Task`, `Product`, and any future tenant-scoped model must carry a `business` foreign key (non-nullable).  
3. **Automatic query scoping — never manual:** Implement a base manager/queryset (e.g., `TenantScopedManager`) used by all tenant-scoped models, so every query is automatically filtered to the current tenant. Individual views/serializers should never be relied upon to remember to add `.filter(business=...)` manually — that pattern is how cross-tenant data leaks happen.  
4. **Tenant resolution:** Resolve the active tenant from the authenticated user's session/JWT (`request.user.business`), set via middleware into `request.business`. **Never trust a tenant ID passed in from the client** (e.g., in a query param or request body) without cross-checking it against the authenticated user's actual tenant membership.  
5. **Celery tasks are tenant-aware:** Background jobs (reminder scheduling, "no activity in N days" escalation, warranty reminders) run outside the request/response cycle, so they cannot rely on thread-local or request-scoped tenant context. Every task payload must explicitly include the relevant `business_id`, and the task must re-verify/scope its DB queries by that ID.  
6. **Indexing for performance:** Composite indexes should lead with `business_id` (e.g., `(business_id, stage)`, `(business_id, assigned_user_id, due_date)`) since nearly every query in the system will be scoped to a single tenant first.  
7. **Cross-tenant safety tests:** Include automated tests specifically designed to attempt cross-tenant data access (e.g., User from Business A requesting a Lead ID belonging to Business B) and assert a 403/404, not a data leak. This should be a standing part of the test suite, not a one-time check.  
8. **Per-tenant configuration:** Fields like follow-up rule timing (e.g., "remind in 2 days" vs. "remind in 3 days"), notification channel preference, and (later) custom pipeline stages should be stored per-`Business`, not globally — different businesses will want different defaults over time even if v1 ships with one fixed default set.  
9. **Superadmin/internal tooling:** Build a lightweight internal-only view (not exposed to tenant users) for cross-tenant visibility — useful for support, debugging, and understanding usage patterns across all customers.

**Out of scope for v1:** Tenant-configurable pipeline stages, per-tenant custom fields, and schema-per-tenant migration are all deferred — the `business_id`\-scoped shared-schema model is sufficient until there's a concrete customer or scale reason to revisit it.

---

## 13\. Open Questions

1. What's the primary reminder/notification channel for the target market — WhatsApp, SMS, or in-app/email? (Likely WhatsApp given regional usage patterns, but this has API cost/approval implications.)  
2. Should lead capture integrate directly with WhatsApp Business (auto-creating leads from incoming messages) in v1, or is manual entry acceptable for the pilot?  
3. Pricing model — flat monthly fee, per-seat, or per-lead? Needs validation with target businesses.  
4. How configurable should the pipeline/stages be in v1 — fixed for all industries, or lightly customizable labels?  
5. Who is the ideal pilot customer segment to start with (solar, CCTV, real estate, construction) — should validate with 3–5 real businesses before building broadly?

---

## 14\. Risks

- **Adoption risk:** Small business owners are often resistant to changing habits (WhatsApp/Excel is "free" and familiar) — onboarding must be nearly frictionless.  
- **Notification cost/reliability risk:** WhatsApp Business API and SMS gateways have per-message costs and approval processes that could affect unit economics.  
- **Scope creep risk:** Given how easy it is to imagine "just one more feature" (quotation builder, invoicing, etc.), there's a real risk of drifting toward becoming "just another CRM." The PRD's non-goals section should be revisited before every roadmap planning cycle.

---

## 15\. Naming

"NeverForget" is a placeholder. Other candidate directions: FollowUp\[.io\], StayOnTop, LeadKeeper, Recall CRM, NoLeadLeftBehind. Final naming should be validated for domain availability and regional resonance before launch.  
