# Security policy

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub:
[Security → Report a vulnerability](https://github.com/kidchemical/Cuttle/security/advisories/new).
Do not open a public issue for a security problem.

Include what you can: affected version or commit, how Cuttle was running
(daemon, Electron Host/Client, Android app), steps to reproduce, and impact.

Cuttle is maintained by one person. Expect an acknowledgement within about a
week. Fixes land on `main` first; once releases exist, the advisory will name
the first fixed release.

## Supported versions

Only the latest `main` (and, once published, the latest GitHub Release) gets
security fixes.

## Threat model

Cuttle is self-hosted control software for **your own machines on a trusted
LAN**. Knowing what it is designed to do helps decide what counts as a
vulnerability.

- **Agents run with your permissions, by design.** Hosted agent CLIs (Cursor,
  Codex, Claude Code, …) can read and write files and run commands as the user
  who started Cuttle. An agent doing that is not a vulnerability; a *remote or
  unauthenticated party* making it happen is.
- **The network boundary is part of the design.** Exposing Cuttle to the
  internet without the steps in the README's
  [security notes](README.md#security-notes) is unsupported.

In scope, for example:

- Authentication, session, pairing or CORS bypass on the Flask API
- Forging or replaying signed action cards (`git.push`, `flask.restart`, …)
- Path traversal (uploads, `/output/…`, project paths)
- Secret leakage (`src/.env`, `.cuttle/personal/secrets/`, tokens in logs or
  chat transcripts)
- TLS validation failures in the Electron or Android clients
- A Cuttle Workers peer executing jobs it was not authorized for

Out of scope: vulnerabilities in vendor agent CLIs themselves (report those to
the vendor), and attacks that require an already-compromised account on the
host machine.
