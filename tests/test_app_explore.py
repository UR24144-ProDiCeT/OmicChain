"""Threshold exploration: stale results never shown, sliders, counts, thresholds carried to the analysis.
Usage: python tests/test_app_explore.py   (mock Rscript)"""
import os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
PROJ = Path(tempfile.mkdtemp()) / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
from eth_account import Account
w = Account.create()
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(), "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
os.chdir(PROJ); sys.path.insert(0, str(PROJ))
import pandas as pd, omicchain_explore as oex
from streamlit.testing.v1 import AppTest
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-1200:]}"))
    if not cond: FAILED.append(name)
def btn(at, label): return next(b for b in at.button if label in b.label)
def texts(els): return " | ".join(str(e.value) for e in els)
def metric(at, label): return int(next(m for m in at.metric if m.label == label).value)

df = pd.DataFrame({"Gene": list("abcd"), "log2FoldChange": [2, -2, 0.5, 3], "padj": [0.01, 0.01, 0.01, None]})
c = oex.classify(df, 0.05, 1.0)
check("U1. classification: up / down / NA never significant", list(c) == [oex.UP, oex.DOWN, oex.NS, oex.NS], list(c))
check("U2. |log2FC| >= 0: every gene with padj < threshold counts", (oex.classify(df, 0.05, 0.0) != oex.NS).sum() == 3)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at.run()
check("1. before exploration: invitation to run it", any("Run the exploration above" in i.value for i in at.info), texts(at.info))
btn(at, "Run exploration").click().run()
full = pd.read_csv(PROJ / "results" / "_exploration" / "full_results.tsv", sep="\t")
exp = int((oex.classify(full, 0.05, 1.0) != oex.NS).sum())
check("2. exploration: count at the default thresholds (0.05 / 1.0) = independent recomputation",
      metric(at, "Significant genes") == exp and exp > 0, (metric(at, "Significant genes"), exp, texts(at.error)))
check("2. plots shown without error", not at.exception and not any("error" in e.value.lower() for e in at.error), texts(at.exception))
at.select_slider(key="explore_lfc").set_value(2.0).run()
exp2 = int((oex.classify(full, 0.05, 2.0) != oex.NS).sum())
check("3. slider |log2FC| = 2: count updated (fewer genes)", metric(at, "Significant genes") == exp2 and exp2 < exp, (metric(at, "Significant genes"), exp2, exp))
at.select_slider(key="explore_lfc").set_value(0.0).run()
exp0 = int((oex.classify(full, 0.05, 0.0) != oex.NS).sum())
check("3. slider |log2FC| = 0: count = recomputation", metric(at, "Significant genes") == exp0, (metric(at, "Significant genes"), exp0))
check("3. up + down = total", metric(at, "Up-regulated") + metric(at, "Down-regulated") == exp0)
btn(at, "Use padj < 0.05").click().run()
check("4. thresholds carried to the analysis parameters", at.session_state["padj_threshold"] == 0.05 and at.session_state["lfc_threshold"] == 0.0
      and any("Analysis parameters set" in s.value for s in at.success), texts(at.success))
btn(at, "Run and certify the analysis").click().run()
check("5. pipeline run with these thresholds", Path(at.session_state.get("results_dir", "")).name == "padj_0.05_lfc_0.0", at.session_state.get("results_dir"))
(PROJ / "expression_input.tsv").write_text((PROJ / "expression_input.tsv").read_text().replace("\t9", "\t8"))
at.run()
check("6. data changed: exploration declared stale, no count shown",
      any("computed for other data" in x.value for x in at.warning) and not any(m.label == "Significant genes" for m in at.metric), texts(at.warning))
check("7. no exception", not at.exception, texts(at.exception))
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
