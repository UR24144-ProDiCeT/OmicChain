"""Test tools: simulated genome / GTF / reads, fake GENCODE, GEO and ENA servers.
Used by the end-to-end tests (real Rsubread to simulate the reads)."""
import gzip, hashlib, http.server, os, random, subprocess, threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

md5b = lambda b: hashlib.md5(b).hexdigest()
wrap = lambda s: "\n".join(s[i:i + 60] for i in range(0, len(s), 60))
ENA_FIELDS = ["run_accession", "experiment_accession", "sample_accession", "secondary_sample_accession", "study_accession",
              "secondary_study_accession", "sample_alias", "sample_title", "experiment_title", "library_layout",
              "library_strategy", "library_source", "instrument_model", "scientific_name", "read_count", "base_count",
              "fastq_ftp", "fastq_md5", "fastq_bytes"]


class SimWorld:
    """A genome, its GTF (GENCODE format), transcripts, and a folder served over HTTP."""

    def __init__(self, tmp, seed=11, genome_len=400_000):
        self.tmp = Path(tmp); self.srv = self.tmp / "srv"; self.srv.mkdir(parents=True, exist_ok=True)
        random.seed(seed)
        seq = "".join(random.choice("ACGT") for _ in range(genome_len))
        rc = lambda s: s.translate(str.maketrans("ACGT", "TGCA"))[::-1]
        gtf, tx, pos, g = [], [], 2000, 0
        while pos < len(seq) - 6000:
            g += 1; strand = "+" if g % 2 else "-"; ex, p = [], pos
            for _ in range(random.randint(1, 3)):
                l = random.randint(300, 800); ex.append((p, p + l - 1)); p += l + random.randint(200, 1200)
            attr = f'gene_id "ENSG{g:011d}.1"; gene_name "G{g}";'
            gtf.append(f"chr1\tH\tgene\t{ex[0][0]}\t{ex[-1][1]}\t.\t{strand}\t.\t{attr}")
            gtf += [f"chr1\tH\texon\t{a}\t{b}\t.\t{strand}\t.\t{attr} transcript_id \"T{g}\";" for a, b in ex]
            s = "".join(seq[a - 1:b] for a, b in ex); tx.append((f"T{g}", s if strand == "+" else rc(s))); pos = p + 1000
        gc = self.srv / "gencode" / "release_48"; gc.mkdir(parents=True, exist_ok=True)
        (gc / "GRCh38.primary_assembly.genome.fa.gz").write_bytes(gzip.compress(f">chr1\n{wrap(seq)}\n".encode()))
        (gc / "gencode.v48.primary_assembly.annotation.gtf.gz").write_bytes(gzip.compress(("\n".join(gtf) + "\n").encode()))
        (gc / "MD5SUMS").write_text("".join(f"{md5b((gc / n).read_bytes())}  {n}\n" for n in sorted(os.listdir(gc)) if n != "MD5SUMS"))
        self.n_tx = len(tx)
        (self.tmp / "tx.fa").write_text("".join(f">{t}\n{wrap(s)}\n" for t, s in tx))
        (self.tmp / "sim.R").write_text("""a <- commandArgs(TRUE); suppressMessages(library(Rsubread))
invisible(capture.output(simReads(a[1], as.numeric(strsplit(a[3], ",")[[1]]), a[2], library.size = as.integer(a[4]),
  read.length = 75L, paired.end = a[5] == "PAIRED", simulate.sequencing.error = TRUE)))""")
        self.base = [random.lognormvariate(3, 1) for _ in tx]
        self.ena_rows = {}
        self.geo = {}
        self.port = None

    def add_run(self, project, run, sample_acc, title, layout="PAIRED", n=12000, up_factor=1.0, gsm=None, srx=None):
        expr = [b * (up_factor if k % 6 == 0 else 1) for k, b in enumerate(self.base)]
        subprocess.run(["Rscript", str(self.tmp / "sim.R"), str(self.tmp / "tx.fa"), str(self.tmp / run),
                        ",".join(f"{x:.3f}" for x in expr), str(n), layout], check=True)
        urls, md5s, sizes = [], [], []
        mates = (1, 2) if layout == "PAIRED" else (None,)
        for m in mates:
            src = self.tmp / f"{run}_R{m or 1}.fastq.gz"; data = src.read_bytes()
            rel = f"vol1/fastq/{run}/{run}_{m}.fastq.gz" if m else f"vol1/fastq/{run}/{run}.fastq.gz"
            (self.srv / rel).parent.mkdir(parents=True, exist_ok=True); (self.srv / rel).write_bytes(data)
            urls.append(f"127.0.0.1:{{port}}/{rel}"); md5s.append(md5b(data)); sizes.append(str(len(data)))
        row = {"run_accession": run, "experiment_accession": srx or f"SRX{run[3:]}", "sample_accession": sample_acc,
               "secondary_sample_accession": "SRS" + run[3:], "study_accession": project, "secondary_study_accession": project,
               "sample_alias": gsm or sample_acc, "sample_title": title, "experiment_title": f"seq; {gsm}: {title}" if gsm else title,
               "library_layout": layout, "library_strategy": "RNA-Seq", "library_source": "TRANSCRIPTOMIC",
               "instrument_model": "Illumina", "scientific_name": "Homo sapiens", "read_count": str(n), "base_count": str(n * 150),
               "fastq_ftp": ";".join(urls), "fastq_md5": ";".join(md5s), "fastq_bytes": ";".join(sizes)}
        self.ena_rows.setdefault(project, []).append(row)

    def add_geo(self, gse, title, project, samples):
        """samples: [(gsm, title, {key: value}, srx)]"""
        soft = [f"^SERIES = {gse}", f"!Series_title = {title}", f"!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term={project}"]
        for gsm, t, chars, srx in samples:
            soft += [f"^SAMPLE = {gsm}", f"!Sample_title = {t}", "!Sample_source_name_ch1 = cells", "!Sample_organism_ch1 = Homo sapiens",
                     *[f"!Sample_characteristics_ch1 = {k}: {v}" for k, v in chars.items()], "!Sample_library_strategy = RNA-Seq",
                     f"!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term={srx}"]
        digits = gse[3:]
        d = self.srv / "geo/series" / ("GSE" + (digits[:-3] if len(digits) > 3 else "") + "nnn") / gse / "soft"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{gse}_family.soft.gz").write_bytes(gzip.compress(("\n".join(soft) + "\n").encode()))

    def serve(self):
        world = self

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k): super().__init__(*a, directory=str(world.srv), **k)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/ena/api":
                    acc = parse_qs(u.query)["accession"][0]
                    rows = world.ena_rows.get(acc) or [r for rs in world.ena_rows.values() for r in rs if r["run_accession"] == acc]
                    lines = ["\t".join(ENA_FIELDS)] + ["\t".join(r[f] for f in ENA_FIELDS) for r in rows]
                    body = ("\n".join(lines) + "\n").replace("{port}", str(world.port)).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
                    self.wfile.write(body); return
                return super().do_GET()

            def log_message(self, *a): pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return {"OMICCHAIN_DISK_NEED_GB": "0.1", "OMICCHAIN_GENCODE_DIR": f"http://127.0.0.1:{self.port}/gencode/release_{{release}}",
                "OMICCHAIN_GEO_SOFT_URL": f"http://127.0.0.1:{self.port}/geo/series/{{stub}}/{{acc}}/soft/{{acc}}_family.soft.gz",
                "OMICCHAIN_ENA_API": f"http://127.0.0.1:{self.port}/ena/api", "OMICCHAIN_ENA_PREFIX": "http://"}
