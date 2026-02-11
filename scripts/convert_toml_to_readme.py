#!/usr/bin/env python3
"""Convert readme.toml to README.md.

- Supports both repo types:
  - normal: lecturers/textbooks/online_resources/course/exam/lab/advice/schedule/related_links/misc
  - multi-project: [[courses]] with nested reviews and teachers

Designed to work on exported TOMLs under final/<CODE>/readme.toml.
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


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
    path = _find_upwards(toml_path, "grades_summary.json")
    if not path:
        return {}
    cached = _GRADES_SUMMARY_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    _GRADES_SUMMARY_CACHE[path] = data
    return data


def _pick_grades_variant(entry: object) -> list[dict]:
    if isinstance(entry, list):
        return [x for x in entry if isinstance(x, dict)]
    if not isinstance(entry, dict):
        return []

    if "default" in entry and isinstance(entry.get("default"), list):
        return [x for x in entry.get("default") if isinstance(x, dict)]

    keys = [k for k in entry.keys() if isinstance(k, str)]
    preferred = [k for k in keys if "default" in k.lower()]
    pick_key = (sorted(preferred)[0] if preferred else (sorted(keys)[0] if keys else ""))
    if pick_key and isinstance(entry.get(pick_key), list):
        return [x for x in entry.get(pick_key) if isinstance(x, dict)]
    return []


def _render_grades_badges_from_items(items: list[dict]) -> list[str]:
    if not items:
        return []

    badges: list[str] = []
    badges.append(_render_shields_badge(alt="成绩构成", label="成绩构成", message=None, color="gold"))
    for it in items:
        name = _s(it.get("name")).strip()
        percent = _s(it.get("percent")).strip()
        if not name:
            continue
        alt = f"{name}{percent}" if percent else name
        badges.append(_render_shields_badge(alt=alt, label=name, message=percent or "", color="wheat"))

    return badges


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _s(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _md_escape_inline(text: str) -> str:
    # conservative escaping for headings / inline.
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_multiline_md(text: str) -> str:
    """Normalize multiline markdown stored in TOML triple-quoted strings.

    Many TOMLs indent the whole block (e.g. two spaces) for readability.
    Using plain `.strip()` would remove indentation from only the first line,
    leaving the rest misaligned in the generated README.
    """

    s = _s(text).replace("\r\n", "\n").replace("\r", "\n")
    s = textwrap.dedent(s)
    return s.strip()


def _as_author_list(author: object) -> list[dict]:
    if author is None:
        return []
    if isinstance(author, dict):
        return [author]
    if isinstance(author, list):
        return [a for a in author if isinstance(a, dict)]
    return []


def _render_one_author_quote(author: dict, *, indent: str = "") -> str:
    name = _s(author.get("name")).strip()
    link = _s(author.get("link")).strip()
    date = _s(author.get("date")).strip()

    # Skip rendering anonymous signatures.
    if name in {"佚名", "匿名"} and not link and not date:
        return ""
    if not name and date:
        name = "佚名"

    if link and name:
        name_part = f"[{name}]({link})"
    else:
        name_part = name

    if not name_part and not date:
        return ""

    suffix = f"{name_part}" if name_part else ""
    if date:
        suffix = (suffix + ", " if suffix else "") + date

    return f"{indent}> 文 / {suffix}"


def _render_author_quote(author: object, *, indent: str = "") -> str:
    authors = _as_author_list(author)
    if not authors:
        return ""
    sigs = [
        q
        for a in authors
        if (q := _render_one_author_quote(a, indent=indent))
    ]
    if not sigs:
        return ""
    if len(sigs) == 1:
        return sigs[0].rstrip()

    # A plain newline between two quote lines is *not* a visible line break in
    # Markdown rendering; it's treated as the same paragraph. We interleave an
    # empty quote line (">") so renderers show a real paragraph break.
    out: list[str] = []
    for i, q in enumerate(sigs):
        if i:
            out.append(f"{indent}>")
        out.append(q)
    return "\n".join(out).rstrip()


def _author_sig_key(author: object) -> tuple[tuple[str, str, str], ...]:
    authors = _as_author_list(author)
    if not authors:
        return tuple()
    key: list[tuple[str, str, str]] = []
    for a in authors:
        key.append(
            (
                _s(a.get("name")).strip(),
                _s(a.get("link")).strip(),
                _s(a.get("date")).strip(),
            )
        )
    return tuple(key)


def _render_author_quote_line(author: object, *, indent: str = "") -> str:
    """Author quote block without leading blank lines.

    Supports either:
    - author = { name, link, date }
    - author = [{...}, {...}]  (multiple lines in README)
    """
    q = _render_author_quote(author, indent=indent)
    # Preserve indentation; only trim right side / newlines.
    return q.rstrip() if q else ""


def _render_author_quote_inline(author: object) -> str:
    q = _render_author_quote_line(author)
    return ("\n\n" + q) if q else ""


def _encode_shields_component(text: str) -> str:
    """Encode a single shields.io path component.

    shields.io uses '-' as a delimiter; a literal '-' must be written as '--'.
    '%' must be percent-encoded to avoid breaking URLs.
    """

    s = _s(text).strip()
    if not s:
        return ""
    s = s.replace("-", "--")
    # Minimal escaping that preserves readable CJK while keeping URLs valid.
    s = s.replace("%", "%25")
    s = s.replace(" ", "%20")
    return s


def _render_shields_badge(*, alt: str, label: str, message: str | None = None, color: str | None = None) -> str:
    base = "https://img.shields.io/badge/"
    if message is None and color is not None:
        # Two-part variant: /badge/<label>-<message>
        path = f"{_encode_shields_component(label)}-{_encode_shields_component(color)}"
    else:
        msg = "" if message is None else message
        col = "brightgreen" if color is None else color
        path = (
            f"{_encode_shields_component(label)}-"
            f"{_encode_shields_component(msg)}-"
            f"{_encode_shields_component(col)}"
        )
    # Keep alt readable; URL part is encoded.
    return f"![{alt}]({base}{path})"


def _split_label_value_tail(text: str) -> tuple[str, str]:
    """Split a segment like '理论学时 32' into ('理论学时','32').

    Falls back to (text,'') when no obvious tail value exists.
    """

    s = _s(text).strip()
    if not s:
        return ("", "")
    parts = s.split()
    if len(parts) < 2:
        return (s, "")
    tail = parts[-1].strip()
    if re.fullmatch(r"\d+(?:\.\d+)?%?", tail):
        label = "".join(parts[:-1]).strip()
        return (label or s, tail)
    return (s, "")


def _render_basic_info_badges(content: str) -> list[str]:
    """Render a '基本信息' block into shields.io badges."""

    text = _normalize_multiline_md(content)
    if not text:
        return []

    kv: dict[str, str] = {}
    for ln in text.split("\n"):
        m = re.match(r"^\s*【(?P<k>[^】]+)】\s*[:：]\s*(?P<v>.*\S)\s*$", ln)
        if not m:
            continue
        kv[m.group("k").strip()] = m.group("v").strip()

    badges: list[str] = []

    def ensure_blank_sep():
        if badges and badges[-1] != "":
            badges.append("")

    credit = kv.get("学分")
    if credit:
        badges.append(
            _render_shields_badge(
                alt="学分",
                label="学分",
                message=credit,
                color="moccasin",
            )
        )

    hours = kv.get("学时构成")
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

    # Trim trailing blank.
    while badges and badges[-1] == "":
        badges.pop()
    return badges


def _render_block(content: str, author: object = None) -> str:
    content = _normalize_multiline_md(content)
    if not content:
        return ""
    if not author:
        return content
    q = _render_author_quote_line(author)
    if not q:
        return content
    # Ensure a blank line after blockquote to prevent lazy continuation.
    return content + "\n\n" + q + "\n\n"


_re_heading = re.compile(r"^#{1,6}\s+")
_re_list = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_re_ul_marker = re.compile(r"^(?P<indent>\s*)[*+]\s+")
_re_ul_dash = re.compile(r"^(?P<indent>\s*)-\s+")
_re_ol_marker = re.compile(r"^(?P<indent>\s*)(?P<num>\d+)(?P<delim>[.)])\s+")
_re_bare_url = re.compile(r"(?<!\()(?<!<)https?://[^\s>]+")
_re_bold_only = re.compile(r"^(?P<indent>\s*)(?:\*\*|__)(?P<text>.+?)(?:\*\*|__)\s*$")
_re_heading_level = re.compile(r"^(?P<marks>#{1,6})\s+")


def _normalize_markdownlint(md: str) -> str:
    """Normalize generated markdown to satisfy common markdownlint rules.

    Covers:
    - MD012: collapse multiple blank lines
    - MD022: blank line around headings
    - MD024: no duplicate headings (auto-disambiguate)
    - MD025: single H1 per doc (demote extra)
    - MD026: no trailing punctuation in headings
    - MD029: ordered list numbering 1/2/3...
    - MD030: exactly one space after list markers
    - MD032: blank line around lists (also inside blockquotes)
    - MD034: no bare URLs
    - MD036: bold-only line -> heading
    """

    md = md.replace("\r\n", "\n").replace("\r", "\n")
    src = [ln.rstrip() for ln in md.split("\n")]

    def is_blank(ln: str) -> bool:
        return ln.strip() == ""

    def split_blockquote_prefix(ln: str) -> tuple[str, str]:
        m = re.match(r"^(?P<prefix>\s*(?:>\s*)+)(?P<body>.*)$", ln)
        if not m:
            return ("", ln)
        return (m.group("prefix"), m.group("body"))

    def strip_blockquote(ln: str) -> str:
        return split_blockquote_prefix(ln)[1]

    def is_code_fence(ln: str) -> bool:
        return ln.strip().startswith("```")

    def is_heading(ln: str) -> bool:
        return bool(_re_heading.match(strip_blockquote(ln).strip()))

    def is_list(ln: str) -> bool:
        return bool(_re_list.match(strip_blockquote(ln)))

    def is_blankish(ln: str) -> bool:
        if is_blank(ln):
            return True
        # treat pure blockquote markers as blank lines within a quote
        return bool(re.fullmatch(r"\s*(?:>\s*)+\s*", ln))

    def strip_heading_trailing_punct(heading_line: str) -> str:
        s = heading_line.rstrip()
        m = _re_heading_level.match(s)
        if not m:
            return heading_line
        marks = m.group("marks")
        rest = s[m.end() :].rstrip()
        rest = rest.rstrip("：:。．，,；;！？!?、")
        return f"{marks} {rest}".rstrip()

    def leading_ws_len(s: str) -> int:
        # Avoid Optional[Match] typing issues from `re.match(...).group(...)`.
        # `lstrip()` removes all leading whitespace (spaces/tabs), so the delta is the prefix length.
        return len(s) - len(s.lstrip())

    out: list[str] = []
    in_code = False

    # State for list normalization.
    list_indent_stack: list[int] = []
    ol_counters: dict[str, int] = {}
    last_list_type: dict[str, str] = {}
    ol_marker_width: dict[str, int] = {}
    last_heading_level = 0
    prev_list: tuple[str, str, int, str] | None = None  # (quote_prefix, kind, indent_len, rest)

    i = 0
    while i < len(src):
        raw_src = src[i]
        # markdownlint counts indentation in spaces; some sources use tabs.
        # Preserve tabs inside fenced code blocks, but normalize elsewhere.
        raw = raw_src if in_code or is_code_fence(raw_src) else raw_src.expandtabs(4)

        if is_code_fence(raw_src):
            in_code = not in_code
            out.append(raw_src)
            i += 1
            continue

        if in_code:
            out.append(raw_src)
            i += 1
            continue

        if is_blank(raw):
            # Blank lines do NOT terminate lists in Markdown; keep list state so
            # ordered list numbering stays sequential across blank lines.
            out.append("")
            prev_list = None
            i += 1
            continue

        quote_prefix, body = split_blockquote_prefix(raw)

        # MD034: wrap bare URLs as <...>
        if "http://" in body or "https://" in body:
            body = _re_bare_url.sub(lambda m: f"<{m.group(0)}>", body)

        # MD036: bold-only line -> heading (only outside blockquote)
        if not quote_prefix:
            m_bold = _re_bold_only.match(body)
            if m_bold and m_bold.group("indent") == "":
                text = m_bold.group("text").strip()
                if text and not text.startswith("#") and not text.startswith(">") and not _re_list.match(text):
                    level = min(max(last_heading_level + 1, 2), 6)
                    body = "#" * level + " " + text

        # MD004 / MD030: normalize unordered list markers/spaces.
        m_ul = _re_ul_marker.match(body)
        if m_ul:
            body = f"{m_ul.group('indent')}- " + body[m_ul.end() :].lstrip()
        m_dash = _re_ul_dash.match(body)
        if m_dash:
            body = f"{m_dash.group('indent')}- " + body[m_dash.end() :].lstrip()

        # Drop orphan list markers (e.g. a line that is just "-"), which can
        # otherwise trigger MD007/MD032 and break list parsing.
        if re.fullmatch(r"\s*[-*+]\s*", body) or re.fullmatch(r"\s*\d+[.)]\s*", body):
            out.append(quote_prefix.rstrip() if quote_prefix else "")
            i += 1
            continue

        # MD005 / MD007: normalize indentation for list markers.
        # - For ordered lists, up to 3 leading spaces are treated as top-level.
        # - For nested lists, markdownlint expects 2-space indentation per level;
        #   many sources use 4 spaces, so reduce 4->2, 6->4, etc.
        m_ol_indent = _re_ol_marker.match(body)
        if m_ol_indent:
            indent_len = len(m_ol_indent.group("indent"))
            if indent_len <= 3:
                norm_indent = ""
            elif indent_len >= 4:
                norm_indent = " " * max(0, indent_len - 2)
            else:
                norm_indent = m_ol_indent.group("indent")
            body = (
                f"{norm_indent}{m_ol_indent.group('num')}{m_ol_indent.group('delim')} "
                + body[m_ol_indent.end() :].lstrip()
            )
        else:
            m_ul_indent = _re_ul_dash.match(body)
            if m_ul_indent:
                indent_len = len(m_ul_indent.group("indent"))
                rest = body[m_ul_indent.end() :].strip()
                ends_with_percent = bool(re.search(r"\b\d+%\s*$", rest))
                looks_like_percent_item = bool(re.search(r"[（(]\s*\d+%\s*[）)]\s*$", rest))

                indent_forced = False

                # Heuristic for score-breakdown lists (common in PE100X):
                # After a nested explanatory bullet, authors sometimes
                # accidentally keep a 2-space indent for the next top-level
                # score component, causing MD005/MD007. If we just saw a 2-space
                # nested bullet without a trailing percent, and the current 2-space
                # item *does* end with a percent, promote it to top-level.
                if (
                    indent_len == 2
                    and (ends_with_percent or looks_like_percent_item)
                    and prev_list is not None
                    and prev_list[0] == quote_prefix
                    and prev_list[1] == "ul"
                    and prev_list[2] == 2
                    and not (re.search(r"\b\d+%\s*$", prev_list[3]) or re.search(r"[（(]\s*\d+%\s*[）)]\s*$", prev_list[3]))
                ):
                    body = f"- " + body[m_ul_indent.end() :].lstrip()
                    indent_len = 0

                # Treat lightly-indented percent-items as top-level list entries.
                if indent_len <= 3 and looks_like_percent_item:
                    body = f"- " + body[m_ul_indent.end() :].lstrip()
                # If we're inside an ordered list item, nested unordered lists
                # must be indented by at least the ordered marker width (e.g.
                # "1. " => 3, "10. " => 4). A common mistake is using 2 spaces,
                # which markdownlint reads as an indented top-level list.
                if list_indent_stack:
                    # Find the nearest ordered-list ancestor.
                    for parent_indent_len in reversed(list_indent_stack):
                        parent_indent = " " * parent_indent_len
                        if last_list_type.get(parent_indent) != "ol" or parent_indent not in ol_marker_width:
                            continue
                        desired = parent_indent_len + ol_marker_width[parent_indent]
                        # Only treat as nested when it is already indented under that OL level.
                        if indent_len > parent_indent_len:
                            # If it's too shallow (e.g. 2 spaces under a "10."), promote it.
                            if indent_len < desired or indent_len != desired:
                                body = f"{' ' * desired}- " + body[m_ul_indent.end() :].lstrip()
                                indent_len = desired
                            indent_forced = True
                        break

                # Reduce common 4-space nested lists to markdownlint's 2-space style.
                if not indent_forced and indent_len >= 4:
                    norm_indent = " " * max(0, indent_len - 2)
                    body = f"{norm_indent}- " + body[m_ul_indent.end() :].lstrip()

        # Headings (only outside blockquote): ensure blank line before/after.
        if not quote_prefix and bool(_re_heading.match(body.strip())):
            list_indent_stack.clear()
            ol_counters.clear()
            last_list_type.clear()
            m_h = _re_heading_level.match(body.strip())
            if m_h:
                last_heading_level = len(m_h.group("marks"))
            if out and not is_blankish(out[-1]):
                out.append("")
            out.append(strip_heading_trailing_punct(body.strip()))
            if i + 1 < len(src) and not is_blank(src[i + 1]):
                out.append("")
            i += 1
            continue

        # MD007: dedent accidentally-indented top-level lists.
        if bool(_re_list.match(body)):
            indent_len = leading_ws_len(body)
            if indent_len > 0 and not list_indent_stack:
                body = body[indent_len:]
                indent_len = 0

            while list_indent_stack and indent_len < list_indent_stack[-1]:
                list_indent_stack.pop()
            if not list_indent_stack or indent_len > list_indent_stack[-1]:
                list_indent_stack.append(indent_len)
        else:
            # Keep list context across indented continuation lines inside list items.
            if list_indent_stack:
                cont_indent = leading_ws_len(body)
                if cont_indent < list_indent_stack[-1] + 2:
                    list_indent_stack.clear()

        # MD029 / MD030: normalize ordered list numbering and spacing.
        m_ol = _re_ol_marker.match(body)
        if m_ol:
            indent = m_ol.group("indent")
            delim = m_ol.group("delim")
            # Track ordered marker width for nested-list indentation.
            ol_marker_width[indent] = len(m_ol.group("num")) + 2  # e.g. "1. " => 3, "10. " => 4
            if last_list_type.get(indent) != "ol":
                ol_counters[indent] = 1
            else:
                ol_counters[indent] = ol_counters.get(indent, 0) + 1
            last_list_type[indent] = "ol"
            body = f"{indent}{ol_counters[indent]}{delim} " + body[m_ol.end() :].lstrip()
        elif bool(_re_list.match(body)):
            indent = body[: leading_ws_len(body)]
            last_list_type[indent] = "ul"
            ol_counters.pop(indent, None)
            ol_marker_width.pop(indent, None)
        else:
            ol_counters.clear()
            last_list_type.clear()
            ol_marker_width.clear()

        # MD032: blank line before list blocks.
        if bool(_re_list.match(body)):
            if out and not is_blankish(out[-1]) and not is_heading(out[-1]) and not is_list(out[-1]):
                out.append(quote_prefix.rstrip() if quote_prefix else "")
            out.append((quote_prefix + body).rstrip())
            m_ol_prev = _re_ol_marker.match(body)
            if m_ol_prev:
                prev_list = (quote_prefix, "ol", len(m_ol_prev.group("indent")), body[m_ol_prev.end() :].strip())
            else:
                m_ul_prev = _re_ul_dash.match(body)
                if m_ul_prev:
                    prev_list = (quote_prefix, "ul", len(m_ul_prev.group("indent")), body[m_ul_prev.end() :].strip())
                else:
                    prev_list = None
            i += 1
            continue

        out.append((quote_prefix + body).rstrip())
        prev_list = None
        i += 1

    # Ensure blank line after list blocks (including within blockquotes)
    out2: list[str] = []
    in_code = False
    i = 0
    while i < len(out):
        ln = out[i]
        if is_code_fence(ln):
            in_code = not in_code
            out2.append(ln)
            i += 1
            continue
        if in_code:
            out2.append(ln)
            i += 1
            continue

        out2.append(ln)
        if is_list(ln):
            j = i + 1
            while j < len(out) and is_blankish(out[j]):
                j += 1
            if j < len(out) and not is_list(out[j]):
                if i + 1 < len(out) and not is_blankish(out[i + 1]):
                    q_prefix, _ = split_blockquote_prefix(ln)
                    out2.append(q_prefix.rstrip() if q_prefix else "")
        i += 1

    # Collapse multiple blank lines to single blank line (treat quote-blank as blank)
    collapsed: list[str] = []
    blank_run = 0
    for ln in out2:
        if is_blankish(ln):
            blank_run += 1
            if blank_run > 1:
                continue
            # preserve quote-blank lines as plain blank for simplicity
            collapsed.append(ln if ln.strip().startswith(">") else "")
        else:
            blank_run = 0
            collapsed.append(ln)

    while collapsed and is_blankish(collapsed[0]):
        collapsed.pop(0)
    while collapsed and is_blankish(collapsed[-1]):
        collapsed.pop()

    # Post-pass headings: MD025 + MD024 + MD026
    final_lines: list[str] = []
    heading_counts: dict[str, int] = {}
    have_h1 = False
    in_code = False
    for ln in collapsed:
        if is_code_fence(ln):
            in_code = not in_code
            final_lines.append(ln)
            continue
        if in_code:
            final_lines.append(ln)
            continue

        q_prefix, body = split_blockquote_prefix(ln)
        s = body.strip()
        m = _re_heading_level.match(s)
        if m and not q_prefix:
            # normalize punctuation
            s2 = strip_heading_trailing_punct(s)
            m2 = _re_heading_level.match(s2)
            marks = m2.group("marks") if m2 else m.group("marks")
            level = len(marks)
            text = (s2[m2.end() :] if m2 else s[m.end() :]).strip()

            if level == 1:
                if have_h1:
                    level = 2
                else:
                    have_h1 = True

            heading_counts[text] = heading_counts.get(text, 0) + 1
            if heading_counts[text] > 1:
                text = f"{text}（{heading_counts[text]}）"

            final_lines.append("#" * level + " " + text)
        else:
            final_lines.append(ln)

    return "\n".join(final_lines).rstrip() + "\n"


def _render_content_only(content: str) -> str:
    return _normalize_multiline_md(content)


def _split_nonempty_lines(text: str) -> list[str]:
    text = _s(text).replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        # normalize leading list markers
        line = re.sub(r"^[-*\u2022]\s+", "", line)
        lines.append(line)
    return lines


def _render_section_items(title: str, items: list[dict], *, topic_key: str | None = None) -> str:
    if not items:
        return ""
    out: list[str] = [f"## {title}", ""]
    pending_sig: tuple[str, str, str] | None = None
    pending_author: object = None

    def flush_sig():
        nonlocal pending_sig, pending_author
        if pending_sig and any(pending_sig):
            aq = _render_author_quote_line(pending_author)
            if aq:
                out.append(aq)
                # Prevent lazy continuation lines from becoming part of the blockquote.
                out.append("")
        pending_sig = None
        pending_author = None

    for item in items:
        if not isinstance(item, dict):
            continue
        topic = _s(item.get(topic_key)).strip() if topic_key else ""
        content = _s(item.get("content"))
        author = item.get("author")

        sig = _author_sig_key(author)
        if pending_sig is None:
            pending_sig = sig
            pending_author = author
        elif sig != pending_sig:
            flush_sig()
            pending_sig = sig
            pending_author = author

        if topic:
            out.append(f"### {topic}")
            out.append("")
        block = _render_content_only(content)
        if block:
            out.append(block)
        out.append("")

    flush_sig()
    return "\n".join(out).rstrip() + "\n"


def render_normal(data: dict, *, grades_summary: dict | None = None) -> str:
    course_name = _md_escape_inline(_s(data.get("course_name")))
    course_code = _md_escape_inline(_s(data.get("course_code")))
    description = _normalize_multiline_md(_s(data.get("description")))

    lines: list[str] = []
    title_has_code = False
    if course_code and course_name:
        lines.append(f"# {course_code} - {course_name}")
        title_has_code = True
    else:
        lines.append(f"# {course_name or course_code or '课程'}")
    if course_code and not title_has_code:
        lines.append("")
        lines.append(f"**课程代码：** {course_code}")

    # Optional: insert grading summary badges (from grades_summary.json) near the top,
    # above the description block.
    if grades_summary and course_code:
        entry = grades_summary.get(course_code)
        items = _pick_grades_variant(entry)
        badges = _render_grades_badges_from_items(items)
        if badges:
            lines.append("")
            lines.extend(badges)

    if description:
        lines.append("")
        lines.append(description)

    lecturers = _as_list(data.get("lecturers"))
    if lecturers:
        lines.append("")
        lines.append("## 授课教师")
        lines.append("")
        for lec in lecturers:
            if not isinstance(lec, dict):
                continue
            name = _md_escape_inline(_s(lec.get("name")).strip())
            if not name:
                continue
            lines.append(f"- {name}")
            reviews = _as_list(lec.get("reviews"))
            for rv in reviews:
                if not isinstance(rv, dict):
                    continue
                content = _s(rv.get("content")).strip()
                author = rv.get("author")
                content_lines = _split_nonempty_lines(content)
                for ln in content_lines:
                    lines.append(f"  - {ln}")
                if content_lines:
                    aq = _render_author_quote_line(author, indent="  ")
                    if aq:
                        lines.append(aq)
                        # Keep the following lines out of the blockquote (CommonMark lazy continuation).
                        lines.append("  ")

    textbooks = _as_list(data.get("textbooks"))
    if textbooks:
        lines.append("")
        lines.append("## 教材")
        for tb in textbooks:
            if not isinstance(tb, dict):
                continue
            title = _s(tb.get("title")).strip()
            if not title:
                continue
            book_author = _s(tb.get("book_author")).strip()
            publisher = _s(tb.get("publisher")).strip()
            edition = _s(tb.get("edition")).strip()
            tb_type = _s(tb.get("type")).strip()
            meta = " / ".join([x for x in [book_author, publisher, edition, tb_type] if x])
            if meta:
                lines.append(f"- **{title}**（{meta}）")
            else:
                lines.append(f"- **{title}**")

    online = _as_list(data.get("online_resources"))
    if online:
        lines.append("")
        lines.append("## 在线资源")
        lines.append("")

        for r in online:
            if not isinstance(r, dict):
                continue
            title = _s(r.get("title")).strip() or _s(r.get("url")).strip()
            url = _s(r.get("url")).strip()
            desc = _s(r.get("description")).strip()
            if not title and not url:
                continue

            if url:
                lines.append(f"- [{title}]({url})" + (f"：{desc}" if desc else ""))
            else:
                lines.append(f"- {title}" + (f"：{desc}" if desc else ""))

    # Standard content blocks
    for key, title in [
        ("course", "课程内容"),
        ("exam", "考核/考试"),
        ("lab", "实验/作业"),
        ("advice", "选课建议"),
        ("schedule", "课程安排"),
    ]:
        section = _render_section_items(title, _as_list(data.get(key)))
        if section:
            lines.append("")
            lines.append(section.rstrip())

    # related_links: do not render signatures
    related = _as_list(data.get("related_links"))
    if related:
        lines.append("")
        lines.append("## 相关链接")
        lines.append("")
        for item in related:
            if not isinstance(item, dict):
                continue
            content = _s(item.get("content")).strip()
            if not content:
                continue
            # Try to make it a list for readability
            for ln in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                lns = ln.strip()
                if not lns:
                    continue
                lines.append(f"- {lns}")

    misc = _as_list(data.get("misc"))
    misc_section = _render_section_items("其他", misc, topic_key="topic")
    if misc_section:
        lines.append("")
        lines.append(misc_section.rstrip())

    return "\n".join(lines).rstrip() + "\n"


def render_multi_project(data: dict) -> str:
    course_name = _md_escape_inline(_s(data.get("course_name")))
    course_code = _md_escape_inline(_s(data.get("course_code")))
    description = _normalize_multiline_md(_s(data.get("description")))

    lines: list[str] = []
    title_has_code = False
    if course_code and course_name:
        lines.append(f"# {course_code} - {course_name}")
        title_has_code = True
    else:
        lines.append(f"# {course_name or course_code or '课程集合'}")
    if course_code and not title_has_code:
        lines.append("")
        lines.append(f"**课程代码：** {course_code}")

    if description:
        lines.append("")
        lines.append(description)

    courses = _as_list(data.get("courses"))
    if courses:
        lines.append("")
        lines.append("## 课程列表")
        lines.append("")
        for c in courses:
            if not isinstance(c, dict):
                continue
            name = _md_escape_inline(_s(c.get("name")).strip())
            code = _md_escape_inline(_s(c.get("code")).strip())
            header = ""
            if code and name:
                header = f"{code} - {name}"
            else:
                header = name or code
            if not header:
                continue

            # Extract '基本信息' badges and render them near the course title.
            basic_info_badges: list[str] = []
            reviews_all = _as_list(c.get("reviews"))
            reviews: list[dict] = []
            for rv in reviews_all:
                if not isinstance(rv, dict):
                    continue
                topic_raw = _s(rv.get("topic")).strip()
                if topic_raw == "基本信息" and not basic_info_badges:
                    basic_info_badges = _render_basic_info_badges(_s(rv.get("content")))
                    continue
                reviews.append(rv)

            lines.append("")
            lines.append(f"### {header}")
            # Title already includes code when available; keep body concise.

            if basic_info_badges:
                lines.append("")
                lines.extend(basic_info_badges)

            # teachers
            teachers = _as_list(c.get("teachers"))
            if teachers:
                lines.append("")
                lines.append(f"#### {header} - 授课教师")
                lines.append("")
                for t in teachers:
                    if not isinstance(t, dict):
                        continue
                    tname = _md_escape_inline(_s(t.get("name")).strip())
                    if not tname:
                        continue
                    lines.append(f"- {tname}")
                    treviews = _as_list(t.get("reviews"))
                    for rv in treviews:
                        if not isinstance(rv, dict):
                            continue
                        content = _s(rv.get("content")).strip()
                        author = rv.get("author")
                        content_lines = _split_nonempty_lines(content)
                        for ln in content_lines:
                            lines.append(f"  - {ln}")
                        if content_lines:
                            aq = _render_author_quote_line(author, indent="  ")
                            if aq:
                                lines.append(aq)
                                lines.append("  ")

            if reviews:
                lines.append("")
                lines.append(f"#### {header} - 课程评价")
                lines.append("")
                for rv in reviews:
                    if not isinstance(rv, dict):
                        continue
                    topic = _md_escape_inline(_s(rv.get("topic")).strip())
                    content = _s(rv.get("content"))
                    author = rv.get("author")
                    if topic:
                        lines.append("")
                        lines.append(f"##### {header} - {topic}")
                        lines.append("")
                    block = _render_block(content, author)
                    if block:
                        lines.append(block)

    misc = _as_list(data.get("misc"))
    # multi-project 的 misc 有时没有 topic
    if misc:
        lines.append("")
        lines.append("## 其他")
        for item in misc:
            if not isinstance(item, dict):
                continue
            topic = _md_escape_inline(_s(item.get("topic")).strip())
            content = _s(item.get("content"))
            author = item.get("author")
            if topic:
                lines.append("")
                lines.append(f"### {topic}")
            block = _render_block(content, author)
            if block:
                lines.append(block)

    return "\n".join(lines).rstrip() + "\n"


def render_readme_from_toml_path(toml_path: Path) -> str:
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    repo_type = _s(data.get("repo_type")).strip().lower()
    grades_summary = _load_grades_summary(toml_path)
    if repo_type == "multi-project":
        return render_multi_project(data)
    return render_normal(data, grades_summary=grades_summary)


def _default_out_path(input_path: Path) -> Path:
    # - final/<CODE>/readme.toml => final/<CODE>/README.md
    # - some_dir/CrossSpecialty.toml => some_dir/CrossSpecialty_README.md
    if input_path.name.lower() == "readme.toml":
        return input_path.with_name("README.md")
    return input_path.with_name(f"{input_path.stem}_README.md")


def convert_one(input_path: Path, output_path: Path, *, overwrite: bool) -> None:
    md = render_readme_from_toml_path(input_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {output_path} (use --overwrite)")
    md = _normalize_markdownlint(md)
    output_path.write_text(md, encoding="utf-8")


def iter_tomls(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    # default scan: find readme.toml first, else *.toml
    readmes = sorted(root.rglob("readme.toml"))
    if readmes:
        return readmes
    return sorted(root.rglob("*.toml"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert readme.toml to README.md (normal & multi-project).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--input",
        "-i",
        help="Input TOML file or directory (e.g., final or final/AUTO3006/readme.toml)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="One-click: convert all exported TOMLs under ./final/**/readme.toml to ./final/**/README.md",
    )
    parser.add_argument("--output", "-o", help="Output README path (only for single input file)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing README.md")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files; just print which would be generated")
    parser.add_argument("--quiet", action="store_true", help="Reduce per-file output; print only summary")

    args = parser.parse_args()
    in_path = Path("final") if args.all else Path(args.input)
    if not in_path.exists():
        raise FileNotFoundError(in_path)

    toml_paths = iter_tomls(in_path)
    if not toml_paths:
        print("No TOML files found.")
        return 1

    if args.output and len(toml_paths) != 1:
        raise ValueError("--output can only be used when --input points to a single TOML file")

    wrote = 0
    skipped = 0

    for p in toml_paths:
        out = Path(args.output) if args.output else _default_out_path(p)
        if args.dry_run:
            if not args.quiet:
                print(f"{p} -> {out}")
            continue
        try:
            convert_one(p, out, overwrite=args.overwrite)
        except FileExistsError:
            skipped += 1
            if not args.quiet:
                print(f"Skip {out} (exists)")
            continue
        wrote += 1
        if not args.quiet:
            print(f"Wrote {out}")

    if args.quiet and not args.dry_run:
        print(f"Wrote {wrote} file(s), skipped {skipped} (exists).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
