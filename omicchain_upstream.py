"""Logic of the upstream workflow (upstream.smk): sample selection, reference checks, merging of the counts, quality control and deterministic provenance."""

import csv
import gzip
import hashlib
import json
import os
import re
from pathlib import Path

import omicchain_names as onm
import omicchain_reference as oref

VERSION = "0.2"
CODE_FILES = ("upstream.smk", "upstream_align.R", "upstream_count.R", "omicchain_upstream.py")


def code_hashes(code_dir=None, files=CODE_FILES):
    """{file: SHA-256} of the upstream code, with line endings normalised."""
    code_dir = Path(code_dir) if code_dir else Path(__file__).resolve().parent
    out = {}
    for name in files:
        p = code_dir / name
        out[name] = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if p.is_file() else None
    return out


class UpstreamError(Exception):
    pass


def sha256_file(path):
    return oref.sha256_file(path)


def read_tsv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_kv(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            k, v = line.split("\t", 1)
            out[k] = v
    return out


ANNOTATED_TABLE = "samples_annotated.tsv"
ANNOTATIONS_JSON = "annotations.json"


def samples_table_path(study):
    """Sample table of a study: samples_annotated.tsv if the user annotated it, otherwise samples.tsv."""
    study = Path(study)
    return study / ANNOTATED_TABLE if (study / ANNOTATED_TABLE).is_file() else study / "samples.tsv"


def select_samples(samples, condition, ref_level, test_level, wanted=""):
    """Samples kept for the comparison (checks the column, the levels and at least two samples per level)."""
    if not (condition and ref_level and test_level):
        raise UpstreamError("give condition=<column of samples.tsv> reference_level=<level> test_level=<level>")
    any_row = next(iter(samples.values()))
    if condition not in any_row:
        raise UpstreamError(f"column '{condition}' is not in samples.tsv; columns: {', '.join(any_row)}")
    levels = sorted({r[condition] for r in samples.values()})
    for lv in (ref_level, test_level):
        if lv not in levels:
            raise UpstreamError(f"level '{lv}' is not in column '{condition}'; levels: {', '.join(levels)}")
    if ref_level == test_level:
        raise UpstreamError("reference_level and test_level must differ")

    if isinstance(wanted, (list, tuple)):
        wanted = [str(s) for s in wanted]
    else:
        wanted = [s.strip() for s in str(wanted or "").split(",") if s.strip()]
    if wanted:
        unknown = [s for s in wanted if s not in samples]
        if unknown:
            raise UpstreamError(f"unknown sample(s): {', '.join(unknown)}")
        chosen = [s for s in samples if s in wanted]
        off = [s for s in chosen if samples[s][condition] not in (ref_level, test_level)]
        if off:
            raise UpstreamError(f"sample(s) outside the two chosen levels: {', '.join(off)}")
    else:
        chosen = [s for s, r in samples.items() if r[condition] in (ref_level, test_level)]
    for lv in (ref_level, test_level):
        if sum(samples[s][condition] == lv for s in chosen) < 2:
            raise UpstreamError(f"fewer than 2 samples for level '{lv}'")

    return chosen


CONFIG_FILE = "upstream_config.json"
TEXT_KEYS = ("study", "condition", "reference_level", "test_level", "ref_dir", "gencode", "index", "strand")


def write_config(workdir, cfg):
    """Write the workflow configuration to <workdir>/upstream_config.json and return the Snakemake arguments. A JSON file keeps text values as typed, which --config does not."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / CONFIG_FILE
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ["--configfile", str(path)]


def _text_config(config):
    """Text values of the configuration, converted back to text if Snakemake interpreted them."""
    out = dict(config)
    for k in TEXT_KEYS:
        v = out.get(k)
        if isinstance(v, (dict, list)):
            raise UpstreamError(f"{k}: value misread by --config ({v!r}); use "
                                "omicchain_upstream.py run, or a --configfile")
        if v is not None and not isinstance(v, str):
            out[k] = str(v)
    return out


def load_plan(config):
    """Build the full plan from the Snakemake configuration; explicit errors when something is missing."""
    config = _text_config(config)
    study = Path(config.get("study", "")).expanduser().resolve()
    if not (study / "fetch_manifest.json").is_file():
        raise UpstreamError(f"study not found: {study} (folder made by omicchain_fetch.py, "
                            "with fetch_manifest.json)")
    manifest = json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8"))
    samples_table = samples_table_path(study)
    samples = {r["sample"]: r for r in read_tsv(samples_table)}
    ena = {r["run_accession"]: r for r in read_tsv(study / "ena_runs.tsv")}

    condition = config.get("condition")
    ref_level, test_level = config.get("reference_level"), config.get("test_level")
    chosen = select_samples(samples, condition, ref_level, test_level, config.get("samples", ""))

    verified = {f["path"]: f for f in manifest.get("files", []) if f.get("md5_verified")}
    runs = {}
    missing = []
    for s in chosen:
        for run in [r for r in samples[s]["runs"].split(";") if r]:
            files = sorted((f for f in verified.values() if f["run"] == run), key=lambda f: (f.get("mate", 0), f["path"]))
            if not files:
                missing.append(f"{s}/{run}")
                continue
            for f in files:
                if not (study / f["path"]).is_file():
                    missing.append(f"{s}/{f['path']}")
            runs[run] = {"sample": s, "fastq": [f["path"] for f in files], "md5": [f["md5"] for f in files],
                         "md5_source": files[0].get("md5_source", "ENA"), "sha256": [f.get("sha256") for f in files],
                         "paired": len(files) == 2,
                         "ena_read_count": int(ena[run]["read_count"]) if ena.get(run, {}).get("read_count", "").isdigit() else None}
    if missing:
        raise UpstreamError("FASTQ file(s) missing or not verified (run omicchain_fetch.py again): " + ", ".join(missing[:10]))
    bad = onm.check_ids(sorted(runs), "run") + onm.check_ids(chosen, "sample")
    if bad:
        raise UpstreamError("name(s) that cannot be used as file names: " + "; ".join(bad[:8]))
    layouts = {r["paired"] for r in runs.values()}
    if len(layouts) > 1:
        raise UpstreamError("mix of single-end and paired-end runs: not supported")

    gencode = str(config.get("gencode", "48"))
    index_type = config.get("index", "gapped")
    ref_dir = Path(config.get("ref_dir") or oref.default_ref_dir()).expanduser().resolve()
    paths = oref.ref_paths(ref_dir, gencode)
    ref_manifest = oref.load_manifest(paths["manifest"])
    if not ref_manifest or index_type not in ref_manifest.get("indexes", {}):
        raise UpstreamError(f"reference GENCODE v{gencode} / {index_type} index not found in {ref_dir}: "
                            f"python omicchain_reference.py prepare --gencode {gencode} --index {index_type}")
    strand = str(config.get("strand", "auto"))
    if strand not in ("auto", "0", "1", "2"):
        raise UpstreamError("strand must be auto, 0, 1 or 2")

    return {
        "study": study, "accession": manifest["provenance"]["input"], "samples_table": samples_table,
        "condition": condition, "reference_level": ref_level, "test_level": test_level,
        "samples": chosen, "sample_rows": {s: samples[s] for s in chosen}, "runs": runs,
        "paired": next(iter(layouts)),
        "gencode": gencode, "index_type": index_type, "ref_root": paths["root"], "ref_manifest": ref_manifest,
        "index_base": paths["root"] / ref_manifest["indexes"][index_type]["path"], "gtf": paths["gtf"],
        "strand": strand, "outdir": study / "upstream" / f"{index_type}_gencode{gencode}",
        "align_threads": int(config.get("align_threads", 8)), "count_threads": int(config.get("count_threads", 4)),
        "mem_gb": 20 if index_type == "full" else 9,
    }


def selection_key(plan):
    """What defines the selection (Snakemake parameter: a change re-runs the merge)."""
    return f"{plan['condition']}|{plan['reference_level']}|{plan['test_level']}|{','.join(plan['samples'])}"


def verify_reference(plan, out):
    """Recompute the fingerprints of the GTF, the genome and the index used."""
    man = plan["ref_manifest"]
    entry = man["indexes"][plan["index_type"]]
    problems = []
    for key in ("genome", "gtf"):
        p = plan["ref_root"] / man["sources"][key]["file"]
        if not p.is_file() or sha256_file(p) != man["sources"][key]["sha256"]:
            problems.append(man["sources"][key]["file"])
    files, fingerprint = oref.index_digest(plan["index_base"])
    if fingerprint != entry["fingerprint"]:
        problems.append(f"index {plan['index_type']}")
    if problems:
        raise UpstreamError("reference changed since it was prepared: " + ", ".join(problems)
                            + " (python omicchain_reference.py verify)")
    result = {"gencode_release": plan["gencode"], "genome_sha256": man["sources"]["genome"]["sha256"],
              "gtf_sha256": man["sources"]["gtf"]["sha256"], "index_type": plan["index_type"],
              "index_fingerprint": fingerprint, "index_rsubread_version": entry.get("rsubread_version"),
              "index_origin": entry["origin"]["method"]}
    Path(out).write_text(json.dumps(result, indent=2), encoding="utf-8")


FIXED_SAMPLE_COLUMNS = {"sample", "title", "source_name", "organism", "library_layout",
                        "library_strategy", "runs", "read_count", "fastq_bytes"}
RESERVED_METADATA_COLUMNS = {"sample", "group", "condition", "title"}


def candidate_covariates(plan):
    """Columns that vary between the selected samples, as [(valid R name, column of the sample table)]."""
    rows = [plan["sample_rows"][s] for s in plan["samples"]]
    out = []
    for col in rows[0]:
        if col in FIXED_SAMPLE_COLUMNS or col == plan["condition"]:
            continue
        values = [r.get(col, "") for r in rows]
        if len(set(values)) < 2 or any(v == "" for v in values):
            continue
        name = re.sub(r"[^0-9A-Za-z_.]", "_", col)
        if not re.match(r"^[A-Za-z]", name):
            name = "x_" + name
        if name in RESERVED_METADATA_COLUMNS or name in [n for n, _ in out]:
            name = "cov_" + name
        out.append((name, col))
    return out


def user_annotations(study):
    """Columns added by the user and keyword rules (None if there are none)."""
    p = Path(study) / ANNOTATIONS_JSON
    if not (Path(study) / ANNOTATED_TABLE).is_file() or not p.is_file():
        return None
    info = json.loads(p.read_text(encoding="utf-8"))
    out = {"user_columns": info.get("user_columns", []), "rules": info.get("rules", []),
           "imported_sheets": info.get("imported_sheets", []), "edited_by_hand": bool(info.get("edited_by_hand")),
           "annotations_json_sha256": sha256_file(p)}
    return out


def gene_names(gtf):
    names = {}
    opener = gzip.open if str(gtf).endswith(".gz") else open
    with opener(gtf, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split("\t", 9)
            if len(parts) < 9 or parts[2] != "gene":
                continue
            gid = re.search(r'gene_id "([^"]+)"', parts[8])
            gname = re.search(r'gene_name "([^"]+)"', parts[8])
            if gid:
                names[gid.group(1)] = gname.group(1) if gname else gid.group(1)
    return names


def read_counts(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        g, c = line.split("\t")
        out[g] = int(c)
    return out


def write_tsv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def merge(plan, counts_dir, align_dir, strand_file, reference_check, outdir, rsubread_version=None):
    outdir = Path(outdir)
    samples = plan["samples"]
    runs = plan["runs"]

    per_sample, gene_ids = {}, None
    for run, info in sorted(runs.items()):
        c = read_counts(Path(counts_dir) / f"{run}.tsv")
        if gene_ids is None:
            gene_ids = sorted(c)
        elif sorted(c) != gene_ids:
            raise UpstreamError(f"{run}: gene list different from the other runs (another GTF?)")
        acc = per_sample.setdefault(info["sample"], dict.fromkeys(gene_ids, 0))
        for g, v in c.items():
            acc[g] += v

    names = gene_names(plan["gtf"])
    write_tsv(outdir / "counts_by_gene_id.tsv", ["gene_id", "gene_name"] + samples,
              [[g, names.get(g, g)] + [per_sample[s][g] for s in samples] for g in gene_ids])

    by_name = {}
    for g in gene_ids:
        acc = by_name.setdefault(names.get(g, g), [0] * len(samples))
        for i, s in enumerate(samples):
            acc[i] += per_sample[s][g]
    collapsed = sum(1 for g in gene_ids) - len(by_name)
    write_tsv(outdir / "expression_input.tsv", ["gene"] + samples,
              [[n] + by_name[n] for n in sorted(by_name)])

    cond = plan["condition"]
    covariates = candidate_covariates(plan)
    group_of = {plan["reference_level"]: "control", plan["test_level"]: "treatment"}
    write_tsv(outdir / "metadata.tsv", ["sample", "group", "condition", "title"] + [c for c, _ in covariates],
              [[s, group_of[plan["sample_rows"][s][cond]], plan["sample_rows"][s][cond],
                plan["sample_rows"][s].get("title", "")] + [plan["sample_rows"][s][src] for _, src in covariates]
               for s in samples])

    qc_rows, warnings = [], []
    strand_info = read_kv(strand_file)
    strand = int(strand_info["strand"])
    for run, info in sorted(runs.items()):
        a = read_kv(Path(align_dir) / f"{run}.tsv")
        st = read_kv(Path(counts_dir) / f"{run}.tsv.stats")
        total, mapped = int(a["total"]), int(a["mapped"])
        assigned = int(st.get("Assigned", 0))
        ena = info["ena_read_count"]
        match = "n/a" if ena is None else ("yes" if ena == total else "NO")
        if match == "NO":
            warnings.append(f"{run}: the FASTQ files hold {total} fragments (read by the aligner), "
                            f"the ENA metadata announce {ena} ({100 * total / ena:.0f} %)")
        if total and mapped / total < 0.7:
            warnings.append(f"{run}: low alignment rate ({100 * mapped / total:.1f} %)")
        qc_rows.append([info["sample"], run, total, ena if ena is not None else "", match, mapped,
                        f"{100 * mapped / total:.2f}" if total else "", assigned,
                        f"{100 * assigned / total:.2f}" if total else ""])
        rsubread_version = rsubread_version or a.get("rsubread_version")
    write_tsv(outdir / "qc_summary.tsv", ["sample", "run", "fragments", "ena_read_count", "fragments_match_ena",
                                          "mapped", "mapped_pct", "assigned", "assigned_pct"], qc_rows)

    ref = json.loads(Path(reference_check).read_text(encoding="utf-8"))
    provenance = {
        "schema": "omicchain.upstream/1",
        "tool": {"name": "omicchain_upstream", "version": VERSION, "code_sha256": code_hashes()},
        "study": {"accession": plan["accession"],
                  "fetch_manifest_sha256": sha256_file(plan["study"] / "fetch_manifest.json"),
                  "samples_tsv_sha256": sha256_file(plan["study"] / "samples.tsv"),
                  "samples_table": plan["samples_table"].name,
                  "samples_table_sha256": sha256_file(plan["samples_table"]),
                  "user_annotations": user_annotations(plan["study"])},
        "design": {"condition_column": cond, "reference_level": plan["reference_level"],
                   "test_level": plan["test_level"], "samples": samples,
                   "group_coding": {"control": plan["reference_level"], "treatment": plan["test_level"]},
                   "candidate_covariates": [c for c, _ in covariates]},
        "inputs": [{"sample": runs[r]["sample"], "run": r, "fastq": [Path(p).name for p in runs[r]["fastq"]],
                    "md5": runs[r]["md5"], "md5_source": runs[r]["md5_source"],
                    **({"sha256": runs[r]["sha256"]} if all(runs[r]["sha256"]) else {}),
                    "md5_rechecked_before_alignment": True}
                   for r in sorted(runs)],
        "reference": ref,
        "alignment": {"aligner": "Rsubread::align", "rsubread_version": rsubread_version, "type": "rna",
                      "other_parameters": "Rsubread defaults", "paired_end": plan["paired"]},
        "counting": {"counter": "Rsubread::featureCounts", "feature_type": "exon", "attribute": "gene_id",
                     "strand_specific": strand, "strand_mode": "detected" if plan["strand"] == "auto" else "set",
                     "strand_detection": {k: int(v) for k, v in strand_info.items() if k.startswith("assigned_")},
                     "runs_per_sample": "summed", "gene_id_to_symbol": "sum of gene_ids sharing a gene_name",
                     "gene_ids": len(gene_ids), "gene_symbols": len(by_name), "gene_ids_collapsed": collapsed},
        "outputs": {name: sha256_file(outdir / name)
                    for name in ("counts_by_gene_id.tsv", "expression_input.tsv", "metadata.tsv", "qc_summary.tsv")},
        "warnings": warnings,
    }
    (outdir / "upstream_provenance.json").write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
                                                     encoding="utf-8")
    return provenance


HERE = Path(__file__).resolve().parent


def total_ram_gb():
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(m)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1024 ** 3
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                return int(line.split()[1]) / 1024 ** 2
    except Exception:
        pass
    return None


def resources(index_type, cores=None, ram_gb=None):
    """Return (cores for Snakemake, memory in GB, threads per alignment)."""
    cores = cores or os.cpu_count() or 4
    ram = ram_gb or total_ram_gb() or 16
    need = 20 if index_type == "full" else 9
    mem = max(need, int(ram) - 4)
    parallel = max(1, min(mem // need, cores // 8))
    align_threads = max(2, min(16, cores // parallel))
    return cores, mem, align_threads


def run(argv=None):
    """Download the FASTQ files of the selected samples, then align and count; each step resumes where it stopped."""
    import argparse
    import subprocess
    import sys
    ap = argparse.ArgumentParser(description="OmicChain: download, alignment and counting of a study")
    ap.add_argument("run")
    ap.add_argument("--study", required=True)
    ap.add_argument("--condition", required=True)
    ap.add_argument("--reference-level", required=True)
    ap.add_argument("--test-level", required=True)
    ap.add_argument("--samples", default="")
    ap.add_argument("--index", default="gapped", choices=["gapped", "full"])
    ap.add_argument("--gencode", default="48")
    ap.add_argument("--ref-dir")
    ap.add_argument("--cores", type=int)
    a = ap.parse_args(argv)

    study = Path(a.study).resolve()
    samples = {r["sample"]: r for r in read_tsv(samples_table_path(study))}
    try:
        chosen = select_samples(samples, a.condition, a.reference_level, a.test_level, a.samples)
    except UpstreamError as e:
        print(f"ERROR: {e}", flush=True)
        return 1
    manifest = json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8"))
    accession = manifest["provenance"]["input"]

    if manifest.get("tool") == "omicchain_local":
        print(f"[1/2] FASTQ files provided by the user ({len(chosen)} samples): nothing to download "
              "(checksums are checked again before the alignment)", flush=True)
    else:
        print(f"[1/2] Downloading the FASTQ files ({len(chosen)} samples)", flush=True)
        code = subprocess.call([sys.executable, str(HERE / "omicchain_fetch.py"), accession, "--out", str(study),
                                "--samples", ",".join(chosen)])
        if code != 0:
            print("ERROR: download incomplete (running again resumes where it stopped)", flush=True)
            return code

    cores, mem, align_threads = resources(a.index, a.cores)
    workdir = study / "upstream"
    workdir.mkdir(parents=True, exist_ok=True)
    cfg = {"study": str(study), "condition": a.condition, "reference_level": a.reference_level,
           "test_level": a.test_level, "samples": chosen, "index": a.index, "gencode": str(a.gencode),
           "align_threads": align_threads}
    if a.ref_dir:
        cfg["ref_dir"] = str(Path(a.ref_dir).resolve())
    config = write_config(workdir, cfg)
    smk = [sys.executable, "-m", "snakemake", "-s", str(HERE / "upstream.smk"), "--directory", str(workdir)]
    print(f"[2/2] Alignment and counting ({cores} cores, {mem} GB, {align_threads} threads per alignment)",
          flush=True)
    subprocess.call(smk + ["--unlock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = subprocess.call(smk + ["-j", str(cores), "--resources", f"mem_gb={mem}", "--rerun-incomplete", *config])
    print("Done." if code == 0 else "ERROR: alignment / counting failed (see above)", flush=True)
    return code


if __name__ == "__main__":
    import sys
    sys.exit(run(sys.argv[1:]) if sys.argv[1:2] == ["run"] else 2)
