"""Upstream workflow (upstream.smk) end to end on a simulated study, with the real Snakemake and Rsubread.
Usage: python tests/test_upstream.py     (R + Rsubread required; R_LIBS variable if needed)"""
import csv, gzip, hashlib, http.server, json, os, random, shutil, subprocess, sys, tempfile, threading
from pathlib import Path

HERE = Path(__file__).resolve().parent; CODE = HERE.parent
ROOT = Path(tempfile.mkdtemp(prefix="upstream test "))
md5 = lambda p: hashlib.md5(Path(p).read_bytes()).hexdigest()
random.seed(7)
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"\n      -> {str(detail)[-2500:]}"))
    if not cond: FAILED.append(name)

chroms = {"chr1": "".join(random.choice("ACGT") for _ in range(600_000)), "chr2": "".join(random.choice("ACGT") for _ in range(300_000))}
def rc(s): return s.translate(str.maketrans("ACGT", "TGCA"))[::-1]
gtf, tx, genes = [], [], []
gid = 0
for chrom, seq in chroms.items():
    pos = 2000
    while pos < len(seq) - 8000:
        gid += 1; strand = "+" if gid % 2 else "-"
        name = "DUPNAME" if gid in (3, 4) else ("PARGENE" if gid == 5 else f"GENE{gid}")
        ensg = f"ENSG{gid:011d}.{1 + gid % 3}" + ("_PAR_Y" if gid == 5 else "")
        exons, p = [], pos
        for _ in range(random.randint(1, 3)):
            l = random.randint(300, 900); exons.append((p, p + l - 1)); p += l + random.randint(200, 1500)
        attr = f'gene_id "{ensg}"; gene_type "protein_coding"; gene_name "{name}";'
        gtf.append(f"{chrom}\tHAVANA\tgene\t{exons[0][0]}\t{exons[-1][1]}\t.\t{strand}\t.\t{attr}")
        gtf.append(f"{chrom}\tHAVANA\ttranscript\t{exons[0][0]}\t{exons[-1][1]}\t.\t{strand}\t.\t{attr} transcript_id \"T{gid}\";")
        for a, b in exons:
            gtf.append(f"{chrom}\tHAVANA\texon\t{a}\t{b}\t.\t{strand}\t.\t{attr} transcript_id \"T{gid}\";")
        s = "".join(seq[a - 1:b] for a, b in exons)
        tx.append((f"T{gid}", s if strand == "+" else rc(s))); genes.append((ensg, name))
        pos = p + random.randint(500, 3000)
N_GENES = len(genes)
wrap = lambda s: "\n".join(s[i:i + 60] for i in range(0, len(s), 60))
fa_txt = "".join(f">{c}\n{wrap(s)}\n" for c, s in chroms.items())
SRV = ROOT / "srv" / "release_48"; SRV.mkdir(parents=True)
(SRV / "GRCh38.primary_assembly.genome.fa.gz").write_bytes(gzip.compress(fa_txt.encode()))
(SRV / "gencode.v48.primary_assembly.annotation.gtf.gz").write_bytes(gzip.compress(("##description: test\n" + "\n".join(gtf) + "\n").encode()))
(SRV / "MD5SUMS").write_text("".join(f"{md5(SRV / n)}  {n}\n" for n in ("GRCh38.primary_assembly.genome.fa.gz", "gencode.v48.primary_assembly.annotation.gtf.gz")))
(ROOT / "tx.fa").write_text("".join(f">{t}\n{wrap(s)}\n" for t, s in tx))

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(ROOT / "srv"), **k)
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
CACHE = ROOT / "ref cache"
env = dict(os.environ, OMICCHAIN_GENCODE_DIR=f"http://127.0.0.1:{srv.server_address[1]}/release_{{release}}")
r = subprocess.run([sys.executable, str(CODE / "omicchain_reference.py"), "--ref-dir", str(CACHE), "prepare", "--index", "gapped"],
                   env=env, capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr
srv.shutdown()

STUDY = ROOT / "study_GSE1"; (STUDY / "fastq").mkdir(parents=True)
DE_UP = set(range(0, N_GENES, 7))
base_expr = [random.lognormvariate(3, 1) for _ in range(N_GENES)]
design = [("GSM1", "ctrl_1", "Control", ["SRR11", "SRR12"]), ("GSM2", "ctrl_2", "Control", ["SRR21"]),
          ("GSM3", "trt_1", "Treated", ["SRR31"]), ("GSM4", "trt_2", "Treated", ["SRR41"]),
          ("GSM5", "other", "Other", ["SRR51"])]
R_SIM = """args <- commandArgs(TRUE); suppressMessages(library(Rsubread)); e <- as.numeric(strsplit(args[3], ",")[[1]])
invisible(capture.output(simReads(args[1], e, args[2], library.size = as.integer(args[4]), read.length = 75L, paired.end = TRUE,
  strandSpecific = 2L, simulate.sequencing.error = TRUE)))"""
(ROOT / "sim.R").write_text(R_SIM)
files, ena_rows, sample_rows, sizes = [], [], [], {}
for gsm, title, trt, runs in design:
    for run in runs:
        expr = [b * (4 if (trt == "Treated" and i in DE_UP) else 1) for i, b in enumerate(base_expr)]
        n = random.randint(15_000, 25_000); sizes[run] = n
        subprocess.run(["Rscript", str(ROOT / "sim.R"), str(ROOT / "tx.fa"), str(ROOT / run), ",".join(f"{x:.4f}" for x in expr), str(n)], check=True)
        for m in (1, 2):
            dest = STUDY / "fastq" / f"{run}_{m}.fastq.gz"; (ROOT / f"{run}_R{m}.fastq.gz").replace(dest)
            files.append({"sample": gsm, "run": run, "url": "x", "path": f"fastq/{dest.name}", "bytes": dest.stat().st_size,
                          "md5": md5(dest), "md5_source": "ENA", "md5_verified": True})
        ena_rows.append({"run_accession": run, "read_count": str(n)})
    sample_rows.append({"sample": gsm, "title": title, "source_name": "x", "organism": "Homo sapiens", "treatment": trt,
                        "cell line": "L" + title[-1], "tissue": "airway", "3rd batch": "b" + str(len(runs)),
                        "library_layout": "PAIRED", "library_strategy": "RNA-Seq", "runs": ";".join(runs), "read_count": "", "fastq_bytes": ""})
ena_rows[1]["read_count"] = str(sizes["SRR12"] + 5)
def wtsv(p, rows):
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n"); w.writeheader(); w.writerows(rows)
wtsv(STUDY / "samples.tsv", sample_rows); wtsv(STUDY / "ena_runs.tsv", ena_rows)
(STUDY / "fetch_manifest.json").write_text(json.dumps({"tool": "omicchain_fetch", "provenance": {"input": "GSE1"}, "files": files}))

def snake(*extra, cwd=ROOT, config=None):
    cfg = dict({"study": str(STUDY), "condition": "treatment", "reference_level": "Control", "test_level": "Treated",
                "ref_dir": str(CACHE), "align_threads": 2, "count_threads": 2}, **(config or {}))
    cmd = [sys.executable, "-m", "snakemake", "-s", str(CODE / "upstream.smk"), "-j", "2", "--resources", "mem_gb=24",
           "--config", *[f"{k}={v}" for k, v in cfg.items()], *extra]
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800)
    return p.returncode, p.stdout + p.stderr

OUT = STUDY / "upstream" / "gapped_gencode48"
rc_, out = snake()
check("1. full workflow: code 0", rc_ == 0, out)
rows = list(csv.reader(open(OUT / "expression_input.tsv"), delimiter="\t"))
check("1. expression_input.tsv: 'gene' + the 4 selected samples (not GSM5 'Other')", rows[0] == ["gene", "GSM1", "GSM2", "GSM3", "GSM4"], rows[0])
names = [r[0] for r in rows[1:]]
check("1. gene_id with the same symbol merged (DUPNAME once), symbols sorted", names.count("DUPNAME") == 1 and names == sorted(names)
      and len(names) == N_GENES - 1, (len(names), N_GENES))
byid = list(csv.reader(open(OUT / "counts_by_gene_id.tsv"), delimiter="\t"))
dup = [r for r in byid[1:] if r[1] == "DUPNAME"]; dn = next(r for r in rows if r[0] == "DUPNAME")
check("1. DUPNAME = sum of its two gene_id", len(dup) == 2 and all(int(dn[i]) == int(dup[0][i + 1]) + int(dup[1][i + 1]) for i in range(1, 5)), (dup, dn))
c11 = {l.split("\t")[0]: int(l.split("\t")[1]) for l in open(OUT / "counts" / "SRR11.tsv")}
c12 = {l.split("\t")[0]: int(l.split("\t")[1]) for l in open(OUT / "counts" / "SRR12.tsv")}
g1 = {r[0]: int(r[2]) for r in byid[1:]}
check("1. GSM1 = SRR11 + SRR12 (runs added up)", all(g1[g] == c11[g] + c12[g] for g in g1))
meta = list(csv.reader(open(OUT / "metadata.tsv"), delimiter="\t"))
check("1. metadata.tsv: group control/treatment, original condition, covariates that vary (not 'tissue')",
      meta[0] == ["sample", "group", "condition", "title", "cell_line", "x_3rd_batch"]
      and [m[1] for m in meta[1:]] == ["control", "control", "treatment", "treatment"]
      and [m[2] for m in meta[1:]] == ["Control", "Control", "Treated", "Treated"]
      and [m[4] for m in meta[1:]] == ["L1", "L2", "L1", "L2"], meta)
prov = json.loads((OUT / "upstream_provenance.json").read_text())
check("1. library strandedness detected: reverse (2)", prov["counting"]["strand_specific"] == 2 and prov["counting"]["strand_mode"] == "detected", prov["counting"])
qc = {r["run"]: r for r in csv.DictReader(open(OUT / "qc_summary.tsv"), delimiter="\t")}
check("1. QC: fragments = ENA read_count for the correct runs", all(qc[r]["fragments_match_ena"] == "yes" for r in ("SRR11", "SRR21", "SRR31", "SRR41")), qc)
check("1. QC: difference with the ENA reported (SRR12)", qc["SRR12"]["fragments_match_ena"] == "NO" and any("SRR12: the FASTQ files hold" in w for w in prov["warnings"]) and "SRR12" in out, prov["warnings"])
check("1. provenance: reference (index fingerprint), FASTQ md5, design",
      len(prov["reference"]["index_fingerprint"]) == 64 and len(prov["inputs"]) == 5 and prov["design"]["samples"] == ["GSM1", "GSM2", "GSM3", "GSM4"])
check("1. temporary BAM files removed", not list((OUT / "bam").glob("*.bam")) if (OUT / "bam").exists() else True, list((OUT / "bam").glob("*")))
up = [i + 1 for i, (e, n) in enumerate(genes) if i in DE_UP and n not in ("DUPNAME", "PARGENE")]
ratio = lambda r: (int(r[3]) + int(r[4]) + 1) / (int(r[1]) + int(r[2]) + 1)
rowmap = {r[0]: r for r in rows[1:]}
up_r = sorted(ratio(rowmap[f"GENE{g}"]) for g in up if f"GENE{g}" in rowmap); flat = sorted(ratio(rowmap[n]) for n in rowmap if n.startswith("GENE") and int(n[4:]) - 1 not in DE_UP)
rel = up_r[len(up_r) // 2] / flat[len(flat) // 2]
check("1. genes simulated 4x: ~4x more expressed in the treated samples, relative to the unchanged genes", 3.0 < rel < 5.5, rel)
check("1. message: direction of the log2FC and possible covariates", "log2FC = Treated vs Control" in out and "cell_line" in out, out[-800:])
FIRST = {n: (OUT / n).read_bytes() for n in ("expression_input.tsv", "counts_by_gene_id.tsv", "metadata.tsv", "qc_summary.tsv", "upstream_provenance.json")}

rc_, out = snake()
check("2. second run: nothing to redo", rc_ == 0 and "Nothing to be done" in out, out[-600:])

shutil.rmtree(OUT)
rc_, out = snake(config={"align_threads": 1, "count_threads": 1})
same = {n: (OUT / n).read_bytes() == b for n, b in FIRST.items()}
check("3. everything redone, 1 thread: outputs identical byte for byte (provenance included)", rc_ == 0 and all(same.values()), same)

p = STUDY / "fastq" / "SRR21_1.fastq.gz"; orig = p.read_bytes(); b = bytearray(orig); b[-30] ^= 1; p.write_bytes(bytes(b))
(OUT / "align" / "SRR21.tsv").unlink()
rc_, out = snake()
check("4. FASTQ modified -> failure with md5 message", rc_ != 0 and "md5 of SRR21_1.fastq.gz" in out, out[-1500:])
p.write_bytes(orig)

idx = next((CACHE / "gencode_v48" / "index_gapped").glob("GRCh38.reads")); ib = idx.read_bytes(); bb = bytearray(ib); bb[0] ^= 1; idx.write_bytes(bytes(bb))
(OUT / "reference_check.json").unlink()
rc_, out = snake()
check("5. index changed -> failure 'reference changed'", rc_ != 0 and "reference changed" in out, out[-1500:])
idx.write_bytes(ib)

rc_, out = snake(config={"condition": "nope"});            check("6. unknown column", rc_ != 0 and "column 'nope' is not in" in out, out[-800:])
rc_, out = snake(config={"test_level": "Treatd"});         check("6. unknown level", rc_ != 0 and "level 'Treatd' is not in" in out, out[-800:])
rc_, out = snake(config={"samples": "GSM1,GSM3"});         check("6. fewer than 2 samples per level", rc_ != 0 and "fewer than 2" in out, out[-800:])
rc_, out = snake(config={"index": "full"});                check("6. index missing from the cache", rc_ != 0 and "omicchain_reference.py prepare" in out, out[-800:])

rc_, out = snake(config={"reference_level": "Treated", "test_level": "Control"})
meta = list(csv.reader(open(OUT / "metadata.tsv"), delimiter="\t"))
check("7. reference_level=Treated -> Treated code 'control'", rc_ == 0 and [m[1] for m in meta[1:]] == ["treatment", "treatment", "control", "control"]
      and "log2FC = Control vs Treated" in out, (meta, out[-600:]))

shutil.rmtree(ROOT, ignore_errors=True)
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
