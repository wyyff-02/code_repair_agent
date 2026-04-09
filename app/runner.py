from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import importlib
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any

from app.agents.coder import CoderAgent
from app.agents.planner import PlanOutput, PlannerAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.tester import TesterAgent
from app.config import AppConfig
from app.schemas import BugTask, RepairResult, ReviewerDecision, TestExecutionResult
from app.tools.patch_tools import (
    get_changed_files,
    get_git_diff,
    git_reset_hard_with_output,
    git_status,
)


class TaskLoadError(Exception):
    """Raised when a task file cannot be loaded or validated."""


LOGGER = logging.getLogger(__name__)


@dataclass
class OpenHandsRunResult:
    success: bool
    agent_output: str
    logs: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodeRepairRunner:
    """Runner for loading tasks and executing local OpenHands workflows."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.planner_agent = PlannerAgent(self.config.prompts_dir / "planner.txt")
        self.coder_agent = CoderAgent(self.config.prompts_dir / "coder.txt")
        self.tester_agent = TesterAgent(self.config.prompts_dir / "tester.txt")
        self.reviewer_agent = ReviewerAgent(self.config.prompts_dir / "reviewer.txt")

    def resolve_task_file(self, task_file: Path) -> Path:
        if task_file.is_absolute():
            return task_file
        return (self.config.project_root / task_file).resolve()

    def load_task(self, task_file: Path, repo_path_override: str | None = None) -> BugTask:
        resolved_task_file = self.resolve_task_file(task_file)

        if not resolved_task_file.exists():
            raise TaskLoadError(f"Task file not found: {resolved_task_file}")

        if resolved_task_file.suffix.lower() != ".json":
            raise TaskLoadError(
                f"Unsupported task file format: {resolved_task_file.name}. Expected a JSON file."
            )

        try:
            payload = json.loads(resolved_task_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TaskLoadError(
                f"Invalid JSON in task file {resolved_task_file}: {exc}"
            ) from exc
        except OSError as exc:
            raise TaskLoadError(
                f"Failed to read task file {resolved_task_file}: {exc}"
            ) from exc

        if repo_path_override:
            payload["repo_path"] = repo_path_override

        return self._build_bug_task(payload, resolved_task_file)

    def _build_bug_task(self, payload: dict[str, Any], task_file: Path) -> BugTask:
        required_fields = {
            "task_id": str,
            "repo_path": str,
            "issue_title": str,
            "issue_description": str,
        }

        missing_fields = [field_name for field_name in required_fields if field_name not in payload]
        if missing_fields:
            joined = ", ".join(missing_fields)
            raise TaskLoadError(
                f"Task file validation failed for {task_file}: missing fields: {joined}"
            )

        invalid_fields: list[str] = []
        for field_name, expected_type in required_fields.items():
            if not isinstance(payload[field_name], expected_type):
                invalid_fields.append(field_name)

        if invalid_fields:
            joined = ", ".join(invalid_fields)
            raise TaskLoadError(
                f"Task file validation failed for {task_file}: these fields must be strings: {joined}"
            )

        expected_test_command = payload.get("expected_test_command")
        if expected_test_command is not None and not isinstance(expected_test_command, str):
            raise TaskLoadError(
                f"Task file validation failed for {task_file}: expected_test_command must be a string when provided."
            )

        return BugTask(
            task_id=payload["task_id"],
            repo_path=payload["repo_path"],
            issue_title=payload["issue_title"],
            issue_description=payload["issue_description"],
            expected_test_command=expected_test_command,
        )

    def format_task_payload(self, task: BugTask) -> dict[str, object]:
        return task.to_dict()

    def build_placeholder_result(self, task: BugTask) -> RepairResult:
        return RepairResult(
            task_id=task.task_id,
            repo_path=task.repo_path,
            success=False,
            retries=0,
            changed_files=[],
            test_output="Task loaded successfully. Test execution is not implemented yet.",
            summary="Task input parsed successfully. Agent repair flow is not implemented yet.",
            final_summary="Task input parsed successfully. Agent repair flow is not implemented yet.",
            max_retry_attempts=self.config.max_retry_attempts,
            attempt_count=0,
        )

    def run_baseline(self, task: BugTask, repo_path: Path) -> RepairResult:
        """Backward-compatible alias for the two-stage plan-and-code flow."""
        return self.run_plan_and_code(task=task, repo_path=repo_path)

    def run_plan_and_code(self, task: BugTask, repo_path: Path) -> RepairResult:
        """Run planner, coder, tester, and reviewer stages with bounded retries."""
        self.config.ensure_directories()
        output_dir = self.config.results_dir / task.task_id
        max_retry_attempts = self.config.max_retry_attempts
        total_allowed_attempts = max_retry_attempts + 1
        attempts: list[dict[str, Any]] = []
        logs: list[str] = []
        structured_logs: list[dict[str, Any]] = []
        started_at = self._utc_now_iso()

        self._record_event(
            structured_logs,
            logs,
            "task_started",
            (
                f"Starting planner-coder-tester-reviewer execution for task {task.task_id} "
                f"with up to {max_retry_attempts} retries."
            ),
            task_id=task.task_id,
            repo_path=str(repo_path),
            max_retry_attempts=max_retry_attempts,
        )

        for retry_index in range(total_allowed_attempts):
            self._record_event(
                structured_logs,
                logs,
                "attempt_started",
                f"Attempt {retry_index + 1}/{total_allowed_attempts} for task {task.task_id} started.",
                retry_index=retry_index,
                total_attempts=total_allowed_attempts,
            )
            attempt = self._run_repair_attempt(
                task=task,
                repo_path=repo_path,
                retry_index=retry_index,
            )
            attempts.append(attempt)
            logs.extend(attempt["logs"])

            self._record_event(
                structured_logs,
                logs,
                "attempt_finished",
                (
                    f"Attempt {retry_index + 1}/{total_allowed_attempts} reviewer decision: "
                    f"{attempt['reviewer_output'].get('decision', 'retry')}"
                ),
                retry_index=retry_index,
                decision=attempt["reviewer_output"].get("decision", "retry"),
                changed_files=list(attempt.get("changed_files", [])),
            )
            decision = attempt["reviewer_output"].get("decision", "retry")
            if decision != "retry":
                result = self._build_repair_result(
                    task=task,
                    output_dir=output_dir,
                    attempts=attempts,
                    logs=logs,
                    structured_logs=structured_logs,
                    max_retry_attempts=max_retry_attempts,
                    started_at=started_at,
                    completed_at=self._utc_now_iso(),
                )
                self.save_repair_result(result)
                return result

            if retry_index >= max_retry_attempts:
                self._record_event(
                    structured_logs,
                    logs,
                    "max_retries_reached",
                    (
                        f"Max retries reached for task {task.task_id}; "
                        f"stopping after attempt {retry_index + 1}."
                    ),
                    level="warning",
                    retry_index=retry_index,
                    total_attempts=total_allowed_attempts,
                )
                final_result = self._build_repair_result(
                    task=task,
                    output_dir=output_dir,
                    attempts=attempts,
                    logs=logs,
                    structured_logs=structured_logs,
                    max_retry_attempts=max_retry_attempts,
                    started_at=started_at,
                    completed_at=self._utc_now_iso(),
                )
                self.save_repair_result(final_result)
                return final_result

            self._record_retry_rollback(
                repo_path=repo_path,
                attempt=attempt,
                logs=logs,
                structured_logs=structured_logs,
                next_retry_index=retry_index + 1,
            )
            retry_result = self._build_repair_result(
                task=task,
                output_dir=output_dir,
                attempts=attempts,
                logs=logs,
                structured_logs=structured_logs,
                max_retry_attempts=max_retry_attempts,
                started_at=started_at,
                completed_at="",
            )
            self.save_repair_result(retry_result)

        final_result = self._build_repair_result(
            task=task,
            output_dir=output_dir,
            attempts=attempts,
            logs=logs,
            structured_logs=structured_logs,
            max_retry_attempts=max_retry_attempts,
            started_at=started_at,
            completed_at=self._utc_now_iso(),
        )
        self.save_repair_result(final_result)
        return final_result

    def save_repair_result(self, result: RepairResult) -> Path:
        """Persist a repair result and companion artifacts under data/results/{task_id}/."""
        self.config.ensure_directories()
        result_dir = (
            Path(result.result_dir)
            if result.result_dir
            else self.config.results_dir / result.task_id
        )
        result_dir.mkdir(parents=True, exist_ok=True)
        output_path = result_dir / "result.json"
        result.result_dir = str(result_dir)
        result.output_file = str(output_path)
        result.final_summary = result.final_summary or result.summary
        result.timestamps = {
            **result.timestamps,
            "saved_at": self._utc_now_iso(),
        }
        output_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (result_dir / "final_diff.patch").write_text(
            result.git_diff,
            encoding="utf-8",
        )
        (result_dir / "test_output.txt").write_text(
            self._render_test_output_artifact(result),
            encoding="utf-8",
        )
        (result_dir / "planner_output.txt").write_text(
            self._render_attempt_stage_artifact(
                attempts=result.attempts,
                field_name="planner_output",
                render_item=self._render_planner_output,
                fallback=result.planner_output,
            ),
            encoding="utf-8",
        )
        (result_dir / "coder_output.txt").write_text(
            self._render_attempt_stage_artifact(
                attempts=result.attempts,
                field_name="coder_output",
                render_item=self._render_text_output,
                fallback=result.coder_output or result.agent_output,
            ),
            encoding="utf-8",
        )
        (result_dir / "reviewer_output.txt").write_text(
            self._render_attempt_stage_artifact(
                attempts=result.attempts,
                field_name="reviewer_output",
                render_item=self._render_json_text,
                fallback=result.reviewer_output,
            ),
            encoding="utf-8",
        )
        LOGGER.info(
            json.dumps(
                {
                    "event": "result_saved",
                    "task_id": result.task_id,
                    "result_dir": str(result_dir),
                    "result_json": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return output_path

    def run_openhands_baseline(self, task: BugTask, repo_path: Path) -> OpenHandsRunResult:
        """Run the legacy single-stage baseline prompt against the local workspace."""
        try:
            prompt = self._build_single_stage_baseline_prompt(task=task, repo_path=repo_path)
        except OSError as exc:
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=["[baseline] Failed to load baseline prompt template."],
                error=str(exc),
            )
        return self.run_openhands_stage(
            prompt=prompt,
            repo_path=repo_path,
            stage_name="baseline",
        )

    def run_openhands_stage(
        self,
        prompt: str,
        repo_path: Path,
        stage_name: str,
    ) -> OpenHandsRunResult:
        """Run a single OpenHands stage against the local repository workspace."""
        logs: list[str] = []

        def log(message: str) -> None:
            logs.append(f"[{stage_name}] {message}")

        log("Starting OpenHands stage.")
        log(f"Resolved local workspace: {repo_path}")

        if not repo_path.exists():
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=logs,
                error=f"Workspace path does not exist: {repo_path}",
            )

        if not repo_path.is_dir():
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=logs,
                error=f"Workspace path is not a directory: {repo_path}",
            )

        if sys.version_info < (3, 12):
            log("OpenHands SDK requires Python 3.12+.")
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=logs,
                error=(
                    f"Current Python version is {sys.version_info.major}."
                    f"{sys.version_info.minor}. OpenHands SDK requires Python 3.12+."
                ),
            )

        try:
            sdk_module = importlib.import_module("openhands.sdk")
            terminal_module = importlib.import_module("openhands.tools.terminal")
            file_editor_module = importlib.import_module("openhands.tools.file_editor")
            task_tracker_module = importlib.import_module("openhands.tools.task_tracker")
            log("Imported OpenHands SDK modules successfully.")
        except ImportError as exc:
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=logs,
                error=(
                    "OpenHands SDK is not installed. Install `openhands-sdk` and "
                    f"`openhands-tools` first. Import error: {exc}"
                ),
            )

        api_key = os.getenv("LLM_API_KEY") or self.config.openhands_api_key
        model_name = os.getenv("LLM_MODEL") or self.config.default_model
        base_url = os.getenv("LLM_BASE_URL") or self.config.openhands_base_url

        if not api_key:
            log("Missing LLM API key.")
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=logs,
                error=(
                    "Missing LLM API key. Set `LLM_API_KEY` or `OPENHANDS_API_KEY` before "
                    "running the OpenHands workflow."
                ),
            )

        LLM = getattr(sdk_module, "LLM")
        Agent = getattr(sdk_module, "Agent")
        Conversation = getattr(sdk_module, "Conversation")
        Tool = getattr(sdk_module, "Tool")

        TerminalTool = getattr(terminal_module, "TerminalTool")
        FileEditorTool = getattr(file_editor_module, "FileEditorTool")
        TaskTrackerTool = getattr(task_tracker_module, "TaskTrackerTool")

        conversation = None
        try:
            llm_kwargs: dict[str, Any] = {"api_key": api_key}
            if model_name:
                llm_kwargs["model"] = model_name
            if base_url:
                llm_kwargs["base_url"] = base_url

            llm = LLM(**llm_kwargs)
            log(
                "Configured LLM for OpenHands stage."
                + (f" Model: {model_name}" if model_name else " Model: SDK default")
            )

            agent = Agent(
                llm=llm,
                tools=[
                    Tool(name=TerminalTool.name),
                    Tool(name=FileEditorTool.name),
                    Tool(name=TaskTrackerTool.name),
                ],
            )
            log("Created OpenHands agent with terminal, file editor, and task tracker tools.")

            conversation = Conversation(agent=agent, workspace=str(repo_path))
            log("Created local OpenHands conversation using the repository workspace.")

            conversation.send_message(prompt)
            log("Sent stage prompt to the agent.")

            conversation.run()
            log("OpenHands conversation finished running.")

            agent_output = self._extract_agent_output(conversation)
            log("Collected final text output from the agent.")

            return OpenHandsRunResult(
                success=True,
                agent_output=agent_output,
                logs=logs,
                error=None,
            )
        except Exception as exc:
            log(f"OpenHands stage failed with exception: {exc}")
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=logs,
                error=str(exc),
            )
        finally:
            if conversation is not None:
                try:
                    conversation.close()
                    log("Closed OpenHands conversation.")
                except Exception as exc:
                    log(f"Failed to close conversation cleanly: {exc}")

    def _run_planner_stage(
        self,
        task: BugTask,
        repo_path: Path,
    ) -> tuple[PlanOutput, OpenHandsRunResult]:
        try:
            planner_prompt = self.planner_agent.build_prompt(
                issue_title=task.issue_title,
                issue_description=task.issue_description,
            )
        except OSError as exc:
            result = OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=["[planner] Failed to load planner prompt template."],
                error=str(exc),
            )
            return self.planner_agent.fallback_plan(task.issue_title), result

        planner_result = self.run_openhands_stage(
            prompt=planner_prompt,
            repo_path=repo_path,
            stage_name="planner",
        )
        if planner_result.success:
            return self.planner_agent.parse_output(planner_result.agent_output), planner_result

        fallback_plan = self.planner_agent.fallback_plan(task.issue_title)
        planner_result.logs.append("[planner] Planner stage failed; using fallback plan output.")
        return fallback_plan, planner_result

    def _run_coder_stage(
        self,
        task: BugTask,
        repo_path: Path,
        planner_output: PlanOutput,
    ) -> OpenHandsRunResult:
        try:
            coder_prompt = self.coder_agent.build_prompt(
                task=task,
                repo_path=repo_path,
                plan_output=planner_output,
            )
        except OSError as exc:
            return OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=["[coder] Failed to load coder prompt template."],
                error=str(exc),
            )

        return self.run_openhands_stage(
            prompt=coder_prompt,
            repo_path=repo_path,
            stage_name="coder",
        )

    def _run_tester_stage(
        self,
        task: BugTask,
        repo_path: Path,
    ) -> TestExecutionResult:
        """Run the local tester stage and return a structured test result."""
        return self.tester_agent.run(
            task=task,
            repo_path=repo_path,
        )

    def _run_reviewer_stage(
        self,
        task: BugTask,
        repo_path: Path,
        planner_output: PlanOutput,
        coder_result: OpenHandsRunResult,
        test_execution: TestExecutionResult,
        planner_result: OpenHandsRunResult,
    ) -> tuple[ReviewerDecision, OpenHandsRunResult]:
        try:
            reviewer_prompt = self.reviewer_agent.build_prompt(
                task=task,
                planner_output=planner_output,
                coder_output=coder_result.agent_output,
                test_execution=test_execution,
            )
        except OSError as exc:
            result = OpenHandsRunResult(
                success=False,
                agent_output="",
                logs=["[reviewer] Failed to load reviewer prompt template."],
                error=str(exc),
            )
            fallback = self.reviewer_agent.fallback_review(
                planner_success=planner_result.success,
                coder_success=coder_result.success,
                test_execution=test_execution,
            )
            return fallback, result

        reviewer_result = self.run_openhands_stage(
            prompt=reviewer_prompt,
            repo_path=repo_path,
            stage_name="reviewer",
        )
        if reviewer_result.success:
            return self.reviewer_agent.parse_output(reviewer_result.agent_output), reviewer_result

        fallback = self.reviewer_agent.fallback_review(
            planner_success=planner_result.success,
            coder_success=coder_result.success,
            test_execution=test_execution,
        )
        reviewer_result.logs.append("[reviewer] Reviewer stage failed; using fallback review output.")
        return fallback, reviewer_result

    def _build_single_stage_baseline_prompt(self, task: BugTask, repo_path: Path) -> str:
        """Build the legacy baseline prompt from the template file."""
        prompt_path = self.config.prompts_dir / "baseline.txt"
        template = prompt_path.read_text(encoding="utf-8")
        return template.format(
            workspace_root=repo_path,
            issue_title=task.issue_title,
            issue_description=task.issue_description,
            test_command=task.expected_test_command or "No explicit test command was provided.",
        )

    def _extract_agent_output(self, conversation: Any) -> str:
        """Get a final text response from the conversation using public APIs when possible."""
        for attribute_name in ("state", "conversation_state"):
            state = getattr(conversation, attribute_name, None)
            if state is None:
                continue
            event_output = self._extract_output_from_events(getattr(state, "events", None))
            if event_output:
                return event_output
            messages = getattr(state, "messages", None)
            message_output = self._extract_output_from_messages(messages)
            if message_output:
                return message_output

        try:
            response = conversation.ask_agent(
                "Summarize your final result as plain text with Diagnosis, Relevant files, "
                "and Suggested next step."
            )
            if isinstance(response, str) and response.strip():
                return response.strip()
        except Exception:
            pass

        return "OpenHands run completed, but no final text output could be extracted."

    def _extract_output_from_events(self, events: Any) -> str:
        """Return the latest assistant text from OpenHands events when available."""
        if not events:
            return ""

        for event in reversed(list(events)):
            llm_message = getattr(event, "llm_message", None)
            if llm_message is None:
                continue
            if getattr(llm_message, "role", None) != "assistant":
                continue
            text = self._extract_text_content(getattr(llm_message, "content", None))
            if text:
                return text

        return ""

    def _extract_output_from_messages(self, messages: Any) -> str:
        """Return the latest assistant text from a message list."""
        if not messages:
            return ""

        for message in reversed(list(messages)):
            role = getattr(message, "role", None)
            if role not in (None, "assistant"):
                continue
            text = self._extract_text_content(getattr(message, "content", None))
            if text:
                return text

        return ""

    def _extract_text_content(self, content: Any) -> str:
        """Normalize string-like message content from SDK objects or plain dicts."""
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
                continue
            if isinstance(item, dict):
                item_text = item.get("text")
                if isinstance(item_text, str) and item_text.strip():
                    parts.append(item_text.strip())

        return "\n".join(parts).strip()

    def _combine_test_output(self, test_result: TestExecutionResult) -> str:
        """Combine stdout and stderr into one text block for storage."""
        stdout = test_result.stdout.strip()
        stderr = test_result.stderr.strip()
        parts = [part for part in (stdout, stderr) if part]
        return "\n\n".join(parts)

    def _combine_test_output_dict(self, test_execution: dict[str, Any]) -> str:
        """Combine stdout and stderr from a serialized test execution dictionary."""
        stdout = str(test_execution.get("stdout", "")).strip()
        stderr = str(test_execution.get("stderr", "")).strip()
        parts = [part for part in (stdout, stderr) if part]
        return "\n\n".join(parts)

    def _build_reviewer_summary(
        self,
        reviewer_output: ReviewerDecision,
        changed_files: list[str],
    ) -> str:
        """Create a final summary based on reviewer output."""
        decision = reviewer_output.decision
        if decision == "accept":
            return (
                f"{reviewer_output.rationale} Changed files: {len(changed_files)}. "
                f"Next action: {reviewer_output.next_action}"
            )
        return f"{reviewer_output.rationale} Next action: {reviewer_output.next_action}"

    def _combine_stage_errors(
        self,
        planner_error: str | None,
        coder_error: str | None,
        reviewer_error: str | None,
    ) -> str:
        """Merge planner, coder, and reviewer stage errors into a single string."""
        errors = []
        if planner_error:
            errors.append(f"planner: {planner_error}")
        if coder_error:
            errors.append(f"coder: {coder_error}")
        if reviewer_error:
            errors.append(f"reviewer: {reviewer_error}")
        return " | ".join(errors)

    def _utc_now_iso(self) -> str:
        """Return the current UTC timestamp in ISO-8601 format."""
        return datetime.now(timezone.utc).isoformat()

    def _record_log(self, logs: list[str], message: str, level: str = "info") -> None:
        """Append a human-readable log message and emit it to the terminal logger."""
        logs.append(message)
        log_method = getattr(LOGGER, level, LOGGER.info)
        log_method(message)

    def _record_event(
        self,
        structured_logs: list[dict[str, Any]],
        logs: list[str],
        event: str,
        message: str,
        level: str = "info",
        **fields: Any,
    ) -> None:
        """Record a structured log event and a paired human-readable message."""
        event_payload = {
            "timestamp": self._utc_now_iso(),
            "event": event,
            **fields,
        }
        structured_logs.append(event_payload)
        self._record_log(logs, message, level=level)
        log_method = getattr(LOGGER, level, LOGGER.info)
        log_method(json.dumps(event_payload, ensure_ascii=False))

    def _run_repair_attempt(
        self,
        task: BugTask,
        repo_path: Path,
        retry_index: int,
    ) -> dict[str, Any]:
        """Run one full repair attempt and return the serialized attempt record."""
        attempt_logs = [
            f"[attempt {retry_index}] Starting planner-coder-tester-reviewer stages."
        ]
        planner_output, planner_result = self._run_planner_stage(task=task, repo_path=repo_path)
        attempt_logs.extend(planner_result.logs)

        coder_result = self._run_coder_stage(
            task=task,
            repo_path=repo_path,
            planner_output=planner_output,
        )
        attempt_logs.extend(coder_result.logs)

        attempt_logs.append("Collecting repository state after coder execution.")
        status_after = git_status(repo_path)
        changed_files = get_changed_files(repo_path)
        git_diff = get_git_diff(repo_path)

        attempt_logs.append("Running tester stage after coder execution.")
        test_execution = self._run_tester_stage(task=task, repo_path=repo_path)
        attempt_logs.append(
            "Tester stage finished with return code "
            f"{test_execution.returncode}."
        )
        if test_execution.executed_command:
            attempt_logs.append(
                "Tester executed command "
                f"({test_execution.execution_mode}): {test_execution.executed_command}"
            )
        if test_execution.notes:
            attempt_logs.append(f"Tester notes: {test_execution.notes}")
        attempt_logs.append(f"Git status after coder:\n{status_after}")
        if test_execution.failed_summary:
            attempt_logs.append(f"Tester failed summary:\n{test_execution.failed_summary}")

        attempt_logs.append("Running reviewer stage after tester execution.")
        reviewer_output, reviewer_result = self._run_reviewer_stage(
            task=task,
            repo_path=repo_path,
            planner_output=planner_output,
            coder_result=coder_result,
            test_execution=test_execution,
            planner_result=planner_result,
        )
        attempt_logs.extend(reviewer_result.logs)
        attempt_logs.append(f"Reviewer decision: {reviewer_output.decision}")

        return {
            "retry_index": retry_index,
            "planner_output": planner_output.to_dict(),
            "coder_output": coder_result.agent_output,
            "test_execution": test_execution.to_dict(),
            "reviewer_output": reviewer_output.to_dict(),
            "changed_files": changed_files,
            "git_diff": git_diff,
            "git_status": status_after,
            "logs": attempt_logs,
            "planner_error": planner_result.error or "",
            "coder_error": coder_result.error or "",
            "reviewer_error": reviewer_result.error or "",
            "pre_retry_git_diff": "",
            "rollback_attempted": False,
            "rollback_success": False,
            "rollback_error": "",
            "rollback_output": "",
        }

    def _record_retry_rollback(
        self,
        repo_path: Path,
        attempt: dict[str, Any],
        logs: list[str],
        structured_logs: list[dict[str, Any]],
        next_retry_index: int,
    ) -> None:
        """Record the pre-retry diff and attempt a git rollback before the next retry."""
        attempt["pre_retry_git_diff"] = get_git_diff(repo_path)
        attempt["rollback_attempted"] = True
        self._record_event(
            structured_logs,
            logs,
            "retry_requested",
            (
                f"Retry {next_retry_index} requested; recorded current git diff "
                "and attempting git reset --hard."
            ),
            retry_index=next_retry_index,
        )
        rollback_success, rollback_output = git_reset_hard_with_output(repo_path)
        attempt["rollback_success"] = rollback_success
        attempt["rollback_output"] = rollback_output
        if rollback_success:
            self._record_event(
                structured_logs,
                logs,
                "rollback_succeeded",
                f"Rollback before retry {next_retry_index} completed successfully.",
                retry_index=next_retry_index,
            )
            return

        attempt["rollback_error"] = rollback_output or "git reset --hard failed."
        self._record_event(
            structured_logs,
            logs,
            "rollback_failed",
            (
                f"Rollback before retry {next_retry_index} failed: "
                f"{attempt['rollback_error']}"
            ),
            level="warning",
            retry_index=next_retry_index,
            error=attempt["rollback_error"],
        )

    def _build_repair_result(
        self,
        task: BugTask,
        output_dir: Path,
        attempts: list[dict[str, Any]],
        logs: list[str],
        structured_logs: list[dict[str, Any]],
        max_retry_attempts: int,
        started_at: str,
        completed_at: str,
    ) -> RepairResult:
        """Build the latest repair result snapshot from the attempt history."""
        if not attempts:
            result = self.build_placeholder_result(task)
            result.result_dir = str(output_dir)
            result.output_file = str(output_dir / "result.json")
            result.logs = logs
            result.structured_logs = structured_logs
            result.max_retry_attempts = max_retry_attempts
            result.attempt_count = 0
            result.timestamps = {
                "started_at": started_at,
                "completed_at": completed_at,
            }
            return result

        latest_attempt = attempts[-1]
        latest_test_execution = latest_attempt.get("test_execution", {})
        latest_reviewer = self._reviewer_decision_from_dict(
            latest_attempt.get("reviewer_output", {})
        )
        final_summary = self._build_reviewer_summary(
            reviewer_output=latest_reviewer,
            changed_files=list(latest_attempt.get("changed_files", [])),
        )

        return RepairResult(
            task_id=task.task_id,
            repo_path=task.repo_path,
            success=latest_reviewer.decision == "accept",
            retries=max(0, len(attempts) - 1),
            test_output=self._combine_test_output_dict(latest_test_execution),
            summary=final_summary,
            final_summary=final_summary,
            timestamps={
                "started_at": started_at,
                "completed_at": completed_at,
            },
            structured_logs=[dict(log) for log in structured_logs],
            attempts=[dict(attempt) for attempt in attempts],
            planner_output=dict(latest_attempt.get("planner_output", {})),
            coder_output=str(latest_attempt.get("coder_output", "")),
            test_execution=dict(latest_test_execution),
            reviewer_output=latest_reviewer.to_dict(),
            agent_output=str(latest_attempt.get("coder_output", "")),
            stdout=str(latest_test_execution.get("stdout", "")),
            stderr=str(latest_test_execution.get("stderr", "")),
            git_diff=str(latest_attempt.get("git_diff", "")),
            logs=list(logs),
            changed_files=list(latest_attempt.get("changed_files", [])),
            max_retry_attempts=max_retry_attempts,
            attempt_count=len(attempts),
            result_dir=str(output_dir),
            output_file=str(output_dir / "result.json"),
            error=self._collect_attempt_errors(attempts),
        )

    def _collect_attempt_errors(self, attempts: list[dict[str, Any]]) -> str:
        """Collect stage and rollback errors across all attempts."""
        errors: list[str] = []
        for attempt in attempts:
            retry_index = int(attempt.get("retry_index", 0))
            for field_name, label in (
                ("planner_error", "planner"),
                ("coder_error", "coder"),
                ("reviewer_error", "reviewer"),
            ):
                value = str(attempt.get(field_name, "")).strip()
                if value:
                    errors.append(f"attempt {retry_index} {label}: {value}")

            rollback_error = str(attempt.get("rollback_error", "")).strip()
            if rollback_error:
                errors.append(f"attempt {retry_index} rollback: {rollback_error}")

        return " | ".join(errors)

    def _reviewer_decision_from_dict(self, payload: dict[str, Any]) -> ReviewerDecision:
        """Normalize a serialized reviewer payload into a ReviewerDecision."""
        decision = str(payload.get("decision", "retry")).strip().lower()
        if decision not in {"accept", "retry", "fail"}:
            decision = "retry"
        rationale = str(payload.get("rationale", "")).strip()
        next_action = str(payload.get("next_action", "")).strip()
        return ReviewerDecision(
            decision=decision,
            rationale=rationale or "Reviewer did not provide a rationale.",
            next_action=next_action or "Inspect the latest logs before the next action.",
        )

    def _render_planner_output(self, payload: dict[str, Any]) -> str:
        """Render planner output to a text artifact, preferring raw_output when available."""
        raw_output = str(payload.get("raw_output", "")).strip()
        if raw_output:
            return raw_output
        return self._render_json_text(payload)

    def _render_text_output(self, payload: Any) -> str:
        """Render a free-form text payload for artifact storage."""
        return str(payload or "").strip()

    def _render_json_text(self, payload: Any) -> str:
        """Render arbitrary JSON-serializable content as pretty text."""
        if not payload:
            return ""
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def _render_attempt_stage_artifact(
        self,
        attempts: list[dict[str, Any]],
        field_name: str,
        render_item: Any,
        fallback: Any,
    ) -> str:
        """Render one stage artifact across all attempts, falling back to the final payload."""
        if not attempts:
            return render_item(fallback)

        sections: list[str] = []
        for attempt_index, attempt in enumerate(attempts):
            retry_index = int(attempt.get("retry_index", attempt_index))
            payload = attempt.get(field_name)
            rendered = render_item(payload)
            sections.append(
                self._format_attempt_section(
                    retry_index=retry_index,
                    content=rendered,
                )
            )
        return "\n\n".join(section for section in sections if section).strip()

    def _render_test_output_artifact(self, result: RepairResult) -> str:
        """Render tester output across attempts, or fall back to the final combined output."""
        if not result.attempts:
            return result.test_output

        sections: list[str] = []
        for attempt_index, attempt in enumerate(result.attempts):
            retry_index = int(attempt.get("retry_index", attempt_index))
            test_execution = attempt.get("test_execution", {})
            rendered = self._render_test_execution_text(test_execution)
            sections.append(
                self._format_attempt_section(
                    retry_index=retry_index,
                    content=rendered,
                )
            )
        return "\n\n".join(section for section in sections if section).strip()

    def _render_test_execution_text(self, payload: dict[str, Any]) -> str:
        """Render a serialized test execution payload into a readable text block."""
        if not payload:
            return ""

        lines = [
            f"success: {payload.get('success', False)}",
            f"returncode: {payload.get('returncode', '')}",
        ]
        executed_command = str(payload.get("executed_command", "")).strip()
        if executed_command:
            lines.append(f"command: {executed_command}")
        execution_mode = str(payload.get("execution_mode", "")).strip()
        if execution_mode:
            lines.append(f"mode: {execution_mode}")
        notes = str(payload.get("notes", "")).strip()
        if notes:
            lines.append(f"notes: {notes}")
        failed_summary = str(payload.get("failed_summary", "")).strip()
        if failed_summary:
            lines.append(f"failed_summary: {failed_summary}")

        combined_output = self._combine_test_output_dict(payload)
        if combined_output:
            lines.append("output:")
            lines.append(combined_output)
        return "\n".join(lines).strip()

    def _format_attempt_section(self, retry_index: int, content: str) -> str:
        """Format a single attempt section for a text artifact."""
        header = f"=== Attempt {retry_index + 1} (retry_index={retry_index}) ==="
        body = content.strip()
        if not body:
            body = "(no output)"
        return f"{header}\n{body}"
