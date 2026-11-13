"""Tab 'Data & reference': prepare / import / verify an index as a background task (AppTest, real Rsubread).
Usage: python tests/test_app_reference.py   (R + Rsubread required; R_LIBS if needed)"""
import gzip, hashlib, http.server, os, random, subprocess, sys, tempfile, threading, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="app ref "))
PROJ = TMP / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
SRV = TMP / "srv" / "release_48"; SRV.mkdir(parents=True); random.seed(3)
seq = "".join(random.choice("ACGT") for _ in range(300_000))
(SRV / "GRCh38.primary_assembly.genome.fa.gz").write_bytes(gzip.compress((">chr1\n" + "\n".join(seq[i:i+60] for i in range(0, len(seq), 60)) + "\n").encode()))
(SRV / "gencode.v48.primary_assembly.annotation.gtf.gz").write_bytes(gzip.compress(b'chr1\tt\texon\t10\t500\t.\t+\t.\tgene_id "G1"; gene_name "A";\n'))
(SRV / "MD5SUMS").write_text("".join(f"{hashlib.md5((SRV / n).read_bytes()).hexdigest()}  {n}\n" for n in sorted(os.listdir(SRV))))
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(TMP / "srv"), **k)
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
from eth_account import Account
w = Account.create()
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(),
                   "OMICCHAIN_DISK_NEED_GB": "0.1", "OMICCHAIN_GENCODE_DIR": f"http://127.0.0.1:{srv.server_address[1]}/release_{{release}}",
                   "OMICCHAIN_REF_DIR": str(TMP / "ref cache")})
os.chdir(PROJ); sys.path.insert(0, str(PROJ))
import omicchain_jobs as oj
from streamlit.testing.v1 import AppTest
JOBS = PROJ / "jobs"
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-1500:]}"))
    if not cond: FAILED.append(name)
def btn(at, label): return next(b for b in at.button if label in b.label)
def texts(els): return " | ".join(str(e.value) for e in els)
def wait_jobs(timeout=600):
    t = time.time()
    while time.time() - t < timeout and any(j["status"] == "running" for j in oj.list_jobs(JOBS)): time.sleep(1)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=120); at.run()
check("0. application loaded, Data & reference tab", not at.exception and any("Data & reference" in h.value for h in at.header), texts(at.exception))
check("0. default cache = OMICCHAIN_REF_DIR", at.text_input(key="ref_dir").value == str(TMP / "ref cache"))
check("1. nothing prepared at the start", sum("Not prepared yet" in x.value for x in at.warning) == 2, texts(at.warning))

btn(at, "Prepare the gapped index").click().run()
jobs = oj.list_jobs(JOBS)
check("2. background task started", len(jobs) == 1 and jobs[0]["status"] == "running" and "gapped" in jobs[0]["label"], jobs)
check("2. during the task: 'Task in progress', no second button", any("Task in progress" in i.value for i in at.info)
      and not any("Prepare the gapped" in b.label for b in at.button), texts(at.info))
btn_count = len([b for b in at.button if "Cancel" in b.label])
check("2. cancel button available", btn_count == 1)
wait_jobs(); at.run()
check("3. task finished (done)", oj.list_jobs(JOBS)[0]["status"] == "done", oj.log_tail(JOBS, oj.list_jobs(JOBS)[0]["id"]))
check("3. gapped index ready, fingerprint shown", any("Ready" in s.value for s in at.success) and any("Fingerprint" in c.value for c in at.caption), texts(at.success))

other = TMP / "Point9 Rsubread" / "index_full"; other.mkdir(parents=True)
subprocess.run(["Rscript", str(PROJ / "build_index.R"), str(SRV / "GRCh38.primary_assembly.genome.fa.gz"), str(other / "GRCh38"), "full",
                str(other / "r.dcf")], cwd=other, check=True, capture_output=True); (other / "r.dcf").unlink()
at.text_input(key="import_path_full").input(str(TMP / "nowhere" / "GRCh38")).run()
check("4. path without index -> error, no button", any("No Rsubread index found" in e.value for e in at.error)
      and not any("Import this index" in b.label for b in at.button), texts(at.error))
at.text_input(key="import_path_full").input(f'"{other / "GRCh38"}"').run()
at.radio(key="import_mode_full").set_value("Use it where it is (no copy)").run()
btn(at, "Import this index").click().run()
wait_jobs(); at.run()
check("5. full index referenced in place", any("used in place" in s.value for s in at.success) and not (TMP / "ref cache" / "gencode_v48" / "index_full").exists(),
      texts(at.success))

btn(at, "Verify fingerprints").click().run(); wait_jobs(); at.run()
v = [j for j in oj.list_jobs(JOBS) if j["label"].startswith("Verify")][0]
check("6. verification as a background task: OK", v["status"] == "done" and "Reference intact" in oj.log_tail(JOBS, v["id"]), oj.log_tail(JOBS, v["id"]))

oj.start(JOBS, [sys.executable, "-c", "import time; print('working', flush=True); time.sleep(120)"], label="Long task", key="long")
oj.start(JOBS, [sys.executable, "-c", "import sys; print('something went wrong'); sys.exit(2)"], label="Failing task")
time.sleep(2); at.run()
btn(at, "Cancel").click().run(); time.sleep(1)
check("7. cancel from the interface", next(j for j in oj.list_jobs(JOBS) if j["label"] == "Long task")["status"] == "cancelled")
at.run()
check("7. failed task: log visible", any("something went wrong" in c.value for c in at.code), texts(at.code))
n = len(oj.list_jobs(JOBS)); next(b for b in at.button if "Remove from the list" in b.label).click().run()
check("8. remove a finished task from the list", len(oj.list_jobs(JOBS)) == n - 1)
check("9. no exception", not at.exception, texts(at.exception))
srv.shutdown()
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
