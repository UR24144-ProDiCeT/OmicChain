args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) stop("Usage: platform_info.R <output_json>")
out <- args[1]
dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)

si <- sessionInfo()
value <- function(x) if (is.null(x) || length(x) == 0 || !nzchar(x[1])) NULL else x[1]
info <- list(
  os = value(si$running),
  platform = R.version$platform,
  r = R.version.string,
  blas = value(si$BLAS),
  lapack = value(si$LAPACK),
  lapack_version = value(La_version()),
  container = nzchar(Sys.getenv("OMICCHAIN_CONTAINER")),
  image_version = value(Sys.getenv("OMICCHAIN_IMAGE_VERSION")),
  image_revision = value(Sys.getenv("OMICCHAIN_IMAGE_REVISION")),
  blas_threads = value(Sys.getenv("OPENBLAS_NUM_THREADS")),
  distribution = value(Sys.getenv("OMICCHAIN_DISTRIBUTION"))
)
info <- info[!vapply(info, is.null, logical(1))]

con <- file(out, "wb")
writeLines(jsonlite::toJSON(info, auto_unbox = TRUE, pretty = TRUE), con, sep = "\n")
close(con)
cat(sprintf("Platform recorded: %s, %s\n", info$os, info$r))
