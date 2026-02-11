"""PR automation flow.

Implements the end-to-end process:
- clone repo
- write readme.toml
- generate README.md (reusing the same converter as render endpoint)
- commit + push a branch
- create a GitHub PR
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .github_client import GitHubClient
from .render import RenderError, render_readme_from_toml


@dataclass(frozen=True)
class PRResult:
    branch: str
    pr_url: str
    action: str  # created | updated | no_change


class PRFlowError(RuntimeError):
    pass


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PRFlowError(
            "command failed\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n\n"
            f"stderr:\n{proc.stderr}\n"
        )


def _maybe_taplo_fmt(toml_path: Path) -> None:
    if shutil.which("taplo") is None:
        return
    subprocess.run(["taplo", "fmt", str(toml_path)], capture_output=True, text=True)


async def create_pr_from_toml(
    *,
    gh: GitHubClient,
    org: str,
    repo: str,
    default_branch: str,
    github_token: str,
    toml_text: str,
    toml_path: str = "readme.toml",
    readme_path: str = "README.md",
    title: str = "chore: update readme.toml",
    body: str = "Automated change via hoa-prServer.",
    branch_prefix: str = "bot/rdme",
) -> PRResult:
    if not github_token:
        raise PRFlowError("GITHUB_TOKEN is required")

    branch = f"{branch_prefix}-{_ts()}"
    clone_url = f"https://x-access-token:{github_token}@github.com/{org}/{repo}.git"

    with tempfile.TemporaryDirectory(prefix="hoa-prserver-pr-") as tmp:
        tmp_path = Path(tmp)

        _run(["git", "clone", "--depth", "1", "--branch", default_branch, clone_url, "repo"], cwd=tmp_path)
        repo_dir = tmp_path / "repo"

        _run(["git", "checkout", "-b", branch], cwd=repo_dir)

        (repo_dir / toml_path).write_text(toml_text, encoding="utf-8", newline="\n")
        _maybe_taplo_fmt(repo_dir / toml_path)

        try:
            readme_md = render_readme_from_toml((repo_dir / toml_path).read_text(encoding="utf-8"))
        except RenderError as e:
            raise PRFlowError(str(e))

        (repo_dir / readme_path).write_text(readme_md, encoding="utf-8", newline="\n")

        _run(["git", "config", "user.name", "hoa-prServer"], cwd=repo_dir)
        _run(["git", "config", "user.email", "actions@github.com"], cwd=repo_dir)

        _run(["git", "add", toml_path, readme_path], cwd=repo_dir)

        # No-op when nothing changed.
        proc = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=str(repo_dir))
        if proc.returncode == 0:
            raise PRFlowError("no changes to commit")

        _run(["git", "commit", "-m", title], cwd=repo_dir)
        _run(["git", "push", "origin", f"HEAD:{branch}"], cwd=repo_dir)

        # head for same-org PR can be just branch name.
        pr_url = await gh.create_pull_request(
            org,
            repo,
            title=title,
            body=body,
            head=branch,
            base=default_branch,
        )

        return PRResult(branch=branch, pr_url=pr_url, action="created")


async def ensure_pr_from_toml(
    *,
    gh: GitHubClient,
    org: str,
    repo: str,
    default_branch: str,
    github_token: str,
    toml_text: str,
    branch: str,
    toml_path: str = "readme.toml",
    readme_path: str = "README.md",
    title: str = "chore: update readme.toml",
    body: str = "Automated change via hoa-prServer.",
) -> PRResult:
    if not github_token:
        raise PRFlowError("GITHUB_TOKEN is required")

    clone_url = f"https://x-access-token:{github_token}@github.com/{org}/{repo}.git"
    head = f"{org}:{branch}"

    existing_pr_url = await gh.find_open_pr_url_by_head(org, repo, head=head)

    with tempfile.TemporaryDirectory(prefix="hoa-prserver-pr-") as tmp:
        tmp_path = Path(tmp)

        _run(["git", "clone", "--depth", "1", "--branch", default_branch, clone_url, "repo"], cwd=tmp_path)
        repo_dir = tmp_path / "repo"

        # Rebuild the branch from base every time for deterministic output.
        _run(["git", "checkout", "-B", branch, f"origin/{default_branch}"], cwd=repo_dir)

        (repo_dir / toml_path).write_text(toml_text, encoding="utf-8", newline="\n")
        _maybe_taplo_fmt(repo_dir / toml_path)

        try:
            readme_md = render_readme_from_toml((repo_dir / toml_path).read_text(encoding="utf-8"))
        except RenderError as e:
            raise PRFlowError(str(e))

        (repo_dir / readme_path).write_text(readme_md, encoding="utf-8", newline="\n")

        _run(["git", "config", "user.name", "hoa-prServer"], cwd=repo_dir)
        _run(["git", "config", "user.email", "actions@github.com"], cwd=repo_dir)

        _run(["git", "add", toml_path, readme_path], cwd=repo_dir)

        proc = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=str(repo_dir))
        if proc.returncode == 0:
            # Nothing changed. If an open PR exists for this head, keep returning it.
            return PRResult(branch=branch, pr_url=existing_pr_url or "", action="no_change")

        _run(["git", "commit", "-m", title], cwd=repo_dir)
        _run(["git", "push", "origin", f"HEAD:{branch}", "--force-with-lease"], cwd=repo_dir)

        if existing_pr_url:
            return PRResult(branch=branch, pr_url=existing_pr_url, action="updated")

        pr_url = await gh.create_pull_request(
            org,
            repo,
            title=title,
            body=body,
            head=branch,
            base=default_branch,
        )
        return PRResult(branch=branch, pr_url=pr_url, action="created")
