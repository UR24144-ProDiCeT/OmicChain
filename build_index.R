args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 4) stop("Usage: build_index.R <genome> <basename> <full|gapped> <result.dcf>")
genome <- args[1]; base <- args[2]; type <- args[3]; result <- args[4]
if (!type %in% c("full", "gapped")) stop("unknown index type: ", type)
if (!requireNamespace("Rsubread", quietly = TRUE))
  stop("Rsubread is not installed: BiocManager::install('Rsubread')")

elapsed <- system.time(
  Rsubread::buildindex(basename = base, reference = genome, gappedIndex = type == "gapped")
)[["elapsed"]]

write.dcf(data.frame(rsubread_version = as.character(packageVersion("Rsubread")),
                     r_version = as.character(getRversion()),
                     elapsed_s = round(elapsed, 1)), result)
