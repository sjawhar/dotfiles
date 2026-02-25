# /// script
# requires-python = ">=3.12"
# dependencies = ["cmarkgfm"]
# ///
"""Markdown preview server with live reload."""

import hashlib
import http.server
import sys
from pathlib import Path

import cmarkgfm

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <link rel="stylesheet"
          href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown-dark.css">
    <link rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github-dark.min.css">
    <script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js"></script>
    <style>
        body {{
            background: #0d1117;
            padding: 2rem;
            display: flex;
            justify-content: center;
        }}
        .markdown-body {{
            max-width: 980px;
            width: 100%;
            padding: 2rem;
        }}
    </style>
    <script>
        // Live reload: poll file hash every 2s
        let lastHash = '';
        setInterval(async () => {{
            try {{
                const resp = await fetch('/_hash');
                const hash = await resp.text();
                if (lastHash && hash !== lastHash) location.reload();
                lastHash = hash;
            }} catch (e) {{}}
        }}, 2000);

        // Syntax highlighting after load
        document.addEventListener('DOMContentLoaded', () => hljs.highlightAll());
    </script>
</head>
<body>
    <article class="markdown-body">
        {body}
    </article>
</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print("Usage: mdview-server.py <file.md> [port]", file=sys.stderr)
        sys.exit(1)

    file = Path(sys.argv[1]).resolve()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8090

    if not file.exists():
        print(f"File not found: {file}", file=sys.stderr)
        sys.exit(1)

    def render():
        try:
            content = file.read_text()
        except FileNotFoundError:
            content = "*File not found — it may have been deleted.*"
        body = cmarkgfm.github_flavored_markdown_to_html(content)
        return TEMPLATE.format(title=file.name, body=body)

    def file_hash():
        try:
            return hashlib.md5(file.read_bytes()).hexdigest()
        except FileNotFoundError:
            return "missing"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/_hash":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(file_hash().encode())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(render().encode())

        def log_message(self, *args):
            pass  # suppress request logs

    class Server(http.server.HTTPServer):
        allow_reuse_address = True

    with Server(("0.0.0.0", port), Handler) as httpd:
        print(f"Serving {file.name} on port {port}", file=sys.stderr)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
