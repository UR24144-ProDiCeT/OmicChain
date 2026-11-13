"""Re-run of a certified analysis from its raw data (omicchain_verify.py --from-raw): check the raw files against the certified checksums, align and count again, compare the rebuilt expression table."""

import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import omicchain_reference as oref
import omicchain_upstream as ou

HERE = Path(__file__).resolve().parent
UPSTREAM_OUTPUTS = ("expression_input.tsv", "counts_by_gene_id.tsv", "metadata.tsv", "qc_summary.tsv")
FASTQ_EXT = re.compile(r"\.(fastq|fq)(\.gz)?$", re.I)
FIXED_COLUMNS = ["sample", "title", "source_name", "organism", "library_layout", "library_strategy",
                 "runs", "read_count", "fastq_bytes"]


class ReplayError(Exception):
    """The replay cannot complete. kind: 'unavailable' (raw data or reference missing or not the certified ones) or 'technical' (failure during the run)."""

    def __init__(self, message, kind="unavailable"):
        super().__init__(message)
        self.kind = kind


def source_kind(origin):
    """'public', 'local' or 'matrix'."""
    schema = str(origin.get("schema", ""))
    if schema.startswith("omicchain.matrix"):
        return "matrix"
    if schema.startswith("omicchain.upstream"):
        local = any(str(i.get("md5_source", "")).startswith("local") for i in origin.get("inputs", []))
        return "local" if local else "public"
    raise ReplayError(f"unknown data origin format ({schema or 'no schema'})")


def check_origin(origin):
    """Refuse a data origin whose names could not be used safely as file names."""
    import omicchain_names as onm
    if source_kind(origin) == "matrix":
        bad = onm.check_ids([Path(str(origin.get("source", {}).get("original_file", ""))).name], "matrix file")
    else:
        inputs = origin.get("inputs", [])
        bad = (onm.check_ids([i.get("run") for i in inputs], "run")
               + onm.check_ids([n for i in inputs for n in i.get("fastq", [])], "FASTQ file")
               + onm.check_ids((origin.get("design") or {}).get("samples", []), "sample"))
        ref = origin.get("reference") or {}
        if not re.fullmatch(r"M?\d{1,3}", str(ref.get("gencode_release", ""))) or ref.get("index_type") not in ("gapped", "full"):
            bad.append("unexpected reference (GENCODE release or index type)")
    if bad:
        raise ReplayError("the data origin of this crate holds names that cannot be used safely: " + "; ".join(bad[:6]))


def describe(origin):
    """Summary of what the replay will need (shown by the application before starting)."""
    kind = source_kind(origin)
    out = {"kind": kind}
    if kind == "matrix":
        src = origin.get("source", {})
        out.update(file=src.get("original_file"), sha256=src.get("original_sha256"))
    else:
        inputs = origin.get("inputs", [])
        ref = origin.get("reference", {})
        out.update(accession=(origin.get("study") or {}).get("accession"), runs=len(inputs),
                   files=sum(len(i.get("fastq", [])) for i in inputs),
                   gencode=ref.get("gencode_release"), index_type=ref.get("index_type"),
                   index_fingerprint=ref.get("index_fingerprint"),
                   samples=len((origin.get("design") or {}).get("samples", [])))
    return out


def _sha256(path):
    return oref.sha256_file(path)


def _md5_sha256(path, chunk=8 << 20):
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            md5.update(block)
            sha.update(block)
    return md5.hexdigest(), sha.hexdigest()


def _index_folder(folder):
    by_name = {}
    if folder:
        folder = Path(folder)
        if not folder.is_dir():
            raise ReplayError(f"FASTQ folder not found: {folder}")
        for p in sorted(folder.rglob("*")):
            if p.is_file() and FASTQ_EXT.search(p.name):
                by_name.setdefault(p.name, []).append(p)
    return by_name


def gather_fastq(origin, dest, fastq_dir=None, download=True, log=print):
    """Find every certified FASTQ file (folder given, otherwise ENA download) and check its checksums. Return ({run: info}, number of files downloaded)."""
    kind = source_kind(origin)
    by_name = _index_folder(fastq_dir)
    dest = Path(dest)
    runs, missing, wrong, ena_cache, downloaded = {}, [], [], {}, 0
    for item in origin.get("inputs", []):
        run, names = item["run"], item.get("fastq", [])
        md5s = item.get("md5", [])
        shas = item.get("sha256") or [None] * len(names)
        paths = []
        for k, name in enumerate(names):
            want_md5, want_sha = str(md5s[k]).lower(), shas[k]
            found = None
            for cand in by_name.get(name, []):
                got_md5, got_sha = _md5_sha256(cand)
                if got_md5 == want_md5 and (not want_sha or got_sha == want_sha):
                    found = cand
                    break
                wrong.append(f"{cand} (md5 {got_md5}, certified {want_md5})")
            if found is None and kind == "public" and download:
                found = _download_from_ena(run, name, want_md5, dest / "fastq", ena_cache, log)
                downloaded += found is not None
            if found is None:
                missing.append(name)
                continue
            log(f"  OK  {name} (md5 = certified)")
            paths.append(found)
        read_count = None
        if kind == "public":
            row = ena_cache.get(run)
            if row is None and download:
                try:
                    row = _ena_row(run, ena_cache)
                except Exception:
                    row = None
            if row and str(row.get("read_count", "")).isdigit():
                read_count = int(row["read_count"])
        runs[run] = {"sample": item["sample"], "paths": paths, "md5": [str(m).lower() for m in md5s],
                     "sha256": shas, "md5_source": item.get("md5_source", "ENA"), "read_count": read_count}
    if missing:
        msg = f"{len(missing)} certified FASTQ file(s) not found"
        if kind == "local":
            msg += (" in the folder given" if fastq_dir else " (give the folder that contains them)")
        msg += ": " + ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else "")
        if wrong:
            msg += ". Files with the same name but another content (not the certified ones): " + "; ".join(wrong[:4])
        raise ReplayError(msg)
    return runs, downloaded


def _ena_row(run, cache):
    import omicchain_fetch as of
    if run not in cache:
        rows, _, _ = of.ena_runs(run)
        cache[run] = next((r for r in rows if r.get("run_accession") == run), None)
    return cache[run]


def _download_from_ena(run, name, md5, dest_dir, cache, log):
    """Download a FASTQ file from the ENA if the ENA still distributes the certified file."""
    import omicchain_fetch as of
    try:
        row = _ena_row(run, cache)
    except Exception as e:
        raise ReplayError(f"ENA could not be reached for {run}: {e}", "technical")
    if not row:
        raise ReplayError(f"{run}: no longer listed by the ENA")
    f = next((x for x in of.run_files(row) if x["name"] == name), None)
    if f is None:
        raise ReplayError(f"{run}: the ENA no longer distributes {name}")
    if f["md5"].lower() != md5:
        raise ReplayError(f"{run}: the ENA now distributes another {name} (md5 {f['md5']}, certified {md5}): "
                          "the certified raw file is no longer available there")
    dest_dir.mkdir(parents=True, exist_ok=True)
    log(f"  downloading {name} from the ENA" + (f" ({f['bytes'] / 1024 ** 2:.0f} MB)" if f.get("bytes") else ""))
    try:
        return of.download(f["url"], dest_dir / name, md5=md5, size=f.get("bytes"))
    except of.FetchError as e:
        raise ReplayError(f"download of {name} failed: {e} (run the verification again to resume)", "technical")


def build_study(origin, metadata, runs, study):
    """Write the study files read by upstream.smk; return True if the metadata covers every sample of the data origin."""
    import pandas as pd
    design = origin["design"]
    cond = design["condition_column"]
    meta = pd.read_csv(metadata, sep="\t", dtype=str).fillna("")
    unknown = sorted(set(meta["sample"]) - set(design["samples"]))
    if unknown:
        raise ReplayError("samples of the metadata absent from the certified data origin: " + ", ".join(unknown[:8]))
    meta = meta.set_index("sample")
    covariates = [c for c in meta.columns if c not in ("group", "condition", "title")]
    coding = {"control": design["reference_level"], "treatment": design["test_level"]}
    complete = set(design["samples"]) <= set(meta.index)
    paired = any(len(r["paths"]) == 2 for r in runs.values())
    by_sample = {}
    for run, r in runs.items():
        by_sample.setdefault(r["sample"], []).append(run)

    study = Path(study)
    study.mkdir(parents=True, exist_ok=True)
    header = FIXED_COLUMNS + [cond] + covariates
    with open(study / "samples.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(header)
        for s in design["samples"]:
            if s in meta.index:
                m = meta.loc[s]
                level = m["condition"] if "condition" in meta.columns else coding[m["group"]]
                title = m["title"] if "title" in meta.columns else s
                covs = [m[c] for c in covariates]
            else:
                level, title, covs = design["reference_level"], s, [""] * len(covariates)
            w.writerow([s, title, "", "", "PAIRED" if paired else "SINGLE", "RNA-Seq",
                        ";".join(sorted(by_sample.get(s, []))), "", ""] + [level] + covs)
    with open(study / "ena_runs.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["run_accession", "read_count"])
        for run in sorted(runs):
            w.writerow([run, "" if runs[run]["read_count"] is None else runs[run]["read_count"]])
    files = []
    for run in sorted(runs):
        r = runs[run]
        for k, p in enumerate(r["paths"]):
            files.append({"sample": r["sample"], "run": run, "mate": (k + 1) if len(r["paths"]) == 2 else 0,
                          "path": str(Path(p).resolve()), "md5": r["md5"][k], "sha256": r["sha256"][k],
                          "md5_source": r["md5_source"], "md5_verified": True})
    manifest = {"tool": "omicchain_replay", "provenance": {"input": (origin.get("study") or {}).get("accession", "")},
                "files": files}
    (study / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return complete


def check_reference(origin, ref_dir, prepare=False, log=print):
    """The cached reference must have the certified fingerprints (genome, GTF, index)."""
    ref = origin["reference"]
    release, index_type = str(ref["gencode_release"]), ref["index_type"]
    ref_dir = Path(ref_dir or oref.default_ref_dir())
    paths = oref.ref_paths(ref_dir, release)
    man = oref.load_manifest(paths["manifest"])
    if not man or index_type not in man.get("indexes", {}):
        if not prepare:
            raise ReplayError(f"the reference GENCODE v{release} / {index_type} index is not in {ref_dir}: prepare it "
                              f"(Data & reference step of the application, or python omicchain_reference.py prepare --gencode {release} "
                              f"--index {index_type}), or allow the verification to prepare it")
        log(f"Preparing the reference GENCODE v{release} / {index_type} index (download + build, long)...")
        code = subprocess.call([sys.executable, str(HERE / "omicchain_reference.py"), "--ref-dir", str(ref_dir),
                                "prepare", "--gencode", release, "--index", index_type])
        man = oref.load_manifest(paths["manifest"])
        if code != 0 or not man or index_type not in man.get("indexes", {}):
            raise ReplayError("the reference could not be prepared (see the log above)", "technical")
    problems = []
    for key, label in (("genome", "genome"), ("gtf", "annotation (GTF)")):
        if man["sources"][key]["sha256"] != ref.get(f"{key}_sha256"):
            problems.append(f"{label}: SHA-256 {man['sources'][key]['sha256'][:16]}... instead of the certified "
                            f"{str(ref.get(f'{key}_sha256'))[:16]}...")
    entry = man["indexes"][index_type]
    if entry["fingerprint"] != ref.get("index_fingerprint"):
        problems.append(f"{index_type} index: fingerprint {entry['fingerprint'][:16]}... (Rsubread "
                        f"{entry.get('rsubread_version')}) instead of the certified {str(ref.get('index_fingerprint'))[:16]}... "
                        f"(Rsubread {ref.get('index_rsubread_version')})")
    if problems:
        raise ReplayError("the reference available is not the certified one: " + "; ".join(problems))
    log(f"  OK  reference GENCODE v{release}, {index_type} index: certified fingerprints")
    return ref_dir


def run_upstream(origin, study, ref_dir, project, cores=None, log=print):
    design, ref, counting = origin["design"], origin["reference"], origin.get("counting", {})
    index_type, release = ref["index_type"], str(ref["gencode_release"])
    strand = "auto" if counting.get("strand_mode", "detected") == "detected" else str(counting.get("strand_specific"))
    cores, mem, align_threads = ou.resources(index_type, cores)
    project = Path(project)
    smk_file = project / "upstream.smk" if (project / "upstream.smk").is_file() else HERE / "upstream.smk"
    workdir = Path(study) / "upstream"
    workdir.mkdir(parents=True, exist_ok=True)
    config = ou.write_config(workdir, {
        "study": str(Path(study).resolve()), "condition": design["condition_column"],
        "reference_level": design["reference_level"], "test_level": design["test_level"],
        "samples": list(design["samples"]), "index": index_type, "gencode": release, "strand": strand,
        "align_threads": align_threads, "ref_dir": str(Path(ref_dir).resolve())})
    smk = [sys.executable, "-m", "snakemake", "-s", str(smk_file), "--directory", str(workdir)]
    subprocess.call(smk + ["--unlock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"Aligning and counting again ({len(design['samples'])} samples, {cores} cores, "
        f"{align_threads} threads per alignment, strand {strand})...")
    log_path = Path(study) / "upstream_replay.log"
    with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(smk + ["-j", str(cores), "--resources", f"mem_gb={mem}", "--rerun-incomplete", *config],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in proc.stdout:
            fh.write(line)
            if "steps (" in line or "WARNING" in line:
                log("  " + line.strip())
        code = proc.wait()
    if code != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        raise ReplayError("alignment / counting failed:\n" + "\n".join(tail), "technical")
    return workdir / f"{index_type}_gencode{release}"


def _compare_counts(certified, replayed):
    """Numeric comparison of two expression tables (information only)."""
    import pandas as pd
    a = pd.read_csv(certified, sep="\t", index_col=0)
    b = pd.read_csv(replayed, sep="\t", index_col=0)
    out = {"genes": (len(a), len(b)), "samples": (list(a.columns), list(b.columns))}
    common = a.index.intersection(b.index)
    cols = [c for c in a.columns if c in b.columns]
    if len(common) and cols:
        d = (a.loc[common, cols] - b.loc[common, cols]).abs()
        out["genes_with_different_counts"] = int((d.max(axis=1) > 0).sum())
        out["max_count_difference"] = int(d.to_numpy().max())
    return out


def _compare_code(origin, installed, rep, what):
    """Record whether the installed upstream code is the certified one (rep['code_same'])."""
    certified = (origin.get("tool") or {}).get("code_sha256")
    if not certified:
        rep["code_same"] = None
        return
    diff = sorted(f for f in certified if certified[f] != installed.get(f))
    rep["code_same"] = not diff
    if diff:
        rep["warnings"].append(f"the {what} code installed here is not the certified one (different: "
                               + ", ".join(diff) + ")")


def replay(origin, metadata, raw_dir, *, fastq_dir=None, matrix=None, ref_dir=None, prepare_reference=False,
           download=True, cores=None, project=HERE, certified_expression=None, log=print):
    """Rebuild the expression table from the raw data. Return a report whose 'ok' says whether it has the certified SHA-256; raise ReplayError if the replay cannot complete."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    kind = source_kind(origin)
    check_origin(origin)
    certified = origin.get("outputs") or {}
    rep = {"kind": kind, "warnings": [], "outputs": []}

    if kind == "matrix":
        import omicchain_matrix as om
        if not matrix:
            raise ReplayError(f"the original count matrix is needed ({origin['source'].get('original_file')})")
        data = Path(matrix).read_bytes()
        got = hashlib.sha256(data).hexdigest()
        if got != origin["source"]["original_sha256"]:
            raise ReplayError(f"{Path(matrix).name} is not the certified count matrix (SHA-256 {got[:16]}..., "
                              f"certified {origin['source']['original_sha256'][:16]}...)")
        log(f"  OK  {Path(matrix).name} (SHA-256 = certified)")
        ds, _ = om.import_matrix(data, origin["source"]["original_file"], raw_dir, name="replay")
        expression = raw_dir / "expression_input.tsv"
        om.write_expression(ds, origin["design"]["samples"], expression)
        rep["files"] = 1
        _compare_code(origin, {"omicchain_matrix.py": hashlib.sha256(
            Path(om.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()}, rep, "matrix import")
        names = ["expression_input.tsv"]
        out_dir = raw_dir
    else:
        runs, rep["downloaded"] = gather_fastq(origin, raw_dir, fastq_dir, download=download, log=log)
        rep["files"] = sum(len(r["paths"]) for r in runs.values())
        ref_root = check_reference(origin, ref_dir, prepare_reference, log=log)
        study = raw_dir / "study"
        complete = build_study(origin, metadata, runs, study)
        out_dir = run_upstream(origin, study, ref_root, project, log=log)
        expression = out_dir / "expression_input.tsv"
        names = list(UPSTREAM_OUTPUTS)
        if not complete:
            names.remove("metadata.tsv")
            rep["warnings"].append("some samples counted for this study were left out of the analysis: the "
                                   "upstream metadata.tsv cannot be rebuilt identically and is not compared")
        new = json.loads((out_dir / "upstream_provenance.json").read_text(encoding="utf-8"))
        old_v, new_v = origin.get("alignment", {}).get("rsubread_version"), new["alignment"].get("rsubread_version")
        if old_v != new_v:
            rep["warnings"].append(f"Rsubread {new_v} used for the replay, {old_v} for the certified analysis")
        _compare_code(origin, new["tool"].get("code_sha256") or {}, rep, "alignment / counting")
        c_old, c_new = origin.get("counting", {}), new["counting"]
        if c_old.get("strand_specific") != c_new.get("strand_specific"):
            rep["warnings"].append(f"library strand detected: {c_new.get('strand_specific')} "
                                   f"(certified: {c_old.get('strand_specific')})")

    for name in names:
        want = certified.get(name)
        if want is None:
            continue
        rep["outputs"].append({"name": name, "same": _sha256(out_dir / name) == want})
    rep["expression"] = str(expression)
    rep["ok"] = bool(certified.get("expression_input.tsv")) and _sha256(expression) == certified["expression_input.tsv"]
    if not rep["ok"] and certified_expression and Path(certified_expression).is_file() \
            and _sha256(certified_expression) == certified.get("expression_input.tsv"):
        rep["comparison"] = _compare_counts(certified_expression, expression)
    return rep
