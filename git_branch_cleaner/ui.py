from __future__ import annotations

import sys

import questionary
from rich.console import Console
from rich.table import Table

from git_branch_cleaner.client import BranchInfo


def make_console() -> Console:
    return Console(stderr=True)


def _tags(branch: BranchInfo) -> str:
    tags = []
    if branch.is_merged:
        tags.append("[merged]")
    if branch.has_open_pr:
        tags.append("[open PR]")
    if branch.is_protected:
        tags.append("[protected]")
    return "  ".join(tags)


def format_branch_label(branch: BranchInfo) -> str:
    name = branch.name[:42].ljust(42)
    age = f"{branch.days_old}d ago".rjust(9)
    author = branch.last_commit_author[:20].ljust(20)
    tags = _tags(branch)
    return f"{name}  {age}  {author}  {tags}"


def _disabled_reason(branch: BranchInfo) -> str | None:
    if branch.is_protected:
        return "protected"
    if branch.has_open_pr:
        return "open PR"
    return None


def build_repo_choices(
    repo_full_name: str,
    branches: list[BranchInfo],
    stale_days: int,
) -> list:
    choices: list = [
        questionary.Separator(
            f"── {repo_full_name} ({len(branches)} branches) " + "─" * 20
        )
    ]
    for branch in branches:
        disabled = _disabled_reason(branch)
        checked = not disabled and branch.days_old >= stale_days
        choices.append(
            questionary.Choice(
                title=format_branch_label(branch),
                value=branch,
                checked=checked,
                disabled=disabled,
            )
        )
    return choices


def run_checklist(
    all_branches_by_repo: dict[str, list[BranchInfo]],
    stale_days: int,
) -> list[BranchInfo]:
    choices: list = []
    for repo_name, branches in all_branches_by_repo.items():
        choices.extend(build_repo_choices(repo_name, branches, stale_days))

    result = questionary.checkbox(
        "Select branches to delete:",
        choices=choices,
        instruction="(Space to toggle, Enter to confirm, Ctrl-C to abort)",
    ).ask()

    if result is None:
        sys.exit(0)

    return result


def print_fetch_summary(
    all_branches_by_repo: dict[str, list[BranchInfo]],
    stale_days: int,
    console: Console,
) -> None:
    table = Table(title="Fetched Branch Summary", show_lines=False)
    table.add_column("Repo", style="bold cyan")
    table.add_column("Total", justify="right")
    table.add_column(
        f"Pre-selected\n(>{stale_days}d old)", justify="right", style="yellow"
    )
    table.add_column("Protected\n(skipped)", justify="right", style="dim")
    table.add_column("Open PR\n(skipped)", justify="right", style="dim")

    for repo_name, branches in all_branches_by_repo.items():
        total = len(branches)
        preselected = sum(
            1
            for b in branches
            if b.days_old >= stale_days and not b.is_protected and not b.has_open_pr
        )
        protected = sum(1 for b in branches if b.is_protected)
        open_pr = sum(1 for b in branches if b.has_open_pr)
        table.add_row(
            repo_name, str(total), str(preselected), str(protected), str(open_pr)
        )

    console.print()
    console.print(table)
    console.print()


def print_deletion_summary(
    selected: list[BranchInfo],
    dry_run: bool,
    console: Console,
) -> None:
    header = (
        "[bold yellow][DRY RUN] Branches that would be deleted:[/bold yellow]"
        if dry_run
        else "[bold red]Branches to be deleted:[/bold red]"
    )
    console.print(f"\n{header}")

    table = Table(show_lines=False)
    table.add_column("Repo", style="cyan")
    table.add_column("Branch", style="bold")
    table.add_column("Age", justify="right")
    table.add_column("Author")
    table.add_column("Merged")

    for branch in selected:
        merged = "[green]yes[/green]" if branch.is_merged else "[dim]no[/dim]"
        table.add_row(
            branch.repo_full_name,
            branch.name,
            f"{branch.days_old}d",
            branch.last_commit_author,
            merged,
        )

    console.print(table)


def confirm_deletion(dry_run: bool) -> bool:
    if dry_run:
        return False
    answer = questionary.text("Type 'yes' to confirm deletion:").ask()
    if answer is None:
        return False
    return answer.strip().lower() == "yes"


def print_deletion_results(
    results: list[tuple[BranchInfo, bool]],
    console: Console,
) -> None:
    console.print()
    success = sum(1 for _, ok in results if ok)
    failed = len(results) - success

    table = Table(title="Deletion Results", show_lines=False)
    table.add_column("Status", justify="center")
    table.add_column("Repo", style="cyan")
    table.add_column("Branch")

    for branch, ok in results:
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        table.add_row(status, branch.repo_full_name, branch.name)

    console.print(table)
    console.print(f"[green]{success} deleted[/green]", end="")
    if failed:
        console.print(f"  [red]{failed} failed[/red]", end="")
    console.print()
