# Cuein Architecture Guide

This document explains how Cuein keeps multiple businesses separate while one
person can move between them without signing in again.

## The core idea

Cuein has two different concepts:

| Concept | Meaning |
| --- | --- |
| `User` | A person's global login identity: username, password, email, profile picture, and account status. |
| `Business` | A private workspace/tenant with its own leads, services, follow-ups, reports, and team. |
| `Membership` | The link between a user and a business. It holds that person's role in that particular workspace. |

One user can therefore have multiple memberships:

```text
Sara Brown (one login)
    │
    ├── Smith LLC       → Owner
    ├── Bright CCTV     → Manager
    └── North Star Solar → Salesperson
```

The role belongs to the membership, not to the global user. Sara can be an
Owner in one business and a Salesperson in another.

`User.business` remains in the model as a legacy compatibility key for now.
It is **not** used to choose the active workspace or authorize access. New
multi-business behavior always uses `Membership`.

## Main data relationships

```text
User ──< Membership >── Business
                         │
                         ├── Leads
                         ├── Products / services
                         ├── Follow-up tasks
                         ├── Notifications
                         └── Activities
```

Every business-owned record has a required `business_id`. That is the tenant
boundary: a lead from Smith LLC cannot be returned by a Bright CCTV query.

## Browser authentication and workspace switching

The web app uses Django's normal session authentication.

```text
1. User signs in
   Browser receives a secure Django session cookie.

2. User opens a page
   Django reads the session cookie and identifies request.user.

3. Cuein resolves the active workspace
   request.session['active_business_id'] is read.

4. Cuein validates membership on the server
   Membership must match:
   - request.user
   - active_business_id
   - membership.is_active = True
   - business.is_active = True

5. The validated membership becomes the request context
   active business + active role are used for the entire request.
```

If the saved workspace is missing, inactive, or no longer belongs to the
person, Cuein selects a valid membership instead. If there are no valid
memberships, it shows the no-workspace state.

### Switching workspace

The workspace switcher submits a business ID to `workspaces/switch/`.
The ID is never trusted by itself. Cuein queries the membership table again
before saving it to the session.

```text
POST business_id
      │
      ▼
Does this authenticated user have an active membership in this business?
      │
      ├── No  → 403 Forbidden
      └── Yes → save active_business_id in the session → dashboard
```

The redirect always starts in the new workspace rather than preserving a URL
from the old one. This avoids accidentally opening an old-business lead URL in
the new context.

## API authentication (JWT)

The API uses JWTs, which are stateless. A session-style mutable active business
would be unsafe for API calls, so each API token is bound to one business.

### Obtaining a token

For a person with one active membership, a normal login can choose it.
For a person with several active memberships, the client must send a
`business_id` when requesting a token:

```json
POST /api/v1/auth/token/
{
  "username": "sara",
  "password": "your-password",
  "business_id": "the-workspace-uuid"
}
```

Cuein verifies the membership, then writes the selected business ID into the
refresh token and access token.

### Using a token

```text
Bearer access token
      │
      ▼
JWT signature and expiry are verified
      │
      ▼
business_id claim is read from the token
      │
      ▼
Membership is checked again against the database
      │
      ├── Invalid / removed / inactive → authentication fails
      └── Valid → request.active_business and request.active_role are set
```

This is why Cuein does not use an `X-Business-ID` request header. A header is
client-controlled and easy to apply inconsistently. The active API business is
instead part of a signed token and is revalidated on each request.

## How data stays isolated

Tenant safety is applied in several layers:

1. **Query scope** — tenant-owned models use `for_business(business)` before
   looking up leads, tasks, products, activities, or notifications.
2. **Role scope** — salespeople only receive their assigned leads/tasks;
   owners and managers can see business-wide records where allowed.
3. **Object lookup scope** — a URL for another business's lead does not expose
   it; the scoped query returns `404`.
4. **Model validation** — assigning a lead, task, activity, or notification to
   a user requires that user to have a membership in the same business.
5. **Write scope** — business IDs are chosen from the validated request
   workspace, never accepted blindly from a form or API payload.
6. **Background work** — Celery tasks receive an explicit `business_id` and
   query within that business, rather than relying on a user session.

The important rule for future code is:

> Never query tenant data from `Model.objects` alone when a business is known.
> Start from `Model.objects.for_business(active_business)`.

## Why pages and API lists are fast

Cuein keeps the common working path small and indexed.

- Membership has indexes for active membership lookup by user and business.
- Tenant-owned tables are indexed by business plus the fields commonly used on
  dashboards and lead lists, such as stage, assignee, and activity date.
- Views use `select_related()` for common foreign keys, avoiding a separate
  database query for every lead's assignee or product.
- The lead API caches list/kanban responses with a key that includes the
  business, role scope, user where needed, filters, and a per-business cache
  version.
- Lead writes invalidate only that business's lead cache, so another business's
  cache remains warm.

In practical terms, a typical browser page needs a session user lookup, one
validated membership lookup, then one business-scoped query. It does not scan
all businesses or all leads.

## Important request flows

### New signup

```text
Signup form/API
    → PendingRegistration
    → email verification code
    → Business created
    → User created
    → Owner Membership created
    → user enters the new workspace
```

No business or user is created until email verification succeeds.

### Owner adds another business

```text
Owner opens workspace switcher
    → Add new business
    → submits name, industry, timezone
    → Business created
    → Owner Membership created for the same user
    → active_business_id saved in session
    → dashboard for the new business
```

The old `User.business` compatibility key remains unchanged. The active
workspace is determined by membership and session context.

### Adding a team member

```text
Owner creates team member in the current workspace
    → User account is created or updated
    → Membership is created/updated for the current business
    → role and active status apply to that business only
```

## Files worth knowing

| File | Responsibility |
| --- | --- |
| `core/models.py` | `Business`, `User`, `Membership`, and tenant base model. |
| `core/tenancy.py` | Resolves/validates active web and API workspace context. |
| `core/authentication.py` | Business-scoped JWT authentication. |
| `core/token_views.py` | API token endpoint that chooses a membership. |
| `core/permissions.py` | Owner and manager permission checks using active membership role. |
| `web/views.py` | Session workspace resolution, switching, and owner business creation. |
| `leads/views.py`, `followups/views.py` | Business-scoped API queries and role filtering. |
| `leads/cache.py` | Tenant-safe lead list/kanban cache keys and invalidation. |

## Rules for future development

- Do not add a client-controlled `business_id` header as an authorization
  mechanism.
- Do not use `request.user.business` for new business-scoped work. Use the
  resolved active membership/business.
- Do not use a global user role to authorize a workspace action. Use the
  membership role for the active business.
- Always validate that assignees belong to the current business through
  membership.
- Always pass `business_id` explicitly into background tasks.
- When adding a new tenant-owned model, inherit from `TenantScopedModel` and
  add business-first indexes for its main query patterns.
- Keep cross-business reporting and data transfer separate from normal
  workspace behavior; they need deliberate product and security design.

## Operational notes

After deploying membership changes, run migrations and the idempotent legacy
backfill command:

```bash
python3 manage.py migrate
python3 manage.py backfill_memberships
```

The backfill creates a membership for old users that only have the legacy
`User.business` relationship. It is safe to run more than once.
