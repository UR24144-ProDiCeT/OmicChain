args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: snapshot_renv.R <output_lockfile_path>")
}
output_path <- args[1]
outdir <- dirname(output_path)
if (!dir.exists(outdir)) dir.create(outdir, recursive = TRUE)
output_path <- normalizePath(output_path, winslash = "/", mustWork = FALSE)

project <- Sys.getenv("OMICCHAIN_RENV_PROJECT", unset = ".")
like <- Sys.getenv("OMICCHAIN_SNAPSHOT_LIKE", unset = "")
like_text <- if (nzchar(like) && file.exists(like)) paste(readLines(like, warn = FALSE), collapse = "\n") else ""

if (nzchar(like_text) && !grepl("\"OmicChain\"", like_text, fixed = TRUE)) {
  if (!requireNamespace("renv", quietly = TRUE)) {
    stop("This analysis was certified with a renv snapshot: the 'renv' package is needed to replay it.")
  }
  lock <- tryCatch(renv::lockfile_read(like), error = function(e) renv:::renv_json_read(like))
  renv::snapshot(project = project, lockfile = output_path, packages = names(lock$Packages), prompt = FALSE)
  cat(sprintf("renv.lock written to: %s (renv snapshot, as in the certified analysis)\n", output_path))
  quit(status = 0)
}

dep_names <- function(text) {
  if (is.na(text) || !nzchar(text)) return(character())
  parts <- trimws(sub("\\(.*$", "", unlist(strsplit(text, ","))))
  parts[nzchar(parts) & parts != "R"]
}

installed <- installed.packages(lib.loc = .libPaths(),
                                fields = c("Repository", "biocViews"))
installed <- installed[!duplicated(installed[, "Package"]), , drop = FALSE]
base <- rownames(installed)[!is.na(installed[, "Priority"]) & installed[, "Priority"] == "base"]

if (nzchar(like_text)) {
  found <- regmatches(like_text, gregexpr("\"Package\": \"[^\"]+\"", like_text))[[1]]
  roots <- sub("\"$", "", sub("^\"Package\": \"", "", found))
  strict <- FALSE
} else {
  description <- file.path(project, "DESCRIPTION")
  if (!file.exists(description)) stop("DESCRIPTION not found in ", normalizePath(project, winslash = "/"))
  roots <- dep_names(read.dcf(description, fields = "Imports")[1, 1])
  strict <- TRUE
}

missing <- setdiff(roots, c(rownames(installed), base))
if (strict && length(missing) > 0) {
  stop("R packages needed by the analysis are not installed in the libraries R uses here (",
       paste(.libPaths(), collapse = "; "), "): ", paste(missing, collapse = ", "),
       ". The R environment cannot be certified. Install them (see INSTALL.md) and run the analysis again.")
}

keep <- character()
queue <- setdiff(intersect(roots, rownames(installed)), base)
while (length(queue) > 0) {
  pkg <- queue[1]
  queue <- queue[-1]
  if (pkg %in% keep) next
  keep <- c(keep, pkg)
  deps <- unlist(lapply(c("Depends", "Imports", "LinkingTo"), function(f) dep_names(installed[pkg, f])))
  queue <- c(queue, setdiff(intersect(deps, rownames(installed)), c(base, keep)))
}
keep <- sort(keep, method = "radix")

is_bioc <- !is.na(installed[keep, "biocViews"])
is_repo <- !is_bioc & !is.na(installed[keep, "Repository"])
bioc_version <- NULL
if ("BiocVersion" %in% rownames(installed)) {
  parts <- unlist(strsplit(installed["BiocVersion", "Version"], ".", fixed = TRUE))
  if (length(parts) >= 2) bioc_version <- paste(parts[1], parts[2], sep = ".")
}

q <- function(x) paste0("\"", gsub("\"", "\\\\\"", gsub("\\\\", "\\\\\\\\", x)), "\"")
lines <- c("{",
           "  \"OmicChain\": {",
           "    \"EnvironmentRecord\": 1",
           "  },",
           "  \"R\": {",
           paste0("    \"Version\": ", q(paste(R.version$major, R.version$minor, sep = ".")), ","),
           "    \"Repositories\": [",
           "      {",
           "        \"Name\": \"CRAN\",",
           "        \"URL\": \"https://cloud.r-project.org\"",
           "      }",
           "    ]",
           "  },")
if (!is.null(bioc_version)) {
  lines <- c(lines, "  \"Bioconductor\": {", paste0("    \"Version\": ", q(bioc_version)), "  },")
}
lines <- c(lines, "  \"Packages\": {")
for (i in seq_along(keep)) {
  pkg <- keep[i]
  entry <- c(paste0("    ", q(pkg), ": {"),
             paste0("      \"Package\": ", q(pkg), ","),
             paste0("      \"Version\": ", q(installed[pkg, "Version"]), ","))
  if (is_bioc[i]) {
    entry <- c(entry, "      \"Source\": \"Bioconductor\"")
  } else if (is_repo[i]) {
    entry <- c(entry, "      \"Source\": \"Repository\",", "      \"Repository\": \"CRAN\"")
  } else {
    entry <- c(entry, "      \"Source\": \"unknown\"")
  }
  lines <- c(lines, entry, if (i < length(keep)) "    }," else "    }")
}
lines <- c(lines, "  }", "}")

con <- file(output_path, open = "wb")
writeLines(enc2utf8(lines), con, sep = "\n", useBytes = TRUE)
close(con)

cat(sprintf("renv.lock written to: %s (%d packages, R %s%s)\n", output_path, length(keep),
            paste(R.version$major, R.version$minor, sep = "."),
            if (is.null(bioc_version)) "" else paste0(", Bioconductor ", bioc_version)))
if (length(missing) > 0) {
  cat("Not installed here (recorded in the certified analysis):", paste(missing, collapse = ", "), "\n")
}
