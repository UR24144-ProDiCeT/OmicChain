# --------------------------
# Main workflow definition
# --------------------------

# Final targets: required outputs of the pipeline
rule all:
    input:
        "results/heatmap.tiff",             # DESeq2 heatmap
        "results/volcano.tiff",             # DESeq2 volcano plot
        "results/dotplot_go.tiff",          # GO enrichment dotplot
        "results/dotplot_kegg.tiff",        # KEGG enrichment dotplot
        "results/combined_figures.png",     # Merged image of the four plots
        "results/snakefile_hash.txt",       # SHA256 hash of this Snakefile
        "results/rscript_hash.txt",         # SHA256 hash of the R script
        "results/top_genes_hash.txt"        # SHA256 hash of the top DE genes

# --------------------------
# Step 1: Run differential analysis
# --------------------------
rule run_deseq2:
    input:
        expr="expression_input.tsv",        # Input count matrix
        meta="metadata.tsv"                 # Group annotation file
    output:
        heat="results/heatmap.tiff",        # Output heatmap
        volc="results/volcano.tiff",        # Output volcano plot
        go="results/dotplot_go.tiff",       # Output GO enrichment plot
        kegg="results/dotplot_kegg.tiff",   # Output KEGG enrichment plot
        genes="results/top_genes.tsv"       # Output table of top DE genes
    shell:
        "Rscript run_deseq2.R {input.expr} {input.meta}"

# --------------------------
# Step 2: Combine four figures into one PNG
# --------------------------
rule combine_figures:
    input:
        heat="results/heatmap.tiff",
        volc="results/volcano.tiff",
        go="results/dotplot_go.tiff",
        kegg="results/dotplot_kegg.tiff"
    output:
        "results/combined_figures.png"
    run:
        from PIL import Image

        # Load individual figures
        imgs = [Image.open(input.heat), Image.open(input.volc),
                Image.open(input.go), Image.open(input.kegg)]

        # Get their sizes to define the layout
        widths, heights = zip(*(i.size for i in imgs))

        # Create a new blank canvas with enough space for 2x2 layout
        combined = Image.new('RGB', (widths[0] + widths[1], heights[0] + heights[2]))

        # Paste images in a 2x2 grid
        combined.paste(imgs[0], (0, 0))                           # Top left: heatmap
        combined.paste(imgs[1], (widths[0], 0))                   # Top right: volcano
        combined.paste(imgs[2], (0, heights[0]))                  # Bottom left: GO
        combined.paste(imgs[3], (widths[2], heights[0]))          # Bottom right: KEGG

        # Save final image
        combined.save(output[0])

# --------------------------
# Step 3: Hash verification rules
# --------------------------

# Hash of the Snakefile
rule hash_snakefile:
    input: "Snakefile"
    output: "results/snakefile_hash.txt"
    run:
        import hashlib
        with open(input[0], 'rb') as f:
            h = hashlib.sha256(f.read()).hexdigest()
        with open(output[0], 'w') as out:
            out.write(h)

# Hash of the R script
rule hash_rscript:
    input: "run_deseq2.R"
    output: "results/rscript_hash.txt"
    run:
        import hashlib
        with open(input[0], 'rb') as f:
            h = hashlib.sha256(f.read()).hexdigest()
        with open(output[0], 'w') as out:
            out.write(h)

# Hash of the output top DE gene table
rule hash_top_genes:
    input: "results/top_genes.tsv"
    output: "results/top_genes_hash.txt"
    run:
        import hashlib
        with open(input[0], 'rb') as f:
            h = hashlib.sha256(f.read()).hexdigest()
        with open(output[0], 'w') as out:
            out.write(h)