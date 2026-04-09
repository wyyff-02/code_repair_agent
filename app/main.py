from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.eval.benchmark import BenchmarkReport, BenchmarkRunner
from app.runner import CodeRepairRunner, TaskLoadError
from app.tools.patch_tools import get_changed_files, git_status
from app.tools.repo_tools import find_keyword_in_repo, list_repo_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-repair-agent",
        description=(
            "CLI for running the local code repair workflow and serial benchmark tasks."
        ),
        epilog=build_cli_examples(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional path to a .env file.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "show-config",
        help="Print resolved configuration and project paths.",
    )

    add_benchmark_subparser(subparsers)
    add_run_subparser(subparsers)

    return parser


def add_benchmark_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Run multiple task JSON files in serial and write a summary report.",
        description="Run a serial benchmark over task JSON files and aggregate metrics.",
    )
    task_source_group = benchmark_parser.add_mutually_exclusive_group()
    task_source_group.add_argument(
        "--tasks-dir",
        type=Path,
        default=None,
        help="Optional tasks directory override. Defaults to data/tasks.",
    )
    task_source_group.add_argument(
        "--task-files",
        type=Path,
        nargs="*",
        default=None,
        help="Optional explicit task JSON files to benchmark.",
    )
    benchmark_parser.add_argument(
        "--mode",
        choices=("baseline", "plan_and_code"),
        default="plan_and_code",
        help="Execution mode for each benchmark task.",
    )

def add_run_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run_parser = subparsers.add_parser(
        "run",
        help="Run one task through the local repair workflow.",
        description="Run a single task file through demo, test-only, agent, or repair modes.",
    )
    run_parser.add_argument(
        "--task-file",
        type=Path,
        required=True,
        help="Path to a task JSON file, usually under data/tasks/.",
    )
    run_parser.add_argument(
        "--repo-path",
        type=str,
        default=None,
        help="Optional repository path override for the loaded task.",
    )
    run_parser.add_argument(
        "--mode",
        choices=("demo", "tests", "agent", "baseline", "plan_and_code"),
        default="demo",
        help="Execution mode for the task runner.",
    )
    shortcut_group = run_parser.add_mutually_exclusive_group()
    shortcut_group.add_argument(
        "--run-tests-only",
        action="store_true",
        help="Shortcut for --mode tests.",
    )
    shortcut_group.add_argument(
        "--run-agent",
        action="store_true",
        help="Shortcut for --mode agent.",
    )

def build_cli_examples() -> str:
    return "\n".join(
        [
            "Examples:",
            "  python -m app.main show-config",
            "  python -m app.main run --task-file data/tasks/sample_bug_task.json --mode plan_and_code",
            "  python -m app.main run --task-file data/tasks/demo_buggy_high_task.json --run-tests-only",
            "  python -m app.main benchmark --tasks-dir data/tasks --mode plan_and_code",
        ]
    )


def config_to_dict(config: AppConfig) -> dict[str, Any]:
    return {
        "project_name": config.project_name,
        "log_level": config.log_level,
        "max_retry_attempts": config.max_retry_attempts,
        "project_root": str(config.project_root),
        "data_dir": str(config.data_dir),
        "tasks_dir": str(config.tasks_dir),
        "results_dir": str(config.results_dir),
        "repos_dir": str(config.repos_dir),
        "prompts_dir": str(config.prompts_dir),
        "openhands_base_url": config.openhands_base_url,
        "default_model": config.default_model,
    }


def extract_search_candidates(issue_title: str) -> list[str]:
    """Extract simple candidate keywords from the issue title for demo search."""
    stop_words = {
        "a",
        "an",
        "the",
        "fix",
        "bug",
        "issue",
        "error",
        "for",
        "in",
        "on",
        "of",
        "to",
        "and",
        "with",
        "when",
        "wrong",
        "result",
    }
    # Filter out common low-signal words and keep candidates useful for code search.
    candidates = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", issue_title.lower())
    filtered = [candidate for candidate in candidates if candidate not in stop_words]
    return filtered or candidates


def resolve_repo_path(project_root: Path, repo_path_value: str) -> Path:
    """Resolve repo paths relative to the project root when needed."""
    candidate = Path(repo_path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()


def setup_logging(log_level: str) -> None:
    """Configure application logging once at startup."""
    resolved_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=resolved_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def summarize_test_result(test_result: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary of the test execution result."""
    stdout = str(test_result.get("stdout", ""))
    stderr = str(test_result.get("stderr", ""))
    return {
        "returncode": test_result.get("returncode"),
        "success": test_result.get("success", False),
        "stdout_preview": stdout[:1000],
        "stderr_preview": stderr[:1000],
    }


def build_repair_summary(result: Any, repo_path: Path) -> dict[str, Any]:
    """Build a compact final summary for CLI output."""
    return {
        "task_id": result.task_id,
        "repo_path": result.repo_path or str(repo_path),
        "success": result.success,
        "retries": result.retries,
        "changed_files": result.changed_files,
        "result_dir": result.result_dir,
        "result_json": result.output_file,
        "final_summary": result.final_summary or result.summary,
    }


def build_benchmark_summary(report: BenchmarkReport) -> dict[str, Any]:
    """Build a compact benchmark summary for CLI output."""
    return {
        "total_tasks": report.metrics.total_tasks,
        "success_count": report.metrics.success_count,
        "success_rate": report.metrics.success_rate,
        "avg_retries": report.metrics.avg_retries,
        "test_pass_rate": report.metrics.test_pass_rate,
        "report_path": str(report.report_path),
        "task_run_count": len(report.task_runs),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        config = AppConfig.load(env_file=args.env_file)
        config.ensure_directories()
        setup_logging(config.log_level)

        if args.command == "show-config":
            print(json.dumps(config_to_dict(config), indent=2, ensure_ascii=False))
            return

        if args.command == "benchmark":
            benchmark_runner = BenchmarkRunner(config=config)
            report = benchmark_runner.run(
                task_files=args.task_files,
                tasks_dir=args.tasks_dir,
                mode=args.mode,
            )
            print(
                json.dumps(
                    build_benchmark_summary(report),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        if args.command == "run":
            runner = CodeRepairRunner(config)
            task = runner.load_task(
                task_file=args.task_file,
                repo_path_override=args.repo_path,
            )
            repo_path = resolve_repo_path(config.project_root, task.repo_path)

            selected_mode = args.mode
            if args.run_tests_only:
                selected_mode = "tests"
            elif args.run_agent:
                selected_mode = "agent"

            if selected_mode == "tests":
                git_status_before = git_status(repo_path)
                test_execution = runner.tester_agent.run(
                    task=task,
                    repo_path=repo_path,
                )
                git_status_after = git_status(repo_path)
                changed_files = get_changed_files(repo_path)
                print(
                    json.dumps(
                        {
                            "task_id": task.task_id,
                            "repo_path": str(repo_path),
                            "test_command": test_execution.executed_command,
                            "test_mode": test_execution.execution_mode,
                            "test_notes": test_execution.notes,
                            "git_status_before": git_status_before,
                            "git_status_after": git_status_after,
                            "changed_files": changed_files,
                            "test_result": summarize_test_result(test_execution.to_dict()),
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return

            if selected_mode == "agent":
                agent_result = runner.run_openhands_baseline(
                    task=task,
                    repo_path=repo_path,
                )
                print(
                    json.dumps(
                        {
                            "task_id": task.task_id,
                            "repo_path": str(repo_path),
                            "issue_title": task.issue_title,
                            "openhands_result": agent_result.to_dict(),
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return

            if selected_mode in {"baseline", "plan_and_code"}:
                result = runner.run_plan_and_code(
                    task=task,
                    repo_path=repo_path,
                )
                print(
                    json.dumps(
                        build_repair_summary(result=result, repo_path=repo_path),
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return

            repo_files = list_repo_files(repo_path, max_files=20)
            search_candidates = extract_search_candidates(task.issue_title)
            search_keyword = ""
            search_results: list[dict[str, Any]] = []
            for candidate in search_candidates:
                matches = find_keyword_in_repo(
                    repo_path=repo_path,
                    keyword=candidate,
                    suffixes=[".py"],
                )
                if matches:
                    search_keyword = candidate
                    search_results = matches
                    break

            if not search_keyword and search_candidates:
                search_keyword = search_candidates[0]
                search_results = find_keyword_in_repo(
                    repo_path=repo_path,
                    keyword=search_keyword,
                    suffixes=[".py"],
                )
            result = runner.build_placeholder_result(task)

            print(
                json.dumps(
                    {
                        "task": runner.format_task_payload(task),
                        "repo_demo": {
                            "resolved_repo_path": str(repo_path),
                            "listed_files": repo_files,
                            "search_keyword": search_keyword,
                            "search_results": search_results[:10],
                        },
                        "result": result.to_dict(),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return

        parser.error(f"Unsupported command: {args.command}")
    except TaskLoadError as exc:
        print(f"Task loading error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
