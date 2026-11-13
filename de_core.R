suppressMessages({
  required_core <- c("DESeq2", "tibble", "dplyr", "BiocManager")
  for (pkg in required_core) {
    if (!suppressWarnings(require(pkg, character.only = TRUE))) {
      stop(paste0("Package required but not available: ", pkg,
                   ". Install with BiocManager::install('", pkg,
                   "') or install.packages."))
    }
  }
})

run_de_core <- function(expression_file, metadata_file, covariate = NULL) {
  expr <- tryCatch({
    read.table(expression_file, header = TRUE, sep = "\t", row.names = 1,
               check.names = FALSE, stringsAsFactors = FALSE)
  }, error = function(e) stop("Failed to read expression file: ", e$message))

  meta <- tryCatch({
    read.table(metadata_file, header = TRUE, sep = "\t", stringsAsFactors = FALSE)
  }, error = function(e) stop("Failed to read metadata file: ", e$message))

  if (!("sample" %in% colnames(meta)) || !("group" %in% colnames(meta))) {
    stop("Metadata must contain columns 'sample' and 'group'")
  }

  if (!all(meta$sample %in% colnames(expr))) {
    stop("Error: Sample names mismatch between metadata and expression file. ",
         "Check metadata$sample and expression column names.")
  }

  expr <- expr[, meta$sample, drop = FALSE]
  meta$group <- factor(meta$group)

  design <- ~ group
  if (!is.null(covariate) && nzchar(covariate)) {
    if (covariate %in% c("sample", "group"))
      stop("The covariate must be another column than 'sample' and 'group'.")
    if (!(covariate %in% colnames(meta)))
      stop("Covariate '", covariate, "' is not a column of the metadata file (columns: ",
           paste(colnames(meta), collapse = ", "), ").")
    if (!identical(make.names(covariate), covariate))
      stop("Covariate name '", covariate, "' is not a valid R name (letters, digits, '.', '_').")
    if (any(is.na(meta[[covariate]]) | meta[[covariate]] == ""))
      stop("Covariate '", covariate, "' has empty values.")
    meta[[covariate]] <- factor(meta[[covariate]])
    if (nlevels(meta[[covariate]]) < 2)
      stop("Covariate '", covariate, "' has a single value: it cannot be used in the model.")
    mm <- model.matrix(as.formula(paste("~", covariate, "+ group")), meta)
    if (qr(mm)$rank < ncol(mm))
      stop("Covariate '", covariate, "' is confounded with the groups (each of its values must ",
           "contain both control and treatment samples, or the design cannot be estimated).")
    design <- as.formula(paste("~", covariate, "+ group"))
  }

  countdata <- round(as.matrix(expr))

  dds <- tryCatch({
    DESeqDataSetFromMatrix(countData = countdata, colData = meta, design = design)
  }, error = function(e) stop("DESeq2 dataset creation failed: ", e$message))

  keep <- rowSums(counts(dds)) > 1
  dds <- dds[keep, ]

  dds <- tryCatch({
    DESeq(dds)
  }, error = function(e) stop("DESeq() failed: ", e$message))

  if (!("apeglm" %in% rownames(installed.packages()))) {
    stop("Package 'apeglm' is required for this pipeline (fixed shrinkage method). ",
         "Install it with BiocManager::install('apeglm') before re-running.")
  }

  res <- tryCatch({
    rn <- resultsNames(dds)
    sel <- rn[startsWith(rn, "group_")]
    if (length(sel) == 0) sel <- rn[grepl("^group", rn) | grepl("group_", rn)]
    coef_name <- if (length(sel) >= 1) sel[1] else if (length(rn) >= 2) rn[2] else stop("Unable to determine the contrast coefficient.")

    lfcShrink(dds, coef = coef_name, type = "apeglm")
  }, error = function(e) stop("Failed to obtain results: ", e$message))

  res_df <- as.data.frame(res)
  res_df <- tibble::rownames_to_column(res_df, "Gene")
  res_df <- res_df[order(res_df$padj, res_df$Gene, na.last = TRUE), ]

  list(res_df = res_df, dds = dds)
}
