# Security Policy

Xushu includes production-facing authentication, proxy identity handling, attachment processing, optional network access, durable state, and multi-worker coordination. Security reports should therefore avoid publishing exploit details before maintainers have had a chance to assess them.

## Supported code

Security fixes target the latest published release and the current `main` branch. Older snapshots may not receive a backport unless the affected contract is still part of the supported release line.

## Reporting a vulnerability

Please **do not open a public GitHub Issue** for a suspected vulnerability that could expose credentials, sessions, private data, filesystem contents, network authority, durable-state integrity, or a practical denial-of-service path.

Use GitHub's private vulnerability reporting / Security Advisory flow for this repository when that option is available. If the GitHub UI does not offer a private report, contact the repository owner through the GitHub profile and request a private channel before sending exploit details.

A useful report includes:

- affected commit or release;
- deployment mode and relevant non-secret configuration;
- minimal reproduction steps;
- expected versus observed security boundary;
- impact and prerequisites;
- whether the issue is already being exploited or publicly known;
- a proposed regression test or fix, when available.

Never include access tokens, session cookies, private datasets, or other live secrets in a report.

## Security-sensitive boundaries

Reports are especially useful when they demonstrate a concrete failure in one of these contracts:

- production authentication or session validation;
- trusted-proxy / client identity handling;
- shared rate limiting;
- attachment type, path, retention, or storage isolation;
- per-run network authorization;
- tool risk / side-effect authority;
- workspace publication and revision fencing;
- run ownership, lease, recovery, cancellation, or stale-worker fencing;
- online-experiment control-plane authorization or durable state;
- browser CSP, same-origin behavior, or unintended data exposure.

## Disclosure

Please coordinate public disclosure until a fix or mitigation is available. Once a security change is public, the repository should keep the regression test and the relevant contract documentation alongside the fix so the boundary remains verifiable.
