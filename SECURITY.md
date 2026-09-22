# Security Policy

## Reporting a vulnerability

Please report security issues privately via
[GitHub's private vulnerability reporting](https://github.com/galak06/persona/security/advisories/new)
rather than opening a public issue.

Include what the issue is, how to reproduce it, and what an attacker could
do with it. You can expect an initial response within a week.

## Handling credentials

Persona talks to several APIs on your behalf. A few things worth knowing:

- **Credentials come from the environment**, or from your brand directory —
  never from source. Don't commit them; `app/.env` and brand directories
  are gitignored.
- **Keys go in headers, not URLs.** A key in a query string is written to
  httpx's request log at INFO level, so running with `-v` would print it
  to stdout and into any log a user shares. The studio's tests assert
  this; please keep it that way.
- **Your brand directory holds session state**, including browser sessions
  for any platform you have logged into. Treat it like a credential store.

## Supported versions

Persona is developed on `main`. Fixes land there; there are no maintained
release branches yet.
