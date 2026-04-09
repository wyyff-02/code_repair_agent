from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from app.agents.planner import PlanOutput
from app.schemas import BugTask, ReviewerDecision, TestExecutionResult


@dataclass
class ReviewerAgent:
    prompt_path: Path

    def load_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def build_prompt(
        self,
        task: BugTask,
        planner_output: PlanOutput,
        coder_output: str,
        test_execution: TestExecutionResult,
    ) -> str:
        template = self.load_prompt()
        return template.format(
            issue_title=task.issue_title,
            issue_description=task.issue_description,
            planner_output=json.dumps(
                planner_output.to_dict(),
                indent=2,
                ensure_ascii=False,
            ),
            coder_output=coder_output,
            tester_output=json.dumps(
                test_execution.to_dict(),
                indent=2,
                ensure_ascii=False,
            ),
        )

    def parse_output(self, output_text: str) -> ReviewerDecision:
        data = self._extract_json(output_text)
        decision = str(data.get("decision", "retry")).strip().lower()
        if decision not in {"accept", "retry", "fail"}:
            decision = "retry"
        rationale = str(data.get("rationale", "")).strip()
        next_action = str(data.get("next_action", "")).strip()
        return ReviewerDecision(
            decision=decision,
            rationale=rationale or output_text.strip() or "Reviewer did not provide a rationale.",
            next_action=next_action or "Inspect the latest logs and repository diff before the next attempt.",
        )

    def fallback_review(
        self,
        planner_success: bool,
        coder_success: bool,
        test_execution: TestExecutionResult,
    ) -> ReviewerDecision:
        if planner_success and coder_success and test_execution.success:
            if test_execution.execution_mode in {"python_compile", "no_test_command"}:
                return ReviewerDecision(
                    decision="retry",
                    rationale=(
                        "The implementation stages completed, but only lightweight validation was available. "
                        "A stronger task-specific test is still needed before accepting the fix."
                    ),
                    next_action=test_execution.notes or "Add a focused validation command or test case for this bug.",
                )
            return ReviewerDecision(
                decision="accept",
                rationale="Planner, coder, and tester stages completed successfully.",
                next_action="No further action is required.",
            )
        if not test_execution.success:
            return ReviewerDecision(
                decision="retry",
                rationale=(
                    "The latest test execution failed. Review the failed test summary and "
                    "the modified files before another attempt."
                ),
                next_action=test_execution.failed_summary or "Re-run the failing test with more focused debugging.",
            )
        return ReviewerDecision(
            decision="fail",
            rationale="One or more implementation stages did not complete successfully.",
            next_action="Inspect planner/coder errors and retry with a narrower fix scope.",
        )

    def _extract_json(self, output_text: str) -> dict[str, Any]:
        candidates = [output_text.strip()]
        fenced_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", output_text, re.DOTALL)
        candidates.extend(fenced_matches)

        start = output_text.find("{")
        end = output_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(output_text[start : end + 1])

        for candidate in candidates:
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        return {}
