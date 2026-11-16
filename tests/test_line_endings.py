"""Every text file that OmicChain writes, and that may be hashed, has Unix line endings on every
system: Python writes with newline="\\n", R writes through binary connections. Otherwise the same
analysis certified on Windows and re-run on Linux would differ by its line endings alone.
Static check of the source code; also writes a few certified files and looks at their bytes.
Usage: python tests/test_line_endings.py"""
import ast, json, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
fails = []


def check(label, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f"   -> {detail}"))
    if not ok:
        fails.append(label)


def python_writes(src):
    bad = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.Call):
            continue
        kws = {k.arg for k in n.keywords}
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr == "write_text" and "newline" not in kws:
            bad.append(n.lineno)
        if isinstance(f, ast.Name) and f.id == "open" and "newline" not in kws:
            mode = n.args[1].value if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else None
            for k in n.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            if isinstance(mode, str) and ("w" in mode or "a" in mode) and "b" not in mode:
                bad.append(n.lineno)
    return bad


bad = {p.name: python_writes(p.read_text(encoding="utf-8")) for p in sorted(ROOT.glob("*.py"))}
bad = {k: v for k, v in bad.items() if v}
check("1. Python modules: every text file written with newline='\\n'", not bad, bad)

wf = {}
for p in [ROOT / "upstream.smk"] + [q for q in (ROOT / "Snakefile", ROOT / "Snakefile.smk") if q.is_file()]:
    lines = [i + 1 for i, l in enumerate(p.read_text(encoding="utf-8").splitlines())
             if (re.search(r"\bopen\([^)]*[\"']w[\"']", l) or ".write_text(" in l) and "newline=" not in l
             and not l.rstrip().endswith("({")]
    if lines:
        wf[p.name] = lines
for p in [ROOT / "upstream.smk"]:
    s = p.read_text(encoding="utf-8")
    for m in re.finditer(r"\.write_text\(", s):
        depth, i = 0, m.end() - 1
        while True:
            c = s[i]
            depth += c == "("
            depth -= c == ")"
            i += 1
            if depth == 0:
                break
        if "newline=" not in s[m.start():i]:
            wf.setdefault(p.name, []).append(s.count("\n", 0, m.start()) + 1)
check("2. Snakemake workflows: every text file written with newline='\\n'", not wf, wf)

rbad = {}
for p in sorted(ROOT.glob("*.R")):
    for i, l in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"\b(writeLines|write\.table|write\.csv|write\.delim)\(", l):
            target = re.sub(r"^.*?\b(writeLines|write\.table|write\.csv|write\.delim)\(", "", l)
            if not re.search(r"\bcon\b|file = con|, con,|, con\)", target):
                rbad.setdefault(p.name, []).append(i)
check("3. R scripts: tables and text written through a binary connection", not rbad, rbad)

import omicchain_core as occ
tmp = Path(tempfile.mkdtemp())
(tmp / "expression_input.tsv").write_bytes(b"gene\ta\nG\t1\n")
origin = {"outputs": {"expression_input.tsv": occ.sha256_file(tmp / "expression_input.tsv")}, "source": {"x": "y"}}
(tmp / "origin.json").write_text(json.dumps(origin), encoding="utf-8", newline="\n")
occ.write_data_origin(tmp / "origin.json", tmp / "expression_input.tsv", tmp / "res" / "data_origin.json")
raw = (tmp / "res" / "data_origin.json").read_bytes()
check("4. data_origin.json written with Unix line endings on this system", b"\r" not in raw and raw.count(b"\n") > 2)

print("\n" + ("ALL CHECKS PASS" if not fails else f"{len(fails)} CHECK(S) FAILED: {fails}"))
sys.exit(1 if fails else 0)
