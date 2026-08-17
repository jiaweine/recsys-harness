from __future__ import annotations

import math
from typing import Any


class ResultVerifier:
    """Independent structural, evidence and evolution gate for every autonomous run."""

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
    def final(actions: list[dict[str, Any]], findings: list[str], evidence: list[dict[str, Any]], *, allow_adaptation: bool) -> dict[str, Any]:
        checks = {
            "executed_tools": bool(actions),
            "no_failed_tools": not any(row.get("status") == "failed" for row in actions),
            "evidence_backed": bool(evidence) or any(row.get("tool", "").endswith("audit") for row in actions),
            "adaptation_respected": allow_adaptation or not any(row.get("result", {}).get("activated") for row in actions),
        }
        severe = [x for x in findings if "非有限" in x or "重复内容" in x or "权限" in x]
        confidence = 0.52
        confidence += 0.16 if checks["no_failed_tools"] else -0.18
        confidence += 0.18 if checks["evidence_backed"] else -0.12
        confidence += 0.08 if checks["adaptation_respected"] else -0.30
        confidence -= min(0.18, 0.05 * len(severe))
        return {
            "passed": all(checks.values()) and not severe,
            "confidence": round(max(0.0, min(0.99, confidence)), 3),
            "checks": checks,
            "severe_findings": severe,
        }
