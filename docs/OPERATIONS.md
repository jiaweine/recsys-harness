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

## Reverse-proxy client identity

Login, attachment, import and task rate limits are keyed by the resolved client address. `X-Forwarded-For` is therefore a security boundary, not ordinary request metadata.

By default, Xushu ignores `X-Forwarded-For` and uses the direct TCP peer address. To trust a reverse proxy, name the proxy network explicitly:

```text
LINGJING_TRUSTED_PROXY_CIDRS=172.18.0.0/16,10.20.30.40/32
```

The immediate peer must belong to one of those networks before the header is considered. Xushu then walks the forwarding chain from right to left, removes trusted proxy hops and uses the first untrusted address as the client identity. This protects append-style proxies from attacker-supplied leftmost XFF values.

Malformed forwarding chains fail closed to the direct peer. If every address in the chain is trusted, Xushu also collapses to the direct peer rather than accepting a spoofable leftmost value.

`LINGJING_TRUST_PROXY_IP=1` remains only as a compatibility mode for loopback proxies (`127.0.0.0/8` and `::1/128`). A proxy in another container, host or load-balancer network must use `LINGJING_TRUSTED_PROXY_CIDRS` explicitly.

Do not configure broad private ranges unless the entire range is controlled proxy infrastructure. If application workers are directly reachable from untrusted networks, those direct connections will ignore XFF even when trusted proxy CIDRs are configured.

## Container runtime boundary

The repository image is built in two stages. The builder produces the project wheel and its runtime dependency wheels from `pyproject.toml`; the final image installs those wheels with `--no-index`. Development-only dependencies such as `pytest` and `httpx` are not installed into the runtime image.

The final container also keeps mutable state separate from application code:

```text
application / installed package  -> read-only runtime surface
LINGJING_DATA_DIR                 -> /data
container user                    -> xushu (non-root)
```

`/data` is declared as a Docker volume. In production, mount a durable volume there if conversations, run checkpoints, strategy memory, attachments and imported workspace data must survive container replacement.

For a host bind mount, make sure the host directory is writable by the container's `xushu` user. A named Docker volume avoids most host UID/GID mismatches.

The `Container QA` workflow builds the actual image and verifies that:

- runtime imports succeed without the source checkout;
- development-only Python dependencies are absent;
- the configured container user is non-root;
- protected production mode starts successfully;
- the Docker health state reaches `healthy`;
- `/api/status` remains authenticated while health endpoints remain available.

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
