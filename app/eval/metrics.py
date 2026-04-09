from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunMetrics:
    total_runs: int = 0
    successful_runs: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.successful_runs / self.total_runs


@dataclass
class BenchmarkMetrics:
    total_tasks: int = 0
    success_count: int = 0
    total_retries: int = 0
    test_pass_count: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.success_count / self.total_tasks

    @property
    def avg_retries(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.total_retries / self.total_tasks

    @property
    def test_pass_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.test_pass_count / self.total_tasks
