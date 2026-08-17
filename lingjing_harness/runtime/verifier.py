from __future__ import annotations

from typing import Any


class ResultVerifier:
    """Rejects structurally invalid or unsupported execution results before they become conclusions."""

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
        return issues

    @staticmethod
    def recommend(rows: list[dict[str, Any]]) -> list[str]:
        issues = []
        if not rows:
            issues.append("当前用户没有可展示内容")
        ids = [x.get("id") for x in rows]
        if len(ids) != len(set(ids)):
            issues.append("推荐结果存在重复内容")
        return issues

    @staticmethod
    def experiment(result: dict[str, Any]) -> list[str]:
        if not result.get("evaluation_ready", True):
            return ["当前缺少足够的离线复核样本，候选方案不能标记为可尝试"]
        if result.get("safe_to_try"):
            return []
        return ["候选方案未通过离线安全门槛，不建议直接扩大流量"]
