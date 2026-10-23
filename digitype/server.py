"""The labelling tool's local web server: pages, boxes, settings and the
Regenerate job. Started by `python -m digitype label`."""
import io
import json
import os
import threading
import traceback

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import images, regen
from .project import Project

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def make_app(project: Project):
    app = FastAPI(title="Digitype")
    job = {"running": False, "log": [], "result": None, "error": None}

    def warm():
        # work out every box's cells once in the background at start, so the
        # first page load (and the first save) doesn't wait for all of them
        try:
            regen.all_cells(project)
        except Exception:
            pass
    threading.Thread(target=warm, daemon=True).start()

    def boxes_with_cells():
        snapshot = project.box_list()
        per = regen.all_cells(project)
        return [{**b, "cells": per.get(b["id"], [])} for b in snapshot]

    def state():
        return {"root": project.root, "pages": project.meta["pages"], "sets": project.meta["sets"],
                "active_set": project.meta["active_set"], "settings": project.settings(),
                "boxes": boxes_with_cells(), "estimated_pitch_mm": regen.estimate_pitch_mm(project),
                "images": project.image_list()}

    @app.get("/api/project")
    def get_project():
        return state()

    # font sets: each typeface labelled separately, so two never get combined
    @app.post("/api/sets")
    def new_set(body: dict = Body(default={})):
        project.new_set((body.get("name") or "").strip())
        return state()

    @app.post("/api/sets/{sid}")
    def edit_set(sid: int, body: dict = Body(default={})):
        if body.get("name"):
            project.rename_set(sid, body["name"].strip())
        if body.get("activate"):
            project.activate_set(sid)
        return state()

    @app.delete("/api/sets/{sid}")
    def delete_set(sid: int):
        try:
            project.delete_set(sid)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return state()

    @app.get("/api/result")
    def result():
        p = os.path.join(project.out_dir(), "result.json")
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    # glyph images: clean pictures of characters standing in for the scans
    def place(g):
        st = project.settings()
        ink = images.load_ink(project.image_path(g))
        pl, how = images.auto_place(ink, g.get("char") or "", result(), st["advance"], st["upem"])
        return project.update_image(g["id"], {"place": pl, "how": how})

    @app.get("/api/images")
    def image_list():
        return project.image_list()

    @app.post("/api/images")
    async def add_image(name: str, request: Request, char: str = ""):
        stem = os.path.splitext(os.path.basename(name))[0]
        if not char:          # a file named after its character: &.png, uni0026.png, U+0026.png
            hexes = [stem[3:] if stem.lower().startswith("uni") else stem[2:] if stem.upper().startswith("U+") else ""]
            if len(stem) == 1:
                char = stem
            elif len(hexes[0]) in (4, 5) and all(c in "0123456789abcdefABCDEF" for c in hexes[0]):
                char = chr(int(hexes[0], 16))
        try:
            g = project.add_image(name, await request.body(), char)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return place(g)

    @app.post("/api/images/{iid}")
    def edit_image(iid: int, body: dict = Body(default={})):
        try:
            g = project.update_image(iid, {k: v for k, v in body.items() if k in ("char", "place", "enabled")})
            if body.get("auto") or ("char" in body and not body.get("place") and g.get("how") != "hand"):
                g = place(g)       # a new character: place it again, unless it was placed by hand
            elif body.get("place"):
                g = project.update_image(iid, {"how": "hand"})
        except KeyError:
            raise HTTPException(404, "no such image")
        return g

    @app.delete("/api/images/{iid}")
    def delete_image(iid: int):
        try:
            project.delete_image(iid)
        except KeyError:
            raise HTTPException(404, "no such image")
        return {"ok": True}

    @app.get("/api/images/{iid}/file")
    def image_file(iid: int):
        try:
            return FileResponse(project.image_path(project.image(iid)))
        except KeyError:
            raise HTTPException(404, "no such image")

    @app.post("/api/sources")
    def add_sources(body: dict = Body(...)):
        paths = [p for p in body.get("paths", []) if p]
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            raise HTTPException(404, f"not found: {', '.join(missing)}")
        project.add_sources(paths)
        return {"pages": project.meta["pages"]}

    @app.post("/api/upload")
    async def upload(name: str, request: Request):
        try:
            path, n = project.add_upload(name, await request.body())
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(400, f"couldn't read {name}: {e}")
        return {"pages": project.meta["pages"], "added": n, "path": path}

    @app.get("/api/page/{pid}/preview")
    def preview(pid: int):
        try:
            return FileResponse(project.preview(pid), media_type="image/jpeg",
                                headers={"Cache-Control": "no-cache"})
        except KeyError:
            raise HTTPException(404, "no such page")

    @app.get("/api/page/{pid}/region")
    def region(pid: int, x: float, y: float, w: float, h: float, out: int = 1600):
        im = project.region(pid, x, y, w, h, min(out, 4000))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return Response(buf.getvalue(), media_type="image/jpeg")

    @app.post("/api/page/{pid}")
    def set_page(pid: int, body: dict = Body(default={})):
        try:
            page = project.set_page(pid, dpi=body.get("dpi", None) if "dpi" in body else None,
                                    rotation=body.get("rotation"), quarter=body.get("quarter"))
        except KeyError:
            raise HTTPException(404, "no such page")
        if "dpi" in body and not body["dpi"]:
            page["dpi"] = None
            project.save()
        if "rotation" in body or "quarter" in body:
            # start turning the full-resolution page now, so the sharp view
            # (zoomed in) and Regenerate don't wait for it later
            threading.Thread(target=lambda: project.grey(pid), daemon=True).start()
        return {"page": page, "boxes": boxes_with_cells()}

    @app.post("/api/box")
    def save_box(box: dict = Body(...)):
        for k in ("page", "x", "y", "w", "h"):
            if k not in box:
                raise HTTPException(400, f"missing {k}")
        if box.get("w", 0) < 0:
            box["x"], box["w"] = box["x"] + box["w"], -box["w"]
        if box.get("h", 0) < 0:
            box["y"], box["h"] = box["y"] + box["h"], -box["h"]
        saved = project.upsert_box(box)
        return {**saved, "cells": regen.box_cells(project, saved)}

    @app.delete("/api/box/{bid}")
    def delete_box(bid: int):
        project.delete_box(bid)
        return {"ok": True}

    @app.get("/api/boxes")
    def boxes():
        return boxes_with_cells()

    @app.post("/api/settings")
    def settings(body: dict = Body(...)):
        s = project.settings()
        for k, v in body.items():
            if k in s:
                if isinstance(s[k], bool):
                    s[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                elif isinstance(s[k], str):
                    s[k] = str(v)
                else:
                    s[k] = type(s[k])(v)
        project.save()
        return s

    @app.post("/api/variant")
    def pick_variant(body: dict = Body(...)):
        """Which variant of a character goes in the font: the one holding this box."""
        st = project.settings()
        st["variant_pick"] = {**(st.get("variant_pick") or {}), body["char"]: int(body["box"])}
        project.save()
        return st

    def run(chars):
        job.update(running=True, log=[], result=None, error=None)
        try:
            job["result"] = regen.regenerate(project, chars, log=job["log"].append)
        except Exception as e:  # report, don't kill the server
            job["error"] = f"{e}"
            job["log"].append(traceback.format_exc())
        finally:
            job["running"] = False

    @app.post("/api/regenerate")
    def regenerate(body: dict = Body(default={})):
        if job["running"]:
            raise HTTPException(409, "already running")
        threading.Thread(target=run, args=(body.get("chars"),), daemon=True).start()
        return {"started": True}

    @app.get("/api/regenerate")
    def status(since: int = 0):
        return {"running": job["running"], "log": job["log"][since:], "n": len(job["log"]),
                "result": job["result"] if not job["running"] else None, "error": job["error"]}

    @app.get("/api/out/{folder}/{name}")
    def out_file(folder: str, name: str):
        p = os.path.join(project.root, "out", os.path.basename(folder), os.path.basename(name))
        if not os.path.isfile(p):
            raise HTTPException(404, "not built yet")
        return FileResponse(p, filename=os.path.basename(p))

    @app.middleware("http")
    async def no_stale_page(request, call_next):
        # the page's own files are re-checked on every load, so an update
        # shows up on a plain reload instead of needing Ctrl+F5
        resp = await call_next(request)
        if not request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app
