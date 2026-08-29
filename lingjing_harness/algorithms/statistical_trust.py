"""Uncertainty-aware trust policy for paired future-holdout evidence.

A fixed sample-count threshold is necessary for tests and tiny local datasets, but
it is not sufficient for production trust.  Two samples can be either perfectly
consistent or almost uninformative.  This module derives an explicit uncertainty
certificate from the paired bootstrap interval and reports an approximate sample
requirement when the interval is too wide for the observed effect.

The certificate is deliberately a promotion gate, never an optimizer objective.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Any


@dataclass(frozen=True, slots=True)
class PairedTrustPolicy:
    minimum_samples: int = 2
    minimum_probability_positive: float = 0.70
    noninferiority_margin: float = 0.01
    absolute_half_width_floor: float = 0.02
    effect_half_width_ratio: float = 4.0

    def assess(self, confidence: dict[str, Any]) -> dict[str, Any]:
        samples = max(0, int(confidence.get("samples", 0) or 0))
        available = bool(confidence.get("available"))
        probability_raw = confidence.get("probability_positive")
        probability = None if probability_raw is None else float(probability_raw)
        delta = float(confidence.get("delta", 0.0) or 0.0)
        ci = confidence.get("ci95")
        blockers: list[str] = []

        if not available:
            blockers.append("paired_uncertainty_unavailable")
        if samples < self.minimum_samples:
            blockers.append("paired_samples_below_minimum")
        if probability is None or not isfinite(probability):
            blockers.append("probability_positive_unavailable")
        elif probability < self.minimum_probability_positive:
            blockers.append("probability_positive_below_threshold")

        low: float | None = None
        high: float | None = None
        half_width: float | None = None
        required_samples = self.minimum_samples
        if not isinstance(ci, (list, tuple)) or len(ci) != 2:
            blockers.append("paired_ci_unavailable")
        else:
            try:
                low = float(ci[0])
                high = float(ci[1])
            except (TypeError, ValueError):
                blockers.append("paired_ci_invalid")
            else:
                if not isfinite(low) or not isfinite(high) or high < low:
                    blockers.append("paired_ci_invalid")
                else:
                    half_width = 0.5 * (high - low)
                    if low < -abs(self.noninferiority_margin):
                        blockers.append("paired_ci_crosses_noninferiority_margin")

                    # If uncertainty is much larger than the observed effect, the
                    # sign probability alone can be unstable.  Estimate the sample
                    # count needed to shrink the bootstrap half-width under the
                    # standard sqrt(n) approximation.  Deterministic/near-zero-width
                    # evidence is not penalized.
                    target_half_width = max(
                        self.absolute_half_width_floor,
                        self.effect_half_width_ratio * max(abs(delta), 0.0025),
                    )
                    if half_width > target_half_width and samples > 0:
                        required_samples = max(
                            self.minimum_samples,
                            int(ceil(samples * (half_width / target_half_width) ** 2)),
                        )
                        # Very strong sign evidence may still be useful for routing,
                        # but durable trust should not be granted while materially
                        # underpowered against the observed effect.
                        if probability is None or probability < 0.95:
                            blockers.append("paired_confidence_underpowered")

        return {
            "available": available,
            "samples": samples,
            "delta": round(delta, 6),
            "ci95": [round(low, 6), round(high, 6)] if low is not None and high is not None else None,
            "ci_half_width": round(half_width, 6) if half_width is not None else None,
            "probability_positive": round(probability, 4) if probability is not None and isfinite(probability) else None,
            "minimum_probability_positive": self.minimum_probability_positive,
            "noninferiority_margin": self.noninferiority_margin,
            "approx_required_samples": required_samples,
            "passed": not blockers,
            "blockers": blockers,
            "method": "paired_bootstrap_uncertainty_gate_v2",
        }


DEFAULT_PAIRED_TRUST_POLICY = PairedTrustPolicy()


def assess_paired_trust(confidence: dict[str, Any]) -> dict[str, Any]:
    return DEFAULT_PAIRED_TRUST_POLICY.assess(confidence)


__all__ = [
    "PairedTrustPolicy",
    "DEFAULT_PAIRED_TRUST_POLICY",
    "assess_paired_trust",
]
