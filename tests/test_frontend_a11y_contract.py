from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
A11Y = ROOT / "frontend" / "a11y.js"
RUNTIME_BUS = ROOT / "frontend" / "runtime-bus.js"


def test_accessibility_module_is_loaded_with_runtime_modules() -> None:
    assert A11Y.is_file()
    runtime_bus = RUNTIME_BUS.read_text(encoding="utf-8")
    assert "import('/assets/a11y.js')" in runtime_bus


def test_accessibility_module_keeps_keyboard_and_dialog_boundaries() -> None:
    source = A11Y.read_text(encoding="utf-8")
    required = (
        "ArrowRight",
        "ArrowLeft",
        "Home",
        "End",
        "aria-controls",
        "role', 'tabpanel",
        "aria-modal",
        "shell.inert",
        "authFocusables",
        "role', 'log",
        "aria-atomic",
        "inspectorReturnFocus",
    )
    missing = [term for term in required if term not in source]
    assert not missing, f"frontend accessibility boundary regressed: {missing}"
