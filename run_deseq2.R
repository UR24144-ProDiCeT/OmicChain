args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: run_deseq2.R <expression_tsv> <metadata_tsv> [padj_threshold] [lfc_threshold]")
}
expression_file <- args[1]
metadata_file <- args[2]

padj_threshold <- if (length(args) >= 3) as.numeric(args[3]) else 0.05
lfc_threshold  <- if (length(args) >= 4) as.numeric(args[4]) else 1.0

outdir <- if (length(args) >= 5) args[5] else "results"

covariate <- if (length(args) >= 6 && nzchar(args[6])) args[6] else NULL

if (is.na(padj_threshold) || padj_threshold <= 0 || padj_threshold > 1) {
  stop("Invalid padj_threshold: must be a number in (0, 1].")
}
if (is.na(lfc_threshold) || lfc_threshold < 0) {
  stop("Invalid lfc_threshold: must be a non-negative number.")
}

cat(sprintf("Analysis parameters: padj_threshold=%s, lfc_threshold=%s, covariate=%s\n",
            padj_threshold, lfc_threshold, if (is.null(covariate)) "none" else covariate))

suppressMessages({
  required <- c("ggplot2", "pheatmap", "RColorBrewer", "clusterProfiler",
                "org.Hs.eg.db", "AnnotationDbi", "gridExtra", "grid", "tiff", "Biobase")
  for (pkg in required) {
    if (!suppressWarnings(require(pkg, character.only = TRUE))) {
      stop(paste0("Package required but not available: ", pkg, ". Install with BiocManager::install('", pkg, "') or install.packages."))
    }
  }
})

if (!dir.exists(outdir)) dir.create(outdir, recursive = TRUE)

source("de_core.R")
core <- run_de_core(expression_file, metadata_file, covariate = covariate)
res_df <- core$res_df
dds <- core$dds

top <- res_df %>%
  dplyr::filter(!is.na(padj),
                padj < padj_threshold,
                abs(log2FoldChange) >= lfc_threshold) %>%
  dplyr::arrange(padj)
cat(sprintf("Significant genes (padj < %s, |log2FC| >= %s): %d\n", padj_threshold, lfc_threshold, nrow(top)))

top <- top %>%
  dplyr::mutate(dplyr::across(where(is.numeric), ~ signif(.x, 6)))

tryCatch({
  write.table(top, file = file.path(outdir, "top_genes.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
}, error = function(e) {
  warning("Failed to write top_genes.tsv: ", e$message)
})

rlog_mat <- tryCatch({
  if (ncol(dds) > 30) {
    vst(dds, blind = TRUE)
  } else {
    rlog(dds, blind = TRUE)
  }
}, error = function(e) {
  tryCatch({
    assay(dds) <- log2(assay(dds) + 1)
    dds
  }, error = function(e2) NULL)
})

sig_genes <- res_df %>%
  dplyr::filter(!is.na(padj) & padj < padj_threshold & abs(log2FoldChange) >= lfc_threshold) %>%
  dplyr::pull(Gene)
heatmap_path <- file.path(outdir, "heatmap.tiff")

if (length(sig_genes) == 0) {
  tiff(heatmap_path, width = 1800, height = 1800, res = 300)
  plot.new()
  text(0.5, 0.5, sprintf("No significant genes (padj < %s, |LFC| >= %s)",
                          padj_threshold, lfc_threshold), cex = 1.6)
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

volcano_path <- file.path(outdir, "volcano.tiff")
plot_df <- res_df
plot_df$padj_plot <- ifelse(is.na(plot_df$padj),
                            ifelse(!is.na(plot_df$pvalue), plot_df$pvalue, 1),
                            plot_df$padj)
plot_df$neglog10padj <- -log10(pmax(plot_df$padj_plot, 1e-300))

plot_df$Regulation <- "NS"
plot_df$Regulation[!is.na(plot_df$padj) & plot_df$padj < padj_threshold &
                      plot_df$log2FoldChange >= lfc_threshold] <- "Upregulated"
plot_df$Regulation[!is.na(plot_df$padj) & plot_df$padj < padj_threshold &
                      plot_df$log2FoldChange <= -lfc_threshold] <- "Downregulated"
plot_df$Regulation <- factor(plot_df$Regulation,
                              levels = c("Downregulated", "NS", "Upregulated"))

top50_labels <- plot_df[!is.na(plot_df$padj), ]
top50_labels <- top50_labels[order(top50_labels$padj), ]
top50_labels <- utils::head(top50_labels, 50)

has_ggrepel <- suppressWarnings(suppressMessages(
  requireNamespace("ggrepel", quietly = TRUE)
))

tiff(volcano_path, width = 1800, height = 1800, res = 300)
tryCatch({
  p <- ggplot2::ggplot(plot_df, ggplot2::aes(x = log2FoldChange, y = neglog10padj, color = Regulation)) +
    ggplot2::geom_point(alpha = 0.6) +
    ggplot2::scale_color_manual(values = c("Downregulated" = "#2166AC",
                                            "NS" = "grey70",
                                            "Upregulated" = "#B2182B")) +
    ggplot2::theme_minimal() +
    ggplot2::labs(x = "Log2 Fold Change", y = "-log10(p-adj)") +
    ggplot2::theme(legend.position = "bottom", legend.title = ggplot2::element_blank())

  if (has_ggrepel) {
    p <- p + ggrepel::geom_text_repel(
      data = top50_labels,
      ggplot2::aes(label = Gene),
      size = 2.5,
      max.overlaps = 100,
      color = "black",
      show.legend = FALSE
    )
  } else {
    p <- p + ggplot2::geom_text(
      data = top50_labels,
      ggplot2::aes(label = Gene),
      size = 2.5,
      vjust = -0.6,
      color = "black",
      show.legend = FALSE
    )
  }

  print(p)
}, error = function(e) {
  plot.new(); text(0.5, 0.5, paste("Volcano error:", e$message), cex = 1.2)
})
dev.off()

go_path <- file.path(outdir, "dotplot_go.tiff")

sig_for_enrich <- res_df %>% dplyr::filter(!is.na(padj) & padj < padj_threshold & abs(log2FoldChange) >= lfc_threshold)
entrez_ids <- character(0)
if (nrow(sig_for_enrich) > 0) {
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
  tiff(go_path, width = 1800, height = 1800, res = 300)
  plot.new(); text(0.5, 0.5, "No genes for GO enrichment", cex = 2)
  dev.off()
} else {
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
}

pdf(file.path(outdir, "report.pdf"), width = 11, height = 8.5)
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

library(grid)
library(gridExtra)

grid.newpage()
pushViewport(viewport(layout = grid.layout(2, 4)))
viewport.plot <- function(row, cols, label, path, letter = NULL) {
  vp <- viewport(layout.pos.row = row, layout.pos.col = cols)
  pushViewport(vp)
  par(mai = c(0.5,0.5,0.5,0.5))
  safe_read_tiff_plot(path, label)
  if (!is.null(letter)) {
    grid::grid.text(
      letter,
      x = unit(0.02, "npc"), y = unit(0.98, "npc"),
      just = c("left", "top"),
      gp = grid::gpar(fontsize = 20, fontface = "bold")
    )
  }
  popViewport()
}

viewport.plot(1, 1:2, "Heatmap", heatmap_path, "A")
viewport.plot(1, 3:4, "Volcano", volcano_path, "B")
viewport.plot(2, 2:3, "GO", go_path, "C")

dev.off()

cat("run_deseq2.R completed\n")
