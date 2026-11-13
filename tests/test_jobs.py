"""omicchain_jobs.py: background tasks.  Usage: python tests/test_jobs.py"""
import os, signal, subprocess, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parent))
import omicchain_jobs as oj
ROOT = Path(tempfile.mkdtemp(prefix="jobs test ")) / "jobs"
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)
def wait(job_id, timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        s = oj.status(ROOT, job_id)
        if s["status"] != "running": return s
        time.sleep(0.3)
    return oj.status(ROOT, job_id)
PY = sys.executable

script = "import time\nfor i in range(1, 5):\n    print(f'{i} of 4 steps ({i*25}%) done', flush=True); time.sleep(0.4)\n"
j = oj.start(ROOT, [PY, "-c", script], label="Test OK", key="k1", cwd=ROOT.parent)
time.sleep(0.9)
s = oj.status(ROOT, j)
check("1. running right after the start", s["status"] == "running", s)
p = oj.progress(ROOT, j)
check("1. progress read from the log (Snakemake)", p is not None and 0 < p < 1, p)
check("1. same key while it runs -> same task", oj.start(ROOT, [PY, "-c", "pass"], label="x", key="k1") == j)
s = wait(j)
check("1. finished: done, code 0", s["status"] == "done" and s["exit_code"] == 0, s)
check("1. full log", "4 of 4 steps (100%) done" in oj.log_tail(ROOT, j) and oj.progress(ROOT, j) == 1.0)
check("1. the command ran in the requested folder", s["cwd"] == str(ROOT.parent.resolve()))

j2 = oj.start(ROOT, [PY, "-c", "import sys; print('boom'); sys.exit(3)"], label="Test KO")
s = wait(j2)
check("2. failure: failed, code 3, message in the log", s["status"] == "failed" and s["exit_code"] == 3 and "boom" in oj.log_tail(ROOT, j2), s)
j3 = oj.start(ROOT, ["command-that-does-not-exist-xyz"], label="Not found")
s = wait(j3)
check("2. command not found: failed with an explanation", s["status"] == "failed" and "could not be started" in oj.log_tail(ROOT, j3), oj.log_tail(ROOT, j3))

marker = ROOT.parent / "child_alive.txt"
child = f"import time\nfrom pathlib import Path\nwhile True:\n    Path(r'{marker}').write_text(str(time.time())); time.sleep(0.2)\n"
parent = f"import subprocess, sys\nsubprocess.run([sys.executable, '-c', {child!r}])\n"
j4 = oj.start(ROOT, [PY, "-c", parent], label="Long", key="long")
time.sleep(1.5)
check("3. child started", marker.exists())
check("3. cancellation accepted", oj.cancel(ROOT, j4))
time.sleep(1.5)
t1 = marker.stat().st_mtime; time.sleep(1.0); t2 = marker.stat().st_mtime
check("3. the child process (grandchild of the application) is stopped too", t1 == t2, (t1, t2))
s = oj.status(ROOT, j4)
check("3. status cancelled, kept afterwards", s["status"] == "cancelled", s)
check("3. a new task with the same key can start after a cancellation", oj.start(ROOT, [PY, "-c", "pass"], label="y", key="long") != j4)

oj.HEARTBEAT_STALE = 4
j5 = oj.start(ROOT, [PY, "-c", "import time; time.sleep(60)"], label="Killed")
time.sleep(1.0)
pid = oj.status(ROOT, j5)["wrapper_pid"]
if os.name == "nt":
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
else:
    os.killpg(os.getpgid(pid), signal.SIGKILL)
time.sleep(5)
s = oj.status(ROOT, j5)
check("4. killed without a result -> interrupted (no heartbeat any more)", s["status"] == "interrupted", s)

import importlib; oj2 = importlib.reload(oj)
jobs = {x["id"]: x["status"] for x in oj2.list_jobs(ROOT)}
check("5. states found again after reloading the module", jobs[j] == "done" and jobs[j2] == "failed" and jobs[j4] == "cancelled", jobs)
check("5. list sorted, most recent first", oj2.list_jobs(ROOT)[0]["created_ts"] >= oj2.list_jobs(ROOT)[-1]["created_ts"])
check("5. deletion of a finished task", oj2.remove(ROOT, j2) and oj2.status(ROOT, j2) is None)

j6 = oj2.start(ROOT, [PY, "-c", "import sys; sys.stdout.write('    10%\\r    55%\\r'); print(); print('done')"], label="pct")
wait(j6)
check("6. last percentage taken (bar rewritten with \\r)", oj2.progress(ROOT, j6) == 0.55, oj2.progress(ROOT, j6))
launcher = (f"import sys; sys.path.insert(0, {str(HERE.parent)!r}); import omicchain_jobs as oj; "
            f"print(oj.start({str(ROOT)!r}, [sys.executable, '-c', 'import time; time.sleep(2); print(\\'finished\\')'], label='orphan'))")
r = subprocess.run([PY, "-c", launcher], capture_output=True, text=True, timeout=30)
jid = r.stdout.strip().splitlines()[-1]
check("7. the launcher has ended, the task still runs", oj2.status(ROOT, jid)["status"] == "running", r.stdout + r.stderr)
s = wait(jid)
check("7. ... and ends normally", s["status"] == "done" and "finished" in oj2.log_tail(ROOT, jid), s)

print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
