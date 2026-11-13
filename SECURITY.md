# Security of an OmicChain installation

OmicChain runs on your computer and holds a key that can publish on the blockchain. This page
says what protects it and what is your responsibility.

## Access to the application

- The application is reachable **from your computer only**: `.streamlit/config.toml` sets
  `address = "127.0.0.1"`. Keep this file in the project folder. Without it, Streamlit listens on
  the whole network, and anyone who reaches the page could publish with your key, start tasks or
  browse the folders you give it. The application shows a red warning when it is not local-only.
- To use OmicChain from another computer, do not change the address: use a remote desktop, an SSH
  tunnel or a VPN.

## Secrets

- `.env` holds your secrets (`PRIVATE_KEY`, `ORCID_CLIENT_SECRET`, RPC URLs with their API key).
  It is listed in `.gitignore`: never commit, share or send it. Only `.env.example` is public.
- **Use a wallet dedicated to OmicChain**, holding only what publishing costs (a few cents of ETH
  on Base). The key is stored in clear text in `.env`: anyone who can read your files can use it.
  If the key leaks, the loss is limited to that wallet; your past certificates stay valid (they are
  on-chain), and you publish the next ones from a new address.
- The public verification page (`build_portal.py`) never contains a secret.

## Verifying a crate received from someone else

- **Checking a fingerprint on-chain executes nothing** and is always safe.
- **Re-running a crate executes its code** (Snakefile, R scripts) on your computer. OmicChain
  therefore compares the crate's code with the code installed here and with the official versions
  listed in `official_code.json`:
  - known code: the re-run starts;
  - unknown code (modified pipeline, or a crate that is not what it claims): the re-run stops. The
    application shows the code so that you can read it, and runs it only after you tick "I have
    read this code and I accept to run it" (`--trust-crate-code` on the command line).
- A crate cannot write outside its extraction folder (paths are checked), and a re-run never touches
  your own `results/`.
- For crates of unknown origin, prefer an isolated environment (the Docker image, when available).

## Names and values coming from outside

Accessions typed in the application, identifiers returned by GEO and the ENA, FASTQ file names,
and everything read from a crate (paths of its manifest, parameters, data origin) are used to
name files and to build commands. They are checked in one place (`omicchain_names.py`) before any
use, and refused with a message rather than silently altered:

- an accession must be a GEO series or an SRA / ENA study (`GSE...`, `SRP...`, `PRJNA...`);
- sample, run and file names must be usable as file names on Windows and Linux (no `/ \ : * ? " < > |`,
  no `,` or `;`, no leading `.` or `-`); spaces and accents are fine;
- the paths listed in a crate's manifest must stay inside the crate; its parameters must be numbers
  and a valid column name;
- commands are always run as argument lists, never through a shell, and the alignment settings are
  passed in a JSON file: a condition level such as `0`, `No` or a folder containing `#` stays the
  text you typed.

## Downloads

- FASTQ files (ENA) and the reference (GENCODE) are downloaded over HTTPS and checked against the
  md5 published by their source. A file that does not match is rejected.
- R packages are installed from the sources recorded in `renv.lock`.
- Python packages: `requirements.txt` bounds each package to tested major versions;
  `requirements.lock.txt` lists the exact versions OmicChain was tested with.

## What is certified, and what is not

- The fingerprint covers the analysis code, the parameters, the input data, the data origin (raw
  files with their checksums, reference, and the SHA-256 of the alignment / counting code), the R
  environment and the results.
- The blockchain proves that this fingerprint existed at a date, published by an address (linked
  to an ORCID iD if you set it up). It does not prove that the analysis is scientifically right.

## Reporting a problem

Open an issue on the GitHub repository, or write to the maintainers privately for anything that
should not be public before it is fixed.
