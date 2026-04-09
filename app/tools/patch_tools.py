from __future__ import annotations

from pathlib import Path
import subprocess


def _run_git_command(repo_path: Path, args: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False, "Git is not installed or not available in PATH."
    except OSError as exc:
        return False, f"Failed to run git command in {repo_path}: {exc}"
    #成功
    if completed.returncode == 0:
        return True, completed.stdout.strip()
    #失败，检查是否不是git仓库
    stderr = completed.stderr.strip()
    if "not a git repository" in stderr.lower() or "不是 git 仓库" in stderr.lower():
        return False, f"{repo_path} is not a git repository."

    return False, stderr or completed.stdout.strip() or "Git command failed."


def get_git_diff(repo_path: Path) -> str:
    """Return the current git diff for the repository, or a friendly error message."""
    success, output = _run_git_command(repo_path, ["diff", "--"])
    return output if success else f"Unable to get git diff: {output}"


def get_changed_files(repo_path: Path) -> list[str]:
    """Return changed file paths from git status, or a single friendly error message entry."""
    success, output = _run_git_command(repo_path, ["status", "--short"])
    if not success:
        return [f"ERROR: {output}"]
    if not output:
        return []

    changed_files: list[str] = []
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            changed_files.append(parts[1])
    return changed_files


def git_reset_hard_with_output(repo_path: Path) -> tuple[bool, str]:
    """Reset the repository to HEAD and return the git command result."""
    return _run_git_command(repo_path, ["reset", "--hard", "HEAD"])


def git_reset_hard(repo_path: Path) -> bool:
    """Reset the repository to HEAD and return whether the operation succeeded."""
    success, _ = git_reset_hard_with_output(repo_path)
    return success


def git_status(repo_path: Path) -> str:
    """Return `git status --short` output, or a friendly error message."""
    success, output = _run_git_command(repo_path, ["status", "--short"])
    if success:
        return output or "Working tree is clean."
    return f"Unable to get git status: {output}"
