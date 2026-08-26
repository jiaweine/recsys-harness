"""Capture README screenshots from the real product UI and smoke-test responsive interaction."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "readme-assets"
BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "最近搜索“露营灯”的结果不太准，结合附件帮我复现、诊断并探索一个可验证的改进方向，但先不要改变当前策略。"
SCREENSHOTS = (
    "workbench.png",
    "overview.png",
    "evidence.png",
    "mobile-workspace.png",
    "mobile-progress.png",
    "mobile-evidence.png",
)
MOBILE_VIEWPORT = {"width": 393, "height": 852}


def post_json(path: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    post_json("/api/conversations", {"scene": "search", "title": "露营灯搜索体验复核"})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        browser_errors: list[str] = []
        bad_responses: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)
        page.on(
            "response",
            lambda response: bad_responses.append(f"{response.status} {response.url}")
            if response.status >= 400 and response.url.startswith(BASE_URL)
            else None,
        )
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.getElementById('dataMeta').textContent.includes('内容')", timeout=15_000)

        page.locator("#fileInput").set_input_files({
            "name": "search-context.json",
            "mimeType": "application/json",
            "buffer": json.dumps({"query": "露营灯", "note": "首屏结果相关性需要复核"}, ensure_ascii=False).encode("utf-8"),
        })
        page.wait_for_selector(".attachment-card", timeout=10_000)
        page.wait_for_timeout(650)
        page.screenshot(path=str(ASSET_DIR / "workbench.png"))

        if page.locator(".history-item").count() and page.locator(".history-item").first.evaluate("el => el.tagName") != "BUTTON":
            raise RuntimeError("History items must be keyboard-native buttons")

        page.evaluate("""() => {
          const original = window.fetch.bind(window);
          let failed = false;
          window.fetch = (input, init) => {
            const url = typeof input === 'string' ? input : String(input?.url || '');
            if (!failed && url.includes('/api/runs/')) {
              failed = true;
              return Promise.reject(new TypeError('simulated transient run polling failure'));
            }
            return original(input, init);
          };
        }""")

        page.locator("#input").fill(PROMPT)
        page.locator("#sendBtn").click()
        page.wait_for_function("document.getElementById('stateText').textContent === '已完成'", timeout=30_000)

        # The completed engineering console must be backed by the actual run payload,
        # not merely by decorative empty containers. Waiting on [hidden] also covers
        # the async response-clone rendering used by the additive UI modules.
        for selector in (
            "#resultSnapshot:not([hidden])",
            "#resultAnalysis:not([hidden])",
            "#strategyExperiment:not([hidden])",
            "#runTelemetry:not([hidden])",
            "#verificationSummary:not([hidden])",
            "#missionSummary:not([hidden])",
            "#agentTrace:not([hidden])",
            "#runControlPlane:not([hidden])",
            "#learningLedger:not([hidden])",
        ):
            page.wait_for_selector(selector, state="attached", timeout=8_000)
            if page.locator(selector).evaluate("el => el.hidden"):
                raise RuntimeError(f"Completed engineering surface stayed hidden: {selector}")

        rank_rows = page.locator("#resultAnalysis .rank-row").count()
        if rank_rows < 3:
            raise RuntimeError(f"Ranked Result Analysis rendered too few real rows: {rank_rows}")
        rank_text = page.locator("#resultAnalysis").inner_text()
        for label in ("匹配", "质量", "新鲜", "热度"):
            if label not in rank_text:
                raise RuntimeError(f"Search ranking explanation lost the real signal column: {label}")

        experiment_blocks = page.locator("#strategyExperiment .experiment-block").count()
        if experiment_blocks < 1:
            raise RuntimeError("Strategy Experiment did not render the real evolution action")
        if page.locator("#strategyExperiment .experiment-gate").count() < 4:
            raise RuntimeError("Strategy Experiment did not keep the independent evaluation gates visible")
        if page.locator("#strategyExperiment .metric-compare > div").count() < 2:
            raise RuntimeError("Strategy Experiment did not render current/candidate metric comparison")
        if "未改变当前策略" not in page.locator("#strategyExperiment").inner_text():
            raise RuntimeError("No-adaptation QA prompt incorrectly presented a candidate as active")

        trace_steps = page.locator("#agentTrace .trace-step").count()
        if trace_steps < 6:
            raise RuntimeError("Agent Trace did not render enough structured run events")
        if page.locator("#missionSummary .mission-requirement").count() < 1:
            raise RuntimeError("Mission Graph did not render evidence requirements")
        if "CONTROL PLANE" not in page.locator("#runControlPlane").inner_text():
            raise RuntimeError("Control Plane did not reconcile completed run boundaries")
        if "LEARNING LEDGER" not in page.locator("#learningLedger").inner_text():
            raise RuntimeError("Learning Ledger did not render durable state")
        if "search_diagnosis" in page.locator("#agentTrace").inner_text():
            raise RuntimeError("Completed Trace leaked an internal requirement key")

        progress_badge = page.locator(".tab[data-tab='progress'] .tab-count")
        evidence_badge = page.locator(".tab[data-tab='evidence'] .tab-count")
        if progress_badge.count() != 1 or progress_badge.inner_text() != str(trace_steps):
            raise RuntimeError("Progress tab count is not synchronized with structured trace events")
        evidence_items = page.locator("#evidenceList .evidence-item").count()
        if evidence_items and (evidence_badge.count() != 1 or evidence_badge.inner_text() != str(evidence_items)):
            raise RuntimeError("Evidence tab count is not synchronized with inspectable evidence")

        stale_reconnect = page.locator("#toast").evaluate("el => el.classList.contains('show') && el.textContent.includes('连接暂时中断')")
        if stale_reconnect:
            raise RuntimeError("Recovered run left a stale reconnect notice visible after completion")

        desktop_inspector = page.locator("#inspector").bounding_box()
        if not desktop_inspector or desktop_inspector["width"] < 330:
            raise RuntimeError(f"Desktop evidence rail is too narrow for mission/trace content: {desktop_inspector}")

        page.wait_for_timeout(250)
        page.locator("#scrollArea").evaluate("el => { el.scrollTop = 0; }")
        page.screenshot(path=str(ASSET_DIR / "overview.png"))

        page.locator(".tab[data-tab='evidence']").click()
        page.wait_for_timeout(120)
        page.screenshot(path=str(ASSET_DIR / "evidence.png"))

        # Mobile QA uses a modern phone viewport and captures three distinct states.
        # The evidence inspector is intentionally a bottom sheet: the underlying task
        # must remain visible so mobile never regresses into a full-screen desktop rail.
        page.set_viewport_size(MOBILE_VIEWPORT)
        page.wait_for_timeout(220)
        if page.locator("#inspector").evaluate("el => el.classList.contains('open')"):
            page.locator("#inspectorClose").click()
            page.wait_for_timeout(120)
        page.locator("#scrollArea").evaluate("el => { el.scrollTop = 0; }")
        page.screenshot(path=str(ASSET_DIR / "mobile-workspace.png"), full_page=False)

        page.locator("#inspectorToggle").click()
        page.wait_for_selector("#inspector.open", timeout=5_000)
        page.wait_for_timeout(180)
        sheet_box = page.locator("#inspector").bounding_box()
        if not sheet_box:
            raise RuntimeError("Mobile evidence sheet has no bounding box")
        if sheet_box["height"] >= MOBILE_VIEWPORT["height"] * 0.86:
            raise RuntimeError(f"Mobile evidence sheet is too tall: {sheet_box}")
        if sheet_box["y"] <= MOBILE_VIEWPORT["height"] * 0.10:
            raise RuntimeError(f"Mobile evidence sheet must preserve task context above it: {sheet_box}")
        if sheet_box["x"] < 6 or sheet_box["x"] + sheet_box["width"] > MOBILE_VIEWPORT["width"] - 6:
            raise RuntimeError(f"Mobile evidence sheet must keep visible page margins: {sheet_box}")

        inspector_luma = page.locator("#inspector .inspector-body").evaluate("""el => {
          const values = (getComputedStyle(el).backgroundColor.match(/\d+/g) || []).slice(0, 3).map(Number);
          const [r = 255, g = 255, b = 255] = values;
          return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        }""")
        if inspector_luma > 70:
            raise RuntimeError(f"Mobile inspector escaped the Graphite dark surface hierarchy: luma={inspector_luma:.1f}")

        page.locator(".tab[data-tab='progress']").click()
        page.wait_for_timeout(150)
        page.screenshot(path=str(ASSET_DIR / "mobile-progress.png"), full_page=False)

        page.locator(".tab[data-tab='evidence']").click()
        page.wait_for_timeout(150)
        page.screenshot(path=str(ASSET_DIR / "mobile-evidence.png"), full_page=False)

        for selector in ("#sendBtn", "#attachBtn", "#networkBtn", "#newTaskBtn", "#inspectorToggle", "#inspectorClose"):
            locator = page.locator(selector)
            if not locator.is_visible():
                continue
            box = locator.bounding_box()
            if not box or box["height"] < 44 or box["width"] < 44:
                raise RuntimeError(f"Touch target too small: {selector} -> {box}")

        if browser_errors:
            raise RuntimeError("Browser errors during product smoke test: " + " | ".join(browser_errors))
        if bad_responses:
            raise RuntimeError("Same-origin HTTP failures during product smoke test: " + " | ".join(bad_responses))
        browser.close()

    for name in SCREENSHOTS:
        path = ASSET_DIR / name
        if not path.exists() or path.stat().st_size < 10_000:
            raise RuntimeError(f"Screenshot capture failed: {path}")
        print(f"captured {name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
