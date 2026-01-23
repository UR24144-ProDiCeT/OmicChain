#!/usr/bin/env Rscript
#
# run_deseq2.R — Robust DESeq2 pipeline for OmicChain
#
# Usage:
#   Rscript run_deseq2.R expression_input.tsv metadata.tsv
#
# Produces:
#   results/heatmap.tiff
#   results/volcano.tiff
#   results/dotplot_go.tiff
#   results/dotplot_kegg.tiff
#   results/top_genes.tsv
#   results/report.pdf
#

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: run_deseq2.R <expression_tsv> <metadata_tsv>")
}
expression_file <- args[1]
metadata_file <- args[2]

suppressMessages({
  # Load libraries but don't crash hard if one is missing — give informative errors
  required <- c("DESeq2", "tibble", "dplyr", "ggplot2", "pheatmap",
                "RColorBrewer", "clusterProfiler", "org.Hs.eg.db",
                "AnnotationDbi", "gridExtra", "grid", "tiff", "BiocManager", "Biobase")
  for (pkg in required) {
    if (!suppressWarnings(require(pkg, character.only = TRUE))) {
      stop(paste0("Package required but not available: ", pkg, ". Install with BiocManager::install('", pkg, "') or install.packages."))
    }
  }
})

# Create results dir
if (!dir.exists("results")) dir.create("results", recursive = TRUE)

# Safe read
expr <- tryCatch({
  read.table(expression_file, header = TRUE, sep = "\t", row.names = 1, check.names = FALSE, stringsAsFactors = FALSE)
}, error = function(e) stop("Failed to read expression file: ", e$message))

meta <- tryCatch({
  read.table(metadata_file, header = TRUE, sep = "\t", stringsAsFactors = FALSE)
}, error = function(e) stop("Failed to read metadata file: ", e$message))

# Validate metadata
if (!("sample" %in% colnames(meta)) || !("group" %in% colnames(meta))) {
  stop("Metadata must contain columns 'sample' and 'group'")
}

if (!all(meta$sample %in% colnames(expr))) {
  stop("Error: Sample names mismatch between metadata and expression file. Check metadata$sample and expression column names.")
}

# Subset and factorize
expr <- expr[, meta$sample, drop = FALSE]
meta$group <- factor(meta$group)

# Convert to integers for counts (rounding)
countdata <- round(as.matrix(expr))

# Create DESeq2 dataset
dds <- tryCatch({
  DESeqDataSetFromMatrix(countData = countdata, colData = meta, design = ~ group)
}, error = function(e) stop("DESeq2 dataset creation failed: ", e$message))

# Pre-filter rows with all zeros (optional but helpful)
keep <- rowSums(counts(dds)) > 1
dds <- dds[keep, ]

# Run DESeq
dds <- tryCatch({
  DESeq(dds)
}, error = function(e) stop("DESeq() failed: ", e$message))

# results()
# Try to get coef name robustly: look for something like "group_treatment_vs_control" or the second level
res <- tryCatch({
  res_all <- results(dds)
  # Attempt shrinkage with available methods (apeglm or as fallback)
  coef_name <- NULL
  # Try to infer coefficient: if resultsNames contains "group_" pattern, pick a contrast
  rn <- resultsNames(dds)
  # prefer names containing "group" and "vs"
  sel <- rn[grepl("^group", rn) | grepl("group_", rn)]
  if (length(sel) >= 1) coef_name <- sel[1] else if (length(rn) >= 2) coef_name <- rn[2]
  # Try shrink if possible
  res_shr <- tryCatch({
    # prefer apeglm if available
    if ("apeglm" %in% installed.packages()[,1]) {
      lfcShrink(dds, coef = coef_name, type = "apeglm")
    } else if ("ashr" %in% installed.packages()[,1]) {
      lfcShrink(dds, coef = coef_name, type = "ashr")
    } else {
      # fallback to normal results (no shrink)
      results(dds)
    }
  }, error = function(e) {
    # fallback
    results(dds)
  })
  res_shr
}, error = function(e) stop("Failed to obtain results: ", e$message))

# Convert to data frame and tidy
res_df <- as.data.frame(res)
res_df <- tibble::rownames_to_column(res_df, "Gene")

# Ensure padj exists; if NA, keep NA
# Order by padj (NAs go to the end)
res_df <- res_df[order(res_df$padj, na.last = TRUE), ]

# Create 'top' table with filters; be permissive if thresholds produce zero rows
top <- tryCatch({
  res_df %>%
    dplyr::filter(!is.na(padj),
                  padj < 0.05,
                  abs(log2FoldChange) > 0) %>%
    dplyr::arrange(padj)
}, error = function(e) {
  # fallback to empty tibble with columns
  res_df[0, , drop = FALSE]
})

# Write top_genes.tsv even if empty
tryCatch({
  write.table(top, file = "results/top_genes.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
}, error = function(e) {
  warning("Failed to write results/top_genes.tsv: ", e$message)
})

# ---------- Heatmap ----------
# Use rlog or vst depending on size
rlog_mat <- tryCatch({
  if (ncol(dds) > 30) {
    vst(dds, blind = TRUE)
  } else {
    rlog(dds, blind = TRUE)
  }
}, error = function(e) {
  # fallback to log transform
  tryCatch({
    assay(dds) <- log2(assay(dds) + 1)
    dds
  }, error = function(e2) NULL)
})

sig_genes <- res_df %>% dplyr::filter(!is.na(padj) & padj < 0.05) %>% dplyr::pull(Gene)
heatmap_path <- "results/heatmap.tiff"

if (length(sig_genes) == 0) {
  # create placeholder image
  tiff(heatmap_path, width = 1800, height = 1800, res = 300)
  plot.new()
  text(0.5, 0.5, "No significant genes (padj < 0.05)", cex = 2)
  dev.off()
} else {
  mat <- tryCatch({
    as.matrix(assay(rlog_mat)[sig_genes, , drop = FALSE])
  }, error = function(e) {
    NULL
  })
  if (is.null(mat) || nrow(mat) == 0) {
    tiff(heatmap_path, width = 1800, height = 1800, res = 300)
    plot.new()
    text(0.5, 0.5, "Heatmap generation failed or no data", cex = 2)
    dev.off()
  } else {
    tiff(heatmap_path, width = 1800, height = 1800, res = 300)
    tryCatch({
      pheatmap::pheatmap(mat, scale = "row", show_rownames = FALSE, cluster_cols = TRUE,
                         color = colorRampPalette(rev(RColorBrewer::brewer.pal(9, "RdBu")))(255))
    }, error = function(e) {
      plot.new(); text(0.5, 0.5, paste("Heatmap error:", e$message), cex = 1.2)
    })
    dev.off()
  }
}

# ---------- Volcano ----------
volcano_path <- "results/volcano.tiff"
# Prepare plotting table: avoid -log10(0) or -Inf
plot_df <- res_df
# Use padj when available; otherwise use pvalue; if both NA, set to 1
plot_df$padj_plot <- ifelse(is.na(plot_df$padj),
                            ifelse(!is.na(plot_df$pvalue), plot_df$pvalue, 1),
                            plot_df$padj)
plot_df$neglog10padj <- -log10(pmax(plot_df$padj_plot, 1e-300))  # avoid Inf
plot_df$Significance <- ifelse(!is.na(plot_df$padj) & plot_df$padj < 0.05 & abs(plot_df$log2FoldChange) > 1, "Significant", "NS")

tiff(volcano_path, width = 1800, height = 1800, res = 300)
tryCatch({
  p <- ggplot2::ggplot(plot_df, ggplot2::aes(x = log2FoldChange, y = neglog10padj, color = Significance)) +
    ggplot2::geom_point(alpha = 0.6) +
    ggplot2::scale_color_manual(values = c("NS" = "grey", "Significant" = "red")) +
    ggplot2::theme_minimal() +
    ggplot2::labs(title = "Volcano Plot", x = "Log2 Fold Change", y = "-log10(p-adj)") +
    ggplot2::theme(plot.title = ggplot2::element_text(hjust = 0.5))
  print(p)
}, error = function(e) {
  plot.new(); text(0.5, 0.5, paste("Volcano error:", e$message), cex = 1.2)
})
dev.off()

# ---------- GO & KEGG enrichment ----------
go_path <- "results/dotplot_go.tiff"
kegg_path <- "results/dotplot_kegg.tiff"

sig_for_enrich <- res_df %>% dplyr::filter(!is.na(padj) & padj < 0.05 & abs(log2FoldChange) > 1)
entrez_ids <- character(0)
if (nrow(sig_for_enrich) > 0) {
  # Map SYMBOL -> ENTREZID
  entrez_map <- tryCatch({
    AnnotationDbi::mapIds(org.Hs.eg.db, keys = sig_for_enrich$Gene, column = "ENTREZID", keytype = "SYMBOL", multiVals = "first")
  }, error = function(e) {
    warning("mapIds failed: ", e$message)
    NULL
  })
  if (!is.null(entrez_map)) {
    entrez_ids <- na.omit(unique(as.character(entrez_map)))
  }
}

if (length(entrez_ids) == 0) {
  # placeholders
  tiff(go_path, width = 1800, height = 1800, res = 300)
  plot.new(); text(0.5, 0.5, "No genes for GO enrichment", cex = 2)
  dev.off()

  tiff(kegg_path, width = 1800, height = 1800, res = 300)
  plot.new(); text(0.5, 0.5, "No genes for KEGG enrichment", cex = 2)
  dev.off()
} else {
  # GO
  go_res <- tryCatch({
    clusterProfiler::enrichGO(gene = entrez_ids, OrgDb = org.Hs.eg.db, ont = "BP", readable = TRUE)
  }, error = function(e) {
    warning("enrichGO failed: ", e$message)
    NULL
  })

  if (!is.null(go_res) && nrow(as.data.frame(go_res)) > 0) {
    tiff(go_path, width = 1800, height = 1800, res = 300)
    tryCatch({
      print(clusterProfiler::dotplot(go_res, showCategory = 10) + ggplot2::ggtitle("GO: Biological Processes"))
    }, error = function(e) {
      plot.new(); text(0.5, 0.5, paste("GO plot error:", e$message), cex = 1.2)
    })
    dev.off()
  } else {
    tiff(go_path, width = 1800, height = 1800, res = 300)
    plot.new(); text(0.5, 0.5, "No GO results", cex = 2)
    dev.off()
  }

  # KEGG (note: enrichKEGG requires ENTREZ IDs)
  kegg_res <- tryCatch({
    clusterProfiler::enrichKEGG(gene = entrez_ids, organism = "hsa")
  }, error = function(e) {
    warning("enrichKEGG failed: ", e$message)
    NULL
  })

  if (!is.null(kegg_res) && nrow(as.data.frame(kegg_res)) > 0) {
    tiff(kegg_path, width = 1800, height = 1800, res = 300)
    tryCatch({
      print(clusterProfiler::dotplot(kegg_res, showCategory = 10) + ggplot2::ggtitle("KEGG Pathways"))
    }, error = function(e) {
      plot.new(); text(0.5, 0.5, paste("KEGG plot error:", e$message), cex = 1.2)
    })
    dev.off()
  } else {
    tiff(kegg_path, width = 1800, height = 1800, res = 300)
    plot.new(); text(0.5, 0.5, "No KEGG results", cex = 2)
    dev.off()
  }
}

# ---------- Multi-panel PDF report ----------
pdf("results/report.pdf", width = 11, height = 8.5)
# Try to read tiffs; if unavailable show message
safe_read_tiff_plot <- function(path, label) {
  if (!file.exists(path)) {
    plot.new(); text(0.5, 0.5, paste(label, "missing"), cex = 1.5)
    return(invisible(NULL))
  }
  img <- tryCatch({
    tiff::readTIFF(path, native = TRUE)
  }, error = function(e) {
    NULL
  })
  if (is.null(img)) {
    plot.new(); text(0.5, 0.5, paste(label, "read error"), cex = 1.2)
    return(invisible(NULL))
  }
  grid::grid.raster(img, width = unit(1, "npc"), height = unit(1, "npc"), interpolate = TRUE)
}

# Layout: 2x2 panels; use grid.arrange with grobs
library(grid)
library(gridExtra)

# Panel 1: Heatmap
grid.newpage()
pushViewport(viewport(layout = grid.layout(2, 2)))
# panel positions
viewport.plot <- function(row, col, label, path) {
  vp <- viewport(layout.pos.row = row, layout.pos.col = col)
  pushViewport(vp)
  par(mai = c(0.5,0.5,0.5,0.5))
  safe_read_tiff_plot(path, label)
  popViewport()
}

viewport.plot(1, 1, "Heatmap", heatmap_path)
viewport.plot(1, 2, "Volcano", volcano_path)
viewport.plot(2, 1, "GO", go_path)
viewport.plot(2, 2, "KEGG", kegg_path)

dev.off()

# End of script: print status to stdout for Snakemake logs
cat("run_deseq2.R completed\n")
