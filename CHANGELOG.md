# Changelog

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
