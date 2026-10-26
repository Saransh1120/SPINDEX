"""Emit an Artifact-ready copy of index.html.

The repository's handbook is a standalone static site, so index.html carries its
own <!doctype>, <html>, <head> and <body>. A Claude Artifact supplies that
skeleton at publish time, so publishing index.html verbatim would nest one
document inside another.

Rather than maintain two near-identical pages, this strips the skeleton and
keeps one source of truth: the head's own tags (title, fonts, stylesheet,
import map, the pre-paint theme restore) followed by the body's contents.

    python handbook/build_artifact.py [out.html]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "index.html"
VIEWPORT_META = '<meta name="claude:viewport-fit-sides" content="cover">'


def build(src: Path = SRC) -> str:
    html = src.read_text(encoding="utf-8")

    head = re.search(r"<head>(.*?)</head>", html, re.S)
    body = re.search(r"<body[^>]*>(.*?)</body>", html, re.S)
    if not head or not body:
        raise SystemExit("index.html has no <head> or <body> to extract")

    kept = []
    for line in head.group(1).splitlines():
        s = line.strip()
        # charset and viewport come from the Artifact skeleton; the favicon is
        # replaced by the `icon` publish parameter.
        if s.startswith("<meta charset") or 'name="viewport"' in s:
            continue
        if s.startswith('<link rel="icon"'):
            continue
        kept.append(line)

    head_out = "\n".join(kept).strip()
    # The gutters use max(16px, env(safe-area-inset-*)), so the page may run
    # under a side system bar.
    head_out = head_out.replace("<title>", VIEWPORT_META + "\n<title>", 1)

    return head_out + "\n\n" + body.group(1).strip() + "\n"


def write_model_sidecars() -> list[Path]:
    """Emit a base64 sidecar beside every .glb.

    The Artifact sandbox will not serve a `.glb`, so the page falls back to
    `<model>.glb.txt` when the binary is unavailable. Ordinary static hosts
    serve the `.glb` and never touch these.
    """
    import base64

    written = []
    for glb in sorted((HERE / "assets" / "models").glob("*.glb")):
        side = glb.with_suffix(glb.suffix + ".txt")
        side.write_text(base64.b64encode(glb.read_bytes()).decode("ascii"),
                        encoding="ascii")
        written.append(side)
        print(f"  sidecar {side.name} ({side.stat().st_size / 1024:.0f} KB)")
    return written


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "artifact.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KB)")
    write_model_sidecars()
