"""FastAPI application entry.

Exposes JSON APIs for:
- listing org repos
- course/repo lookup and fetching readme.toml
- submitting TOML to create PR (or enqueue pending if repo missing)
- background poller that watches pending requests and creates PRs when repos appear
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import tomlkit

from .auth import require_api_key
from .db import get_request, init_db, insert_pending, list_by_status, update_status
from .emailer import send_admin_email
from .github_client import GitHubClient, GitHubError, normalize_repo_name
from .pr_flow import PRFlowError, create_pr_from_toml, ensure_pr_from_toml
from .render import RenderError, render_readme_from_toml
from .settings import Settings, is_repo_allowed, load_settings
from .toml_templates import multiproject_template, normal_template
from .toml_ops import apply_ops, parse_ops
from .toml_paragraph import locate_paragraph_candidates, patch_paragraph
from .toml_summary import summarize_toml

log = logging.getLogger("hoa_prserver")


def _should_hide_repo_from_listing(*, name: str, org_name: str) -> bool:
    # Hide internal / meta repos by convention.
    if not name:
        return True
    if name == org_name:
        return True
    if "-" in name:
        return True
    if name.startswith("."):
        return True
    if name.startswith("hoa-"):
        return True
    return False


def _resolve_repo_name_or_422(*, repo_name: str | None, course_code: str | None) -> str:
    raw = (repo_name or course_code or "").strip()
    try:
        return normalize_repo_name(raw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"invalid repo name: {e}")


def create_app() -> FastAPI:
    settings = load_settings()
    init_db(settings.db_path)

    app = FastAPI(title="hoa-prServer", version="0.1.0")
    app.state.settings = settings
    app.state.github = GitHubClient(token=settings.github_token)
    app.state.course_index_cache = {"ts": 0.0, "items": []}

    # Allow frontend usage. If you serve the frontend via /web (same origin),
    # CORS is not needed; but enabling common localhost ports reduces friction.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:8010",
            "http://127.0.0.1:8010",
            "http://localhost:5173",
            "http://localhost:5500",
            "http://127.0.0.1:5500",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        app.state._poller_task = asyncio.create_task(_poller_loop(app))

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "_poller_task", None)
        if task:
            task.cancel()

    # Static frontend (no build step)
    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.exists():
        app.mount("/web", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()


def _settings_dep() -> Settings:
    return app.state.settings


def _github_dep() -> GitHubClient:
    return app.state.github


def _auth_dep(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(_settings_dep),
) -> None:
    require_api_key(settings, x_api_key)


class RenderRequest(BaseModel):
    toml: str = Field(..., description="TOML text (readme.toml)")


class RenderResponse(BaseModel):
    readme_md: str


class LocateParagraphRequest(BaseModel):
    toml: str = Field(..., description="TOML text (readme.toml)")
    snippet: str = Field(..., description="Original paragraph snippet to locate")


class LocateParagraphCandidate(BaseModel):
    type: str
    preview: str
    target: dict = Field(default_factory=dict)


class LocateParagraphResponse(BaseModel):
    candidates: list[LocateParagraphCandidate]


class PatchParagraphRequest(BaseModel):
    toml: str = Field(..., description="TOML text (readme.toml)")
    target: dict = Field(..., description="Target info returned by locate")
    old_paragraph: str = Field(..., description="Original paragraph (must still match)")
    new_paragraph: str = Field(..., description="Replacement paragraph")
    author: dict | None = Field(default=None, description="Optional author to append")


class PatchParagraphResponse(BaseModel):
    toml: str


class RepoInfo(BaseModel):
    name: str
    full_name: str
    html_url: str
    default_branch: str


class LookupResponse(BaseModel):
    exists: bool
    repo: RepoInfo | None = None
    toml: str


class SubmitRequest(BaseModel):
    repo_name: str | None = Field(
        default=None,
        description=(
            "Optional: override target GitHub repo name. "
            "If omitted, course_code is used as repo name."
        ),
    )
    course_code: str = Field(..., description="Course code, also used as repo name by default")
    course_name: str
    repo_type: str = Field("normal", description="normal | multi-project")
    toml: str = Field(..., description="Full TOML payload")


class SubmitResponse(BaseModel):
    status: str
    request_id: int | None = None
    pr_url: str | None = None


class SubmitOpsRequest(BaseModel):
    repo_name: str | None = Field(default=None, description="Target GitHub repo name override")
    course_code: str = Field(..., description="Course code (used for title/body/template)")
    course_name: str = Field("", description="Course name (used for template)")
    repo_type: str = Field("normal", description="normal | multi-project")

    ops: list[dict] = Field(..., description="List of TOML patch operations")

    dry_run: bool = Field(False, description="If true, only return patched TOML; do not create PR")


class SubmitOpsResponse(BaseModel):
    status: str
    toml: str | None = None
    request_id: int | None = None
    pr_url: str | None = None


class EnsurePRRequest(BaseModel):
    repo_name: str | None = Field(default=None, description="Target GitHub repo name override")
    course_code: str = Field(..., description="Course code (defaults to repo name if repo_name not provided)")
    course_name: str = Field("", description="Course name (optional)")
    repo_type: str = Field("normal", description="normal | multi-project")

    toml: str | None = Field(
        default=None,
        description=(
            "Final-schema readme.toml text. If omitted, server will try to load from FINAL_DIR/<CODE>/readme.toml."
        ),
    )


class EnsurePRResponse(BaseModel):
    status: str
    pr_url: str | None = None
    branch: str | None = None
    source: str | None = None
    request_id: int | None = None


class CourseIndexItem(BaseModel):
    repo_name: str
    course_code: str
    course_name: str
    repo_type: str
    html_url: str = ""


class ReadmeResponse(BaseModel):
    repo: RepoInfo | None = None
    readme_md: str
    source: str


class TomlResponse(BaseModel):
    repo: RepoInfo | None = None
    toml: str
    source: str


class StructureResponse(BaseModel):
    repo: RepoInfo | None = None
    summary: dict


class FinalTomlResponse(BaseModel):
    course_code: str
    toml: str
    source: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/readme/render", response_model=RenderResponse)
def render_readme(req: RenderRequest) -> RenderResponse:
    try:
        readme_md = render_readme_from_toml(req.toml)
    except RenderError as e:
        # Most failures here are user-provided TOML format errors.
        raise HTTPException(status_code=400, detail=str(e))
    return RenderResponse(readme_md=readme_md)


@app.post("/v1/toml/locate", response_model=LocateParagraphResponse)
def locate_paragraph(req: LocateParagraphRequest) -> LocateParagraphResponse:
    try:
        candidates = locate_paragraph_candidates(req.toml, req.snippet)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return LocateParagraphResponse(
        candidates=[
            LocateParagraphCandidate(type=c.type, preview=c.preview, target={"type": c.type, **(c.data or {})})
            for c in candidates
        ]
    )


@app.post("/v1/toml/patch_paragraph", response_model=PatchParagraphResponse)
def patch_paragraph_api(req: PatchParagraphRequest) -> PatchParagraphResponse:
    target_type = str((req.target or {}).get("type") or "").strip()
    target_data = dict(req.target or {})
    target_data.pop("type", None)

    try:
        patched = patch_paragraph(
            req.toml,
            candidate_type=target_type,
            candidate_data=target_data,
            old_paragraph=req.old_paragraph,
            new_paragraph=req.new_paragraph,
            author=req.author,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PatchParagraphResponse(toml=patched)


@app.get("/v1/org/repos", response_model=list[RepoInfo])
async def list_repos(
    q: str = "",
    limit: int = 200,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> list[RepoInfo]:
    repos = await gh.list_org_repos(settings.org_name, limit=max(1, min(limit, 500)))

    # Default filters (always applied)
    repos = [r for r in repos if not _should_hide_repo_from_listing(name=r.name, org_name=settings.org_name)]

    if settings.allowed_repos is not None:
        repos = [r for r in repos if r.name in settings.allowed_repos]
    if q:
        q_lower = q.lower()
        repos = [r for r in repos if q_lower in (r.name or "").lower()]
    return [RepoInfo(**r.__dict__) for r in repos]


@app.get("/v1/courses/lookup", response_model=LookupResponse)
async def lookup_course(
    course_code: str | None = None,
    repo_name: str | None = None,
    course_name: str = "",
    repo_type: str = "normal",
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> LookupResponse:
    if not course_code and not repo_name:
        raise HTTPException(status_code=422, detail="either course_code or repo_name is required")

    resolved_repo_name = _resolve_repo_name_or_422(repo_name=repo_name, course_code=course_code)
    if not is_repo_allowed(settings, resolved_repo_name):
        raise HTTPException(status_code=403, detail="repo not allowed in current server config")
    repo = await gh.get_repo(settings.org_name, resolved_repo_name)

    effective_course_code = (course_code or resolved_repo_name).strip()
    effective_course_name = (course_name or effective_course_code).strip()

    if repo is None:
        tmpl = (
            multiproject_template(course_name=effective_course_name, course_code=effective_course_code)
            if repo_type == "multi-project"
            else normal_template(course_name=effective_course_name, course_code=effective_course_code)
        )
        return LookupResponse(exists=False, repo=None, toml=tmpl)

    toml_text = await gh.get_file_text(
        settings.org_name, resolved_repo_name, "readme.toml", ref=repo.default_branch
    )
    if toml_text is None:
        toml_text = (
            multiproject_template(course_name=effective_course_name, course_code=effective_course_code)
            if repo_type == "multi-project"
            else normal_template(course_name=effective_course_name, course_code=effective_course_code)
        )

    return LookupResponse(exists=True, repo=RepoInfo(**repo.__dict__), toml=toml_text)


@app.get("/v1/courses/index", response_model=list[CourseIndexItem])
async def course_index(
    q: str = "",
    limit: int = 500,
    refresh: bool = False,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> list[CourseIndexItem]:
    # Simple in-memory cache to avoid hammering GitHub.
    cache = app.state.course_index_cache
    now = time.time()
    if not refresh and cache.get("items") and (now - float(cache.get("ts") or 0.0)) < 300:
        items = cache["items"]
    else:
        try:
            repos = await gh.list_org_repos(settings.org_name, limit=max(1, min(limit, 1000)))
            repos = [r for r in repos if not _should_hide_repo_from_listing(name=r.name, org_name=settings.org_name)]
            if settings.allowed_repos is not None:
                repos = [r for r in repos if r.name in settings.allowed_repos]

            sem = asyncio.Semaphore(12)

            async def one(r) -> CourseIndexItem:
                async with sem:
                    toml_text = await gh.get_file_text(
                        settings.org_name, r.name, "readme.toml", ref=r.default_branch
                    )
                    course_code = r.name
                    course_name = r.name
                    repo_type = "normal"
                    if toml_text:
                        try:
                            doc = tomlkit.parse(toml_text)
                            course_code = str(doc.get("course_code") or r.name)
                            course_name = str(doc.get("course_name") or course_code)
                            repo_type = str(doc.get("repo_type") or repo_type)
                        except Exception:
                            pass
                    return CourseIndexItem(
                        repo_name=r.name,
                        course_code=course_code,
                        course_name=course_name,
                        repo_type=repo_type,
                        html_url=r.html_url,
                    )

            items = await asyncio.gather(*[one(r) for r in repos])
            cache["ts"] = now
            cache["items"] = items
        except GitHubError as e:
            msg = str(e)
            low = msg.lower()
            if "rate limit" in low or "rate-limit" in low:
                raise HTTPException(status_code=429, detail=f"GitHub API rate limited: {msg}")
            raise HTTPException(status_code=502, detail=f"GitHub upstream error: {msg}")

    if q:
        ql = q.lower()
        items = [
            it
            for it in items
            if ql in (it.repo_name or "").lower()
            or ql in (it.course_code or "").lower()
            or ql in (it.course_name or "").lower()
        ]
    return items


@app.get("/v1/courses/toml", response_model=TomlResponse)
async def get_course_toml(
    repo_name: str,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> TomlResponse:
    resolved_repo_name = _resolve_repo_name_or_422(repo_name=repo_name, course_code=None)
    if not is_repo_allowed(settings, resolved_repo_name):
        raise HTTPException(status_code=403, detail="repo not allowed in current server config")
    repo = await gh.get_repo(settings.org_name, resolved_repo_name)
    if repo is None:
        tmpl = normal_template(course_name=resolved_repo_name, course_code=resolved_repo_name)
        return TomlResponse(repo=None, toml=tmpl, source="template")

    toml_text = await gh.get_file_text(settings.org_name, resolved_repo_name, "readme.toml", ref=repo.default_branch)
    if toml_text is None:
        tmpl = normal_template(course_name=resolved_repo_name, course_code=resolved_repo_name)
        return TomlResponse(repo=RepoInfo(**repo.__dict__), toml=tmpl, source="template")
    return TomlResponse(repo=RepoInfo(**repo.__dict__), toml=toml_text, source="repo_toml")


@app.get("/v1/courses/readme", response_model=ReadmeResponse)
async def get_course_readme(
    repo_name: str,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> ReadmeResponse:
    resolved_repo_name = _resolve_repo_name_or_422(repo_name=repo_name, course_code=None)
    if not is_repo_allowed(settings, resolved_repo_name):
        raise HTTPException(status_code=403, detail="repo not allowed in current server config")
    repo = await gh.get_repo(settings.org_name, resolved_repo_name)
    if repo is None:
        toml_text = normal_template(course_name=resolved_repo_name, course_code=resolved_repo_name)
        return ReadmeResponse(repo=None, readme_md=render_readme_from_toml(toml_text), source="rendered_template")

    readme_md = await gh.get_file_text(settings.org_name, resolved_repo_name, "README.md", ref=repo.default_branch)
    if readme_md:
        return ReadmeResponse(repo=RepoInfo(**repo.__dict__), readme_md=readme_md, source="repo_readme")

    toml_text = await gh.get_file_text(settings.org_name, resolved_repo_name, "readme.toml", ref=repo.default_branch)
    if toml_text:
        return ReadmeResponse(
            repo=RepoInfo(**repo.__dict__),
            readme_md=render_readme_from_toml(toml_text),
            source="rendered_from_toml",
        )

    toml_text = normal_template(course_name=resolved_repo_name, course_code=resolved_repo_name)
    return ReadmeResponse(repo=RepoInfo(**repo.__dict__), readme_md=render_readme_from_toml(toml_text), source="rendered_template")


@app.get("/v1/courses/structure", response_model=StructureResponse)
async def get_course_structure(
    repo_name: str,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> StructureResponse:
    resolved_repo_name = _resolve_repo_name_or_422(repo_name=repo_name, course_code=None)
    if not is_repo_allowed(settings, resolved_repo_name):
        raise HTTPException(status_code=403, detail="repo not allowed in current server config")
    repo = await gh.get_repo(settings.org_name, resolved_repo_name)
    if repo is None:
        tmpl = normal_template(course_name=resolved_repo_name, course_code=resolved_repo_name)
        return StructureResponse(repo=None, summary=summarize_toml(tmpl))

    toml_text = await gh.get_file_text(settings.org_name, resolved_repo_name, "readme.toml", ref=repo.default_branch)
    if toml_text is None:
        tmpl = normal_template(course_name=resolved_repo_name, course_code=resolved_repo_name)
        return StructureResponse(repo=RepoInfo(**repo.__dict__), summary=summarize_toml(tmpl))

    return StructureResponse(repo=RepoInfo(**repo.__dict__), summary=summarize_toml(toml_text))


@app.get("/v1/final/toml", response_model=FinalTomlResponse)
async def get_final_toml(
    course_code: str,
    settings: Settings = Depends(_settings_dep),
    _auth: None = Depends(_auth_dep),
) -> FinalTomlResponse:
    code = (course_code or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="course_code is required")
    toml_text = _try_load_final_toml(settings, course_code=code)
    if toml_text is None:
        raise HTTPException(status_code=404, detail="final toml not found (check FINAL_DIR)")
    return FinalTomlResponse(course_code=code, toml=toml_text, source="final_dir")


@app.post("/v1/courses/submit", response_model=SubmitResponse)
async def submit_course(
    req: SubmitRequest,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> SubmitResponse:
    resolved_repo_name = _resolve_repo_name_or_422(repo_name=req.repo_name, course_code=req.course_code)
    if not is_repo_allowed(settings, resolved_repo_name):
        raise HTTPException(status_code=403, detail="repo not allowed in current server config")
    repo = await gh.get_repo(settings.org_name, resolved_repo_name)

    if repo is None:
        request_id = insert_pending(
            settings.db_path,
            org=settings.org_name,
            repo=resolved_repo_name,
            course_code=req.course_code,
            course_name=req.course_name,
            repo_type=req.repo_type,
            toml_text=req.toml,
            status="waiting_repo",
        )

        send_admin_email(
            settings,
            subject=f"[hoa-prServer] 仓库不存在：{resolved_repo_name}",
            text=(
                f"收到提交，但组织 {settings.org_name} 下尚不存在仓库 {resolved_repo_name}。\n"
                f"course_code: {req.course_code}\n"
                f"course_name: {req.course_name}\n"
                f"request_id: {request_id}\n\n"
                "请管理员创建仓库后，服务端会按小时轮询并自动创建 PR。\n"
            ),
        )

        return SubmitResponse(status="waiting_repo", request_id=request_id)

    if not settings.github_token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN not configured")

    try:
        pr = await create_pr_from_toml(
            gh=gh,
            org=settings.org_name,
            repo=resolved_repo_name,
            default_branch=repo.default_branch,
            github_token=settings.github_token,
            toml_text=req.toml,
            toml_path="readme.toml",
            title=f"chore: update {req.course_code} readme.toml",
            body="Automated PR via hoa-prServer.",
            branch_prefix="bot/rdme",
        )
    except PRFlowError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return SubmitResponse(status="pr_created", pr_url=pr.pr_url)


def _try_load_final_toml(settings: Settings, *, course_code: str) -> str | None:
    if settings.final_dir is None:
        return None
    code = (course_code or "").strip()
    if not code:
        return None
    path = settings.final_dir / code / "readme.toml"
    if not path.exists() or not path.is_file():
        return None
    # utf-8-sig tolerates BOM (Windows editors)
    return path.read_text(encoding="utf-8-sig")


@app.post("/v1/pr/ensure", response_model=EnsurePRResponse)
async def ensure_pr(
    req: EnsurePRRequest,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> EnsurePRResponse:
    resolved_repo_name = _resolve_repo_name_or_422(repo_name=req.repo_name, course_code=req.course_code)
    if not is_repo_allowed(settings, resolved_repo_name):
        raise HTTPException(status_code=403, detail="repo not allowed in current server config")

    repo = await gh.get_repo(settings.org_name, resolved_repo_name)

    effective_course_code = (req.course_code or resolved_repo_name).strip() or resolved_repo_name
    effective_course_name = (req.course_name or effective_course_code).strip()

    toml_source = "provided"
    toml_text = req.toml
    if toml_text is None:
        toml_text = _try_load_final_toml(settings, course_code=effective_course_code)
        toml_source = "final_dir" if toml_text is not None else toml_source

    if toml_text is None:
        raise HTTPException(
            status_code=422,
            detail="toml is required unless FINAL_DIR is configured and contains final/<CODE>/readme.toml",
        )

    if repo is None:
        request_id = insert_pending(
            settings.db_path,
            org=settings.org_name,
            repo=resolved_repo_name,
            course_code=effective_course_code,
            course_name=effective_course_name,
            repo_type=req.repo_type,
            toml_text=toml_text,
            status="waiting_repo",
        )
        return EnsurePRResponse(status="waiting_repo", request_id=request_id, source=toml_source)

    if not settings.github_token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN not configured")

    stable_branch = f"bot/rdme-{resolved_repo_name}"
    try:
        pr = await ensure_pr_from_toml(
            gh=gh,
            org=settings.org_name,
            repo=resolved_repo_name,
            default_branch=repo.default_branch,
            github_token=settings.github_token,
            toml_text=toml_text,
            branch=stable_branch,
            toml_path="readme.toml",
            title=f"chore: update {effective_course_code} readme.toml",
            body="Automated PR via hoa-prServer (ensure).",
        )
    except PRFlowError as e:
        raise HTTPException(status_code=400, detail=str(e))

    status = {"created": "pr_created", "updated": "pr_updated", "no_change": "no_change"}.get(pr.action, "pr_created")
    return EnsurePRResponse(status=status, pr_url=pr.pr_url or None, branch=pr.branch, source=toml_source)


@app.post("/v1/courses/submit_ops", response_model=SubmitOpsResponse)
async def submit_ops(
    req: SubmitOpsRequest,
    settings: Settings = Depends(_settings_dep),
    gh: GitHubClient = Depends(_github_dep),
    _auth: None = Depends(_auth_dep),
) -> SubmitOpsResponse:
    resolved_repo_name = _resolve_repo_name_or_422(repo_name=req.repo_name, course_code=req.course_code)
    if not is_repo_allowed(settings, resolved_repo_name):
        raise HTTPException(status_code=403, detail="repo not allowed in current server config")

    repo = await gh.get_repo(settings.org_name, resolved_repo_name)

    effective_course_code = (req.course_code or resolved_repo_name).strip()
    effective_course_name = (req.course_name or effective_course_code).strip()

    if repo is None:
        base = (
            multiproject_template(course_name=effective_course_name, course_code=effective_course_code)
            if req.repo_type == "multi-project"
            else normal_template(course_name=effective_course_name, course_code=effective_course_code)
        )
    else:
        base = await gh.get_file_text(settings.org_name, resolved_repo_name, "readme.toml", ref=repo.default_branch)
        if base is None:
            base = (
                multiproject_template(course_name=effective_course_name, course_code=effective_course_code)
                if req.repo_type == "multi-project"
                else normal_template(course_name=effective_course_name, course_code=effective_course_code)
            )

    try:
        ops = parse_ops(req.ops)
        patched = apply_ops(base, ops)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid ops: {e}")

    if req.dry_run:
        return SubmitOpsResponse(status="patched", toml=patched)

    if repo is None:
        request_id = insert_pending(
            settings.db_path,
            org=settings.org_name,
            repo=resolved_repo_name,
            course_code=effective_course_code,
            course_name=effective_course_name,
            repo_type=req.repo_type,
            toml_text=patched,
            status="waiting_repo",
        )
        return SubmitOpsResponse(status="waiting_repo", request_id=request_id, toml=patched)

    if not settings.github_token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN not configured")

    try:
        pr = await create_pr_from_toml(
            gh=gh,
            org=settings.org_name,
            repo=resolved_repo_name,
            default_branch=repo.default_branch,
            github_token=settings.github_token,
            toml_text=patched,
            toml_path="readme.toml",
            title=f"chore: update {effective_course_code} readme.toml",
            body="Automated PR via hoa-prServer (ops).",
            branch_prefix="bot/rdme",
        )
    except PRFlowError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return SubmitOpsResponse(status="pr_created", pr_url=pr.pr_url, toml=patched)


@app.get("/v1/requests/{request_id}")
async def get_pending_request(
    request_id: int,
    settings: Settings = Depends(_settings_dep),
    _auth: None = Depends(_auth_dep),
) -> dict:
    r = get_request(settings.db_path, request_id)
    if not r:
        raise HTTPException(status_code=404, detail="not found")
    return r.__dict__


async def _poller_loop(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    gh: GitHubClient = app.state.github

    while True:
        try:
            pending = list_by_status(settings.db_path, "waiting_repo", limit=50)
            for it in pending:
                if not is_repo_allowed(settings, it.repo):
                    update_status(settings.db_path, it.id, status="failed", last_error="repo not allowed")
                    continue
                repo = await gh.get_repo(it.org, it.repo)
                if repo is None:
                    continue

                if not settings.github_token:
                    update_status(settings.db_path, it.id, status="failed", last_error="GITHUB_TOKEN not configured")
                    continue

                try:
                    pr = await create_pr_from_toml(
                        gh=gh,
                        org=it.org,
                        repo=it.repo,
                        default_branch=repo.default_branch,
                        github_token=settings.github_token,
                        toml_text=it.toml_text,
                        title=f"chore: update {it.course_code} readme.toml",
                        body="Automated PR via hoa-prServer (delayed until repo existed).",
                        branch_prefix="bot/rdme",
                    )
                    update_status(settings.db_path, it.id, status="pr_created", pr_url=pr.pr_url)
                except Exception as e:
                    update_status(settings.db_path, it.id, status="failed", last_error=str(e))

        except Exception as e:
            log.exception("poller loop error: %s", e)

        await asyncio.sleep(max(10, settings.poll_interval_seconds))
