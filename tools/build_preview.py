#!/usr/bin/env python3
"""
Build a single self-contained HTML file of the browser client running in
MOCK mode (see client/js/mock/mock-socket.js). No server is needed, so the
file can be hosted on any static host or opened from a phone to judge the UI.

    python tools/build_preview.py [output.html] [--fragment]

--fragment writes a head-less page (title, style, body content, script) for
hosts that add their own <!DOCTYPE>/<head> wrapper.

Requires Node (npx fetches esbuild on first run).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fragment = "--fragment" in sys.argv
    out = Path(args[0] if args else ROOT / "preview" / "index.html")
    js = subprocess.run(
        ["npx", "--yes", "esbuild@0.25.0", str(CLIENT / "js" / "app.js"),
         "--bundle", "--format=iife", "--minify", "--target=es2020"],
        check=True, capture_output=True, text=True,
    ).stdout
    css = (CLIENT / "css" / "main.css").read_text()
    html = (CLIENT / "index.html").read_text()

    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    body = re.sub(r'\s*<script type="module"[^>]*></script>', "", body)
    js = js.replace("</script", "<\\/script")

    if fragment:
        # The host's wrapper already pads :root by the safe-area insets and sets
        # the viewport; zero the app's own copies so the insets are not doubled.
        page = f"""<title>Baron</title>
<style>
{css}
html, body {{ height: 100%; }}
:root {{ --safe-t: 0px; --safe-r: 0px; --safe-b: 0px; --safe-l: 0px; }}
body.has-ribbon {{ --safe-t: 18px; }}
.mock-ribbon {{ height: 18px; padding-top: 0; }}
#join-panel, #lobby-panel, #game-panel {{ height: 100%; }}
</style>
{body}
<script>window.BARON_MOCK = true;</script>
<script>{js}</script>
"""
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page)
        print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
        return

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="theme-color" content="#10261b" />
<title>Baron</title>
<style>
{css}
html, body {{ height: 100%; }}
</style>
</head>
<body>
{body}
<script>window.BARON_MOCK = true;</script>
<script>{js}</script>
</body>
</html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
