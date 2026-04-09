from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path

from app.main import build_benchmark_summary, build_parser, build_repair_summary
from app.schemas import RepairResult
from app.eval.benchmark import BenchmarkMetrics, BenchmarkReport, BenchmarkTaskRun


class MainSummaryTests(unittest.TestCase):
    def test_build_repair_summary_returns_compact_payload(self) -> None:
        result = RepairResult(
            task_id="demo-001",
            repo_path="repos/demo_repo",
            success=True,
            retries=1,
            test_output="OK",
            summary="Final summary",
            final_summary="Final summary",
            changed_files=["calculator.py"],
            result_dir="/tmp/demo-001",
            output_file="/tmp/demo-001/result.json",
        )

        summary = build_repair_summary(
            result=result,
            repo_path=Path("/workspace/repos/demo_repo"),
        )

        self.assertEqual(summary["task_id"], "demo-001")
        self.assertTrue(summary["success"])
        self.assertEqual(summary["retries"], 1)
        self.assertEqual(summary["result_dir"], "/tmp/demo-001")
        self.assertEqual(summary["result_json"], "/tmp/demo-001/result.json")
        self.assertEqual(summary["changed_files"], ["calculator.py"])
        self.assertEqual(summary["final_summary"], "Final summary")

    def test_build_benchmark_summary_returns_compact_payload(self) -> None:
        report = BenchmarkReport(
            metrics=BenchmarkMetrics(
                total_tasks=3,
                success_count=2,
                total_retries=1,
                test_pass_count=2,
            ),
            task_runs=[
                BenchmarkTaskRun(
                    task_id="demo-001",
                    task_file="data/tasks/demo-001.json",
                    repo_path="repos/demo_repo",
                    success=True,
                    retries=0,
                    test_passed=True,
                    result_dir="/tmp/demo-001",
                    result_json="/tmp/demo-001/result.json",
                ),
                BenchmarkTaskRun(
                    task_id="demo-002",
                    task_file="data/tasks/demo-002.json",
                    repo_path="repos/demo_repo",
                    success=False,
                    retries=1,
                    test_passed=False,
                    result_dir="/tmp/demo-002",
                    result_json="/tmp/demo-002/result.json",
                ),
            ],
            report_path=Path("/tmp/benchmark_summary.md"),
        )

        summary = build_benchmark_summary(report)

        self.assertEqual(summary["total_tasks"], 3)
        self.assertEqual(summary["success_count"], 2)
        self.assertAlmostEqual(summary["success_rate"], 2 / 3)
        self.assertAlmostEqual(summary["avg_retries"], 1 / 3)
        self.assertAlmostEqual(summary["test_pass_rate"], 2 / 3)
        self.assertEqual(summary["report_path"], "/tmp/benchmark_summary.md")
        self.assertEqual(summary["task_run_count"], 2)


class MainParserTests(unittest.TestCase):
    def test_build_parser_rejects_conflicting_run_shortcuts(self) -> None:
        parser = build_parser()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "run",
                        "--task-file",
                        "data/tasks/sample_bug_task.json",
                        "--run-tests-only",
                        "--run-agent",
                    ]
                )

    def test_build_parser_rejects_conflicting_benchmark_task_sources(self) -> None:
        parser = build_parser()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "benchmark",
                        "--tasks-dir",
                        "data/tasks",
                        "--task-files",
                        "data/tasks/sample_bug_task.json",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
