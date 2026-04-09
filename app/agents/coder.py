from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from app.agents.planner import PlanOutput
from app.schemas import BugTask


@dataclass
class CoderAgent:
    prompt_path: Path

    def load_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def build_prompt(
        self,
        task: BugTask,
        repo_path: Path,
        plan_output: PlanOutput,
    ) -> str:
        template = self.load_prompt()
        return template.format(
            workspace_root=repo_path,
            issue_title=task.issue_title,
            issue_description=task.issue_description,
            test_command=task.expected_test_command or "No explicit test command was provided.",
            planner_output=json.dumps(
                plan_output.to_dict(),
                indent=2,
                ensure_ascii=False,
            ),
        )
