"""omicchain_reference.py: mock GENCODE (local HTTP) + real Rsubread.  Usage: python tests/test_reference.py
Needs R with Rsubread (R_LIBS variable if needed)."""
import gzip, hashlib, http.server, json, os, random, shutil, subprocess, sys, tempfile, threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REF = HERE.parent / "omicchain_reference.py"
ROOT = Path(tempfile.mkdtemp(prefix="ref test "))
SRV = ROOT / "srv" / "release_48"; SRV.mkdir(parents=True)
random.seed(1)
seq = "".join(random.choice("ACGT") for _ in range(400_000))
fa = ">chr1\n" + "\n".join(seq[i:i + 60] for i in range(0, len(seq), 60)) + "\n>chr2\n" + "\n".join(seq[i:i + 60] for i in range(0, 120_000, 60)) + "\n"
GENOME = gzip.compress(fa.encode()); GTF = gzip.compress(b'chr1\tt\texon\t100\t900\t.\t+\t.\tgene_id "G1"; transcript_id "T1";\n')
(SRV / "GRCh38.primary_assembly.genome.fa.gz").write_bytes(GENOME)
(SRV / "gencode.v48.primary_assembly.annotation.gtf.gz").write_bytes(GTF)
md5 = lambda b: hashlib.md5(b).hexdigest()
def write_md5sums(genome_md5=None):
    (SRV / "MD5SUMS").write_text(f"{genome_md5 or md5(GENOME)}  GRCh38.primary_assembly.genome.fa.gz\n"
                                 f"{md5(GTF)}  gencode.v48.primary_assembly.annotation.gtf.gz\n{'0'*32}  autre.txt\n")
write_md5sums()

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(ROOT / "srv"), **k)
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
CACHE = ROOT / "cache dir"
env = dict(os.environ, OMICCHAIN_GENCODE_DIR=f"http://127.0.0.1:{srv.server_address[1]}/release_{{release}}")
env.pop("OMICCHAIN_REF_DIR", None)
def ref(*args, extra_env=None):
    r = subprocess.run([sys.executable, str(REF), "--ref-dir", str(CACHE), *args], env=dict(env, **(extra_env or {})),
                       capture_output=True, text=True, timeout=900, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"\n      -> {str(detail)[-1500:]}"))
    if not cond: FAILED.append(name)
D = CACHE / "gencode_v48"
man = lambda: json.loads((D / "reference.json").read_text())

rc, out = ref("prepare", "--index", "gapped")
check("1. prepare gapped: code 0", rc == 0, out)
m = man(); g = m["indexes"].get("gapped", {})
check("1. sources: GENCODE md5 + sha256 recorded", m["sources"]["genome"]["md5"] == md5(GENOME) and len(m["sources"]["gtf"]["sha256"]) == 64, m.get("sources"))
check("1. index: fingerprint, Rsubread version, .files/.log excluded",
      len(g.get("fingerprint", "")) == 64 and g.get("rsubread_version") and ".files" not in g.get("files_sha256", {})
      and ".reads" in g.get("files_sha256", {}), g)
check("1. no .building folder and no temporary file left",
      not list(D.glob("*.building")) and not list(D.rglob("subread-index-sam-*")) and not list(ROOT.glob("subread-index-sam-*")),
      list(D.rglob("*")))
check("1. build log written", (D / "build_gapped.log").stat().st_size > 0)

mt = (D / "index_gapped" / "GRCh38.reads").stat().st_mtime
rc, out = ref("prepare", "--index", "gapped")
check("2. second run: already prepared, nothing rebuilt", rc == 0 and "already prepared" in out and (D / "index_gapped" / "GRCh38.reads").stat().st_mtime == mt, out)

rc, out = ref("prepare", "--index", "full"); fp1 = man()["indexes"]["full"]["fingerprint"]
rc2, out2 = ref("prepare", "--index", "full", "--rebuild"); fp2 = man()["indexes"]["full"]["fingerprint"]
check("3. full built twice -> same fingerprint", rc == 0 and rc2 == 0 and fp1 == fp2, out + out2)
check("3. gapped and full have different fingerprints", fp1 != man()["indexes"]["gapped"]["fingerprint"])

other = ROOT / "Point9 Rsubread" / "full_work" / "index_full"; other.mkdir(parents=True)
(other.parent / "genome.fa.gz").write_bytes(GENOME)
r = subprocess.run(["Rscript", str(HERE.parent / "build_index.R"), str(other.parent / "genome.fa.gz"), str(other / "GRCh38"), "full",
                    str(other / "r.dcf")], cwd=other.parent, capture_output=True, text=True, env=env)
(other / "r.dcf").unlink()
shutil.rmtree(D / "index_full")
rc, out = ref("prepare", "--index", "full", "--import-index", str(other / "GRCh38"), "--rsubread-version", "2.24.0")
e = man()["indexes"]["full"]
check("4. import: code 0, same fingerprint as a local build", rc == 0 and e["fingerprint"] == fp1, out)
check("4. import: origin and declared version recorded", e["origin"]["method"] == "imported" and e["rsubread_version"] == "2.24.0", e)

shutil.rmtree(D / "index_full")
rc, out = ref("prepare", "--index", "full", "--import-index", str(other / "GRCh38"), "--in-place")
e = man()["indexes"]["full"]
check("4b. in place: same fingerprint, original path, no copy in the cache",
      rc == 0 and e["fingerprint"] == fp1 and e["origin"]["method"] == "referenced" and Path(e["path"]) == (other / "GRCh38").resolve()
      and not (D / "index_full").exists(), out)
rc, out = ref("prepare", "--index", "full")
check("4b. second run: recognised as already prepared (no rebuild)", rc == 0 and "already prepared" in out, out)
rc, out = ref("verify", "--index", "full")
check("4b. verify works on the referenced index", rc == 0 and "OK   index full" in out, out)
rc, out = ref("prepare", "--index", "full", "--import-index", str(ROOT / "nowhere" / "GRCh38"), "--in-place")
check("4b. path without index -> clear error", rc != 0 and "no Rsubread index" in out, out)

rc, out = ref("verify")
check("5. verify: all OK", rc == 0 and "Reference intact" in out, out)
(other / "GRCh38.files").write_text("changed")
rc, out = ref("verify", "--index", "full")
check("5. .files changed -> still OK (excluded)", rc == 0, out)
p = D / "index_gapped" / "GRCh38.reads"; b = bytearray(p.read_bytes()); b[0] ^= 1; p.write_bytes(bytes(b))
rc, out = ref("verify")
check("5. one byte changed in the gapped index -> BAD, code 1", rc == 1 and "BAD  index gapped" in out and "OK   index full" in out, out)
p = D / "gencode.v48.primary_assembly.annotation.gtf.gz"; p.write_bytes(p.read_bytes() + b"x")
rc, out = ref("verify", "--index", "full")
check("5. GTF changed -> BAD", rc == 1 and "BAD  gencode.v48" in out, out)

write_md5sums(genome_md5="1" * 32)
rc, out = ref("--ref-dir", str(ROOT / "cache2"), "prepare", "--index", "gapped")
check("6. wrong GENCODE md5 -> refused, nothing accepted", rc == 1 and "md5" in out and
      not (ROOT / "cache2" / "gencode_v48" / "GRCh38.primary_assembly.genome.fa.gz").exists(), out)
write_md5sums()

fake = ROOT / "fake_rscript.py"; fake.write_text("import sys; print('boom'); sys.exit(3)\n")
wrapper = ROOT / ("fake_rscript.bat" if os.name == "nt" else "fake_rscript.sh")
wrapper.write_text(f'@"{sys.executable}" "{fake}" %*\n' if os.name == "nt" else f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n'); wrapper.chmod(0o755)
c3 = ROOT / "cache3"
rc, out = ref("--ref-dir", str(c3), "prepare", "--index", "gapped", extra_env={"OMICCHAIN_RSCRIPT": str(wrapper)})
m3 = json.loads((c3 / "gencode_v48" / "reference.json").read_text())
check("7. build failure -> code 1, no index recorded and no final folder",
      rc != 0 and "gapped" not in m3["indexes"] and not (c3 / "gencode_v48" / "index_gapped").exists(), out)

rc, out = ref("show")
check("8. show lists both indexes", rc == 0 and "index gapped" in out and "index full" in out and "referenced" in out, out)

srv.shutdown(); shutil.rmtree(ROOT, ignore_errors=True)
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
