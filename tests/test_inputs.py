"""Input audit: what comes from outside (typed accession, GEO / ENA answers, file names, condition levels,
crate content) can neither leave the expected folders nor be reinterpreted.
Fast: no R, no network.   Usage: python tests/test_inputs.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import omicchain_names as onm, omicchain_fetch as of, omicchain_upstream as ou, omicchain_local as ol
import omicchain_design as odz, omicchain_replay as orp, omicchain_verify as ov
import pandas as pd

TMP = Path(tempfile.mkdtemp(prefix="inputs test "))
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"\n      -> {str(detail)[-1500:]}"))
    if not cond: FAILED.append(name)
def raises(exc, fn, *a, **k):
    try: fn(*a, **k)
    except exc as e: return str(e) or True
    return False

ok = ["GSM1275862", "SRR1039508", "d1_L001", "sample 1", "KO+dox (rep2)", "éch_1", "a.b-c_d"]
bad = ["", " x", "x ", "a/b", "a\\b", "..", "../x", ".hidden", "-rf", "a,b", "a;b", "a\tb", "a\nb", "a:b", "a*b", 'a"b', "a<b", "a|b",
       "CON", "nul.txt", "x.", "a" * 151, None, 12]
check("1. identifiers accepted (spaces, accents, +, parentheses)", all(onm.id_problem(x) is None for x in ok), [(x, onm.id_problem(x)) for x in ok])
check("1. identifiers refused (separators, .., comma, control characters, reserved names, too long)", all(onm.id_problem(x) for x in bad),
      [x for x in bad if not onm.id_problem(x)])
check("1. accessions accepted", all(onm.accession_problem(a) is None for a in ["GSE52778", "SRP033351", "ERP000001", "PRJNA229998", "PRJEB1234"]))
bad_acc = ["GSE52778/../../x", "GSE", "GSE12 ", "gse12", "GSE12;rm", "PRJNA", "../GSE1", "GSE1\n", "SRR123", "GSE1?x=1", ""]
check("1. accessions refused", all(onm.accession_problem(a) for a in bad_acc), [a for a in bad_acc if not onm.accession_problem(a)])
paths_ok = ["code/Snakefile", "data_origin.json", "inputs/metadata.tsv", "top_genes.tsv"]
paths_bad = ["../x", "code/../../x", "/etc/passwd", "C:/x", "C:\\x", "code\\x", "code//x", "code/./x", "", "a/..", "x\x00y", "a:b/c"]
check("1. paths inside a crate: accepted / refused", all(onm.relpath_problem(p) is None for p in paths_ok)
      and all(onm.relpath_problem(p) for p in paths_bad), [p for p in paths_bad if not onm.relpath_problem(p)])
check("1. annotation value: tabs and line breaks removed", onm.clean_value(" a\tb\r\nc ") == "a b c")

calls = []
real_get = of.http_get
of.http_get = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("network request"))
for a in ("GSE1/../../x", "GSE12;rm -rf", "https://evil/GSE1", "SRR123"):
    r = raises(of.FetchError, of.resolve, a)
    check(f"2. resolve({a!r}) refused without a request", r and not calls, (r, calls))
class R:
    def __init__(self, text): self.text, self.content, self.url = text, text.encode(), "u"
def ena(rows):
    cols = ["run_accession", "fastq_ftp", "fastq_md5", "fastq_bytes"]
    return R("\t".join(cols) + "\n" + "".join("\t".join(r.get(c, "") for c in cols) + "\n" for r in rows))
of.http_get = lambda *a, **k: ena([{"run_accession": "../../evil", "fastq_ftp": "h/x_1.fastq.gz"}])
check("2. unexpected run in the ENA answer -> refused", "unexpected" in str(raises(of.FetchError, of.resolve, "PRJNA1")))
check("2. unexpected file name (ENA) -> refused", raises(of.FetchError, of.run_files, {"fastq_ftp": "host/dir/..", "fastq_md5": "x"})
      and raises(of.FetchError, of.run_files, {"fastq_ftp": "host/dir/a,b.fastq.gz", "fastq_md5": "x"}))
check("2. normal file name (ENA) -> accepted", [f["name"] for f in of.run_files(
    {"fastq_ftp": "h/SRR1/SRR1_1.fastq.gz;h/SRR1/SRR1_2.fastq.gz", "fastq_md5": "a;b", "fastq_bytes": "1;2"})] == ["SRR1_1.fastq.gz", "SRR1_2.fastq.gz"])
of.http_get = real_get

smk = TMP / "smk"; smk.mkdir()
(smk / "Snakefile").write_text(
    "import sys, json\nsys.path.insert(0, %r)\nimport omicchain_upstream as ou\n"
    "c = ou._text_config(config)\nprint('CFG ' + json.dumps({k: c.get(k) for k in ('study', 'reference_level', 'test_level', 'samples', 'condition')}))\n"
    "rule all:\n    input: []\n" % str(ROOT))
def roundtrip(cfg):
    args = ou.write_config(smk / "wd", cfg)
    r = subprocess.run([sys.executable, "-m", "snakemake", "-n", "-s", str(smk / "Snakefile"), "--directory", str(smk / "wd"), *args],
                       capture_output=True, text=True)
    line = next((l for l in (r.stdout + r.stderr).splitlines() if l.startswith("CFG ")), None)
    return json.loads(line[4:]) if line else r.stdout[-800:] + r.stderr[-800:]
nasty = {"study": str(TMP / "run #2" / "my study: v2"), "condition": "time", "reference_level": "0", "test_level": "007",
         "samples": ["1", "2.0", "No", "a b", "x=y", "#3"]}
got = roundtrip(nasty)
check("3. configuration file: levels '0' / '007', path with '#' and ': ', samples '1' 'No' '#3' unchanged", got == nasty, got)
for lv in ("1e3", "yes", "null", "a: b", "[x]", "{a}", "- a", "'q'", "true", "1.0"):
    got = roundtrip({**nasty, "test_level": lv})
    if not (isinstance(got, dict) and got["test_level"] == lv):
        check(f"3. level {lv!r} kept", False, got); break
else:
    check("3. levels '1e3', 'yes', 'null', 'a: b', '[x]', '- a', 'true', '1.0' kept exactly", True)
r = subprocess.run([sys.executable, "-m", "snakemake", "-n", "-s", str(smk / "Snakefile"), "--directory", str(smk / "wd2"), "--config",
                    "reference_level=0", "test_level=24", "condition=time"], capture_output=True, text=True)
line = next((l for l in (r.stdout + r.stderr).splitlines() if l.startswith("CFG ")), "CFG {}")
check("3. --config typed by hand: a numeric level (0, 24) becomes text again", json.loads(line[4:]).get("reference_level") == "0"
      and json.loads(line[4:]).get("test_level") == "24", line)
check("3. --config misread (list / dictionary): clear error", "misread" in str(raises(ou.UpstreamError, ou._text_config, {"test_level": {"a": "b"}})))
samples = {s: {"sample": s, "time": t} for s, t in (("a", "0"), ("b", "0"), ("c", "24"), ("d", "24"))}
check("3. select_samples: levels '0' / '24', list of samples (names unchanged)", ou.select_samples(samples, "time", "0", "24", ["a", "b", "c", "d"]) == list("abcd")
      and ou.select_samples(samples, "time", "0", "24", "a, b,c,d") == list("abcd"))
cmd = odz.upstream_command(odz.upstream_config(r"C:\data\run #2\study_GSE1", "time", "0", "-KO"), windows=True)
check("3. displayed command: omicchain_upstream.py run, --option=value form (level '-KO', path with #)",
      "omicchain_upstream.py run" in cmd and "--test-level=-KO" in cmd and "--reference-level=0" in cmd and "--config" not in cmd, cmd)
import argparse
ap = argparse.ArgumentParser(); ap.add_argument("--test-level"); ap.add_argument("--samples")
ns = ap.parse_args(["--test-level=-KO", "--samples=-x,y"])
check("3. ... argparse reads '-KO' and '-x,y' as values", ns.test_level == "-KO" and ns.samples == "-x,y")

d = TMP / "fq"; d.mkdir()
for n in ("good 1_R1.fastq.gz", "good 1_R2.fastq.gz", "bad,name_R1.fastq.gz", "bad,name_R2.fastq.gz", "-dash.fastq.gz"):
    (d / n).write_bytes(b"x")
sc = ol.scan(d)
check("4. FASTQ name with a comma or a leading dash: reported (rename); name with a space: accepted",
      any("bad,name" in p and "rename" in p for p in sc["problems"]) and any("-dash" in p for p in sc["problems"])
      and not any("good 1" in p for p in sc["problems"]), sc["problems"])

study = TMP / "st"; study.mkdir()
(study / "samples.tsv").write_text("sample\ttitle\nA\ta\nB\tb\n")
t, info = odz.load_annotations(study); info["user_columns"] = ["condition"]; t["condition"] = ["ctrl\tx", "trt\nline2"]
odz.save_annotations(study, t, info)
back = pd.read_csv(study / "samples_annotated.tsv", sep="\t", dtype=str)
check("5. annotation with a tab / line break: one value per cell, table read back identically",
      list(back["condition"]) == ["ctrl x", "trt line2"] and list(back.columns) == ["sample", "title", "condition"]
      and len((study / "samples_annotated.tsv").read_text().splitlines()) == 3, back)

def origin(**over):
    o = {"schema": "omicchain.upstream/1", "study": {"accession": "GSE1"},
         "design": {"condition_column": "c", "reference_level": "a", "test_level": "b", "samples": ["S1", "S2"]},
         "inputs": [{"sample": "S1", "run": "SRR1", "fastq": ["SRR1_1.fastq.gz"], "md5": ["0" * 32], "md5_source": "ENA"}],
         "reference": {"gencode_release": "48", "index_type": "gapped"}}
    for k, v in over.items():
        node = o
        *path, last = k.split("__")
        for part in path: node = node[int(part) if part.isdigit() else part]
        node[last] = v
    return o
check("6. normal provenance accepted", not raises(orp.ReplayError, orp.check_origin, origin()))
for label, o in (("run '../../x'", origin(inputs__0__run="../../x")), ("FASTQ '/etc/passwd'", origin(inputs__0__fastq=["/etc/passwd"])),
                 ("sample 'a/b'", origin(design__samples=["a/b", "S2"])), ("release '48/../..'", origin(reference__gencode_release="48/../..")),
                 ("index 'x; rm'", origin(reference__index_type="x; rm"))):
    check(f"6. provenance with {label}: replay refused before any file", "cannot be used safely" in str(raises(orp.ReplayError, orp.check_origin, o)))
raw = TMP / "raw"
r = raises(orp.ReplayError, orp.replay, origin(inputs__0__run="../../x"), TMP / "none.tsv", raw)
check("6. replay(): refused at the start (names), no file written", "cannot be used safely" in str(r) and not (TMP.parent / "x").exists()
      and not [p for p in raw.rglob("*") if p.is_file()], r)

good = {"bundle_hash": "a" * 64, "components": [{"id": "code", "label": "Snakefile", "crate_path": "code/Snakefile", "sha256": "b" * 64}]}
check("7. normal manifest accepted", ov._check_bundle(good) is good)
for label, b in (("path 'code/../../evil'", {**good, "components": [{**good["components"][0], "crate_path": "code/../../evil"}]}),
                 ("absolute path", {**good, "components": [{**good["components"][0], "crate_path": "/etc/passwd"}]}),
                 ("Windows path", {**good, "components": [{**good["components"][0], "crate_path": "C:\\Windows\\x"}]}),
                 ("malformed component", {**good, "components": ["x"]}), ("fingerprint not SHA-256", {**good, "bundle_hash": "../x"}),
                 ("no components", {"bundle_hash": "a" * 64})):
    check(f"7. manifest with {label}: refused", raises(ov.VerifyError, ov._check_bundle, b))
check("7. normal parameters accepted", not raises(ov.VerifyError, ov._check_parameters, {"padj_threshold": "0.05", "lfc_threshold": "1.0", "covariate": "cell_line"}))
for label, p in (("padj '../../x'", {"padj_threshold": "../../x", "lfc_threshold": "1"}), ("lfc 'nan'", {"padj_threshold": "0.05", "lfc_threshold": "nan"}),
                 ("lfc '1; rm'", {"padj_threshold": "0.05", "lfc_threshold": "1; rm"}), ("covariate '../x'", {"padj_threshold": "0.05", "lfc_threshold": "1", "covariate": "../x"}),
                 ("covariate 'a b'", {"padj_threshold": "0.05", "lfc_threshold": "1", "covariate": "a b"})):
    check(f"7. parameters with {label}: refused", raises(ov.VerifyError, ov._check_parameters, p))

print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
