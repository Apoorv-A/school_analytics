# ADR 0002: Shared Application with PostgreSQL RLS

**Status:** Accepted

## Context

Per-tenant deployments increase operational cost. Row-level sharing requires defense in depth.

## Decision

Deploy one application instance per environment serving all tenants. Use `tenant_id` on all school-owned tables plus PostgreSQL RLS as the database backstop.

Registry tables (`tenants`, `tenant_domains`) are **excluded** from RLS because hostname resolution and the tenant operator read them before request tenant context exists.

## Consequences

- Simpler deployment and upgrades (one image, one Helm release).
- Application bugs must not bypass tenant predicates; RLS catches ORM mistakes.
- SQLite (local/tests) uses application-level filtering only; RLS tests require PostgreSQL.
