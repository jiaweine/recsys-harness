"""Verify ranked results navigate to the exact existing evidence entry."""
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

        link.click()
        page.wait_for_function("document.getElementById('inspectorToggle').getAttribute('aria-expanded') === 'true'", timeout=5_000)
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
        opacity = mobile_link.evaluate("el => getComputedStyle(el).opacity")
        if opacity != "0":
            raise RuntimeError(f"Mobile full-row evidence action should be visually transparent, got opacity={opacity}")
        mobile_link.click()
        page.wait_for_function("document.getElementById('inspectorToggle').getAttribute('aria-expanded') === 'true'", timeout=5_000)
        page.wait_for_selector("#evidenceList .evidence-item.evidence-target", timeout=5_000)
        assert_selected_evidence(page, title, rank)

        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Evidence navigation introduced page-level mobile overflow: {overflow}px")
        if browser_errors:
            raise RuntimeError("Browser errors during result evidence navigation QA:\n" + "\n".join(browser_errors))
        browser.close()


if __name__ == "__main__":
    main()
