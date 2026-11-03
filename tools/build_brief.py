"""Rebuild docs/PROJECT_BRIEF.html from docs/PROJECT_BRIEF.md, keeping the page wrapper (stylesheet, print note) of the existing HTML.

    .venv\\Scripts\\python.exe -m tools.build_brief

The PDF is that page printed by Chrome (a throwaway profile, so it cannot attach to your own browser):

    chrome.exe --headless=new --disable-gpu --no-pdf-header-footer --user-data-dir=<temp dir> ^
        --print-to-pdf=docs\\PROJECT_BRIEF.pdf file:///<project>/docs/PROJECT_BRIEF.html
"""
from __future__ import annotations

import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
HTML, MD = ROOT / "docs" / "PROJECT_BRIEF.html", ROOT / "docs" / "PROJECT_BRIEF.md"


def build() -> str:
    old = HTML.read_text(encoding="utf-8")
    head = old[:old.index("<main>") + len("<main>")]                         # doctype ... <main>: the title and the whole stylesheet
    print_note = re.search(r'<p class="printnote">.*?</p>', old, re.S).group(0)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc"], extension_configs={"toc": {"toc_depth": "2-3"}})
    body = md.convert(MD.read_text(encoding="utf-8"))
    toc = f'<h2 id="contents" style="border:0;padding:0;margin-top:1em">Contents</h2><div class="toc">{md.toc}</div>'
    i = body.index("<hr />") + len("<hr />")                                  # the contents follow the title block
    return head + "\n" + print_note + "\n" + body[:i] + "\n" + toc + body[i:] + "\n</main></body></html>\n"


if __name__ == "__main__":
    html = build()
    HTML.write_text(html, encoding="utf-8")
    print(f"wrote {HTML} ({len(html)} characters)")
