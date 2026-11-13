"""Re-run verification of a certified analysis from its RO-Crate: crate integrity, code check, optional replay from the raw data, re-run with the crate's code and parameters, comparison of the fingerprints."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import omicchain_core as occ
import omicchain_names as onm

__all__ = ["rerun_verify", "input_hashes", "crate_origin", "crate_code_check"]

HERE = Path(__file__).resolve().parent
TOOLING = ("omicchain_core.py", "identicon_utils.py", "snapshot_renv.R")
INPUT_NAMES = {"expression": "expression_input.tsv", "metadata": "metadata.tsv"}


class VerifyError(Exception):
    """A check that prevents the re-run (reported to the user, not a crash)."""


def input_hashes(path) -> dict:
    """Raw and canonical SHA-256 of a TSV, computed EXACTLY like the Snakefile
    rule `hash_input_data` (keep both in sync)."""
    import pandas as pd

    raw = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    df = pd.read_csv(path, sep="\t")
    canonical = df.sort_index(axis=1).to_csv(sep="\t", index=False, lineterminator="\n").encode("utf-8")
    return {"raw": raw, "canonical": hashlib.sha256(canonical).hexdigest()}


def _check_inputs(crate: Path, files: dict) -> list:
    """Compare the supplied inputs with data_provenance.txt. Returns warnings;
    raises VerifyError if the content is not the certified one."""
    certified = occ.read_key_value_file(crate / "data_provenance.txt")
    if not certified:
        raise VerifyError("data_provenance.txt is missing from the crate: the input data cannot be checked.")
    warnings = []
    for key, path in files.items():
        got = input_hashes(path)
        want_c = certified.get(f"{key}_canonical_sha256")
        want_r = certified.get(f"{key}_raw_sha256")
        if got["canonical"] != want_c:
            raise VerifyError(
                f"The {key} file supplied ({path}) is not the one used for this analysis: "
                "its content differs from the certified one."
                + (" The metadata expected is the metadata.tsv that OmicChain wrote in the project folder for "
                   "this analysis (columns sample, group...), not the sample sheet that was imported."
                   if key == "metadata" else "")
            )
        if got["raw"] != want_r:
            warnings.append(
                f"The {key} file has the certified content but not the same bytes (line endings, "
                "column order...): the input-data component of the fingerprint will differ."
            )
    return warnings


MAX_CRATE_FILES = 5000
MAX_CRATE_BYTES = int(os.environ.get("OMICCHAIN_MAX_CRATE_MB", "2048")) * 1024 ** 2
MAX_MEMBER_READ = 64 * 1024 ** 2


def _safe_extract(zip_path: Path, dest: Path, max_bytes=None, max_files=MAX_CRATE_FILES) -> None:
    max_bytes = MAX_CRATE_BYTES if max_bytes is None else max_bytes
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        if len(infos) > max_files:
            raise VerifyError(f"The zip holds {len(infos)} files (limit: {max_files}): not an OmicChain crate.")
        if sum(i.file_size for i in infos) > max_bytes:
            raise VerifyError(f"The zip would expand to more than {max_bytes // 1024 ** 2} MB: refused "
                              "(set OMICCHAIN_MAX_CRATE_MB to raise the limit for a crate you trust).")
        written = 0
        for info in infos:
            target = (root / info.filename).resolve()
            if not target.is_relative_to(root):
                raise VerifyError(f"Unsafe path in the zip: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    written += len(block)
                    if written > max_bytes:
                        raise VerifyError(f"The zip expands to more than {max_bytes // 1024 ** 2} MB: refused.")
                    out.write(block)


def _read_member(z: zipfile.ZipFile, name: str, limit=MAX_MEMBER_READ) -> bytes:
    """One small file of the zip, read in memory (never more than `limit` bytes)."""
    with z.open(name) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{name}: too large to be read")
    return data


def _check_bundle(bundle) -> dict:
    """A bundle_manifest.json comes from the crate, i.e. from someone else. Its paths are used to
    read files of the crate and to write the re-run folder: refuse anything that could leave them."""
    if not isinstance(bundle, dict) or not isinstance(bundle.get("components"), list):
        raise VerifyError("bundle_manifest.json is not an OmicChain manifest.")
    if not isinstance(bundle.get("bundle_hash"), str) or len(bundle["bundle_hash"]) != 64 \
            or any(ch not in "0123456789abcdef" for ch in bundle["bundle_hash"]):
        raise VerifyError("bundle_manifest.json: the fingerprint is not a SHA-256.")
    for c in bundle["components"]:
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) for k in ("id", "label", "crate_path", "sha256")):
            raise VerifyError("bundle_manifest.json: malformed component.")
        why = onm.relpath_problem(c["crate_path"])
        if why:
            raise VerifyError(f"bundle_manifest.json: unsafe path {c['crate_path'][:80]!r} ({why}): "
                              "this is not a crate made by OmicChain.")
    return bundle


def _check_parameters(params: dict) -> None:
    """Certified parameters end up in a folder name and on a command line: numbers and an R name only."""
    for key in ("padj_threshold", "lfc_threshold"):
        try:
            value = float(params[key])
        except (KeyError, TypeError, ValueError):
            raise VerifyError(f"analysis_parameters.txt: {key} is missing or not a number.")
        if not (0 <= value <= 1e6) or value != value:
            raise VerifyError(f"analysis_parameters.txt: {key} out of range.")
    cov = params.get("covariate")
    if cov and (onm.id_problem(cov) or not __import__("re").fullmatch(r"(?:[A-Za-z]|\.(?![0-9]))[A-Za-z0-9._]*", cov)):
        raise VerifyError("analysis_parameters.txt: the covariate is not a valid column name.")


def _origin_component(bundle: dict):
    return next((c for c in bundle["components"] if c["id"] == "data_origin"), None)


def crate_origin(crate):
    """Certified data origin (data_origin.json) of a crate folder or .zip, or None.
    Used by the app to say what a re-run from the raw data will need."""
    crate = Path(crate)
    try:
        if crate.is_file() and zipfile.is_zipfile(crate):
            with zipfile.ZipFile(crate) as z:
                bundle = _check_bundle(json.loads(_read_member(z, "bundle_manifest.json")))
                c = _origin_component(bundle)
                return json.loads(_read_member(z, c["crate_path"])) if c else None
        bundle = occ.load_bundle_manifest(crate)
        c = _origin_component(_check_bundle(bundle)) if bundle else None
        return json.loads((crate / c["crate_path"]).read_text(encoding="utf-8")) if c else None
    except (KeyError, OSError, ValueError, VerifyError):
        return None


OFFICIAL_CODE = "official_code.json"


def _code_sha(data: bytes) -> str:
    """SHA-256 with line endings normalised: same code, same hash on Windows and Linux."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _official_releases(project: Path) -> list:
    for folder in (project, HERE):
        p = folder / OFFICIAL_CODE
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("releases", [])
            except ValueError:
                return []
    return []


def _code_check(read, bundle: dict, project: Path) -> dict:
    """read(crate_path) -> bytes. -> {"known": bool, "files": [{"file", "label", "known", "origin"}]}"""
    releases = _official_releases(project)
    files = []
    for c in bundle.get("components", []):
        if not c["crate_path"].startswith("code/"):
            continue
        name = _original_path(c["crate_path"])
        digest = _code_sha(read(c["crate_path"]))
        installed = project / name
        if installed.is_file() and _code_sha(installed.read_bytes()) == digest:
            origin = "identical to the code installed here"
        else:
            versions = [r["version"] for r in releases if r.get("files", {}).get(name) == digest]
            origin = f"official OmicChain {versions[-1]}" if versions else None
        files.append({"file": name, "label": c["label"], "known": origin is not None,
                      "origin": origin or "NOT a known OmicChain version", "crate_path": c["crate_path"]})
    return {"known": all(f["known"] for f in files), "files": files}


def crate_code_check(crate, project=HERE):
    """Code check of a crate folder or .zip (used by the app before any re-run). None if unreadable."""
    crate, project = Path(crate), Path(project).resolve()
    try:
        if crate.is_file() and zipfile.is_zipfile(crate):
            with zipfile.ZipFile(crate) as z:
                return _code_check(lambda name: _read_member(z, name),
                                   _check_bundle(json.loads(_read_member(z, "bundle_manifest.json"))), project)
        bundle = occ.load_bundle_manifest(crate)
        return _code_check(lambda cp: (crate / cp).read_bytes(), _check_bundle(bundle), project) if bundle else None
    except (KeyError, OSError, ValueError, VerifyError):
        return None


def crate_code_text(crate, crate_path: str) -> str:
    """Text of one code file of a crate .zip (shown in the app so that it can be read first)."""
    with zipfile.ZipFile(crate) as z:
        return _read_member(z, crate_path).decode("utf-8", errors="replace")


def register_official_code(version: str, project=HERE) -> dict:
    """Maintainers, at release time: record the hashes of the certified code of this installation
    as an official version in official_code.json."""
    project = Path(project).resolve()
    manifest = occ.load_manifest(project / "omicchain.json")
    files = {c["path"]: _code_sha((project / c["path"]).read_bytes())
             for c in manifest["components"] if c.get("role") in ("workflow", "code")}
    p = project / OFFICIAL_CODE
    registry = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"schema": "omicchain.official-code/1",
                                                                              "releases": []}
    registry["releases"] = [r for r in registry["releases"] if r["version"] != version] + [
        {"version": version, "files": files}]
    p.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    return files


def _original_path(crate_path: str) -> str:
    """Where a certified file lived in the project, from its place in the crate."""
    if crate_path.startswith("code/") or crate_path.startswith("inputs/"):
        return crate_path.split("/", 1)[1]
    return "{results_dir}/" + crate_path


def _prepare_workdir(crate: Path, workdir: Path, project: Path, files: dict, bundle: dict) -> None:
    workdir.mkdir(parents=True, exist_ok=False)

    for c in bundle["components"]:
        if c["crate_path"].startswith("code/"):
            dest = workdir / _original_path(c["crate_path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(crate / c["crate_path"], dest)
    if not (workdir / "Snakefile").is_file():
        raise VerifyError("The crate contains no Snakefile: this analysis cannot be re-run here.")

    manifest = {
        "schema": "omicchain.manifest/1",
        "components": [
            {"id": c["id"], "label": c["label"], "role": c.get("role", "file"),
             "path": _original_path(c["crate_path"])}
            for c in bundle["components"]
        ],
    }
    (workdir / "omicchain.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for name in TOOLING:
        src = project / name
        if not src.is_file():
            raise VerifyError(f"{name} not found in {project}.")
        shutil.copyfile(src, workdir / name)

    for key, path in files.items():
        shutil.copyfile(path, workdir / INPUT_NAMES[key])

    for c in bundle["components"]:
        if c["id"] == "data_origin":
            shutil.copyfile(crate / c["crate_path"], workdir / "data_origin.json")

    if not (project / "renv" / "activate.R").is_file():
        raise VerifyError(
            f"renv is not initialised in {project}: run renv::init(bare = TRUE) and "
            "install_dependencies.R there first."
        )
    (workdir / "renv").mkdir()
    (workdir / "renv" / "activate.R").write_text(
        "# Activates the renv library of the OmicChain project, then comes back here.\n"
        "local({\n"
        f"  owd <- setwd({json.dumps(project.as_posix())})\n"
        "  on.exit(setwd(owd))\n"
        '  source("renv/activate.R")\n'
        "})\n",
        encoding="utf-8",
    )
    (workdir / ".Rprofile").write_text('source("renv/activate.R")\n', encoding="utf-8")


def _snakemake_command() -> list:
    """Snakemake of this Python if installed, otherwise the `snakemake` command
    on the PATH (what the OmicChain app runs)."""
    import importlib.util
    if importlib.util.find_spec("snakemake") is not None:
        return [sys.executable, "-m", "snakemake"]
    exe = shutil.which("snakemake")
    if exe:
        return [exe]
    raise VerifyError(
        "Snakemake was not found, neither for this Python nor as a command: "
        "python -m pip install snakemake"
    )


def _find_results(workdir: Path) -> Path:
    found = [p.parent for p in (workdir / "results").glob("*/bundle_manifest.json")]
    if len(found) != 1:
        raise VerifyError(f"Expected one results folder in the re-run, found {len(found)}.")
    return found[0]


def _compare_tables(a: Path, b: Path) -> dict:
    """Numerical comparison of two results tables (information only)."""
    import pandas as pd

    ta, tb = pd.read_csv(a, sep="\t"), pd.read_csv(b, sep="\t")
    key = "Gene" if "Gene" in ta.columns and "Gene" in tb.columns else None
    out = {"rows": (len(ta), len(tb))}
    if key is None:
        return out
    ga, gb = set(ta[key]), set(tb[key])
    out.update(only_certified=sorted(ga - gb)[:20], only_rerun=sorted(gb - ga)[:20],
               n_only_certified=len(ga - gb), n_only_rerun=len(gb - ga))
    common = ta.merge(tb, on=key, suffixes=("_c", "_r"))
    diffs = {}
    for col in ta.columns:
        if col == key or f"{col}_c" not in common:
            continue
        x, y = pd.to_numeric(common[f"{col}_c"], errors="coerce"), pd.to_numeric(common[f"{col}_r"], errors="coerce")
        if x.notna().any():
            d = (x - y).abs()
            rel = d / x.abs().where(x.abs() > 0)
            diffs[col] = {"max_abs": float(d.max()), "max_rel": float(rel.max()) if rel.notna().any() else 0.0}
    out["max_differences"] = diffs
    return out


def _compare_locks(a: Path, b: Path) -> list:
    """Package / R versions that differ between two renv.lock files."""
    try:
        la, lb = json.loads(a.read_text(encoding="utf-8")), json.loads(b.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["renv.lock could not be compared"]
    out = []
    ra, rb = la.get("R", {}).get("Version"), lb.get("R", {}).get("Version")
    if ra != rb:
        out.append(f"R {ra} -> {rb}")
    pa, pb = la.get("Packages", {}), lb.get("Packages", {})
    for name in sorted(set(pa) | set(pb)):
        va, vb = pa.get(name, {}).get("Version"), pb.get(name, {}).get("Version")
        if va != vb:
            out.append(f"{name} {va or 'absent'} -> {vb or 'absent'}")
    if not out:
        out.append("same R and package versions (the files differ in other metadata)")
    return out


def rerun_verify(crate, *, expression=None, metadata=None, expected=None,
                 project=HERE, workdir=None, keep=False, cores=4, log=print,
                 from_raw=False, fastq_dir=None, matrix=None, ref_dir=None,
                 prepare_reference=False, raw_workdir=None, download=True,
                 trust_crate_code=False) -> dict:
    """Run the four steps (five with from_raw). Returns a report dict with
    'verdict', 'exit_code' and 'scope' ('downstream' or 'raw')."""
    project = Path(project).resolve()
    cleanup = []
    report = {"verdict": "NOT REPRODUCED", "exit_code": 1, "steps": [], "warnings": [],
              "scope": "raw" if from_raw else "downstream"}
    raw_dir = None

    def step(name, ok, detail=""):
        report["steps"].append({"name": name, "ok": ok, "detail": detail})
        log(f"[{'OK' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

    try:
        crate = Path(crate)
        if crate.is_file() and zipfile.is_zipfile(crate):
            tmp = Path(tempfile.mkdtemp(prefix="omicchain-crate-"))
            cleanup.append(tmp)
            try:
                _safe_extract(crate, tmp)
            except (VerifyError, zipfile.BadZipFile, OSError) as exc:
                report["verdict"] = "NOT VERIFIED (the crate was refused)"
                step("crate opened safely", False, str(exc))
                return report
            crate = tmp
        crate = crate.resolve()

        raw_manifest = occ.load_bundle_manifest(crate)
        if not raw_manifest:
            raise VerifyError("No bundle_manifest.json in this crate: not an OmicChain crate.")
        _check_bundle(raw_manifest)

        integrity = occ.verify_crate(crate, expected_bundle_hash=expected)
        bad = [c["label"] for c in integrity["components"] if not c["ok"]]
        if not integrity["ok"]:
            step("crate integrity", False, "modified or missing: " + (", ".join(bad) or "bundle fingerprint"))
            return report
        if integrity["matches_expected"] is False:
            step("crate integrity", False, "the crate's fingerprint is not the one given with --expected")
            return report
        step("crate integrity", True, "matches the fingerprint published on-chain" if expected else
             "files match bundle_manifest.json (use --expected to compare with the on-chain fingerprint)")
        bundle = occ.load_bundle_manifest(crate)
        report["certified_bundle"] = bundle["bundle_hash"]

        check = _code_check(lambda cp: (crate / cp).read_bytes(), bundle, project)
        report["code_check"] = check
        unknown = [f["file"] for f in check["files"] if not f["known"]]
        if not unknown:
            step("code of the crate", True, "; ".join(sorted({f["origin"] for f in check["files"]})))
        elif trust_crate_code:
            step("code of the crate", True, "not a known OmicChain version (" + ", ".join(unknown)
                 + "): run because you accepted it")
            report["warnings"].append("the crate's code is not a known OmicChain version: " + ", ".join(unknown))
        else:
            step("code of the crate", False, "not a known OmicChain version: " + ", ".join(unknown)
                 + ". Re-running would execute this code on your computer: read it first, then accept it "
                   "(--trust-crate-code, or the checkbox in the application).")
            report["verdict"] = "NOT VERIFIED (the crate contains code that is not a known OmicChain version)"
            return report

        if from_raw:
            raw_dir, raw_temp = _replay_raw(crate, bundle, report, step, log, project=project, metadata=metadata,
                                            expression=expression, fastq_dir=fastq_dir, matrix=matrix,
                                            ref_dir=ref_dir, prepare_reference=prepare_reference,
                                            raw_workdir=raw_workdir, download=download, cores=cores)
            if raw_temp:
                report["_raw_temp"] = str(raw_dir)
            if not report["raw"].get("ok"):
                return report
            expression = report["raw"]["expression"]

        files = {}
        for key, given in (("expression", expression), ("metadata", metadata)):
            in_crate = crate / "inputs" / INPUT_NAMES[key]
            path = Path(given) if given else (in_crate if in_crate.is_file() else None)
            if path is None or not path.is_file():
                raise VerifyError(
                    f"The {key} file is needed to re-run the analysis: it is not in the crate, "
                    f"pass it with --{key}."
                )
            files[key] = path.resolve()
        report["warnings"] += _check_inputs(crate, files)
        step("input data are the certified ones", True)
        for w in report["warnings"]:
            log("  warning: " + w)

        params = occ.read_key_value_file(crate / "analysis_parameters.txt")
        if "padj_threshold" not in params or "lfc_threshold" not in params:
            raise VerifyError("analysis_parameters.txt does not give padj_threshold and lfc_threshold.")
        _check_parameters(params)

        if workdir:
            workdir = Path(workdir).resolve()
            if workdir.exists():
                raise VerifyError(f"The re-run folder {workdir} already exists: choose a new one.")
        else:
            temp_root = Path(tempfile.mkdtemp(prefix="omicchain-rerun-"))
            report["_temp_root"] = str(temp_root)
            workdir = temp_root / "run"
        report["workdir"] = str(workdir)
        _prepare_workdir(crate, workdir, project, files, bundle)
        config = [f"padj_threshold={params['padj_threshold']}", f"lfc_threshold={params['lfc_threshold']}"]
        if params.get("covariate"):
            config.append(f"covariate={params['covariate']}")
        log(f"Re-running the analysis in {workdir} (padj < {params['padj_threshold']}, "
            f"|log2FC| >= {params['lfc_threshold']}"
            + (f", covariate {params['covariate']}" if params.get("covariate") else "") + ")...")
        env = dict(os.environ, OMICCHAIN_RENV_PROJECT=str(project))
        for c in bundle["components"]:
            if c.get("role") == "environment":
                env["OMICCHAIN_SNAPSHOT_LIKE"] = str((crate / c["crate_path"]).resolve())
        with open(workdir / "rerun.log", "w", encoding="utf-8") as logf:
            proc = subprocess.run(
                _snakemake_command() + ["-j", str(cores), "--config", *config],
                cwd=workdir, env=env, stdout=logf, stderr=subprocess.STDOUT,
            )
        if proc.returncode != 0:
            step("re-run of the pipeline", False, f"Snakemake failed, see {workdir / 'rerun.log'}")
            report["verdict"] = "NOT VERIFIED (the re-run could not complete)"
            lines = (workdir / "rerun.log").read_text(encoding="utf-8", errors="replace").splitlines()
            report["log_tail"] = lines[-40:]
            keep = True
            return report
        new_dir = _find_results(workdir)
        step("re-run of the pipeline", True)

        new_bundle = occ.load_bundle_manifest(new_dir)
        report["rerun_bundle"] = new_bundle["bundle_hash"]
        new_by_id = {c["id"]: c for c in new_bundle["components"]}
        diff = []
        for c in bundle["components"]:
            n = new_by_id.get(c["id"])
            same = bool(n and n["sha256"] == c["sha256"])
            log(f"  [{'same' if same else 'DIFFERENT'}] {c['label']}")
            report.setdefault("components", []).append({"label": c["label"], "same": same})
            if not same:
                diff.append(c)
        report["different"] = [c["label"] for c in diff]

        if new_bundle["bundle_hash"] == bundle["bundle_hash"]:
            report.update(verdict="REPRODUCED", exit_code=0)
        else:
            outputs_differ = any(c.get("role") == "output" for c in diff)
            if not outputs_differ:
                report.update(verdict="RESULTS REPRODUCED", exit_code=2)
            for c in diff:
                if c.get("role") == "environment":
                    report["environment_changes"] = _compare_locks(crate / c["crate_path"], new_dir / c["crate_path"])
                if c.get("role") == "output" and c["crate_path"].endswith(".tsv"):
                    report.setdefault("table_comparison", {})[c["label"]] = _compare_tables(
                        crate / c["crate_path"], new_dir / c["crate_path"])
        return report

    except VerifyError as exc:
        step("verification", False, str(exc))
        return report
    finally:
        for tmp in cleanup:
            shutil.rmtree(tmp, ignore_errors=True)
        temp_root = report.pop("_temp_root", None)
        if temp_root and not keep and report["exit_code"] == 0:
            shutil.rmtree(temp_root, ignore_errors=True)
            report["workdir_removed"] = True
        raw_temp = report.pop("_raw_temp", None)
        if raw_temp and not keep and report["exit_code"] == 0:
            shutil.rmtree(raw_temp, ignore_errors=True)
            report["raw_workdir_removed"] = True


def _replay_raw(crate, bundle, report, step, log, *, project, metadata, expression, fastq_dir, matrix,
                ref_dir, prepare_reference, raw_workdir, download, cores):
    """Step 1b: rebuild the expression table from the certified raw data.
    -> (replay folder, True if it is a default folder that may be removed)."""
    import omicchain_replay as orp

    comp = _origin_component(bundle)
    if comp is None:
        raise VerifyError("This analysis was certified without a data origin (data loaded by hand or before "
                          "OmicChain recorded it): only the downstream part can be re-run.")
    origin = json.loads((crate / comp["crate_path"]).read_text(encoding="utf-8"))
    meta_path = Path(metadata) if metadata else crate / "inputs" / INPUT_NAMES["metadata"]
    if not meta_path.is_file():
        raise VerifyError("The metadata file is needed to re-run the analysis: it is not in the crate, "
                          "pass it with --metadata.")
    certified_expr = Path(expression) if expression else crate / "inputs" / INPUT_NAMES["expression"]
    if raw_workdir:
        raw_dir, temp = Path(raw_workdir).resolve(), False
    else:
        raw_dir, temp = project / "verify_runs" / bundle["bundle_hash"][:16], True
    report["raw_workdir"] = str(raw_dir)
    info = orp.describe(origin)
    log({"public": f"Raw data: public study {info.get('accession')} ({info.get('files')} FASTQ files)",
         "local": f"Raw data: FASTQ files provided by the user ({info.get('files')} files)",
         "matrix": f"Raw data: count matrix {info.get('file')}"}[info["kind"]])
    try:
        rep = orp.replay(origin, meta_path, raw_dir, fastq_dir=fastq_dir, matrix=matrix, ref_dir=ref_dir,
                         prepare_reference=prepare_reference, download=download, cores=cores, project=project,
                         certified_expression=certified_expr if certified_expr.is_file() else None, log=log)
    except orp.ReplayError as exc:
        report["raw"] = {"kind": info["kind"], "ok": False, "error": str(exc)}
        step("raw data -> expression table", False, str(exc))
        report["verdict"] = ("NOT VERIFIED (the certified raw data or reference are not available)"
                             if exc.kind == "unavailable" else "NOT VERIFIED (the replay from the raw data could not complete)")
        return raw_dir, False
    report["raw"] = rep
    report["warnings"] += rep["warnings"]
    what = {"public": "FASTQ re-checked against the certified md5, aligned and counted again",
            "local": "FASTQ re-checked against the certified md5 / SHA-256, aligned and counted again",
            "matrix": "original matrix re-checked against its certified SHA-256"}[rep["kind"]]
    if rep["ok"]:
        step("raw data -> expression table", True, f"{rep['files']} file(s); {what}; the expression table "
             "rebuilt has the certified SHA-256")
    else:
        step("raw data -> expression table", False, f"{what}, but the expression table rebuilt differs from "
             "the certified one")
        report["verdict"] = "NOT REPRODUCED"
    return raw_dir, temp


def _print_summary(report: dict) -> None:
    print()
    if report.get("certified_bundle"):
        print(f"certified fingerprint : {report['certified_bundle']}")
    if report.get("rerun_bundle"):
        print(f"re-run fingerprint    : {report['rerun_bundle']}")
    for line in report.get("environment_changes", []):
        print(f"  environment: {line}")
    for label, cmp in report.get("table_comparison", {}).items():
        print(f"  {label}: {cmp['rows'][0]} rows certified, {cmp['rows'][1]} rows re-run")
        if "n_only_certified" in cmp:
            print(f"    genes only in the certified table: {cmp['n_only_certified']}, only in the re-run: {cmp['n_only_rerun']}")
        for col, d in cmp.get("max_differences", {}).items():
            print(f"    {col}: max absolute difference {d['max_abs']:.3g}, max relative {d['max_rel']:.3g}")
        print("    (information only: the fingerprint is not reproduced)")
    if report.get("log_tail"):
        print("\n--- end of rerun.log ---")
        for line in report["log_tail"]:
            print("  " + line)
        print("--- end of log ---")
    raw = report.get("raw") or {}
    for o in raw.get("outputs", []):
        print(f"  raw-data replay, {o['name']}: {'same' if o['same'] else 'DIFFERENT'}")
    cmp = raw.get("comparison")
    if cmp:
        print(f"  expression table: {cmp['genes'][0]} genes certified, {cmp['genes'][1]} rebuilt; "
              f"{cmp.get('genes_with_different_counts', '?')} gene(s) with other counts "
              f"(max difference {cmp.get('max_count_difference', '?')})")
    scope = " (from the raw data)" if report.get("scope") == "raw" and report["exit_code"] in (0, 2) else ""
    print(f"\nVERDICT : {report['verdict']}{scope}")
    if report["verdict"] == "RESULTS REPRODUCED":
        print("The results are identical, but the fingerprint differs because of: " + ", ".join(report["different"]))
    if report.get("workdir") and not report.get("workdir_removed"):
        print(f"Re-run folder kept: {report['workdir']}")
    if report.get("raw_workdir") and not report.get("raw_workdir_removed"):
        print(f"Raw-data replay folder kept (running again resumes there): {report['raw_workdir']}")


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="omicchain_verify",
                                     description="Re-run a certified OmicChain analysis and compare fingerprints.")
    parser.add_argument("crate", nargs="?", help="RO-Crate folder or omicchain-crate.zip")
    parser.add_argument("--expression", help="expression matrix (if not in the crate)")
    parser.add_argument("--metadata", help="sample metadata (if not in the crate)")
    parser.add_argument("--expected", help="fingerprint read on-chain (recommended)")
    parser.add_argument("--project", default=str(HERE), help="OmicChain project with renv (default: this folder)")
    parser.add_argument("--workdir", help="re-run folder (must not exist; default: a temporary folder)")
    parser.add_argument("--keep", action="store_true", help="keep the re-run folder even when reproduced")
    parser.add_argument("--cores", type=int, default=4)
    raw = parser.add_argument_group("re-run from the raw data")
    raw.add_argument("--from-raw", action="store_true",
                     help="rebuild the expression table from the certified raw data first (FASTQ or count matrix)")
    raw.add_argument("--fastq-dir", help="folder with the FASTQ files (needed for your own FASTQ; for a public "
                                         "study, files found there are not downloaded again)")
    raw.add_argument("--matrix", help="original count matrix (analyses started from a matrix)")
    raw.add_argument("--ref-dir", help="reference cache (default: the OmicChain one)")
    raw.add_argument("--prepare-reference", action="store_true",
                     help="download and build the reference if it is not in the cache (long)")
    raw.add_argument("--raw-workdir", help="replay folder (default: verify_runs/ in the project; kept to resume)")
    parser.add_argument("--report-json", help="also write the report to this JSON file (used by the app)")
    parser.add_argument("--trust-crate-code", action="store_true",
                        help="run the crate's code even if it is not a known OmicChain version (read it first: "
                             "it is executed on this computer)")
    parser.add_argument("--register-official-code", metavar="VERSION",
                        help="maintainers: record the certified code installed here as an official version "
                             "in official_code.json, and exit")
    args = parser.parse_args(argv)
    if args.register_official_code:
        for name, digest in register_official_code(args.register_official_code, args.project).items():
            print(f"{digest}  {name}")
        return 0
    if not args.crate:
        parser.error("the crate (folder or .zip) is required")
    report = rerun_verify(args.crate, expression=args.expression, metadata=args.metadata,
                          expected=args.expected, project=args.project, workdir=args.workdir,
                          keep=args.keep, cores=args.cores, from_raw=args.from_raw, fastq_dir=args.fastq_dir,
                          matrix=args.matrix, ref_dir=args.ref_dir, prepare_reference=args.prepare_reference,
                          raw_workdir=args.raw_workdir, trust_crate_code=args.trust_crate_code)
    _print_summary(report)
    if args.report_json:
        Path(args.report_json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(_main())
