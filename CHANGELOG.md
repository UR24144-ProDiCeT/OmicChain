# Changelog

## Unreleased

**Added**
- `USER_GUIDE.md`: the user guide, from the installation to the verification of an analysis.
- `examples/simulated_samples.tsv`: the sample sheet of the simulated example (first analysis in two clicks).

**Fixed**
- The application stopped at start-up when `.env` was missing, or held no publishing account
  (`ACCOUNT_ADDRESS`, `PRIVATE_KEY`). It now starts: without network settings, analyses are run and
  certified; with network settings only, fingerprints are checked on-chain. The missing settings are
  named where they are needed.
- Background tasks on Windows: writing the state of a task could fail ("Access is denied") when another
  process was reading it at that moment. The write and the read are now retried for a short time.

## 1.0.0-rc4 — 2026-11

**Added**
- Windows installer (`installer/`): one `OmicChain-<version>-setup.exe` that contains R 4.5.1, the R packages of
  `renv.lock`, Python 3.12 with the packages of `requirements.windows.lock.txt`, and the application. It installs
  without administrator rights; nothing else is needed (no `renv::restore()`, no `pip install`). Analyses are kept
  in `%USERPROFILE%\OmicChain`. The installer is built, installed and tested by the continuous integration
  (workflow `windows-installer`) and attached to each release.
- `platform.json` records the distribution used (Windows installer and its version).

**Changed**
- Snakemake is started with the Python that runs OmicChain (`python -m snakemake`), not from the PATH.

**Fixed**
- Background tasks (download, alignment, counting): a task could be shown "interrupted" for a moment while it
  was still running, when its heartbeat file was read during its rewriting. The heartbeat is now written
  atomically, and its modification time is used if it cannot be read.
- `requirements.windows.lock.txt`: cryptography 41.0.7 (2023, known security issues) replaced by 50.0.1, the
  version of the Linux lock file and of the container image. The Windows lock file is now installed and tested
  as such by the continuous integration (Python 3.12, job `windows-lock`).

## 1.0.0-rc3 — 2026-11

**Fixed**
- R environment record: `BiocVersion` is now one of the packages of the analysis (`DESCRIPTION`, `renv.lock`).
  The Bioconductor release is read from it; an installation made with `renv::restore()` did not install it,
  so its record lacked the Bioconductor release and differed from the record of the same packages
  installed otherwise (BiocManager, container image).

## 1.0.0-rc2 — 2026-11

**Added**
- Verdict `RESULTS EQUIVALENT` (exit code 3): same code, parameters and data, and a results table that differs
  only by computing noise (same genes and directions of change, values within a relative tolerance, 0.001 by
  default, `--tolerance`). DESeq2 is not identical bit for bit across systems; this verdict names the
  difference instead of reporting the analysis as not reproduced.
- Computing platform recorded with each analysis, outside the fingerprint (`platform.json`: system, R,
  linear algebra library, OmicChain image), and shown by the verification.
- OmicChain image: linear algebra on a single thread, so that every run gives the same bytes.
- Reference container image (`Dockerfile`, `docker-compose.yml`, `DOCKER.md`), built, tested and published by
  GitHub Actions. The build fails if the R environment of the image differs from `renv.lock`.
- `renv.lock` at the root of the repository is now the complete environment (154 packages, R 4.5.1,
  Bioconductor 3.22).
- Tests with the real R packages: environment record, whole pipeline with DESeq2, verification (`tests/run_all.py real`).

**Fixed**
- Line endings: every file that OmicChain writes, and that may be certified (parameters, input data record,
  data origin, results table, upstream counts), is now written with Unix line endings on every system. On
  Windows they were written with Windows line endings, so that an analysis certified on Windows could not
  give the same fingerprint when re-run on Linux or in the container. New test `test_line_endings`.
- R environment component: the record certified with each analysis (`renv.lock`) now lists the R version,
  the Bioconductor release and every package the analysis depends on, with its version, wherever R loads
  it from. Before, `renv::snapshot` only saw the project's private library, so on a computer where the
  packages were installed elsewhere the certified record held `renv` alone. The certification now stops
  if a package of the analysis is not installed.
- The record holds nothing specific to the computer (name, version and source of each package, Unix line
  endings), so that two computers with the same versions produce the same bytes.
- Analyses certified before this change are still replayed with the former method by Verify.

## 1.0.0-rc1 — 2026-11

A complete rewrite of the first public version.

**Data sources**
- Three sources: a public study (GEO / SRA accession, FASTQ downloaded from the ENA with md5 checks and
  resumable downloads), the user's own FASTQ files, or a count matrix.
- Alignment and counting with Rsubread on a GENCODE reference that is downloaded, built and
  fingerprinted; long steps run as background tasks.
- Sample annotation in the application (columns, keyword rules, imported sample sheets).

**Analysis**
- Optional covariate in the DESeq2 model (`~ covariate + group`), with checks before running.
- Quality control before the analysis (PCA, library sizes, alignment rates) and visual exploration of
  the thresholds.
- List of the certified analyses of the computer, each can be reopened.

**Certification**
- Composite fingerprint driven by a manifest (`omicchain.json`); a certified **data origin**
  component describes the raw files (checksums), the reference, the alignment settings, the hash of
  the upstream code and the user's annotations.
- RO-Crate with the certified files, the metadata and the expression table; PDF certificate with an
  annex listing every raw file.
- Optional link between the publishing account and an ORCID iD.

**Verification**
- Re-run from the crate: from the expression table, or from the raw data (FASTQ downloaded again or
  provided; original matrix), with a verdict per step.
- The code of a crate is compared with the installed and official versions before being executed.
- Public page to look a fingerprint up on-chain.

**Security**
- Application reachable from the local computer only; confirmation, balance and cost before a mainnet
  publication; limits on crate size; names coming from outside checked before use.

**Removed**
- NFT minting and IPFS storage of the first version.

## 0.1.0 — 2026-02

First public version (deposited at the Agence pour la Protection des Programmes and archived in
Software Heritage): DESeq2 pipeline from a count matrix, SHA-256 hashes of the pipeline and results,
publication on Base, NFT minting.
