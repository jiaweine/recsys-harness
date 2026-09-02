from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


async def _wait_terminal(client, run_id: str, *, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/runs/{run_id}")
        if response.status_code != 200:
            raise AssertionError(f"run poll failed: {response.status_code} {response.text}")
        row = response.json()
        if row.get("status") in {"completed", "failed", "cancelled"}:
            return row
        await asyncio.sleep(0.025)
    raise AssertionError(f"run did not reach terminal state: {run_id}")


async def _exercise_api(workers: int, race: int) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient
    import lingjing_harness.api as api_module

    workers = max(1, int(workers))
    race = max(2, int(race))
    started = time.perf_counter()
    transport = ASGITransport(app=api_module.app)

    async with api_module.lifespan(api_module.app):
        # This is an in-process ASGI client, not a public deployment hostname.
        # Use the standard trusted synthetic host so the stress harness exercises
        # the same Host boundary as TestClient without weakening production
        # LINGJING_ALLOWED_HOSTS defaults.
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            status = await client.get("/api/status")
            if status.status_code != 200:
                raise AssertionError(f"status failed: {status.status_code} {status.text}")

            async def run_one(worker: int) -> dict[str, Any]:
                created = await client.post(
                    "/api/conversations",
                    json={"scene": "search", "title": f"api-stress-{worker}"},
                )
                if created.status_code != 200:
                    raise AssertionError(
                        f"conversation create failed: {created.status_code} {created.text}"
                    )
                cid = created.json()["id"]
                accepted = await client.post(
                    f"/api/conversations/{cid}/messages",
                    json={
                        "content": "检查搜索“露营灯”的结果是否正常",
                        "attachments": [],
                        "allow_network": False,
                    },
                )
                if accepted.status_code != 200:
                    raise AssertionError(
                        f"run create failed: {accepted.status_code} {accepted.text}"
                    )
                run_id = accepted.json()["run_id"]
                terminal = await _wait_terminal(client, run_id)
                if terminal.get("status") != "completed":
                    raise AssertionError(
                        f"concurrent run ended as {terminal.get('status')}: {terminal.get('error')}"
                    )
                if not isinstance(terminal.get("result"), dict):
                    raise AssertionError("completed concurrent run lost its result payload")
                return terminal

            completed = await asyncio.gather(*(run_one(index) for index in range(workers)))
            if len(completed) != workers:
                raise AssertionError("concurrent run count mismatch")

            # Hold one run open before entering the real executor so all concurrent
            # messages race on the durable reservation rather than winning after a
            # very fast sample-data run has already completed.
            original_execute = api_module._execute

            async def delayed_execute(*args, **kwargs):
                await asyncio.sleep(0.35)
                return await original_execute(*args, **kwargs)

            api_module._execute = delayed_execute
            try:
                created = await client.post(
                    "/api/conversations",
                    json={"scene": "search", "title": "same-conversation-race"},
                )
                cid = created.json()["id"]

                async def compete(index: int):
                    return await client.post(
                        f"/api/conversations/{cid}/messages",
                        json={
                            "content": f"检查搜索“露营灯”的结果 #{index}",
                            "attachments": [],
                            "allow_network": False,
                        },
                    )

                responses = await asyncio.gather(*(compete(index) for index in range(race)))
                winners = [response for response in responses if response.status_code == 200]
                conflicts = [response for response in responses if response.status_code == 409]
                if len(winners) != 1 or len(conflicts) != race - 1:
                    raise AssertionError(
                        "same-conversation reservation lost atomicity: "
                        f"accepted={len(winners)} conflicts={len(conflicts)} race={race}"
                    )
                race_run_id = winners[0].json()["run_id"]
                race_terminal = await _wait_terminal(client, race_run_id)
                if race_terminal.get("status") != "completed":
                    raise AssertionError("reservation winner did not complete")

                # A workspace replacement must not slip through while a task is
                # durably active, even before the worker reaches the first tool.
                cancel_conversation = await client.post(
                    "/api/conversations",
                    json={"scene": "audit", "title": "cancel-import-race"},
                )
                cancel_cid = cancel_conversation.json()["id"]
                accepted = await client.post(
                    f"/api/conversations/{cancel_cid}/messages",
                    json={
                        "content": "做一次全局体检",
                        "attachments": [],
                        "allow_network": False,
                    },
                )
                if accepted.status_code != 200:
                    raise AssertionError(f"cancel test run was not accepted: {accepted.text}")
                cancel_run_id = accepted.json()["run_id"]

                import_response = await client.post(
                    "/api/data/import",
                    json={"name": api_module.catalog.name, "data": api_module.catalog.to_payload()},
                )
                if import_response.status_code != 409:
                    raise AssertionError(
                        f"workspace import crossed an active-run fence: {import_response.status_code}"
                    )

                cancelled = await client.post(f"/api/runs/{cancel_run_id}/cancel")
                if cancelled.status_code != 200:
                    raise AssertionError(
                        f"cancel request failed: {cancelled.status_code} {cancelled.text}"
                    )
                cancel_terminal = await _wait_terminal(client, cancel_run_id)
                if cancel_terminal.get("status") != "cancelled":
                    raise AssertionError(
                        f"cancelled run ended as {cancel_terminal.get('status')}"
                    )
            finally:
                api_module._execute = original_execute

            conversations = await client.get("/api/conversations")
            if conversations.status_code != 200:
                raise AssertionError("conversation listing failed after contention")

    elapsed = max(time.perf_counter() - started, 1e-9)
    return {
        "workers": workers,
        "completed_runs": workers,
        "same_conversation_race": race,
        "reservation_winners": 1,
        "reservation_conflicts": race - 1,
        "active_import_conflicts": 1,
        "cancelled_runs": 1,
        "elapsed_seconds": round(elapsed, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ASGI lifecycle contention smoke test for Xushu API run orchestration."
    )
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--race", type=int, default=8)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="xushu-api-stress-") as directory:
        os.environ["LINGJING_DATA_DIR"] = str(Path(directory))
        os.environ["LINGJING_ENV"] = "development"
        os.environ["LINGJING_TRUST_PROXY_IP"] = "0"
        result = asyncio.run(_exercise_api(args.workers, args.race))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
