"""The labelling tool's API without a web server, for the static site: the
page's service worker hands each /api/ request to Python running in the
browser (Pyodide), and `handle` answers it the way server.py does.

Regenerate is run by the page's worker directly (`regenerate`), so its log
can be shown while it runs."""
import io
import json
import os
import zipfile
from urllib.parse import parse_qs, unquote

from . import images, regen
from .project import Project


class HTTPError(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Api:
    def __init__(self, root):
        self.root = root
        self.project = Project(root)

    def open_zip(self, data):
        """Replace the project with one from a zip (Download project, or a
        project folder zipped on another computer). Page files that aren't
        where project.json says are looked for in its uploads/ folder."""
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            top = names[0].split("/")[0] + "/" if names and all(n.startswith(names[0].split("/")[0] + "/") for n in names) else ""
            if top + "project.json" not in names:
                raise HTTPError(400, "no project.json in the zip")
            for d, dirs, files in os.walk(self.root, topdown=False):
                for n in files:
                    os.remove(os.path.join(d, n))
                for n in dirs:
                    os.rmdir(os.path.join(d, n))
            for n in names:
                if n.endswith("/") or not n.startswith(top):
                    continue
                dest = os.path.join(self.root, *n[len(top):].split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(z.read(n))
        path = os.path.join(self.root, "project.json")
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        for page in meta.get("pages", []):
            here = os.path.join(self.root, "uploads", page["file"].replace("\\", "/").split("/")[-1])
            if not os.path.isfile(page["file"]) and os.path.isfile(here):
                page["file"] = here
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1, ensure_ascii=False)
        self.project = Project(self.root)

    # ------------------------------------------------------------ helpers
    def boxes_with_cells(self):
        p = self.project
        per = regen.all_cells(p)
        return [{**b, "cells": per.get(b["id"], [])} for b in p.box_list()]

    def state(self):
        p = self.project
        return {"root": "this browser" if p.root.startswith("/projects/") else p.root,
                "pages": p.meta["pages"], "sets": p.meta["sets"],
                "active_set": p.meta["active_set"], "settings": p.settings(),
                "boxes": self.boxes_with_cells(), "estimated_pitch_mm": regen.estimate_pitch_mm(p),
                "images": p.image_list()}

    def result(self):
        path = os.path.join(self.project.out_dir(), "result.json")
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def place(self, g):
        p = self.project
        st = p.settings()
        ink = images.load_ink(p.image_path(g))
        pl, how = images.auto_place(ink, g.get("char") or "", self.result(), st["advance"], st["upem"])
        return p.update_image(g["id"], {"place": pl, "how": how})

    def regenerate(self, chars, log):
        return regen.regenerate(self.project, chars, log=log)

    # ------------------------------------------------------------ requests
    def handle(self, method, path, query, body):
        """-> (status, content type, body bytes, download filename or "")"""
        q = {k: v[-1] for k, v in parse_qs(query or "").items()}
        if body is not None and not isinstance(body, bytes):
            body = body.to_bytes()          # a JS Uint8Array, from the worker
        try:
            out = self.route(method, [unquote(x) for x in path.strip("/").split("/")[1:]], q, body or b"")
        except HTTPError as e:
            return e.status, "application/json", json.dumps({"detail": e.detail}).encode(), ""
        if isinstance(out, tuple):
            return out
        return 200, "application/json", json.dumps(out, ensure_ascii=False).encode(), ""

    def route(self, method, parts, q, body):
        p = self.project

        def js():
            return json.loads(body) if body else {}

        def file(path, ctype, name=""):
            with open(path, "rb") as f:
                return 200, ctype, f.read(), name

        match (method, parts):
            case ("GET", ["project"]):
                return self.state()
            case ("POST", ["sets"]):
                p.new_set((js().get("name") or "").strip())
                return self.state()
            case ("POST", ["sets", sid]):
                b = js()
                if b.get("name"):
                    p.rename_set(int(sid), b["name"].strip())
                if b.get("activate"):
                    p.activate_set(int(sid))
                return self.state()
            case ("DELETE", ["sets", sid]):
                try:
                    p.delete_set(int(sid))
                except ValueError as e:
                    raise HTTPError(409, str(e))
                return self.state()
            case ("GET", ["result"]):
                return self.result()

            case ("GET", ["images"]):
                return p.image_list()
            case ("POST", ["images"]):
                name, char = q.get("name", "image.png"), q.get("char", "")
                stem = os.path.splitext(os.path.basename(name))[0]
                if not char:      # a file named after its character: &.png, uni0026.png, U+0026.png
                    hx = stem[3:] if stem.lower().startswith("uni") else stem[2:] if stem.upper().startswith("U+") else ""
                    if len(stem) == 1:
                        char = stem
                    elif len(hx) in (4, 5) and all(c in "0123456789abcdefABCDEF" for c in hx):
                        char = chr(int(hx, 16))
                try:
                    g = p.add_image(name, body, char)
                except ValueError as e:
                    raise HTTPError(400, str(e))
                return self.place(g)
            case ("POST", ["images", iid]):
                b = js()
                try:
                    g = p.update_image(int(iid), {k: v for k, v in b.items() if k in ("char", "place", "enabled")})
                    if b.get("auto") or ("char" in b and not b.get("place") and g.get("how") != "hand"):
                        g = self.place(g)
                    elif b.get("place"):
                        g = p.update_image(int(iid), {"how": "hand"})
                except KeyError:
                    raise HTTPError(404, "no such image")
                return g
            case ("DELETE", ["images", iid]):
                try:
                    p.delete_image(int(iid))
                except KeyError:
                    raise HTTPError(404, "no such image")
                return {"ok": True}
            case ("GET", ["images", iid, "file"]):
                try:
                    path = p.image_path(p.image(int(iid)))
                except KeyError:
                    raise HTTPError(404, "no such image")
                return file(path, "image/png")

            case ("POST", ["sources"]):
                raise HTTPError(400, "the static site can't read paths on this computer - use Upload")
            case ("POST", ["upload"]):
                name = q.get("name", "upload")
                try:
                    path, n = p.add_upload(name, body)
                except ValueError as e:
                    raise HTTPError(400, str(e))
                except Exception as e:
                    raise HTTPError(400, f"couldn't read {name}: {e}")
                if q.get("dpi") and n:          # pages rendered from a PDF in the page carry no dpi
                    for page in p.meta["pages"][-n:]:
                        page["dpi"] = float(q["dpi"])
                    p.save()
                return {"pages": p.meta["pages"], "added": n, "path": path}

            case ("GET", ["page", pid, "preview"]):
                try:
                    return file(p.preview(int(pid)), "image/jpeg")
                except KeyError:
                    raise HTTPError(404, "no such page")
            case ("GET", ["page", pid, "region"]):
                im = p.region(int(pid), float(q["x"]), float(q["y"]), float(q["w"]), float(q["h"]),
                              min(int(q.get("out", 1600)), 4000))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88)
                return 200, "image/jpeg", buf.getvalue(), ""
            case ("POST", ["page", pid]):
                b = js()
                try:
                    page = p.set_page(int(pid), dpi=b.get("dpi") if "dpi" in b else None,
                                      rotation=b.get("rotation"), quarter=b.get("quarter"))
                except KeyError:
                    raise HTTPError(404, "no such page")
                if "dpi" in b and not b["dpi"]:
                    page["dpi"] = None
                    p.save()
                return {"page": page, "boxes": self.boxes_with_cells()}

            case ("POST", ["box"]):
                box = js()
                for k in ("page", "x", "y", "w", "h"):
                    if k not in box:
                        raise HTTPError(400, f"missing {k}")
                if box.get("w", 0) < 0:
                    box["x"], box["w"] = box["x"] + box["w"], -box["w"]
                if box.get("h", 0) < 0:
                    box["y"], box["h"] = box["y"] + box["h"], -box["h"]
                saved = p.upsert_box(box)
                return {**saved, "cells": regen.box_cells(p, saved)}
            case ("DELETE", ["box", bid]):
                p.delete_box(int(bid))
                return {"ok": True}
            case ("GET", ["boxes"]):
                return self.boxes_with_cells()

            case ("POST", ["settings"]):
                s = p.settings()
                for k, v in js().items():
                    if k in s:
                        if isinstance(s[k], bool):
                            s[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                        elif isinstance(s[k], str):
                            s[k] = str(v)
                        else:
                            s[k] = type(s[k])(v)
                p.save()
                return s
            case ("POST", ["variant"]):
                b = js()
                st = p.settings()
                st["variant_pick"] = {**(st.get("variant_pick") or {}), b["char"]: int(b["box"])}
                p.save()
                return st

            case ("GET", ["out", folder, name]):
                path = os.path.join(p.root, "out", os.path.basename(folder), os.path.basename(name))
                if not os.path.isfile(path):
                    raise HTTPError(404, "not built yet")
                ctype = "font/ttf" if path.endswith(".ttf") else "application/octet-stream"
                return file(path, ctype, os.path.basename(path))

            case ("POST", ["import"]):
                self.open_zip(body)
                return self.state()
            case ("GET", ["export"]):
                # the whole project as a zip (without the page cache, which is rebuilt)
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for d, dirs, files in os.walk(p.root):
                        dirs[:] = [x for x in dirs if not (d == p.root and x == "cache")]
                        for n in files:
                            full = os.path.join(d, n)
                            z.write(full, os.path.relpath(full, p.root))
                return 200, "application/zip", buf.getvalue(), "digitype-project.zip"
        raise HTTPError(404, "not found")
