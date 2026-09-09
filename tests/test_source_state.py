from skdashboard.source_state import rollup_source_states, source_state


def test_dimensions_are_independent_and_legacy_truth_is_preserved():
    state = source_state(availability="available", freshness="stale", coverage="complete", data_quality="valid")
    assert state["freshness"] == "stale"
    assert state["coverage"] == "complete"
    assert state["truth_state"] == "stale"


def test_rollup_mixed_states_keeps_observation_dimensions():
    result = rollup_source_states([
        source_state(availability="available", freshness="fresh", coverage="complete", data_quality="valid"),
        source_state(availability="available", freshness="stale", coverage="partial", data_quality="degraded", degradation_reasons=[{"code": "LAG"}]),
    ])
    assert result["availability"] == "available"
    assert result["freshness"] == "stale"
    assert result["coverage"] == "partial"
    assert result["data_quality"] == "degraded"
    assert result["truth_state"] == "partial"
    assert result["degradation_reasons"] == [{"code": "LAG", "message": ""}]
