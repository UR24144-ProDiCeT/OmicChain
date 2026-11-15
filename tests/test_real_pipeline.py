"""The whole downstream pipeline with the real R packages (DESeq2, clusterProfiler): analysis of the
example matrix, certified R environment, same fingerprint on a second run, verification by re-run.
Usage: python tests/test_real_pipeline.py   (needs the R packages of DESCRIPTION; skipped without DESeq2
unless OMICCHAIN_REQUIRE_REAL is set, as in the Docker image)"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RSCRIPT = shutil.which("Rscript")
have = RSCRIPT and subprocess.run([RSCRIPT, "-e", "quit(status = if (requireNamespace('DESeq2', quietly = TRUE)) 0 else 1)"],
                                  capture_output=True, cwd=tempfile.gettempdir()).returncode == 0
if not have:
    if os.environ.get("OMICCHAIN_REQUIRE_REAL"):
        sys.exit("FAIL  DESeq2 is not installed, and OMICCHAIN_REQUIRE_REAL is set")
    print("SKIP  DESeq2 is not installed: the real pipeline is not tested here\n\nALL CHECKS PASS")
    sys.exit(0)

os.environ["RENV_CONFIG_AUTOLOADER_ENABLED"] = "false"
for k in ("OMICCHAIN_SNAPSHOT_LIKE", "OMICCHAIN_RENV_PROJECT"):
    os.environ.pop(k, None)
TMP = Path(tempfile.mkdtemp(prefix="real pipeline "))
fails = []


def check(label, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f"   -> {detail}"), flush=True)
    if not ok:
        fails.append(label)


def project(name):
    proj = TMP / name
    subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(proj)], check=True, capture_output=True)
    shutil.rmtree(proj / "mockbin")
    lines = (ROOT / "examples" / "simulated_counts.tsv").read_text().splitlines()
    samples = lines[0].split("\t")[1:]
    (proj / "expression_input.tsv").write_text("\n".join(["gene\t" + "\t".join(samples)] + lines[1:]) + "\n")
    meta = "sample\tgroup\n" + "".join(f"{s}\t{'control' if s.startswith('ctrl') else 'treatment'}\n" for s in samples)
    (proj / "metadata.tsv").write_text(meta)
    (proj / "metadata_source.tsv").write_text(meta)
    return proj


def run(proj):
    return subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0"],
                          cwd=proj, capture_output=True, text=True)


p1 = project("first")
r = run(p1)
res = p1 / "results" / "padj_0.05_lfc_1.0"
check("1. pipeline with the real DESeq2: code 0", r.returncode == 0, (r.stdout + r.stderr)[-3000:])
if r.returncode != 0:
    print(f"\n1 CHECK(S) FAILED: {fails}")
    sys.exit(1)
top = (res / "top_genes.tsv").read_text().splitlines()
check("1. differentially expressed genes found in the example matrix", len(top) > 20 and top[0].split("\t")[0] == "Gene", top[:2])
check("1. figures and report written", all((res / n).stat().st_size > 1000 for n in
      ("heatmap.tiff", "volcano.tiff", "dotplot_go.tiff", "report.pdf")), str(sorted(x.name for x in res.iterdir())))
lock = json.loads((res / "renv.lock").read_text())
pk = lock["Packages"]
check("2. certified R environment: DESeq2, clusterProfiler and their dependencies, with versions",
      lock.get("OmicChain") and all(n in pk for n in ("DESeq2", "clusterProfiler", "org.Hs.eg.db", "apeglm", "Rsubread"))
      and len(pk) > 60, f"{len(pk)} packages")
check("2. Bioconductor release recorded", bool(lock.get("Bioconductor", {}).get("Version")), str(lock.get("Bioconductor")))
root_lock = ROOT / "renv.lock"
if os.environ.get("OMICCHAIN_CONTAINER") and root_lock.is_file():
    check("2. in the image: the certified environment is, byte for byte, renv.lock of the repository",
          (res / "renv.lock").read_bytes() == root_lock.read_bytes())
bundle = (res / "bundle_hash.txt").read_text().strip()
check("3. composite fingerprint computed", len(bundle) == 64)

p2 = project("second")
r = run(p2)
bundle2 = (p2 / "results" / "padj_0.05_lfc_1.0" / "bundle_hash.txt").read_text().strip() if r.returncode == 0 else ""
check("4. same analysis in another folder: same fingerprint", bundle2 == bundle, f"{bundle} / {bundle2}")

sys.path.insert(0, str(p1))
import omicchain_core as occ, omicchain_verify as ov
crate = occ.make_crate_zip(res)
reviewer = TMP / "reviewer"
reviewer.mkdir()
shutil.copy(crate, reviewer / "crate.zip")
rep = ov.rerun_verify(reviewer / "crate.zip", expected=bundle, project=p1, log=lambda *a, **k: None)
check("5. verification from the crate alone: REPRODUCED", rep["verdict"] == "REPRODUCED" and rep["exit_code"] == 0,
      {k: rep.get(k) for k in ("verdict", "reason", "log_tail")})

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("ALL CHECKS PASS" if not fails else f"{len(fails)} CHECK(S) FAILED: {fails}"))
sys.exit(1 if fails else 0)
