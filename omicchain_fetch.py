"""From a GEO / SRA / ENA accession to verified FASTQ files: study metadata (GEO, ENA), sample table, resumable downloads checked against the ENA md5."""

import argparse
import csv
import datetime as _dt
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

import omicchain_names as onm

VERSION = "0.1"

GEO_SOFT_URL = os.environ.get(
    "OMICCHAIN_GEO_SOFT_URL",
    "https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{acc}/soft/{acc}_family.soft.gz")
ENA_API = os.environ.get("OMICCHAIN_ENA_API", "https://www.ebi.ac.uk/ena/portal/api/filereport")
ENA_PREFIX = os.environ.get("OMICCHAIN_ENA_PREFIX", "https://")

ENA_FIELDS = ["run_accession", "experiment_accession", "sample_accession", "secondary_sample_accession",
              "study_accession", "secondary_study_accession", "sample_alias", "sample_title",
              "experiment_title", "library_layout", "library_strategy", "library_source",
              "instrument_model", "scientific_name", "read_count", "base_count",
              "fastq_ftp", "fastq_md5", "fastq_bytes"]

USER_AGENT = f"OmicChain-fetch/{VERSION}"
_print_lock = threading.Lock()


def say(*parts):
    with _print_lock:
        print(*parts, flush=True)


class FetchError(Exception):
    pass


def http_get(url, *, params=None, attempts=5, timeout=(30, 120)):
    last = None
    for i in range(attempts):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
            if r.status_code == 404:
                raise FetchError(f"not found (404): {r.url}")
            if r.status_code == 400:
                raise FetchError(f"request refused (400): {r.url}\n    answer: {r.text.strip()[:400]}")
            r.raise_for_status()
            return r
        except FetchError:
            raise
        except requests.RequestException as e:
            last = e
            time.sleep(min(30, 3 * (i + 1)))
    raise FetchError(f"request failed {url}: {last}")


def geo_stub(acc):
    """GSE52778 -> GSE52nnn; GSE123 -> GSEnnn (layout of the GEO FTP site)."""
    digits = acc[3:]
    return "GSE" + (digits[:-3] if len(digits) > 3 else "") + "nnn"


def parse_soft(text):
    series = {"relations": [], "sample_ids": [], "title": "", "type": []}
    samples, current = [], None
    for line in text.splitlines():
        if line.startswith("^SAMPLE"):
            current = {"sample": line.split("=", 1)[1].strip(), "title": "", "source_name": "",
                       "organism": "", "characteristics": [], "relations": [], "library_strategy": ""}
            samples.append(current)
            continue
        if line.startswith("^"):
            current = None if not line.startswith("^SERIES") else current
            continue
        if not line.startswith("!") or "=" not in line:
            continue
        key, value = (s.strip() for s in line[1:].split("=", 1))
        if current is None:
            if key == "Series_title":
                series["title"] = value
            elif key == "Series_relation":
                series["relations"].append(value)
            elif key == "Series_sample_id":
                series["sample_ids"].append(value)
            elif key == "Series_type":
                series["type"].append(value)
        else:
            if key == "Sample_title":
                current["title"] = value
            elif key == "Sample_source_name_ch1":
                current["source_name"] = value
            elif key == "Sample_organism_ch1":
                current["organism"] = value
            elif key.startswith("Sample_characteristics_ch"):
                current["characteristics"].append(value)
            elif key == "Sample_relation":
                current["relations"].append(value)
            elif key == "Sample_library_strategy":
                current["library_strategy"] = value
    for s in samples:
        s["srx"] = re.findall(r"\b([SED]RX\d+)\b", " ".join(s["relations"]))
    return series, samples


def study_from_series(series):
    """SRA / BioProject accessions cited by a GEO series."""
    rel = " ".join(series["relations"])
    for pattern in (r"\b([SED]RP\d+)\b", r"\b(PRJ[NED][A-Z]?\d+)\b"):
        m = re.findall(pattern, rel)
        if m:
            return sorted(set(m))
    return []


def characteristics_columns(samples):
    """Turn GEO characteristics ('cell line: N61311') into columns (cell_line)."""
    rows = []
    for s in samples:
        cols, seen = {}, {}
        for i, c in enumerate(s["characteristics"], 1):
            if ":" in c:
                k, v = c.split(":", 1)
                k = re.sub(r"[^0-9a-z]+", "_", k.strip().lower()).strip("_") or f"characteristics_{i}"
            else:
                k, v = f"characteristics_{i}", c
            seen[k] = seen.get(k, 0) + 1
            if seen[k] > 1:
                k = f"{k}_{seen[k]}"
            cols[k] = v.strip()
        rows.append(cols)
    return rows


def ena_runs(accession):
    params = {"accession": accession, "result": "read_run", "fields": ",".join(ENA_FIELDS),
              "format": "tsv", "limit": 0}
    r = http_get(ENA_API, params=params)
    rows = list(csv.DictReader(io.StringIO(r.text), delimiter="\t"))
    return rows, r.url, hashlib.md5(r.content).hexdigest()


def run_files(run):
    """FASTQ files of a run: _1 / _2 for paired-end libraries, the single file otherwise."""
    urls = [u for u in run.get("fastq_ftp", "").split(";") if u]
    md5s = run.get("fastq_md5", "").split(";")
    sizes = run.get("fastq_bytes", "").split(";")
    files = [{"url": ENA_PREFIX + u, "name": u.rsplit("/", 1)[-1],
              "md5": md5s[i] if i < len(md5s) else "", "bytes": int(sizes[i]) if i < len(sizes) and sizes[i] else None}
             for i, u in enumerate(urls)]
    bad = onm.check_ids([f["name"] for f in files], "file")
    if bad:
        raise FetchError("unexpected file name in the ENA answer: " + "; ".join(bad[:3]))
    if len(files) > 1:
        paired = [f for f in files if re.search(r"_[12]\.f(ast)?q\.gz$", f["name"])]
        if paired:
            files = paired
    return files


def md5_file(path, chunk=8 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def download(url, dest, *, md5, size, attempts=30, label=None):
    """Download url to dest, resuming a partial file; the file is accepted only if its size and md5 are the announced ones."""
    dest = Path(dest)
    label = label or dest.name
    part = dest.with_name(dest.name + ".part")
    if dest.exists():
        return dest
    step_report = 0.25
    for attempt in range(1, attempts + 1):
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have == size:
            break
        if size is not None and have > size:
            part.unlink(); have = 0
        headers = {"User-Agent": USER_AGENT}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(60, 300)) as r:
                if r.status_code == 416:
                    break
                r.raise_for_status()
                mode = "ab" if (have and r.status_code == 206) else "wb"
                if mode == "wb":
                    have = 0
                with open(part, mode) as f:
                    for block in r.iter_content(chunk_size=1 << 18):
                        f.write(block)
                        have += len(block)
                        if size and have / size >= step_report:
                            say(f"    {label} : {int(100 * have / size)} %")
                            step_report += 0.25
            if size is None or have >= size:
                break
            raise requests.ConnectionError("connection closed before the end")
        except requests.RequestException as e:
            got = part.stat().st_size if part.exists() else 0
            total = f" of {size // (1 << 20)} MB" if size else ""
            say(f"    {label}: connection interrupted ({got // (1 << 20)} MB received{total}), retry {attempt}/{attempts}")
            time.sleep(min(60, 5 * attempt))
    else:
        raise FetchError(f"{label}: download failed after {attempts} attempts "
                         "(running the command again resumes where it stopped)")

    got_size = part.stat().st_size
    if size is not None and got_size != size:
        raise FetchError(f"{label}: size {got_size} instead of {size}")
    got_md5 = md5_file(part)
    if md5 and got_md5.lower() != md5.lower():
        bad = part.with_name(dest.name + ".bad")
        part.replace(bad)
        raise FetchError(f"{label}: md5 {got_md5} instead of {md5} (file set aside: {bad.name})")
    part.replace(dest)
    return dest


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def resolve(accession):
    """Return (series, samples, ena_rows, provenance)."""
    acc = accession.strip().upper()
    if onm.accession_problem(acc):
        raise FetchError(f"accession not recognised: {acc[:40]} (expected: GSE..., SRP..., ERP..., PRJNA..., PRJEB...)")
    provenance = {"input": acc}
    series, samples = None, None
    if acc.startswith("GSE"):
        url = GEO_SOFT_URL.format(stub=geo_stub(acc), acc=acc)
        r = http_get(url)
        provenance["geo_soft"] = {"url": url, "md5": hashlib.md5(r.content).hexdigest()}
        text = gzip.decompress(r.content).decode("utf-8", errors="replace")
        series, samples = parse_soft(text)
        if not samples:
            raise FetchError(f"{acc}: no sample in the GEO SOFT file")
        studies = [st for st in study_from_series(series) if not onm.accession_problem(st)]
        if not studies:
            raise FetchError(f"{acc}: no SRA project cited by the GEO series (data not deposited in the SRA?)")
    elif re.match(r"^([SED]RP\d+|PRJ[NED][A-Z]?\d+)$", acc):
        studies = [acc]
    else:
        raise FetchError(f"accession not recognised: {acc} (expected: GSE..., SRP..., ERP..., PRJNA..., PRJEB...)")

    rows, queries = [], []
    for st in studies:
        got, url, md5 = ena_runs(st)
        rows += got
        queries.append({"accession": st, "url": url, "response_md5": md5, "runs": len(got)})
    provenance["ena"] = queries
    rows = list({r["run_accession"]: r for r in rows}.values())
    if not rows:
        raise FetchError(f"no run found at the ENA for {', '.join(studies)}")
    odd = [r["run_accession"] for r in rows if not onm.RUN_ACCESSION.match(r.get("run_accession", ""))]
    if odd:
        raise FetchError("unexpected run identifier(s) in the ENA answer: " + ", ".join(map(repr, odd[:5])))
    return series, samples, rows, provenance


def join_runs(samples, rows):
    """Match each run with a GEO sample: by SRX experiment, then ENA sample alias, then the GSM in the title."""
    by_srx = {x: s["sample"] for s in samples for x in s["srx"]}
    gsms = {s["sample"] for s in samples}
    joined, unmatched = {s["sample"]: [] for s in samples}, []
    for r in rows:
        gsm = by_srx.get(r.get("experiment_accession", ""))
        if not gsm and r.get("sample_alias") in gsms:
            gsm = r["sample_alias"]
        if not gsm:
            m = re.search(r"\b(GSM\d+)\b", " ".join([r.get("experiment_title", ""), r.get("sample_title", "")]))
            gsm = m.group(1) if m and m.group(1) in gsms else None
        (joined[gsm].append(r) if gsm else unmatched.append(r))
    return joined, unmatched


def build_table(series, samples, rows):
    """Rows of the draft samples.tsv, and warnings."""
    warnings = []
    if samples is None:
        groups = {}
        for r in rows:
            groups.setdefault(r.get("sample_accession") or r["run_accession"], []).append(r)
        table = [{"sample": k, "title": v[0].get("sample_title", ""), "source_name": "",
                  "organism": v[0].get("scientific_name", ""), "_runs": v} for k, v in groups.items()]
        chars = [{} for _ in table]
    else:
        joined, unmatched = join_runs(samples, rows)
        if unmatched:
            warnings.append(f"{len(unmatched)} run(s) of the SRA project without a sample in this GEO series, ignored: "
                            + ", ".join(r["run_accession"] for r in unmatched[:10]) + (" ..." if len(unmatched) > 10 else ""))
        table = [{"sample": s["sample"], "title": s["title"], "source_name": s["source_name"],
                  "organism": s["organism"], "_runs": joined[s["sample"]]} for s in samples]
        chars = characteristics_columns(samples)
        missing = [t["sample"] for t in table if not t["_runs"]]
        if missing:
            warnings.append(f"{len(missing)} GEO sample(s) without an SRA run: " + ", ".join(missing))
        strategies = {s["library_strategy"] for s in samples if s["library_strategy"]}
        if strategies - {"RNA-Seq"}:
            warnings.append("library strategy other than RNA-Seq: " + ", ".join(sorted(strategies)))
        if any("SuperSeries of" in rel for rel in series["relations"]):
            warnings.append("this series is a SuperSeries (it groups several sub-series): check the samples")

    for t, c in zip(table, chars):
        runs = t["_runs"]
        t["characteristics"] = c
        t["runs"] = ";".join(r["run_accession"] for r in runs)
        t["library_layout"] = ";".join(sorted({r.get("library_layout", "") for r in runs}))
        t["library_strategy"] = ";".join(sorted({r.get("library_strategy", "") for r in runs}))
        t["read_count"] = sum(int(r["read_count"]) for r in runs if r.get("read_count", "").isdigit())
        t["files"] = [dict(f, run=r["run_accession"]) for r in runs for f in run_files(r)]
        t["fastq_bytes"] = sum(f["bytes"] or 0 for f in t["files"])
        no_fastq = [r["run_accession"] for r in runs if not run_files(r)]
        if no_fastq:
            warnings.append(f"{t['sample']}: no FASTQ available at the ENA for " + ", ".join(no_fastq))
    layouts = {t["library_layout"] for t in table if t["library_layout"]}
    if len(layouts) > 1:
        warnings.append("mix of single-end and paired-end libraries: " + ", ".join(sorted(layouts)))
    bad = onm.check_ids([t["sample"] for t in table], "sample")
    if bad:
        raise FetchError("sample identifier(s) that cannot be used: " + "; ".join(bad[:5]))
    organisms = {t["organism"] for t in table if t["organism"]}
    if organisms - {"Homo sapiens"}:
        warnings.append("organism other than human: " + ", ".join(sorted(organisms))
                        + " (the reference is human GENCODE)")
    return table, warnings


def write_samples_tsv(table, path):
    char_keys = []
    for t in table:
        for k in t["characteristics"]:
            if k not in char_keys:
                char_keys.append(k)
    cols = ["sample", "title", "source_name", "organism"] + char_keys + \
           ["library_layout", "library_strategy", "runs", "read_count", "fastq_bytes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(cols)
        for t in table:
            w.writerow([t["characteristics"].get(c, "") if c in char_keys else t.get(c, "") for c in cols])


def main(argv=None):
    ap = argparse.ArgumentParser(description="OmicChain: retrieval of a public RNA-seq study (GEO / SRA / ENA).")
    ap.add_argument("accession", help="GSE..., SRP..., ERP..., PRJNA..., PRJEB...")
    ap.add_argument("--out", help="output folder (default: study_<accession>)")
    ap.add_argument("--dry-run", action="store_true", help="metadata only, no FASTQ downloaded")
    ap.add_argument("--samples", help="samples to download (GSM...,GSM...)")
    ap.add_argument("--limit", type=int, help="download the first N samples only")
    ap.add_argument("--jobs", type=int, default=4, help="simultaneous downloads (default 4)")
    ap.add_argument("--force", action="store_true", help="skip the disk space check")
    a = ap.parse_args(argv)

    acc = a.accession.strip().upper()
    out = Path(a.out or f"study_{acc}")
    out.mkdir(parents=True, exist_ok=True)
    say(f"OmicChain fetch {VERSION} -- {acc}")

    try:
        series, samples, rows, provenance = resolve(acc)
    except FetchError as e:
        say(f"ERROR: {e}")
        return 1
    if series:
        say(f"  GEO series: {series['title']}")
        provenance["geo_series_title"] = series["title"]
    say(f"  SRA/ENA project(s): {', '.join(q['accession'] for q in provenance['ena'])} "
        f"({sum(q['runs'] for q in provenance['ena'])} run(s))")

    table, warnings = build_table(series, samples, rows)
    write_samples_tsv(table, out / "samples.tsv")
    with open(out / "ena_runs.tsv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ENA_FIELDS, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        w.writeheader(); w.writerows(rows)

    say(f"\n  {len(table)} sample(s):")
    for t in table:
        chars = "; ".join(f"{k}: {v}" for k, v in t["characteristics"].items())
        say(f"    {t['sample']:<12} {t['title'][:32]:<32} {t['library_layout']:<7} "
            f"{len(t['runs'].split(';')) if t['runs'] else 0} run(s) {human(t['fastq_bytes']):>9}   {chars[:70]}")
    for wmsg in warnings:
        say(f"  WARNING: {wmsg}")
    say(f"\n  Draft metadata: {out / 'samples.tsv'} (to be reviewed: condition column, etc.)")

    chosen = table
    if a.samples:
        wanted = [s.strip().upper() for s in a.samples.split(",") if s.strip()]
        unknown = set(wanted) - {t["sample"] for t in table}
        if unknown:
            say(f"ERROR: unknown sample(s): {', '.join(sorted(unknown))}")
            return 1
        chosen = [t for t in table if t["sample"] in wanted]
    if a.limit:
        chosen = chosen[:a.limit]
    files = [dict(f, sample=t["sample"]) for t in chosen for f in t["files"]]
    total = sum(f["bytes"] or 0 for f in files)

    manifest = {
        "tool": "omicchain_fetch", "version": VERSION,
        "date": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provenance": provenance,
        "samples": [{k: t[k] for k in ("sample", "title", "runs", "library_layout", "read_count")} for t in table],
        "warnings": warnings, "files": [],
    }

    def save_manifest():
        (out / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    if a.dry_run:
        say(f"\n  --dry-run: {len(files)} file(s), {human(total)} to download for {len(chosen)} sample(s).")
        save_manifest()
        return 0

    fq = out / "fastq"
    fq.mkdir(exist_ok=True)
    remaining = sum((f["bytes"] or 0) - ((fq / (f["name"] + ".part")).stat().st_size if (fq / (f["name"] + ".part")).exists() else 0)
                    for f in files if not (fq / f["name"]).exists())
    free = shutil.disk_usage(fq).free
    say(f"\n  Download: {len(files)} file(s), {human(total)} ({human(remaining)} left), "
        f"{human(free)} free, {a.jobs} at a time")
    if remaining > free * 0.95 and not a.force:
        say("ERROR: not enough disk space (--force to go ahead anyway)")
        return 1

    previous = {}
    if (out / "fetch_manifest.json").exists():
        try:
            for f in json.loads((out / "fetch_manifest.json").read_text(encoding="utf-8")).get("files", []):
                previous[f["path"]] = f
        except (ValueError, KeyError):
            pass

    def one(f):
        dest = fq / f["name"]
        rel = dest.relative_to(out).as_posix()
        old = previous.get(rel)
        if dest.exists() and old and old.get("md5_verified") and old.get("bytes") == dest.stat().st_size:
            return dict(old)
        if dest.exists():
            if (f["bytes"] is not None and dest.stat().st_size != f["bytes"]) or \
               (f["md5"] and md5_file(dest).lower() != f["md5"].lower()):
                dest.replace(dest.with_name(dest.name + ".bad"))
        t0 = time.time()
        download(f["url"], dest, md5=f["md5"], size=f["bytes"], label=f"{f['sample']} {f['name']}")
        say(f"  OK  {f['sample']} {f['name']} ({human(dest.stat().st_size)}, {time.time() - t0:.0f} s, md5 verified)")
        return {"sample": f["sample"], "run": f["run"], "url": f["url"], "path": rel,
                "bytes": dest.stat().st_size, "md5": f["md5"].lower(), "md5_source": "ENA", "md5_verified": True}

    chosen_paths = {(fq / f["name"]).relative_to(out).as_posix() for f in files}
    manifest["files"] = [dict(f) for path, f in previous.items()
                         if path not in chosen_paths and f.get("md5_verified") and (out / path).exists()
                         and (out / path).stat().st_size == f.get("bytes")]
    errors = []
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        futures = {pool.submit(one, f): f for f in files}
        for fut in as_completed(futures):
            try:
                manifest["files"].append(fut.result())
                save_manifest()
            except Exception as e:
                errors.append(str(e))
                say(f"  ERROR {e}")
    manifest["files"].sort(key=lambda x: x["path"])
    save_manifest()
    done = sum(1 for f in manifest["files"] if f["path"] in chosen_paths)
    say(f"\n  {done}/{len(files)} file(s) ready in {time.time() - t_start:.0f} s "
        f"({len(manifest['files'])} in the manifest: {out / 'fetch_manifest.json'})")
    if errors:
        say(f"  {len(errors)} file(s) failed: run the same command again to resume.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
