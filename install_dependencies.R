if (!file.exists("renv/activate.R")) {
  stop("Run this script from the project folder, after renv::init().")
}

lib <- normalizePath(.libPaths()[1], winslash = "/", mustWork = FALSE)
renv_dir <- normalizePath("renv", winslash = "/", mustWork = TRUE)
cat("Active library:", lib, "\n")
if (!startsWith(lib, renv_dir)) {
  stop(
    "The active library is not the one of the renv project. ",
    "Stopping so that no package is installed in your user library. ",
    "Check that the script is run from the project folder."
  )
}

bioc_pkgs <- c("DESeq2", "clusterProfiler", "org.Hs.eg.db",
               "AnnotationDbi", "Biobase", "apeglm", "Rsubread")
cran_pkgs <- c("BiocManager", "tibble", "dplyr", "ggplot2", "pheatmap",
               "RColorBrewer", "gridExtra", "tiff", "ggrepel")
required <- c(bioc_pkgs, cran_pkgs)

renv::hydrate(packages = required, update = FALSE)

missing <- required[!vapply(required, requireNamespace, logical(1), quietly = TRUE)]
if (length(missing) > 0) {
  cat("Packages missing after hydrate(), downloading:",
      paste(missing, collapse = ", "), "\n")
  to_install <- c(
    paste0("bioc::", intersect(missing, bioc_pkgs)),
    intersect(missing, cran_pkgs)
  )
  renv::install(to_install)
}

status <- system2(file.path(R.home("bin"), "Rscript"), c("snapshot_renv.R", "renv.lock"))
if (status != 0) stop("The R environment could not be recorded (snapshot_renv.R).")

missing <- required[!vapply(required, requireNamespace, logical(1),
                            quietly = TRUE)]
if (length(missing) > 0) {
  stop("Packages still missing: ", paste(missing, collapse = ", "))
}
cat("OK: all the required packages are available in the project library.\n")
