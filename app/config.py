from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal environments
    load_dotenv = None


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_from_root(project_root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (project_root / candidate).resolve()


def _parse_non_negative_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


@dataclass
class AppConfig:
    project_name: str
    log_level: str
    max_retry_attempts: int
    project_root: Path
    data_dir: Path
    tasks_dir: Path
    results_dir: Path
    repos_dir: Path
    prompts_dir: Path
    openhands_base_url: str | None
    openhands_api_key: str | None
    default_model: str | None

    @classmethod
    def load(cls, env_file: Path | None = None) -> "AppConfig":
        project_root = _project_root()
        resolved_env = env_file or project_root / ".env"

        if resolved_env.exists() and load_dotenv is not None:
            load_dotenv(resolved_env)

        project_name = os.getenv("PROJECT_NAME", "code_repair_agent")
        log_level = os.getenv("LOG_LEVEL", "INFO")
        max_retry_attempts = _parse_non_negative_int(
            os.getenv("MAX_RETRY_ATTEMPTS"),
            default=2,
        )

        data_dir = _resolve_from_root(project_root, os.getenv("DATA_DIR", "data"))
        tasks_dir = _resolve_from_root(project_root, os.getenv("TASKS_DIR", "data/tasks"))
        results_dir = _resolve_from_root(project_root, os.getenv("RESULTS_DIR", "data/results"))
        repos_dir = _resolve_from_root(project_root, os.getenv("REPOS_DIR", "repos"))
        prompts_dir = _resolve_from_root(project_root, os.getenv("PROMPTS_DIR", "app/prompts"))

        return cls(
            project_name=project_name,
            log_level=log_level,
            max_retry_attempts=max_retry_attempts,
            project_root=project_root,
            data_dir=data_dir,
            tasks_dir=tasks_dir,
            results_dir=results_dir,
            repos_dir=repos_dir,
            prompts_dir=prompts_dir,
            openhands_base_url=os.getenv("OPENHANDS_BASE_URL") or None,
            openhands_api_key=os.getenv("OPENHANDS_API_KEY") or None,
            default_model=os.getenv("DEFAULT_MODEL") or None,
        )

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.tasks_dir,
            self.results_dir,
            self.repos_dir,
            self.prompts_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
