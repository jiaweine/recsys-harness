from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite, log1p
from typing import Any


def _number(
    value: Any,
    *,
    field_name: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if value is None or value == "":
        value = default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是数值") from exc
    if not isfinite(number):
        raise ValueError(f"{field_name} 必须是有限数值")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} 不能小于 {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} 不能大于 {maximum:g}")
    return number


def _boolean(value: Any, *, field_name: str, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "是"}:
            return True
        if normalized in {"false", "0", "no", "n", "否"}:
            return False
    raise ValueError(f"{field_name} 必须是布尔值")


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{key} 必须是数组")
    if any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{key} 中的每一项都必须是对象")
    return value


@dataclass(slots=True)
class Item:
    item_id: str
    title: str
    text: str = ""
    categories: list[str] = field(default_factory=list)
    popularity: float = 0.0
    quality: float = 0.5
    freshness: float = 0.5
    eligible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Item":
        categories = row.get("categories") or row.get("category") or []
        if isinstance(categories, str):
            categories = [x.strip() for x in categories.replace("，", ",").split(",") if x.strip()]
        elif not isinstance(categories, (list, tuple, set)):
            raise ValueError("categories 必须是字符串或数组")

        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata 必须是对象")

        return cls(
            item_id=str(row.get("item_id") or row.get("id") or "").strip(),
            title=str(row.get("title") or row.get("name") or "").strip(),
            text=str(row.get("text") or row.get("description") or "").strip(),
            categories=[str(x).strip() for x in categories if str(x).strip()],
            popularity=_number(row.get("popularity"), field_name="popularity", default=0.0, minimum=0.0),
            quality=_number(row.get("quality"), field_name="quality", default=0.5, minimum=0.0, maximum=1.0),
            freshness=_number(row.get("freshness"), field_name="freshness", default=0.5, minimum=0.0, maximum=1.0),
            eligible=_boolean(row.get("eligible"), field_name="eligible", default=True),
            metadata=dict(metadata),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "title": self.title,
            "categories": self.categories,
            "quality": round(self.quality, 4),
            "freshness": round(self.freshness, 4),
        }


@dataclass(slots=True)
class Interaction:
    user_id: str
    item_id: str
    event: str = "click"
    weight: float = 1.0
    timestamp: float = 0.0

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "Interaction":
        event = str(row.get("event") or "click").lower()
        default_weight = {"view": 0.2, "click": 1.0, "favorite": 2.0, "cart": 2.4, "purchase": 3.2}.get(event, 1.0)
        return cls(
            user_id=str(row.get("user_id") or row.get("user") or "").strip(),
            item_id=str(row.get("item_id") or row.get("item") or "").strip(),
            event=event,
            weight=_number(row.get("weight"), field_name="weight", default=default_weight, minimum=0.0),
            timestamp=_number(row.get("timestamp"), field_name="timestamp", default=0.0),
        )


@dataclass(slots=True)
class QueryLabel:
    query: str
    relevant: list[str]

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "QueryLabel":
        relevant = row.get("relevant") or row.get("relevant_ids") or []
        if isinstance(relevant, str):
            relevant = [x.strip() for x in relevant.split(",") if x.strip()]
        elif not isinstance(relevant, (list, tuple, set)):
            raise ValueError("relevant 必须是字符串或数组")
        return cls(query=str(row.get("query") or "").strip(), relevant=[str(x).strip() for x in relevant if str(x).strip()])


@dataclass
class Catalog:
    items: list[Item]
    interactions: list[Interaction] = field(default_factory=list)
    query_labels: list[QueryLabel] = field(default_factory=list)
    name: str = "演示数据"

    def __post_init__(self) -> None:
        self.items = [x for x in self.items if x.item_id and x.title]
        seen: set[str] = set()
        deduped: list[Item] = []
        for item in self.items:
            if item.item_id in seen:
                continue
            seen.add(item.item_id)
            deduped.append(item)
        self.items = deduped
        self.item_by_id = {x.item_id: x for x in self.items}
        self.interactions = [x for x in self.interactions if x.user_id and x.item_id in self.item_by_id]
        self.interactions.sort(key=lambda x: (x.user_id, x.timestamp, x.item_id))

        eligible_ids = {x.item_id for x in self.items if x.eligible}
        labels: list[QueryLabel] = []
        for label in self.query_labels:
            relevant = list(dict.fromkeys(x for x in label.relevant if x in eligible_ids))
            if label.query and relevant:
                labels.append(QueryLabel(label.query, relevant))
        self.query_labels = labels

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, name: str = "导入数据") -> "Catalog":
        if not isinstance(payload, dict):
            raise ValueError("数据文件顶层必须是 JSON 对象")
        item_rows = _rows(payload, "items")
        interaction_rows = _rows(payload, "interactions")
        query_rows = _rows(payload, "query_labels")
        if not item_rows:
            raise ValueError("数据中至少需要一条包含 id 与 title 的有效内容")

        catalog = cls(
            items=[Item.from_dict(x) for x in item_rows],
            interactions=[Interaction.from_dict(x) for x in interaction_rows],
            query_labels=[QueryLabel.from_dict(x) for x in query_rows],
            name=str(name or "导入数据").strip() or "导入数据",
        )
        if not catalog.items:
            raise ValueError("数据中至少需要一条包含 id 与 title 的有效内容")
        return catalog

    def to_payload(self) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": item.item_id,
                    "title": item.title,
                    "text": item.text,
                    "categories": list(item.categories),
                    "popularity": item.popularity,
                    "quality": item.quality,
                    "freshness": item.freshness,
                    "eligible": item.eligible,
                    "metadata": dict(item.metadata),
                }
                for item in self.items
            ],
            "interactions": [
                {
                    "user_id": event.user_id,
                    "item_id": event.item_id,
                    "event": event.event,
                    "weight": event.weight,
                    "timestamp": event.timestamp,
                }
                for event in self.interactions
            ],
            "query_labels": [
                {"query": label.query, "relevant": list(label.relevant)}
                for label in self.query_labels
            ],
        }

    def summary(self) -> dict[str, Any]:
        users = {x.user_id for x in self.interactions}
        cats = {c for item in self.items for c in item.categories}
        return {
            "name": self.name,
            "items": len(self.items),
            "users": len(users),
            "interactions": len(self.interactions),
            "queries": len(self.query_labels),
            "categories": len(cats),
        }

    def popularity_norm(self, item: Item) -> float:
        top = max((x.popularity for x in self.items), default=1.0)
        return log1p(item.popularity) / max(1e-9, log1p(max(1.0, top)))
