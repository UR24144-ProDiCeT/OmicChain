"""Optional covariate (design ~ covariate + group): passed to DESeq2, certified, replayed by verify.
Usage: python tests/test_covariate.py   (mock Rscript, as in test_verify.py)"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
PROJ = Path(tempfile.mkdtemp()) / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
os.environ["PATH"] = str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]
for k in ("MOCK_NOISE", "MOCK_DESEQ2_VERSION", "MOCK_FAIL"): os.environ.pop(k, None)
sys.path.insert(0, str(PROJ))
import omicchain_core as occ, omicchain_verify as ov
(PROJ / "metadata.tsv").write_text("sample\tgroup\tcell_line\nctrl_1\tcontrol\tL1\nctrl_2\tcontrol\tL2\ntrt_1\ttreatment\tL1\ntrt_2\ttreatment\tL2\n")
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)
def snake(*cfg):
    return subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0", *cfg],
                          cwd=PROJ, capture_output=True, text=True)
R = PROJ / "results" / "padj_0.05_lfc_1.0"
RC = PROJ / "results" / "padj_0.05_lfc_1.0_cov_cell_line"

r = snake(); assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
params_plain = (R / "analysis_parameters.txt").read_text(); bundle_plain = (R / "bundle_hash.txt").read_text()
check("1. without covariate: no covariate line (file as before)", "covariate" not in params_plain, params_plain)
check("1. ... expected lines only", params_plain.splitlines() == ["padj_threshold=0.05", "lfc_threshold=1.0",
      "control_samples=ctrl_1,ctrl_2", "treatment_samples=trt_1,trt_2"], params_plain)

r = snake("covariate=cell_line")
check("2. with covariate=cell_line: the pipeline runs again", r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:])
params_cov = (RC / "analysis_parameters.txt").read_text(); bundle_cov = (RC / "bundle_hash.txt").read_text()
check("2. separate results folder, the analysis without covariate is kept", (R / "bundle_hash.txt").read_text() == bundle_plain)
check("2. covariate certified in analysis_parameters.txt", params_cov.splitlines()[-1] == "covariate=cell_line", params_cov)
check("2. passed to run_deseq2.R (different result)", "3.75" in (RC / "top_genes.tsv").read_text(), (RC / "top_genes.tsv").read_text())
check("2. fingerprint different from the analysis without covariate", bundle_cov != bundle_plain)

r = snake("covariate=donor")
check("3. covariate missing from metadata.tsv -> failure", r.returncode != 0 and "donor" in r.stdout + r.stderr)
r = snake("covariate=cell_line"); assert r.returncode == 0

ZIP = occ.make_crate_zip(RC)
REV = Path(tempfile.mkdtemp()) / "rev"; REV.mkdir()
shutil.copy(PROJ / "expression_input.tsv", REV); shutil.copy(PROJ / "metadata.tsv", REV)
rep = ov.rerun_verify(ZIP, expression=REV / "expression_input.tsv", metadata=REV / "metadata.tsv",
                      expected=bundle_cov, project=PROJ, log=lambda *a: None)
check("4. verify replays the analysis WITH the covariate -> REPRODUCED", rep["verdict"] == "REPRODUCED", rep.get("verdict"))

r = snake(); assert r.returncode == 0
check("5. back to no covariate -> same fingerprint as at the start", (R / "bundle_hash.txt").read_text() == bundle_plain)
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
