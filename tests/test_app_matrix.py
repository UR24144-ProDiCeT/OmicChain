"""Source 'count matrix': file checks, annotation / sample sheet, comparison, loading in the Pipeline,
covariate, deterministic provenance. Mock DESeq2.
Usage: python tests/test_app_matrix.py"""
import json, os, random, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="app matrix "))
PROJ = TMP / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
sys.path.insert(0, str(PROJ))
import omicchain_matrix as om, pandas as pd
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-1500:]}"))
    if not cond: FAILED.append(name)

def errs(text, name="m.tsv"): return om.read_matrix(text.encode(), name)[1]
check("U1. duplicated gene identifiers", any("more than once" in e for e in errs("gene\ta\tb\nG1\t1\t2\nG1\t3\t4\n")))
check("U2. non-numeric value", any("non-numeric" in e for e in errs("gene\ta\tb\nG1\t1\tx\n")))
check("U3. negative value", any("negative" in e for e in errs("gene\ta\tb\nG1\t1\t-2\n")))
check("U4. a single sample column", any("fewer than 2" in e for e in errs("gene\ta\nG1\t1\n")))
df, e, w = om.read_matrix(b"gene,a,b\nG1,1.5,2\nG2,3,4\n", "m.csv")
check("U5. CSV read; non-integer values -> warning", df is not None and df.shape == (2, 2) and any("not integers" in x for x in w), (e, w))
random.seed(5)
genes = [f"GENE{i}" for i in range(1500)]
samples = ["S1", "S2", "S3", "S4", "S5"]
rows = ["gene_id\t" + "\t".join(samples)] + [g + "\t" + "\t".join(str(random.randint(0, 900)) for _ in samples) for g in genes]
MATRIX = ("\n".join(rows) + "\n").encode()
SHEET = b"sample\ttreatment\tcell line\nS1\tuntreated\tA\nS2\tTGFb\tA\nS3\tuntreated\tB\nS4\tTGFb\tB\nS5\tother\tA\n"

from eth_account import Account
w = Account.create()
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(), "OMICCHAIN_REF_DIR": str(TMP / "ref"),
                   "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
os.chdir(PROJ)
from streamlit.testing.v1 import AppTest
def btn(at, label): return next(b for b in at.button if label in b.label)
def texts(els): return " | ".join(str(e.value) for e in els)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at.run()
at.radio(key="data_source").set_value("A count matrix").run()
at.file_uploader(key="matrix_upload").upload("bad.tsv", b"gene\ta\tb\nG1\t1\t-2\n", "text/plain").run()
check("1. invalid matrix -> error, nothing imported", any("negative" in e.value for e in at.error) and not (PROJ / "studies").exists(), texts(at.error))
at.file_uploader(key="matrix_upload").upload("my counts.tsv", MATRIX, "text/plain").run()
D = PROJ / "studies" / "matrix_my_counts"
check("2. matrix imported: summary, original file kept", any("1500 genes × 5 samples" in m.value for m in at.markdown)
      and (D / "original_my_counts.tsv").read_bytes() == MATRIX and not at.error, texts(at.markdown)[-500:] + texts(at.error))
check("2. 'few genes' not reported (1500)", not any("only" in x.value for x in at.warning), texts(at.warning))
check("3. nothing to compare before description", any("No column has at least two values" in i.value and "sample sheet" in i.value for i in at.info), texts(at.info))
at.file_uploader(key="annot_sheet_matrix_my_counts").upload("design.tsv", SHEET, "text/plain").run()
check("4. sheet imported; comparison proposed: treatment, untreated vs TGFb",
      (at.selectbox(key="geo_condition").value, at.selectbox(key="geo_ref").value, at.selectbox(key="geo_test").value) == ("treatment", "untreated", "TGFb"),
      (at.selectbox(key="geo_condition").value, at.selectbox(key="geo_ref").value, at.selectbox(key="geo_test").value, texts(at.caption)))
btn(at, "Use these data for the analysis").click().run()
exp = pd.read_csv(PROJ / "expression_input.tsv", sep="\t", index_col=0)
src = pd.read_csv(PROJ / "metadata_source.tsv", sep="\t", dtype=str)
check("5. Pipeline: only the 4 compared samples, coded metadata", list(exp.columns) == ["S1", "S2", "S3", "S4"] and len(exp) == 1500
      and src["group"].tolist() == ["control", "treatment", "control", "treatment"] and list(src.columns) == ["sample", "group", "condition", "title", "cell_line"], src)
check("5. selection pre-filled", at.session_state["ctrl_samples"] == ["S1", "S3"] and at.session_state["trt_samples"] == ["S2", "S4"])
prov = json.loads((D / "comparison" / "matrix_provenance.json").read_text())
check("6. provenance: original file (SHA-256), imported sheet, plan", prov["source"]["original_file"] == "my counts.tsv"
      and prov["user_annotations"]["imported_sheets"][0]["file"] == "design.tsv" and prov["design"]["candidate_covariates"] == ["cell_line"], prov)
first = (D / "comparison" / "matrix_provenance.json").read_bytes()
btn(at, "Use these data for the analysis").click().run()
check("6. deterministic provenance (same comparison -> same bytes)", (D / "comparison" / "matrix_provenance.json").read_bytes() == first)
at.selectbox(key="covariate_choice").select("cell_line").run(); btn(at, "Run and certify the analysis").click().run()
rd = Path(at.session_state.get("results_dir", ""))
check("7. analysis with covariate", rd.name.endswith("_cov_cell_line") and "covariate=cell_line" in (rd / "analysis_parameters.txt").read_text(), texts(at.error))
bm = json.loads((rd / "bundle_manifest.json").read_text())
check("7b. matrix provenance certified with the analysis (data_origin)", "data_origin" in [c["id"] for c in bm["components"]]
      and json.loads((rd / "data_origin.json").read_text())["source"]["original_file"] == "my counts.tsv", [c["id"] for c in bm["components"]])

at2 = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at2.run()
at2.radio(key="data_source").set_value("A count matrix").run()
check("8. new session: dataset found again, annotations kept", at2.selectbox(key="matrix_dataset").value == "matrix_my_counts"
      and at2.selectbox(key="geo_condition").value == "treatment", (at2.selectbox(key="matrix_dataset").value, texts(at2.info)))
check("9. no exception", not at.exception and not at2.exception, texts(at.exception) + texts(at2.exception))
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
