from __future__ import annotations

from dataclasses import dataclass, field
from math import log1p
from typing import Any


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
        return cls(
            item_id=str(row.get("item_id") or row.get("id") or "").strip(),
            title=str(row.get("title") or row.get("name") or "").strip(),
            text=str(row.get("text") or row.get("description") or "").strip(),
            categories=[str(x).strip() for x in categories if str(x).strip()],
            popularity=max(0.0, float(row.get("popularity", 0.0) or 0.0)),
            quality=min(1.0, max(0.0, float(row.get("quality", 0.5) or 0.5))),
            freshness=min(1.0, max(0.0, float(row.get("freshness", 0.5) or 0.5))),
            eligible=bool(row.get("eligible", True)),
            metadata=dict(row.get("metadata") or {}),
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
            weight=max(0.0, float(row.get("weight", default_weight) or default_weight)),
            timestamp=float(row.get("timestamp", 0.0) or 0.0),
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
        return cls(query=str(row.get("query") or "").strip(), relevant=[str(x) for x in relevant])


@dataclass
class Catalog:
    items: list[Item]
    interactions: list[Interaction] = field(default_factory=list)
    query_labels: list[QueryLabel] = field(default_factory=list)
    name: str = "演示数据"

    def __post_init__(self) -> None:
        self.items = [x for x in self.items if x.item_id and x.title]
        seen: set[str] = set(); deduped: list[Item] = []
        for item in self.items:
            if item.item_id in seen:
                continue
            seen.add(item.item_id); deduped.append(item)
        self.items = deduped
        self.item_by_id = {x.item_id: x for x in self.items}
        self.interactions = [x for x in self.interactions if x.user_id and x.item_id in self.item_by_id]
        self.interactions.sort(key=lambda x: (x.user_id, x.timestamp, x.item_id))
        self.query_labels = [x for x in self.query_labels if x.query and any(i in self.item_by_id for i in x.relevant)]

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, name: str = "导入数据") -> "Catalog":
        items = [Item.from_dict(x) for x in payload.get("items", [])]
        if not items:
            raise ValueError("数据中至少需要一条包含 id 与 title 的有效内容")
        return cls(
            items=items,
            interactions=[Interaction.from_dict(x) for x in payload.get("interactions", [])],
            query_labels=[QueryLabel.from_dict(x) for x in payload.get("query_labels", [])],
            name=name,
        )

    def summary(self) -> dict[str, Any]:
        users = {x.user_id for x in self.interactions}
        cats = {c for item in self.items for c in item.categories}
        return {"name": self.name, "items": len(self.items), "users": len(users), "interactions": len(self.interactions), "queries": len(self.query_labels), "categories": len(cats)}

    def popularity_norm(self, item: Item) -> float:
        top = max((x.popularity for x in self.items), default=1.0)
        return log1p(item.popularity) / max(1e-9, log1p(max(1.0, top)))
