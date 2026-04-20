#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.progress import Progress, SpinnerColumn, TextColumn

from git_branch_cleaner.client import (
    BranchInfo,
    RepoNotFoundError,
    check_merged_status,
    create_github_client,
    delete_branch,
    fetch_branches,
    get_open_pr_branches,
    get_repo,
    validate_token,
)
from git_branch_cleaner.ui import (
    confirm_deletion,
    make_console,
    print_deletion_results,
    print_deletion_summary,
    print_fetch_summary,
    run_checklist,
)

REPO_PATTERN = re.compile(r"^[\w.\-]+/[\w.\-]+$")
GITHUB_REPO_URL = re.compile(
    r"^https?://github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)

# Load .env from the package directory, then cwd (cwd overrides on duplicate keys).
_PACKAGE_DIR = Path(__file__).resolve().parent
load_dotenv(_PACKAGE_DIR / ".env")
load_dotenv(override=True)


def normalize_repo_ref(raw: str) -> str:
    s = raw.strip()
    if not s:
        raise ValueError("empty repository entry")
    if REPO_PATTERN.match(s):
        return s
    m = GITHUB_REPO_URL.match(s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    raise ValueError(f"expected 'owner/repo' or github.com URL, got: {raw!r}")


def load_repos_from_json(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise click.UsageError(f"Invalid JSON in {path}: {e}") from e

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "repos" in data:
        items = data["repos"]
    else:
        raise click.UsageError(
            f"{path} must be a JSON array of repos or an object with a 'repos' array."
        )

    if not isinstance(items, list):
        raise click.UsageError(f"{path}: 'repos' must be an array.")

    out: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, str):
            raise click.UsageError(f"{path}: entry {i} must be a string.")
        try:
            out.append(normalize_repo_ref(item))
        except ValueError as e:
            raise click.UsageError(f"{path}: {e}") from e
    return out


def resolve_repos(
    repos: tuple[str, ...],
    repos_file: str | None,
    repos_json: str | None,
) -> list[str]:
    names: list[str] = []
    for r in repos:
        try:
            names.append(normalize_repo_ref(r))
        except ValueError as e:
            raise click.UsageError(str(e)) from e

    if repos_file:
        text = Path(repos_file).read_text()
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    names.append(normalize_repo_ref(line))
                except ValueError as e:
                    raise click.UsageError(str(e)) from e

    if repos_json:
        json_path = Path(repos_json).resolve()
        if not json_path.is_file():
            raise click.UsageError(f"Repos JSON file not found: {json_path}")
        names.extend(load_repos_from_json(json_path))
    elif not names and not repos_file:
        default_json = Path.cwd() / "repos.json"
        if default_json.is_file():
            names.extend(load_repos_from_json(default_json))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)

    if not unique:
        raise click.UsageError(
            "No repositories specified. Use positional args, --repos-file, "
            "--repos-json, or a repos.json file in the current directory."
        )

    invalid = [r for r in unique if not REPO_PATTERN.match(r)]
    if invalid:
        raise click.UsageError(
            f"Invalid repository format (expected 'owner/repo'): {', '.join(invalid)}"
        )

    return unique


@click.command()
@click.argument("repos", nargs=-1)
@click.option(
    "--repos-file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Text file with one org/repo per line. Lines starting with # are ignored.",
)
@click.option(
    "--repos-json",
    type=click.Path(exists=False, dir_okay=False),
    default=None,
    help=(
        'JSON file: {"repos": [...]} or a JSON array. '
        "Values may be owner/repo or github.com URLs."
    ),
)
@click.option(
    "--token",
    envvar="GITHUB_TOKEN",
    required=True,
    help="GitHub Personal Access Token. Defaults to GITHUB_TOKEN env var.",
)
@click.option(
    "--stale-days",
    default=90,
    show_default=True,
    help="Branches with no commits in this many days are pre-selected for deletion.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be deleted without actually deleting anything.",
)
def main(
    repos: tuple[str, ...],
    repos_file: str | None,
    repos_json: str | None,
    token: str,
    stale_days: int,
    dry_run: bool,
) -> None:
    """Clean up stale GitHub branches interactively.

    GITHUB_TOKEN is read from the environment (e.g. from a .env file in the
    working directory).

    Repos: positional arguments, --repos-file (plain text), --repos-json, or
    repos.json in the current directory when nothing else is given.
    """
    console = make_console()

    if dry_run:
        console.print(
            "[bold yellow]DRY RUN mode — no branches will be deleted.[/bold yellow]\n"
        )

    # Step 1: Resolve and validate repo list
    try:
        repo_names = resolve_repos(repos, repos_file, repos_json)
    except click.UsageError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    # Step 2: Authenticate
    try:
        client = create_github_client(token)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    validate_token(client, console)

    # Step 3: Fetch branch data for all repos
    all_branches_by_repo: dict[str, list[BranchInfo]] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Fetching repos...", total=len(repo_names))

        for repo_name in repo_names:
            progress.update(task, description=f"Fetching {repo_name}...")
            try:
                repo = get_repo(client, repo_name)
            except RepoNotFoundError as e:
                console.print(f"[yellow]Warning: {e} — skipping.[/yellow]")
                progress.advance(task)
                continue
            except RuntimeError as e:
                console.print(f"[yellow]Warning: {e} — skipping.[/yellow]")
                progress.advance(task)
                continue

            open_prs = get_open_pr_branches(repo)
            branches = fetch_branches(repo, open_prs, console)

            progress.update(
                task, description=f"Checking merge status for {repo_name}..."
            )
            check_merged_status(repo, branches, console)

            if branches:
                all_branches_by_repo[repo_name] = branches

            progress.advance(task)

    if not all_branches_by_repo:
        console.print("[yellow]No branches found across all specified repos.[/yellow]")
        sys.exit(0)

    # Step 4: Show summary table
    print_fetch_summary(all_branches_by_repo, stale_days, console)

    # Step 5: Interactive checklist
    selected = run_checklist(all_branches_by_repo, stale_days)

    if not selected:
        console.print("No branches selected. Exiting.")
        sys.exit(0)

    # Step 6: Show deletion summary and confirm
    print_deletion_summary(selected, dry_run, console)

    if dry_run:
        console.print(
            "\n[bold yellow]Dry run complete. No branches were deleted.[/bold yellow]"
        )
        sys.exit(0)

    if not confirm_deletion(dry_run=False):
        console.print("Aborted.")
        sys.exit(0)

    # Step 7: Delete selected branches
    results: list[tuple[BranchInfo, bool]] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Deleting branches...", total=len(selected))
        for branch in selected:
            progress.update(
                task, description=f"Deleting {branch.repo_full_name}:{branch.name}..."
            )
            try:
                repo = get_repo(client, branch.repo_full_name)
                success = delete_branch(
                    repo, branch.name, dry_run=False, console=console
                )
            except Exception as e:
                console.print(f"[red]Error deleting {branch.name}: {e}[/red]")
                success = False
            results.append((branch, success))
            progress.advance(task)

    print_deletion_results(results, console)


if __name__ == "__main__":
    main()
