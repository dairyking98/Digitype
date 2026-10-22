"""The labelling tool's local web server: pages, boxes, settings and the
Regenerate job. Started by `python -m typeseed label`."""
import io
import os
import threading
import traceback

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import regen
from .project import Project

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def make_app(project: Project):
    app = FastAPI(title="Type Seed")
    job = {"running": False, "log": [], "result": None, "error": None}

    def boxes_with_cells():
        per = regen.all_cells(project)
        return [{**b, "cells": per.get(b["id"], [])} for b in project.box_list()]

    @app.get("/api/project")
    def get_project():
        return {"root": project.root, "pages": project.meta["pages"], "settings": project.meta["settings"],
                "boxes": boxes_with_cells(), "estimated_pitch_mm": regen.estimate_pitch_mm(project)}

    @app.post("/api/sources")
    def add_sources(body: dict = Body(...)):
        paths = [p for p in body.get("paths", []) if p]
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            raise HTTPException(404, f"not found: {', '.join(missing)}")
        project.add_sources(paths)
        return {"pages": project.meta["pages"]}

    @app.get("/api/page/{pid}/preview")
    def preview(pid: int):
        try:
            return FileResponse(project.preview(pid), media_type="image/jpeg",
                                headers={"Cache-Control": "max-age=86400"})
        except KeyError:
            raise HTTPException(404, "no such page")

    @app.get("/api/page/{pid}/region")
    def region(pid: int, x: float, y: float, w: float, h: float, out: int = 1600):
        im = project.region(pid, x, y, w, h, min(out, 4000))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return Response(buf.getvalue(), media_type="image/jpeg")

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
        s = project.meta["settings"]
        for k, v in body.items():
            if k in s:
                s[k] = type(s[k])(v) if not isinstance(s[k], str) else str(v)
        project.save()
        return s

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

    @app.get("/api/out/{name}")
    def out_file(name: str):
        p = os.path.join(project.root, "out", os.path.basename(name))
        if not os.path.isfile(p):
            raise HTTPException(404, "not built yet")
        return FileResponse(p, filename=os.path.basename(p))

    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app
