"""User FASTQ files as a data source: scan a folder, group mates and lanes per sample, register the files with their md5 and SHA-256 (no copy)."""

import argparse
import csv
import datetime as _dt
import hashlib
import json
import re
import sys
from pathlib import Path

VERSION = "0.1"
FASTQ_EXT = re.compile(r"\.(fastq|fq)(\.gz)?$", re.I)
PATTERNS = [
    re.compile(r"^(?P<sample>.+?)(?:_S\d+)?(?P<lane>_L\d{3})?_R(?P<read>[12])(?:_\d{3})?$", re.I),
    re.compile(r"^(?P<sample>.+?)[._-](?:R)?(?P<read>[12])$", re.I),
]


def _stem(name):
    return FASTQ_EXT.sub("", name)


def parse_name(name):
    """Return (sample, unit, read 1 | 2 | None)."""
    stem = _stem(name)
    for pat in PATTERNS:
        m = pat.match(stem)
        if m:
            sample = m.group("sample")
            lane = (m.groupdict().get("lane") or "").lstrip("_")
            return sample, (f"{sample}_{lane}" if lane else sample), int(m.group("read"))
    return stem, stem, None


def scan(folder):
    """Return {"samples": {sample: {"layout", "units": {unit: {1: Path, 2: Path} | {None: Path}}}},
    "problems": [...], "ignored": [...]}."""
    folder = Path(folder)
    files = sorted(p for p in folder.rglob("*") if p.is_file() and FASTQ_EXT.search(p.name))
    ignored = sorted(str(p.relative_to(folder)) for p in folder.rglob("*")
                     if p.is_file() and not FASTQ_EXT.search(p.name) and not p.name.startswith("."))
    names, problems = {}, []
    for p in files:
        if p.name in names:
            problems.append(f"same file name twice: {names[p.name].relative_to(folder)} and {p.relative_to(folder)}")
        names[p.name] = p
    samples = {}
    for p in files:
        sample, unit, read = parse_name(p.name)
        u = samples.setdefault(sample, {"units": {}})["units"].setdefault(unit, {})
        if read in u:
            problems.append(f"{p.name}: another file is already read {read or 'single'} of {unit}")
        u[read] = p
    import omicchain_names as onm
    for why in onm.check_ids(sorted(samples), "sample name") + onm.check_ids(
            sorted(u for s in samples.values() for u in s["units"] if u not in samples), "file group"):
        problems.append(why + " — rename the FASTQ file(s)")
    for sample, s in samples.items():
        kinds = set()
        for unit, u in s["units"].items():
            if set(u) == {1, 2}:
                kinds.add("PAIRED")
            elif set(u) == {None}:
                kinds.add("SINGLE")
            elif set(u) in ({1}, {2}):
                kinds.add("SINGLE")
                problems.append(f"{unit}: read {next(iter(u))} without its mate — treated as single-end")
            else:
                kinds.add("MIXED")
                problems.append(f"{unit}: both a single file and paired files")
        s["layout"] = kinds.pop() if len(kinds) == 1 else "MIXED"
        if s["layout"] == "MIXED":
            problems.append(f"{sample}: some files are paired, others single-end")
    return {"samples": samples, "problems": problems, "ignored": ignored}


def _hashes(path, chunk=8 << 20):
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            md5.update(block); sha.update(block)
    return md5.hexdigest(), sha.hexdigest()


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "local"


def register(folder, study, name=None, log=print):
    folder = Path(folder).resolve()
    study = Path(study)
    study.mkdir(parents=True, exist_ok=True)
    found = scan(folder)
    if not found["samples"]:
        raise SystemExit(f"ERROR: no FASTQ file (.fastq, .fq, .fastq.gz, .fq.gz) in {folder}")
    blocking = [p for p in found["problems"] if "same file name" in p or "already read" in p]
    if blocking:
        raise SystemExit("ERROR: " + "; ".join(blocking))
    previous = {}
    if (study / "fetch_manifest.json").is_file():
        for f in json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8")).get("files", []):
            previous[f["path"]] = f
    name = name or f"LOCAL_{safe_name(folder.name)}"
    todo = [(s, unit, read, p) for s, info in sorted(found["samples"].items())
            for unit, u in sorted(info["units"].items()) for read, p in sorted(u.items(), key=lambda x: x[0] or 0)]
    total = sum(p.stat().st_size for *_, p in todo)
    done_bytes, files = 0, []
    log(f"{len(found['samples'])} sample(s), {len(todo)} file(s), {total / 1024 ** 3:.1f} GB to register")
    for i, (sample, unit, read, p) in enumerate(todo, 1):
        st = p.stat()
        old = previous.get(str(p))
        if old and old.get("bytes") == st.st_size and old.get("mtime") == int(st.st_mtime):
            md5, sha = old["md5"], old["sha256"]
        else:
            md5, sha = _hashes(p)
        done_bytes += st.st_size
        files.append({"sample": sample, "run": unit, "mate": read or 0, "path": str(p), "bytes": st.st_size,
                      "mtime": int(st.st_mtime), "md5": md5, "sha256": sha, "md5_source": "local (computed by OmicChain)",
                      "md5_verified": True})
        log(f"  {i}/{len(todo)}  {p.name}  ({100 * done_bytes / max(total, 1):.0f}%)")

    by_sample = {}
    for f in files:
        by_sample.setdefault(f["sample"], []).append(f)
    with open(study / "samples.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["sample", "title", "source_name", "organism", "library_layout", "library_strategy", "runs",
                    "read_count", "fastq_bytes"])
        for s in sorted(by_sample):
            units = sorted({f["run"] for f in by_sample[s]})
            w.writerow([s, s, "", "", found["samples"][s]["layout"], "RNA-Seq", ";".join(units), "",
                        sum(f["bytes"] for f in by_sample[s])])
    with open(study / "ena_runs.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["run_accession", "library_layout", "read_count", "fastq_bytes"])
        for unit in sorted({f["run"] for f in files}):
            fs = [f for f in files if f["run"] == unit]
            w.writerow([unit, "PAIRED" if len(fs) == 2 else "SINGLE", "", ";".join(str(f["bytes"]) for f in fs)])
    manifest = {"tool": "omicchain_local", "version": VERSION,
                "date": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "provenance": {"input": name, "source": "user-provided FASTQ files", "folder": str(folder)},
                "warnings": found["problems"], "files": files}
    (study / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Done: local study '{name}' in {study}")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="OmicChain: user FASTQ files")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("folder")
    r = sub.add_parser("register"); r.add_argument("folder"); r.add_argument("--study", required=True); r.add_argument("--name")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        found = scan(a.folder)
        for name, info in sorted(found["samples"].items()):
            print(f"{name:<30} {info['layout']:<7} {len(info['units'])} unit(s)")
        for p in found["problems"]:
            print("WARNING:", p)
        return 0
    register(a.folder, a.study, a.name, log=lambda m: print(m, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
