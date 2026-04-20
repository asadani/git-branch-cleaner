from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import github
from github import Github, GithubException
from rich.console import Console


class RepoNotFoundError(Exception):
    def __init__(self, repo_full_name: str):
        super().__init__(f"Repository '{repo_full_name}' not found or not accessible")
        self.repo_full_name = repo_full_name


@dataclass
class BranchInfo:
    repo_full_name: str
    name: str
    last_commit_sha: str
    last_commit_date: datetime  # timezone-aware UTC
    last_commit_author: str
    is_protected: bool
    is_merged: bool
    has_open_pr: bool
    days_old: int
    is_default: bool


def normalize_github_token(raw: str) -> str:
    """Strip whitespace, BOM, and optional surrounding quotes from env/.env values."""
    t = (raw or "").strip()
    t = t.lstrip("\ufeff")
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t


def create_github_client(token: str) -> Github:
    cleaned = normalize_github_token(token)
    if not cleaned:
        raise ValueError(
            "GitHub token is missing or empty. Set GITHUB_TOKEN (e.g. in a .env file) "
            "or pass --token."
        )
    return Github(cleaned, per_page=100)


def validate_token(client: Github, console: Console) -> None:
    try:
        login = client.get_user().login
        console.print(f"[dim]Authenticated as [bold]{login}[/bold][/dim]")
    except GithubException as e:
        if e.status == 401:
            console.print(
                "[red]Error: GitHub rejected the token (401 unauthorized).[/red]\n"
                "[dim]Check that the token is valid and not expired; remove stray "
                "spaces or quotes around the value in .env; and use a classic PAT "
                "(repo scope) or a fine-grained PAT with API access to your "
                "account.[/dim]"
            )
        else:
            console.print(
                f"[red]GitHub API error during authentication: {e.data}[/red]"
            )
        sys.exit(1)


def get_repo(client: Github, repo_full_name: str) -> github.Repository.Repository:
    try:
        return client.get_repo(repo_full_name)
    except github.UnknownObjectException:
        raise RepoNotFoundError(repo_full_name)
    except GithubException as e:
        raise RuntimeError(f"GitHub API error for '{repo_full_name}': {e.data}") from e


def get_open_pr_branches(repo: github.Repository.Repository) -> set[str]:
    """Returns a set of branch names that have an open pull request."""
    try:
        return {pr.head.ref for pr in repo.get_pulls(state="open")}
    except GithubException:
        return set()


def fetch_branches(
    repo: github.Repository.Repository,
    open_pr_branches: set[str],
    console: Console,
) -> list[BranchInfo]:
    now = datetime.now(timezone.utc)
    branches: list[BranchInfo] = []

    try:
        for branch in repo.get_branches():
            if branch.name == repo.default_branch:
                continue

            commit = branch.commit.commit
            commit_date = commit.author.date
            if commit_date.tzinfo is None:
                commit_date = commit_date.replace(tzinfo=timezone.utc)

            days_old = (now - commit_date).days
            branches.append(
                BranchInfo(
                    repo_full_name=repo.full_name,
                    name=branch.name,
                    last_commit_sha=branch.commit.sha,
                    last_commit_date=commit_date,
                    last_commit_author=commit.author.name or "",
                    is_protected=branch.protected,
                    is_merged=False,
                    has_open_pr=(branch.name in open_pr_branches),
                    days_old=days_old,
                    is_default=False,
                )
            )
    except GithubException as e:
        msg = f"{repo.full_name}: {e.data}"
        console.print(f"[yellow]Warning: Could not list branches for {msg}[/yellow]")

    branches.sort(key=lambda b: b.last_commit_date)
    return branches


def check_merged_status(
    repo: github.Repository.Repository,
    branches: list[BranchInfo],
    console: Console,
) -> None:
    """Check which branches are fully merged into the default branch."""
    candidates = [b for b in branches if not b.is_protected and not b.has_open_pr]
    if not candidates:
        return

    try:
        rate = repo._requester.requestJsonAndCheck("GET", "/rate_limit")[1]
        remaining = rate.get("resources", {}).get("core", {}).get("remaining", 5000)
    except Exception:
        remaining = 5000

    if remaining < len(candidates) + 50:
        console.print(
            f"[yellow]Warning: Low API rate limit ({remaining} remaining). "
            "Skipping merge status checks to preserve quota.[/yellow]"
        )
        return

    for branch in candidates:
        try:
            comparison = repo.compare(repo.default_branch, branch.name)
            branch.is_merged = comparison.status in ("behind", "identical")
        except GithubException as e:
            if e.status == 404:
                pass
            else:
                detail = f"{branch.name}: {e.data}"
                console.print(f"[yellow]Warning: Could not compare {detail}[/yellow]")


def delete_branch(
    repo: github.Repository.Repository,
    branch_name: str,
    dry_run: bool,
    console: Console,
) -> bool:
    if dry_run:
        console.print(
            f"[dim][DRY RUN] Would delete {repo.full_name}:{branch_name}[/dim]"
        )
        return True
    try:
        ref = repo.get_git_ref(f"heads/{branch_name}")
        ref.delete()
        return True
    except GithubException as e:
        console.print(
            f"[red]Failed to delete {repo.full_name}:{branch_name}: {e.data}[/red]"
        )
        return False
