"""The evaluation harness as a CI gate: ranking quality must not slip below the baseline."""

import json

from evals import harness


def test_ranker_meets_baseline_and_tuning_claims(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "REPORT", tmp_path / "REPORT.md")
    assert harness.main([]) == 0, (tmp_path / "REPORT.md").read_text()


def test_gate_catches_a_regression():
    baseline = json.loads(harness.BASELINE.read_text())
    worse = {m: baseline[m] for m in harness.GATED} | {"mainstream_share": 1.0}
    failed = [r["metric"] for r in harness.gate(worse, baseline) if not r["pass"]]
    assert failed == ["mainstream_share"]


def test_known_failure_that_starts_passing_fails_the_run():
    check = {"check": "x", "pass": True, "known": "reason"}
    assert harness.failures([], [check]) == ["x (fixed: remove from KNOWN_FAILING)"]
