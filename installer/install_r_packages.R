args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("Usage: install_r_packages.R <renv.lock> <library>")
lock_path <- normalizePath(args[1], winslash = "/")
lib <- args[2]
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
lib <- normalizePath(lib, winslash = "/")
.libPaths(c(lib, .Library))

options(repos = c(CRAN = "https://packagemanager.posit.co/cran/latest"),
        Ncpus = parallel::detectCores(), timeout = 600)
for (p in c("renv", "BiocManager", "jsonlite")) {
  if (!requireNamespace(p, lib.loc = lib, quietly = TRUE)) install.packages(p, lib = lib)
}
lock <- jsonlite::fromJSON(lock_path, simplifyVector = FALSE)
BiocManager::install(version = lock$Bioconductor$Version, ask = FALSE, update = FALSE, lib = lib)

options(renv.config.pak.enabled = FALSE, renv.consent = TRUE)
renv::restore(lockfile = lock_path, library = lib, repos = getOption("repos"), prompt = FALSE)
cat("R packages installed in", lib, "\n")
