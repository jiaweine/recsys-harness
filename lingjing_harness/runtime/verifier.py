from __future__ import annotations

import math
from typing import Any


class ResultVerifier:
    """Independent structural, evidence, trajectory and evolution gate for every autonomous run."""

    @staticmethod
    def search(rows: list[dict[str, Any]]) -> list[str]:
        issues = []
        if not rows:
            issues.append("当前查询没有可展示结果")
        ids = [x.get("id") for x in rows]
        if len(ids) != len(set(ids)):
            issues.append("搜索结果存在重复内容")
        if any(not x.get("title") for x in rows):
            issues.append("搜索结果存在缺失标题")
        if any(not math.isfinite(float(x.get("score", 0.0))) for x in rows):
            issues.append("搜索结果存在非有限分数")
        return issues

    @staticmethod
    def recommend(rows: list[dict[str, Any]]) -> list[str]:
        issues = []
        if not rows:
            issues.append("当前用户没有可展示内容")
        ids = [x.get("id") for x in rows]
        if len(ids) != len(set(ids)):
            issues.append("推荐结果存在重复内容")
        if any(not math.isfinite(float(x.get("score", 0.0))) for x in rows):
            issues.append("推荐结果存在非有限分数")
        return issues

    @staticmethod
    def experiment(result: dict[str, Any]) -> list[str]:
        if not result.get("evaluation_ready", True):
            return ["当前缺少足够的离线复核样本，候选策略不能进入自主学习"]
        if not result.get("safe_to_try"):
            return ["候选策略未通过稳健性门槛，不会进入经验库或改变当前策略"]
        if result.get("safe_to_try") and not result.get("trusted"):
            return ["候选策略结构安全，但没有形成足够稳定的优势，暂不晋升为长期经验"]
        return []

    @staticmethod
    def final(
        actions: list[dict[str, Any]],
        findings: list[str],
        evidence: list[dict[str, Any]],
        *,
        allow_adaptation: bool,
        critic: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        critic = critic or {}
        completed_actions = [row for row in actions if row.get("status") == "completed"]
        completed_audits = [
            row for row in completed_actions if str(row.get("tool") or "").endswith("audit")
        ]
        external_evidence = [row for row in evidence if row.get("kind") == "external"]
        local_evidence = [row for row in evidence if row.get("kind") != "external"]

        # Public/web evidence may inform the current explanation, but it must not
        # satisfy the product's final evidence floor by itself. Otherwise a
        # poisoned or irrelevant external source could make a trajectory look
        # verified and indirectly increase durable policy/episode reward.
        # A completed owned audit is acceptable local execution evidence; a
        # failed audit is not.
        evidence_backed = bool(local_evidence) or bool(completed_audits)
        external_only = bool(external_evidence) and not evidence_backed
        checks = {
            "executed_tools": bool(actions),
            "no_failed_tools": not any(row.get("status") == "failed" for row in actions),
            "evidence_backed": evidence_backed,
            "external_evidence_bounded": not external_only,
            "adaptation_respected": allow_adaptation
            or not any(row.get("result", {}).get("activated") for row in actions),
            "mission_terminal": bool(critic.get("ready", True)),
            "contradictions_resolved": not bool(critic.get("unresolved_contradictions")),
        }
        severe = [x for x in findings if "非有限" in x or "重复内容" in x or "权限" in x]
        confidence = 0.46
        confidence += 0.14 if checks["no_failed_tools"] else -0.18
        confidence += 0.16 if checks["evidence_backed"] else -0.12
        confidence += 0.08 if checks["adaptation_respected"] else -0.30
        confidence += 0.10 if checks["mission_terminal"] else -0.18
        confidence += 0.06 if checks["contradictions_resolved"] else -0.16
        confidence += 0.08 * float(critic.get("evidence_coverage", 0.0) or 0.0)
        if external_only:
            confidence -= 0.06
        confidence -= min(0.18, 0.05 * len(severe))
        return {
            "passed": all(checks.values()) and not severe,
            "confidence": round(max(0.0, min(0.99, confidence)), 3),
            "checks": checks,
            "severe_findings": severe,
            "evidence_provenance": {
                "local": len(local_evidence),
                "external": len(external_evidence),
                "completed_audits": len(completed_audits),
                "external_only": external_only,
            },
            "trajectory": {
                "evidence_coverage": critic.get("evidence_coverage"),
                "terminal_coverage": critic.get("terminal_coverage"),
                "blocked": critic.get("blocked", []),
                "unresolved": critic.get("unresolved", []),
                "contradictions": critic.get("contradictions", []),
            },
        }
