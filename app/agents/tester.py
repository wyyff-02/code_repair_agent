from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex

from app.schemas import BugTask, TestExecutionResult
from app.tools.test_tools import run_tests


@dataclass
class TesterAgent:
    prompt_path: Path

    def load_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def run(self, task: BugTask, repo_path: Path, timeout: int = 300) -> TestExecutionResult:
        resolved_command, execution_mode, notes = self._resolve_test_command(
            task=task,
            repo_path=repo_path,
        )
        if not resolved_command:
            return TestExecutionResult(
                success=False,
                returncode=-1,
                stdout="",
                stderr="No explicit test command or fallback validation command could be determined.",
                failed_summary="No test or validation command was available for this task.",
                executed_command="",
                execution_mode="no_test_command",
                notes="Tester could not infer a safe validation strategy.",
            )

        result = run_tests(
            test_command=resolved_command,
            cwd=repo_path,
            timeout=timeout,
        )
        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))
        success = bool(result.get("success", False))
        return TestExecutionResult(
            success=success,
            returncode=int(result.get("returncode", -1)),
            stdout=stdout,
            stderr=stderr,
            failed_summary="" if success else self._extract_failed_summary(stdout=stdout, stderr=stderr),
            executed_command=resolved_command,
            execution_mode=execution_mode,
            notes=notes,
        )

    def build_reviewer_handoff(
        self,
        test_command: str,
        execution_result: TestExecutionResult,
    ) -> str:
        template = self.load_prompt()
        return template.format(
            test_command=execution_result.executed_command or test_command,
            success=execution_result.success,
            returncode=execution_result.returncode,
            failed_summary=execution_result.failed_summary or "No failure summary. Tests passed or no failures were detected.",
        )

    def _resolve_test_command(
        self,
        task: BugTask,
        repo_path: Path,
    ) -> tuple[str, str, str]:
        explicit_command = (task.expected_test_command or "").strip()
        if explicit_command:
            return (
                explicit_command,
                "explicit_command",
                "Used the task-provided test command.",
            )

        referenced_python_files = self._extract_referenced_python_files(task, repo_path)
        if referenced_python_files:
            target = referenced_python_files[0]
            return (
                f"python3 {shlex.quote(str(target.name))}",
                "python_file_smoke",
                "No explicit test command was provided; ran the referenced Python file as a smoke test.",
            )

        discovered_test_files = sorted(repo_path.glob("test_*.py"))
        if discovered_test_files:
            return (
                "python3 -m unittest -q",
                "unittest_discovery",
                "No explicit test command was provided; used unittest discovery because test files were found.",
            )

        python_files = sorted(path for path in repo_path.glob("*.py") if path.is_file())
        if python_files:
            quoted_files = " ".join(shlex.quote(str(path.name)) for path in python_files)
            return (
                f"python3 -m py_compile {quoted_files}",
                "python_compile",
                "No explicit test command or test files were found; ran Python syntax validation instead.",
            )

        return "", "no_test_command", "No validation strategy was available."

    def _extract_referenced_python_files(self, task: BugTask, repo_path: Path) -> list[Path]:
        combined_text = f"{task.issue_title}\n{task.issue_description}"
        matches = re.findall(r"\b[\w./-]+\.py\b", combined_text)
        resolved_paths: list[Path] = []
        for match in matches:
            candidate = (repo_path / match).resolve()
            if candidate.exists() and candidate.is_file():
                resolved_paths.append(candidate)
                continue

            repo_local = repo_path / Path(match).name
            if repo_local.exists() and repo_local.is_file():
                resolved_paths.append(repo_local.resolve())

        return resolved_paths

    def _extract_failed_summary(self, stdout: str, stderr: str) -> str:
        combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part).strip()
        if not combined:
            return ""

        failure_patterns = [
            r"FAILED\s+\([^)]+\)",
            r"AssertionError:.*",
            r"E\s+.*",
            r"FAIL:.*",
            r"ERROR:.*",
            r"Traceback \(most recent call last\):",
        ]
        summary_lines: list[str] = []
        for line in combined.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(re.search(pattern, stripped) for pattern in failure_patterns):
                summary_lines.append(stripped)

        if summary_lines:
            return "\n".join(summary_lines[:10])

        lines = [line.strip() for line in combined.splitlines() if line.strip()]
        return "\n".join(lines[-10:])
