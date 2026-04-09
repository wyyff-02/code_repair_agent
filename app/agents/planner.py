from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
from typing import Any


@dataclass
class PlanOutput:
    possible_related_modules: list[str] = field(default_factory=list)
    file_types_to_check: list[str] = field(default_factory=list)
    suggested_steps: list[str] = field(default_factory=list)
    test_strategy: list[str] = field(default_factory=list)
    raw_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlannerAgent:
    prompt_path: Path

    def load_prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    def build_prompt(self, issue_title: str, issue_description: str) -> str:
        template = self.load_prompt()
        return template.format(
            issue_title=issue_title,
            issue_description=issue_description,
        )

    def parse_output(self, output_text: str) -> PlanOutput:
        data = self._extract_json(output_text)
        return PlanOutput(
            possible_related_modules=self._to_string_list(
                data.get("possible_related_modules", [])
            ),
            file_types_to_check=self._to_string_list(
                data.get("file_types_to_check", [])
            ),
            suggested_steps=self._to_string_list(
                data.get("suggested_steps", [])
            ),
            test_strategy=self._to_string_list(
                data.get("test_strategy", [])
            ),
            raw_output=output_text.strip(),
        )

    def fallback_plan(self, issue_title: str) -> PlanOutput: #失败时的兜底方案
        return PlanOutput(
            possible_related_modules=[issue_title],
            file_types_to_check=[".py", ".json", ".yaml"],
            suggested_steps=[
                "Search the repository for symbols and tests related to the issue title.",
                "Inspect the most relevant implementation files.",
                "Apply the smallest safe fix.",
                "Run the provided test command and confirm the result.",
            ],
            test_strategy=[
                "Run the task's expected test command.",
                "Review changed files and git diff after the fix.",
            ],
            raw_output="",
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

    def _to_string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return []
