"""Thin GitHub REST API client.

Responsibilities:
- list org repos
- check repo existence
- read file contents (readme.toml)
- create pull requests
 - find existing pull requests by head branch
"""

from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class GitHubRepo:
    name: str
    full_name: str
    html_url: str
    default_branch: str


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, *, token: str | None) -> None:
        self._token = token

    async def _request(self, method: str, url: str, *, params: dict | None = None, json: dict | None = None) -> httpx.Response:
        # Minimal retry/backoff for transient failures (network, 429, 5xx).
        backoff_s = 0.6
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.request(
                        method,
                        url,
                        headers=self._headers(),
                        params=params,
                        json=json,
                    )
                if r.status_code in (429, 500, 502, 503, 504):
                    # Respect GitHub rate limiting when possible.
                    await asyncio.sleep(backoff_s)
                    backoff_s = min(backoff_s * 2, 6.0)
                    continue
                return r
            except Exception as e:
                last_exc = e
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, 6.0)
        raise GitHubError(f"request failed after retries: {method} {url}: {last_exc}")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hoa-prServer",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def list_org_repos(self, org: str, *, limit: int = 200) -> list[GitHubRepo]:
        repos: list[GitHubRepo] = []
        per_page = 100
        page = 1
        while len(repos) < limit:
            url = f"https://api.github.com/orgs/{org}/repos"
            r = await self._request("GET", url, params={"per_page": per_page, "page": page})
            if r.status_code >= 400:
                raise GitHubError(f"list repos failed: {r.status_code} {r.text}")

            items = r.json()
            if not isinstance(items, list) or not items:
                break

            for it in items:
                if not isinstance(it, dict):
                    continue
                repos.append(
                    GitHubRepo(
                        name=str(it.get("name") or ""),
                        full_name=str(it.get("full_name") or ""),
                        html_url=str(it.get("html_url") or ""),
                        default_branch=str(it.get("default_branch") or "main"),
                    )
                )
                if len(repos) >= limit:
                    break

            page += 1

        return repos

    async def get_repo(self, org: str, repo: str) -> GitHubRepo | None:
        url = f"https://api.github.com/repos/{org}/{repo}"
        r = await self._request("GET", url)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GitHubError(f"get repo failed: {r.status_code} {r.text}")
        it = r.json()
        return GitHubRepo(
            name=str(it.get("name") or ""),
            full_name=str(it.get("full_name") or ""),
            html_url=str(it.get("html_url") or ""),
            default_branch=str(it.get("default_branch") or "main"),
        )

    async def get_file_text(self, org: str, repo: str, path: str, *, ref: str | None = None) -> str | None:
        url = f"https://api.github.com/repos/{org}/{repo}/contents/{path}"
        params: dict[str, str] = {}
        if ref:
            params["ref"] = ref
        r = await self._request("GET", url, params=params)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GitHubError(f"get file failed: {r.status_code} {r.text}")
        data = r.json()
        if not isinstance(data, dict):
            return None
        if data.get("encoding") != "base64":
            return None
        import base64

        content = data.get("content")
        if not isinstance(content, str):
            return None
        # GitHub inserts line breaks in base64.
        b = base64.b64decode(re.sub(r"\s+", "", content))
        return b.decode("utf-8", errors="replace")

    async def find_open_pr_url_by_head(self, org: str, repo: str, *, head: str) -> str | None:
        # head format: "ORG:branch" for same-org PRs.
        url = f"https://api.github.com/repos/{org}/{repo}/pulls"
        r = await self._request("GET", url, params={"state": "open", "head": head, "per_page": 10})
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise GitHubError(f"list pulls failed: {r.status_code} {r.text}")
        items = r.json()
        if not isinstance(items, list) or not items:
            return None
        for it in items:
            if isinstance(it, dict) and it.get("html_url"):
                return str(it.get("html_url"))
        return None

    async def create_pull_request(
        self,
        org: str,
        repo: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> str:
        if not self._token:
            raise GitHubError("GITHUB_TOKEN is required to create PR")

        url = f"https://api.github.com/repos/{org}/{repo}/pulls"
        r = await self._request("POST", url, json={"title": title, "body": body, "head": head, "base": base})
        if r.status_code == 422:
            # Common case: PR already exists for this head.
            existing = await self.find_open_pr_url_by_head(org, repo, head=head)
            if existing:
                return existing
        if r.status_code >= 400:
            raise GitHubError(f"create PR failed: {r.status_code} {r.text}")
        data = r.json()
        pr_url = data.get("html_url")
        return str(pr_url or "")


_RE_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def normalize_repo_name(repo: str) -> str:
    repo = repo.strip()
    if not repo or "/" in repo or "\\" in repo:
        raise ValueError("invalid repo name")
    if repo == "-":
        raise ValueError("invalid repo name")
    if not _RE_SAFE.match(repo):
        raise ValueError("invalid repo name")
    return repo
