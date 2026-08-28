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
        "# Active strategy lifecycle",
        "# Existing system integration",
        "# 快速启动",
        "# 配置",
        "# 生产数据契约",
        "# 系统架构",
        "# 可靠性与安全",
        "# Repository map",
        "# 质量门槛",
    )
    missing = [section for section in required_sections if section not in readme]
    assert not missing, f"README lost product-contract sections: {missing}"


def test_readme_keeps_evidence_and_authority_boundaries_explicit() -> None:
    readme = _readme()

    # Keep estimator identity visible instead of collapsing every evaluation basis
    # into one generic quality score.
    assert "logged_replay" in readme
    assert "propensity_weighted_logged_replay" in readme
    assert "evaluation basis" in readme

    # Trust, activation and business-objective authority must remain separate.
    assert "Trusted 与 Active 分层" in readme
    assert "explicit user authority" in readme
    assert "RewardSpec 是业务 contract" in readme
    assert "RewardSpec cannot be evolved by optimizer" in readme

    # Existing-system integration must stay adapter-based rather than implying a
    # forced serving-stack migration.
    assert "Serving adapter" in readme
    assert "不需要先迁移 serving architecture" in readme
