"""Record of the R environment (snapshot_renv.R) with the real R: packages actually used, their
dependencies, same bytes on every run; a missing package stops the certification.
Usage: python tests/test_environment.py   (R + Rsubread required; R_LIBS if needed)"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RSCRIPT = os.environ.get("OMICCHAIN_RSCRIPT") or shutil.which("Rscript")
TMP = Path(tempfile.mkdtemp(prefix="env record "))
fails = []


def check(label, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f"   -> {detail}"))
    if not ok:
        fails.append(label)


def snapshot(project, out, like=None):
    env = dict(os.environ, OMICCHAIN_RENV_PROJECT=str(project))
    env.pop("OMICCHAIN_SNAPSHOT_LIKE", None)
    if like:
        env["OMICCHAIN_SNAPSHOT_LIKE"] = str(like)
    return subprocess.run([RSCRIPT, str(ROOT / "snapshot_renv.R"), str(out)], cwd=project, env=env,
                          capture_output=True, text=True)


proj = TMP / "proj"
proj.mkdir()
(proj / "DESCRIPTION").write_text("Package: X\nType: Project\nImports:\n    Rsubread (>= 2.0),\n    stats\n")
lock = proj / "results" / "renv.lock"
r = snapshot(proj, lock)
check("1. record written", r.returncode == 0 and lock.exists(), r.stderr[-400:])
raw = lock.read_bytes()
d = json.loads(raw)
pk = d["Packages"]
check("1. marked as an OmicChain environment record, R version recorded",
      d.get("OmicChain") == {"EnvironmentRecord": 1} and d["R"]["Version"].count(".") == 2, str(list(d)))
check("1. Rsubread recorded with its version, source Bioconductor",
      pk.get("Rsubread", {}).get("Source") == "Bioconductor" and pk["Rsubread"]["Version"][:1].isdigit(), str(pk.get("Rsubread")))
check("1. dependencies of Rsubread recorded too (Matrix), base packages left out",
      "Matrix" in pk and "stats" not in pk and "utils" not in pk, str(list(pk)))
check("1. only name, version and source for each package (nothing specific to this computer)",
      all(set(v) <= {"Package", "Version", "Source", "Repository"} for v in pk.values()))
check("1. Unix line endings, packages sorted", b"\r" not in raw and list(pk) == sorted(pk))
first = raw
r = snapshot(proj, lock)
check("2. second run: same bytes", r.returncode == 0 and lock.read_bytes() == first)

(proj / "DESCRIPTION").write_text("Package: X\nImports: Rsubread, notAnInstalledPackage\n")
bad = proj / "results" / "bad.lock"
r = snapshot(proj, bad)
check("3. package of the analysis not installed -> error naming it, nothing written",
      r.returncode != 0 and "notAnInstalledPackage" in r.stderr and "cannot be certified" in r.stderr and not bad.exists(),
      r.stderr[-300:])

certified = proj / "certified.lock"
certified.write_text(first.decode().replace('"Matrix"', '"removedSince"'))
again = proj / "results" / "again.lock"
r = snapshot(proj, again, like=certified)
check("4. replay of a certified record: same packages looked up, the absent one is reported, no error",
      r.returncode == 0 and "removedSince" in r.stdout and "removedSince" not in json.loads(again.read_text())["Packages"],
      (r.stdout + r.stderr)[-300:])
(proj / "DESCRIPTION").write_text("Package: X\nImports: Rsubread, somethingAddedLater\n")
same = proj / "results" / "same.lock"
r = snapshot(proj, same, like=lock)
check("4. DESCRIPTION extended after the certification: the replay still gives the certified bytes",
      r.returncode == 0 and same.read_bytes() == first, (r.stdout + r.stderr)[-300:])

has_renv = subprocess.run([RSCRIPT, "-e", "quit(status = if (requireNamespace('renv', quietly = TRUE)) 0 else 1)"],
                          cwd=TMP, capture_output=True).returncode == 0
if has_renv:
    legacy = proj / "legacy.lock"
    legacy.write_text('{\n  "R": {"Version": "4.5.1", "Repositories": []},\n  "Packages": {}\n}\n')
    out = proj / "results" / "legacy_out.lock"
    r = snapshot(proj, out, like=legacy)
    check("5. analysis certified before this change (renv snapshot): replayed with renv, as it was",
          r.returncode == 0 and out.exists() and "OmicChain" not in json.loads(out.read_text()), (r.stdout + r.stderr)[-300:])
else:
    print("SKIP  5. renv not installed: replay of older analyses not tested")

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("ALL CHECKS PASS" if not fails else f"{len(fails)} CHECK(S) FAILED: {fails}"))
sys.exit(1 if fails else 0)
