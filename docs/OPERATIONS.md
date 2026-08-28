# Production Operations Contract

Xushu exposes separate liveness and readiness probes so process health is not conflated with serving readiness.

## Probe endpoints

| Endpoint | Success | Meaning |
| --- | --- | --- |
| `GET /health/live` | `200 {"status":"ok"}` | The ASGI process is alive and can answer HTTP. It does not query SQLite, workspace state, network research, model providers, or serving adapters. |
| `GET /health/ready` | `200 {"status":"ready"}` | Durable workspace state is reachable, the local worker has converged to the durable catalog revision, and no workspace update lease is active. |

Both endpoints are intentionally outside `/api/`, contain no workspace data, token, worker identifier, revision value, or user information, and remain available when production API authentication is enabled.

## Readiness behavior

Readiness is fail-closed, but it is not passive.

Before reporting a revision mismatch, the worker calls the same workspace synchronization boundary used by the API. If another worker has committed a new catalog revision and the shared catalog file matches that revision, the probing worker reloads the workspace and can return to ready without requiring unrelated user traffic.

`/health/ready` returns HTTP 503 when any of these conditions hold:

- the durable store cannot be read;
- the local workspace cannot safely converge to the durable revision;
- the durable revision is missing or still differs from the local revision after synchronization;
- a workspace update lease is active.

A workspace update making readiness temporarily false is expected. It should not make liveness false, because an update is a traffic-readiness condition rather than a process failure.

## Container healthcheck

The repository Docker image uses `/health/ready` as its `HEALTHCHECK` target:

```text
interval:     30s
timeout:       3s
start period: 10s
retries:       3
```

The probe uses Python's standard library inside the image, so no additional `curl` or `wget` package is required.

For orchestrators that support separate probes, prefer:

```text
livenessProbe  -> GET /health/live
readinessProbe -> GET /health/ready
```

Do not use `/api/status` as a container health endpoint. It is part of the authenticated product API and includes product/workspace information that health infrastructure does not need.

## Failure interpretation

A liveness failure means the process or HTTP server is unavailable and may justify a restart according to the deployment platform.

A readiness failure means the process is alive but should not receive new product traffic until durable state is safe to serve. Restarting solely because readiness is false can make workspace updates or temporary storage incidents harder to recover from.

## Related contracts

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — durable runtime, workspace synchronization and recovery boundaries.
- [`HARNESS_CONTRACT.md`](HARNESS_CONTRACT.md) — execution, checkpoint and authority semantics.
- [`ACCEPTANCE.md`](ACCEPTANCE.md) — verifiable runtime and product acceptance criteria.
