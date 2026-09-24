# Security Policy

JARVIS can execute local tools, interact with the desktop and expose agent capabilities, so security reports are taken seriously.

## Supported development line

Security fixes are expected to target the current `main` branch while the project is under active development.

## Reporting a vulnerability

Please avoid publishing exploitable details in a public issue before the maintainer has had a chance to investigate.

When reporting a security-sensitive problem, include as much of the following as possible:

- affected component/file;
- reproduction steps;
- expected vs. observed behavior;
- required local configuration;
- whether the issue crosses a session/user/lease boundary;
- whether it can trigger system, browser, filesystem or external side effects;
- logs with secrets and personal data removed;
- a minimal proof of concept when safe to share.

If GitHub private vulnerability reporting is available for this repository, prefer that channel. Otherwise contact the maintainer privately through the contact options on the GitHub profile before disclosing exploit details publicly.

## Security model

The project is designed around several trust boundaries:

- the AI model proposes actions but should not receive unrestricted host authority;
- sensitive tool calls are evaluated by the Policy Engine;
- physical/computer-control capabilities use temporary authority/leases;
- local HTTP/WebSocket access is expected to be bound to loopback by default;
- authentication/session identity must be preserved across confirmation and execution;
- privileged operations should fail closed when required identity or authority is missing;
- API keys and tokens belong in environment configuration, never in source control;
- programs started by JARVIS (apps, browser, IDE, the `agy` CLI, MCP servers) never inherit `JARVIS_TOKEN` or the provider keys (`processos.py`);
- URLs chosen by the model are fetched only from the public internet: loopback, the local network and cloud metadata are refused, including through redirects and DNS rebinding (`rede_segura.py`).

## Out of scope

The following are generally not treated as vulnerabilities by themselves:

- intentionally enabled local automation behaving as documented;
- attacks that require replacing trusted source code or a user's local `.env` first;
- failures caused exclusively by unsupported third-party services without a JARVIS trust-boundary impact.

They may still be valid bug reports if they reveal unsafe or misleading behavior.

## Secret handling

Never include real credentials in issues, pull requests, screenshots, test fixtures or logs. The repository CI includes secret scanning, but contributors remain responsible for keeping credentials out of Git history.
