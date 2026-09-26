import pytest


def test_anomaly_flags_are_review_signals_not_reward_or_ban_decisions():
    from app.application.anti_cheat import anomaly_flags

    assert anomaly_flags(outcome="accepted", elapsed_seconds=2) == []
    assert anomaly_flags(outcome="accepted", elapsed_seconds=0.2) == ["fast_decision"]
    assert anomaly_flags(outcome="duplicate", replay_count=5) == ["frequent_replay"]
    assert anomaly_flags(outcome="conflicting") == ["conflicting_command"]
    assert anomaly_flags(outcome="accepted", active_sessions=5) == [
        "many_parallel_sessions"
    ]
    assert anomaly_flags(outcome="accepted", perfect_count=3) == [
        "repeated_perfect_sequence"
    ]
    assert "rapid_completion" in anomaly_flags(
        outcome="accepted", completion_seconds=0.3
    )


@pytest.mark.parametrize("value", ["0", "-1", "invalid", "100001"])
def test_rate_policy_rejects_invalid_configuration(monkeypatch, value):
    from app.application.anti_cheat import RatePolicy

    monkeypatch.setenv("RATE_LIMIT_DECISION_PER_MINUTE", value)
    with pytest.raises(ValueError):
        RatePolicy.from_environment("decision")
