#!/usr/bin/env Rscript

# =============================================================================
# OmicChain R Dependencies Installation Script
# =============================================================================
# This script installs all required R packages for the OmicChain pipeline

cat("Installing OmicChain R dependencies...\n")

# Function to install packages with error handling
install_with_check <- function(packages, method = "CRAN") {
  for (pkg in packages) {
    if (!require(pkg, character.only = TRUE, quietly = TRUE)) {
      cat(paste("Installing", pkg, "from", method, "...\n"))
      tryCatch({
        if (method == "Bioconductor") {
          BiocManager::install(pkg, update = FALSE, ask = FALSE)
        } else {
          install.packages(pkg, dependencies = TRUE, repos = "https://cran.r-project.org/")
        }
        cat(paste("✓", pkg, "installed successfully\n"))
      }, error = function(e) {
        cat(paste("✗ Failed to install", pkg, ":", e$message, "\n"))
      })
    } else {
      cat(paste("✓", pkg, "already installed\n"))
    }
  }
}

# Install BiocManager first
if (!require("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager", repos = "https://cran.r-project.org/")
}

# CRAN packages
cran_packages <- c(
  "tibble",
  "dplyr", 
  "ggplot2",
  "gridExtra",
  "grid",
  "tiff",
  "RColorBrewer",
  "pheatmap"
)

# Bioconductor packages
bioc_packages <- c(
  "DESeq2",
  "clusterProfiler", 
  "org.Hs.eg.db",
  "AnnotationDbi"
)

# Install packages
cat("\n=== Installing CRAN packages ===\n")
install_with_check(cran_packages, "CRAN")

cat("\n=== Installing Bioconductor packages ===\n") 
install_with_check(bioc_packages, "Bioconductor")

cat("\n=== Verifying installation ===\n")
all_packages <- c(cran_packages, bioc_packages)
failed_packages <- c()

for (pkg in all_packages) {
  if (require(pkg, character.only = TRUE, quietly = TRUE)) {
    cat(paste("✓", pkg, "loaded successfully\n"))
  } else {
    cat(paste("✗", pkg, "failed to load\n"))
    failed_packages <- c(failed_packages, pkg)
  }
}

if (length(failed_packages) == 0) {
  cat("\n🎉 All R dependencies installed successfully!\n")
  cat("You can now run the BioProof pipeline.\n")
} else {
  cat("\n⚠️  Some packages failed to install:\n")
  cat(paste("  -", failed_packages, collapse = "\n"))
  cat("\nPlease install these manually or check for system dependencies.\n")
}

cat("\nInstallation complete!\n")