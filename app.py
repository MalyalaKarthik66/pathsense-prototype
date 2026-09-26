"""
PathSense web application (local demo server)

    python app.py                      # desktop:  http://localhost:8000
    python app.py --lan --https        # phone on the same Wi-Fi:  https://<PC-LAN-IP>:8443  (self-signed certificate)

Frontend (web/) -> this Flask API -> the existing PathSense pipeline (main.run_pipeline, live.LivePipeline,
events, accident). Nothing is re-implemented in the browser.

Research prototype - not a certified autonomous driving or safety system. The emergency workflow is a SIMULATION:
no message is sent and no call is placed.
"""

import argparse
import glob
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from typing import Any, Dict, Optional

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUTS = os.path.join(ROOT, "outputs")
WEB_OUT = os.path.join(OUTPUTS, "web")
DEMOS = os.path.join(ROOT, "demos")      # built-in demos, committed to Git (build_demos.py) - present on every deploy
UPLOADS = os.path.join(ROOT, "uploads")
CERTS = os.path.join(ROOT, "certs")
ALLOWED_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
MAX_UPLOAD_MB = 1024
# The pipeline loads YOLOv8, Depth-Anything-V2 and CLIP: measured peak RSS 1.58 GB on CPU (60-frame run, Windows).
# With less RAM than this the process is killed mid-job (e.g. the 512 MB Render Free instance), so uploads are refused
# up front with the reason instead. Override with PATHSENSE_INFERENCE=on|off.
PIPELINE_PEAK_RAM_GB = 1.6
MIN_INFERENCE_RAM_GB = 2.0

app = Flask(__name__, static_folder=os.path.join(ROOT, "web", "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

JOBS: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()
PIPELINE_LOCK = threading.Lock()  # one GPU pipeline at a time (upload processing vs live camera)
_live = {"pipeline": None, "session": None, "last": 0.0, "error": None}
_live_lock = threading.Lock()
_system_cache: Dict[str, Any] = {}


# ------------------------------------------------------------------------------------------------ helpers
def _json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _demo_entry(video_path: str, root: str = OUTPUTS, url_prefix: str = "/media/", name_prefix: str = "",
                meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """A demo = a rendered <name>_pathsense.mp4 that exists, either built in (demos/) or a local output (outputs/)."""
    base = video_path[: -len("_pathsense.mp4")]
    rel = lambda p: os.path.relpath(p, root).replace("\\", "/")
    meta = meta or {}
    name = name_prefix + (meta.get("clip") or rel(base))
    stats = _json(base + "_stats.json") or {}
    lab = stats.get("decision_label_pct", {})
    poster = os.path.join(os.path.dirname(base), "poster.jpg")
    return {
        "name": name,
        "title": meta.get("title") or os.path.basename(base).replace("_", " "),
        "place": meta.get("place"), "desc": meta.get("desc"),
        "builtin": bool(meta),
        "video_url": url_prefix + rel(video_path),
        "poster_url": url_prefix + rel(poster) if meta and os.path.exists(poster) else None,
        "timeline_url": url_prefix + rel(base + "_timeline.png") if os.path.exists(base + "_timeline.png") else None,
        "has_events": os.path.exists(base + "_events.json"),
        "has_frames": os.path.exists(base + "_frames.json"),
        "browser_playable": bool(meta) or stats.get("video_codec") == "avc1",
        "frames": stats.get("frames_processed"), "fps": stats.get("input_fps"),
        "duration_s": round(stats["frames_processed"] / stats["input_fps"], 1) if stats.get("input_fps") else None,
        "processing_fps": stats.get("processing_fps"),
        "brake_pct": round(lab.get("BRAKE", 0) + lab.get("NO SAFE PATH - BRAKE", 0), 1) if lab else None,
        "decision_pct": lab, "uploaded": name.startswith("web/"),
        "accidents": len((stats.get("accident") or {}).get("confirmed", [])),
    }


def _builtin_demos():
    cat = _json(os.path.join(DEMOS, "catalog.json")) or {}
    out = []
    for m in cat.get("demos", []):
        v = os.path.join(DEMOS, m["clip"], f"{m['clip']}_pathsense.mp4")
        if os.path.isfile(v):
            out.append(_demo_entry(v, DEMOS, "/demos/", "demo/", m))
    return out


def _mem_limit_gb() -> Optional[float]:
    """RAM available to this process: the container (cgroup) limit if there is one, else physical memory."""
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(p) as f:
                v = f.read().strip()
            if v.isdigit() and int(v) < 1 << 50:
                return int(v) / 2**30
        except OSError:
            pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (AttributeError, ValueError, OSError):
        pass
    if sys.platform == "win32":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                       [(n, ctypes.c_ulonglong) for n in ("total", "avail", "tpf", "apf", "tv", "av", "aev")]
        m = MS(); m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.total / 2**30
    return None


_caps: Dict[str, Any] = {}


def capabilities() -> Dict[str, Any]:
    """Can this server run the PathSense pipeline? Cheap (no torch import) so the page can ask on load."""
    if _caps:
        return _caps
    import importlib.util
    mem = _mem_limit_gb()
    missing = [m for m in ("torch", "ultralytics", "transformers", "cv2") if importlib.util.find_spec(m) is None]
    force = os.environ.get("PATHSENSE_INFERENCE", "").lower()
    if force in ("0", "off", "false"):
        ok, reason = False, "Video processing is switched off on this server (PATHSENSE_INFERENCE=off)."
    elif missing:
        ok, reason = False, f"Video processing is unavailable on this server: missing Python packages {', '.join(missing)}."
    elif force not in ("1", "on", "true") and mem is not None and mem < MIN_INFERENCE_RAM_GB:
        ok, reason = False, (f"Video processing is unavailable on this server: it has {mem:.1f} GB RAM, and the "
                             f"PathSense pipeline (YOLOv8 + Depth-Anything-V2 + CLIP) peaks at about "
                             f"{PIPELINE_PEAK_RAM_GB} GB. The built-in demos on this page were processed by "
                             f"the same pipeline; to process your own video, run PathSense locally (python app.py).")
    else:
        ok, reason = True, None
    _caps.update(inference=ok, reason=reason, memory_gb=round(mem, 1) if mem else None,
                 host="render" if os.environ.get("RENDER") else "local", max_upload_mb=MAX_UPLOAD_MB,
                 allowed_ext=sorted(ALLOWED_EXT))
    return _caps


def _lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


# ------------------------------------------------------------------------------------------------ pages / media
@app.route("/")
def index():
    return send_from_directory(os.path.join(ROOT, "web"), "index.html")


@app.route("/media/<path:subpath>")
def media(subpath):
    full = os.path.normpath(os.path.join(OUTPUTS, subpath))
    if not full.startswith(OUTPUTS) or not os.path.isfile(full):
        abort(404)
    return send_from_directory(OUTPUTS, subpath, conditional=True)  # HTTP range requests for video seeking


@app.route("/demos/<path:subpath>")
def demo_media(subpath):
    """Built-in demo assets (repository files, so they exist on every deployment). Range requests -> seeking works."""
    full = os.path.normpath(os.path.join(DEMOS, subpath))
    if not full.startswith(DEMOS + os.sep) or not os.path.isfile(full):
        abort(404)
    return send_from_directory(DEMOS, subpath, conditional=True, max_age=86400)


# ------------------------------------------------------------------------------------------------ demos / events
@app.route("/api/demos")
def api_demos():
    """Built-in SIH demos first (always available), then any local pipeline outputs and uploads on this machine."""
    builtin = _builtin_demos()
    clips = {d["name"].split("/", 1)[1] for d in builtin}
    vids = sorted(glob.glob(os.path.join(OUTPUTS, "*_pathsense.mp4")) + glob.glob(os.path.join(WEB_OUT, "*_pathsense.mp4")),
                  key=os.path.getmtime, reverse=True)
    local = [e for e in (_demo_entry(v) for v in vids) if e and e["name"] not in clips]  # built-in copy wins
    return jsonify(builtin + local)


def _base_for(name: str) -> str:
    root = OUTPUTS
    if name.startswith("demo/"):
        clip = name[len("demo/"):]
        root, name = DEMOS, f"{clip}/{clip}"
    base = os.path.normpath(os.path.join(root, name))
    if not base.startswith(root + os.sep):
        abort(400)
    return base


@app.route("/api/demos/<path:name>/stats")
def api_demo_stats(name):
    d = _json(_base_for(name) + "_stats.json")
    return jsonify(d) if d is not None else (jsonify({"error": "no stats"}), 404)


@app.route("/api/demos/<path:name>/events")
def api_demo_events(name):
    d = _json(_base_for(name) + "_events.json")
    return jsonify(d) if d is not None else (jsonify({"error": "no events"}), 404)


@app.route("/api/demos/<path:name>/frames")
def api_demo_frames(name):
    d = _json(_base_for(name) + "_frames.json")
    return jsonify(d) if d is not None else (jsonify({"error": "no per-frame telemetry"}), 404)


@app.route("/api/demos/<path:name>/events/<int:event_id>/card")
def api_event_card(name, event_id):
    from events import event_card
    d = _json(_base_for(name) + "_events.json")
    ev = next((e for e in (d or {}).get("events", []) if e["id"] == event_id), None)
    if ev is None:
        return jsonify({"error": "no such event"}), 404
    return jsonify({"event": ev, "card": event_card(ev)})


# ------------------------------------------------------------------------------------------------ uploads / jobs
def _run_job(job_id: str, in_path: str, out_video: str, stats_json: str):
    job = JOBS[job_id]

    def progress(p):
        job.update(stage="processing", frame=p["frame"], total=p.get("total"), label=p.get("label"),
                   fps=round(p.get("fps") or 0, 1),
                   progress=round(100.0 * p["frame"] / p["total"], 1) if p.get("total") else None)

    try:
        with PIPELINE_LOCK:
            job["stage"] = "initializing"
            from main import run_pipeline
            summary = run_pipeline(in_path, out_video, stats_json=stats_json, progress_cb=progress,
                                   cancel_event=job["cancel"])
        job.update(status="done", stage="completed", progress=100.0,
                   result=os.path.relpath(out_video, OUTPUTS)[: -len("_pathsense.mp4")].replace("\\", "/"),
                   summary={k: summary.get(k) for k in ("frames_processed", "processing_fps", "decision_label_pct",
                                                        "brake_episodes", "behavior_counts", "accident")})
    except Exception as e:  # report, do not crash the server
        job.update(status="error", stage="error", error=f"{type(e).__name__}: {e}")
        traceback.print_exc()


@app.route("/api/capabilities")
def api_capabilities():
    return jsonify({**capabilities(), "lan_ips": _lan_ips()})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    caps = capabilities()
    if not caps["inference"]:  # checked before the body is parsed: nothing is stored
        return jsonify({"error": caps["reason"], "code": "inference_unavailable"}), 503
    f = request.files.get("video")
    if f is None or not f.filename:
        return jsonify({"error": "no file in form field 'video'"}), 400
    name = secure_filename(f.filename) or "upload.mp4"
    stem, ext = os.path.splitext(name)
    if ext.lower() not in ALLOWED_EXT:
        return jsonify({"error": f"unsupported file type '{ext or '(none)'}'; use {', '.join(sorted(ALLOWED_EXT))}"}), 415
    job_id = uuid.uuid4().hex[:10]
    os.makedirs(UPLOADS, exist_ok=True); os.makedirs(WEB_OUT, exist_ok=True)
    in_path = os.path.join(UPLOADS, f"{job_id}_{name}")
    try:
        f.save(in_path)
    except OSError as e:
        return jsonify({"error": f"could not store the upload on the server: {e}"}), 507
    base = os.path.join(WEB_OUT, f"{stem}_{job_id}")
    JOBS[job_id] = {"id": job_id, "status": "running", "stage": "queued", "progress": 0.0, "file": name,
                    "created": time.time(), "cancel": threading.Event()}
    threading.Thread(target=_run_job, args=(job_id, in_path, base + "_pathsense.mp4", base + "_stats.json"),
                     daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>")
def api_job(job_id):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "unknown job"}), 404
    return jsonify({k: v for k, v in job.items() if k != "cancel"})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    job = JOBS.get(job_id)
    if job is None:
        return jsonify({"error": "unknown job"}), 404
    job["cancel"].set()
    return jsonify({"ok": True})


# ------------------------------------------------------------------------------------------------ live camera
@app.route("/api/live/start", methods=["POST"])
def api_live_start():
    caps = capabilities()
    if not caps["inference"]:
        return jsonify({"error": caps["reason"], "code": "inference_unavailable"}), 503
    with _live_lock:
        if _live["pipeline"] is None:
            try:
                from live import LivePipeline
                _live["pipeline"] = LivePipeline()
            except Exception as e:
                _live["error"] = f"{type(e).__name__}: {e}"
                return jsonify({"error": _live["error"]}), 500
        else:
            _live["pipeline"].reset()
        _live["session"] = uuid.uuid4().hex[:8]
    return jsonify({"session": _live["session"]})


@app.route("/api/live/frame", methods=["POST"])
def api_live_frame():
    if _live["pipeline"] is None or request.args.get("session") != _live["session"]:
        return jsonify({"error": "no active live session"}), 409
    data = request.get_data()
    if not data:
        return jsonify({"error": "empty frame"}), 400
    if not PIPELINE_LOCK.acquire(blocking=False):
        return jsonify({"busy": True, "error": "a video upload is being processed - live mode paused"}), 503
    try:
        t = time.time()
        res = _live["pipeline"].process_jpeg(data)
        res["perf"]["server_total_ms"] = round((time.time() - t) * 1000.0, 1)
        return jsonify(res)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        PIPELINE_LOCK.release()


@app.route("/api/live/stop", methods=["POST"])
def api_live_stop():
    _live["session"] = None
    return jsonify({"ok": True})


# ------------------------------------------------------------------------------------------------ emergency (simulation)
def _emergency():
    from accident import EmergencyService
    return EmergencyService(config_path=os.path.join(ROOT, "config", "emergency.json"))


@app.route("/api/emergency/contact", methods=["GET", "POST", "DELETE"])
def api_contact():
    es = _emergency()
    if request.method == "POST":
        return jsonify(es.save_contact(request.get_json(force=True) or {}))
    if request.method == "DELETE":
        es.delete_contact()
        return jsonify({})
    return jsonify(es.load_contact())


@app.route("/api/emergency/hospitals")
def api_hospitals():
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lon are required"}), 400
    return jsonify(_emergency().find_nearby_hospital(lat, lon))


@app.route("/api/emergency/trigger", methods=["POST"])
def api_emergency_trigger():
    """Runs the emergency workflow in DEMO / SIMULATION mode (nothing is sent or called)."""
    body = request.get_json(force=True) or {}
    es = _emergency()
    loc = es.get_location(body.get("lat"), body.get("lon"))
    event = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "confidence": float(body.get("confidence", 0.0)),
             "source": body.get("source", "demo trigger")}
    hospitals = es.find_nearby_hospital(loc["lat"], loc["lon"]) if loc["lat"] is not None else \
        {"results": [], "error": "location unavailable - allow browser location or set a DEMO LOCATION"}
    msg = es.prepare_emergency_message(event, loc)
    return jsonify({"mode": "DEMO / SIMULATION", "event": event, "location": loc, "contact": es.load_contact(),
                    "message": msg, "notification": es.send_notification(msg), "hospitals": hospitals,
                    "ambulance_request": "SIMULATED - READY FOR CONFIRMATION", "confirmation": es.request_user_confirmation()})


@app.route("/api/emergency/confirm", methods=["POST"])
def api_emergency_confirm():
    return jsonify({"mode": "DEMO / SIMULATION", "confirmed": True, "sent": False,
                    "note": "No real emergency provider is configured in this prototype - nothing was sent or called. "
                            "In a real emergency, call 112 (India) yourself."})


# ------------------------------------------------------------------------------------------------ system
@app.route("/api/system")
def api_system():
    if not _system_cache:
        info = {"python": sys.version.split()[0]}
        try:
            import torch
            info.update(torch=torch.__version__, cuda=torch.cuda.is_available(),
                        gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
                        vram_gb=round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1) if torch.cuda.is_available() else None)
        except Exception as e:
            info["torch_error"] = str(e)
        info["models"] = ["YOLOv8n + ByteTrack", "Depth-Anything-V2-Small (road-plane calibrated)",
                          "CLIP ViT-B/32 (auto-rickshaw refinement)"]
        _system_cache.update(info)
    tests = _json(os.path.join(ROOT, "outputs", "scenario_tests_last.json"))
    return jsonify({**_system_cache, "lan_ips": _lan_ips(), "tests": tests, "https": request.is_secure})


@app.route("/api/system/run-tests", methods=["POST"])
def api_run_tests():
    """Runs scenario_tests.py (the safety regression suite) and stores the summary."""
    t = time.time()
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scenario_tests.py")], cwd=ROOT, capture_output=True,
                       text=True, timeout=900)
    lines = [l for l in p.stdout.splitlines() if "[PASS]" in l or "[FAIL]" in l or "[SKIP]" in l]
    summary = next((l for l in reversed(p.stdout.splitlines()) if "scenarios passed" in l), "")
    res = {"ok": p.returncode == 0, "summary": summary.strip(), "seconds": round(time.time() - t, 1),
           "results": [l.strip() for l in lines], "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    os.makedirs(OUTPUTS, exist_ok=True)
    with open(os.path.join(OUTPUTS, "scenario_tests_last.json"), "w") as f:
        json.dump(res, f, indent=1)
    return jsonify(res)


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": f"file is larger than the {MAX_UPLOAD_MB} MB upload limit"}), 413


@app.errorhandler(Exception)
def api_error(e):
    """JSON errors for /api/* so the page can show the real status and message instead of an HTML error page."""
    from werkzeug.exceptions import HTTPException
    if not request.path.startswith("/api/"):
        return e if isinstance(e, HTTPException) else ("Internal Server Error", 500)
    if isinstance(e, HTTPException):
        return jsonify({"error": f"{e.name}: {e.description}"}), e.code
    traceback.print_exc()
    return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


# ------------------------------------------------------------------------------------------------ HTTPS (phone camera)
def ensure_self_signed_cert() -> Optional[tuple]:
    """Self-signed certificate for LAN HTTPS (browsers only allow camera access in a secure context)."""
    os.makedirs(CERTS, exist_ok=True)
    crt, key = os.path.join(CERTS, "pathsense-dev.crt"), os.path.join(CERTS, "pathsense-dev.key")
    if os.path.exists(crt) and os.path.exists(key):
        return crt, key
    san = ",".join(["DNS:localhost", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in _lan_ips()])
    import shutil
    exe = shutil.which("openssl") or next((p for p in (r"C:\Program Files\Git\usr\bin\openssl.exe",
                                                       r"C:\Program Files\Git\mingw64\bin\openssl.exe") if os.path.exists(p)), "openssl")
    cmd = [exe, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365", "-keyout", key, "-out", crt,
           "-subj", "/CN=PathSense local dev", "-addext", f"subjectAltName={san}"]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return crt, key
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"[HTTPS] could not create a certificate with openssl ({e}). Install OpenSSL (e.g. Git for Windows "
              "ships one) or place pathsense-dev.crt / pathsense-dev.key in certs/.")
        return None


def main():
    ap = argparse.ArgumentParser(description="PathSense web app")
    ap.add_argument("--port", type=int, default=None, help="default 8000 (http) / 8443 (https)")
    ap.add_argument("--lan", action="store_true", help="listen on all interfaces (phone on the same Wi-Fi)")
    ap.add_argument("--https", action="store_true", help="serve HTTPS with a self-signed dev certificate (needed for phone camera)")
    a = ap.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    port = a.port or (8443 if a.https else 8000)
    ssl_ctx = None
    if a.https:
        ssl_ctx = ensure_self_signed_cert()
        if ssl_ctx is None:
            sys.exit(1)
    scheme = "https" if ssl_ctx else "http"
    print("=" * 64)
    print("  PathSense web app  -  research prototype, not a certified safety system")
    print(f"  Desktop:  {scheme}://localhost:{port}")
    if a.lan:
        for ip in _lan_ips():
            print(f"  Phone:    {scheme}://{ip}:{port}" + ("" if ssl_ctx else "   (camera needs --https on a phone)"))
    print("=" * 64)
    app.run(host=host, port=port, threaded=True, ssl_context=ssl_ctx, use_reloader=False)


if __name__ == "__main__":
    main()
