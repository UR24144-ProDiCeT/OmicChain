"""Study whose conditions are only in the titles (ENA project, no GEO), with one single-end sample:
user annotation, separate download, comparison on added columns, certified annotations.
Mock ENA / GENCODE, real Rsubread, mock DESeq2.
Usage: python tests/test_app_annotate.py   (R + Rsubread required; R_LIBS if needed)"""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
from sim_study import SimWorld
TMP = Path(tempfile.mkdtemp(prefix="app annotate "))
PROJ = TMP / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
W = SimWorld(TMP)
titles = [("BT379 shRNA18-2", "PAIRED", 4), ("BT379 shRNA18-1", "PAIRED", 4), ("BT379 non targeting C1", "PAIRED", 1),
          ("BT273 shRNA18-1", "PAIRED", 4), ("BT273 non targeting C1", "PAIRED", 1), ("BT273 non targeting C2", "SINGLE", 1),
          ("BT273 shRNA53-1", "PAIRED", 1), ("BT379 non targeting C2", "PAIRED", 1)]
for i, (t, lay, f) in enumerate(titles):
    W.add_run("PRJNA88", f"SRR88{i}", f"SAMN88{i}", t, layout=lay, up_factor=f)
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
os.chdir(PROJ); sys.path.insert(0, str(PROJ))
import omicchain_jobs as oj, omicchain_design as odz, pandas as pd
from streamlit.testing.v1 import AppTest
JOBS = PROJ / "jobs"; STUDY = PROJ / "studies" / "study_PRJNA88"
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-2000:]}"))
    if not cond: FAILED.append(name)
def btn(at, label): return next(b for b in at.button if label in b.label)
def texts(els): return " | ".join(str(e.value) for e in els)
def wait_jobs(timeout=900):
    t = time.time()
    while time.time() - t < timeout and any(j["status"] == "running" for j in oj.list_jobs(JOBS)): time.sleep(1)
def rule(at, src, kw, col, val):
    at.selectbox(key="rule_src_PRJNA88").select(src); at.text_input(key="rule_kw_PRJNA88").input(kw)
    at.selectbox(key="rule_col_PRJNA88").select(col); at.text_input(key="rule_val_PRJNA88").input(val)
    btn(at, "Apply").click().run()

t = pd.DataFrame({"sample": list("abcd"), "title": ["X ctl", "X ko", "Y ctl", "Y ko"], "library_layout": ["PAIRED"] * 3 + ["SINGLE"]})
t["condition"] = ""
t2, n = odz.apply_rule(t, "title", "KO", "condition", "ko")
check("U1. keyword rule, case-insensitive", n == 2 and t2["condition"].tolist() == ["", "ko", "", "ko"])
check("U2. column name: invalid / already present / 'group'", odz.column_name_problem("2x", []) and odz.column_name_problem("title", ["title"])
      and odz.column_name_problem("group", []) and odz.column_name_problem("cell_line", ["title"]) is None)
check("U3. single / paired mix detected", odz.mixed_layouts(t, list("abcd")) == {"PAIRED": ["a", "b", "c"], "SINGLE": ["d"]} and not odz.mixed_layouts(t, list("abc")))

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at.run()
at.text_input(key="geo_accession").input("PRJNA88").run(); btn(at, "Get the study's metadata").click().run()
check("1. ENA metadata (no GEO): single / paired mix warning", any("single-end and paired-end" in x.value for x in at.warning), texts(at.warning))
check("1. nothing to compare until there is an annotation", any("No column has at least two values" in i.value for i in at.info), texts(at.info))
at.text_input(key="annot_newcol_PRJNA88").input("2bad"); btn(at, "Add column").click().run()
check("2. invalid column name refused", any("2bad" in e.value for e in at.error), texts(at.error))
for c in ("condition", "cell_line"):
    at.text_input(key="annot_newcol_PRJNA88").input(c); btn(at, "Add column").click().run()
check("2. two columns added, samples.tsv untouched", json.loads((STUDY / "annotations.json").read_text())["user_columns"] == ["condition", "cell_line"]
      and "condition" not in (STUDY / "samples.tsv").read_text())
rule(at, "title", "non targeting", "condition", "control")
check("3. rule applied: 4 samples", any("4 sample(s) set to condition = control" in c.value for c in at.caption), texts(at.caption))
rule(at, "title", "shrna18", "condition", "shRNA18"); rule(at, "title", "shRNA53", "condition", "shRNA53")
rule(at, "title", "BT379", "cell_line", "BT379"); rule(at, "title", "BT273", "cell_line", "BT273")
ann = pd.read_csv(STUDY / "samples_annotated.tsv", sep="\t", dtype=str).set_index("sample")
check("3. annotated table correct", ann.loc["SAMN880", "condition"] == "shRNA18" and ann.loc["SAMN882", "condition"] == "control"
      and ann.loc["SAMN886", "condition"] == "shRNA53" and ann.loc["SAMN884", "cell_line"] == "BT273", ann[["title", "condition", "cell_line"]])
check("4. comparison proposed: condition, control vs shRNA18",
      (at.selectbox(key="geo_condition").value, at.selectbox(key="geo_ref").value, at.selectbox(key="geo_test").value) == ("condition", "control", "shRNA18"),
      (at.selectbox(key="geo_condition").value, at.selectbox(key="geo_ref").value, at.selectbox(key="geo_test").value))
check("5. single / paired mix in the comparison -> error naming the sample", any("SINGLE: SAMN885" in e.value for e in at.error), texts(at.error))
ms_key = "geo_samples_PRJNA88_condition_control_shRNA18"
at.multiselect(key=ms_key).unselect("SAMN885").run()
check("5. without it: no error left", not any("mix sequencing types" in e.value for e in at.error), texts(at.error))

at.multiselect(key="geo_dl_PRJNA88").set_value(["SAMN880", "SAMN882"]).run()
btn(at, "Download the selected samples").click().run(); wait_jobs(); at.run()
check("6. download only: 2 samples verified", any("2 / 8 samples downloaded" in c.value for c in at.caption)
      and len(list((STUDY / "fastq").glob("*.fastq.gz"))) == 4, texts(at.caption))

btn(at, "Download, align and count").click().run(); wait_jobs(); at.run()
job = oj.list_jobs(JOBS)[0]
check("7. alignment and counting (the rest downloaded on the way)", job["status"] == "done" and any("Counts ready: 6 samples" in s.value for s in at.success),
      oj.log_tail(JOBS, job["id"], 40) + texts(at.success))
OUT = STUDY / "upstream" / "gapped_gencode48"
prov = json.loads((OUT / "upstream_provenance.json").read_text())
ua = prov["study"]["user_annotations"]
check("8. provenance: annotated table certified, user columns and rules",
      prov["study"]["samples_table"] == "samples_annotated.tsv" and ua["user_columns"] == ["condition", "cell_line"]
      and [r["contains"] for r in ua["rules"]] == ["non targeting", "shrna18", "shRNA53", "BT379", "BT273"], prov["study"])
meta = pd.read_csv(OUT / "metadata.tsv", sep="\t")
check("8. metadata.tsv: original condition + cell_line usable as covariate", list(meta.columns) == ["sample", "group", "condition", "title", "cell_line"]
      and prov["design"]["candidate_covariates"] == ["cell_line"], (list(meta.columns), prov["design"]))

rule(at, "title", "shRNA18-2", "cell_line", "BT379b")
check("9. annotations changed -> information, no load button", any("annotations changed" in i.value for i in at.info)
      and not any("Use these counts" in b.label for b in at.button), texts(at.info))
btn(at, "Download, align and count").click().run(); wait_jobs(); at.run()
log = oj.log_tail(JOBS, oj.list_jobs(JOBS)[0]["id"], 400)
check("9. second run: merge only, counts ready again", "rule align:" not in log and "rule merge:" in log
      and any("Counts ready" in s.value for s in at.success), log[-1200:])
btn(at, "Use these counts for the analysis").click().run()
check("10. in the Pipeline: cell_line covariate proposed", at.selectbox(key="covariate_choice").options == ["(none)", "cell_line"], at.selectbox(key="covariate_choice").options)

at.selectbox(key="annot_drop_PRJNA88").select("cell_line"); btn(at, "Remove").click().run()
check("11. column removed (and its rules)", json.loads((STUDY / "annotations.json").read_text())["user_columns"] == ["condition"]
      and "cell_line" not in (STUDY / "samples_annotated.tsv").read_text().splitlines()[0])
check("12. no exception", not at.exception, texts(at.exception))
W.httpd.shutdown()
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
