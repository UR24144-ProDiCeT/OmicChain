import os as _os

if not _os.path.exists("renv/activate.R"):
    raise Exception(
        "renv is not initialized in this project yet.\n"
        "Run this ONCE, at the project root, in R or Rscript:\n\n"
        "    install.packages('renv')\n"
        "    renv::init()\n\n"
        "Then re-run the pipeline. See the .Rprofile / renv/ folder this "
        "creates -- they should be committed alongside the Snakefile."
    )

PADJ_THRESHOLD = float(config.get("padj_threshold", 0.05))
LFC_THRESHOLD = float(config.get("lfc_threshold", 1.0))
COVARIATE = str(config.get("covariate", "") or "").strip()

def _fmt_threshold(x):
    x = float(x)
    return f"{x:.1f}" if x.is_integer() else f"{x:g}"

RESULTS_DIR = f"results/padj_{_fmt_threshold(PADJ_THRESHOLD)}_lfc_{_fmt_threshold(LFC_THRESHOLD)}"
if COVARIATE:
    RESULTS_DIR += f"_cov_{COVARIATE}"

import sys as _sys
_sys.path.insert(0, workflow.basedir)
import omicchain_core as occ

MANIFEST_PATH = "omicchain.json"
CERT_INPUTS = occ.certify_input_paths(occ.load_manifest(MANIFEST_PATH), RESULTS_DIR)
DATA_ORIGIN = _os.path.exists("data_origin.json")
if DATA_ORIGIN:
    CERT_INPUTS = CERT_INPUTS + [f"{RESULTS_DIR}/data_origin.json"]

rule all:
    input:
        f"{RESULTS_DIR}/heatmap.tiff",
        f"{RESULTS_DIR}/volcano.tiff",
        f"{RESULTS_DIR}/dotplot_go.tiff",
        f"{RESULTS_DIR}/combined_figures.png",
        f"{RESULTS_DIR}/analysis_parameters.txt",
        f"{RESULTS_DIR}/data_provenance.txt",
        f"{RESULTS_DIR}/renv.lock",
        f"{RESULTS_DIR}/bundle_manifest.json",
        f"{RESULTS_DIR}/bundle_hash.txt",
        f"{RESULTS_DIR}/ro-crate-metadata.json",
        f"{RESULTS_DIR}/identicon_blockies.png"


rule run_deseq2:
    input:
        expr="expression_input.tsv",
        meta="metadata.tsv",
        script="run_deseq2.R",
        core="de_core.R"
    output:
        heat=f"{RESULTS_DIR}/heatmap.tiff",
        volc=f"{RESULTS_DIR}/volcano.tiff",
        go=f"{RESULTS_DIR}/dotplot_go.tiff",
        genes=f"{RESULTS_DIR}/top_genes.tsv",
        report=f"{RESULTS_DIR}/report.pdf"
    params:
        padj=PADJ_THRESHOLD,
        lfc=LFC_THRESHOLD,
        outdir=RESULTS_DIR,
        covariate=COVARIATE
    shell:
        "Rscript run_deseq2.R {input.expr} {input.meta} {params.padj} {params.lfc} {params.outdir} {params.covariate}"

rule combine_figures:
    input:
        heat=f"{RESULTS_DIR}/heatmap.tiff",
        volc=f"{RESULTS_DIR}/volcano.tiff",
        go=f"{RESULTS_DIR}/dotplot_go.tiff"
    output:
        f"{RESULTS_DIR}/combined_figures.png"
    run:
        from PIL import Image, ImageDraw, ImageFont

        FONT_CANDIDATES = [
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "arialbd.ttf",
        ]

        def _load_bold_font(size):
            for path in FONT_CANDIDATES:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
            try:
                return ImageFont.load_default(size=size)
            except TypeError:
                return None

        def add_panel_letter(img, letter):
            img = img.convert("RGB")
            font_size = max(28, img.height // 12)
            font = _load_bold_font(font_size)

            if font is not None:
                draw = ImageDraw.Draw(img)
                margin = font_size // 4
                bbox = draw.textbbox((margin, margin), letter, font=font)
                pad = font_size // 6
                draw.rectangle(
                    [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
                    fill="white",
                )
                draw.text((margin, margin), letter, fill="black", font=font)
                return img

            tmp = Image.new("RGB", (16, 16), "white")
            ImageDraw.Draw(tmp).text((2, 1), letter, fill="black")
            scale = max(1, font_size // 9)
            tmp = tmp.resize((tmp.width * scale, tmp.height * scale), Image.NEAREST)
            margin = font_size // 4
            img.paste(tmp, (margin, margin))
            return img

        heat_img = add_panel_letter(Image.open(input.heat), "A")
        volc_img = add_panel_letter(Image.open(input.volc), "B")
        go_img = add_panel_letter(Image.open(input.go), "C")

        top_width = heat_img.width + volc_img.width
        top_height = max(heat_img.height, volc_img.height)

        canvas_width = top_width
        canvas_height = top_height + go_img.height

        combined = Image.new('RGB', (canvas_width, canvas_height), color='white')

        combined.paste(heat_img, (0, 0))
        combined.paste(volc_img, (heat_img.width, 0))

        go_x = (canvas_width - go_img.width) // 2
        combined.paste(go_img, (go_x, top_height))

        combined.save(output[0])


rule write_parameters:
    input:
        meta="metadata.tsv"
    output:
        f"{RESULTS_DIR}/analysis_parameters.txt"
    params:
        padj=PADJ_THRESHOLD,
        lfc=LFC_THRESHOLD,
        covariate=COVARIATE
    run:
        with open(input.meta) as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]

        header = lines[0].split("\t")
        sample_idx = header.index("sample")
        group_idx = header.index("group")

        control_samples = []
        treatment_samples = []
        for row in lines[1:]:
            fields = row.split("\t")
            sample = fields[sample_idx]
            group = fields[group_idx]
            if group == "control":
                control_samples.append(sample)
            elif group == "treatment":
                treatment_samples.append(sample)

        with open(output[0], "w") as out:
            out.write(f"padj_threshold={params.padj}\n")
            out.write(f"lfc_threshold={params.lfc}\n")
            out.write(f"control_samples={','.join(control_samples)}\n")
            out.write(f"treatment_samples={','.join(treatment_samples)}\n")
            if params.covariate:
                out.write(f"covariate={params.covariate}\n")


rule hash_input_data:
    input:
        expr="expression_input.tsv",
        meta="metadata.tsv"
    output:
        f"{RESULTS_DIR}/data_provenance.txt"
    run:
        import hashlib
        import pandas as pd

        def raw_sha256(path):
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        def canonical_sha256(path):
            df = pd.read_csv(path, sep="\t")
            canonical = df.sort_index(axis=1).to_csv(
                sep="\t", index=False, lineterminator="\n"
            ).encode("utf-8")
            return hashlib.sha256(canonical).hexdigest()

        with open(output[0], "w") as out:
            out.write(f"expression_raw_sha256={raw_sha256(input.expr)}\n")
            out.write(f"expression_canonical_sha256={canonical_sha256(input.expr)}\n")
            out.write(f"metadata_raw_sha256={raw_sha256(input.meta)}\n")
            out.write(f"metadata_canonical_sha256={canonical_sha256(input.meta)}\n")


rule snapshot_renv:
    input:
        f"{RESULTS_DIR}/top_genes.tsv"
    output:
        f"{RESULTS_DIR}/renv.lock"
    shell:
        "Rscript snapshot_renv.R {output}"


rule data_origin:
    input:
        origin="data_origin.json",
        expr="expression_input.tsv"
    output:
        f"{RESULTS_DIR}/data_origin.json"
    run:
        occ.write_data_origin(input.origin, input.expr, output[0])

rule certify:
    input:
        CERT_INPUTS,
        manifest=MANIFEST_PATH
    output:
        manifest=f"{RESULTS_DIR}/bundle_manifest.json",
        bundle=f"{RESULTS_DIR}/bundle_hash.txt",
        crate=f"{RESULTS_DIR}/ro-crate-metadata.json"
    run:
        if not DATA_ORIGIN and _os.path.exists(f"{RESULTS_DIR}/data_origin.json"):
            _os.remove(f"{RESULTS_DIR}/data_origin.json")
        occ.certify(input.manifest, RESULTS_DIR, ".")

rule generate_identicons:
    input:
        f"{RESULTS_DIR}/bundle_hash.txt"
    output:
        blockies=f"{RESULTS_DIR}/identicon_blockies.png"
    run:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(workflow.snakefile)))
        from identicon_utils import generate_blockies

        bundle = open(input[0]).read().strip()
        generate_blockies(bundle).save(output.blockies)


EXPLORATION_DIR = "results/_exploration"

PADJ_GRID = [0.001, 0.01, 0.05]
LFC_GRID = [0.0, 1.0, 2.0]

rule exploration_fit:
    input:
        expr="expression_input.tsv",
        meta="metadata.tsv",
        script="run_exploration.R",
        core="de_core.R"
    output:
        f"{EXPLORATION_DIR}/full_results.tsv"
    params:
        covariate=COVARIATE
    shell:
        "Rscript run_exploration.R {input.expr} {input.meta} {output} {params.covariate}"

rule exploration_robustness:
    input:
        f"{EXPLORATION_DIR}/full_results.tsv"
    output:
        table=f"{EXPLORATION_DIR}/robustness_table.tsv",
        manifest=f"{EXPLORATION_DIR}/grid_manifest.txt",
        hits=f"{EXPLORATION_DIR}/gene_combo_hits.tsv"
    params:
        padj_grid=PADJ_GRID,
        lfc_grid=LFC_GRID
    run:
        import pandas as pd

        df = pd.read_csv(input[0], sep="\t").dropna(subset=["padj"])
        combos = [(p, l) for p in params.padj_grid for l in params.lfc_grid]

        records = []
        manifest_lines = []
        for padj, lfc in combos:
            sig = df[(df["padj"] < padj) & (df["log2FoldChange"].abs() >= lfc)]
            manifest_lines.append(
                f"padj={padj},lfc={lfc},n_significant={len(sig)}"
            )
            for gene in sig["Gene"]:
                records.append({"Gene": gene, "padj": padj, "lfc": lfc})

        n_combos = len(combos)
        hits = pd.DataFrame(records, columns=["Gene", "padj", "lfc"])

        if hits.empty:
            robustness = pd.DataFrame(
                columns=["Gene", "n_combinations_significant", "robustness_score"]
            )
        else:
            counts = (
                hits.groupby("Gene")
                .size()
                .rename("n_combinations_significant")
                .reset_index()
            )
            counts["robustness_score"] = counts["n_combinations_significant"] / n_combos
            robustness = counts.sort_values(
                ["robustness_score", "Gene"], ascending=[False, True]
            )

        robustness.to_csv(output.table, sep="\t", index=False)
        hits.to_csv(output.hits, sep="\t", index=False)
        with open(output.manifest, "w") as f:
            f.write(f"padj_grid={','.join(str(p) for p in PADJ_GRID)}\n")
            f.write(f"lfc_grid={','.join(str(l) for l in LFC_GRID)}\n")
            f.write(f"n_combinations={n_combos}\n")
            f.write("\n".join(manifest_lines) + "\n")

rule exploration:
    input:
        f"{EXPLORATION_DIR}/robustness_table.tsv",
        f"{EXPLORATION_DIR}/grid_manifest.txt"
