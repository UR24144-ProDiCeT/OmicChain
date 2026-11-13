"""Verification from the raw data (omicchain_verify.py --from-raw, omicchain_replay.py):
public study (FASTQ downloaded again from a mock ENA, or taken from a folder), user FASTQ, count matrix;
failure cases (file changed at the source, wrong file, missing reference, different counts).
Real Rsubread, mock DESeq2.
Usage: python tests/test_replay.py   (R + Rsubread required; R_LIBS if needed)"""
import csv, hashlib, json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sim_study import SimWorld

TMP = Path(tempfile.mkdtemp(prefix="replay test "))
PROJ = TMP / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"\n      -> {str(detail)[-2500:]}"))
    if not cond: FAILED.append(name)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

W = SimWorld(TMP / "world")
design = [("GSM1", "u1", "Untreated", "N1", ["SRR101"]), ("GSM2", "d1", "Dex", "N1", ["SRR102"]),
          ("GSM3", "u2", "Untreated", "N2", ["SRR103", "SRR113"]), ("GSM4", "d2", "Dex", "N2", ["SRR104"]),
          ("GSM5", "o1", "Other", "N1", ["SRR105"])]
geo = []
for gsm, title, trt, cl, runs in design:
    srx = "SRX" + gsm[3:]
    for run in runs:
        W.add_run("PRJNA9", run, "SAMN" + gsm[3:], title, n=6000, up_factor=5.0 if trt == "Dex" else 1.0, gsm=gsm, srx=srx)
    geo.append((gsm, title, {"treatment": trt, "cell line": cl}, srx))
W.add_geo("GSE9", "Simulated study", "PRJNA9", geo)
env = W.serve()
os.environ.update(env)
os.environ.update({"MOCK_REAL_RSCRIPT": shutil.which("Rscript"), "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
for k in ("MOCK_NOISE", "MOCK_DESEQ2_VERSION", "MOCK_FAIL"): os.environ.pop(k, None)
sys.path.insert(0, str(PROJ))
import omicchain_core as occ, omicchain_verify as ov, omicchain_design as odz, omicchain_matrix as om, omicchain_replay as orp
import pandas as pd

CACHE = TMP / "ref cache"
r = subprocess.run([sys.executable, str(PROJ / "omicchain_reference.py"), "--ref-dir", str(CACHE), "prepare", "--index", "gapped"],
                   capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr

def run_upstream(study, cond, ref, test):
    r = subprocess.run([sys.executable, str(PROJ / "omicchain_upstream.py"), "run", "--study", str(study), "--condition", cond,
                        "--reference-level", ref, "--test-level", test, "--ref-dir", str(CACHE), "--cores", "4"],
                       cwd=PROJ, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    return study / "upstream" / "gapped_gencode48"

def certify(out, provenance, keep=None, name="crate"):
    """Like 'Use these counts' + Pipeline tab: load, metadata (selection), pipeline, crate."""
    shutil.copy(out / "expression_input.tsv", PROJ / "expression_input.tsv")
    src = pd.read_csv(out / "metadata.tsv", sep="\t", dtype=str)
    if keep:
        src = src[src["sample"].isin(keep)]
    shutil.copy(out / provenance, PROJ / "data_origin.json")
    ctrl = src.loc[src["group"] == "control", "sample"].tolist(); trt = src.loc[src["group"] == "treatment", "sample"].tolist()
    odz.build_metadata(ctrl, trt, src).to_csv(PROJ / "metadata.tsv", sep="\t", index=False)
    r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0"],
                       cwd=PROJ, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    R = PROJ / "results" / "padj_0.05_lfc_1.0"
    z = occ.make_crate_zip(R)
    dest = TMP / "reviewer" / name; dest.mkdir(parents=True)
    shutil.copy(z, dest / "crate.zip"); shutil.copy(PROJ / "metadata.tsv", dest / "metadata.tsv")
    shutil.copy(PROJ / "expression_input.tsv", dest / "expression_input.tsv")
    return dest, (R / "bundle_hash.txt").read_text()

def verify(d, **kw):
    kw.setdefault("metadata", d / "metadata.tsv")
    return ov.rerun_verify(d / "crate.zip", project=PROJ, log=lambda *a: None, ref_dir=kw.pop("ref_dir", CACHE), **kw)

STUDY = PROJ / "studies" / "study_GSE9"
r = subprocess.run([sys.executable, str(PROJ / "omicchain_fetch.py"), "GSE9", "--dry-run", "--out", str(STUDY)], cwd=PROJ,
                   capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
OUT = run_upstream(STUDY, "treatment", "Untreated", "Dex")
GEO, BUNDLE = certify(OUT, "upstream_provenance.json", name="geo")
origin = ov.crate_origin(GEO / "crate.zip")
check("0. crate_origin: provenance read from the zip (public study)", origin and orp.describe(origin)["kind"] == "public"
      and orp.describe(origin)["files"] == 10, origin and orp.describe(origin))

rep = verify(GEO, expression=GEO / "expression_input.tsv", expected=BUNDLE)
check("1. downstream only: REPRODUCED, scope 'downstream'", rep["verdict"] == "REPRODUCED" and rep["scope"] == "downstream", rep)

rep = verify(GEO, from_raw=True, expected=BUNDLE)
steps = {s["name"]: s for s in rep["steps"]}
check("2. from the raw data (ENA download): REPRODUCED, scope 'raw'", rep["verdict"] == "REPRODUCED"
      and rep["scope"] == "raw" and rep["exit_code"] == 0, (rep["verdict"], rep["steps"], rep.get("raw")))
check("2. ... 10 FASTQ downloaded again, upstream outputs identical (counts, table, metadata, QC)",
      rep.get("raw", {}).get("downloaded") == 10 and len(rep["raw"]["outputs"]) == 4 and all(o["same"] for o in rep["raw"]["outputs"]),
      rep.get("raw"))
check("2. ... upstream code fingerprint in the certified provenance, and installed code = certified code",
      set(origin["tool"]["code_sha256"]) == {"upstream.smk", "upstream_align.R", "upstream_count.R", "omicchain_upstream.py"}
      and all(len(v) == 64 for v in origin["tool"]["code_sha256"].values()) and rep["raw"].get("code_same") is True, (origin["tool"], rep.get("raw")))
check("2. ... step 'raw data -> expression table' before the downstream re-run", list(steps)[:4] ==
      ["crate integrity", "code of the crate", "raw data -> expression table", "input data are the certified ones"], list(steps))
check("2. ... replay folder removed after success", rep.get("raw_workdir_removed") and not Path(rep["raw_workdir"]).exists(), rep.get("raw_workdir"))

rep = verify(GEO, from_raw=True, fastq_dir=STUDY / "fastq", download=False, raw_workdir=TMP / "rw_local")
check("3. FASTQ taken from a folder (nothing downloaded): REPRODUCED", rep["verdict"] == "REPRODUCED" and rep["raw"]["downloaded"] == 0,
      (rep["verdict"], rep["steps"]))
check("3. ... replay folder given explicitly: kept", (TMP / "rw_local").is_dir())

row = W.ena_rows["PRJNA9"][0]; good = row["fastq_md5"]
row["fastq_md5"] = "0" * 32 + good[32:]
rep = verify(GEO, from_raw=True, raw_workdir=TMP / "rw_changed")
check("4. ENA serves another file: NOT VERIFIED (raw data unavailable), not NOT REPRODUCED",
      rep["verdict"].startswith("NOT VERIFIED") and "no longer available" in rep["steps"][-1]["detail"] and rep["exit_code"] == 1,
      (rep["verdict"], rep["steps"]))
row["fastq_md5"] = good

rep = verify(GEO, from_raw=True, fastq_dir=STUDY / "fastq", download=False, ref_dir=TMP / "empty cache", raw_workdir=TMP / "rw_noref")
check("5. missing reference: NOT VERIFIED, message to prepare it", rep["verdict"].startswith("NOT VERIFIED")
      and "omicchain_reference.py prepare" in rep["steps"][-1]["detail"], rep["steps"])

man_p = CACHE / "gencode_v48" / "reference.json"; man_txt = man_p.read_text()
m = json.loads(man_txt); m["indexes"]["gapped"]["fingerprint"] = "f" * 64; man_p.write_text(json.dumps(m))
rep = verify(GEO, from_raw=True, fastq_dir=STUDY / "fastq", download=False, raw_workdir=TMP / "rw_badref")
check("6. cache index different from the certified index: NOT VERIFIED", rep["verdict"].startswith("NOT VERIFIED")
      and "not the certified one" in rep["steps"][-1]["detail"], rep["steps"])
man_p.write_text(man_txt)

P2 = TMP / "proj2"; shutil.copytree(PROJ, P2, symlinks=True)
cnt = (P2 / "upstream_count.R").read_text()
marker = "writeLines(paste(names(m), as.integer(m)"
assert marker in cnt, cnt[-1500:]
(P2 / "upstream_count.R").write_text(cnt.replace(marker, "m <- m + 1L\n  " + marker, 1))
rep = ov.rerun_verify(GEO / "crate.zip", project=P2, log=lambda *a: None, ref_dir=CACHE, from_raw=True, fastq_dir=STUDY / "fastq",
                      download=False, metadata=GEO / "metadata.tsv", expression=GEO / "expression_input.tsv", raw_workdir=TMP / "rw_diff")
check("7. rebuilt table different: NOT REPRODUCED, downstream not replayed", rep["verdict"] == "NOT REPRODUCED"
      and rep["steps"][-1]["name"] == "raw data -> expression table" and not rep["steps"][-1]["ok"], (rep["verdict"], rep["steps"]))
check("7. ... upstream code different from the certified code is reported (file named)", rep["raw"].get("code_same") is False
      and any("not the certified one" in w and "upstream_count.R" in w for w in rep["warnings"]), rep["warnings"])
cmp = rep.get("raw", {}).get("comparison", {})
check("7. ... numeric comparison of the counts", cmp.get("genes_with_different_counts", 0) > 0, rep.get("raw"))

rj = TMP / "report.json"
r = subprocess.run([sys.executable, str(PROJ / "omicchain_verify.py"), str(GEO / "crate.zip"), "--metadata", str(GEO / "metadata.tsv"),
                    "--from-raw", "--fastq-dir", str(STUDY / "fastq"), "--ref-dir", str(CACHE), "--project", str(PROJ),
                    "--raw-workdir", str(TMP / "rw_cli"), "--report-json", str(rj)], cwd=PROJ, capture_output=True, text=True)
check("8. CLI --from-raw: code 0, verdict 'from the raw data', JSON report", r.returncode == 0 and "REPRODUCED (from the raw data)" in r.stdout
      and json.loads(rj.read_text())["scope"] == "raw", r.stdout[-2000:] + r.stderr[-1500:])

SUB, B_SUB = certify(OUT, "upstream_provenance.json", keep=["GSM2", "GSM3", "GSM4"], name="subset")
rep = verify(SUB, from_raw=True, fastq_dir=STUDY / "fastq", download=False, raw_workdir=TMP / "rw_subset", expected=B_SUB)
check("9. sample dropped in the Pipeline: rebuilt table identical, REPRODUCED, upstream metadata not compared",
      rep["verdict"] == "REPRODUCED" and [o["name"] for o in rep["raw"]["outputs"]] == ["expression_input.tsv", "counts_by_gene_id.tsv", "qc_summary.tsv"]
      and any("not compared" in w for w in rep["warnings"]), (rep["verdict"], rep["steps"], rep.get("raw")))

LOCAL_SRC = TMP / "my fastq"; LOCAL_SRC.mkdir()
for gsm, title, trt, cl, runs in design[:4]:
    for m in (1, 2):
        shutil.copy(STUDY / "fastq" / f"{runs[0]}_{m}.fastq.gz", LOCAL_SRC / f"{title}_R{m}.fastq.gz")
LSTUDY = PROJ / "studies" / "local #1 mine"
r = subprocess.run([sys.executable, str(PROJ / "omicchain_local.py"), "register", str(LOCAL_SRC), "--study", str(LSTUDY)],
                   capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
t, info = odz.load_annotations(LSTUDY)
info["user_columns"] = ["condition"]; t["condition"] = ["24" if s.startswith("d") else "0" for s in t["sample"]]
odz.save_annotations(LSTUDY, t, info)
LOUT = run_upstream(LSTUDY, "condition", "0", "24")
lprov = json.loads((LOUT / "upstream_provenance.json").read_text())
lmeta = pd.read_csv(LOUT / "metadata.tsv", sep="\t", dtype=str)
check("10. levels '0' / '24' and folder 'local #1 mine': alignment done, groups correct",
      lprov["design"]["reference_level"] == "0" and lprov["design"]["test_level"] == "24"
      and dict(zip(lmeta["condition"], lmeta["group"])) == {"0": "control", "24": "treatment"}, (lprov["design"], lmeta))
LOC, B_LOC = certify(LOUT, "upstream_provenance.json", name="local")
check("10. crate_origin: user FASTQ", orp.describe(ov.crate_origin(LOC / "crate.zip"))["kind"] == "local")
rep = verify(LOC, from_raw=True, expected=B_LOC, raw_workdir=TMP / "rw_l0")
check("10. without a FASTQ folder: NOT VERIFIED, asks for the folder", rep["verdict"].startswith("NOT VERIFIED")
      and "give the folder" in rep["steps"][-1]["detail"], rep["steps"])
COPY = TMP / "reviewer copy of fastq"; shutil.copytree(LOCAL_SRC, COPY)
rep = verify(LOC, from_raw=True, fastq_dir=COPY, expected=B_LOC)
check("11. user FASTQ (copy at the reviewer, md5 + SHA-256): REPRODUCED from the raw data",
      rep["verdict"] == "REPRODUCED" and rep["scope"] == "raw" and all(o["same"] for o in rep["raw"]["outputs"]), (rep["verdict"], rep["steps"]))
victim = COPY / "u1_R1.fastq.gz"; victim.write_bytes(victim.read_bytes()[:-10] + b"0123456789")
rep = verify(LOC, from_raw=True, fastq_dir=COPY, raw_workdir=TMP / "rw_l2")
check("12. a FASTQ modified (same name): NOT VERIFIED, file named", rep["verdict"].startswith("NOT VERIFIED")
      and "u1_R1.fastq.gz" in rep["steps"][-1]["detail"] and "another content" in rep["steps"][-1]["detail"], rep["steps"])

mat = pd.read_csv(OUT / "expression_input.tsv", sep="\t", index_col=0).rename(columns=lambda c: "S_" + c).astype(float)
mat.iloc[0, 0] += 0.4
MFILE = TMP / "my counts.csv"; mat.to_csv(MFILE)
mstudy, _ = om.import_matrix(MFILE.read_bytes(), MFILE.name, PROJ / "studies")
mt = pd.read_csv(mstudy / "samples.tsv", sep="\t", dtype=str).fillna("")
mt["condition"] = ["ctrl" if c in ("S_GSM1", "S_GSM3") else "trt" for c in mt["sample"]]
om.comparison_files(mstudy, mt, "condition", "ctrl", "trt", ["S_GSM1", "S_GSM3", "S_GSM2", "S_GSM4"], mstudy / "comparison")
MAT, B_MAT = certify(mstudy / "comparison", "matrix_provenance.json", name="matrix")
rep = verify(MAT, from_raw=True, matrix=MFILE, expected=B_MAT, raw_workdir=TMP / "rw_m")
check("13. matrix: import code fingerprint certified and identical", rep["raw"].get("code_same") is True, rep.get("raw"))
check("13. original matrix: REPRODUCED from the matrix", rep["verdict"] == "REPRODUCED" and rep["raw"]["kind"] == "matrix"
      and rep["raw"]["outputs"] == [{"name": "expression_input.tsv", "same": True}], (rep["verdict"], rep["steps"], rep.get("raw")))
other = TMP / "other.csv"; other.write_text(MFILE.read_text() + "\n")
rep = verify(MAT, from_raw=True, matrix=other, raw_workdir=TMP / "rw_m2")
check("14. other matrix: NOT VERIFIED (not the certified matrix)", rep["verdict"].startswith("NOT VERIFIED")
      and "not the certified count matrix" in rep["steps"][-1]["detail"], rep["steps"])
rep = verify(MAT, from_raw=True, raw_workdir=TMP / "rw_m3")
check("14. matrix not provided: NOT VERIFIED, expected file name", rep["verdict"].startswith("NOT VERIFIED") and "my counts.csv" in rep["steps"][-1]["detail"], rep["steps"])

(PROJ / "data_origin.json").unlink()
r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0"], cwd=PROJ,
                   capture_output=True, text=True); assert r.returncode == 0
NO = TMP / "reviewer" / "none"; NO.mkdir(); shutil.copy(occ.make_crate_zip(PROJ / "results" / "padj_0.05_lfc_1.0"), NO / "crate.zip")
shutil.copy(PROJ / "metadata.tsv", NO / "metadata.tsv")
rep = verify(NO, from_raw=True)
check("15. analysis without provenance: clear message (only the downstream part can be replayed)", "only the downstream part" in rep["steps"][-1]["detail"]
      and ov.crate_origin(NO / "crate.zip") is None, rep["steps"])

print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
