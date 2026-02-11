"""Summarize readme.toml for UI.

The frontend/bot mainly needs:
- course_code/course_name/repo_type
- lecturer list / review counts
- final schema sections ([[sections]] + [[sections.items]]) with previews

We keep this tolerant: if parsing fails, we return minimal info.
"""

from __future__ import annotations

from typing import Any

import tomlkit
from tomlkit.items import AoT, Table


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _preview(text: str, *, limit: int = 80) -> str:
    s = (text or "").strip().replace("\r\n", "\n")
    if not s:
        return ""
    first = s.split("\n", 1)[0].strip()
    if len(first) > limit:
        return first[: limit - 1] + "…"
    return first


def summarize_toml(toml_text: str) -> dict[str, Any]:
    try:
        doc = tomlkit.parse(toml_text)
    except Exception:
        return {
            "meta": {"course_code": "", "course_name": "", "repo_type": ""},
            "sections": {},
        }

    meta = {
        "course_code": _safe_str(doc.get("course_code")),
        "course_name": _safe_str(doc.get("course_name")),
        "repo_type": _safe_str(doc.get("repo_type")),
    }

    sections: dict[str, Any] = {}

    # Scalar
    sections["description"] = {"preview": _preview(_safe_str(doc.get("description")))}

    # lecturers: name + reviews count
    lecturers_v = doc.get("lecturers")
    lecturers_items = []
    if isinstance(lecturers_v, AoT):
        for i, it in enumerate(lecturers_v):
            if not isinstance(it, Table):
                continue
            reviews_v = it.get("reviews")
            reviews_cnt = len(reviews_v) if isinstance(reviews_v, AoT) else 0
            lecturers_items.append(
                {
                    "index": i,
                    "label": _safe_str(it.get("name")),
                    "preview": f"{reviews_cnt} reviews",
                }
            )
    sections["lecturers"] = {"items": lecturers_items}

    # final schema: [[sections]] with [[sections.items]]
    sections_items: list[dict[str, Any]] = []
    sec_v = doc.get("sections")
    if isinstance(sec_v, AoT):
        for si, sec in enumerate(sec_v):
            if not isinstance(sec, Table):
                continue
            title = _safe_str(sec.get("title")).strip()
            items_v = sec.get("items")
            item_summaries: list[dict[str, Any]] = []
            if isinstance(items_v, AoT):
                for ii, it in enumerate(items_v):
                    if not isinstance(it, Table):
                        continue
                    pv = _preview(_safe_str(it.get("content")))
                    item_summaries.append({"index": ii, "preview": pv})

            sections_items.append(
                {
                    "index": si,
                    "label": title,
                    "preview": item_summaries[0]["preview"] if item_summaries else "",
                    "items": item_summaries,
                }
            )

    sections["sections"] = {"items": sections_items}

    return {"meta": meta, "sections": sections}
