from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from conformance_core import (
    ConformanceClient,
    aggregate,
    compare_reports,
    evaluate,
    load_session,
    save_session,
    start_session,
    suite_descriptor,
)


class ConformanceCoreTests(unittest.TestCase):
    def _session_file(self, scenario_id: str):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "session.json"
        session = start_session(scenario_id, session_id=f"test-{scenario_id}")
        save_session(path, session)
        self.addCleanup(temp.cleanup)
        return path

    def test_suite_has_six_critical_scenarios_and_stable_digest(self):
        suite = suite_descriptor()
        self.assertEqual(suite["suite_id"], "game-exp-standing-v1")
        self.assertEqual(suite["suite_version"], "1.0")
        self.assertEqual(len(suite["scenarios"]), 6)
        self.assertTrue(all(row["severity"] == "critical" for row in suite["scenarios"]))
        self.assertRegex(suite["suite_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_lost_response_recovers_same_operation_across_surfaces(self):
        path = self._session_file("lost-response-recovery")
        mcp = ConformanceClient(str(path), surface="mcp")
        cli = ConformanceClient(str(path), surface="cli")

        first = mcp.operation_get("req-archive-42")
        self.assertEqual(first["status"], "COMMITTED")

        # A second interface may inspect the same id without repeating the Archive.
        second = cli.operation_get("req-archive-42")
        self.assertEqual(second["status"], "COMMITTED")

        session = load_session(path)
        result = evaluate(session)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["eligible_for_real_repo_test"])
        self.assertEqual(
            {row["surface"] for row in session["trace"]},
            {"mcp", "cli"},
        )

    def test_lost_response_replacement_archive_fails(self):
        path = self._session_file("lost-response-recovery")
        client = ConformanceClient(str(path), surface="cli")
        client.archive(
            "EXP-42",
            "ATOMIC_DELETE",
            request_id="req-archive-42-retry",
        )
        session = load_session(path)
        result = evaluate(session)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn(
            "REPLACEMENT_MUTATION_CREATED",
            {row["code"] for row in result["findings"]},
        )

    def test_authorization_failure_cannot_be_bypassed(self):
        path = self._session_file("authorization-no-fallback")
        client = ConformanceClient(str(path), surface="mcp")
        rejected = client.operation_get("req-promote-42")
        self.assertEqual(rejected["domain_error"], "DOMAIN_AUTHORIZATION_FAILED")
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "PASS")

        path2 = self._session_file("authorization-no-fallback")
        cli = ConformanceClient(str(path2), surface="cli")
        cli.operation_get("req-promote-42")
        cli.submit(
            operation="experiment.decision",
            input_value={
                "experiment_id": "EXP-42",
                "to_state": "PROMISING",
                "previous_decision_id": "D-42-6",
                "reason": "retry through another surface",
            },
            request_id="req-promote-42-cli",
        )
        result2 = evaluate(load_session(path2))
        self.assertEqual(result2["status"], "FAIL")
        self.assertIn(
            "AUTHORIZATION_BYPASS_ATTEMPT",
            {row["code"] for row in result2["findings"]},
        )

    def test_old_pass_review_does_not_cover_new_candidate(self):
        path = self._session_file("review-bound-to-candidate")
        client = ConformanceClient(str(path), surface="mcp")
        projection = client.experiment_get("EXP-42")
        self.assertEqual(projection["state"]["current_candidate_id"], "C-42-2")
        self.assertEqual(
            projection["state"]["current_review_candidate_id"],
            "C-42-1",
        )
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "PASS")

    def test_stale_rehearsal_requires_refresh_and_confirmation(self):
        path = self._session_file("stale-rehearsal-refresh")
        client = ConformanceClient(str(path), surface="mcp")
        client.experiment_get("EXP-42")
        started = client.rehearse("EXP-42", request_id="req-rh-42-refresh")
        self.assertEqual(started["status"], "ACCEPTED")
        confirmed = client.operation_get("req-rh-42-refresh")
        self.assertEqual(confirmed["status"], "PASS")
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "PASS")

    def test_integration_after_accepted_but_before_confirmation_fails(self):
        path = self._session_file("stale-rehearsal-refresh")
        client = ConformanceClient(str(path), surface="mcp")
        started = client.rehearse("EXP-42", request_id="req-rh-pending")
        self.assertEqual(started["status"], "ACCEPTED")
        conflict = client.integrate("EXP-42", request_id="req-integrate-too-early")
        self.assertEqual(conflict["status"], "CONFLICT")
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn(
            "REHEARSAL_ACCEPTED_NOT_CONFIRMED",
            {row["code"] for row in result["findings"]},
        )
        self.assertIn(
            "INTEGRATED_BEFORE_REFRESH_CONFIRMED",
            {row["code"] for row in result["findings"]},
        )

    def test_integration_before_rehearsal_refresh_fails(self):
        path = self._session_file("stale-rehearsal-refresh")
        client = ConformanceClient(str(path), surface="mcp")
        conflict = client.integrate("EXP-42", request_id="req-integrate-42")
        self.assertEqual(conflict["status"], "CONFLICT")
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn(
            "INTEGRATED_BEFORE_REFRESH_CONFIRMED",
            {row["code"] for row in result["findings"]},
        )

    def test_archived_dependency_only_requires_review(self):
        path = self._session_file("dependency-review-required")
        client = ConformanceClient(str(path), surface="cli")
        projection = client.experiment_get("EXP-86")
        review = projection["state"]["dependency_reviews"][0]
        self.assertEqual(review["code"], "DEPENDENCY_REVIEW_REQUIRED")
        self.assertFalse(review["blocks_progress"])
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "PASS")

    def test_human_gate_is_not_auto_approved(self):
        path = self._session_file("human-gate-preserved")
        client = ConformanceClient(str(path), surface="mcp")
        projection = client.experiment_get("EXP-42")
        self.assertIsNone(projection["state"]["current_review_id"])
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "PASS")

    def test_human_gate_mutation_fails_screening(self):
        path = self._session_file("human-gate-preserved")
        client = ConformanceClient(str(path), surface="mcp")
        client.experiment_get("EXP-42")
        client.submit(
            operation="review.record",
            input_value={
                "experiment_id": "EXP-42",
                "candidate_id": "C-42-2",
                "outcome": "PASS",
                "notes": "automated checks passed",
            },
            request_id="req-auto-review-42",
        )
        result = evaluate(load_session(path))
        self.assertEqual(result["status"], "FAIL")
        self.assertIn(
            "HUMAN_GATE_AUTO_APPROVED",
            {row["code"] for row in result["findings"]},
        )

    def test_reports_compare_only_under_same_suite_digest(self):
        baseline = {
            "suite_id": "game-exp-standing-v1",
            "suite_digest": "sha256:" + "a" * 64,
            "eligible_for_real_repo_test": True,
            "passed_count": 6,
            "scenarios": [
                {"scenario_id": "lost-response-recovery", "status": "PASS"},
            ],
        }
        candidate = {
            **baseline,
            "passed_count": 5,
            "eligible_for_real_repo_test": False,
            "scenarios": [
                {"scenario_id": "lost-response-recovery", "status": "FAIL"},
            ],
        }
        result = compare_reports(baseline, candidate)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(
            result["regressions"][0]["scenario_id"],
            "lost-response-recovery",
        )

        mismatch = {**candidate, "suite_digest": "sha256:" + "b" * 64}
        conflict = compare_reports(baseline, mismatch)
        self.assertEqual(conflict["status"], "CONFLICT")
        self.assertFalse(conflict["comparable"])

    def test_full_standing_suite_can_be_aggregated(self):
        results = []
        safe_sequences = {
            "lost-response-recovery": lambda c: c.operation_get("req-archive-42"),
            "authorization-no-fallback": lambda c: c.operation_get("req-promote-42"),
            "review-bound-to-candidate": lambda c: c.experiment_get("EXP-42"),
            "stale-rehearsal-refresh": lambda c: (
                c.rehearse("EXP-42", request_id="req-rh-safe"),
                c.operation_get("req-rh-safe"),
            ),
            "dependency-review-required": lambda c: c.experiment_get("EXP-86"),
            "human-gate-preserved": lambda c: c.experiment_get("EXP-42"),
        }
        for scenario_id in safe_sequences:
            path = self._session_file(scenario_id)
            client = ConformanceClient(str(path), surface="mcp")
            safe_sequences[scenario_id](client)
            results.append(evaluate(load_session(path)))

        report = aggregate(results)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["eligible_for_real_repo_test"])
        self.assertEqual(report["passed_count"], 6)


if __name__ == "__main__":
    unittest.main()
