args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: snapshot_renv.R <output_lockfile_path>")
}
output_path <- args[1]

outdir <- dirname(output_path)
if (!dir.exists(outdir)) dir.create(outdir, recursive = TRUE)

if (!requireNamespace("renv", quietly = TRUE)) {
  stop(
    "The 'renv' package is not installed. Run once, from the project ",
    "folder: install.packages('renv') then renv::init()."
  )
}

renv_project <- Sys.getenv("OMICCHAIN_RENV_PROJECT", unset = ".")
output_path <- normalizePath(output_path, winslash = "/", mustWork = FALSE)
like <- Sys.getenv("OMICCHAIN_SNAPSHOT_LIKE", unset = "")
if (nzchar(like) && file.exists(like)) {
  lock <- tryCatch(renv::lockfile_read(like), error = function(e) renv:::renv_json_read(like))
  pkgs <- names(lock$Packages)
  renv::snapshot(project = renv_project, lockfile = output_path, packages = pkgs, prompt = FALSE)
} else {
  renv::snapshot(project = renv_project, lockfile = output_path, type = "explicit", prompt = FALSE)
}

cat(sprintf("renv.lock written to: %s\n", output_path))
