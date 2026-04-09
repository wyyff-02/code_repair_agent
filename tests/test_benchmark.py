from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.eval.benchmark import BenchmarkReport, BenchmarkRunner
from app.schemas import BugTask, RepairResult


class FakeRunner:
    def __init__(self, tasks: dict[str, BugTask], results: dict[str, RepairResult]) -> None:
        self._tasks = tasks
        self._results = results
        self.run_calls: list[tuple[str, Path]] = []

    def load_task(self, task_file: Path, repo_path_override: str | None = None) -> BugTask:
        return self._tasks[task_file.name]

    def run_plan_and_code(self, task: BugTask, repo_path: Path) -> RepairResult:
        self.run_calls.append((task.task_id, repo_path))
        return self._results[task.task_id]

    def run_baseline(self, task: BugTask, repo_path: Path) -> RepairResult:
        self.run_calls.append((task.task_id, repo_path))
        return self._results[task.task_id]


class BenchmarkRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_root = Path(self.temp_dir.name)
        self.tasks_dir = self.project_root / "data" / "tasks"
        self.results_dir = self.project_root / "data" / "results"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def test_run_collects_metrics_and_writes_markdown_report(self) -> None:
        alpha_task_file = self._write_task_file("alpha.json", "task-alpha")
        beta_task_file = self._write_task_file("beta.json", "task-beta")

        fake_runner = FakeRunner(
            tasks={
                alpha_task_file.name: self._build_task("task-alpha"),
                beta_task_file.name: self._build_task("task-beta"),
            },
            results={
                "task-alpha": self._build_result(
                    task_id="task-alpha",
                    success=True,
                    retries=1,
                    test_passed=True,
                ),
                "task-beta": self._build_result(
                    task_id="task-beta",
                    success=False,
                    retries=2,
                    test_passed=False,
                ),
            },
        )
        config = SimpleNamespace(
            project_root=self.project_root,
            tasks_dir=self.tasks_dir,
            results_dir=self.results_dir,
            ensure_directories=lambda: None,
        )

        benchmark_runner = BenchmarkRunner(config=config, runner=fake_runner)

        report = benchmark_runner.run()

        self.assertIsInstance(report, BenchmarkReport)
        self.assertEqual(report.metrics.total_tasks, 2)
        self.assertEqual(report.metrics.success_count, 1)
        self.assertAlmostEqual(report.metrics.success_rate, 0.5)
        self.assertAlmostEqual(report.metrics.avg_retries, 1.5)
        self.assertAlmostEqual(report.metrics.test_pass_rate, 0.5)
        self.assertEqual(len(report.task_runs), 2)
        self.assertEqual(report.report_path, self.results_dir / "benchmark_summary.md")
        self.assertTrue(report.report_path.exists())
        report_text = report.report_path.read_text(encoding="utf-8")
        self.assertIn("# Benchmark Summary", report_text)
        self.assertIn("total_tasks", report_text)
        self.assertIn("task-alpha", report_text)
        self.assertIn("task-beta", report_text)
        self.assertEqual(
            fake_runner.run_calls,
            [
                ("task-alpha", self.project_root / "repos" / "demo_repo"),
                ("task-beta", self.project_root / "repos" / "demo_repo"),
            ],
        )

    def test_run_includes_failed_task_when_runner_raises(self) -> None:
        task_file = self._write_task_file("broken.json", "task-broken")

        class ErrorRunner(FakeRunner):
            def run_plan_and_code(self, task: BugTask, repo_path: Path) -> RepairResult:
                raise RuntimeError("runner crashed")

        fake_runner = ErrorRunner(
            tasks={task_file.name: self._build_task("task-broken")},
            results={},
        )
        config = SimpleNamespace(
            project_root=self.project_root,
            tasks_dir=self.tasks_dir,
            results_dir=self.results_dir,
            ensure_directories=lambda: None,
        )

        benchmark_runner = BenchmarkRunner(config=config, runner=fake_runner)

        report = benchmark_runner.run()

        self.assertEqual(report.metrics.total_tasks, 1)
        self.assertEqual(report.metrics.success_count, 0)
        self.assertAlmostEqual(report.metrics.success_rate, 0.0)
        self.assertAlmostEqual(report.metrics.avg_retries, 0.0)
        self.assertAlmostEqual(report.metrics.test_pass_rate, 0.0)
        self.assertEqual(report.task_runs[0].task_id, "task-broken")
        self.assertFalse(report.task_runs[0].success)
        self.assertIn("runner crashed", report.task_runs[0].error)

    def _write_task_file(self, filename: str, task_id: str) -> Path:
        task_file = self.tasks_dir / filename
        task_file.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "repo_path": "repos/demo_repo",
                    "issue_title": f"Issue for {task_id}",
                    "issue_description": f"Description for {task_id}",
                }
            ),
            encoding="utf-8",
        )
        return task_file

    def _build_task(self, task_id: str) -> BugTask:
        return BugTask(
            task_id=task_id,
            repo_path="repos/demo_repo",
            issue_title=f"Issue for {task_id}",
            issue_description=f"Description for {task_id}",
        )

    def _build_result(
        self,
        task_id: str,
        success: bool,
        retries: int,
        test_passed: bool,
    ) -> RepairResult:
        task_result_dir = self.results_dir / task_id
        task_result_dir.mkdir(parents=True, exist_ok=True)
        result_path = task_result_dir / "result.json"
        result_path.write_text("{}", encoding="utf-8")
        return RepairResult(
            task_id=task_id,
            repo_path="repos/demo_repo",
            success=success,
            retries=retries,
            test_output="ok",
            summary="summary",
            final_summary="summary",
            test_execution={"success": test_passed},
            result_dir=str(task_result_dir),
            output_file=str(result_path),
        )


if __name__ == "__main__":
    unittest.main()
