from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import blake2b
from math import exp, sqrt

from lingjing_harness.domain import Catalog, Item
from .text import cosine, hashed_vector


@dataclass(frozen=True)
class RecommendConfig:
    profile: float = 0.34
    graph: float = 0.20
    category: float = 0.10
    quality: float = 0.12
    freshness: float = 0.13
    popularity: float = 0.05
    novelty: float = 0.06
    diversity: float = 0.14
    exploration: float = 0.04


class RecommendationEngine:
    """Project-owned implicit-feedback recommender with graph + semantic profile + slate optimization."""

    def __init__(self, catalog: Catalog, config: RecommendConfig | None = None) -> None:
        self.catalog = catalog; self.config = config or RecommendConfig()
        self._vectors = {x.item_id: hashed_vector(" ".join([x.title, x.text, *x.categories])) for x in catalog.items}
        self._by_user: dict[str, list] = defaultdict(list)
        for e in catalog.interactions: self._by_user[e.user_id].append(e)
        self._co: dict[str, Counter[str]] = defaultdict(Counter)
        for events in self._by_user.values():
            ids = list(dict.fromkeys(e.item_id for e in events))
            for i,a in enumerate(ids):
                for b in ids[i+1:]:
                    self._co[a][b] += 1; self._co[b][a] += 1

    def known_users(self) -> list[str]: return sorted(self._by_user)

    def _profile(self, user_id: str) -> tuple[dict[int,float], Counter[str], set[str], Counter[str]]:
        events = self._by_user.get(user_id, []); seen = {e.item_id for e in events}; cats: Counter[str] = Counter(); seeds: Counter[str] = Counter(); vec: dict[int,float] = {}
        max_ts = max((e.timestamp for e in events), default=0.0)
        for e in events:
            item = self.catalog.item_by_id[e.item_id]
            age = max(0.0, max_ts-e.timestamp); recency = exp(-age/30.0) if max_ts > 0 else 1.0
            w = e.weight*(.55+.45*recency); seeds[e.item_id] += w
            for k,v in self._vectors[e.item_id].items(): vec[k] = vec.get(k,0.0)+v*w
            for c in item.categories: cats[c] += w
        norm = sqrt(sum(v*v for v in vec.values())) or 1.0
        return {k:v/norm for k,v in vec.items()}, cats, seen, seeds

    def _graph(self, item_id: str, seeds: Counter[str]) -> float:
        raw = sum(weight*self._co[s].get(item_id,0) for s,weight in seeds.items())
        denom = sum(seeds.values()) or 1.0
        return min(1.0, raw/denom)

    @staticmethod
    def _similarity(a: Item, b: Item) -> float:
        aa, bb = set(a.categories), set(b.categories)
        return len(aa&bb)/max(1,len(aa|bb))

    @staticmethod
    def _stable_explore(user_id: str, item_id: str) -> float:
        value = int.from_bytes(blake2b(f"{user_id}:{item_id}".encode(), digest_size=4).digest(), "little")
        return (value % 1000)/1000.0

    def recommend(self, user_id: str, *, limit: int = 10) -> list[dict]:
        profile, cats, seen, seeds = self._profile(user_id); cat_total = sum(cats.values()) or 1.0
        rows=[]
        for item in self.catalog.items:
            if not item.eligible or item.item_id in seen: continue
            profile_fit = max(0.0, cosine(profile, self._vectors[item.item_id])) if profile else 0.0
            cat_fit = sum(cats.get(c,0.0) for c in item.categories)/cat_total
            graph = self._graph(item.item_id, seeds); pop = self.catalog.popularity_norm(item); novelty = 1.0-pop
            explore = self._stable_explore(user_id,item.item_id)*item.freshness
            base = self.config.profile*profile_fit+self.config.graph*graph+self.config.category*cat_fit+self.config.quality*item.quality+self.config.freshness*item.freshness+self.config.popularity*pop+self.config.novelty*novelty+self.config.exploration*explore
            rows.append({"item":item,"base":base,"signals":{"fit":round(min(1.0,.55*profile_fit+.30*cat_fit+.15*graph),4),"quality":round(item.quality,4),"freshness":round(item.freshness,4),"novelty":round(novelty,4)}})
        rows.sort(key=lambda x:(-x["base"],x["item"].item_id)); pool=rows[:max(40,limit*6)]; selected=[]
        while pool and len(selected)<limit:
            best=None; best_score=float("-inf")
            for row in pool:
                redundancy=max((self._similarity(row["item"],x["item"]) for x in selected),default=0.0)
                adjusted=row["base"]-self.config.diversity*redundancy
                if adjusted>best_score: best_score,best=adjusted,row
            assert best is not None
            selected.append({**best,"adjusted":best_score}); pool.remove(best)
        return [{"rank":i+1,**r["item"].public_dict(),"score":round(r["adjusted"],5),"signals":r["signals"]} for i,r in enumerate(selected)]
