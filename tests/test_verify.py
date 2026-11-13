"""Tests of omicchain_verify.py (re-run) with the mock Rscript.  Usage: python tests/test_verify.py"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
PROJ = Path(tempfile.mkdtemp()) / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
os.environ["PATH"] = str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]
for k in ("MOCK_NOISE", "MOCK_DESEQ2_VERSION"): os.environ.pop(k, None)
sys.path.insert(0, str(PROJ))
import omicchain_core as occ, omicchain_verify as ov

FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)
def quiet(*a, **k): pass
def tree_hash(d):
    h = hashlib.sha256()
    for p in sorted(Path(d).rglob("*")):
        if p.is_file(): h.update(p.relative_to(d).as_posix().encode()); h.update(p.read_bytes())
    return h.hexdigest()

r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0"],
                   cwd=PROJ, capture_output=True, text=True)
assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
R = PROJ / "results" / "padj_0.05_lfc_1.0"
BUNDLE = (R / "bundle_hash.txt").read_text()
ZIP = occ.make_crate_zip(R)
REV = Path(tempfile.mkdtemp()) / "reviewer"; REV.mkdir()
shutil.copy(ZIP, REV / "crate.zip"); shutil.copy(PROJ / "expression_input.tsv", REV); shutil.copy(PROJ / "metadata.tsv", REV)
EXPR, META = REV / "expression_input.tsv", REV / "metadata.tsv"
before = tree_hash(PROJ / "results")
def verify(crate=REV / "crate.zip", **kw):
    kw.setdefault("expression", EXPR); kw.setdefault("metadata", META)
    return ov.rerun_verify(crate, project=PROJ, log=quiet, **kw)

rep = verify(expected=BUNDLE)
check("1. nominal case (zip + published fingerprint) -> REPRODUCED, code 0", rep["verdict"] == "REPRODUCED" and rep["exit_code"] == 0, rep)
check("1. the recomputed fingerprint is the published one", rep.get("rerun_bundle") == BUNDLE, rep.get("rerun_bundle"))
check("1. the temporary folder is removed after success", rep.get("workdir_removed") and not Path(rep["workdir"]).exists())
check("2. the original results of the project are untouched", tree_hash(PROJ / "results") == before)

(PROJ / "de_core.R").write_text("# code changed AFTER certification\n")
rep = verify(expected=BUNDLE)
check("3. project code changed afterwards: the code OF THE CRATE is used -> REPRODUCED", rep["verdict"] == "REPRODUCED", rep)

bad = REV / "bad_expr.tsv"; bad.write_text(EXPR.read_text().replace("80", "81"))
rep = verify(expression=bad)
check("4. wrong input data -> stop before the re-run", rep["exit_code"] == 1 and "not the one used" in rep["steps"][-1]["detail"] and "workdir" not in rep, rep["steps"])

crlf = REV / "meta_crlf.tsv"; crlf.write_bytes(META.read_bytes().replace(b"\n", b"\r\n"))
rep = verify(metadata=crlf, expected=BUNDLE)
check("5. same data, Windows line endings -> warning", any("same bytes" in w for w in rep["warnings"]), rep["warnings"])
check("5. ... verdict RESULTS REPRODUCED (code 2), data component involved", rep["verdict"] == "RESULTS REPRODUCED" and rep["exit_code"] == 2
      and rep["different"] == ["Input data (expression + metadata)"], rep.get("different"))

os.environ["MOCK_DESEQ2_VERSION"] = "1.51.0"; rep = verify(); os.environ.pop("MOCK_DESEQ2_VERSION")
check("6. autre version de DESeq2 -> RESULTS REPRODUCED", rep["verdict"] == "RESULTS REPRODUCED" and rep["different"] == ["R environment (renv.lock)"], rep.get("different"))
check("6. ... the version change is named", "DESeq2 1.50.2 -> 1.51.0" in rep.get("environment_changes", []), rep.get("environment_changes"))

os.environ["MOCK_NOISE"] = "1e-9"; rep = verify(); os.environ.pop("MOCK_NOISE")
cmp = rep.get("table_comparison", {}).get("Top DE genes table", {})
check("7. petite difference numerique -> NOT REPRODUCED (code 1)", rep["verdict"] == "NOT REPRODUCED" and rep["exit_code"] == 1, rep["verdict"])
check("7. ... numeric difference: ~1e-9 on log2FoldChange, same genes", 5e-10 < cmp["max_differences"]["log2FoldChange"]["max_abs"] < 5e-9
      and cmp["n_only_certified"] == 0 and cmp["n_only_rerun"] == 0, cmp)
check("7. ... the re-run folder is kept for inspection", Path(rep["workdir"]).exists())

ext = REV / "extrait"; zipfile.ZipFile(REV / "crate.zip").extractall(ext)
(ext / "top_genes.tsv").write_text((ext / "top_genes.tsv").read_text().replace("3.5", "9.9"))
rep = verify(crate=ext)
check("8. tampered crate -> stop at the integrity step, no re-run", rep["exit_code"] == 1 and rep["steps"][0]["name"] == "crate integrity"
      and not rep["steps"][0]["ok"] and "workdir" not in rep, rep["steps"])
rep = verify(expected="0" * 64)
check("9. different published fingerprint -> stop", rep["exit_code"] == 1 and "--expected" in rep["steps"][0]["detail"], rep["steps"])
with zipfile.ZipFile(REV / "crate.zip") as z:
    names = z.namelist()
check("10. the metadata and the expression table are in the crate", "inputs/metadata.tsv" in names and "inputs/expression_input.tsv" in names, names)
rep = ov.rerun_verify(REV / "crate.zip", project=PROJ, log=quiet)
check("10. ... the crate alone is enough -> REPRODUCED", rep["verdict"] == "REPRODUCED", rep["steps"])

ext2 = REV / "with_inputs"; zipfile.ZipFile(REV / "crate.zip").extractall(ext2); (ext2 / "inputs").mkdir(exist_ok=True)
shutil.copy(EXPR, ext2 / "inputs" / "expression_input.tsv"); shutil.copy(META, ext2 / "inputs" / "metadata.tsv")
rep = ov.rerun_verify(ext2, project=PROJ, log=quiet)
check("11. data included in the crate -> no argument needed", rep["verdict"] == "REPRODUCED", rep)

evil = REV / "evil.zip"
with zipfile.ZipFile(evil, "w") as z: z.writestr("../../evil.txt", "x")
rep = verify(crate=evil)
check("12. zip with a path leaving the folder -> refused", rep["exit_code"] == 1 and "Unsafe path" in rep["steps"][-1]["detail"], rep["steps"])
check("12. ... verdict NOT VERIFIED (crate refused), nothing written outside the folder", rep["verdict"].startswith("NOT VERIFIED")
      and not (REV.parent / "evil.txt").exists() and not (REV.parent.parent / "evil.txt").exists(), rep["verdict"])
hostile = REV / "hostile"; shutil.copytree(ext2, hostile)
bm = json.loads((hostile / "bundle_manifest.json").read_text())
for c in bm["components"]:
    if c["crate_path"] == "code/Snakefile": c["crate_path"] = "code/../../../evil_snakefile"
(hostile / "bundle_manifest.json").write_text(json.dumps(bm))
rep = ov.rerun_verify(hostile, project=PROJ, log=quiet, trust_crate_code=True)
check("12b. manifest with a path leaving the crate -> refused before any read / write, even with the code accepted",
      rep["exit_code"] == 1 and "unsafe path" in rep["steps"][-1]["detail"] and "workdir" not in rep
      and not list(REV.parent.glob("**/evil_snakefile")) and ov.crate_code_check(hostile, PROJ) is None, rep["steps"])
bomb = REV / "bomb.zip"
with zipfile.ZipFile(REV / "crate.zip") as zin, zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist(): zout.writestr(item, zin.read(item.filename))
    zout.writestr("results/padding.bin", b"\0" * (40 * 1024 ** 2))
check("12b. the bomb is small on disk (< 1 MB more than the crate)", bomb.stat().st_size < (REV / "crate.zip").stat().st_size + 10 ** 6)
old_limit = ov.MAX_CRATE_BYTES; ov.MAX_CRATE_BYTES = 20 * 1024 ** 2
rep = ov.rerun_verify(bomb, project=PROJ, log=quiet)
check("12b. zip that expands beyond the limit -> refused before extraction", rep["verdict"].startswith("NOT VERIFIED")
      and "would expand to more than 20 MB" in rep["steps"][-1]["detail"] and "workdir" not in rep, rep["steps"])
import tempfile as _tf
d = Path(_tf.mkdtemp())
try:
    ov._safe_extract(bomb, d, max_bytes=10 ** 12, max_files=3); many = False
except ov.VerifyError as exc:
    many = "files (limit: 3)" in str(exc)
check("12b. too many files -> refused", many and not any(d.iterdir()))
class _Lying(zipfile.ZipFile):
    def infolist(self):
        infos = super().infolist()
        for i in infos: i.file_size = 1
        return infos
real_zip = zipfile.ZipFile; ov.zipfile.ZipFile = _Lying
try:
    ov._safe_extract(bomb, d, max_bytes=5 * 1024 ** 2); lied = False
except Exception as exc:
    lied = True
finally:
    ov.zipfile.ZipFile = real_zip
written = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
check("12b. wrong declared sizes -> stop while writing (less than 6 MB written)", lied and written < 6 * 1024 ** 2, written)
ov.MAX_CRATE_BYTES = old_limit
check("12b. the normal crate still passes", verify()["verdict"] == "REPRODUCED")
wd = REV / "existe"; wd.mkdir()
rep = verify(workdir=wd)
check("13. existing re-run folder -> refused without overwriting anything", "already exists" in rep["steps"][-1]["detail"], rep["steps"])

os.environ["MOCK_FAIL"] = "1"; rep = verify(); os.environ.pop("MOCK_FAIL")
check("15. technical failure of the re-run -> verdict 'NOT VERIFIED', not 'NOT REPRODUCED'", rep["verdict"].startswith("NOT VERIFIED") and rep["exit_code"] == 1, rep["verdict"])
check("15. ... the end of the log is reported (with the error)", any("simulated failure" in l for l in rep.get("log_tail", [])), rep.get("log_tail"))
cli = subprocess.run([sys.executable, str(PROJ / "omicchain_verify.py"), str(REV / "crate.zip"), "--expression", str(EXPR),
                      "--metadata", str(META), "--expected", BUNDLE, "--project", str(PROJ)], capture_output=True, text=True)
check("14. command line: VERDICT REPRODUCED, code 0", cli.returncode == 0 and "VERDICT : REPRODUCED" in cli.stdout, cli.stdout[-600:] + cli.stderr[-600:])
check("16. crate code recognised (official) -> step 'code of the crate' OK, nothing asked",
      any(st["name"] == "code of the crate" and st["ok"] and "official OmicChain 1.0.0-rc1" in st["detail"] for st in verify()["steps"]))
cc = ov.crate_code_check(REV / "crate.zip", PROJ)
check("16. crate_code_check (zip): known, 3 files", cc and cc["known"] and len(cc["files"]) == 3, cc)
snake_ok = (PROJ / "Snakefile").read_text()
(PROJ / "Snakefile").write_text(snake_ok + "\n# code from a stranger\n")
r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.01", "lfc_threshold=1.0"],
                   cwd=PROJ, capture_output=True, text=True)
assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
alien = REV / "alien.zip"; shutil.copy(occ.make_crate_zip(PROJ / "results" / "padj_0.01_lfc_1.0"), alien)
(PROJ / "Snakefile").write_text(snake_ok)
cc = ov.crate_code_check(alien, PROJ)
check("17. unknown Snakefile: crate_code_check points at it", cc and not cc["known"] and [f["file"] for f in cc["files"] if not f["known"]] == ["Snakefile"], cc)
check("17. ... and its text can be read before accepting", "code from a stranger" in ov.crate_code_text(alien, "code/Snakefile"))
rep = verify(crate=alien)
check("17. unknown code -> NOT VERIFIED, stop BEFORE any execution", rep["verdict"].startswith("NOT VERIFIED") and rep["exit_code"] == 1
      and rep["steps"][-1]["name"] == "code of the crate" and "workdir" not in rep and "--trust-crate-code" in rep["steps"][-1]["detail"], rep)
rep = verify(crate=alien, trust_crate_code=True)
check("18. unknown code explicitly accepted -> run, REPRODUCED, warning", rep["verdict"] == "REPRODUCED"
      and any("not a known OmicChain version" in w for w in rep["warnings"]), (rep["verdict"], rep["warnings"]))
cli = subprocess.run([sys.executable, str(PROJ / "omicchain_verify.py"), str(alien), "--expression", str(EXPR), "--project", str(PROJ)],
                     capture_output=True, text=True)
check("18. CLI without --trust-crate-code: code 1, NOT VERIFIED", cli.returncode == 1 and "NOT VERIFIED" in cli.stdout, cli.stdout[-500:])
(PROJ / "Snakefile").write_text(snake_ok + "\n# code from a stranger\n")
cli = subprocess.run([sys.executable, str(PROJ / "omicchain_verify.py"), "--register-official-code", "0.8", "--project", str(PROJ)],
                     capture_output=True, text=True)
(PROJ / "Snakefile").write_text(snake_ok)
cc = ov.crate_code_check(alien, PROJ)
check("19. version recorded in official_code.json -> recognised ('official OmicChain 0.8')", cli.returncode == 0 and cc["known"]
      and any(f["origin"] == "official OmicChain 0.8" for f in cc["files"]), (cli.stdout + cli.stderr, cc))
reg = json.loads((PROJ / "official_code.json").read_text())
check("19. ... the registry keeps the other versions", [r["version"] for r in reg["releases"]] == ["0.9", "1.0.0-rc1", "0.8"], reg)
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
