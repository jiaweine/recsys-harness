from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_is_product_documentation_not_release_notes() -> None:
    readme = _readme()
    forbidden_progress_language = (
        "# 当前边界",
        "现阶段",
        "当前主线",
        "上一版",
        "这次更新",
        "当前仍需继续推进",
        "明确的下一阶段",
    )

    hits = [phrase for phrase in forbidden_progress_language if phrase in readme]
    assert not hits, f"README drifted back into release-progress language: {hits}"


def test_readme_keeps_core_product_contract_visible() -> None:
    readme = _readme()
    required_sections = (
        "# 产品价值",
        "# 生产价值闭环",
        "# Agent Harness",
        "# 垂直自进化",
        "# Existing system integration",
        "# 快速启动",
        "# 生产数据契约",
        "# 系统架构",
        "# 可靠性与安全边界",
        "# 能力边界与扩展面",
    )
    missing = [section for section in required_sections if section not in readme]
    assert not missing, f"README lost product-contract sections: {missing}"


def test_readme_keeps_evidence_and_authority_boundaries_explicit() -> None:
    readme = _readme()

    assert "logged_replay" in readme
    assert "它不是“完整无偏 OPE”" in readme
    assert "Trusted 不等于 Active" in readme
    assert "RewardSpec 是产品 contract，不是进化 gene" in readme
    assert "不会猜远端参数后直接修改生产服务" in readme
