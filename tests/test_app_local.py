"""Source 'my own FASTQ': detection (pairs, Illumina lanes), registration (md5 + SHA-256, no copy),
sample sheet, comparison, alignment, provenance. Real Rsubread, mock DESeq2.
Usage: python tests/test_app_local.py   (R + Rsubread required; R_LIBS if needed)"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
from sim_study import SimWorld
TMP = Path(tempfile.mkdtemp(prefix="app local "))
PROJ = TMP / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
sys.path.insert(0, str(PROJ))
import omicchain_local as ol, omicchain_design as odz, pandas as pd
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-2000:]}"))
    if not cond: FAILED.append(name)

U = TMP / "unit"; (U / "sub").mkdir(parents=True)
for n in ["A_S1_L001_R1_001.fastq.gz", "A_S1_L001_R2_001.fastq.gz", "A_S1_L002_R1_001.fastq.gz", "A_S1_L002_R2_001.fastq.gz",
          "B_1.fq.gz", "B_2.fq.gz", "sub/C.fastq.gz", "D_R1.fastq.gz", "notes.txt"]:
    (U / n).write_bytes(b"@r\nACGT\n+\nIIII\n")
f = ol.scan(U)
check("U1. A: 2 paired lanes; B: _1/_2; C: single; D: R1 only", {k: (v["layout"], len(v["units"])) for k, v in f["samples"].items()}
      == {"A": ("PAIRED", 2), "B": ("PAIRED", 1), "C": ("SINGLE", 1), "D": ("SINGLE", 1)}, f["samples"])
check("U2. R1 without R2 reported; non-FASTQ file ignored", any("D: read 1 without its mate" in p for p in f["problems"]) and f["ignored"] == ["notes.txt"], f)
(U / "sub" / "B_1.fq.gz").write_bytes(b"x")
check("U3. same file name twice -> blocking", any("same file name twice" in p for p in ol.scan(U)["problems"]))
(U / "sub" / "B_1.fq.gz").unlink()
t = pd.DataFrame({"sample": ["S1", "S2"], "title": ["ctrl a", "ko b"]})
info = {"user_columns": [], "rules": []}
msg, ok = odz.import_sample_sheet(t, info, b"sample,condition,cell line,2x\nS1,control,L1,a\nS2,KO,L1,b\nS9,KO,L2,c\n", "sheet.csv")
check("U4. CSV sheet: columns added (names fixed), unknown samples ignored", ok and ok["table"]["condition"].tolist() == ["control", "KO"]
      and info["user_columns"] == ["condition", "cell_line", "x_2x"] and "2/3 sample(s) matched on sample" in msg, (msg, info))
msg, ok = odz.import_sample_sheet(t, {"user_columns": [], "rules": []}, b"sample\tgrp\nctrl a\tA\nko b\tB\n", "s.tsv")
check("U5. TSV sheet matched on the title", ok and ok["table"]["grp"].tolist() == ["A", "B"] and "matched on title" in msg, msg)
check("U6. no 'sample' column -> message", odz.import_sample_sheet(t, {"user_columns": [], "rules": []}, b"id,x\n1,2\n", "bad.csv")[1] is None)

W = SimWorld(TMP)
FQ = TMP / "my run 2026"; FQ.mkdir()
plan = [("CTRL1", 1, ["L001", "L002"]), ("CTRL2", 1, ["L001"]), ("KO1", 4, ["L001"]), ("KO2", 4, ["L001"])]
k = 0
for s, fct, lanes in plan:
    for lane in lanes:
        W.add_run("P", f"SRR9{k}", s, s, n=8000, up_factor=fct)
        for m in (1, 2):
            shutil.copy(W.srv / f"vol1/fastq/SRR9{k}/SRR9{k}_{m}.fastq.gz", FQ / f"{s}_S{k}_{lane}_R{m}_001.fastq.gz")
        k += 1
(FQ / "SampleSheet.txt").write_text("not a fastq")
env = W.serve()
from eth_account import Account
w = Account.create()
os.environ.update(env)
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(), "OMICCHAIN_REF_DIR": str(TMP / "ref cache"),
                   "MOCK_REAL_RSCRIPT": shutil.which("Rscript"), "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
r = subprocess.run([sys.executable, str(PROJ / "omicchain_reference.py"), "prepare", "--index", "gapped"], cwd=PROJ, capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
os.chdir(PROJ)
import omicchain_jobs as oj
from streamlit.testing.v1 import AppTest
JOBS = PROJ / "jobs"; STUDY = PROJ / "studies" / "study_LOCAL_my_run_2026"
def btn(at, label): return next(b for b in at.button if label in b.label)
def texts(els): return " | ".join(str(e.value) for e in els)
def wait_jobs(timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout and any(j["status"] == "running" for j in oj.list_jobs(JOBS)): time.sleep(1)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at.run()
at.radio(key="data_source").set_value("My FASTQ files").run()
at.text_input(key="local_folder").input(str(TMP / "nope")).run()
check("1. folder not found -> error", any("Folder not found" in e.value for e in at.error))
at.text_input(key="local_folder").input(f'"{FQ}"').run()
df = next(d for d in at.dataframe if "layout" in d.value.columns).value
check("2. 4 samples detected, CTRL1 on 2 lanes, non-FASTQ file ignored",
      df["sample"].tolist() == ["CTRL1", "CTRL2", "KO1", "KO2"] and df.set_index("sample").loc["CTRL1", "lanes / units"] == 2
      and any("Ignored (not FASTQ): 1" in c.value for c in at.caption), df)
check("2. name proposed from the folder", at.text_input(key="local_name").value == "LOCAL_my_run_2026")
btn(at, "Register the files").click().run(); wait_jobs(); at.run()
check("3. registration: md5 + SHA-256", any("10 file(s) registered" in s.value for s in at.success), texts(at.success) + oj.log_tail(JOBS, oj.list_jobs(JOBS)[0]["id"]))
man = json.loads((STUDY / "fetch_manifest.json").read_text())
f0 = next(x for x in man["files"] if x["sample"] == "KO1" and x["mate"] == 1)
data = Path(f0["path"]).read_bytes()
check("3. fingerprints correct, files not copied", f0["md5"] == hashlib.md5(data).hexdigest() and f0["sha256"] == hashlib.sha256(data).hexdigest()
      and f0["md5_source"].startswith("local") and not (STUDY / "fastq").exists(), f0)
check("4. nothing to compare before annotation", any("No column has at least two values" in i.value for i in at.info), texts(at.info))
sheet = b"sample\tcondition\tcell line\nCTRL1\tcontrol\tL1\nCTRL2\tcontrol\tL2\nKO1\tKO\tL1\nKO2\tKO\tL2\n"
at.file_uploader(key="annot_sheet_LOCAL_my_run_2026").upload("design.tsv", sheet, "text/tab-separated-values").run()
check("5. sheet imported: condition + cell_line", any("2 column(s) imported (condition, cell_line), 4/4 sample(s) matched" in c.value for c in at.caption), texts(at.caption))
check("6. comparison proposed: control vs KO",
      (at.selectbox(key="geo_condition").value, at.selectbox(key="geo_ref").value, at.selectbox(key="geo_test").value) == ("condition", "control", "KO"))
check("6. nothing to download", any("Nothing to download" in c.value for c in at.caption), texts(at.caption))
btn(at, "Download, align and count").click().run(); wait_jobs(); at.run()
log = oj.log_tail(JOBS, oj.list_jobs(JOBS)[0]["id"], 400)
check("7. alignment: no download, counts ready", "nothing to download" in log and any("Counts ready: 4 samples" in s.value for s in at.success),
      log[-1500:] + texts(at.success))
OUT = STUDY / "upstream" / "gapped_gencode48"
prov = json.loads((OUT / "upstream_provenance.json").read_text())
i0 = next(x for x in prov["inputs"] if x["sample"] == "CTRL1")
check("8. provenance: 5 units, local source, md5 + SHA-256, CTRL1 = 2 lanes", len(prov["inputs"]) == 5 and i0["md5_source"].startswith("local")
      and len(i0["sha256"]) == 2 and prov["study"]["user_annotations"]["imported_sheets"][0]["file"] == "design.tsv"
      if prov["study"].get("user_annotations") and "imported_sheets" in prov["study"]["user_annotations"] else False, prov["study"])
qc = pd.read_csv(OUT / "qc_summary.tsv", sep="\t", keep_default_na=False)
check("8. QC: no comparison with the ENA (n/a)", set(qc["fragments_match_ena"].astype(str)) == {"n/a"}, qc)
counts = pd.read_csv(OUT / "counts_by_gene_id.tsv", sep="\t")
c1 = pd.read_csv(OUT / "counts" / "CTRL1_L001.tsv", sep="\t", header=None)[1].sum() + pd.read_csv(OUT / "counts" / "CTRL1_L002.tsv", sep="\t", header=None)[1].sum()
check("8. CTRL1 = sum of its 2 lanes", counts["CTRL1"].sum() == c1)

p = Path(f0["path"]); orig = p.read_bytes(); p.write_bytes(orig + b"\n"); at.run()
check("9. folder changed since registration -> reported", any("folder changed since the files were registered" in x.value for x in at.warning), texts(at.warning))
p.write_bytes(orig); os.utime(p, (f0["mtime"], f0["mtime"])); at.run()
check("9. file restored -> up to date again", any("10 file(s) registered" in s.value for s in at.success), texts(at.warning))
btn(at, "Use these counts for the analysis").click().run()
check("10. Pipeline: cell_line covariate proposed", at.selectbox(key="covariate_choice").options == ["(none)", "cell_line"])
check("11. no exception", not at.exception, texts(at.exception))
W.httpd.shutdown()
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
