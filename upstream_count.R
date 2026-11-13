args <- commandArgs(trailingOnly = TRUE)
mode <- args[1]
suppressPackageStartupMessages(library(Rsubread))

fc <- function(bam, gtf, paired, strand, threads, tmp) {
  featureCounts(bam, annot.ext = gtf, isGTFAnnotationFile = TRUE,
                GTF.featureType = "exon", GTF.attrType = "gene_id",
                isPairedEnd = paired, strandSpecific = strand, nthreads = threads,
                tmpDir = tmp, verbose = FALSE)
}
assigned <- function(x) x$stat[x$stat$Status == "Assigned", 2]

if (mode == "count") {
  if (length(args) != 7) stop("Usage: upstream_count.R count <bam> <gtf> <paired> <strand> <threads> <counts.tsv>")
  out <- args[7]
  x <- fc(args[2], args[3], args[4] == "TRUE", as.integer(args[5]), as.integer(args[6]), dirname(out))
  m <- x$counts[order(rownames(x$counts)), 1]
  writeLines(paste(names(m), as.integer(m), sep = "\t"), out)
  writeLines(paste(x$stat$Status, x$stat[, 2], sep = "\t"), paste0(out, ".stats"))
} else if (mode == "detect") {
  if (length(args) != 6) stop("Usage: upstream_count.R detect <bam> <gtf> <paired> <threads> <strand.tsv>")
  out <- args[6]
  n <- sapply(0:2, function(s) assigned(fc(args[2], args[3], args[4] == "TRUE", s, as.integer(args[5]), dirname(out))))
  guess <- if (n[2] / max(n[3], 1) > 4) 1L else if (n[3] / max(n[2], 1) > 4) 2L else 0L
  writeLines(c(paste0("strand\t", guess), paste0("assigned_s", 0:2, "\t", n)), out)
} else stop("unknown mode: ", mode)
