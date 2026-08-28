# Counterfactual OPE & Experiment Gates

Xushu supports explicit contextual-bandit off-policy evaluation for Search and Recommendation policies when the integration can provide the probability that both the logging policy and the target policy assign to the **same logged action** in each decision context.

The contract is deliberately explicit: rank, score, position, or an unrelated exposure propensity is not converted into a target-policy probability.

## CounterfactualRecord

Each decision is represented by one logged action:

```python
from lingjing_harness import CounterfactualRecord

row = CounterfactualRecord(
    decision_id="req-001",
    surface="recommend",
    action_id="sku-42",
    reward=1.0,
    logging_propensity=0.25,   # mu(a_logged | x)
    target_propensity=0.50,    # pi(a_logged | x)
    logging_policy_id="prod-control",
    target_policy_id="candidate-a",
)
```

Required identity and probability fields:

```text
decision_id
surface                    search | recommend
action_id
reward
logging_propensity         mu(a_logged | x)
target_propensity          pi(a_logged | x)
logging_policy_id
target_policy_id
```

For Doubly Robust evaluation, provide both direct-model quantities:

```text
logged_reward_estimate     q_hat(x, a_logged)
target_reward_estimate     E_{a~pi(.|x)} q_hat(x, a)
```

The two values are separate so an arbitrary model or ranking score cannot be mistaken for a valid DR baseline.

## IPS / SNIPS / Doubly Robust

```python
from lingjing_harness import evaluate_off_policy

report = evaluate_off_policy(
    records,
    importance_weight_cap=20.0,
    bootstrap_iterations=600,
)
```

The report includes:

```text
estimators
  logged_mean
  ips
  snips
  dr

confidence
  estimator delta vs logged
  CI95
  probability_positive

diagnostics
  raw importance-weight ESS
  clipped importance-weight ESS
  support coverage
  clipped share
  propensity ranges
  direct-model coverage
```

Weight clipping is reported explicitly. Raw-weight effective sample size is retained alongside clipped-weight ESS so variance control cannot make weak logging-policy overlap appear healthier than it is.

DR is available only when every decision supplies the two direct-model inputs required by the contract.

## Experiment eligibility

Counterfactual evidence can be evaluated against product-owned criteria before a candidate advances to a controlled online experiment.

```python
from lingjing_harness import (
    ExperimentCriteria,
    ExperimentSpec,
    evaluate_counterfactual_experiment,
)

criteria = ExperimentCriteria(
    minimum_samples=1000,
    minimum_effective_sample_ratio=0.5,
    maximum_clipped_share=0.05,
    minimum_support_coverage=0.95,
    minimum_probability_positive=0.95,
    minimum_estimated_delta=0.0,
)

spec = ExperimentSpec(
    experiment_id="exp-candidate-a",
    surface="recommend",
    hypothesis="candidate improves business reward",
    logging_policy_id="prod-control",
    candidate_policy_id="candidate-a",
    primary_estimator="snips",
    criteria=criteria,
    importance_weight_cap=20.0,
)

result = evaluate_counterfactual_experiment(records, spec)
```

The decision surface is explicit:

```text
eligible_for_online_test
automatic_activation = false
primary_estimator
effective_sample_ratio_basis = raw_importance_weights
blockers
next_step
```

Passing this gate means the counterfactual evidence satisfies the configured criteria for a **controlled online experiment**. Production activation remains a separate authority and lifecycle decision.

## Evidence rules

Counterfactual reports enforce these identities:

- exactly one logged action per `decision_id`;
- exactly one surface per report;
- exactly one logging policy and one target policy per report;
- valid logging and target probabilities for the same logged action;
- explicit support and overlap diagnostics;
- confidence is not claimed from a single decision;
- experiment eligibility gates on **raw** importance-weight ESS, not clipping-inflated ESS;
- a missing DR reward model keeps DR unavailable rather than substituting another score.

These rules keep counterfactual evaluation in the same evidence-first model as the rest of Xushu: estimators inform a decision, while authority, controlled experiments, activation, revalidation, and retirement remain distinct lifecycle steps.
