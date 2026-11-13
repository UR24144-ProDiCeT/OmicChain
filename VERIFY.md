# Re-running a certified analysis (`omicchain_verify.py`)

`omicchain_verify.py` checks that a certified analysis can be **reproduced**: it re-runs it
from its RO-Crate and compares the new fingerprint with the certified one.

## Steps

1. **Crate integrity**: every certified file matches `bundle_manifest.json` and, with
   `--expected`, the fingerprint published on-chain. Stops if not.
2. **Input data**: the expression matrix and metadata you supply must be the ones certified in
   `data_provenance.txt` (same content). Stops if not.
3. **Re-run** in a separate folder, with the **code taken from the crate** (not the installed one),
   the certified thresholds, and the R library of this OmicChain project. Your own `results/`
   folder is never touched.
4. **Comparison**, component by component.

## Verdicts

| Verdict | Meaning | Exit code |
|---|---|---|
| `REPRODUCED` | identical fingerprint, bit for bit | 0 |
| `RESULTS REPRODUCED` | same results table; another component differs (R environment, bytes of the input files) and is named | 2 |
| `NOT REPRODUCED` | the results differ, or a check failed | 1 |

When the results table differs, a numerical comparison is printed (genes present in only one
table, largest absolute and relative differences per column). It is **information only**: it never
turns a different fingerprint into a pass.

## Use

From the OmicChain project folder (renv set up, as for running the pipeline):

```
python omicchain_verify.py omicchain-crate.zip --expression expression_input.tsv --metadata metadata.tsv --expected <fingerprint read on-chain>
```

- The crate can be the `.zip` or an extracted folder.
- `--metadata` and `--expression` are not needed: the crate includes both (`"include_inputs": true`
  in `omicchain.json`). They are only for crates made without them (older crates, or
  `"include_inputs": false`).
- **The re-run executes the code of the crate.** It starts only if that code is the one installed
  here or an official OmicChain version (`official_code.json`). For other code, read it, then add
  `--trust-crate-code`. See `SECURITY.md`.
- The re-run folder is temporary and removed when the analysis is reproduced; it is kept
  otherwise, with `rerun.log`, so you can inspect it. `--keep` keeps it in every case,
  `--workdir DIR` chooses it (it must not exist yet).
- The re-run takes as long as the original analysis.

## Data origin and R environment

- When the analysed data were loaded from the **Data & reference** tab (public study, FASTQ files
  or count matrix), the crate contains `data_origin.json`, a certified component describing where
  they come from (accession, FASTQ md5 / SHA-256, reference and index fingerprint, Rsubread and
  featureCounts settings, the user's sample annotations, the comparison). The re-run puts it back
  in place and checks that the expression table supplied is the one it describes.
- The R environment is captured on **exactly the packages of the certified `renv.lock`**, not on
  those of this project's current `DESCRIPTION`: a package added to OmicChain since the analysis
  was certified (Rsubread, for example) does not prevent a `REPRODUCED` verdict.
- By default the re-run starts from the certified expression table (downstream part). With
  `--from-raw` it starts from the raw data (next section).

## Re-running from the raw data (`--from-raw`)

The certified data origin lists every raw file with its checksum (it is also printed in the annex of
the PDF certificate). With `--from-raw`, a step is added before the downstream re-run:

| Source of the analysis | What is needed | What is checked and redone |
|---|---|---|
| Public study (GEO / SRA) | nothing: the FASTQ files are downloaded again from the ENA (`--fastq-dir` to use files you already have) | md5 = certified md5 (if the ENA now distributes another file, the verification stops); alignment and counting again |
| Your own FASTQ files | `--fastq-dir` with the files | md5 and SHA-256 = certified; alignment and counting again |
| Count matrix | `--matrix` with the original file | SHA-256 = certified; expression table rebuilt |

For FASTQ files, the reference of the cache (genome, GTF, Rsubread index) must have the certified
fingerprints (`--prepare-reference` downloads and builds it if it is missing; an index built with
another Rsubread version has another fingerprint and stops the verification). The samples, the
library strand detection and the alignment / counting settings are those of the data origin. The
expression table rebuilt must have the certified SHA-256; then the downstream re-run uses it.

```
python omicchain_verify.py omicchain-crate.zip --metadata metadata.tsv --expected <fingerprint> --from-raw
python omicchain_verify.py omicchain-crate.zip --metadata metadata.tsv --from-raw --fastq-dir D:\my_fastq
python omicchain_verify.py omicchain-crate.zip --metadata metadata.tsv --from-raw --matrix counts.csv
```

- Verdicts: `REPRODUCED (from the raw data)`; `NOT REPRODUCED` when the table rebuilt differs (the
  number of genes with other counts is shown); `NOT VERIFIED` when the raw data or the reference
  are not available or not the certified ones (nothing is proven or disproven), or when the
  alignment could not complete.
- The replay folder (`verify_runs/` in the project, or `--raw-workdir`) is removed when the analysis
  is reproduced and kept otherwise: running the command again resumes the downloads and alignments.
- The upstream code (alignment and counting) is the one installed, not part of the crate, but
  the SHA-256 of each of its files is in the certified data origin: installed code that differs
  from it, or another Rsubread version, is reported as a warning.
- Count hours for a FASTQ study (download and alignment, as for the original analysis).

## Limits

- The re-run uses the R packages installed in **this** project. If they are not the certified
  versions, the verdict is at best `RESULTS REPRODUCED`, with the differing versions listed.
  Restoring the exact environment (for example `renv::restore()` from the crate's `renv.lock`, or
  the Docker image) is the way to reach `REPRODUCED` on another machine.
- Floating-point results can differ slightly between machines or library builds; the numerical
  comparison shows by how much, but such a re-run is reported as `NOT REPRODUCED`.
- The re-run is specific to the OmicChain DESeq2 pipeline (Snakemake, padj / |log2FC| thresholds).
  The integrity check alone (`python omicchain_core.py verify`) works for any pipeline.

## In the application

The **Verify** step has a *Re-run and compare (reproducibility)* section: upload the RO-Crate
`.zip` (the page asks for the metadata or the expression matrix only if the crate does not include
them); the fingerprint field is pre-filled with
the one checked on-chain above it. Then choose what to re-run:

- **Downstream only**: the re-run runs in the page (minutes).
- **Everything, from the raw data**: the page reads the data origin in the crate and asks for what
  is needed (a FASTQ folder, the original matrix), shows whether the reference is ready, and runs
  the verification as a background task (you can close the page). The report is shown at the end.

Each component (same / DIFFERENT), the files rebuilt from the raw data, both fingerprints and the
verdict are shown.
