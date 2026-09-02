"""Verify evidence interactions and shared runtime JSON parsing in the real product."""
from __future__ import annotations

import os
import time

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "检查搜索“露营灯”的当前结果，只做复现和证据核对，不改变策略。"
USER_NOTE = "补充：保留这段用户要求。"


def wait_for_completed_search(page) -> None:
    page.locator("#newTaskBtn").click()
    page.locator('.scene[data-scene="search"]').click()
    page.locator("#input").fill(PROMPT)
    page.locator("#sendBtn").click()
    expect(page.locator("#stateText")).to_have_text("已完成", timeout=30_000)
    page.wait_for_selector('#resultAnalysis:not([hidden]) .rank-row[data-evidence-linked="true"]', timeout=10_000)
    page.wait_for_selector("#evidenceList .evidence-item", state="attached", timeout=10_000)
    page.wait_for_selector("#evidenceList .evidence-item[data-result-linked='true'][data-followup-linked='true']", state="attached", timeout=10_000)


def matching_evidence(page, expected_title: str, expected_rank: int):
    rows = page.locator("#evidenceList .evidence-item[data-result-linked='true']")
    for index in range(rows.count()):
        row = rows.nth(index)
        if row.get_attribute("data-result-title") != expected_title:
            continue
        if int(row.get_attribute("data-result-rank") or "0") == expected_rank:
            return row
    raise RuntimeError("Could not resolve the persisted evidence row by exact title + rank identity")


def assert_selected_evidence(page, expected_title: str, expected_rank: int) -> None:
    if page.locator('.tab[data-tab="evidence"]').get_attribute("aria-selected") != "true":
        raise RuntimeError("Result evidence navigation did not activate the Evidence tab")
    target = page.locator("#evidenceList .evidence-item.evidence-target")
    if target.count() != 1:
        raise RuntimeError("Result evidence navigation did not highlight exactly one evidence row")
    actual = target.locator("b").inner_text().strip()
    if actual != expected_title:
        raise RuntimeError(f"Evidence navigation matched the wrong title: {actual!r} != {expected_title!r}")
    detail = target.locator("small").inner_text().strip()
    if f"第 {expected_rank} 位" not in detail:
        raise RuntimeError(f"Evidence navigation matched the right title but wrong ranked evidence: {detail!r}")
    expect(target).to_be_focused(timeout=2_000)


def assert_selected_rank(page, expected_title: str, expected_rank: int) -> None:
    target = page.locator("#resultAnalysis .rank-row.rank-target")
    if target.count() != 1:
        raise RuntimeError("Evidence-to-result navigation did not highlight exactly one ranked row")
    actual = target.locator(".rank-title > b").inner_text().strip()
    if actual != expected_title:
        raise RuntimeError(f"Reverse navigation matched the wrong ranked title: {actual!r} != {expected_title!r}")
    actual_rank = int(target.get_attribute("data-evidence-rank") or "0")
    if actual_rank != expected_rank:
        raise RuntimeError(f"Reverse navigation matched the wrong rank: {actual_rank} != {expected_rank}")
    expect(target).to_be_focused(timeout=2_000)


def reverse_button(page, title: str, rank: int):
    button = matching_evidence(page, title, rank).locator(".evidence-rank-link")
    if button.count() != 1:
        raise RuntimeError("Matched evidence did not expose exactly one reverse Result action")
    return button


def assert_scoped_followup(page, title: str, rank: int, *, mobile: bool) -> None:
    item = matching_evidence(page, title, rank)
    button = item.locator(".evidence-followup-link")
    if button.count() != 1 or not button.is_visible():
        raise RuntimeError("Mapped Evidence row lost its scoped follow-up action")
    if title not in (button.get_attribute("aria-label") or ""):
        raise RuntimeError("Scoped follow-up accessibility label lost the real ranked-result title")
    if mobile:
        box = button.bounding_box()
        if not box or box["height"] < 44 or box["width"] < 44:
            raise RuntimeError(f"Mobile scoped follow-up lost its 44px touch target: {box}")

    detail = item.locator("small").inner_text().strip()
    message_count = page.locator("#messageList .msg").count()
    composer = page.locator("#input")
    composer.fill(USER_NOTE)
    button.click()

    if mobile:
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "false", timeout=5_000)
    elif page.locator("#inspectorToggle").get_attribute("aria-expanded") != "true":
        raise RuntimeError("Desktop scoped follow-up incorrectly closed the persistent Evidence rail")

    expect(composer).to_be_focused(timeout=2_500)
    value = composer.input_value()
    if USER_NOTE not in value:
        raise RuntimeError("Scoped follow-up overwrote an existing user draft")
    if title not in value or f"第 {rank} 位" not in value:
        raise RuntimeError("Scoped follow-up draft lost the exact ranked-result identity")
    if "先不要改变当前策略" not in value:
        raise RuntimeError("Scoped follow-up draft lost the no-strategy-change boundary")
    evidence_prefix = detail[: min(28, len(detail))]
    if evidence_prefix and evidence_prefix not in value:
        raise RuntimeError("Scoped follow-up draft is not grounded in the selected persisted evidence detail")
    page.wait_for_timeout(120)
    if page.locator("#messageList .msg").count() != message_count:
        raise RuntimeError("Scoped follow-up must prepare an editable draft, never auto-send a new turn")
    composer.fill("")


def wait_runtime_bus(page, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        latest = page.evaluate(
            "window.XushuRuntimeBus?.snapshot ? window.XushuRuntimeBus.snapshot() : null"
        )
        if (
            isinstance(latest, dict)
            and latest.get("matchedResponses", 0) > 0
            and latest.get("parsedResponses") == latest.get("matchedResponses")
        ):
            return latest
        page.wait_for_timeout(50)
    raise RuntimeError(f"Shared runtime response cache did not converge: {latest}")


def assert_runtime_response_cache(page) -> None:
    stats = wait_runtime_bus(page)
    if stats["parseErrors"] != 0:
        raise RuntimeError(f"Shared runtime response cache saw JSON parse errors: {stats}")
    if stats["parsedResponses"] != stats["matchedResponses"]:
        raise RuntimeError(f"Runtime JSON was not parsed exactly once per matched response: {stats}")
    if stats["cloneReads"] < stats["matchedResponses"] * 3:
        raise RuntimeError(f"Expected the three existing UI consumers to keep reading response clones: {stats}")
    if stats["jsonReads"] < stats["matchedResponses"] * 4:
        raise RuntimeError(f"Expected app.js plus three UI consumers to share each parsed payload: {stats}")
    if stats["parsedResponses"] >= stats["jsonReads"]:
        raise RuntimeError(f"Shared parsing did not reduce duplicate JSON work: {stats}")

    for selector, label in (
        ("#resultSnapshot:not([hidden])", "Run Snapshot"),
        ("#agentTrace:not([hidden])", "Agent Trace"),
        ("#runControlPlane:not([hidden])", "Control Plane"),
        ("#learningLedger:not([hidden])", "Learning Ledger"),
    ):
        if page.locator(selector).count() != 1:
            raise RuntimeError(f"{label} did not render after shared runtime response parsing")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.locator("body.ready").wait_for(state="attached", timeout=15_000)
        if page.locator("html").get_attribute("data-theme") != "light":
            raise RuntimeError("Evidence navigation QA must start from the default regular theme")
        if not page.evaluate("Boolean(window.XushuRuntimeBus?.snapshot)"):
            raise RuntimeError("Shared runtime response cache did not initialize before product consumers")
        wait_for_completed_search(page)
        assert_runtime_response_cache(page)

        first_row = page.locator('#resultAnalysis .rank-row[data-evidence-linked="true"]').first
        title = first_row.locator(".rank-title > b").inner_text().strip()
        rank = int(first_row.get_attribute("data-evidence-rank") or "0")
        if not title or rank <= 0:
            raise RuntimeError("Ranked result row does not expose real title + rank identity")
        link = first_row.locator(".rank-evidence-link")
        if not link.is_visible():
            raise RuntimeError("Desktop ranked result lost its explicit evidence action")
        if title not in (link.get_attribute("aria-label") or ""):
            raise RuntimeError("Evidence action accessibility label lost the real result title")

        # Desktop: Rank -> Evidence -> editable evidence-grounded follow-up -> same Rank.
        link.click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "true", timeout=5_000)
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)
        assert_scoped_followup(page, title, rank, mobile=False)

        reverse = reverse_button(page, title, rank)
        if not reverse.is_visible() or title not in (reverse.get_attribute("aria-label") or ""):
            raise RuntimeError("Desktop Evidence row lost its reverse Result action or title identity")
        reverse.click()
        page.wait_for_selector("#resultAnalysis .rank-row.rank-target", timeout=5_000)
        assert_selected_rank(page, title, rank)
        if page.locator("#inspector").evaluate("el => getComputedStyle(el).display === 'none'"):
            raise RuntimeError("Desktop reverse navigation incorrectly removed the persistent Evidence rail")

        # Move into the real mobile layout with the sheet closed.
        first_row.locator(".rank-evidence-link").click()
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)
        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(180)
        close = page.locator("#inspectorClose")
        if not close.is_visible():
            raise RuntimeError("Mobile evidence sheet did not expose its close control")
        close.click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "false", timeout=5_000)

        first_row = page.locator('#resultAnalysis .rank-row[data-evidence-linked="true"]').first
        row_box = first_row.bounding_box()
        mobile_link = first_row.locator(".rank-evidence-link")
        link_box = mobile_link.bounding_box()
        if not row_box or row_box["height"] < 44:
            raise RuntimeError(f"Mobile ranked result is not a safe evidence touch target: {row_box}")
        if not link_box or link_box["height"] < 44 or link_box["width"] < row_box["width"] - 2:
            raise RuntimeError(f"Mobile evidence action does not cover the ranked row: row={row_box}, link={link_box}")
        if mobile_link.evaluate("el => getComputedStyle(el).opacity") != "0":
            raise RuntimeError("Mobile full-row evidence action must remain visually transparent")

        # Mobile follow-up closes the Bottom Sheet and returns keyboard focus to the existing Composer.
        mobile_link.click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "true", timeout=5_000)
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)
        assert_scoped_followup(page, title, rank, mobile=True)

        # Re-open the same evidence and still preserve the independent Evidence -> Rank round trip.
        mobile_link.click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "true", timeout=5_000)
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)
        mobile_reverse = reverse_button(page, title, rank)
        reverse_box = mobile_reverse.bounding_box()
        if not reverse_box or reverse_box["height"] < 44 or reverse_box["width"] < 44:
            raise RuntimeError(f"Mobile reverse Result action lost its 44px touch target: {reverse_box}")
        mobile_reverse.click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "false", timeout=5_000)
        page.wait_for_selector("#resultAnalysis .rank-row.rank-target", timeout=5_000)
        assert_selected_rank(page, title, rank)

        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Evidence traceability/follow-up introduced page-level mobile overflow: {overflow}px")
        if browser_errors:
            raise RuntimeError("Browser errors during shared-response/evidence QA:\n" + "\n".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
