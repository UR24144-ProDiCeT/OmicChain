"""Reference cache (GENCODE genome and annotation, Rsubread index): download, build, import, fingerprint and verify."""

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import omicchain_fetch as of

VERSION = "0.1"
HERE = Path(__file__).resolve().parent
GENCODE_DIR = os.environ.get("OMICCHAIN_GENCODE_DIR",
                             "https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_{release}")
GENOME_FILE = "GRCh38.primary_assembly.genome.fa.gz"
GTF_FILE = "gencode.v{release}.primary_assembly.annotation.gtf.gz"
INDEX_BASENAME = "GRCh38"
EXCLUDED_INDEX_SUFFIXES = (".files", ".log")
INDEX_TYPES = ("gapped", "full")


def say(*parts):
    print(*parts, flush=True)


DISK_NEED_GB = {"gapped": 12, "full": 22}


def disk_need_gb(index_type):
    """Disk space (GB) to plan for this index; OMICCHAIN_DISK_NEED_GB overrides it."""
    import os
    try:
        return float(os.environ["OMICCHAIN_DISK_NEED_GB"])
    except (KeyError, ValueError):
        return DISK_NEED_GB[index_type]


def free_bytes(path):
    """Free space on the disk holding `path` (the folder may not exist yet)."""
    import shutil
    p = Path(path).expanduser().resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def default_ref_dir():
    if os.environ.get("OMICCHAIN_REF_DIR"):
        return Path(os.environ["OMICCHAIN_REF_DIR"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "OmicChain" / "references"
    return Path.home() / ".omicchain" / "references"


def sha256_file(path, chunk=8 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def now():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def index_files(base):
    """Files of an Rsubread index <base>.*, except .files and .log."""
    base = Path(base)
    files = sorted(p for p in base.parent.glob(base.name + ".*")
                   if p.is_file() and not p.name.endswith(EXCLUDED_INDEX_SUFFIXES))
    return files


def index_digest(base):
    """Return ({suffix: sha256}, fingerprint of the index)."""
    base = Path(base)
    files = {p.name[len(base.name):]: sha256_file(p) for p in index_files(base)}
    if not files:
        raise SystemExit(f"ERROR: no index file found for {base}")
    listing = "".join(f"{suffix}\t{digest}\n" for suffix, digest in sorted(files.items()))
    return files, hashlib.sha256(listing.encode()).hexdigest()


def rscript():
    path = os.environ.get("OMICCHAIN_RSCRIPT") or shutil.which("Rscript")
    if not path:
        raise SystemExit("ERROR: Rscript not found (add R to the PATH, or set OMICCHAIN_RSCRIPT)")
    return path


def build_index(genome, dest_base, index_type, log_path):
    """Build the index in a '.building' folder, then rename it, so that an interrupted build is never taken for a complete index."""
    final_dir = Path(dest_base).parent
    work = final_dir.with_name(final_dir.name + ".building")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    result = work / "build_result.dcf"
    cmd = [rscript(), str(HERE / "build_index.R"), str(Path(genome).resolve()),
           str((work / INDEX_BASENAME).resolve()), index_type, str(result.resolve())]
    say(f"  Building the {index_type} index (Rsubread, single core); "
        f"allow about 1 h (gapped) to 3 h (full). Log: {log_path}")
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            log.write(line); log.flush()
            if re.search(r"\d+%|completed|Total running time", line):
                say("    " + line.rstrip())
        status = proc.wait()
    if status != 0 or not result.exists():
        raise SystemExit(f"ERROR: the index could not be built (see {log_path})")
    info = {}
    for line in result.read_text().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    result.unlink()
    for tmp in work.glob("subread-index-sam-*"):
        tmp.unlink()
    if final_dir.exists():
        shutil.rmtree(final_dir)
    work.replace(final_dir)
    return info


def import_index(source_base, dest_base):
    source_base = Path(source_base)
    files = sorted(p for p in source_base.parent.glob(source_base.name + ".*") if p.is_file())
    if not index_files(source_base):
        raise SystemExit(f"ERROR: no Rsubread index found for {source_base} "
                         "(give the path without extension, for example ...\\index_full\\GRCh38)")
    final_dir = Path(dest_base).parent
    work = final_dir.with_name(final_dir.name + ".building")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    total = sum(p.stat().st_size for p in files)
    say(f"  Copying the index ({total / 1024**3:.1f} GB) from {source_base.parent}")
    for p in files:
        shutil.copyfile(p, work / (INDEX_BASENAME + p.name[len(source_base.name):]))
    if final_dir.exists():
        shutil.rmtree(final_dir)
    work.replace(final_dir)


def installed_rsubread_version():
    try:
        out = subprocess.run([rscript(), "-e", "cat(as.character(packageVersion('Rsubread')))"],
                             capture_output=True, text=True, timeout=120)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def ref_paths(ref_dir, release):
    root = Path(ref_dir) / f"gencode_v{release}"
    return {"root": root, "manifest": root / "reference.json",
            "genome": root / GENOME_FILE, "gtf": root / GTF_FILE.format(release=release)}


def load_manifest(path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_manifest(path, manifest):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def gencode_md5sums(release):
    url = GENCODE_DIR.format(release=release) + "/MD5SUMS"
    r = of.http_get(url)
    sums = {}
    for line in r.text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sums[parts[-1].lstrip("*")] = parts[0].lower()
    return sums, url


def ensure_sources(release, paths, manifest):
    """Genome and GTF, downloaded if needed and checked against the GENCODE md5."""
    sources = manifest.get("sources") or {}
    names = {"genome": GENOME_FILE, "gtf": GTF_FILE.format(release=release)}
    if all(k in sources and paths[k].exists() and paths[k].stat().st_size == sources[k]["bytes"] for k in names):
        return sources
    sums, sums_url = gencode_md5sums(release)
    base = GENCODE_DIR.format(release=release)
    for key, name in names.items():
        if name not in sums:
            raise SystemExit(f"ERROR: {name} is not listed in {sums_url} (release {release}?)")
        dest = paths[key]
        if dest.exists() and of.md5_file(dest) != sums[name]:
            dest.replace(dest.with_name(dest.name + ".bad"))
        if not dest.exists():
            say(f"  Downloading {name}")
        of.download(f"{base}/{name}", dest, md5=sums[name], size=None, label=name)
        sources[key] = {"file": name, "url": f"{base}/{name}", "bytes": dest.stat().st_size,
                        "md5": sums[name], "md5_source": sums_url, "sha256": sha256_file(dest)}
        say(f"  OK  {name} (GENCODE md5 verified)")
    return sources


def cmd_prepare(a):
    paths = ref_paths(a.ref_dir, a.gencode)
    paths["root"].mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(paths["manifest"]) or {
        "tool": "omicchain_reference", "version": VERSION, "organism": "Homo sapiens",
        "assembly": "GRCh38 primary assembly", "annotation": f"GENCODE v{a.gencode}",
        "gencode_release": str(a.gencode), "indexes": {}}
    say(f"OmicChain reference {VERSION} -- GENCODE v{a.gencode}, index {a.index}\n  Cache: {paths['root']}")
    manifest["sources"] = ensure_sources(a.gencode, paths, manifest)
    save_manifest(paths["manifest"], manifest)

    dest_base = paths["root"] / f"index_{a.index}" / INDEX_BASENAME
    if a.import_index and a.in_place:
        dest_base = Path(a.import_index).resolve()
    existing = manifest["indexes"].get(a.index)
    if existing and not a.import_index and not a.rebuild and index_files(paths["root"] / existing["path"]):
        say(f"  Index {a.index} already prepared (fingerprint {existing['fingerprint'][:16]}...); "
            "use 'verify' to check it")
        return 0

    t0 = time.time()
    if a.import_index:
        if a.in_place:
            if not index_files(dest_base):
                raise SystemExit(f"ERROR: no Rsubread index found for {dest_base} "
                                 "(give the path without extension, for example ...\\index_full\\GRCh38)")
            say(f"  Index used in place (no copy): {dest_base}")
        else:
            import_index(a.import_index, dest_base)
        info = {"rsubread_version": a.rsubread_version or installed_rsubread_version()}
        origin = {"method": "referenced" if a.in_place else "imported", "from": str(Path(a.import_index).resolve()),
                  "note": "index built elsewhere; rebuild it and compare the fingerprint to check it"}
    else:
        info = build_index(paths["genome"], dest_base, a.index, paths["root"] / f"build_{a.index}.log")
        origin = {"method": "built", "elapsed_s": float(info.get("elapsed_s", time.time() - t0)),
                  "r_version": info.get("r_version")}
    say("  Computing the index fingerprints...")
    files, fingerprint = index_digest(dest_base)
    manifest["indexes"][a.index] = {
        "type": a.index, "gapped": a.index == "gapped",
        "path": str(dest_base) if (a.import_index and a.in_place) else f"index_{a.index}/{INDEX_BASENAME}",
        "aligner": "Rsubread", "rsubread_version": info.get("rsubread_version"),
        "genome_sha256": manifest["sources"]["genome"]["sha256"],
        "files_sha256": files, "excluded_from_fingerprint": list(EXCLUDED_INDEX_SUFFIXES),
        "fingerprint": fingerprint, "origin": origin, "date": now(),
        "bytes": sum(p.stat().st_size for p in index_files(dest_base))}
    save_manifest(paths["manifest"], manifest)
    say(f"  OK  index {a.index} ready: fingerprint {fingerprint}")
    return 0


def cmd_verify(a):
    paths = ref_paths(a.ref_dir, a.gencode)
    manifest = load_manifest(paths["manifest"])
    if not manifest:
        say(f"ERROR: no GENCODE v{a.gencode} reference in {Path(a.ref_dir)}")
        return 1
    problems = []
    for key in ("genome", "gtf"):
        src = manifest["sources"][key]
        p = paths[key]
        ok = p.exists() and sha256_file(p) == src["sha256"]
        say(f"  {'OK  ' if ok else 'BAD '} {src['file']}")
        if not ok:
            problems.append(src["file"])
    for t in ([a.index] if a.index else list(manifest["indexes"])):
        entry = manifest["indexes"].get(t)
        if not entry:
            say(f"  MISSING index {t}"); problems.append(f"index {t}"); continue
        base = paths["root"] / entry["path"]
        files = {p.name[len(base.name):]: p for p in index_files(base)}
        bad = [s for s, d in entry["files_sha256"].items() if s not in files or sha256_file(files[s]) != d]
        extra = sorted(set(files) - set(entry["files_sha256"]))
        ok = not bad and not extra
        say(f"  {'OK  ' if ok else 'BAD '} index {t} ({entry['fingerprint'][:16]}...)"
            + (f": files changed or missing {bad}" if bad else "") + (f": unexpected files {extra}" if extra else ""))
        if not ok:
            problems.append(f"index {t}")
    say("  Reference intact." if not problems else f"  {len(problems)} problem(s): {', '.join(problems)}")
    return 0 if not problems else 1


def cmd_show(a):
    root = Path(a.ref_dir)
    say(f"Cache: {root}")
    found = False
    for m in sorted(root.glob("gencode_v*/reference.json")):
        found = True
        man = json.loads(m.read_text(encoding="utf-8"))
        say(f"  {man['annotation']} ({man['assembly']})")
        for t, e in man.get("indexes", {}).items():
            say(f"    index {t:<6} Rsubread {e.get('rsubread_version')}  {e['bytes'] / 1024**3:.1f} GB  "
                f"{e['origin']['method']}  fingerprint {e['fingerprint']}")
    if not found:
        say("  (empty)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="OmicChain: GENCODE reference and Rsubread index, cached and fingerprinted.")
    ap.add_argument("--ref-dir", type=Path, default=None, help="reference cache folder")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="download and check GENCODE, build (or import) the index")
    p.add_argument("--gencode", default="48", help="GENCODE release (default 48)")
    p.add_argument("--index", choices=INDEX_TYPES, default="gapped", help="index type (default gapped)")
    p.add_argument("--import-index", help="index already built, to import: path without extension (...\\GRCh38)")
    p.add_argument("--rsubread-version", help="Rsubread version that built the imported index")
    p.add_argument("--in-place", action="store_true",
                   help="with --import-index: use the index where it is, without copying it into the cache")
    p.add_argument("--rebuild", action="store_true", help="rebuild even if the index exists")
    v = sub.add_parser("verify", help="recompute the fingerprints and compare them with the manifest")
    v.add_argument("--gencode", default="48")
    v.add_argument("--index", choices=INDEX_TYPES)
    sub.add_parser("show", help="list the cached references")
    a = ap.parse_args(argv)
    a.ref_dir = a.ref_dir or default_ref_dir()
    try:
        return {"prepare": cmd_prepare, "verify": cmd_verify, "show": cmd_show}[a.command](a)
    except of.FetchError as e:
        say(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
