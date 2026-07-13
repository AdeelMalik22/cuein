# Cuein

Cuein is a lightweight lead follow-up tool for small service businesses. It helps teams record leads, assign an owner, move them through a sales pipeline, and eventually schedule the next follow-up so opportunities are not forgotten.

The application is being built from scratch, step by step, with special attention to understanding multi-tenancy in Django.

## What multi-tenancy means here

One running application serves many independent businesses. Each business is a tenant.

- A `Business` is the tenant.
- Each user belongs to one Business.
- Products and Leads inherit `TenantScopedModel`, which gives them a required `business_id`.
- API queries start from the authenticated user’s Business, preventing one business from reading another business’s data.

## Current functionality

- Business registration and JWT login.
- Owner-only team management.
- Products scoped to one business.
- Leads scoped to one business, with assignment, pipeline stage transitions, search, filters, and role-based visibility.

## API overview

| Area | Endpoint |
| --- | --- |
| Sign up | `POST /api/v1/auth/signup/` |
| Login | `POST /api/v1/auth/token/` |
| Current user | `GET /api/v1/me/` |
| Current business | `GET/PATCH /api/v1/business/` |
| Team users | `/api/v1/users/` |
| Products | `/api/v1/products/` |
| Leads | `/api/v1/leads/` |
| Assign lead | `POST /api/v1/leads/{id}/assign/` |
| Change stage | `POST /api/v1/leads/{id}/transition/` |

All API routes require JWT authentication except signup, login, and token refresh.

## Local setup

1. Ensure PostgreSQL is running and create the configured database/user.
2. Create the root `.env` file with local PostgreSQL connection values:

   ```text
   POSTGRES_DB=cuein
   POSTGRES_USER=your_user
   POSTGRES_PASSWORD=your_password
   POSTGRES_HOST=127.0.0.1
   POSTGRES_PORT=5432
   ```

3. Apply migrations and run checks:

   ```bash
   venv/bin/python manage.py migrate
   venv/bin/python manage.py check
   venv/bin/python manage.py test
   ```

4. Start the development server:

   ```bash
   venv/bin/python manage.py runserver
   ```

The API will be available at `http://127.0.0.1:8000/api/v1/`.

## Important development rule

Never hand-write a Django migration. After model changes, generate it with:

```bash
venv/bin/python manage.py makemigrations
```

See `PRD-FollowUpCRM.md` for the product requirements and `plan.md` for the delivery plan.
