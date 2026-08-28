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


def test_readme_keeps_public_product_spine_visible() -> None:
    readme = _readme()
    required_sections = (
        "# 快速启动",
        "# 真实产品",
        "# 为什么是序枢",
        "# 核心能力",
        "# 工作原理",
        "# Existing system integration",
        "# 生产数据契约",
        "# 系统架构",
        "# 可靠性与安全",
        "# 配置",
        "# 开发与质量门槛",
    )
    missing = [section for section in required_sections if section not in readme]
    assert not missing, f"README lost public product sections: {missing}"


def test_readme_keeps_evidence_authority_and_recovery_semantics() -> None:
    readme = _readme()

    evidence_anchors = (
        "Business Reward",
        "Domain Guardrails",
        "Logged Replay + Temporal Holdout",
        "future holdout",
        "paired comparison",
        "Trusted Strategy Memory",
    )
    missing_evidence = [anchor for anchor in evidence_anchors if anchor not in readme]
    assert not missing_evidence, f"README lost evidence semantics: {missing_evidence}"

    authority_anchors = (
        "explicit user authority",
        "Permissioned Activation",
        "RewardSpec cannot be evolved by optimizer",
        "证据、权限、恢复路径",
    )
    missing_authority = [anchor for anchor in authority_anchors if anchor not in readme]
    assert not missing_authority, f"README lost authority/recovery semantics: {missing_authority}"

    integration_anchors = (
        "Serving adapter",
        "同一套 business replay 和 evidence loop",
        "不需要改变原有 serving architecture",
    )
    missing_integration = [anchor for anchor in integration_anchors if anchor not in readme]
    assert not missing_integration, f"README lost serving integration semantics: {missing_integration}"
