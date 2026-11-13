"""omicchain_fetch.py against a fake GEO + ENA (real formats, interrupted downloads, wrong md5).
Usage: python tests/test_fetch.py"""
import gzip, hashlib, http.server, json, os, re, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
FETCH = HERE.parent / "omicchain_fetch.py"
ROOT = Path(tempfile.mkdtemp())
FILES = ROOT / "srv"; FILES.mkdir()
md5 = lambda b: hashlib.md5(b).hexdigest()

SOFT = """^DATABASE = GeoMiame
!Database_name = Gene Expression Omnibus (GEO)
^SERIES = GSE90001
!Series_title = Test airway-like study
!Series_type = Expression profiling by high throughput sequencing
!Series_sample_id = GSM100
!Series_sample_id = GSM101
!Series_sample_id = GSM102
!Series_sample_id = GSM103
!Series_relation = BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA900001
!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP900001
^PLATFORM = GPL11154
!Platform_title = Illumina HiSeq 2000 (Homo sapiens)
^SAMPLE = GSM100
!Sample_title = N61311_untreated
!Sample_source_name_ch1 = airway smooth muscle
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = cell line: N61311
!Sample_characteristics_ch1 = treatment: Untreated
!Sample_library_strategy = RNA-Seq
!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX100
^SAMPLE = GSM101
!Sample_title = N61311_Dex
!Sample_source_name_ch1 = airway smooth muscle
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = cell line: N61311
!Sample_characteristics_ch1 = treatment: Dexamethasone
!Sample_library_strategy = RNA-Seq
!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRX101
^SAMPLE = GSM102
!Sample_title = N052611_untreated
!Sample_source_name_ch1 = airway smooth muscle
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = cell line: N052611
!Sample_characteristics_ch1 = treatment: Untreated
!Sample_characteristics_ch1 = free text without key
!Sample_library_strategy = RNA-Seq
^SAMPLE = GSM103
!Sample_title = N052611_Dex
!Sample_source_name_ch1 = airway smooth muscle
!Sample_organism_ch1 = Homo sapiens
!Sample_characteristics_ch1 = cell line: N052611
!Sample_characteristics_ch1 = treatment: Dexamethasone
!Sample_library_strategy = RNA-Seq
"""
geo_dir = FILES / "geo/series/GSE90nnn/GSE90001/soft"; geo_dir.mkdir(parents=True)
(geo_dir / "GSE90001_family.soft.gz").write_bytes(gzip.compress(SOFT.encode()))

runs = [("SRR1001", "SRX100", "GSM100", "x"), ("SRR1002", "SRX100", "GSM100", "x"),
        ("SRR1011", "SRX101", "GSM101", "x"), ("SRR1021", "SRX102", "GSM102", "x"),
        ("SRR1031", "SRX103", "SAMN555", "Illumina HiSeq 2000 sequencing; GSM103: N052611_Dex"),
        ("SRR999", "SRX999", "GSM999", "other")]
fields = ["run_accession", "experiment_accession", "sample_accession", "secondary_sample_accession", "study_accession",
          "secondary_study_accession", "sample_alias", "sample_title", "experiment_title", "library_layout", "library_strategy",
          "library_source", "instrument_model", "scientific_name", "read_count", "base_count", "fastq_ftp", "fastq_md5", "fastq_bytes"]
CONTENT = {}
lines = ["\t".join(fields)]
for i, (srr, srx, alias, title) in enumerate(runs):
    urls, md5s, sizes = [], [], []
    names = [f"{srr}_1.fastq.gz", f"{srr}_2.fastq.gz"] + ([f"{srr}.fastq.gz"] if srr == "SRR1011" else [])
    for name in names:
        data = os.urandom((3_000_000 if srr == "SRR1001" else 300_000) + 1000 * i)
        p = f"vol1/fastq/{srr[:6]}/{srr}/{name}"
        (FILES / p).parent.mkdir(parents=True, exist_ok=True); (FILES / p).write_bytes(data)
        CONTENT[name] = data
        urls.append(f"127.0.0.1:{{port}}/{p}"); md5s.append(md5(data) if srr != "SRR1021" or name.endswith("_1.fastq.gz") else "0" * 32)
        sizes.append(str(len(data)))
    lines.append("\t".join([srr, srx, f"SAMN{i}", f"SRS{i}", "PRJNA900001", "SRP900001", alias, "", title, "PAIRED",
                            "RNA-Seq", "TRANSCRIPTOMIC", "Illumina HiSeq 2000", "Homo sapiens", "1000", "50000",
                            ";".join(urls), ";".join(md5s), ";".join(sizes)]))
ENA_TSV = "\n".join(lines) + "\n"

state = {"cuts": 0, "queries": [], "resumed": 0}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/ena/portal/api/filereport":
            q = parse_qs(u.query); state["queries"].append(q)
            assert q["result"] == ["read_run"] and "fastq_md5" in q["fields"][0]
            if q["accession"][0] not in ("SRP900001", "PRJNA900001"):
                body = b"run_accession\n"
            else:
                body = ENA_TSV.replace("{port}", str(PORT)).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        f = FILES / u.path.lstrip("/")
        if not f.is_file():
            self.send_response(404); self.end_headers(); return
        data = f.read_bytes(); start = 0
        m = re.match(r"bytes=(\d+)-", self.headers.get("Range", ""))
        if m:
            start = int(m.group(1)); self.send_response(206)
            if f.name.startswith("SRR1001_") and start > 0: state["resumed"] += 1
            self.send_header("Content-Range", f"bytes {start}-{len(data)-1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        if f.name.startswith("SRR1001_") and state["cuts"] < 3:
            state["cuts"] += 1
            self.wfile.write(body[: len(body) // 3]); self.wfile.flush(); self.connection.shutdown(2); return
        self.wfile.write(body)
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

env = dict(os.environ, OMICCHAIN_GEO_SOFT_URL=f"http://127.0.0.1:{PORT}/geo/series/{{stub}}/{{acc}}/soft/{{acc}}_family.soft.gz",
           OMICCHAIN_ENA_API=f"http://127.0.0.1:{PORT}/ena/portal/api/filereport", OMICCHAIN_ENA_PREFIX="http://")
def fetch(*args, cwd=ROOT):
    r = subprocess.run([sys.executable, str(FETCH), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr

FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"\n      -> {detail}"))
    if not cond: FAILED.append(name)

rc, out = fetch("GSE90001", "--dry-run")
S = ROOT / "study_GSE90001"
tsv = (S / "samples.tsv").read_text().splitlines()
head = tsv[0].split("\t"); rows = {l.split("\t")[0]: dict(zip(head, l.split("\t"))) for l in tsv[1:]}
check("1. dry-run: code 0", rc == 0, out)
check("1. 4 samples, characteristics as columns", list(rows) == ["GSM100", "GSM101", "GSM102", "GSM103"]
      and "cell_line" in head and "treatment" in head and rows["GSM101"]["treatment"] == "Dexamethasone", tsv)
check("1. value without a key -> characteristics_3", rows["GSM102"].get("characteristics_3") == "free text without key", head)
check("1. GSM100 = 2 runs (via SRX)", rows["GSM100"]["runs"] == "SRR1001;SRR1002", rows["GSM100"])
check("1. GSM102 found through sample_alias", rows["GSM102"]["runs"] == "SRR1021")
check("1. GSM103 matched by the experiment title", rows["GSM103"]["runs"] == "SRR1031")
check("1. run of another series reported and ignored", "SRR999" in out and "SRR999" not in (S / "samples.tsv").read_text(), out)
check("1. no FASTQ downloaded", not (S / "fastq").exists())
man = json.loads((S / "fetch_manifest.json").read_text())
check("1. provenance: md5 of the SOFT file + ENA query", man["provenance"]["geo_soft"]["md5"] == md5((geo_dir / "GSE90001_family.soft.gz").read_bytes())
      and man["provenance"]["ena"][0]["accession"] == "SRP900001", man["provenance"])

rc, out = fetch("GSE90001", "--samples", "GSM100,GSM101", "--jobs", "3")
fq = S / "fastq"
got = sorted(p.name for p in fq.iterdir())
check("2. code 0", rc == 0, out)
check("2. right files (_1/_2, not the orphan reads file)", got == ["SRR1001_1.fastq.gz", "SRR1001_2.fastq.gz", "SRR1002_1.fastq.gz",
      "SRR1002_2.fastq.gz", "SRR1011_1.fastq.gz", "SRR1011_2.fastq.gz"], got)
check("2. content identical to the original", all((fq / n).read_bytes() == CONTENT[n] for n in got))
check("2. interruptions recovered by resuming", state["cuts"] == 3 and out.count("connection interrupted") >= 3, out)
check("2. ... resuming where it was interrupted (Range), not from the start", state["resumed"] >= 3, {k: state[k] for k in ("cuts", "resumed")})
man = json.loads((S / "fetch_manifest.json").read_text())
check("2. manifest: 6 files, ENA md5 verified", len(man["files"]) == 6 and all(f["md5_verified"] for f in man["files"])
      and all(f["md5"] == md5(CONTENT[Path(f["path"]).name]) for f in man["files"]), man["files"])

before = {p.name: p.stat().st_mtime for p in fq.iterdir()}
rc, out = fetch("GSE90001", "--samples", "GSM100,GSM101")
check("3. second run: code 0, no file rewritten", rc == 0 and before == {p.name: p.stat().st_mtime for p in fq.iterdir()}
      and "OK " not in out, out)

rc, out = fetch("GSE90001", "--samples", "GSM102,GSM103")
check("4. wrong md5 -> code 1 and message", rc == 1 and "md5" in out and "SRR1021_2" in out, out)
check("4. wrong file set aside (.bad), never accepted", (fq / "SRR1021_2.fastq.gz.bad").exists() and not (fq / "SRR1021_2.fastq.gz").exists())
check("4. the other files are there", all((fq / n).exists() for n in ["SRR1021_1.fastq.gz", "SRR1031_1.fastq.gz", "SRR1031_2.fastq.gz"]))
man = json.loads((S / "fetch_manifest.json").read_text())
check("4. the manifest keeps the verified files, including those of an earlier run",
      len(man["files"]) == 9 and not any("SRR1021_2" in f["path"] for f in man["files"]), [f["path"] for f in man["files"]])

(fq / "SRR1031_1.fastq.gz").write_bytes(b"corrompu")
rc, out = fetch("GSE90001", "--samples", "GSM103")
check("5. corrupted file replaced by the right one", (fq / "SRR1031_1.fastq.gz").read_bytes() == CONTENT["SRR1031_1.fastq.gz"], out)

man = json.loads((S / "fetch_manifest.json").read_text())
check("5. ... and the manifest still lists the 9 verified files", len(man["files"]) == 9, [f["path"] for f in man["files"]])

rc, out = fetch("SRP900001", "--limit", "1", "--out", "srp_test")
t = (ROOT / "srp_test" / "samples.tsv").read_text().splitlines()
check("6. SRP: 6 ENA samples, only 1 downloaded", rc == 0 and len(t) == 7 and len(list((ROOT / "srp_test" / "fastq").iterdir())) == 2, out)

rc, out = fetch("GSE12", "--dry-run")
check("7. unknown GSE -> clear message", rc == 1 and "not found" in out, out)
rc, out = fetch("XYZ123")
check("7. accession not recognised", rc == 1 and "not recognised" in out, out)
rc, out = fetch("GSE90001", "--samples", "GSM777")
check("7. unknown sample", rc == 1 and "GSM777" in out, out)

srv.shutdown(); shutil.rmtree(ROOT, ignore_errors=True)
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
