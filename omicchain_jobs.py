"""Background tasks (downloads, index building, alignment): each task is a detached process with a log, a heartbeat and a result file."""

import argparse
import datetime as _dt
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HEARTBEAT_EVERY = 3
HEARTBEAT_STALE = 30
IS_WINDOWS = os.name == "nt"


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start(root, command, *, label, key=None, cwd=None, env=None):
    """Start `command` (argument list) as a background task and return its id; a running task with the same `key` is reused."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if key:
        for job in list_jobs(root):
            if job.get("key") == key and job["status"] == "running":
                return job["id"]
    job_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = root / job_id
    d.mkdir()
    info = {"id": job_id, "label": label, "key": key, "command": [str(c) for c in command],
            "cwd": str(Path(cwd or os.getcwd()).resolve()), "created": _now(), "created_ts": time.time()}
    _write_json(d / "job.json", info)
    (d / "heartbeat").write_text(str(time.time()))
    wrapper = [sys.executable, str(Path(__file__).resolve()), "_run", str(d.resolve())]
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  cwd=info["cwd"], env=env, close_fds=True)
    if IS_WINDOWS:
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008
                                   | subprocess.CREATE_NO_WINDOW)
    else:
        kwargs["start_new_session"] = True
    try:
        if IS_WINDOWS:
            proc = subprocess.Popen(wrapper, **dict(kwargs, creationflags=kwargs["creationflags"] | 0x01000000))
        else:
            proc = subprocess.Popen(wrapper, **kwargs)
    except OSError:
        proc = subprocess.Popen(wrapper, **kwargs)
    info["wrapper_pid"] = proc.pid
    _write_json(d / "job.json", info)
    return job_id


def status(root, job_id):
    """Full state of a task (dict), or None if it does not exist."""
    d = Path(root) / job_id
    info = _read_json(d / "job.json")
    if not info:
        return None
    result = _read_json(d / "result.json")
    if result:
        info.update(result)
    else:
        try:
            beat = float((d / "heartbeat").read_text())
        except (OSError, ValueError):
            beat = info.get("created_ts", 0)
        info["status"] = "running" if time.time() - beat < HEARTBEAT_STALE else "interrupted"
    end = info.get("ended_ts") or time.time()
    info["elapsed_s"] = max(0.0, end - info.get("created_ts", end))
    info["dir"] = str(d)
    return info


def list_jobs(root):
    root = Path(root)
    if not root.is_dir():
        return []
    jobs = [status(root, p.name) for p in root.iterdir() if (p / "job.json").is_file()]
    return sorted([j for j in jobs if j], key=lambda j: j.get("created_ts", 0), reverse=True)


def log_tail(root, job_id, lines=40):
    path = Path(root) / job_id / "log.txt"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    out = [ln.rsplit("\r", 1)[-1] for ln in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(out[-lines:])


_SNAKEMAKE = re.compile(r"(\d+) of (\d+) steps \((\d+(?:\.\d+)?)%\) done")
_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?) ?%")


def progress(root, job_id):
    """Progress fraction (0-1) read from the log, or None when unknown."""
    tail = log_tail(root, job_id, lines=200)
    m = _SNAKEMAKE.findall(tail)
    if m:
        done, total, _ = m[-1]
        return int(done) / int(total) if int(total) else None
    m = [float(x) for x in _PERCENT.findall(tail) if float(x) <= 100]
    return m[-1] / 100 if m else None


def cancel(root, job_id):
    """Stop the task and all its child processes."""
    d = Path(root) / job_id
    info = status(root, job_id)
    if not info or info["status"] != "running":
        return False
    pid = info.get("wrapper_pid")
    _write_json(d / "result.json", {"status": "cancelled", "exit_code": None,
                                    "ended": _now(), "ended_ts": time.time()})
    if pid:
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    return True


def remove(root, job_id):
    """Delete a finished task (its folder)."""
    import shutil
    info = status(root, job_id)
    if info and info["status"] != "running":
        shutil.rmtree(Path(root) / job_id, ignore_errors=True)
        return True
    return False


def _run(job_dir):
    d = Path(job_dir)
    info = _read_json(d / "job.json")
    stop = threading.Event()

    def beat():
        while not stop.is_set():
            try:
                (d / "heartbeat").write_text(str(time.time()))
            except OSError:
                pass
            stop.wait(HEARTBEAT_EVERY)

    threading.Thread(target=beat, daemon=True).start()
    code, error = None, None
    with open(d / "log.txt", "ab", buffering=0) as log:
        log.write(f"[{_now()}] $ {subprocess.list2cmdline(info['command'])}\n".encode())
        try:
            env = dict(os.environ, PYTHONUNBUFFERED="1")
            kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
            proc = subprocess.Popen(info["command"], cwd=info["cwd"], stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, **kw)
            code = proc.wait()
        except OSError as e:
            error = str(e)
            log.write(f"\nERROR: the command could not be started: {e}\n".encode())
        log.write(f"\n[{_now()}] fin, code {code}\n".encode())
    stop.set()
    if not (d / "result.json").exists():
        _write_json(d / "result.json", {"status": "done" if code == 0 else "failed", "exit_code": code,
                                        "error": error, "ended": _now(), "ended_ts": time.time()})


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "_run":
        _run(argv[1])
        return 0
    ap = argparse.ArgumentParser(description="Taches de fond d'OmicChain")
    ap.add_argument("--root", default="jobs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("cancel"); c.add_argument("id")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for j in list_jobs(a.root):
            print(f"{j['id']}  {j['status']:<11} {j['elapsed_s'] / 60:6.1f} min  {j['label']}")
    else:
        print("cancelled" if cancel(a.root, a.id) else "nothing to cancel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
