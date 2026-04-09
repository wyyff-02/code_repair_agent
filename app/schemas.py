from __future__ import annotations

from typing import Any

from dataclasses import asdict, dataclass, field


@dataclass
class BugTask:
    task_id: str
    repo_path: str
    issue_title: str
    issue_description: str
    expected_test_command: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class TestExecutionResult:
    success: bool
    returncode: int
    stdout: str
    stderr: str
    failed_summary: str
    executed_command: str = ""
    execution_mode: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ReviewerDecision:
    decision: str
    rationale: str
    next_action: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RepairResult:
    task_id: str
    success: bool
    test_output: str
    summary: str
    repo_path: str = ""
    retries: int = 0
    final_summary: str = ""
    timestamps: dict[str, str] = field(default_factory=dict)
    structured_logs: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    planner_output: dict[str, Any] = field(default_factory=dict)
    coder_output: str = ""
    test_execution: dict[str, Any] = field(default_factory=dict)
    reviewer_output: dict[str, Any] = field(default_factory=dict)
    agent_output: str = ""
    stdout: str = ""
    stderr: str = ""
    git_diff: str = ""
    logs: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    max_retry_attempts: int = 0
    attempt_count: int = 0
    result_dir: str = ""
    output_file: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
