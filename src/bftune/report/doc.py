"""One document model for every bftune report: rendered as a self-contained HTML page (shared CSS,
light/dark, embedded figures, copyable code blocks) and as a short Markdown text for the agent.

    doc = Doc("bftune tune report", subtitle="3.5 Mini · Betaflight 2026.6.2", kind="tune")
    doc.cards([("Verdict", "PASS", "pass"), ("Stick lag roll", "7.4 → 4.1 ms", None)])
    doc.section("Changes").table(["setting", "old", "new", "why"], rows)
    doc.figure("loop_compare.png", "Sensitivity at hover")
    doc.write(out_dir, "report")   # report.html + report.md
"""

from __future__ import annotations

import base64
import datetime as dt
import html
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__

CSS = """
:root{--bg:#ffffff;--fg:#1c2128;--muted:#5f6b76;--line:#d8dee4;--panel:#f6f8fa;--accent:#0b62c4;
--pass:#1a7f37;--fail:#cf222e;--warn:#9a6700;--info:#5f6b76;--shadow:0 1px 3px rgba(0,0,0,.08)}
@media (prefers-color-scheme: dark){:root{--bg:#0e1116;--fg:#e6edf3;--muted:#9aa4ae;--line:#30363d;--panel:#161b22;
--accent:#58a6ff;--pass:#3fb950;--fail:#f85149;--warn:#d29922;--info:#9aa4ae;--shadow:none}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0}
.wrap{max-width:1180px;margin:0 auto;padding:28px 18px 60px}
header{border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:18px}
header .kind{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:700}
header h1{margin:4px 0 2px;font-size:26px}header .sub{color:var(--muted)}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0 0;font-size:13px}nav a{color:var(--accent);text-decoration:none}
h2{font-size:19px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:16px;margin:20px 0 8px}
p{margin:8px 0}.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;margin:14px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;box-shadow:var(--shadow)}
.card .l{font-size:12px;color:var(--muted)}.card .v{font-size:18px;font-weight:650;margin-top:2px}
.pass{color:var(--pass)}.fail{color:var(--fail)}.warn{color:var(--warn)}.info{color:var(--info)}
.tbl{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;font-size:13px;min-width:100%}
th,td{border-bottom:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}
th{background:var(--panel);font-weight:600;position:sticky;top:0}
tr:hover td{background:color-mix(in srgb,var(--panel) 60%,transparent)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code{background:var(--panel);border:1px solid var(--line);border-radius:5px;padding:0 4px;font-size:90%}
.code{position:relative;margin:10px 0}
.code pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto;margin:0;font-size:13px}
.code button{position:absolute;top:8px;right:8px;font-size:12px;border:1px solid var(--line);background:var(--bg);
color:var(--fg);border-radius:6px;padding:2px 8px;cursor:pointer}
figure{margin:14px 0}figure img{max-width:100%;border:1px solid var(--line);border-radius:8px;background:#fff}
figcaption{font-size:13px;color:var(--muted);margin-top:4px}
ul.items{padding-left:20px}ul.items li{margin:3px 0}
.note{border-left:3px solid var(--accent);background:var(--panel);padding:8px 12px;border-radius:0 8px 8px 0;margin:10px 0}
.note.warn{border-color:var(--warn)}.note.fail{border-color:var(--fail)}.note.pass{border-color:var(--pass)}
footer{margin-top:40px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:10px}
"""

JS = """
document.querySelectorAll('.code button').forEach(b=>b.addEventListener('click',()=>{
 const t=b.parentElement.querySelector('pre').innerText;navigator.clipboard.writeText(t).then(()=>{b.textContent='copied';
 setTimeout(()=>b.textContent='copy',1500)})}));
"""


def _e(x) -> str:
    return html.escape(str(x))


def evidence(ev: dict, max_len: int = 90) -> str:
    """A finding's evidence as one short line (long lists such as event records are left out)."""
    return ", ".join(f"{k} {v}" for k, v in ev.items() if len(str(v)) <= max_len)


@dataclass
class Block:
    kind: str
    data: dict = field(default_factory=dict)


@dataclass
class Doc:
    title: str
    subtitle: str = ""
    kind: str = "report"
    blocks: list[Block] = field(default_factory=list)

    # ------------------------------------------------------------------ building
    def section(self, title: str) -> Doc:
        self.blocks.append(Block("h2", {"text": title}))
        return self

    def sub(self, title: str) -> Doc:
        self.blocks.append(Block("h3", {"text": title}))
        return self

    def p(self, text: str, cls: str = "") -> Doc:
        self.blocks.append(Block("p", {"text": text, "cls": cls}))
        return self

    def note(self, text: str, status: str = "") -> Doc:
        self.blocks.append(Block("note", {"text": text, "status": status}))
        return self

    def cards(self, items: list[tuple[str, str, str | None]]) -> Doc:
        self.blocks.append(Block("cards", {"items": items}))
        return self

    def table(self, headers: list[str], rows: list[list], status_col: int | None = None) -> Doc:
        self.blocks.append(Block("table", {"headers": headers, "rows": rows, "status_col": status_col}))
        return self

    def items(self, lines: list[str]) -> Doc:
        if lines:
            self.blocks.append(Block("items", {"lines": lines}))
        return self

    def code(self, text: str, label: str = "") -> Doc:
        self.blocks.append(Block("code", {"text": text.rstrip(), "label": label}))
        return self

    def figure(self, path: Path | str | None, caption: str = "") -> Doc:
        if path:
            self.blocks.append(Block("figure", {"path": str(path), "caption": caption}))
        return self

    # ------------------------------------------------------------------ rendering
    def html(self, base: Path) -> str:
        toc = [b.data["text"] for b in self.blocks if b.kind == "h2"]
        out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
               f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(self.title)}</title>"
               f"<style>{CSS}</style></head><body><div class='wrap'><header><div class='kind'>{_e(self.kind)}</div>"
               f"<h1>{_e(self.title)}</h1><div class='sub'>{_e(self.subtitle)}</div>"]
        if len(toc) > 2:
            out.append("<nav>" + "".join(f"<a href='#s{i}'>{_e(t)}</a>" for i, t in enumerate(toc)) + "</nav>")
        out.append("</header>")
        n_h2 = 0
        for b in self.blocks:
            d = b.data
            if b.kind == "h2":
                out.append(f"<h2 id='s{n_h2}'>{_e(d['text'])}</h2>")
                n_h2 += 1
            elif b.kind == "h3":
                out.append(f"<h3>{_e(d['text'])}</h3>")
            elif b.kind == "p":
                out.append(f"<p class='{d['cls']}'>{_e(d['text'])}</p>")
            elif b.kind == "note":
                out.append(f"<div class='note {d['status']}'>{_e(d['text'])}</div>")
            elif b.kind == "cards":
                out.append("<div class='cards'>" + "".join(
                    f"<div class='card'><div class='l'>{_e(lbl)}</div><div class='v {st or ''}'>{_e(val)}</div></div>"
                    for lbl, val, st in d["items"]) + "</div>")
            elif b.kind == "table":
                sc = d["status_col"]
                rows = []
                for r in d["rows"]:
                    cells = []
                    for i, c in enumerate(r):
                        cls = []
                        if isinstance(c, (int, float)):
                            cls.append("num")
                        if sc is not None and i == sc:
                            s = str(c).lower()
                            cls.append("pass" if s in ("pass", "ok") else "fail" if s.startswith("fail") else "")
                        cells.append(f"<td class='{' '.join(cls)}'>{_e(c)}</td>")
                    rows.append("<tr>" + "".join(cells) + "</tr>")
                out.append("<div class='tbl'><table><thead><tr>" + "".join(f"<th>{_e(h)}</th>" for h in d["headers"])
                           + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")
            elif b.kind == "items":
                out.append("<ul class='items'>" + "".join(f"<li>{_e(x)}</li>" for x in d["lines"]) + "</ul>")
            elif b.kind == "code":
                lbl = f"<div class='muted' style='font-size:12px;margin-bottom:3px'>{_e(d['label'])}</div>" if d["label"] else ""
                out.append(f"<div class='code'>{lbl}<button>copy</button><pre>{_e(d['text'])}</pre></div>")
            elif b.kind == "figure":
                p = Path(d["path"])
                p = p if p.is_absolute() else base / p
                if p.exists():
                    data = base64.b64encode(p.read_bytes()).decode()
                    out.append(f"<figure><img alt='{_e(d['caption'])}' src='data:image/png;base64,{data}'>"
                               f"<figcaption>{_e(d['caption'])}</figcaption></figure>")
        out.append(f"<footer>bftune {__version__} · {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} · model predictions, "
                   f"not promises</footer></div><script>{JS}</script></body></html>")
        return "".join(out)

    def markdown(self) -> str:
        L = [f"# {self.title}", "", self.subtitle, ""]
        for b in self.blocks:
            d = b.data
            if b.kind == "h2":
                L += [f"## {d['text']}", ""]
            elif b.kind == "h3":
                L += [f"### {d['text']}", ""]
            elif b.kind in ("p", "note"):
                L += [d["text"], ""]
            elif b.kind == "cards":
                L += [" · ".join(f"**{lbl}:** {val}" for lbl, val, _ in d["items"]), ""]
            elif b.kind == "table":
                L.append("| " + " | ".join(map(str, d["headers"])) + " |")
                L.append("|" + "---|" * len(d["headers"]))
                L += ["| " + " | ".join(str(c) for c in r) + " |" for r in d["rows"]]
                L.append("")
            elif b.kind == "items":
                L += [f"- {x}" for x in d["lines"]] + [""]
            elif b.kind == "code":
                L += ["```", d["text"], "```", ""]
            elif b.kind == "figure":
                L += [f"![{d['caption']}]({Path(d['path']).name})", ""]
        return "\n".join(L).rstrip() + "\n"

    def write(self, out: Path, stem: str = "report") -> Path:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{stem}.md").write_text(self.markdown(), encoding="utf-8")
        p = out / f"{stem}.html"
        p.write_text(self.html(out), encoding="utf-8")
        return p
