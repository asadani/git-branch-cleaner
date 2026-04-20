# git-branch-cleaner

Interactive CLI to review and delete **stale GitHub branches** across one or more repositories. It uses the GitHub API (no local clone required), shows a terminal checklist with rich summaries, and applies several guardrails so protected branches and open PRs are hard to delete by mistake.

## Requirements

- Python 3.10+
- A [GitHub personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) with access to the repos you target and permission to delete branch refs (for non–dry-run runs)

## Install

```bash
cd /path/to/git-branch-cleaner
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

The `git-branch-cleaner` command is available on your PATH after install. You can also run:

```bash
python -m git_branch_cleaner
```

**Contributors** (formatting and lint):

```bash
pip install -e ".[dev]"
black .
flake8
```

Optional [pre-commit](https://pre-commit.com/) hooks (Black + Flake8, aligned via `pyproject.toml` and `.flake8`):

```bash
pre-commit install
```

## Configuration

### Token (`.env`)

Copy [`.env.example`](.env.example) to `.env` and set `GITHUB_TOKEN`, or export it in your shell.

The app loads **`GITHUB_TOKEN` from a `.env` file next to the installed package** (`src/git_branch_cleaner/` when developing from a clone), then from **`.env` in your current working directory** (which overrides on duplicate keys). You can also pass `--token`. Values are trimmed; avoid wrapping the token in quotes unless the whole value is consistently quoted.

Do not commit `.env`; it is listed in `.gitignore`.

### Repos (`repos.json`)

If you do **not** pass positional repos, `--repos-file`, or `--repos-json`, the tool looks for **`repos.json` in the current working directory** when the file exists.

Copy [`repos.json.example`](repos.json.example) to `repos.json` and edit. `repos.json` is gitignored so your list stays local.

Supported shapes:

- Object with a `repos` array
- Top-level JSON array

Each entry may be `owner/repo` or a full GitHub URL (`https://github.com/owner/repo`).

## Usage

```bash
# From a directory with .env and repos.json — dry run (no deletions)
git-branch-cleaner --dry-run

# Explicit repos on the command line
git-branch-cleaner myorg/repo1 myorg/repo2 --dry-run

# Plain-text list (one owner/repo per line; # starts a comment)
git-branch-cleaner --repos-file repos.txt --stale-days 60 --dry-run

# Explicit JSON path
git-branch-cleaner --repos-json /path/to/repos.json --stale-days 90 --dry-run

# Actually delete (after checklist, you must type the exact word yes)
git-branch-cleaner --stale-days 90
```

Run `git-branch-cleaner --help` for all options.

### Options

| Option | Description |
|--------|-------------|
| `REPOS...` | Optional `owner/repo` arguments (merged with other sources). |
| `--repos-file` | Text file: one repo per line; `#` comments ignored. |
| `--repos-json` | JSON file: `{"repos": [...]}` or `[...]`; URLs or `owner/repo`. |
| `--token` | PAT; defaults to `GITHUB_TOKEN` (e.g. from `.env`). |
| `--stale-days` | Branches at least this many days since last commit are **pre-selected** in the checklist (default: 90). You can still toggle any eligible branch. |
| `--dry-run` | Fetches data and shows what would be deleted; **exits before** the `yes` confirmation and performs **no** API deletions. |

## Project layout

| Path | Role |
|------|------|
| [`pyproject.toml`](pyproject.toml) | Project metadata, dependencies, Black settings |
| [`.flake8`](.flake8) | Flake8 (88 columns, Black-compatible ignores) |
| [`.pre-commit-config.yaml`](.pre-commit-config.yaml) | Optional pre-commit: Black + Flake8 |
| `src/git_branch_cleaner/cli.py` | CLI entrypoint and orchestration |
| `src/git_branch_cleaner/client.py` | GitHub API: list branches, open PRs, merge status, delete refs |
| `src/git_branch_cleaner/ui.py` | TUI: questionary checkbox, rich tables, confirmation prompt |

## Safety behavior

- **Default branch** is never listed (it is skipped when fetching branches).
- **Protected branches** and branches with an **open pull request** appear in the checklist as **disabled** (you cannot select them for deletion).
- **`--dry-run`** always stops after showing the deletion summary; there is no deletion confirmation step and nothing is deleted.
- **Confirmation** for real deletion requires typing the exact string **`yes`** (not `y`, not empty).
- **Per-branch errors** during deletion are logged; one failure does not abort the rest. A results table shows success and failure per branch.
