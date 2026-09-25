"""Tiny Markdown→HTML converter for bftune reports (self-contained: PNGs are embedded).

Supports exactly what report.md uses: headings, paragraphs, lists, tables, fenced code,
images, **bold**, `code`. No external dependency.
"""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path

CSS = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--code:#f6f8fa;--pass:#1a7f37;--fail:#cf222e;--accent:#0969da}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;--code:#151b23;--pass:#3fb950;--fail:#f85149;--accent:#4493f8}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;max-width:1180px;margin:0 auto;padding:24px 16px}
h1,h2,h3{line-height:1.25}h2{border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:32px}
table{border-collapse:collapse;display:block;overflow-x:auto;font-size:13px;margin:12px 0}
th,td{border:1px solid var(--line);padding:4px 8px;vertical-align:top}th{background:var(--code);text-align:left}
code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:90%}
pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto}pre code{padding:0;background:none}
img{max-width:100%;height:auto;background:#fff;border-radius:6px;border:1px solid var(--line)}
.PASS{color:var(--pass);font-weight:700}.FAIL{color:var(--fail);font-weight:700}
"""


def _inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\b(PASS|FAIL)\b", r'<span class="\1">\1</span>', t)
    return t


def _img(src: str, alt: str, base: Path) -> str:
    p = base / src
    if p.exists():
        data = base64.b64encode(p.read_bytes()).decode()
        return f'<p><img alt="{html.escape(alt)}" src="data:image/png;base64,{data}"></p>'
    return f"<p>[missing image {html.escape(src)}]</p>"


def markdown_to_html(md: str, base: Path, title: str = "bftune report") -> str:
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"]
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].startswith("```"):
                buf.append(lines[j])
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            i += 1
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", ln.strip())
        if m:
            out.append(_img(m.group(2), m.group(1), base))
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>")
                for r in rows[1:]:
                    out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                out.append("</tbody></table>")
            continue
        if re.match(r"^(\s*[-*]|\s*\d+\.)\s+", ln):
            tag = "ol" if re.match(r"^\s*\d+\.", ln) else "ul"
            out.append(f"<{tag}>")
            while i < len(lines) and re.match(r"^(\s*[-*]|\s*\d+\.)\s+", lines[i]):
                out.append("<li>" + _inline(re.sub(r"^(\s*[-*]|\s*\d+\.)\s+", "", lines[i])) + "</li>")
                i += 1
            out.append(f"</{tag}>")
            continue
        if ln.strip():
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||```|!\[|\s*[-*]\s|\s*\d+\.\s)", lines[i]):
                para.append(lines[i])
                i += 1
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            continue
        i += 1
    out.append("</body></html>")
    return "\n".join(out)
