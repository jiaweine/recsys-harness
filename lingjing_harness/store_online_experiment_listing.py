from __future__ import annotations

from typing import Any


def install_online_experiment_list_aggregation(store_module: Any) -> None:
    """Replace the experiment-list N+1 count loop with one bounded SQL query."""

    if getattr(store_module, "_ONLINE_EXPERIMENT_LIST_AGGREGATION_INSTALLED", False):
        return

    store_cls = store_module.DurableOnlineExperimentStore

    def list_experiments(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                """
                with recent as (
                  select experiment_id,status,current_epoch_id,version,created_at,updated_at
                  from online_experiments
                  order by updated_at desc
                  limit ?
                )
                select
                  recent.experiment_id,
                  recent.status,
                  recent.current_epoch_id,
                  recent.version,
                  recent.created_at,
                  recent.updated_at,
                  count(observations.unit_id) as observation_count
                from recent
                left join online_experiment_observations as observations
                  on observations.experiment_id=recent.experiment_id
                group by
                  recent.experiment_id,
                  recent.status,
                  recent.current_epoch_id,
                  recent.version,
                  recent.created_at,
                  recent.updated_at
                order by recent.updated_at desc
                """,
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "observation_count": int(row["observation_count"] or 0),
            }
            for row in rows
        ]

    store_cls.list_experiments = list_experiments
    store_module._ONLINE_EXPERIMENT_LIST_AGGREGATION_INSTALLED = True


__all__ = ["install_online_experiment_list_aggregation"]
