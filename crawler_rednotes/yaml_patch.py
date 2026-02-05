from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple


def _quote_yaml_scalar(value: str) -> str:
    s = str(value or "")
    if s == "":
        return "''"
    # quote if contains special chars or leading/trailing spaces
    if any(ch in s for ch in [":", "#", "{", "}", "[", "]", "\n", "\r", "\t"]) or s.strip() != s:
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return f"\"{s}\""
    return s


def _find_block(lines: List[str], key: str) -> Optional[Tuple[int, int, int]]:
    """
    Find a top-level or nested mapping block by key.
    Returns (key_line_index, block_start_index, indent) where block_start is the first line after the key.
    """
    key_re = re.compile(rf"^(?P<indent>\s*){re.escape(key)}\s*:\s*(#.*)?$")
    for i, ln in enumerate(lines):
        m = key_re.match(ln)
        if not m:
            continue
        indent = len(m.group("indent") or "")
        return i, i + 1, indent
    return None


def set_scalar(path: str, key_path: List[str], value: str) -> bool:
    """
    Set a scalar value in YAML by best-effort in-place patching, preserving unrelated lines/comments.
    Supports only nested mappings (no complex YAML features).
    Returns True if updated/inserted.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

    parent_indent = 0
    start = 0
    end = len(lines)

    # Walk down blocks for all but last key
    for depth, key in enumerate(key_path[:-1]):
        # Search within current slice only
        key_re = re.compile(rf"^(?P<indent>\s*){re.escape(key)}\s*:\s*(#.*)?$")
        found = None
        for i in range(start, end):
            m = key_re.match(lines[i].rstrip("\r\n"))
            if not m:
                continue
            indent = len(m.group("indent") or "")
            if indent < parent_indent:
                continue
            found = (i, indent)
            break
        if not found:
            # Insert missing mapping key at end of current block
            insert_at = end
            new_indent = " " * parent_indent
            lines.insert(insert_at, f"{new_indent}{key}:\n")
            found = (insert_at, parent_indent)
            end += 1

        key_line, indent = found
        child_indent = indent + 2
        # Determine block extent (until indent <= current indent)
        j = key_line + 1
        while j < len(lines):
            ln = lines[j].rstrip("\r\n")
            if not ln.strip():
                j += 1
                continue
            cur_indent = len(ln) - len(ln.lstrip(" "))
            if cur_indent <= indent:
                break
            j += 1
        start = key_line + 1
        end = j
        parent_indent = child_indent

    leaf = key_path[-1]
    leaf_re = re.compile(rf"^(?P<indent>\s*){re.escape(leaf)}\s*:\s*(?P<rest>.*)$")
    for i in range(start, end):
        raw = lines[i].rstrip("\r\n")
        if raw.lstrip().startswith("#"):
            continue
        m = leaf_re.match(raw)
        if not m:
            continue
        indent = m.group("indent") or ""
        # Replace entire line (preserve trailing comment if any)
        rest = m.group("rest") or ""
        comment = ""
        if "#" in rest:
            # keep inline comment
            comment = " #" + rest.split("#", 1)[1].strip()
        new_line = f"{indent}{leaf}: {_quote_yaml_scalar(value)}{comment}\n"
        lines[i] = new_line
        p.write_text("".join(lines), encoding="utf-8")
        return True

    # Not found -> insert near end (before end)
    indent_str = " " * (parent_indent if key_path[:-1] else 0)
    insert_at = end
    lines.insert(insert_at, f"{indent_str}{leaf}: {_quote_yaml_scalar(value)}\n")
    p.write_text("".join(lines), encoding="utf-8")
    return True


def set_block_scalar(path: str, key_path: List[str], text: str) -> bool:
    """
    Set a YAML block scalar (|) value by best-effort in-place patching.
    Useful for keeping long prompts readable without rewriting the whole file.

    Only supports nested mappings (no complex YAML features).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

    parent_indent = 0
    start = 0
    end = len(lines)

    # Walk down blocks for all but last key
    for key in key_path[:-1]:
        key_re = re.compile(rf"^(?P<indent>\s*){re.escape(key)}\s*:\s*(#.*)?$")
        found = None
        for i in range(start, end):
            m = key_re.match(lines[i].rstrip("\r\n"))
            if not m:
                continue
            indent = len(m.group("indent") or "")
            if indent < parent_indent:
                continue
            found = (i, indent)
            break
        if not found:
            insert_at = end
            new_indent = " " * parent_indent
            lines.insert(insert_at, f"{new_indent}{key}:\n")
            found = (insert_at, parent_indent)
            end += 1

        key_line, indent = found
        child_indent = indent + 2
        j = key_line + 1
        while j < len(lines):
            ln = lines[j].rstrip("\r\n")
            if not ln.strip():
                j += 1
                continue
            cur_indent = len(ln) - len(ln.lstrip(" "))
            if cur_indent <= indent:
                break
            j += 1
        start = key_line + 1
        end = j
        parent_indent = child_indent

    leaf = key_path[-1]
    leaf_re = re.compile(rf"^(?P<indent>\s*){re.escape(leaf)}\s*:\s*(?P<rest>.*)$")

    def make_block(indent_str: str) -> List[str]:
        ind2 = indent_str + "  "
        body = (text or "").rstrip("\r\n")
        out: List[str] = [f"{indent_str}{leaf}: |\n"]
        if body:
            for ln in body.splitlines():
                out.append(f"{ind2}{ln}\n")
        else:
            out.append(f"{ind2}\n")
        return out

    # Replace existing
    for i in range(start, end):
        raw = lines[i].rstrip("\r\n")
        if raw.lstrip().startswith("#"):
            continue
        m = leaf_re.match(raw)
        if not m:
            continue
        indent_str = m.group("indent") or ""
        indent_len = len(indent_str)
        # Determine how many following lines belong to this value (quoted multiline or existing block scalar)
        j = i + 1
        while j < len(lines):
            ln = lines[j].rstrip("\r\n")
            if not ln.strip():
                j += 1
                continue
            cur_indent = len(ln) - len(ln.lstrip(" "))
            if cur_indent <= indent_len:
                break
            j += 1
        lines[i:j] = make_block(indent_str)
        p.write_text("".join(lines), encoding="utf-8")
        return True

    # Not found -> insert near end
    indent_str = " " * (parent_indent if key_path[:-1] else 0)
    insert_at = end
    block_lines = make_block(indent_str)
    lines[insert_at:insert_at] = block_lines
    p.write_text("".join(lines), encoding="utf-8")
    return True


def add_account(path: str, name: str, url: str) -> bool:
    """
    Append an account under accounts: list without rewriting the whole file.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        raise ValueError("name/url empty")

    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    blk = _find_block([ln.rstrip("\r\n") for ln in lines], "accounts")
    if not blk:
        # insert accounts at top
        lines.insert(0, "accounts:\n")
        lines.insert(1, f"  - name: {_quote_yaml_scalar(name)}\n")
        lines.insert(2, f"    url: {_quote_yaml_scalar(url)}\n")
        p.write_text("".join(lines), encoding="utf-8")
        return True

    key_line, block_start, indent = blk
    list_indent = " " * (indent + 2)
    prop_indent = " " * (indent + 4)
    # find insertion point: end of this block (next line with indent <= indent)
    end = block_start
    while end < len(lines):
        ln = lines[end].rstrip("\r\n")
        if not ln.strip():
            end += 1
            continue
        cur_indent = len(ln) - len(ln.lstrip(" "))
        if cur_indent <= indent:
            break
        end += 1

    # Append
    insert_at = end
    lines.insert(insert_at, f"{list_indent}- name: {_quote_yaml_scalar(name)}\n")
    lines.insert(insert_at + 1, f"{prop_indent}url: {_quote_yaml_scalar(url)}\n")
    p.write_text("".join(lines), encoding="utf-8")
    return True


def delete_account_by_index(path: str, index_1based: int) -> bool:
    """
    Delete the Nth non-comment list item under accounts: (1-based).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    raw = [ln.rstrip("\r\n") for ln in lines]
    blk = _find_block(raw, "accounts")
    if not blk:
        return False
    key_line, block_start, indent = blk
    # Determine block extent
    end = block_start
    while end < len(raw):
        ln = raw[end]
        if not ln.strip():
            end += 1
            continue
        cur_indent = len(ln) - len(ln.lstrip(" "))
        if cur_indent <= indent:
            break
        end += 1

    # Find list items starting with "- " at proper indent, skipping commented lines
    item_starts: List[int] = []
    item_indent = indent + 2
    for i in range(block_start, end):
        ln = raw[i]
        if ln.lstrip().startswith("#"):
            continue
        cur_indent = len(ln) - len(ln.lstrip(" "))
        if cur_indent == item_indent and ln.strip().startswith("- "):
            item_starts.append(i)

    if index_1based < 1 or index_1based > len(item_starts):
        return False

    start_i = item_starts[index_1based - 1]
    stop_i = end
    # item ends at next item start or block end
    for s in item_starts[index_1based:]:
        if s > start_i:
            stop_i = s
            break

    del lines[start_i:stop_i]
    p.write_text("".join(lines), encoding="utf-8")
    return True
