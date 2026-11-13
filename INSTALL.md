# OmicChain — installation

OmicChain runs a Snakemake/R differential-expression pipeline, fingerprints
everything that goes into the result (code, parameters, input data, R
environment, output) and publishes the fingerprint on-chain.

## Requirements

- **Python 3.10+**
- **R 4.5.x** — the R packages are pinned to Bioconductor 3.22, which needs R 4.5
- An internet connection for the first install
- Windows only: [Rtools](https://cran.r-project.org/bin/windows/Rtools/) if R
  reports that a package has to be compiled (usually not needed)

## 1. Python packages

```
python -m venv .venv
.venv\Scripts\activate          (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

`requirements.txt` accepts the tested major versions of each package. For the exact versions
OmicChain was tested with (Python 3.13), use `pip install -r requirements.lock.txt` instead.

## 2. R packages (exact versions)

The R packages are pinned in `renv.lock`. From the **project folder**:

```
Rscript -e "renv::restore()"
```

This installs renv (first run) and every package at the recorded version, including
Rsubread (alignment and counting of FASTQ files, Data & reference tab).

If you add Rsubread to an existing project instead (OmicChain versions before step 6e), run
`Rscript install_dependencies.R` from the project folder: it installs what is missing and
records the environment (`renv::snapshot`), which also clears the "project is out-of-sync"
message.
It takes several minutes. Then check:

```
Rscript -e "library(DESeq2); cat('DESeq2 OK\n')"
```

Notes:
- Always run R commands **from the project folder**: the `.Rprofile` there
  activates the project's private R library.
- Do **not** run `renv::init()`: the project is already set up.
- If you get `there is no package called 'renv'`, run
  `Rscript -e "install.packages('renv')"` and repeat the restore.

## 3. Configuration

Copy `.env.example` to `.env` and fill it in (RPC URLs, contract addresses,
account). Use the contract addresses published with the certificates you want
to verify, or deploy `Proof.sol` yourself. `PRIVATE_KEY` is only needed to
publish; never share your `.env`. Use a wallet dedicated to OmicChain, and keep the file
`.streamlit/config.toml` in the project folder (it makes the application reachable from your
computer only): see `SECURITY.md`.

## 4. Run

```
streamlit run OmicChain.py
```
