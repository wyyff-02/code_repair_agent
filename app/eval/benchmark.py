from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.eval.metrics import BenchmarkMetrics
from app.runner import CodeRepairRunner
from app.schemas import RepairResult


LOGGER = logging.getLogger(__name__)


@dataclass
class BenchmarkTaskRun:
    task_id: str
    task_file: str
    repo_path: str
    success: bool
    retries: int
    test_passed: bool
    result_dir: str = ""
    result_json: str = ""
    final_summary: str = ""
    error: str = ""


@dataclass
class BenchmarkReport:
    metrics: BenchmarkMetrics
    task_runs: list[BenchmarkTaskRun]
    report_path: Path


class BenchmarkRunner:
    """Serial benchmark runner built on top of the existing repair workflow."""

    def __init__(
        self,
        config: AppConfig,
        runner: CodeRepairRunner | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or CodeRepairRunner(config)

    def run(
        self,
        task_files: list[Path] | None = None,
        tasks_dir: Path | None = None,
        mode: str = "plan_and_code",
    ) -> BenchmarkReport:
        self.config.ensure_directories()
        resolved_task_files = self.resolve_task_files(task_files=task_files, tasks_dir=tasks_dir)
        LOGGER.info(
            "Starting serial benchmark run for %s task(s) in mode=%s.",
            len(resolved_task_files),
            mode,
        )

        task_runs: list[BenchmarkTaskRun] = []
        with self._benchmark_log_scope():
            for index, task_file in enumerate(resolved_task_files, start=1):
                LOGGER.info("Benchmark task %s/%s: %s", index, len(resolved_task_files), task_file)
                task_runs.append(self._run_task_file(task_file=task_file, mode=mode))

        metrics = self._build_metrics(task_runs)
        report_path = self.config.results_dir / "benchmark_summary.md"
        report = BenchmarkReport(
            metrics=metrics,
            task_runs=task_runs,
            report_path=report_path,
        )
        report_path.write_text(self.render_markdown(report), encoding="utf-8")
        LOGGER.info("Benchmark summary saved to %s", report_path)
        return report

    def resolve_task_files(
        self,
        task_files: list[Path] | None = None,
        tasks_dir: Path | None = None,
    ) -> list[Path]:
        if task_files:
            return sorted(self._resolve_path(task_file) for task_file in task_files)

        resolved_tasks_dir = self._resolve_path(tasks_dir or self.config.tasks_dir)
        return sorted(path for path in resolved_tasks_dir.glob("*.json") if path.is_file())

    def _run_task_file(self, task_file: Path, mode: str) -> BenchmarkTaskRun:
        task_id = task_file.stem
        try:
            task = self.runner.load_task(task_file=task_file)
            task_id = task.task_id
            repo_path = self._resolve_repo_path(task.repo_path)
            result = self._run_task(task=task, repo_path=repo_path, mode=mode)
            return self._task_run_from_result(task_file=task_file, result=result)
        except Exception as exc:
            return BenchmarkTaskRun(
                task_id=task_id,
                task_file=str(task_file),
                repo_path="",
                success=False,
                retries=0,
                test_passed=False,
                error=str(exc),
            )

    def _run_task(self, task: Any, repo_path: Path, mode: str) -> RepairResult:
        if mode == "baseline":
            return self.runner.run_baseline(task=task, repo_path=repo_path)
        if mode == "plan_and_code":
            return self.runner.run_plan_and_code(task=task, repo_path=repo_path)
        raise ValueError(f"Unsupported benchmark mode: {mode}")

    def _task_run_from_result(self, task_file: Path, result: RepairResult) -> BenchmarkTaskRun:
        test_passed = bool(result.test_execution.get("success", False))
        return BenchmarkTaskRun(
            task_id=result.task_id,
            task_file=str(task_file),
            repo_path=result.repo_path,
            success=result.success,
            retries=result.retries,
            test_passed=test_passed,
            result_dir=result.result_dir,
            result_json=result.output_file,
            final_summary=result.final_summary or result.summary,
            error=result.error,
        )

    def _build_metrics(self, task_runs: list[BenchmarkTaskRun]) -> BenchmarkMetrics:
        metrics = BenchmarkMetrics(total_tasks=len(task_runs))
        for task_run in task_runs:
            metrics.success_count += int(task_run.success)
            metrics.total_retries += task_run.retries
            metrics.test_pass_count += int(task_run.test_passed)
        return metrics

    def render_markdown(self, report: BenchmarkReport) -> str:
        metrics = report.metrics
        lines = [
            "# Benchmark Summary",
            "",
            f"- total_tasks: {metrics.total_tasks}",
            f"- success_count: {metrics.success_count}",
            f"- success_rate: {metrics.success_rate:.2%}",
            f"- avg_retries: {metrics.avg_retries:.2f}",
            f"- test_pass_rate: {metrics.test_pass_rate:.2%}",
            "",
            "## Task Results",
            "",
            "| task_id | success | retries | test_passed | task_file | result_json | error |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for task_run in report.task_runs:
            lines.append(
                "| "
                + " | ".join(
                    [
                        self._escape_table_value(task_run.task_id),
                        "yes" if task_run.success else "no",
                        str(task_run.retries),
                        "yes" if task_run.test_passed else "no",
                        self._escape_table_value(task_run.task_file),
                        self._escape_table_value(task_run.result_json),
                        self._escape_table_value(task_run.error),
                    ]
                )
                + " |"
            )
        return "\n".join(lines) + "\n"

    def _resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (self.config.project_root / path).resolve()

    def _resolve_repo_path(self, repo_path_value: str) -> Path:
        candidate = Path(repo_path_value).expanduser()
        if candidate.is_absolute():
            return candidate
        return (self.config.project_root / candidate).resolve()

    def _escape_table_value(self, value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ").strip()

    @contextmanager
    def _benchmark_log_scope(self) -> Any:
        logger_names = [
            "app.runner",
            "app.tools.test_tools",
            "openhands",
        ]
        previous_levels: dict[str, int] = {}
        try:
            for logger_name in logger_names:
                logger = logging.getLogger(logger_name)
                previous_levels[logger_name] = logger.level
                logger.setLevel(logging.WARNING)
            yield
        finally:
            for logger_name, previous_level in previous_levels.items():
                logging.getLogger(logger_name).setLevel(previous_level)
