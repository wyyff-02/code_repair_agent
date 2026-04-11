from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AttemptContext:
    """Context pack assembled from the previous failed attempt."""

    previous_failure_reason: str
    failed_test_summary: str
    previous_diff_summary: str
    previous_changed_files: list[str]
    attempt_index: int

    def is_first_attempt(self) -> bool:
        return self.attempt_index == 0

    def to_prompt_block(self) -> str:
        """Render as a compact text block for prompt injection.

        Returns an empty string on the first attempt so callers can
        insert it unconditionally without changing prompt structure.
        """
        if self.is_first_attempt():
            return ""
        lines = [
            f"[Previous attempt {self.attempt_index} failed]",
            f"Failure reason: {self.previous_failure_reason}",
        ]
        if self.failed_test_summary:
            lines.append(f"Failed test output:\n{self.failed_test_summary}")
        if self.previous_changed_files:
            files = ", ".join(self.previous_changed_files[:5])
            lines.append(f"Files modified last time: {files}")
        if self.previous_diff_summary:
            lines.append(f"Last diff (truncated):\n{self.previous_diff_summary[:800]}")
        return "\n".join(lines)


def build_attempt_context(
    attempt_index: int,
    previous_attempts: list[dict[str, Any]],
) -> AttemptContext:
    """Build context from the most recent completed attempt, if any."""
    if not previous_attempts:
        return AttemptContext(
            previous_failure_reason="",
            failed_test_summary="",
            previous_diff_summary="",
            previous_changed_files=[],
            attempt_index=0,
        )
    last = previous_attempts[-1]
    reviewer = last.get("reviewer_output", {})
    test_exec = last.get("test_execution", {})
    return AttemptContext(
        previous_failure_reason=str(reviewer.get("rationale", "")).strip(),
        failed_test_summary=str(test_exec.get("failed_summary", "")).strip(),
        previous_diff_summary=str(last.get("git_diff", "")).strip(),
        previous_changed_files=list(last.get("changed_files", [])),
        attempt_index=attempt_index,
    )
