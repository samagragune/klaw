# Security Policy

Klaw sits between AI agents and the actions they take, so security issues matter here.

## Reporting a vulnerability

Please **do not open a public issue**. Report privately via
[GitHub private vulnerability reporting](https://github.com/samagragune/klaw/security/advisories/new).
Include what you found, how to reproduce it, and the impact you expect. We aim to acknowledge
reports within a few days and will credit you in the fix unless you'd rather not be named.

## Scope

In scope: the supervisor (`backend/`), adapters (`companion/`, `sdk/`) and the dashboard
(`dashboard/`) — for example authentication or tenant-isolation bypasses, a way for an
integration key to exceed its scope, a way to make an adapter break or hang the agent it
watches, or a decision that should block being silently skipped.

Out of scope: the risk rules not recognising a particular dangerous command (they are pattern
rules and don't claim completeness — please open a normal issue or PR to add a rule).

## Supported versions

Security fixes go into `main` and the latest release.
