#!/usr/bin/env python3
"""Convert readme.toml -> README.md (final schema only).

This is the same minimal deterministic renderer as workspace root
convert_toml_to_readme.py, vendored here so prServer Docker/runtime does not
accidentally fall back to the legacy renderer.

Only supports the *final* normalized schema:
- normal: unified [[sections]]; section items contain only {content, author?}
- multi-project: [[courses]] with [[courses.sections]]; teacher list in [[courses.teachers]]

CLI
- --input FILE|DIR: convert one file or scan DIR/**/readme.toml
- --all: convert ./final/**/readme.toml
"""

from __future__ import annotations

import argparse
import re
import textwrap
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover (Python <= 3.10)
    import tomli as tomllib  # type: ignore


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _norm_block(text: Any) -> str:
    return _s(text).replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_multiline_md(text: Any) -> str:
    s = _s(text).replace("\r\n", "\n").replace("\r", "\n")
    s = textwrap.dedent(s)
    return s.strip()


def _encode_shields_component(text: str) -> str:
    """Encode a single shields.io path component."""

    s = _s(text).strip()
    if not s:
        return ""
    s = s.replace("-", "--")
    s = s.replace("%", "%25")
    s = s.replace(" ", "%20")
    return s


def _render_shields_badge(*, alt: str, label: str, message: str | None = None, color: str | None = None) -> str:
    base = "https://img.shields.io/badge/"
    if message is None and color is not None:
        path = f"{_encode_shields_component(label)}-{_encode_shields_component(color)}"
    else:
        msg = "" if message is None else message
        col = "brightgreen" if color is None else color
        path = (
            f"{_encode_shields_component(label)}-"
            f"{_encode_shields_component(msg)}-"
            f"{_encode_shields_component(col)}"
        )
    return f"![{alt}]({base}{path})"


def _split_label_value_tail(text: str) -> tuple[str, str]:
    s = _s(text).strip()
    if not s:
        return ("", "")
    parts = s.split()
    if len(parts) >= 2:
        tail = parts[-1].strip()
        if re.fullmatch(r"\d+(?:\.\d+)?%?", tail):
            label = "".join(parts[:-1]).strip()
            return (label or s, tail)

    m = re.match(r"^(?P<label>.*?)(?P<tail>\d+(?:\.\d+)?%?)$", s)
    if m:
        label = _s(m.group("label")).strip()
        tail = _s(m.group("tail")).strip()
        return (label or s, tail)

    return (s, "")


_GRADES_SUMMARY_CACHE: dict[Path, dict] = {}


def _find_upwards(start: Path, filename: str) -> Path | None:
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for p in [cur, *cur.parents]:
        cand = p / filename
        if cand.exists() and cand.is_file():
            return cand
    return None


def _load_grades_summary(toml_path: Path) -> dict:
    path = _find_upwards(Path.cwd(), "grades_summary.toml") or _find_upwards(toml_path, "grades_summary.toml")
    if not path:
        return {}
    cached = _GRADES_SUMMARY_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    _GRADES_SUMMARY_CACHE[path] = data
    return data


def _pick_grade_string(grades_summary: dict, course_code: str) -> str:
    grades = grades_summary.get("grades") if isinstance(grades_summary, dict) else None
    if not isinstance(grades, dict):
        return ""
    entry = grades.get(course_code)
    if not isinstance(entry, dict):
        return ""

    def get_in(node: Any, keys: tuple[str, ...]) -> Any:
        cur = node
        for k in keys:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    preferred_paths: list[tuple[str, ...]] = [("default", "default"), ("default",)]
    for p in preferred_paths:
        cand = get_in(entry, p)
        if isinstance(cand, dict) and _s(cand.get("grade")).strip():
            return _s(cand.get("grade")).strip()

    def dfs(node: Any) -> str:
        if isinstance(node, dict):
            g = _s(node.get("grade")).strip()
            if g:
                return g
            for k in sorted([x for x in node.keys() if isinstance(x, str)]):
                out = dfs(node.get(k))
                if out:
                    return out
        return ""

    return dfs(entry)


def _render_grading_badges_from_grade_string(grade: str) -> list[str]:
    grade = _s(grade).strip()
    if not grade:
        return []

    parts = [p.strip() for p in re.split(r"\s*\|\s*|(?<=[0-9%])\s*\+\s*", grade) if p.strip()]
    if not parts:
        return []

    badges: list[str] = [_render_shields_badge(alt="成绩构成", label="成绩构成", message=None, color="gold")]
    for seg in parts:
        label, value = _split_label_value_tail(seg)
        if not label:
            continue
        alt = f"{label}{value}" if value else label
        badges.append(_render_shields_badge(alt=alt, label=label, message=value or "", color="wheat"))
    return badges


def _render_basic_info_badges(content: str, *, fallback_grading_badges: list[str]) -> list[str]:
    text = _normalize_multiline_md(content)
    if not text:
        return fallback_grading_badges

    kv: dict[str, str] = {}
    for ln in text.split("\n"):
        m = re.match(r"^\s*【(?P<k>[^】]+)】\s*[:：]\s*(?P<v>.*\S)\s*$", ln)
        if not m:
            continue
        kv[m.group("k").strip()] = m.group("v").strip()

    badges: list[str] = []

    def ensure_blank_sep() -> None:
        if badges and badges[-1] != "":
            badges.append("")

    credit = kv.get("学分")
    if credit:
        badges.append(_render_shields_badge(alt="学分", label="学分", message=credit, color="moccasin"))

    hours = kv.get("学时构成") or kv.get("学时分布")
    if hours:
        ensure_blank_sep()
        badges.append(_render_shields_badge(alt="学时构成", label="学时构成", message=None, color="gold"))
        for seg in [p.strip() for p in hours.split("|") if p.strip()]:
            label, value = _split_label_value_tail(seg)
            alt = f"{label}{value}" if value else label
            badges.append(_render_shields_badge(alt=alt, label=label, message=value or "", color="wheat"))

    grading = kv.get("成绩构成")
    if grading:
        ensure_blank_sep()
        badges.append(_render_shields_badge(alt="成绩构成", label="成绩构成", message=None, color="gold"))
        for seg in [p.strip() for p in grading.split("|") if p.strip()]:
            label, value = _split_label_value_tail(seg)
            alt = f"{label}{value}" if value else label
            badges.append(_render_shields_badge(alt=alt, label=label, message=value or "", color="wheat"))
    elif fallback_grading_badges:
        ensure_blank_sep()
        badges.extend(fallback_grading_badges)

    while badges and badges[-1] == "":
        badges.pop()
    return badges


def _iter_authors(author: Any) -> list[dict]:
    if author is None:
        return []
    if isinstance(author, str):
        name = author.strip()
        return [{"name": name, "link": "", "date": ""}] if name else []
    if isinstance(author, dict):
        return [author]
    if isinstance(author, list):
        return [a for a in author if isinstance(a, dict)]
    return []


def _render_author(author: Any, *, indent: str = "") -> str:
    parts: list[str] = []
    for a in _iter_authors(author):
        name = _s(a.get("name")).strip()
        link = _s(a.get("link")).strip()
        date = _s(a.get("date")).strip()
        if not name and not link and not date:
            continue
        disp = f"[{name}]({link})" if (name and link) else (name or link)
        if date:
            disp = f"{disp}，{date}" if disp else date
        if disp:
            parts.append(disp)
    if not parts:
        return ""
    return f"{indent}> 文 / " + "，".join(parts)


_LIST_ITEM_PREFIX_RE = re.compile(r"^(?:[-*+]|\d+\.)\s+")


def _listify_md_lines(lines: list[str], *, indent: str) -> list[str]:
    out: list[str] = []
    for ln in lines:
        s = _s(ln).strip()
        if not s:
            continue
        if _LIST_ITEM_PREFIX_RE.match(s):
            out.append(indent + s)
        else:
            out.append(indent + "- " + s)
    return out


def _render_lecturers(lecturers: Any) -> list[str]:
    lec_list = [x for x in _as_list(lecturers) if isinstance(x, dict)]
    if not lec_list:
        return []

    lines: list[str] = ["## 授课教师", ""]
    for lec in lec_list:
        name = _s(lec.get("name")).strip()
        if not name:
            continue
        lines.append(f"- {name}")

        reviews = [x for x in _as_list(lec.get("reviews")) if isinstance(x, dict)]
        for rv in reviews:
            content = _norm_block(rv.get("content"))
            author = rv.get("author")
            if not content and not author:
                continue

            content_lines = content.split("\n") if content else []
            bullet_lines = _listify_md_lines(content_lines, indent="  ")
            if bullet_lines:
                lines.extend(bullet_lines)
            else:
                lines.append("  -")

            aq = _render_author(author, indent="    ")
            if aq:
                lines.append(aq)

    return lines


def _render_teachers_with_reviews(teachers: Any) -> list[str]:
    t_list = [x for x in _as_list(teachers) if isinstance(x, dict)]
    if not t_list:
        return []

    lines: list[str] = []
    for t in t_list:
        name = _s(t.get("name")).strip()
        if not name:
            continue
        lines.append(f"- {name}")

        reviews = [x for x in _as_list(t.get("reviews")) if isinstance(x, dict)]
        for rv in reviews:
            content = _norm_block(rv.get("content"))
            author = rv.get("author")
            if not content and not author:
                continue

            content_lines = content.split("\n") if content else []
            bullet_lines = _listify_md_lines(content_lines, indent="  ")
            if bullet_lines:
                lines.extend(bullet_lines)
            else:
                lines.append("  -")

            aq = _render_author(author, indent="    ")
            if aq:
                lines.append(aq)

    return lines


def _render_section_items(items: Any) -> list[dict]:
    out: list[dict] = []
    for it in _as_list(items):
        if not isinstance(it, dict):
            continue
        out.append({"content": _norm_block(it.get("content")), "author": it.get("author")})
    return out


def _extract_basic_info_from_sections(sections: list[dict], *, fallback_grading_badges: list[str]) -> tuple[list[str], list[dict]]:
    kept: list[dict] = []
    contents: list[str] = []
    for sec in sections:
        title = _s(sec.get("title")).strip()
        if title == "基本信息":
            items = _render_section_items(sec.get("items"))
            for it in items:
                c = _norm_block(it.get("content"))
                if c:
                    contents.append(c)
            continue
        kept.append(sec)

    if not contents:
        return (fallback_grading_badges, kept)
    badges = _render_basic_info_badges("\n".join(contents), fallback_grading_badges=fallback_grading_badges)
    return (badges, kept)


def _render_sections_schema(data: dict, *, grades_summary: dict | None = None) -> str:
    course_name = _s(data.get("course_name")).strip()
    course_code = _s(data.get("course_code")).strip()
    description = _norm_block(data.get("description"))

    lines: list[str] = []
    if course_code and course_name:
        lines.append(f"# {course_code} - {course_name}")
    else:
        lines.append(f"# {course_name or course_code or '课程'}")

    sections = [x for x in _as_list(data.get("sections")) if isinstance(x, dict)]

    fallback_grading_badges: list[str] = []
    if grades_summary and course_code:
        grade = _pick_grade_string(grades_summary, course_code)
        fallback_grading_badges = _render_grading_badges_from_grade_string(grade)

    basic_badges, sections = _extract_basic_info_from_sections(sections, fallback_grading_badges=fallback_grading_badges)
    if basic_badges:
        lines.append("")
        lines.extend(basic_badges)

    if description:
        lines.append("")
        lines.append(description)

    lec_lines = _render_lecturers(data.get("lecturers"))
    if lec_lines:
        lines.append("")
        lines.extend(lec_lines)

    for sec in sections:
        title = _s(sec.get("title")).strip() or "章节"
        items = _render_section_items(sec.get("items"))
        if not items:
            continue

        lines.append("")
        lines.append(f"## {title}")
        for it in items:
            content = it["content"]
            author = it["author"]
            if content:
                lines.append("")
                lines.append(content)
            aq = _render_author(author)
            if aq:
                lines.append("")
                lines.append(aq)

    return "\n".join(lines).rstrip() + "\n"


def render_multi_project(data: dict, *, grades_summary: dict | None = None) -> str:
    course_name = _s(data.get("course_name")).strip()
    course_code = _s(data.get("course_code")).strip()
    description = _norm_block(data.get("description"))

    lines: list[str] = []
    if course_code and course_name:
        lines.append(f"# {course_code} - {course_name}")
    else:
        lines.append(f"# {course_name or course_code or '课程'}")

    if description:
        lines.append("")
        lines.append(description)

    courses = [x for x in _as_list(data.get("courses")) if isinstance(x, dict)]
    for c in courses:
        name = _s(c.get("name")).strip()
        code = _s(c.get("code")).strip()
        header = " - ".join([x for x in [code, name] if x]) or "课程"

        lines.append("")
        lines.append(f"## {header}")

        sections = [x for x in _as_list(c.get("sections")) if isinstance(x, dict)]

        fallback_grading_badges: list[str] = []
        if grades_summary and code:
            grade = _pick_grade_string(grades_summary, code)
            fallback_grading_badges = _render_grading_badges_from_grade_string(grade)

        basic_badges, sections = _extract_basic_info_from_sections(sections, fallback_grading_badges=fallback_grading_badges)
        if basic_badges:
            lines.append("")
            lines.extend(basic_badges)

        teacher_lines = _render_teachers_with_reviews(c.get("teachers"))
        if teacher_lines:
            lines.append("")
            lines.append(f"### {header} - 授课教师")
            lines.append("")
            lines.extend(teacher_lines)

        for sec in sections:
            stitle = _s(sec.get("title")).strip() or "章节"
            items = _render_section_items(sec.get("items"))
            if not items:
                continue

            lines.append("")
            lines.append(f"### {header} - {stitle}")
            for it in items:
                content = it["content"]
                author = it["author"]
                if content:
                    lines.append("")
                    lines.append(content)
                aq = _render_author(author)
                if aq:
                    lines.append("")
                    lines.append(aq)

    return "\n".join(lines).rstrip() + "\n"


def render_readme(data: dict, *, toml_path: Path) -> str:
    repo_type = _s(data.get("repo_type")).strip().lower()
    grades_summary = _load_grades_summary(toml_path)
    if repo_type == "multi-project":
        return render_multi_project(data, grades_summary=grades_summary)

    sections_val = data.get("sections")
    if sections_val is None:
        data = dict(data)
        data["sections"] = []
    elif not isinstance(sections_val, list):
        raise ValueError("normal repo requires unified [[sections]] schema")

    return _render_sections_schema(data, grades_summary=grades_summary)


def _iter_readme_tomls(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.rglob("readme.toml"))


def _default_out_path(input_path: Path) -> Path:
    if input_path.name.lower() == "readme.toml":
        return input_path.with_name("README.md")
    return input_path.with_name(f"{input_path.stem}_README.md")


def main() -> int:
    p = argparse.ArgumentParser(description="Convert readme.toml to README.md (final schema only).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Convert ./final/**/readme.toml -> ./final/**/README.md")
    g.add_argument("--input", "-i", help="Input TOML file or a directory to scan")
    p.add_argument("--output", "-o", help="Output path (only valid when --input is a single file)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing README")
    args = p.parse_args()

    root = Path("final") if args.all else Path(args.input)
    toml_paths = _iter_readme_tomls(root)
    if not toml_paths:
        return 0

    if args.output and len(toml_paths) != 1:
        raise ValueError("--output can only be used with a single input file")

    for toml_path in toml_paths:
        out = Path(args.output) if args.output else _default_out_path(toml_path)
        if out.exists() and not args.overwrite:
            continue
        data = tomllib.loads(toml_path.read_text(encoding="utf-8-sig"))
        out.write_text(render_readme(data, toml_path=toml_path), encoding="utf-8", newline="\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
