from __future__ import annotations

from pathlib import Path


SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
}

TEXT_FILE_EXTENSIONS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".html",
    ".css",
    ".scss",
    ".sh",
    ".sql",
    ".xml",
}


def _should_skip_directory(path: Path) -> bool:
    return path.name in SKIP_DIR_NAMES


def _is_probably_text_file(file_path: Path) -> bool:
    if file_path.suffix.lower() in TEXT_FILE_EXTENSIONS:
        return True

    try:
        sample = file_path.open('rb').read(1024)#rb: read binary mode, 1024: read the first 1024 bytes of the file
    except OSError:
        return False

    return b"\x00" not in sample


def _iter_repo_files(repo_path: Path) -> list[Path]:
    try:
        if not repo_path.exists():
            raise FileNotFoundError(f"Repository path does not exist: {repo_path}")
        if not repo_path.is_dir():
            raise NotADirectoryError(f"Repository path is not a directory: {repo_path}")
    except OSError as exc:
        raise OSError(f"Failed to inspect repository path {repo_path}: {exc}") from exc

    collected: list[Path] = []
    for path in repo_path.rglob("*"):
        if path.is_dir() and _should_skip_directory(path):
            continue

        relative_parts = path.relative_to(repo_path).parts
        if any(part in SKIP_DIR_NAMES for part in relative_parts):
            continue

        if path.is_file() and _is_probably_text_file(path):
            collected.append(path)

    collected.sort()#保证结果的健壮性，不排序不同系统可能返回不同的顺序造成不可知错误
    return collected


def list_repo_files(repo_path: Path, max_files: int = 500) -> list[str]:
    """Return up to `max_files` text file paths relative to the repository root."""
    try:
        files = _iter_repo_files(repo_path)
    except OSError:
        return []

    limited_files = files[:max_files]
    return [str(path.relative_to(repo_path)) for path in limited_files]


def read_text_file(file_path: Path) -> str:
    """Read a UTF-8 text file and return its content."""
    try:
        if not file_path.exists():
            raise FileNotFoundError(f"File does not exist: {file_path}")
        if not file_path.is_file():
            raise IsADirectoryError(f"Path is not a file: {file_path}")
        if not _is_probably_text_file(file_path):
            raise ValueError(f"Refusing to read a non-text file: {file_path}")
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Failed to decode file as UTF-8 text: {file_path}") from exc
    except OSError as exc:
        raise OSError(f"Failed to read file {file_path}: {exc}") from exc


def write_text_file(file_path: Path, content: str) -> None:
    """Write UTF-8 text content to a file, creating parent directories if needed."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise OSError(f"Failed to write file {file_path}: {exc}") from exc


def find_keyword_in_repo(
    repo_path: Path,
    keyword: str,
    suffixes: list[str] | None = None,
) -> list[dict]:
    """Search text files in a repository and return matches(符合关键字要求和后缀的内容) with path, line number, and text."""
    normalized_keyword = keyword.strip()
    if not normalized_keyword:
        return []

    normalized_suffixes = {suffix.lower() for suffix in suffixes or []}
    matches: list[dict] = []

    try:
        files = _iter_repo_files(repo_path)
    except OSError:
        return matches

    for file_path in files:
        if normalized_suffixes and file_path.suffix.lower() not in normalized_suffixes:
            continue

        try:
            content = read_text_file(file_path)
        except (OSError, ValueError):
            continue

        for line_number, line in enumerate(content.splitlines(), start=1):
            if normalized_keyword.lower() in line.lower():
                matches.append(
                    {
                        "file_path": str(file_path.relative_to(repo_path)),
                        "line_number": line_number,
                        "line_text": line.strip(),
                    }
                )

    return matches


def get_python_files(repo_path: Path) -> list[str]:
    """Return all Python source files relative to the repository root."""
    try:
        files = _iter_repo_files(repo_path)
    except OSError:
        return []

    return [
        str(file_path.relative_to(repo_path))
        for file_path in files
        if file_path.suffix.lower() == ".py"
    ]
