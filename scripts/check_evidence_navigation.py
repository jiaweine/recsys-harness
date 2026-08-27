"""Verify ranked results and evidence navigate to each other without invented identity."""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")
PROMPT = "检查搜索“露营灯”的当前结果，只做复现和证据核对，不改变策略。"


def wait_for_completed_search(page) -> None:
    page.locator("#newTaskBtn").click()
    page.locator('.scene[data-scene="search"]').click()
    page.locator("#input").fill(PROMPT)
    page.locator("#sendBtn").click()
    page.wait_for_function("document.getElementById('stateText').textContent === '已完成'", timeout=30_000)
    page.wait_for_selector('#resultAnalysis:not([hidden]) .rank-row[data-evidence-linked="true"]', timeout=10_000)
    page.wait_for_selector("#evidenceList .evidence-item", state="attached", timeout=10_000)
    page.wait_for_selector("#evidenceList .evidence-item[data-result-linked='true']", state="attached", timeout=10_000)


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
    page.wait_for_function(
        "document.activeElement?.classList.contains('evidence-item') && document.activeElement?.classList.contains('evidence-target')",
        timeout=2_000,
    )


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
    page.wait_for_function(
        "document.activeElement?.classList.contains('rank-row') && document.activeElement?.classList.contains('rank-target')",
        timeout=2_000,
    )


def reverse_button_for_target(page):
    target = page.locator("#evidenceList .evidence-item.evidence-target")
    button = target.locator(".evidence-rank-link")
    if button.count() != 1:
        raise RuntimeError("Matched evidence did not expose exactly one reverse Result action")
    return button


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        if page.locator("html").get_attribute("data-theme") != "light":
            raise RuntimeError("Evidence navigation QA must start from the default regular theme")
        wait_for_completed_search(page)

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

        # Desktop round trip: Rank -> Evidence -> same Rank. The persistent rail stays open.
        link.click()
        page.wait_for_function("document.getElementById('inspectorToggle').getAttribute('aria-expanded') === 'true'", timeout=5_000)
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)
        reverse = reverse_button_for_target(page)
        if not reverse.is_visible() or title not in (reverse.get_attribute("aria-label") or ""):
            raise RuntimeError("Desktop Evidence row lost its reverse Result action or title identity")
        reverse.click()
        page.wait_for_selector("#resultAnalysis .rank-row.rank-target", timeout=5_000)
        assert_selected_rank(page, title, rank)
        if page.locator("#inspector").evaluate("el => getComputedStyle(el).display === 'none'"):
            raise RuntimeError("Desktop reverse navigation incorrectly removed the persistent Evidence rail")

        # Return to Evidence once more, then validate the complete mobile round trip.
        first_row.locator(".rank-evidence-link").click()
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)
        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(180)
        close = page.locator("#inspectorClose")
        if not close.is_visible():
            raise RuntimeError("Mobile evidence sheet did not expose its close control")
        close.click()
        page.wait_for_function("document.getElementById('inspectorToggle').getAttribute('aria-expanded') === 'false'", timeout=5_000)

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

        mobile_link.click()
        page.wait_for_function("document.getElementById('inspectorToggle').getAttribute('aria-expanded') === 'true'", timeout=5_000)
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)
        mobile_reverse = reverse_button_for_target(page)
        reverse_box = mobile_reverse.bounding_box()
        if not reverse_box or reverse_box["height"] < 44 or reverse_box["width"] < 44:
            raise RuntimeError(f"Mobile reverse Result action lost its 44px touch target: {reverse_box}")
        mobile_reverse.click()
        page.wait_for_function("document.getElementById('inspectorToggle').getAttribute('aria-expanded') === 'false'", timeout=5_000)
        page.wait_for_selector("#resultAnalysis .rank-row.rank-target", timeout=5_000)
        assert_selected_rank(page, title, rank)

        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Bidirectional evidence navigation introduced page-level mobile overflow: {overflow}px")
        if browser_errors:
            raise RuntimeError("Browser errors during result/evidence round-trip QA:\n" + "\n".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
