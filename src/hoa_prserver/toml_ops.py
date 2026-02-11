"""TOML patch operations.

This module provides small, safe, structured edits for readme.toml so clients
(bot / web) don't need to POST the whole TOML text for common changes.

Targets the final schema:
- top-level: course_name/repo_type/course_code/description
- lecturers: [[lecturers]] with optional [[lecturers.reviews]]
- sections: [[sections]] with [[sections.items]]

We use tomlkit to preserve a reasonable TOML layout and to support arrays of
inline tables for `author`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

import tomlkit
from tomlkit.items import AoT, Array, InlineTable, Table


@dataclass(frozen=True)
class Author:
    name: str
    link: str
    date: str


@dataclass(frozen=True)
class AddLecturerReview:
    lecturer_name: str
    content: str
    author: Author


@dataclass(frozen=True)
class AppendExamLine:
    index: int
    line: str
    author: Author


@dataclass(frozen=True)
class SetDescription:
    content: str


@dataclass(frozen=True)
class AppendSectionItem:
    section: str
    item: dict[str, Any]


@dataclass(frozen=True)
class UpdateSectionItem:
    section: str
    index: int
    fields: dict[str, Any]


_T = AddLecturerReview | AppendExamLine | SetDescription | AppendSectionItem | UpdateSectionItem


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


def _author_item(author: Author) -> InlineTable:
    t = tomlkit.inline_table()
    t["name"] = (author.name or "").strip()
    t["link"] = (author.link or "").strip()
    t["date"] = _normalize_date(author.date)
    return t


def _ensure_aot(doc: Table, key: str) -> AoT:
    existing = doc.get(key)
    if existing is None:
        new = tomlkit.aot()
        doc[key] = new
        return new
    if not isinstance(existing, AoT):
        raise ValueError(f"{key} must be an array of tables")
    return existing


def _ensure_table_aot_field(parent: Table, key: str) -> AoT:
    existing = parent.get(key)
    if existing is None:
        new = tomlkit.aot()
        parent[key] = new
        return new
    if not isinstance(existing, AoT):
        raise ValueError(f"{key} must be an array of tables")
    return existing


def _append_author(target: Table, author: Author) -> None:
    """Append author to target['author'].

    Supports:
    - missing -> inline table
    - inline table -> array of inline tables
    - array -> append
    """

    new_item = _author_item(author)
    existing = target.get("author")
    if existing is None:
        target["author"] = new_item
        return

    if isinstance(existing, Array):
        existing.append(new_item)
        return

    if isinstance(existing, (InlineTable, dict)):
        arr = tomlkit.array()
        arr.multiline(True)
        arr.append(existing)
        arr.append(new_item)
        target["author"] = arr
        return

    # Fallback: replace.
    target["author"] = new_item


def _find_or_create_lecturer(doc: Table, *, lecturer_name: str) -> Table:
    lecturers = _ensure_aot(doc, "lecturers")
    for it in lecturers:
        if isinstance(it, Table) and str(it.get("name") or "").strip() == lecturer_name:
            return it

    t = tomlkit.table()
    t["name"] = lecturer_name
    lecturers.append(t)
    return t


def _add_lecturer_review(doc: Table, op: AddLecturerReview) -> None:
    lecturer_name = op.lecturer_name.strip()
    if not lecturer_name:
        raise ValueError("lecturer_name is required")

    lecturer = _find_or_create_lecturer(doc, lecturer_name=lecturer_name)
    reviews = _ensure_table_aot_field(lecturer, "reviews")

    rv = tomlkit.table()
    rv["content"] = tomlkit.string(op.content.rstrip("\n"), multiline=True)
    rv["author"] = _author_item(op.author)
    reviews.append(rv)


def _append_exam_line(doc: Table, op: AppendExamLine) -> None:
    exams = _ensure_aot(doc, "exam")
    if op.index < 0 or op.index >= len(exams):
        raise ValueError(f"exam index out of range: {op.index}")

    ex = exams[op.index]
    if not isinstance(ex, Table):
        raise ValueError("exam item must be a table")

    content_v = ex.get("content")
    content = "" if content_v is None else str(content_v)
    content = content.rstrip("\n")
    line = op.line.rstrip("\n")
    if not line:
        raise ValueError("line is required")

    new_content = f"{content}\n{line}" if content else line
    ex["content"] = tomlkit.string(new_content, multiline=True)

    _append_author(ex, op.author)


def _toml_string(value: str) -> Any:
    s = (value or "").rstrip("\n")
    if "\n" in s:
        return tomlkit.string(s, multiline=True)
    return s


def _set_description(doc: Table, op: SetDescription) -> None:
    doc["description"] = tomlkit.string(op.content.rstrip("\n"), multiline=True)


def _sections_aot(doc: Table) -> AoT:
    return _ensure_aot(doc, "sections")


def _find_section(doc: Table, *, title: str) -> Table | None:
    title = (title or "").strip()
    if not title:
        return None
    for it in _sections_aot(doc):
        if isinstance(it, Table) and str(it.get("title") or "").strip() == title:
            return it
    return None


def _ensure_section(doc: Table, *, title: str) -> Table:
    title = (title or "").strip()
    if not title:
        raise ValueError("section title is required")

    existing = _find_section(doc, title=title)
    if existing is not None:
        return existing

    t = tomlkit.table()
    t["title"] = title
    t["items"] = tomlkit.aot()
    _sections_aot(doc).append(t)
    return t


def _ensure_items_aot(section: Table) -> AoT:
    existing = section.get("items")
    if existing is None:
        new = tomlkit.aot()
        section["items"] = new
        return new
    if not isinstance(existing, AoT):
        raise ValueError("sections.items must be an array of tables")
    return existing


def _section_item_table(doc: Table, *, section_title: str, item_index: int) -> Table:
    sec = _find_section(doc, title=section_title)
    if sec is None:
        raise ValueError(f"section not found: {section_title}")
    items = _ensure_items_aot(sec)
    if item_index < 0 or item_index >= len(items):
        raise ValueError(f"section item index out of range: {item_index}")
    t = items[item_index]
    if not isinstance(t, Table):
        raise ValueError("section item must be a table")
    return t


def _append_section_item(doc: Table, op: AppendSectionItem) -> None:
    section_title = op.section.strip()
    if not section_title:
        raise ValueError("section is required")

    sec = _ensure_section(doc, title=section_title)
    items = _ensure_items_aot(sec)
    t = tomlkit.table()

    for k, v in op.item.items():
        if k == "author":
            if isinstance(v, dict):
                t["author"] = _author_item(
                    Author(
                        name=str(v.get("name") or ""),
                        link=str(v.get("link") or ""),
                        date=str(v.get("date") or ""),
                    )
                )
                continue
            if isinstance(v, list):
                arr = tomlkit.array()
                arr.multiline(True)
                for one in v:
                    if isinstance(one, dict):
                        arr.append(
                            _author_item(
                                Author(
                                    name=str(one.get("name") or ""),
                                    link=str(one.get("link") or ""),
                                    date=str(one.get("date") or ""),
                                )
                            )
                        )
                t["author"] = arr
                continue
        if k == "content" and isinstance(v, str):
            t["content"] = _toml_string(v)
            continue
        t[k] = v

    items.append(t)


def _update_section_item(doc: Table, op: UpdateSectionItem) -> None:
    section_title = op.section.strip()
    if not section_title:
        raise ValueError("section is required")

    t = _section_item_table(doc, section_title=section_title, item_index=op.index)
    for k, v in op.fields.items():
        if k == "author":
            if isinstance(v, dict):
                _append_author(
                    t,
                    Author(
                        name=str(v.get("name") or ""),
                        link=str(v.get("link") or ""),
                        date=str(v.get("date") or ""),
                    ),
                )
                continue
            if isinstance(v, list):
                for one in v:
                    if not isinstance(one, dict):
                        continue
                    _append_author(
                        t,
                        Author(
                            name=str(one.get("name") or ""),
                            link=str(one.get("link") or ""),
                            date=str(one.get("date") or ""),
                        ),
                    )
                continue
        if k == "content" and isinstance(v, str):
            t["content"] = _toml_string(v)
            continue
        t[k] = v


def apply_ops(toml_text: str, ops: Iterable[_T]) -> str:
    doc = tomlkit.parse(toml_text)

    for op in ops:
        if isinstance(op, AddLecturerReview):
            _add_lecturer_review(doc, op)
        elif isinstance(op, AppendExamLine):
            _append_exam_line(doc, op)
        elif isinstance(op, SetDescription):
            _set_description(doc, op)
        elif isinstance(op, AppendSectionItem):
            _append_section_item(doc, op)
        elif isinstance(op, UpdateSectionItem):
            _update_section_item(doc, op)
        else:  # pragma: no cover
            raise ValueError(f"unsupported op: {op!r}")

    return tomlkit.dumps(doc).rstrip() + "\n"


def parse_ops(payload: list[dict[str, Any]]) -> list[_T]:
    """Parse ops from JSON dicts.

    We intentionally keep this module free from Pydantic models so it can be used
    from both API and background tasks.
    """

    out: list[_T] = []
    for it in payload:
        if not isinstance(it, dict):
            raise ValueError("op must be an object")
        op_type = str(it.get("op") or "").strip()

        if op_type == "add_lecturer_review":
            author = it.get("author")
            if not isinstance(author, dict):
                raise ValueError("author is required")
            out.append(
                AddLecturerReview(
                    lecturer_name=str(it.get("lecturer_name") or ""),
                    content=str(it.get("content") or ""),
                    author=Author(
                        name=str(author.get("name") or ""),
                        link=str(author.get("link") or ""),
                        date=str(author.get("date") or ""),
                    ),
                )
            )
            continue

        if op_type == "append_exam_line":
            author = it.get("author")
            if not isinstance(author, dict):
                raise ValueError("author is required")
            out.append(
                AppendExamLine(
                    index=int(it.get("index") or 0),
                    line=str(it.get("line") or ""),
                    author=Author(
                        name=str(author.get("name") or ""),
                        link=str(author.get("link") or ""),
                        date=str(author.get("date") or ""),
                    ),
                )
            )
            continue

        if op_type == "set_description":
            out.append(SetDescription(content=str(it.get("content") or "")))
            continue

        if op_type == "append_section_item":
            item = it.get("item")
            if not isinstance(item, dict):
                raise ValueError("item is required")
            out.append(AppendSectionItem(section=str(it.get("section") or ""), item=item))
            continue

        if op_type == "update_section_item":
            fields = it.get("fields")
            if not isinstance(fields, dict):
                raise ValueError("fields is required")
            out.append(
                UpdateSectionItem(
                    section=str(it.get("section") or ""),
                    index=int(it.get("index") or 0),
                    fields=fields,
                )
            )
            continue

        raise ValueError(f"unknown op: {op_type}")

    return out
