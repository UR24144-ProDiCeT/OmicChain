"""Data origin (data_origin) certified with the analysis, replayed by verify and described in the certificate;
without it the fingerprint is unchanged.  Usage: python tests/test_data_origin.py (mock Rscript)"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
PROJ = Path(tempfile.mkdtemp()) / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
os.environ["PATH"] = str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]
for k in ("MOCK_NOISE", "MOCK_DESEQ2_VERSION", "MOCK_FAIL"): os.environ.pop(k, None)
sys.path.insert(0, str(PROJ))
import omicchain_core as occ, omicchain_verify as ov, certificate_utils as cu
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-1500:]}"))
    if not cond: FAILED.append(name)
def snake(*cfg):
    return subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0", *cfg],
                          cwd=PROJ, capture_output=True, text=True)
R = PROJ / "results" / "padj_0.05_lfc_1.0"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

r = snake(); assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
plain = (R / "bundle_hash.txt").read_text()
old_manifest = json.loads((PROJ / "omicchain.json").read_text())
old_manifest["components"] = [c for c in old_manifest["components"] if c["id"] != "data_origin"]
(PROJ / "old_manifest.json").write_text(json.dumps(old_manifest))
legacy = occ.certify(PROJ / "old_manifest.json", R, PROJ, stage=False, write_crate=False)["bundle_hash"]
r = snake("--forcerun", "certify"); assert r.returncode == 0
check("0. without data_origin: same fingerprint as with the old manifest", legacy == plain == (R / "bundle_hash.txt").read_text(), (legacy, plain))

origin = {"schema": "omicchain.matrix/1", "source": {"original_file": "my counts.tsv", "original_sha256": "ab" * 32, "genes": 2,
          "samples": 4, "rounded_values": False}, "user_annotations": {"user_columns": ["condition"], "rules": [{"x": 1}],
          "imported_sheets": [{"file": "design.tsv"}], "edited_by_hand": False},
          "design": {"condition_column": "condition", "reference_level": "ctrl", "test_level": "trt", "samples": ["ctrl_1", "ctrl_2", "trt_1", "trt_2"]},
          "outputs": {"expression_input.tsv": sha(PROJ / "expression_input.tsv")}}
(PROJ / "data_origin.json").write_text(json.dumps(origin))
r = snake()
check("1. pipeline with data_origin: OK", r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:])
bm = json.loads((R / "bundle_manifest.json").read_text())
ids = [c["id"] for c in bm["components"]]
check("1. data_origin component certified, after 'data'", ids.index("data_origin") == ids.index("data") + 1, ids)
with_origin = (R / "bundle_hash.txt").read_text()
check("1. fingerprint different from the analysis without data origin", with_origin != plain)
cert = json.loads((R / "data_origin.json").read_text())
check("1. certified link with the analysed table", cert["certified_link"] == {"expression_input_sha256": sha(PROJ / "expression_input.tsv"), "matches_data_origin": True})
crate_meta = (R / "ro-crate-metadata.json").read_text()
check("1. data_origin.json in the RO-Crate", "data_origin.json" in crate_meta)

ZIP = occ.make_crate_zip(R)
REV = Path(tempfile.mkdtemp()) / "rev"; REV.mkdir()
shutil.copy(PROJ / "expression_input.tsv", REV); shutil.copy(PROJ / "metadata.tsv", REV)
rep = ov.rerun_verify(ZIP, expression=REV / "expression_input.tsv", metadata=REV / "metadata.tsv", expected=with_origin, project=PROJ, log=lambda *a: None)
check("2. verify: REPRODUCED with data_origin", rep["verdict"] == "REPRODUCED", (rep.get("verdict"), rep.get("log_tail")))

desc = (PROJ / "DESCRIPTION").read_text()
(PROJ / "DESCRIPTION").write_text(desc.rstrip() + ",\n    NewPackageAddedLater\n")
rep = ov.rerun_verify(ZIP, expression=REV / "expression_input.tsv", metadata=REV / "metadata.tsv", expected=with_origin, project=PROJ, log=lambda *a: None)
check("2b. DESCRIPTION extended afterwards: verify REPRODUCED (snapshot on the packages of the certified lock)", rep["verdict"] == "REPRODUCED",
      (rep.get("verdict"), rep.get("environment_changes")))
(PROJ / "DESCRIPTION").write_text(desc)

orig_expr = (PROJ / "expression_input.tsv").read_text()
(PROJ / "expression_input.tsv").write_text(orig_expr.replace("80", "81"))
r = snake()
check("3. expression table different from the described one -> explicit failure", r.returncode != 0 and "not the expression table described by data_origin.json" in r.stdout + r.stderr,
      r.stdout[-800:] + r.stderr[-800:])
(PROJ / "expression_input.tsv").write_text(orig_expr)

r = snake(); assert r.returncode == 0 and (R / "data_origin.json").exists(), r.stdout[-800:]
(PROJ / "data_origin.json").unlink()
r = snake()
check("4. without data origin: original fingerprint, no data_origin left in the results", r.returncode == 0 and (R / "bundle_hash.txt").read_text() == plain
      and not (R / "data_origin.json").exists(), r.stdout[-800:])

rows = dict(cu.data_origin_rows(origin))
check("5. matrix: source, comparison, annotation", "my counts.tsv" in rows["Source"] and rows["Comparison"].startswith("condition: trt vs ctrl")
      and "design.tsv" in rows["Sample annotation (user)"], rows)
up = {"schema": "omicchain.upstream/1", "study": {"accession": "GSE52778", "user_annotations": None},
      "inputs": [{"run": "SRR1", "fastq": ["a_1", "a_2"], "md5_source": "ENA"}],
      "reference": {"gencode_release": "48", "index_type": "gapped", "index_fingerprint": "f20e9bb8" * 8},
      "alignment": {"rsubread_version": "2.24.0", "type": "rna", "paired_end": True},
      "counting": {"strand_specific": 0, "strand_mode": "detected", "gene_symbols": 77266},
      "design": {"condition_column": "treatment", "reference_level": "Untreated", "test_level": "Dexamethasone", "samples": list("abcdefgh")},
      "warnings": ["SRR1039513: the FASTQ files hold 16823088 fragments", "x"]}
rows = dict(cu.data_origin_rows(up))
check("5. public study: accession, reference + index fingerprint, Rsubread, warnings", "Public study GSE52778" in rows["Source"]
      and "f20e9bb8f20e9bb8" in rows["Reference"] and "Rsubread 2.24.0" in rows["Alignment and counting"] and rows["Warnings"].startswith("2:"), rows)
up["inputs"][0]["md5_source"] = "local (computed by OmicChain)"
check("5. local FASTQ: user source", "provided by the user" in dict(cu.data_origin_rows(up))["Source"])
pdf = PROJ / "c.pdf"
cu.generate_certificate_pdf(output_path=pdf, bundle_hash=with_origin, network="Base Sepolia", explorer="https://x", tx_hash="0x" + "1" * 64,
                            block_number=1, blockchain_timestamp=1700000000, proof_contract="0x" + "2" * 40, publisher_address="0x" + "3" * 40,
                            components=bm["components"], analysis_parameters={"padj_threshold": "0.05"}, data_origin=up)
txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
check("5. PDF: Data origin section", "Data origin" in txt and "GSE52778" in txt and "GENCODE v48" in txt, txt[:1500])
up["inputs"] = [{"sample": "GSM1", "run": "SRR1", "fastq": ["SRR1_1.fastq.gz", "SRR1_2.fastq.gz"], "md5": ["a" * 32, "b" * 32],
                 "md5_source": "ENA"}]
up["reference"].update(genome_sha256="c" * 64, gtf_sha256="d" * 64)
up["outputs"] = {"expression_input.tsv": "e" * 64}
cu.generate_certificate_pdf(output_path=pdf, bundle_hash=with_origin, network="Base Sepolia", explorer="https://x", tx_hash="0x" + "1" * 64,
                            block_number=1, blockchain_timestamp=1700000000, proof_contract="0x" + "2" * 40, publisher_address="0x" + "3" * 40,
                            components=bm["components"], analysis_parameters={"padj_threshold": "0.05"}, data_origin=up)
txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
pages = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
origin_sha = next(c["sha256"] for c in bm["components"] if c["id"] == "data_origin")
check("6. annex: 2nd page, every FASTQ with its md5, reference, analysed table, data_origin fingerprint",
      "Pages:           2" in pages and "ANNEX" in txt and "SRR1_2.fastq.gz" in txt and "b" * 32 in txt and "c" * 64 in txt
      and "e" * 64 in txt and origin_sha in txt.replace("\n", "").replace(" ", ""), txt[-2500:])
up["tool"] = {"name": "omicchain_upstream", "version": "0.2", "code_sha256": {"upstream.smk": "7" * 64, "upstream_count.R": "8" * 64}}
t = cu.raw_data_tables(up)
check("6. annex: fingerprint of the upstream code", t["code"] == [["upstream.smk", "7" * 64], ["upstream_count.R", "8" * 64]] and "0.2" in t["code_version"], t.get("code"))
up["inputs"][0].update(md5_source="local (computed by OmicChain)", sha256=["f" * 64, "9" * 64])
t = cu.raw_data_tables(up)
check("6. local FASTQ: SHA-256 in the annex, command with --fastq-dir", "9" * 64 in t["files"][1][3] and "--fastq-dir" in t["check"], t)
t = cu.raw_data_tables(origin)
check("6. matrix: original file + SHA-256, command with --matrix", t["files"][0][0] == "my counts.tsv" and t["files"][0][2] == "ab" * 32
      and "--matrix" in t["check"], t)
cu.generate_certificate_pdf(output_path=pdf, bundle_hash=plain, components=bm["components"])
check("6. without data origin: a single page", "Pages:           1" in subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout)
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
