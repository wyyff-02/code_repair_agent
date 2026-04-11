# Code Repair Agent

`code_repair_agent` is a local Python CLI project for automatically repairing code bugs through a multi-stage agent pipeline with context engineering.

The core loop is:

`issue -> patch -> test -> review`

The project uses a single-model, multi-agent workflow with four roles:

- `Planner`: turns the issue into a short repair plan
- `Coder`: searches the repo, edits code, and explains the repair
- `Tester`: runs the local test command through the Python tool layer and summarizes the result
- `Reviewer`: decides whether the current state should be `accept`, `retry`, or `fail`

## System Architecture

```mermaid
flowchart LR
    A[Task JSON] --> B[Planner]
    B --> C[Coder]
    C --> D[Tester]
    D --> E[Reviewer]
    E -->|accept / fail| F[data/results/]
    E -->|retry| G[git reset --hard]
    G --> B
```

Key modules:

- `app/main.py`: CLI entrypoint
- `app/runner.py`: workflow orchestration, retry loop, rollback, result persistence
- `app/context.py`: attempt context pack assembled from the previous failed attempt
- `app/heuristics.py`: heuristic memory — save successful fixes, retrieve via semantic search
- `app/agents/planner.py`: planner stage
- `app/agents/coder.py`: coder stage
- `app/agents/tester.py`: tester stage
- `app/agents/reviewer.py`: reviewer stage
- `app/tools/`: repo, test, and git helpers
- `app/eval/benchmark.py`: serial benchmark runner

## Context Engineering

Each repair attempt assembles a **context pack** before the coder stage runs, drawing from three sources:

### Attempt context (`app/context.py`)

On retry, the coder receives a structured summary of what went wrong in the previous attempt:

- failure reason from the reviewer
- failed test output
- list of files modified last time
- truncated git diff

This prevents the coder from repeating the same incorrect fix across retries.

### Heuristic memory (`app/heuristics.py`)

After each successful repair (`accept`), the system automatically:

1. Extracts the `Fix explanation:` section from the coder output
2. Calls the embedding API (`text-embedding-v3` via DashScope) to compute a vector for the fix
3. Appends the entry to `data/heuristics.json`

Before each coder stage, the system retrieves relevant past fixes using a **hybrid retrieval strategy**:

- **Semantic search**: embeds the current issue and ranks all stored entries by cosine similarity, returning the top 3
- **Recency**: always includes the 5 most recent entries
- Results are deduplicated and injected into the coder prompt as a `[Repair heuristics]` block

When the embedding API is unavailable, the system falls back to Jaccard keyword similarity automatically.

### Prompt structure

The assembled coder prompt follows this layout:

```
Planner output
[Repair heuristics from past successful fixes]   ← historical patterns
[Previous attempt N failed]                       ← retry context (empty on first attempt)
Your job / Rules / Response format
```

## Quick Start

Recommended environment:

- Python 3.12 for OpenHands-backed planner/coder/reviewer stages
- a configured `.env` file for model access when using `plan_and_code`

Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create the OpenHands runtime environment with Python 3.12:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Create your local environment file:

```bash
cp .env.example .env
```

Then edit `.env` and fill in the model-related settings you actually use:

- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_BASE_URL`
- `EMBEDDING_MODEL` — embedding model name for heuristic semantic search (e.g. `text-embedding-v3`)
- `OPENHANDS_API_KEY` and `OPENHANDS_BASE_URL` when needed in your setup

Optional sanity check:

```bash
.venv/bin/python -m app.main show-config
```

One command that can run immediately without model configuration:

```bash
.venv/bin/python -m app.main run --task-file data/tasks/buggy_high.json --run-tests-only
```

Full workflow with planner, coder, tester, reviewer, retry, and rollback:

```bash
.venv/bin/python -m app.main run --task-file data/tasks/buggy_low.json --mode plan_and_code
```

Useful CLI commands:

```bash
.venv/bin/python -m app.main show-config
.venv/bin/python -m app.main run --task-file data/tasks/buggy_low.json --mode demo
```

Main CLI commands:

- `show-config`: print resolved project paths and runtime config
- `run`: run one task in `demo`, `tests`, `agent`, `baseline`, or `plan_and_code` mode
- `benchmark`: run multiple tasks serially and write a summary report

Result persistence:

- Per-task results go to `data/results/{task_id}/`
- Heuristic memory goes to `data/heuristics.json` (auto-maintained)
- Benchmark summary goes to `data/results/benchmark_summary.md`

## Benchmark Results

Tested benchmark snapshot:

| metric | value |
| --- | --- |
| total_tasks | 8 |
| success_count | 6 |
| success_rate | 75.00% |
| avg_retries | 0.25 |
| test_pass_rate | 87.50% |

This is a local benchmark result for the current project state, intended as an engineering snapshot rather than a fixed guarantee.

## Task Format

Each task is a JSON file under `data/tasks/`.

Example:

```json
{
  "task_id": "repair-high-003",
  "repo_path": "repos/demo_repo",
  "issue_title": "Fix mutable default log state and thread-safe counter behavior",
  "issue_description": "In buggy_high.py, add_to_log should not share state across separate calls, and increment_counter should produce the expected final count when used from multiple threads.",
  "expected_test_command": "python3 buggy_high.py"
}
```

Fields:

- `task_id`: unique identifier for result storage
- `repo_path`: repository path, relative to the project root or absolute
- `issue_title`: short issue summary
- `issue_description`: detailed repair target
- `expected_test_command`: optional explicit test command; if omitted, the tester falls back to the repo-specific default behavior

## Benchmark

The project includes a serial benchmark runner for internal evaluation. The current tested result snapshot is shown in the Benchmark Results section above.

It records:

- `total_tasks`
- `success_count`
- `success_rate`
- `avg_retries`
- `test_pass_rate`

Current behavior:

- serial execution only
- failed tasks are still included in the final report
- report format is markdown
- each task still writes its own normal result directory while benchmark is running
- benchmark summary artifacts are written under `data/results/`

## Current Limits

- This is a local CLI project only. There is no frontend, database, or deployment layer.
- Planner, coder, and reviewer stages rely on OpenHands and an external model endpoint when running the full `plan_and_code` workflow.
- OpenHands-backed stages require Python 3.12+. Running the CLI with an older interpreter can make tests pass while agent stages still fail.
- Retry and rollback are bounded and simple: reviewer-driven retries are capped, and rollback uses `git reset --hard` inside the target repo.
- Result diff capture is currently less precise for untracked files. `changed_files` can reflect working-tree state rather than only the files edited in the current attempt.
- Benchmark runs are not isolated per task. Tasks share the same local repository state unless you reset or prepare the repo between runs.
- Heuristic retrieval accuracy depends on the quality of the embedding model and the coder output format. Entries without a `Fix explanation:` section fall back to the first two lines of the coder output.

## License

This repository is provided under the MIT License. See `LICENSE`.
