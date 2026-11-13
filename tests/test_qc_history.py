"""Quality control before the analysis (PCA, libraries, alignment) and list of the certified analyses.
Mock Rscript.   Usage: python tests/test_qc_history.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="qc history "))
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
import numpy as np, pandas as pd
import omicchain_qc as oqc, omicchain_history as ohi, omicchain_matrix as om, omicchain_design as odz
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"\n      -> {str(detail)[-1500:]}"))
    if not cond: FAILED.append(name)
def texts(els): return " | ".join(str(e.value) for e in els)

rng = np.random.default_rng(1)
samples = ["c_L1", "t_L1", "c_L2", "t_L2", "c_L3", "t_L3"]
meta = pd.DataFrame({"sample": samples, "group": ["control", "treatment"] * 3, "cell_line": ["L1", "L1", "L2", "L2", "L3", "L3"],
                     "title": samples})
def simulate(line_effect, group_effect, n=2000):
    base = rng.gamma(2.0, 200.0, size=n)
    m = np.empty((n, 6))
    line_fc = {l: np.exp(rng.normal(0, line_effect, n)) for l in ("L1", "L2", "L3")}
    grp_fc = np.exp(rng.normal(0, group_effect, n))
    for j, s in enumerate(samples):
        mu = base * line_fc[s[2:]] * (grp_fc if s.startswith("t") else 1.0)
        m[:, j] = rng.poisson(mu)
    return pd.DataFrame(m.astype(int), index=[f"G{i}" for i in range(n)], columns=samples)

c1 = simulate(line_effect=1.0, group_effect=0.05)
coords, ratio, n_genes = oqc.pca(c1)
summ = {r["column"]: r for r in oqc.factor_summary(coords, meta, ["group", "cell_line"])}
tip = oqc.advice(list(summ.values()), covariate="")
check("1. dominant cell-line effect: PC1 explained by cell_line, not by the group", summ["cell_line"]["PC1"] > 0.8 and summ["group"]["PC1"] < 0.2
      and n_genes == 500 and abs(sum(ratio)) <= 1.0001, (summ, ratio))
check("1. ... advice: consider cell_line as a covariate", tip and tip[0] == "warning" and "cell_line" in tip[1], tip)
check("1. ... advice not repeated when cell_line is already the covariate", (oqc.advice(list(summ.values()), covariate="cell_line") or ("", ""))[0] != "warning")
c2 = simulate(line_effect=0.05, group_effect=1.0)
coords2, _, _ = oqc.pca(c2)
summ2 = {r["column"]: r for r in oqc.factor_summary(coords2, meta, ["group", "cell_line"])}
tip2 = oqc.advice(list(summ2.values()), covariate="")
check("2. dominant treatment effect: the groups separate on PC1 (positive message)", summ2["group"]["PC1"] > 0.8 and tip2 and tip2[0] == "success", (summ2, tip2))
check("3. eta squared: 1 for a perfect separation, 0 for an unrelated factor", abs(oqc.explained_by([1, 1, 5, 5], list("aabb")) - 1) < 1e-9
      and oqc.explained_by([1, 5, 1, 5], list("aabb")) < 1e-9 and oqc.explained_by([2, 2, 2, 2], list("aabb")) == 0.0)
check("3. fewer than 3 samples or empty table: no PCA (None), no error", oqc.pca(c1.iloc[:, :2]) is None and oqc.pca(c1 * 0) is None)
check("3. PCA insensitive to library size (CPM): a sample sequenced 5x deeper stays in place",
      abs(oqc.explained_by(oqc.pca(c1.assign(c_L1=c1["c_L1"] * 5))[0]["PC1"], meta["cell_line"]) - summ["cell_line"]["PC1"]) < 0.05)
qdir = TMP / "counts"; qdir.mkdir()
(qdir / "qc_summary.tsv").write_text("sample\trun\tfragments\tmapped\tassigned\nA\tr1\t1000000\t900000\t700000\nA\tr2\t1000000\t800000\t600000\nB\tr3\t500000\t450000\t400000\n")
mt = oqc.mapping_table(qdir, ["A", "B"])
check("4. alignment per sample: runs added up (A: 2 M, 85 % aligned, 65 % assigned)", list(mt["fragments (M)"]) == [2.0, 0.5]
      and list(mt["aligned %"]) == [85.0, 90.0] and list(mt["assigned to genes %"]) == [65.0, 80.0], mt)
check("4. matrix provided (no qc_summary): no alignment table", oqc.mapping_table(None, ["A"]) is None and oqc.mapping_table(TMP, ["A"]) is None)

ds, _ = om.import_matrix(c1.rename_axis("gene").to_csv(sep="\t").encode(), "sim.tsv", PROJ / "studies")
t = pd.read_csv(ds / "samples.tsv", sep="\t", dtype=str).fillna("")
t["condition"] = ["ctrl", "trt"] * 3; t["cell_line"] = meta["cell_line"].tolist()
om.comparison_files(ds, t, "condition", "ctrl", "trt", samples, ds / "comparison")
import shutil
shutil.copy(ds / "comparison" / "expression_input.tsv", PROJ / "expression_input.tsv")
shutil.copy(ds / "comparison" / "metadata.tsv", PROJ / "metadata_source.tsv")
shutil.copy(ds / "comparison" / "matrix_provenance.json", PROJ / "data_origin.json")
(PROJ / "loaded_data.json").write_text(json.dumps({"label": "sim — condition: trt vs ctrl", "counts_dir": str(ds / "comparison"), "samples": 6}))

from streamlit.testing.v1 import AppTest
at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240); at.run()
check("5. Analysis step: section 'Quality control of the samples', no exception",
      any("Quality control of the samples" in h.value for h in at.subheader) and not at.exception, texts(at.exception))
check("5. ... PCA described (6 samples, 500 genes) and cell_line covariate advice",
      any("PCA of the 6 selected samples" in c.value and "500 most variable" in c.value for c in at.caption)
      and any("cell_line" in x.value and "covariate" in x.value for x in at.warning), texts(at.caption)[-600:] + texts(at.warning))
check("5. ... table: what each column explains of PC1 / PC2", any("explains of PC1" in list(d.value.columns) for d in at.dataframe),
      [list(d.value.columns) for d in at.dataframe])
check("5. empty history announced", any("No certified analysis yet" in c.value for c in at.caption))
at.selectbox(key="covariate_choice").set_value("cell_line").run()
check("6. covariate chosen: the advice disappears", not any("Consider" in x.value for x in at.warning) and not at.exception, texts(at.warning))

def run(padj):
    r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", f"padj_threshold={padj}", "lfc_threshold=1.0"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    R = PROJ / "results" / f"padj_{padj}_lfc_1.0"
    return R, (R / "bundle_hash.txt").read_text().strip()
R1, B1 = run("0.05"); R2, B2 = run("0.01")
os.utime(R1 / "bundle_hash.txt", (1_700_000_000, 1_700_000_000))
state = {"tx_hash": "0x" + "ab" * 32, "network_used": "Base Sepolia", "proof_timestamp": 1700000000, "proof_block": 42,
         "proof_contract": "0x" + "22" * 20, "explorer_used": "https://sepolia.basescan.org", "proof_bundle": B1}
ohi.save_proof(R1, state)
lst = ohi.list_analyses(PROJ / "results")
check("7. two analyses listed, most recent first; exploration (_exploration) ignored", [a["bundle"] for a in lst] == [B2, B1], [a["folder"].name for a in lst])
check("7. ... parameters, data and proof read (R1 published, R2 not)", lst[1]["params"]["padj_threshold"] == "0.05" and "sim.tsv" in lst[1]["data"]
      and "trt vs ctrl" in lst[1]["data"] and lst[1]["proof"]["tx_hash"] == state["tx_hash"] and lst[0]["proof"] is None, lst)
tb = ohi.table(lst)
check("7. ... table: Published 'Base Sepolia' / 'not yet'", list(tb["Published"]) == ["not yet", "Base Sepolia"], tb)
ohi.save_proof(R2, {**state, "proof_bundle": "f" * 64})
check("8. proof of another fingerprint (analysis re-run in the same folder): ignored", ohi.list_analyses(PROJ / "results")[0]["proof"] is None)
s = {"pending_tx": {"x": 1}, "tx_hash": "old", "proof_bundle": "old"}
ohi.restore(lst[1], s)
check("8. restore: folder, fingerprint and proof of the analysis; no pending transaction left",
      s["results_dir"] == str(R1) and s["bundle_hash"] == B1 and s["tx_hash"] == state["tx_hash"] and s["proof_bundle"] == B1 and "pending_tx" not in s, s)
s2 = {"tx_hash": "old", "proof_bundle": "old", "network_used": "x"}
ohi.restore(ohi.list_analyses(PROJ / "results")[0], s2)
check("8. restore of an unpublished analysis: no proof inherited from the previous one", "tx_hash" not in s2 and "proof_bundle" not in s2 and s2["bundle_hash"] == B2, s2)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240); at.run()
check("9. application: history (2 analyses) in 'Certified analyses on this computer'",
      any(list(d.value.columns)[:2] == ["Certified", "Data"] and len(d.value) == 2 for d in at.dataframe) and not at.exception, texts(at.exception))
at.selectbox(key="history_choice").set_value(1).run()
at.button(key="history_open").click().run()
check("9. 'Open' on the published analysis: fingerprint and proof of that analysis in the session",
      at.session_state["bundle_hash"] == B1 and at.session_state["tx_hash"] == state["tx_hash"]
      and at.session_state["results_dir"] == str(R1) and not at.exception, texts(at.exception))
check("9. ... the analysis is flagged as open, steps 'Certified' / 'Published on-chain'",
      any("currently open" in c.value for c in at.caption) and any("Published on-chain" in m.value for m in at.markdown), texts(at.caption)[-400:])

print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
