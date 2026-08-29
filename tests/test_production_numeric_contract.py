from __future__ import annotations

import pytest

from lingjing_harness.production import ExposureEvent, RewardSpec


def _event(**overrides):
    row = {
        "request_id": "req-1",
        "timestamp": 100,
        "surface": "recommend",
        "item_id": "sku-1",
        "event": "click",
        "value": 1,
        "propensity": 0.5,
        "position": 2,
        "user_id": "u-1",
    }
    row.update(overrides)
    return row


def test_reward_spec_preserves_numeric_string_ingestion():
    spec = RewardSpec.from_dict(
        {
            "weights": {"click": "0.5", "purchase": "5"},
            "inverse_propensity_cap": "20",
        }
    )

    assert spec.weights == {"click": 0.5, "purchase": 5.0}
    assert spec.inverse_propensity_cap == 20.0
    assert spec.reward("click", "2") == 1.0


@pytest.mark.parametrize(
    "payload",
    [
        {"weights": {"click": True}},
        {"weights": {"click": 1.0}, "inverse_propensity_cap": True},
    ],
)
def test_reward_spec_rejects_boolean_numeric_fields(payload):
    with pytest.raises(ValueError):
        RewardSpec.from_dict(payload)


def test_reward_value_rejects_boolean_even_when_weight_is_valid():
    spec = RewardSpec(weights={"click": 1.0})
    with pytest.raises(ValueError, match="reward event value"):
        spec.reward("click", True)


def test_exposure_event_preserves_numeric_string_ingestion():
    event = ExposureEvent.from_dict(
        _event(timestamp="100.25", value="2.5", propensity="0.4", position="3")
    )

    assert event.timestamp == 100.25
    assert event.value == 2.5
    assert event.propensity == 0.4
    assert event.position == 3


@pytest.mark.parametrize("field", ["timestamp", "value", "propensity", "position"])
def test_exposure_event_rejects_boolean_numeric_fields(field):
    with pytest.raises(ValueError):
        ExposureEvent.from_dict(_event(**{field: True}))


@pytest.mark.parametrize("position", [1.0, 1.9, "1.0", "1.9", object()])
def test_exposure_event_rejects_non_integer_positions_without_truncation(position):
    with pytest.raises(ValueError, match="event.position 必须是整数"):
        ExposureEvent.from_dict(_event(position=position))


@pytest.mark.parametrize("position", [0, -1, "0", "-2"])
def test_exposure_event_rejects_non_positive_positions(position):
    with pytest.raises(ValueError, match="event.position 必须 >= 1"):
        ExposureEvent.from_dict(_event(position=position))


@pytest.mark.parametrize("field", ["timestamp", "value"])
def test_exposure_event_rejects_non_finite_core_values(field):
    with pytest.raises(ValueError, match="必须是有限数值"):
        ExposureEvent.from_dict(_event(**{field: float("nan")}))


@pytest.mark.parametrize("propensity", [0, -0.1, 1.1, float("nan"), float("inf")])
def test_exposure_event_rejects_invalid_propensity(propensity):
    with pytest.raises(ValueError, match="event.propensity 必须在"):
        ExposureEvent.from_dict(_event(propensity=propensity))
