from __future__ import annotations

import unittest
from pathlib import Path

from app.config import AppConfig
from app.runner import CodeRepairRunner


class DemoTaskFixtureTests(unittest.TestCase):
    def test_demo_task_json_is_loadable(self) -> None:
        config = AppConfig.load()
        runner = CodeRepairRunner(config)

        task = runner.load_task(Path("data/tasks/demo_buggy_high_task.json"))

        self.assertEqual(task.task_id, "demo-buggy-high-004")
        self.assertEqual(task.repo_path, "repos/demo_repo")
        self.assertEqual(task.expected_test_command, "python3 buggy_high.py")

    def test_sample_task_uses_explicit_calculator_command(self) -> None:
        config = AppConfig.load()
        runner = CodeRepairRunner(config)

        task = runner.load_task(Path("data/tasks/sample_bug_task.json"))

        self.assertEqual(task.task_id, "demo-001")
        self.assertEqual(task.repo_path, "repos/demo_repo")
        self.assertEqual(
            task.expected_test_command,
            (
                'python3 -c "from calculator import add; '
                "assert add(1, 2) == 3; "
                "assert add(-1, 1) == 0; "
                "assert add(0, 0) == 0; "
                "print('calculator ok')\""
            ),
        )

    def test_retry_and_fail_benchmark_tasks_are_loadable(self) -> None:
        config = AppConfig.load()
        runner = CodeRepairRunner(config)

        retry_task = runner.load_task(Path("data/tasks/retry_weak_validation.json"))
        fail_task = runner.load_task(Path("data/tasks/fail_blocked_missing_file.json"))

        self.assertEqual(retry_task.task_id, "retry-weak-validation-001")
        self.assertEqual(retry_task.repo_path, "repos/demo_repo")
        self.assertIsNone(retry_task.expected_test_command)
        self.assertIn("process_user_data", retry_task.issue_description)

        self.assertEqual(fail_task.task_id, "fail-blocked-missing-file-001")
        self.assertEqual(fail_task.repo_path, "repos/demo_repo")
        self.assertIsNone(fail_task.expected_test_command)
        self.assertIn("billing_gateway.py", fail_task.issue_description)


if __name__ == "__main__":
    unittest.main()
