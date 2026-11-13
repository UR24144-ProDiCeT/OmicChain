args <- commandArgs(trailingOnly = TRUE)
if (!(length(args) %in% c(6, 8)))
  stop("Usage: upstream_align.R <index_base> <bam> <threads> <stats.tsv> <r1> <md5_r1> [<r2> <md5_r2>]")
index <- args[1]; bam <- args[2]; threads <- as.integer(args[3]); stats_out <- args[4]
r1 <- args[5]; md5_1 <- tolower(args[6])
paired <- length(args) == 8
r2 <- if (paired) args[7] else NULL
md5_2 <- if (paired) tolower(args[8]) else NULL

suppressPackageStartupMessages(library(Rsubread))

check_md5 <- function(path, expected) {
  got <- unname(tolower(tools::md5sum(path)))
  if (!identical(got, expected))
    stop(sprintf("md5 of %s: %s instead of %s (file changed since it was downloaded?)",
                 basename(path), got, expected), call. = FALSE)
}
check_md5(r1, md5_1)
if (paired) check_md5(r2, md5_2)

dir.create(dirname(bam), showWarnings = FALSE, recursive = TRUE)
aln <- align(index = index, readfile1 = r1, readfile2 = r2, type = "rna",
             output_file = bam, nthreads = threads)
unlink(paste0(bam, c(".indel.vcf", ".summary")))

key <- if ("Total_fragments" %in% rownames(aln)) "fragments" else "reads"
stats <- c(rsubread_version = as.character(packageVersion("Rsubread")),
           paired = if (paired) "TRUE" else "FALSE",
           total = aln[paste0("Total_", key), 1],
           mapped = aln[paste0("Mapped_", key), 1],
           uniquely_mapped = if (paste0("Uniquely_mapped_", key) %in% rownames(aln))
             aln[paste0("Uniquely_mapped_", key), 1] else NA)
writeLines(paste(names(stats), stats, sep = "\t"), stats_out)
