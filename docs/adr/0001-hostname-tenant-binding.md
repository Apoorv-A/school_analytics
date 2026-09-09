# ADR 0001: Hostname Tenant Binding

**Status:** Accepted

## Context

Multi-tenant SaaS must route each request to exactly one school. Shared path-based routing (`/t/sunrise/...`) leaks tenant identity in URLs and complicates cookies.

## Decision

Bind tenant identity to the HTTP `Host` header via `tenant_domains.hostname`. Each school gets a dedicated hostname (e.g. `sunrise.example.com`).

## Consequences

- DNS and TLS must be provisioned per tenant hostname.
- Hostname uniqueness is enforced at config validation time.
- Smoke tests verify tenant resolution by hostname, not port-forward.
