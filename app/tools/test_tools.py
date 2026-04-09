from __future__ import annotations

import logging
from pathlib import Path
import subprocess


LOGGER = logging.getLogger(__name__)


def run_command(cmd: str, cwd: Path, timeout: int = 120) -> dict:
    """Run a shell command and return a stable result dictionary."""
    LOGGER.info("Running command: %s (cwd=%s, timeout=%ss)", cmd, cwd, timeout)

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "success": completed.returncode == 0,
        }
        LOGGER.info("Command finished with return code %s", completed.returncode)
        return result
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        LOGGER.warning("Command timed out after %ss: %s", timeout, cmd)
        return {
            "returncode": -1,
            "stdout": stdout,
            "stderr": stderr or f"Command timed out after {timeout} seconds.",
            "success": False,
        }
    except OSError as exc:
        LOGGER.exception("OS error while running command: %s", cmd)
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
            "success": False,
        }
    except Exception as exc:  # pragma: no cover - defensive guard
        LOGGER.exception("Unexpected error while running command: %s", cmd)
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
            "success": False,
        }


def run_tests(test_command: str, cwd: Path, timeout: int = 300) -> dict:
    """Run the provided test command and return the captured test result."""
    LOGGER.info("Starting test run with command: %s", test_command)
    result = run_command(cmd=test_command, cwd=cwd, timeout=timeout)
    LOGGER.info("Test run success=%s returncode=%s", result["success"], result["returncode"])
    return result
