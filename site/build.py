"""Build the static site into site/dist/: the labelling tool's page with
relative api/ URLs, the boot code, and the digitype package as a zip for
Pyodide. Serve the folder with any static file server, e.g.

    python3 site/build.py && python3 -m http.server -d site/dist 8771

`python3 site/build.py docs/app` builds the copy GitHub Pages serves at
leonardchau.com/Digitype/app/ - rebuild it whenever digitype/ changes.
"""
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "digitype")
WEB = os.path.join(PKG, "web")
DIST = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "dist")

BOOT = """
<div id="boot" class="boot">
  <div class="boot-card">
    <b class="brand">Digitype</b>
    <p class="muted small">Runs entirely in this browser: Python (Pyodide) does the averaging, tracing and
      font building, and nothing is uploaded anywhere. The first start downloads about 40 MB.</p>
    <p id="boot-status">Starting…</p>
    <div id="boot-pick" hidden>
      <button id="boot-browser" class="primary" title="Kept in this browser's storage, on this computer">Keep the project in this browser</button>
      <button id="boot-folder" title="A folder on disk holds the project (Chrome/Edge); it's read into memory when opened">Open a project folder…</button>
    </div>
  </div>
</div>
"""
STYLE = """
<style>
.boot { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; background: var(--bg, #f4f4f2); }
.boot-card { max-width: 30rem; padding: 1.5rem; display: grid; gap: .6rem; }
.boot-card button { margin: .2rem .4rem .2rem 0; }
.static-note { font-size: 12px; }
</style>
"""


def main():
    shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(DIST)
    rel = lambda s: re.sub(r'(["`(])/api/', r"\1api/", s)       # "/api/x" -> "api/x": works under any path

    html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    html = html.replace('<script type="module" src="app.js"></script>', '<script type="module" src="static.js"></script>')
    html = html.replace("</head>", STYLE + "</head>")
    html = html.replace("<body>", "<body>" + BOOT, 1)
    # paths on this computer can't be read from a web page: offer the project download instead
    html = html.replace('<details class="add">', '<p class="muted static-note">Project: <span id="static-where"></span> · '
                        '<a href="api/export" download>Download project (.zip)</a> · '
                        '<a href="#" id="static-open">Open project (.zip)…</a><input id="static-zip" type="file" accept=".zip" hidden></p>\n    <details class="add" hidden>')
    open(os.path.join(DIST, "index.html"), "w", encoding="utf-8").write(rel(html))

    app = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
    open(os.path.join(DIST, "app.js"), "w", encoding="utf-8").write(rel(app))
    shutil.copy(os.path.join(WEB, "style.css"), DIST)
    for f in ("static.js", "worker.js", "sw.js"):
        shutil.copy(os.path.join(HERE, f), DIST)

    with zipfile.ZipFile(os.path.join(DIST, "digitype.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for n in sorted(os.listdir(PKG)):
            if n.endswith(".py") and n not in ("server.py", "__main__.py"):
                z.write(os.path.join(PKG, n), f"digitype/{n}")
    print(f"built {DIST}")


if __name__ == "__main__":
    main()
