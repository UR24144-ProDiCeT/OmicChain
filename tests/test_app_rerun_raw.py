"""Verify tab, 'Everything - from the raw data' (AppTest, mock Rscript): matrix replayed end to end as a
background task; public study / local FASTQ: what is asked, reference status, button condition.
Usage: python tests/test_app_rerun_raw.py"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="app rerun raw "))
PROJ = TMP / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
from eth_account import Account
w = Account.create()
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(),
                   "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
for k in ("MOCK_NOISE", "MOCK_DESEQ2_VERSION", "MOCK_FAIL"): os.environ.pop(k, None)
os.chdir(PROJ); sys.path.insert(0, str(PROJ))
import pandas as pd
import omicchain_core as occ, omicchain_matrix as om, omicchain_design as odz, omicchain_jobs as oj
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-2000:]}"))
    if not cond: FAILED.append(name)
def texts(els): return " | ".join(e.value for e in els)
def snake():
    r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    R = PROJ / "results" / "padj_0.05_lfc_1.0"
    return occ.make_crate_zip(R).read_bytes(), (R / "bundle_hash.txt").read_text()

MFILE = TMP / "counts.tsv"
MFILE.write_text("gene\tA1\tA2\tB1\tB2\n" + "".join(f"G{i}\t{10 + i}\t{12 + i}\t{30 + 2 * i}\t{33 + i}\n" for i in range(1200)))
ds, _ = om.import_matrix(MFILE.read_bytes(), MFILE.name, PROJ / "studies")
t = pd.read_csv(ds / "samples.tsv", sep="\t", dtype=str).fillna(""); t["condition"] = ["a", "a", "b", "b"]
om.comparison_files(ds, t, "condition", "a", "b", ["A1", "A2", "B1", "B2"], ds / "comparison")
for n in ("expression_input.tsv",): shutil.copy(ds / "comparison" / n, PROJ / n)
shutil.copy(ds / "comparison" / "matrix_provenance.json", PROJ / "data_origin.json")
src = pd.read_csv(ds / "comparison" / "metadata.tsv", sep="\t", dtype=str)
odz.build_metadata(["A1", "A2"], ["B1", "B2"], src).to_csv(PROJ / "metadata.tsv", sep="\t", index=False)
ZIP, BUNDLE = snake(); META = (PROJ / "metadata.tsv").read_bytes()

from streamlit.testing.v1 import AppTest
RAW = "Everything — from the raw data: FASTQ files or count matrix (hours for FASTQ)"
def app(zip_bytes, meta=META, scope=RAW):
    at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240); at.run()
    ups = {u.label: u for u in at.get("file_uploader")}
    ups["RO-Crate (.zip)"].upload("omicchain-crate.zip", zip_bytes, "application/zip")
    at.run()
    assert not any("metadata" in u.label.lower() for u in at.get("file_uploader")), "metadata asked although it is in the crate"
    next(r for r in at.radio if r.label == "What to re-run").set_value(scope).run()
    return at
raw_btn = lambda at: next(b for b in at.button if "from the raw data" in b.label)

at = app(ZIP)
check("1. matrix: what is asked (original file, SHA-256)", any("count matrix" in i.value and "counts.tsv" in i.value for i in at.info)
      and not at.exception, texts(at.info) + texts(at.exception))
check("1. ... button disabled until the matrix is provided", raw_btn(at).disabled)
next(u for u in at.get("file_uploader") if u.label == "Original count matrix").upload("counts.tsv", MFILE.read_bytes(), "text/tab-separated-values")
at.run()
next(t for t in at.text_input if "Fingerprint published" in t.label).set_value(BUNDLE).run()
check("2. matrix provided: button enabled", not raw_btn(at).disabled, texts(at.error))
raw_btn(at).click().run()
jobs = oj.list_jobs(PROJ / "jobs")
check("2. verification started as a background task", jobs and "Verification from the raw data" in jobs[0]["label"], jobs)
for _ in range(240):
    if oj.status(PROJ / "jobs", jobs[0]["id"])["status"] != "running": break
    time.sleep(0.5)
job = oj.status(PROJ / "jobs", jobs[0]["id"])
check("2. task finished without error", job["status"] == "done", oj.log_tail(PROJ / "jobs", job["id"]))
at.run(); at.run()
check("3. report read back: REPRODUCED FROM THE RAW DATA", any("REPRODUCED FROM THE RAW DATA" in s.value for s in at.success),
      texts(at.success) + texts(at.error) + texts(at.exception))
check("3. ... rebuilt table compared (same)", any("Rebuilt from the raw data" in d.value.columns for d in at.dataframe), [list(d.value.columns) for d in at.dataframe])
check("3. ... no exception", not at.exception, texts(at.exception))

bad = TMP / "other.tsv"; bad.write_text(MFILE.read_text() + "\n")
at = app(ZIP)
next(u for u in at.get("file_uploader") if u.label == "Original count matrix").upload("other.tsv", bad.read_bytes(), "text/tab-separated-values")
at.run(); raw_btn(at).click().run()
jid = oj.list_jobs(PROJ / "jobs")[0]["id"]
for _ in range(240):
    if oj.status(PROJ / "jobs", jid)["status"] != "running": break
    time.sleep(0.5)
at.run(); at.run()
check("4. other matrix: NOT VERIFIED shown, with the reason", any("NOT VERIFIED" in e.value for e in at.error)
      and any("not the certified count matrix" in e.value for e in at.error), texts(at.error) + texts(at.success))

def upstream_origin(local):
    return {"schema": "omicchain.upstream/1", "tool": {"name": "omicchain_upstream", "version": "0.1"},
            "study": {"accession": "LOCAL_x" if local else "GSE77", "user_annotations": None},
            "design": {"condition_column": "condition", "reference_level": "a", "test_level": "b", "samples": ["A1", "A2", "B1", "B2"]},
            "inputs": [{"sample": s, "run": "SRR" + s, "fastq": [f"{s}_1.fastq.gz", f"{s}_2.fastq.gz"], "md5": ["0" * 32] * 2,
                        "md5_source": "local (computed by OmicChain)" if local else "ENA"} for s in ("A1", "A2", "B1", "B2")],
            "reference": {"gencode_release": "48", "index_type": "gapped", "index_fingerprint": "9" * 64, "genome_sha256": "1" * 64,
                          "gtf_sha256": "2" * 64},
            "alignment": {"rsubread_version": "2.24.0"}, "counting": {"strand_specific": 0, "strand_mode": "detected"},
            "outputs": {"expression_input.tsv": sha(PROJ / "expression_input.tsv")}}
EMPTY_REF = TMP / "empty ref"
for local in (False, True):
    (PROJ / "data_origin.json").write_text(json.dumps(upstream_origin(local)))
    z, _ = snake()
    at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240)
    at.session_state["ref_dir"] = str(EMPTY_REF)
    at.run()
    {u.label: u for u in at.get("file_uploader")}["RO-Crate (.zip)"].upload("c.zip", z, "application/zip")
    at.run(); next(r for r in at.radio if r.label == "What to re-run").set_value(RAW).run()
    tag = "local FASTQ" if local else "public study"
    want = "FASTQ files provided by the user" if local else "Public study **GSE77**: 8 FASTQ files"
    check(f"5. {tag}: description of what will be replayed", any(want in i.value for i in at.info) and not at.exception,
          texts(at.info) + texts(at.exception))
    check(f"5. {tag}: missing reference reported, button disabled", any("is not in" in wv.value for wv in at.warning) and raw_btn(at).disabled,
          texts(at.warning))
    next(c for c in at.checkbox if "build the reference" in c.label).check().run()
    if local:
        check("5. local FASTQ: without a folder, button still disabled", raw_btn(at).disabled)
        next(t for t in at.text_input if t.label == "Folder with the FASTQ files").set_value(str(TMP / "nowhere")).run()
        check("5. local FASTQ: missing folder reported", any("Folder not found" in e.value for e in at.error) and raw_btn(at).disabled, texts(at.error))
        next(t for t in at.text_input if t.label == "Folder with the FASTQ files").set_value(str(TMP)).run()
    check(f"5. {tag}: reference preparation accepted -> button enabled", not raw_btn(at).disabled, texts(at.error) + texts(at.warning))

(PROJ / "data_origin.json").unlink()
z, _ = snake()
at = app(z)
check("6. no provenance: only the downstream part is possible (message)", any("only the downstream part" in wv.value for wv in at.warning), texts(at.warning))
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
