args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  stop("Usage: run_exploration.R <expression_tsv> <metadata_tsv> <output_tsv> [covariate]")
}
expression_file <- args[1]
metadata_file <- args[2]
output_file <- args[3]
covariate <- if (length(args) >= 4 && nzchar(args[4])) args[4] else NULL

outdir <- dirname(output_file)
if (!dir.exists(outdir)) dir.create(outdir, recursive = TRUE)

source("de_core.R")

core <- run_de_core(expression_file, metadata_file, covariate = covariate)
res_df <- core$res_df[, c("Gene", "log2FoldChange", "padj", "pvalue")]

write.table(res_df, file = output_file, sep = "\t", quote = FALSE, row.names = FALSE)

cat(sprintf("run_exploration.R completed: %d genes\n", nrow(res_df)))
