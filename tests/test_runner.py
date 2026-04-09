from __future__ import annotations

import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.planner import PlanOutput
from app.runner import CodeRepairRunner
from app.runner import OpenHandsRunResult
from app.schemas import BugTask, RepairResult, ReviewerDecision, TestExecutionResult


class ExtractAgentOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CodeRepairRunner.__new__(CodeRepairRunner)

    def test_extract_agent_output_prefers_latest_assistant_event_text(self) -> None:
        reviewer_json = (
            '{"decision":"accept","rationale":"All tests passed.","next_action":"None"}'
        )
        conversation = SimpleNamespace(
            state=SimpleNamespace(
                events=[
                    SimpleNamespace(
                        llm_message=SimpleNamespace(
                            role="user",
                            content=[SimpleNamespace(text="Return JSON only.")],
                        )
                    ),
                    SimpleNamespace(
                        llm_message=SimpleNamespace(
                            role="assistant",
                            content=[SimpleNamespace(text=reviewer_json)],
                        )
                    ),
                ]
            )
        )

        output = self.runner._extract_agent_output(conversation)

        self.assertEqual(output, reviewer_json)


class RetryLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CodeRepairRunner.__new__(CodeRepairRunner)
        self.runner.config = SimpleNamespace(
            results_dir=Path("/tmp"),
            max_retry_attempts=2,
            ensure_directories=lambda: None,
        )

    def test_run_plan_and_code_retries_until_reviewer_accepts(self) -> None:
        task = self._build_task()
        repo_path = Path("/tmp/demo-repo")

        planner_stage_outputs = [
            (
                PlanOutput(raw_output="plan-0"),
                OpenHandsRunResult(success=True, agent_output="planner-0", logs=["planner-0"]),
            ),
            (
                PlanOutput(raw_output="plan-1"),
                OpenHandsRunResult(success=True, agent_output="planner-1", logs=["planner-1"]),
            ),
        ]
        coder_stage_outputs = [
            OpenHandsRunResult(success=True, agent_output="coder-0", logs=["coder-0"]),
            OpenHandsRunResult(success=True, agent_output="coder-1", logs=["coder-1"]),
        ]
        tester_stage_outputs = [
            self._build_test_execution(success=False, failed_summary="failed-on-attempt-0"),
            self._build_test_execution(success=True, failed_summary=""),
        ]
        reviewer_stage_outputs = [
            (
                ReviewerDecision(
                    decision="retry",
                    rationale="Need another pass.",
                    next_action="Retry with a clean tree.",
                ),
                OpenHandsRunResult(success=True, agent_output="reviewer-0", logs=["reviewer-0"]),
            ),
            (
                ReviewerDecision(
                    decision="accept",
                    rationale="Looks good now.",
                    next_action="Ship it.",
                ),
                OpenHandsRunResult(success=True, agent_output="reviewer-1", logs=["reviewer-1"]),
            ),
        ]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(self.runner, "_run_planner_stage", side_effect=planner_stage_outputs)
            )
            stack.enter_context(
                patch.object(self.runner, "_run_coder_stage", side_effect=coder_stage_outputs)
            )
            stack.enter_context(
                patch.object(self.runner, "_run_tester_stage", side_effect=tester_stage_outputs)
            )
            stack.enter_context(
                patch.object(self.runner, "_run_reviewer_stage", side_effect=reviewer_stage_outputs)
            )
            stack.enter_context(patch.object(self.runner, "save_repair_result"))
            stack.enter_context(
                patch("app.runner.git_status", side_effect=["M calculator.py", "Working tree is clean."])
            )
            stack.enter_context(
                patch("app.runner.get_changed_files", side_effect=[["calculator.py"], []])
            )
            stack.enter_context(
                patch("app.runner.get_git_diff", side_effect=["diff-attempt-0", "pre-retry-diff-0", ""])
            )
            stack.enter_context(
                patch("app.runner.git_reset_hard_with_output", return_value=(True, "HEAD is now at abc123"))
            )
            result = self.runner.run_plan_and_code(task=task, repo_path=repo_path)

        self.assertTrue(result.success)
        self.assertEqual(result.reviewer_output["decision"], "accept")
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.retries, 1)
        self.assertEqual(result.repo_path, "repos/demo_repo")
        self.assertEqual(result.final_summary, result.summary)
        self.assertIn("started_at", result.timestamps)
        self.assertIn("completed_at", result.timestamps)
        self.assertEqual(result.attempts[0]["retry_index"], 0)
        self.assertEqual(result.attempts[1]["retry_index"], 1)
        self.assertEqual(result.attempts[0]["reviewer_output"]["decision"], "retry")
        self.assertEqual(result.attempts[0]["pre_retry_git_diff"], "pre-retry-diff-0")
        self.assertTrue(result.attempts[0]["rollback_attempted"])
        self.assertTrue(result.attempts[0]["rollback_success"])
        self.assertFalse(result.attempts[1]["rollback_attempted"])
        self.assertIn("Attempt 1/3", "\n".join(result.logs))
        self.assertIn("Attempt 2/3", "\n".join(result.logs))
        self.assertTrue(
            any(log["event"] == "retry_requested" for log in result.structured_logs)
        )

    def test_run_plan_and_code_records_rollback_failure_and_stops_after_max_retries(self) -> None:
        task = self._build_task()
        repo_path = Path("/tmp/demo-repo")
        self.runner.config.max_retry_attempts = 1

        planner_stage_outputs = [
            (
                PlanOutput(raw_output="plan-0"),
                OpenHandsRunResult(success=True, agent_output="planner-0", logs=[]),
            ),
            (
                PlanOutput(raw_output="plan-1"),
                OpenHandsRunResult(success=True, agent_output="planner-1", logs=[]),
            ),
        ]
        coder_stage_outputs = [
            OpenHandsRunResult(success=True, agent_output="coder-0", logs=[]),
            OpenHandsRunResult(success=True, agent_output="coder-1", logs=[]),
        ]
        tester_stage_outputs = [
            self._build_test_execution(success=False, failed_summary="failed-0"),
            self._build_test_execution(success=False, failed_summary="failed-1"),
        ]
        reviewer_stage_outputs = [
            (
                ReviewerDecision(
                    decision="retry",
                    rationale="Still broken.",
                    next_action="Try again.",
                ),
                OpenHandsRunResult(success=True, agent_output="reviewer-0", logs=[]),
            ),
            (
                ReviewerDecision(
                    decision="retry",
                    rationale="Out of retries.",
                    next_action="Stop retrying.",
                ),
                OpenHandsRunResult(success=True, agent_output="reviewer-1", logs=[]),
            ),
        ]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(self.runner, "_run_planner_stage", side_effect=planner_stage_outputs)
            )
            stack.enter_context(
                patch.object(self.runner, "_run_coder_stage", side_effect=coder_stage_outputs)
            )
            stack.enter_context(
                patch.object(self.runner, "_run_tester_stage", side_effect=tester_stage_outputs)
            )
            stack.enter_context(
                patch.object(self.runner, "_run_reviewer_stage", side_effect=reviewer_stage_outputs)
            )
            stack.enter_context(patch.object(self.runner, "save_repair_result"))
            stack.enter_context(
                patch("app.runner.git_status", side_effect=["M calculator.py", "M calculator.py"])
            )
            stack.enter_context(
                patch("app.runner.get_changed_files", side_effect=[["calculator.py"], ["calculator.py"]])
            )
            stack.enter_context(
                patch("app.runner.get_git_diff", side_effect=["diff-0", "pre-retry-diff-0", "diff-1"])
            )
            stack.enter_context(
                patch(
                    "app.runner.git_reset_hard_with_output",
                    return_value=(False, "reset failed: repository locked"),
                )
            )
            result = self.runner.run_plan_and_code(task=task, repo_path=repo_path)

        self.assertFalse(result.success)
        self.assertEqual(len(result.attempts), 2)
        self.assertTrue(result.attempts[0]["rollback_attempted"])
        self.assertFalse(result.attempts[0]["rollback_success"])
        self.assertEqual(
            result.attempts[0]["rollback_error"],
            "reset failed: repository locked",
        )
        self.assertIn("rollback: reset failed: repository locked", result.error)
        self.assertIn("Max retries reached", "\n".join(result.logs))
        self.assertTrue(
            any(log["event"] == "rollback_failed" for log in result.structured_logs)
        )

    def _build_task(self) -> BugTask:
        return BugTask(
            task_id="demo-001",
            repo_path="repos/demo_repo",
            issue_title="Fix demo bug",
            issue_description="Tests are failing.",
            expected_test_command="python3 -m unittest -q",
        )

    def _build_test_execution(self, success: bool, failed_summary: str) -> TestExecutionResult:
        return TestExecutionResult(
            success=success,
            returncode=0 if success else 1,
            stdout="",
            stderr="",
            failed_summary=failed_summary,
            executed_command="python3 -m unittest -q",
            execution_mode="explicit_command",
            notes="Used the task-provided test command.",
        )


class ResultPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.runner = CodeRepairRunner.__new__(CodeRepairRunner)
        self.runner.config = SimpleNamespace(
            results_dir=Path(self.temp_dir.name),
            ensure_directories=lambda: None,
            max_retry_attempts=2,
        )

    def test_save_repair_result_writes_directory_artifacts(self) -> None:
        result = RepairResult(
            task_id="demo-001",
            repo_path="repos/demo_repo",
            success=True,
            retries=1,
            test_output="test stdout",
            summary="Final summary",
            final_summary="Final summary",
            planner_output={"raw_output": "planner raw"},
            coder_output="coder raw",
            reviewer_output={"decision": "accept", "rationale": "done"},
            git_diff="diff --git a/file.py b/file.py",
            changed_files=["file.py"],
            timestamps={
                "started_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:01:00+00:00",
                "saved_at": "2026-01-01T00:01:01+00:00",
            },
        )

        output_path = self.runner.save_repair_result(result)

        result_dir = Path(self.temp_dir.name) / "demo-001"
        self.assertEqual(output_path, result_dir / "result.json")
        self.assertTrue((result_dir / "result.json").exists())
        self.assertTrue((result_dir / "final_diff.patch").exists())
        self.assertTrue((result_dir / "test_output.txt").exists())
        self.assertTrue((result_dir / "planner_output.txt").exists())
        self.assertTrue((result_dir / "coder_output.txt").exists())
        self.assertTrue((result_dir / "reviewer_output.txt").exists())
        self.assertEqual((result_dir / "test_output.txt").read_text(encoding="utf-8"), "test stdout")
        self.assertIn("planner raw", (result_dir / "planner_output.txt").read_text(encoding="utf-8"))
        self.assertIn("coder raw", (result_dir / "coder_output.txt").read_text(encoding="utf-8"))
        self.assertIn("accept", (result_dir / "reviewer_output.txt").read_text(encoding="utf-8"))
        self.assertIn("diff --git", (result_dir / "final_diff.patch").read_text(encoding="utf-8"))
        result_json = (result_dir / "result.json").read_text(encoding="utf-8")
        self.assertIn('"task_id": "demo-001"', result_json)
        self.assertIn('"repo_path": "repos/demo_repo"', result_json)
        self.assertIn('"retries": 1', result_json)
        self.assertIn('"final_summary": "Final summary"', result_json)

    def test_save_repair_result_writes_all_attempt_stage_outputs_when_retries_exist(self) -> None:
        result = RepairResult(
            task_id="demo-002",
            repo_path="repos/demo_repo",
            success=True,
            retries=1,
            test_output="latest output only should not be the only persisted content",
            summary="Final summary",
            final_summary="Final summary",
            planner_output={"raw_output": "plan-1"},
            coder_output="coder-1",
            reviewer_output={"decision": "accept", "rationale": "done"},
            attempts=[
                {
                    "retry_index": 0,
                    "planner_output": {"raw_output": "plan-0"},
                    "coder_output": "coder-0",
                    "test_execution": {
                        "success": False,
                        "returncode": 1,
                        "stdout": "stdout-0",
                        "stderr": "stderr-0",
                        "failed_summary": "failed-0",
                        "executed_command": "python3 -m unittest -q",
                        "execution_mode": "explicit_command",
                        "notes": "attempt-0",
                    },
                    "reviewer_output": {
                        "decision": "retry",
                        "rationale": "need retry",
                        "next_action": "try again",
                    },
                },
                {
                    "retry_index": 1,
                    "planner_output": {"raw_output": "plan-1"},
                    "coder_output": "coder-1",
                    "test_execution": {
                        "success": True,
                        "returncode": 0,
                        "stdout": "stdout-1",
                        "stderr": "",
                        "failed_summary": "",
                        "executed_command": "python3 -m unittest -q",
                        "execution_mode": "explicit_command",
                        "notes": "attempt-1",
                    },
                    "reviewer_output": {
                        "decision": "accept",
                        "rationale": "done",
                        "next_action": "ship it",
                    },
                },
            ],
        )

        self.runner.save_repair_result(result)

        result_dir = Path(self.temp_dir.name) / "demo-002"
        planner_output = (result_dir / "planner_output.txt").read_text(encoding="utf-8")
        coder_output = (result_dir / "coder_output.txt").read_text(encoding="utf-8")
        test_output = (result_dir / "test_output.txt").read_text(encoding="utf-8")
        reviewer_output = (result_dir / "reviewer_output.txt").read_text(encoding="utf-8")

        self.assertIn("Attempt 1", planner_output)
        self.assertIn("plan-0", planner_output)
        self.assertIn("plan-1", planner_output)
        self.assertIn("Attempt 2", coder_output)
        self.assertIn("coder-0", coder_output)
        self.assertIn("coder-1", coder_output)
        self.assertIn("stdout-0", test_output)
        self.assertIn("stdout-1", test_output)
        self.assertIn("stderr-0", test_output)
        self.assertIn('"decision": "retry"', reviewer_output)
        self.assertIn('"decision": "accept"', reviewer_output)

if __name__ == "__main__":
    unittest.main()
