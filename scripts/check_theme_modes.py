"""Verify regular light and layered Charcoal dark workspace themes against the real product."""
from __future__ import annotations

import os

from playwright.sync_api import sync_playwright


BASE_URL = os.environ.get("RECSYS_CAPTURE_URL", "http://127.0.0.1:8765").rstrip("/")


def rgb(page, selector: str) -> str:
    return page.locator(selector).evaluate("el => getComputedStyle(el).backgroundColor")


def luma(page, selector: str) -> float:
    return float(page.locator(selector).evaluate("""el => {
      const values = (getComputedStyle(el).backgroundColor.match(/\d+/g) || []).slice(0, 3).map(Number);
      const [r = 0, g = 0, b = 0] = values;
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }"""))


def assert_selected_theme(page, theme: str) -> None:
    actual_theme = page.locator("html").get_attribute("data-theme")
    if actual_theme != theme:
        raise RuntimeError(f"Expected theme {theme!r}, got {actual_theme!r}")
    active = page.locator(f'[data-theme-choice="{theme}"]')
    if active.get_attribute("aria-pressed") != "true":
        raise RuntimeError(f"{theme} theme button is not exposed as selected")
    other = "dark" if theme == "light" else "light"
    if page.locator(f'[data-theme-choice="{other}"]').get_attribute("aria-pressed") != "false":
        raise RuntimeError(f"Inactive {other} theme button incorrectly reports selected")


def assert_desktop_theme(page, theme: str) -> None:
    expected = {
        "light": {
            "body": "rgb(246, 247, 249)",
            ".topbar": "rgb(255, 255, 255)",
            ".sidebar": "rgb(243, 244, 246)",
            ".main": "rgb(255, 255, 255)",
            ".inspector": "rgb(250, 250, 250)",
        },
        "dark": {
            "body": "rgb(11, 13, 17)",
            ".topbar": "rgb(15, 18, 23)",
            ".sidebar": "rgb(16, 19, 25)",
            ".main": "rgb(21, 24, 32)",
            ".inspector": "rgb(16, 19, 25)",
        },
    }[theme]
    assert_selected_theme(page, theme)
    for selector, expected_color in expected.items():
        actual = rgb(page, selector)
        if actual != expected_color:
            raise RuntimeError(f"{theme} desktop {selector} background drifted: {actual} != {expected_color}")

    if theme == "dark":
        chrome = luma(page, ".sidebar")
        workspace = luma(page, ".main")
        if workspace - chrome < 4:
            raise RuntimeError(f"Charcoal dark workspace collapsed back into chrome: chrome={chrome}, workspace={workspace}")


def assert_mobile_theme(page, theme: str) -> None:
    assert_selected_theme(page, theme)
    surfaces = ("body", ".topbar", ".main", ".inspector")
    values = {selector: luma(page, selector) for selector in surfaces}
    if theme == "light":
        too_dark = {selector: value for selector, value in values.items() if value < 235}
        if too_dark:
            raise RuntimeError(f"Regular mobile theme retained dark primary surfaces: {too_dark}")
    else:
        too_light = {selector: value for selector, value in values.items() if value > 70}
        if too_light:
            raise RuntimeError(f"Dark mobile theme escaped the Graphite surface ladder: {too_light}")
        if values[".main"] - values["body"] < 6:
            raise RuntimeError(f"Charcoal mobile workspace lost depth from the shell: {values}")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page = context.new_page()
        browser_errors: list[str] = []
        page.on("pageerror", lambda exc: browser_errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: browser_errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        page.evaluate("localStorage.removeItem('xushu-theme')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)

        # A new user gets the regular light workspace, not a dark canvas.
        assert_desktop_theme(page, "light")
        if page.locator('meta[name="theme-color"]').get_attribute("content") != "#f6f7f9":
            raise RuntimeError("Regular theme did not publish its light browser chrome color")

        page.locator('[data-theme-choice="dark"]').click()
        assert_desktop_theme(page, "dark")
        if page.locator('meta[name="theme-color"]').get_attribute("content") != "#101319":
            raise RuntimeError("Dark theme did not publish its Charcoal browser chrome color")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        assert_desktop_theme(page, "dark")

        # Explicitly switch back and verify persistence in the other direction too.
        page.locator('[data-theme-choice="light"]').click()
        assert_desktop_theme(page, "light")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("document.body.classList.contains('ready')", timeout=15_000)
        assert_desktop_theme(page, "light")

        page.set_viewport_size({"width": 393, "height": 852})
        page.wait_for_timeout(180)
        picker = page.locator(".theme-picker")
        if not picker.is_visible():
            raise RuntimeError("Theme picker disappeared on mobile")
        for theme in ("light", "dark"):
            button = page.locator(f'[data-theme-choice="{theme}"]')
            box = button.bounding_box()
            if not box or box["height"] < 44 or box["width"] < 44:
                raise RuntimeError(f"Mobile {theme} theme control lost its 44px touch target: {box}")

        assert_mobile_theme(page, "light")
        page.locator('[data-theme-choice="dark"]').click()
        assert_mobile_theme(page, "dark")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Dark theme introduced mobile page overflow: {overflow}px")
        page.locator('[data-theme-choice="light"]').click()
        assert_mobile_theme(page, "light")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 1:
            raise RuntimeError(f"Regular theme introduced mobile page overflow: {overflow}px")

        if browser_errors:
            raise RuntimeError("Browser errors during theme QA:\n" + "\n".join(browser_errors))
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
