from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import tomlkit
from tomlkit.items import AoT, Array, InlineTable, Table
from tomlkit.toml_document import TOMLDocument


def _norm_text(s: str) -> str:
    return (s or "").strip().replace("\r\n", "\n")


def _safe_str(v: object) -> str:
    if v is None:
        return ""
    return str(v)


def _doc_table(doc: object) -> Table | TOMLDocument:
    if not isinstance(doc, (Table, TOMLDocument)):
        raise ValueError("invalid TOML doc")
    return doc


def _aot(v: object) -> AoT | None:
    return v if isinstance(v, AoT) else None


def _preview_line(content: str, *, limit: int = 60) -> str:
    pv = (content or "").strip().split("\n", 1)[0].strip()
    if len(pv) > limit:
        return pv[: limit - 1] + "…"
    return pv


_RE_YM = re.compile(r"^(\d{4})-(\d{1,2})$")
_RE_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def _normalize_date(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if m := _RE_YM.match(s):
        y, mo = m.group(1), int(m.group(2))
        return f"{y}-{mo:02d}"
    if m := _RE_YMD.match(s):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        return f"{y}-{mo:02d}-{d:02d}"
    return s


def _append_author_field(target: Table, author: dict[str, Any]) -> None:
    name = str(author.get("name") or "").strip()
    link = str(author.get("link") or "").strip()
    date_str = _normalize_date(str(author.get("date") or "").strip())

    t = tomlkit.inline_table()
    t["name"] = name
    t["link"] = link
    t["date"] = date_str

    existing = target.get("author")
    if existing is None:
        target["author"] = t
        return
    if isinstance(existing, Array):
        existing.append(t)
        return
    if isinstance(existing, (InlineTable, dict)):
        arr = tomlkit.array()
        arr.multiline(True)
        arr.append(existing)
        arr.append(t)
        target["author"] = arr
        return
    target["author"] = t


@dataclass(frozen=True)
class Candidate:
    type: str
    preview: str
    data: dict[str, Any]


def locate_paragraph_candidates(toml_text: str, snippet: str) -> list[Candidate]:
    s = _norm_text(snippet)
    if not s:
        return []

    doc = _doc_table(tomlkit.parse(toml_text))
    repo_type = _safe_str(doc.get("repo_type")).strip() or "normal"

    out: list[Candidate] = []

    desc = _norm_text(_safe_str(doc.get("description")))
    if desc and s in desc:
        out.append(Candidate(type="description", preview=_preview_line(desc), data={}))

    lecturers = _aot(doc.get("lecturers"))
    if lecturers:
        for lec in lecturers:
            if not isinstance(lec, Table):
                continue
            ln = _safe_str(lec.get("name")).strip() or "(未命名教师)"
            reviews = _aot(lec.get("reviews"))
            if not reviews:
                continue
            for ridx0, rv in enumerate(reviews):
                if not isinstance(rv, Table):
                    continue
                rc = _norm_text(_safe_str(rv.get("content")))
                if rc and s in rc:
                    out.append(
                        Candidate(
                            type="lecturer_review",
                            preview=_preview_line(rc),
                            data={"lecturer": ln, "review_index": ridx0},
                        )
                    )

    sections = _aot(doc.get("sections"))
    if sections:
        for sec in sections:
            if not isinstance(sec, Table):
                continue
            title = _safe_str(sec.get("title")).strip() or "(未命名章节)"
            items = _aot(sec.get("items"))
            if not items:
                continue
            for idx0, it in enumerate(items):
                if not isinstance(it, Table):
                    continue
                content = _norm_text(_safe_str(it.get("content")))
                if content and s in content:
                    out.append(
                        Candidate(
                            type="section_item",
                            preview=_preview_line(content),
                            data={"section": title, "index": idx0},
                        )
                    )

    if repo_type == "multi-project":
        courses = _aot(doc.get("courses"))
        if courses:
            for cidx0, c in enumerate(courses):
                if not isinstance(c, Table):
                    continue
                cname = _safe_str(c.get("name")).strip() or f"course#{cidx0+1}"

                teachers = _aot(c.get("teachers"))
                if teachers:
                    for t in teachers:
                        if not isinstance(t, Table):
                            continue
                        tn = _safe_str(t.get("name")).strip() or "(未命名教师)"
                        reviews = _aot(t.get("reviews"))
                        if reviews:
                            for ridx0, rv in enumerate(reviews):
                                if not isinstance(rv, Table):
                                    continue
                                rc = _norm_text(_safe_str(rv.get("content")))
                                if rc and s in rc:
                                    out.append(
                                        Candidate(
                                            type="course_teacher_review",
                                            preview=_preview_line(rc),
                                            data={
                                                "course_index": cidx0,
                                                "course_name": cname,
                                                "teacher": tn,
                                                "review_index": ridx0,
                                            },
                                        )
                                    )

                csecs = _aot(c.get("sections"))
                if csecs:
                    for sec in csecs:
                        if not isinstance(sec, Table):
                            continue
                        st = _safe_str(sec.get("title")).strip() or "(未命名章节)"
                        items = _aot(sec.get("items"))
                        if not items:
                            continue
                        for idx0, it in enumerate(items):
                            if not isinstance(it, Table):
                                continue
                            cc = _norm_text(_safe_str(it.get("content")))
                            if cc and s in cc:
                                out.append(
                                    Candidate(
                                        type="course_section_item",
                                        preview=_preview_line(cc),
                                        data={
                                            "course_index": cidx0,
                                            "course_name": cname,
                                            "section": st,
                                            "index": idx0,
                                        },
                                    )
                                )

    return out


def patch_paragraph(
    toml_text: str,
    *,
    candidate_type: str,
    candidate_data: dict[str, Any],
    old_paragraph: str,
    new_paragraph: str,
    author: dict[str, Any] | None = None,
) -> str:
    doc = _doc_table(tomlkit.parse(toml_text))
    s_old = _norm_text(old_paragraph)
    s_new = _norm_text(new_paragraph)
    if not s_old:
        raise ValueError("old_paragraph is empty")

    def _replace_in_text(value: str) -> str:
        cur = _norm_text(value)
        if s_old not in cur:
            raise ValueError("old_paragraph not found (content changed?)")
        return cur.replace(s_old, s_new, 1)

    t = (candidate_type or "").strip()

    if t == "description":
        desc = _safe_str(doc.get("description"))
        doc["description"] = tomlkit.string(_replace_in_text(desc), multiline=True)
        return tomlkit.dumps(doc).rstrip() + "\n"

    if t == "section_item":
        section = str(candidate_data.get("section") or "").strip()
        idx0 = int(candidate_data.get("index") or 0)
        sections = _aot(doc.get("sections"))
        if not sections:
            raise ValueError("sections missing")
        for sec in sections:
            if not isinstance(sec, Table):
                continue
            if _safe_str(sec.get("title")).strip() != section:
                continue
            items = _aot(sec.get("items"))
            if not items or idx0 < 0 or idx0 >= len(items):
                raise ValueError("item index out of range")
            it = items[idx0]
            if not isinstance(it, Table):
                raise ValueError("item must be a table")
            it["content"] = tomlkit.string(_replace_in_text(_safe_str(it.get("content"))), multiline=True)
            if author:
                _append_author_field(it, author)
            return tomlkit.dumps(doc).rstrip() + "\n"
        raise ValueError("section not found")

    if t == "lecturer_review":
        lecturer = str(candidate_data.get("lecturer") or "").strip()
        ridx0 = int(candidate_data.get("review_index") or 0)
        lecturers = _aot(doc.get("lecturers"))
        if not lecturers:
            raise ValueError("lecturers missing")
        for lec in lecturers:
            if not isinstance(lec, Table):
                continue
            if _safe_str(lec.get("name")).strip() != lecturer:
                continue
            reviews = _aot(lec.get("reviews"))
            if not reviews or ridx0 < 0 or ridx0 >= len(reviews):
                raise ValueError("review index out of range")
            rv = reviews[ridx0]
            if not isinstance(rv, Table):
                raise ValueError("review must be a table")
            rv["content"] = tomlkit.string(_replace_in_text(_safe_str(rv.get("content"))), multiline=True)
            if author:
                _append_author_field(rv, author)
            return tomlkit.dumps(doc).rstrip() + "\n"
        raise ValueError("lecturer not found")

    if t in {"course_teacher_review", "course_section_item"}:
        courses = _aot(doc.get("courses"))
        if not courses:
            raise ValueError("courses missing")
        cidx0 = int(candidate_data.get("course_index") or 0)
        if cidx0 < 0 or cidx0 >= len(courses):
            raise ValueError("course_index out of range")
        c = courses[cidx0]
        if not isinstance(c, Table):
            raise ValueError("course must be a table")

        if t == "course_teacher_review":
            teacher = str(candidate_data.get("teacher") or "").strip()
            ridx0 = int(candidate_data.get("review_index") or 0)
            teachers = _aot(c.get("teachers"))
            if not teachers:
                raise ValueError("teachers missing")
            for tt in teachers:
                if not isinstance(tt, Table):
                    continue
                if _safe_str(tt.get("name")).strip() != teacher:
                    continue
                reviews = _aot(tt.get("reviews"))
                if not reviews or ridx0 < 0 or ridx0 >= len(reviews):
                    raise ValueError("review index out of range")
                rv = reviews[ridx0]
                if not isinstance(rv, Table):
                    raise ValueError("review must be a table")
                rv["content"] = tomlkit.string(_replace_in_text(_safe_str(rv.get("content"))), multiline=True)
                if author:
                    _append_author_field(rv, author)
                return tomlkit.dumps(doc).rstrip() + "\n"
            raise ValueError("teacher not found")

        section = str(candidate_data.get("section") or "").strip()
        idx0 = int(candidate_data.get("index") or 0)
        sections = _aot(c.get("sections"))
        if not sections:
            raise ValueError("sections missing")
        for sec in sections:
            if not isinstance(sec, Table):
                continue
            if _safe_str(sec.get("title")).strip() != section:
                continue
            items = _aot(sec.get("items"))
            if not items or idx0 < 0 or idx0 >= len(items):
                raise ValueError("item index out of range")
            it = items[idx0]
            if not isinstance(it, Table):
                raise ValueError("item must be a table")
            it["content"] = tomlkit.string(_replace_in_text(_safe_str(it.get("content"))), multiline=True)
            if author:
                _append_author_field(it, author)
            return tomlkit.dumps(doc).rstrip() + "\n"
        raise ValueError("section not found")

    raise ValueError(f"unsupported candidate_type: {t}")
