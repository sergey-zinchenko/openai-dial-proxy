# Security Policy

## Reporting security issues

Please do not report security vulnerabilities in public issues.

Preferred channel:

- GitHub Security Advisory form: `/security/advisories/new`

Fallback channel:

- Email: `sergey.zinchenko.rnd@gmail.com`

Please include:

- affected image tag or git revision
- how the proxy is exposed (Ingress or HTTPRoute)
- clear reproduction steps
- expected vs actual behavior
- impact assessment
- any proposed fix or mitigation

## Sensitive areas of particular interest

- Auth header handling (`Authorization` / `Api-Key`) that forwards a secret to the wrong upstream or logs it.
- Path routing that lets this proxy answer DIAL Core platform routes (`/v1/ops`, `/v1/applications`, `/v1/toolsets`).
- Request smuggling or response-header leaks (hop-by-hop, `content-encoding` on an already decoded body).
- Vulnerable packages that ship in the published container image.

## Triage and response

This project is maintained by a single maintainer on a best-effort basis.

I will acknowledge valid reports as soon as possible, reproduce and triage the issue, and publish a fix when ready. There is no guaranteed SLA.

## Scope

This policy covers the proxy source, the container image built by this repository, and the example Helm values.

It does not cover AI DIAL Core, the dial-extension chart, your Gateway or Ingress controller, or the model behind Core.

## Disclosure

For confirmed vulnerabilities I aim to fix first, then publish an advisory. If users need a mitigation before a patched image exists, I may publish that guidance earlier.
