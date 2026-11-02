"""Render recap.md into site/index.html, one self-contained file that reads well on a shared screen.

Run it beside the recap: `uv run --with markdown python build_page.py [recap.md]`. Images come from site/img.
"""
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).parent
CSS = """
body{font-family:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;color:#1f2937;max-width:1100px;margin:2rem auto;padding:0 1.5rem;font-size:20px;line-height:1.5}
h1{font-size:2.1rem;margin-bottom:.2rem}h1+p{color:#6b7280;margin-top:0}
h2{font-size:1.5rem;margin-top:2.4rem;border-bottom:2px solid #e5e7eb;padding-bottom:.3rem}
li{margin:.45rem 0}strong{color:#111827}
.row{display:flex;gap:1.5rem;flex-wrap:wrap;margin:1rem 0}
.row img{flex:1 1 420px;max-width:680px;min-width:320px}
body>p>img{max-width:100%}
a{color:#2563eb}code{font-size:.9em}
"""


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd() / "recap.md"
    body = markdown.markdown(src.read_text(), extensions=["extra", "sane_lists"])
    out = src.parent / "site" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
        f"content='width=device-width,initial-scale=1'><title>Recap</title><style>{CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
    out.write_text(html)
    print("wrote", out, len(html), "- images stay in", out.parent / "img")


if __name__ == "__main__":
    main()
