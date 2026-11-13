"""GEO source end to end in the application: GSE -> metadata -> comparison -> download + alignment +
counting as a background task -> counts loaded -> covariate -> pipeline.
Mock GEO / ENA / GENCODE (local HTTP), real Rsubread, mock DESeq2.
Usage: python tests/test_app_geo.py   (R + Rsubread required; R_LIBS if needed)"""
import gzip, hashlib, http.server, json, os, random, re, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="app geo "))
PROJ = TMP / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
SRV = TMP / "srv"; SRV.mkdir()
md5b = lambda b: hashlib.md5(b).hexdigest()
random.seed(11)

seq = "".join(random.choice("ACGT") for _ in range(400_000))
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
wrap = lambda s: "\n".join(s[i:i + 60] for i in range(0, len(s), 60))
GC = SRV / "gencode" / "release_48"; GC.mkdir(parents=True)
(GC / "GRCh38.primary_assembly.genome.fa.gz").write_bytes(gzip.compress(f">chr1\n{wrap(seq)}\n".encode()))
(GC / "gencode.v48.primary_assembly.annotation.gtf.gz").write_bytes(gzip.compress(("\n".join(gtf) + "\n").encode()))
(GC / "MD5SUMS").write_text("".join(f"{md5b((GC / n).read_bytes())}  {n}\n" for n in sorted(os.listdir(GC))))
(TMP / "tx.fa").write_text("".join(f">{t}\n{wrap(s)}\n" for t, s in tx))

(TMP / "sim.R").write_text("""a <- commandArgs(TRUE); suppressMessages(library(Rsubread))
invisible(capture.output(simReads(a[1], as.numeric(strsplit(a[3], ",")[[1]]), a[2], library.size = as.integer(a[4]),
  read.length = 75L, paired.end = TRUE, simulate.sequencing.error = TRUE)))""")
base = [random.lognormvariate(3, 1) for _ in tx]
design = [("GSM1", "u1", "Untreated", "N1"), ("GSM2", "d1", "Dexamethasone", "N1"), ("GSM3", "u2", "Untreated", "N2"),
          ("GSM4", "d2", "Dexamethasone", "N2"), ("GSM5", "u3", "Untreated", "N3"), ("GSM6", "a1", "Albuterol", "N1")]
soft = ["^SERIES = GSE77", "!Series_title = Simulated airway-like study",
        "!Series_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term=SRP77"]
ena = ["\t".join(["run_accession", "experiment_accession", "sample_accession", "secondary_sample_accession", "study_accession",
                  "secondary_study_accession", "sample_alias", "sample_title", "experiment_title", "library_layout", "library_strategy",
                  "library_source", "instrument_model", "scientific_name", "read_count", "base_count", "fastq_ftp", "fastq_md5", "fastq_bytes"])]
for i, (gsm, title, trt, cl) in enumerate(design):
    run, srx = f"SRR77{i}", f"SRX77{i}"
    soft += [f"^SAMPLE = {gsm}", f"!Sample_title = {title}", "!Sample_source_name_ch1 = ASM", "!Sample_organism_ch1 = Homo sapiens",
             f"!Sample_characteristics_ch1 = treatment: {trt}", f"!Sample_characteristics_ch1 = cell line: {cl}",
             "!Sample_characteristics_ch1 = tissue: airway", "!Sample_library_strategy = RNA-Seq",
             f"!Sample_relation = SRA: https://www.ncbi.nlm.nih.gov/sra?term={srx}"]
    expr = [b * (5 if trt == "Dexamethasone" and k % 6 == 0 else 1) for k, b in enumerate(base)]
    n = 12000
    subprocess.run(["Rscript", str(TMP / "sim.R"), str(TMP / "tx.fa"), str(TMP / run), ",".join(f"{x:.3f}" for x in expr), str(n)], check=True)
    urls, md5s, sizes = [], [], []
    for m in (1, 2):
        data = (TMP / f"{run}_R{m}.fastq.gz").read_bytes(); rel = f"vol1/fastq/{run}/{run}_{m}.fastq.gz"
        (SRV / rel).parent.mkdir(parents=True, exist_ok=True); (SRV / rel).write_bytes(data)
        urls.append(f"127.0.0.1:{{port}}/{rel}"); md5s.append(md5b(data)); sizes.append(str(len(data)))
    ena.append("\t".join([run, srx, f"SAMN{i}", f"SRS{i}", "PRJNA77", "SRP77", gsm, "", f"x; {gsm}: {title}", "PAIRED", "RNA-Seq",
                          "TRANSCRIPTOMIC", "Illumina", "Homo sapiens", str(n), str(n * 150), ";".join(urls), ";".join(md5s), ";".join(sizes)]))
geo = SRV / "geo/series/GSEnnn/GSE77/soft"; geo.mkdir(parents=True)
(geo / "GSE77_family.soft.gz").write_bytes(gzip.compress(("\n".join(soft) + "\n").encode()))
ENA_TSV = "\n".join(ena) + "\n"

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(SRV), **k)
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/ena/api":
            body = (ENA_TSV.replace("{port}", str(PORT)) if parse_qs(u.query)["accession"][0] == "SRP77" else "run_accession\n").encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        return super().do_GET()
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

from eth_account import Account
w = Account.create()
real_rscript = shutil.which("Rscript")
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(),
                   "OMICCHAIN_DISK_NEED_GB": "0.1", "OMICCHAIN_GENCODE_DIR": f"http://127.0.0.1:{PORT}/gencode/release_{{release}}",
                   "OMICCHAIN_GEO_SOFT_URL": f"http://127.0.0.1:{PORT}/geo/series/{{stub}}/{{acc}}/soft/{{acc}}_family.soft.gz",
                   "OMICCHAIN_ENA_API": f"http://127.0.0.1:{PORT}/ena/api", "OMICCHAIN_ENA_PREFIX": "http://",
                   "OMICCHAIN_REF_DIR": str(TMP / "ref cache"), "MOCK_REAL_RSCRIPT": real_rscript,
                   "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
r = subprocess.run([sys.executable, str(PROJ / "omicchain_reference.py"), "prepare", "--index", "gapped"], cwd=PROJ, capture_output=True, text=True)
assert r.returncode == 0, r.stdout + r.stderr

os.chdir(PROJ); sys.path.insert(0, str(PROJ))
import omicchain_jobs as oj
from streamlit.testing.v1 import AppTest
JOBS = PROJ / "jobs"
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-2000:]}"))
    if not cond: FAILED.append(name)
def btn(at, label): return next(b for b in at.button if label in b.label)
def sel(at, key): return at.selectbox(key=key)
def texts(els): return " | ".join(str(e.value) for e in els)
def wait_jobs(timeout=900):
    t = time.time()
    while time.time() - t < timeout and any(j["status"] == "running" for j in oj.list_jobs(JOBS)): time.sleep(1)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180); at.run()
check("0. default source: public study", at.radio(key="data_source").value.startswith("A public"))
at.text_input(key="geo_accession").input("GSE404").run(); btn(at, "Get the study's metadata").click().run()
check("1. unknown accession -> error with the output of fetch", any("Could not get the metadata" in e.value for e in at.error)
      and "not found" in texts(at.code), texts(at.error) + texts(at.code))
at.text_input(key="geo_accession").input("gse77").run()
btn(at, "Get the study's metadata").click().run()
md = texts(at.markdown)
df = next(d for d in at.dataframe if "sample" in d.value.columns).value
check("2. metadata: series title, 6 samples", "Simulated airway-like study" in md and len(df) == 6 and not at.error, md[-600:])
check("2. table: columns that vary (not tissue)", list(df.columns) == ["sample", "title", "treatment", "cell_line"], list(df.columns))
check("2b. comparison proposed by default: treatment, Untreated (control) vs Dexamethasone",
      (sel(at, "geo_condition").value, sel(at, "geo_ref").value, sel(at, "geo_test").value) == ("treatment", "Untreated", "Dexamethasone"),
      (sel(at, "geo_condition").value, sel(at, "geo_ref").value, sel(at, "geo_test").value))
sel(at, "geo_ref").select("Albuterol").run()
check("2c. reference changed: tested level still valid, 1 sample only -> error",
      sel(at, "geo_test").value != "Albuterol" and any("At least 2 samples" in e.value for e in at.error), texts(at.error))
sel(at, "geo_condition").select("treatment").run(); sel(at, "geo_ref").select("Untreated").run(); sel(at, "geo_test").select("Dexamethasone").run()
check("3. 3 Untreated + 2 Dexamethasone selected, log2FC Dex vs Untreated",
      any("3 × Untreated (control), 2 × Dexamethasone (treatment) — log2FC = Dexamethasone vs Untreated" in c.value for c in at.caption), texts(at.caption))
check("3. index: only gapped (ready) proposed", at.radio(key="geo_index").options == ["gapped"], at.radio(key="geo_index").options)
check("3. download size shown", any("To download:" in c.value for c in at.caption))
btn(at, "Download, align and count").click().run()
check("4. task started, no button left", any("in progress" in i.value for i in at.info) and not any("Download, align" in b.label for b in at.button), texts(at.info))
wait_jobs(); at.run()
job = oj.list_jobs(JOBS)[0]
check("5. task finished", job["status"] == "done", oj.log_tail(JOBS, job["id"], 60))
check("5. log: 2 steps, download verified", "[1/2] Downloading" in oj.log_tail(JOBS, job["id"], 400)
      and "[2/2] Alignment" in oj.log_tail(JOBS, job["id"], 400))
study = PROJ / "studies" / "study_GSE77"
check("5. upstream Snakemake in the study folder (no conflict with the pipeline)", (study / "upstream" / ".snakemake").is_dir())
check("6. counts ready + QC shown", any("Counts ready: 5 samples" in s.value for s in at.success) and any("fragments" in d.value.columns for d in at.dataframe),
      texts(at.success))
btn(at, "Use these counts for the analysis").click().run()
check("7. counts loaded in the project, selection pre-filled", (PROJ / "expression_input.tsv").read_text().startswith("gene\tGSM1\tGSM2\tGSM3\tGSM4\tGSM5")
      and at.session_state["ctrl_samples"] == ["GSM1", "GSM3", "GSM5"] and at.session_state["trt_samples"] == ["GSM2", "GSM4"])
cov = at.selectbox(key="covariate_choice")
check("8. cell_line covariate proposed (not title/condition)", cov.options == ["(none)", "cell_line"], cov.options)
cov.select("cell_line").run(); btn(at, "Run and certify the analysis").click().run()
rd = Path(at.session_state.get("results_dir", ""))
check("9. analysis run with the covariate", rd.name.endswith("_cov_cell_line") and "covariate=cell_line" in (rd / "analysis_parameters.txt").read_text(),
      texts(at.error))
do = json.loads((rd / "data_origin.json").read_text()) if (rd / "data_origin.json").exists() else {}
check("9b. upstream provenance certified (GSE77, index fingerprint, ENA md5)", do.get("study", {}).get("accession") == "GSE77"
      and len(do.get("reference", {}).get("index_fingerprint", "")) == 64 and do.get("certified_link", {}).get("matches_data_origin")
      and "data_origin" in [c["id"] for c in json.loads((rd / "bundle_manifest.json").read_text())["components"]], list(do))

at.multiselect(key="geo_samples_GSE77_treatment_Untreated_Dexamethasone").unselect("GSM5").run()
check("10. other selection: information + run button", any("another selection" in i.value for i in at.info)
      and any("Download, align and count" in b.label for b in at.button), texts(at.info))
t0 = time.time(); btn(at, "Download, align and count").click().run(); wait_jobs(); at.run()
log = oj.log_tail(JOBS, oj.list_jobs(JOBS)[0]["id"], 400)
check("11. second run: FASTQ already there, alignments reused (only the merge redone)", oj.list_jobs(JOBS)[0]["status"] == "done"
      and "rule align:" not in log and "rule merge:" in log, log[-1500:])
check("11. counts for 4 samples", any("Counts ready: 4 samples" in s.value for s in at.success), texts(at.success))

pp = study / "upstream" / "gapped_gencode48" / "upstream_provenance.json"; good = pp.read_text()
pj = json.loads(good); pj["design"].pop("group_coding"); pp.write_text(json.dumps(pj)); at.run()
check("11b. counts from an older version -> warning, no load button", any("older version" in x.value for x in at.warning)
      and not any("Use these counts" in b.label for b in at.button), texts(at.warning))
pp.write_text(good)

at.text_input(key="ref_release").input("47").run()
check("12. release without index -> message, no run", any("No index ready for GENCODE v47" in x.value for x in at.warning)
      and not any("Download, align" in b.label for b in at.button), texts(at.warning))
check("13. no exception", not at.exception, texts(at.exception))
srv.shutdown()
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
