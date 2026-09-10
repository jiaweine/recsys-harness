"""Xushu Search & Recommendation Agent Harness."""

from . import online_experiment_store as _online_experiment_store_module
from . import store as _store_module
from .api_message_start import (
    install_message_store_fast_paths as _install_message_store_fast_paths,
)
from .store_conversation_detail_read import (
    install_conversation_detail_read_boundary as _install_conversation_detail_read_boundary,
)
from .store_assistant_lookup import (
    install_fresh_run_assistant_lookup_gate as _install_fresh_run_assistant_lookup_gate,
)
from .store_online_experiment_listing import (
    install_online_experiment_list_aggregation as _install_online_experiment_list_aggregation,
)
from .store_run_recovery import install_run_recovery_claim_fence as _install_run_recovery_claim_fence
from .store_run_schema_migration import (
    install_workspace_run_schema_migration_guard as _install_workspace_run_schema_migration_guard,
)
from .store_workspace_publication import install_workspace_publication_fence as _install_workspace_publication_fence
from .store_workspace_publication_atomic_fence import (
    install_workspace_publication_atomic_fence as _install_workspace_publication_atomic_fence,
)

_install_workspace_run_schema_migration_guard(_store_module)
_install_conversation_detail_read_boundary(_store_module)
_install_message_store_fast_paths(_store_module)
_install_fresh_run_assistant_lookup_gate(_store_module)
_install_run_recovery_claim_fence(_store_module)
_install_workspace_publication_fence(_store_module)
_install_workspace_publication_atomic_fence(_store_module)
_install_online_experiment_list_aggregation(_online_experiment_store_module)

from .adapters import (
    AdapterRecommendationEngine,
    AdapterSearchEngine,
    CallableRecommendAdapter,
    CallableSearchAdapter,
    RecommendServingAdapter,
    SearchServingAdapter,
)
from .counterfactual import CounterfactualRecord, evaluate_off_policy
from .domain import Catalog, Item, Interaction, QueryLabel
from .experiments import (
    ExperimentCriteria,
    ExperimentSpec,
    evaluate_counterfactual_experiment,
)
from .production import ExposureEvent, RewardSpec
from .runtime.harness import AgentHarness
from .slate_counterfactual import SlatePositionRecord, evaluate_slate_off_policy
from .slate_experiments import (
    SlateExperimentCriteria,
    SlateExperimentSpec,
    evaluate_slate_experiment,
)

__all__ = [
    "Catalog",
    "Item",
    "Interaction",
    "QueryLabel",
    "ExposureEvent",
    "RewardSpec",
    "CounterfactualRecord",
    "evaluate_off_policy",
    "ExperimentCriteria",
    "ExperimentSpec",
    "evaluate_counterfactual_experiment",
    "SlatePositionRecord",
    "evaluate_slate_off_policy",
    "SlateExperimentCriteria",
    "SlateExperimentSpec",
    "evaluate_slate_experiment",
    "SearchServingAdapter",
    "RecommendServingAdapter",
    "AdapterSearchEngine",
    "AdapterRecommendationEngine",
    "CallableSearchAdapter",
    "CallableRecommendAdapter",
    "AgentHarness",
]
