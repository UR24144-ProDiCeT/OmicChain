"""Curation screen (public GEO study) + covariate in the application (AppTest, mock Rscript).
Usage: python tests/test_app_design.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
PROJ = Path(tempfile.mkdtemp()) / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
from eth_account import Account
w = Account.create()
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(),
                   "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
os.chdir(PROJ); sys.path.insert(0, str(PROJ))
from streamlit.testing.v1 import AppTest
import omicchain_design as odz, pandas as pd

FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-1200:]}"))
    if not cond: FAILED.append(name)
def btn(at, label): return next(b for b in at.button if label in b.label)
def sel(at, label): return next(x for x in at.selectbox if x.label == label)
def texts(els): return " | ".join(str(e.value) for e in els)

m = pd.DataFrame({"sample": list("abcd"), "group": ["control", "control", "treatment", "treatment"],
                  "cell_line": ["L1", "L2", "L1", "L2"], "batch": ["x", "x", "y", "y"], "one": ["u"] * 4,
                  "title": list("wxyz"), "cell line": ["1", "2", "1", "2"], "hole": ["p", "", "p", "q"]})
ok, rej = odz.covariate_options(m)
check("U1. only cell_line is usable", ok == ["cell_line"], (ok, rej))
check("U2. reasons: confounded / single value / invalid name / empty", rej == {"batch": "confounded with the groups", "one": "a single value",
      "cell line": "not a valid R name", "hole": "empty values for some samples"}, rej)
cmd = odz.upstream_command(odz.upstream_config(r"C:\Users\me\avec identicons\study_GSE1", "treatment", "Untreated", "Dexamethasone"), windows=True)
check("U3. Windows command: path with spaces in quotes", '"--study=C:\\Users\\me\\avec identicons\\study_GSE1"' in cmd and "--reference-level=Untreated" in cmd, cmd)

(PROJ / "expression_input.tsv").write_text("gene\tGSM1\tGSM2\tGSM3\tGSM4\nTP53\t10\t80\t12\t90\nMYC\t5\t6\t7\t8\n")
(PROJ / "metadata_source.tsv").write_text("sample\tgroup\tcondition\ttitle\tcell_line\nGSM1\tcontrol\tUntreated\tu1\tN1\n"
                                          "GSM2\ttreatment\tDexamethasone\td1\tN1\nGSM3\tcontrol\tUntreated\tu2\tN2\nGSM4\ttreatment\tDexamethasone\td2\tN2\n")
(PROJ / "loaded_data.json").write_text('{"label": "GSE1 — treatment: Dexamethasone vs Untreated", "counts_dir": "", "samples": 4}')
at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180)
at.run()
check("0. application loaded, counts of the study used", not at.exception and any("Data loaded from the Data & reference step" in i.value
      and "GSE1" in i.value for i in at.info), texts(at.exception) + texts(at.info))
meta = pd.read_csv(PROJ / "metadata.tsv", sep="\t")
check("6. metadata.tsv: control/treatment + columns kept", list(meta.columns) == ["sample", "group", "condition", "title", "cell_line"]
      and meta["group"].tolist() == ["control", "control", "treatment", "treatment"], meta)
cov = sel(at, "Covariate (optional)")
check("7. covariate proposed: cell_line", cov.options == ["(none)", "cell_line"], cov.options)
cov.select("cell_line").run()
check("8. model shown", any("~ cell_line + group" in c.value for c in at.caption), texts(at.caption))
btn(at, "Run and certify the analysis").click().run()
rd = Path(at.session_state.get("results_dir", ""))
check("9. pipeline run with the covariate: folder _cov_cell_line, certified parameter",
      rd.name == "padj_0.05_lfc_1.0_cov_cell_line" and "covariate=cell_line" in (rd / "analysis_parameters.txt").read_text()
      and at.session_state.get("bundle_hash"), (rd, texts(at.error)))
cov_bundle = at.session_state["bundle_hash"]
sel(at, "Covariate (optional)").select("(none)").run()
btn(at, "Run and certify the analysis").click().run()
rd2 = Path(at.session_state["results_dir"])
check("10. without covariate: usual folder, other fingerprint", rd2.name == "padj_0.05_lfc_1.0" and at.session_state["bundle_hash"] != cov_bundle
      and "covariate" not in (rd2 / "analysis_parameters.txt").read_text())

at2 = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at2.run()
labels = [u.label for u in at2.file_uploader]
check("13. Pipeline: no 'Expression Table' or metadata to upload; Snakefile / R under Advanced",
      "Expression Table" not in labels and "Sample metadata (optional)" not in labels and "Snakefile" in labels, labels)
check("13b. new session: loaded data found again, selection pre-filled from the metadata",
      at2.session_state["ctrl_samples"] == ["GSM1", "GSM3"] and at2.session_state["trt_samples"] == ["GSM2", "GSM4"]
      and any("Data loaded from the Data & reference step" in i.value for i in at2.info), texts(at2.info))
(PROJ / "loaded_data.json").unlink()
at3 = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at3.run()
check("14. no data loaded: message, Execute disabled", any("No data loaded yet" in i.value for i in at3.info)
      and btn(at3, "Run and certify the analysis").disabled, texts(at3.info))
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
