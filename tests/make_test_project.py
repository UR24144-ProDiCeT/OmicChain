"""Build a complete test project (mock Rscript: R / DESeq2 not needed).
Usage: python tests/make_test_project.py /tmp/omicchain_testproj"""
import shutil, stat, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
dest = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/omicchain_testproj")
shutil.rmtree(dest, ignore_errors=True); dest.mkdir(parents=True)

for name in ("omicchain_core.py", "omicchain_identity.py", "certificate_utils.py", "OmicChain.py", "omicchain.json",
             "run_deseq2.R", "de_core.R", "run_exploration.R", "snapshot_renv.R", "contract_abi_proof.json",
             "omicchain_verify.py", "omicchain_chain.py", "omicchain_design.py", "omicchain_jobs.py", "omicchain_ui_data.py", "omicchain_reference.py", "omicchain_fetch.py", "build_index.R",
             "upstream.smk", "upstream_align.R", "upstream_count.R", "omicchain_upstream.py", "omicchain_local.py", "omicchain_matrix.py",
             "omicchain_explore.py", "omicchain_replay.py", "omicchain_ui_verify.py", "omicchain_ui_style.py", "omicchain_names.py", "omicchain_qc.py", "omicchain_history.py", "DESCRIPTION", "official_code.json"):
    shutil.copy(ROOT / name, dest / name)
shutil.copy(ROOT / ("Snakefile" if (ROOT / "Snakefile").is_file() else "Snakefile.smk"), dest / "Snakefile")
shutil.copytree(ROOT / ".streamlit", dest / ".streamlit")

up = ROOT / "identicon_utils.py"
if up.is_file():
    shutil.copy(up, dest / "identicon_utils.py")
else:
    (dest / "identicon_utils.py").write_text(
        "from PIL import Image\n"
        "def generate_blockies(h, grid_size=8, scale=32):\n"
        "    v = int(h[:6], 16); return Image.new('RGB', (grid_size*scale, grid_size*scale), (v>>16 & 255, v>>8 & 255, v & 255))\n"
        "def generate_identicon(h, output_path=None):\n"
        "    im = generate_blockies(h)\n"
        "    if output_path: im.save(output_path)\n"
        "    return im\n")

(dest / "renv").mkdir(); (dest / "renv" / "activate.R").write_text("# stub\n")
(dest / "expression_input.tsv").write_text("gene\tctrl_1\tctrl_2\ttrt_1\ttrt_2\nTP53\t10\t12\t80\t90\n")
(dest / "metadata.tsv").write_text("sample\tgroup\nctrl_1\tcontrol\nctrl_2\tcontrol\ntrt_1\ttreatment\ntrt_2\ttreatment\n")
(dest / "metadata_source.tsv").write_text("sample\tgroup\nctrl_1\tcontrol\nctrl_2\tcontrol\ntrt_1\ttreatment\ntrt_2\ttreatment\n")
(dest / "loaded_data.json").write_text('{"label": "test data", "counts_dir": "", "samples": 4, "provenance_file": null}')

mock = dest / "mockbin"; mock.mkdir()
(mock / "Rscript").write_text('''#!/usr/bin/env python3
import sys, os, json
from PIL import Image
script, args = sys.argv[1], sys.argv[2:]
if os.environ.get("MOCK_FAIL"):
    sys.exit("mock Rscript: simulated failure (" + script + ")")
if script == "run_deseq2.R":
    expr, meta, padj, lfc, outdir = args[:5]
    covariate = args[5] if len(args) > 5 else ""
    if covariate and covariate not in open(meta).readline().rstrip("\\n").split("\\t"):
        sys.exit("mock Rscript: covariate missing from metadata.tsv: " + covariate)
    os.makedirs(outdir, exist_ok=True)
    for n, c in (("heatmap.tiff","red"),("volcano.tiff","blue"),("dotplot_go.tiff","green")):
        Image.new("RGB", (120, 90), c).save(os.path.join(outdir, n))
    noise = float(os.environ.get("MOCK_NOISE", "0"))
    open(os.path.join(outdir, "top_genes.tsv"), "w").write(
        "Gene\\tlog2FoldChange\\tpadj\\nTP53\\t%r\\t0.0001\\nMYC\\t-2.25\\t0.002\\n" % (3.5 + noise + (0.25 if covariate else 0)))
    open(os.path.join(outdir, "report.pdf"), "wb").write(b"%PDF-fake")
elif script == "snapshot_renv.R":
    out = args[0]; os.makedirs(os.path.dirname(out), exist_ok=True)
    deseq2 = os.environ.get("MOCK_DESEQ2_VERSION", "1.50.2")
    like = os.environ.get("OMICCHAIN_SNAPSHOT_LIKE")
    if like and os.path.exists(like):
        names = list(json.load(open(like))["Packages"])
    else:
        proj = os.environ.get("OMICCHAIN_RENV_PROJECT", ".")
        desc = open(os.path.join(proj, "DESCRIPTION")).read() if os.path.exists(os.path.join(proj, "DESCRIPTION")) else ""
        names = ["DESeq2"] + [x.strip().rstrip(",") for x in desc.split("Imports:", 1)[-1].splitlines()[1:]
                              if x.startswith((" ", "\t")) and x.strip().rstrip(",") not in ("", "DESeq2")] if desc else ["DESeq2"]
    pk = {n: {"Version": deseq2 if n == "DESeq2" else "1.0.0"} for n in sorted(names)}
    json.dump({"R": {"Version": "4.5.1"}, "Packages": pk}, open(out, "w"), indent=2)
elif script == "run_exploration.R":
    import math
    out = args[2]; os.makedirs(os.path.dirname(out), exist_ok=True)
    lines = ["Gene\\tlog2FoldChange\\tpadj\\tpvalue"]
    for i in range(300):
        lfc = round(-3 + 6 * i / 299, 4)
        padj = "NA" if i % 37 == 0 else repr(min(1.0, 0.9 * math.exp(-2.2 * abs(lfc))))
        lines.append(f"G{i}\\t{lfc}\\t{padj}\\t{padj}")
    open(out, "w").write("\\n".join(lines) + "\\n")
elif os.environ.get("MOCK_REAL_RSCRIPT"):
    os.execv(os.environ["MOCK_REAL_RSCRIPT"], [os.environ["MOCK_REAL_RSCRIPT"]] + sys.argv[1:])
else:
    sys.exit("mock Rscript: unknown script " + script)
''')
(mock / "Rscript").chmod((mock / "Rscript").stat().st_mode | stat.S_IEXEC)
print(f"test project ready: {dest}")
