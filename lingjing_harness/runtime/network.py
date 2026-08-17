from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class NetworkResearch:
    """Optional web-research adapter for a self-hosted JSON search endpoint.

    The harness never requires this adapter. When configured, it is exposed as a
    network-risk tool and its output is evidence only; it is deliberately kept
    outside the strategy-evolution input path.
    """

    def __init__(self, endpoint: str | None = None, api_key: str | None = None, timeout: float = 8.0) -> None:
        self.endpoint = (endpoint if endpoint is not None else os.environ.get("LINGJING_WEB_SEARCH_URL", "")).strip()
        self.api_key = api_key if api_key is not None else os.environ.get("LINGJING_WEB_SEARCH_KEY", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        if not self.endpoint:
            return False
        return urlparse(self.endpoint).scheme in {"http", "https"}

    def search(self, query: str, limit: int = 6) -> dict[str, Any]:
        query = str(query or "").strip()[:320]
        if not query:
            return {"query": "", "results": [], "source": "network", "configured": self.configured}
        if not self.configured:
            raise RuntimeError("联网研究尚未配置")
        limit = max(1, min(int(limit), 8))
        separator = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{separator}{urlencode({'q': query, 'format': 'json', 'language': 'zh-CN', 'safesearch': 1})}"
        headers = {"Accept": "application/json", "User-Agent": "LingjingResearch/1"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2 * 1024 * 1024)
        except Exception as exc:  # network errors are converted into a tool-level failure
            raise RuntimeError(f"联网研究请求失败：{type(exc).__name__}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("联网研究返回了无法解析的数据") from exc
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            target = str(row.get("url") or "").strip()
            title = str(row.get("title") or target or "外部资料").strip()[:220]
            snippet = str(row.get("content") or row.get("snippet") or "").strip()[:700]
            if not target or urlparse(target).scheme not in {"http", "https"}:
                continue
            results.append({"title": title, "url": target, "snippet": snippet})
            if len(results) >= limit:
                break
        return {
            "query": query,
            "results": results,
            "source": "network",
            "configured": True,
            "count": len(results),
        }
