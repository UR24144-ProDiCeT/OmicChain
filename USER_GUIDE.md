# OmicChain — user guide

OmicChain runs an RNA-seq differential expression analysis (DESeq2) and certifies it. Every
component of the analysis (code, parameters, input data, origin of the data, R environment,
results) is hashed, and these hashes are combined into one **composite fingerprint**. The
fingerprint is published on the Base blockchain, which gives a public, dated and unalterable
record. A PDF certificate and an RO-Crate (a zip file with every certified file) let anyone check
the record and re-run the analysis.

OmicChain proves *what* was run, *on which data* and *when*. It does not prove that an analysis is
scientifically right: the choice of samples, design and thresholds remains yours.

**Contents**

1. [Install](#1-install)
2. [Configure (`.env`)](#2-configure-env)
3. [A tour of the application](#3-a-tour-of-the-application)
4. [Your first analysis in ten minutes](#4-your-first-analysis-in-ten-minutes)
5. [Data & reference](#5-data--reference)
6. [Analysis](#6-analysis)
7. [Blockchain proof](#7-blockchain-proof)
8. [Certificate and publisher identity](#8-certificate-and-publisher-identity)
9. [Verify an analysis](#9-verify-an-analysis)
10. [Where your files are](#10-where-your-files-are)
11. [Questions and problems](#11-questions-and-problems)

---

## 1. Install

There are three ways to install OmicChain. They run the same certified code: the same analysis
gives the same fingerprint, whichever is used.

| | For whom | What to do |
|---|---|---|
| **Windows installer** | Windows users, no programming needed | download and run `OmicChain-<version>-setup.exe` |
| **Container image** | Linux, macOS, Windows with Docker; verifying in the authors' exact environment | `docker compose up` ([DOCKER.md](DOCKER.md)) |
| **Source code** | developers, other systems | clone the repository ([INSTALL.md](INSTALL.md)) |

### Windows installer

1. Download `OmicChain-<version>-setup.exe` from the
   [releases](https://github.com/UR24144-ProDiCeT/OmicChain/releases) (section *Assets*).
2. Run it. Windows may show *"Windows protected your PC"* because the installer is not signed:
   click **More info**, then **Run anyway**. No administrator rights are needed.
3. Start OmicChain from the **Start menu** (or the desktop shortcut, if you chose one).

A black window opens, then OmicChain opens in your browser at <http://localhost:8501> after a few
seconds. **Keep the black window open while you use OmicChain**; closing it stops OmicChain.

The installer contains its own R (4.5.1), the exact R packages of the certified environment
(`renv.lock`), its own Python and the application. It does not touch any other R or Python of the
computer, and there is nothing else to install. To uninstall: *Settings → Apps → OmicChain*. Your
analyses are kept (see [section 10](#10-where-your-files-are)).

### Container image and source code

See [DOCKER.md](DOCKER.md) and [INSTALL.md](INSTALL.md). In both cases, the application is then
opened at <http://localhost:8501>.

---

## 2. Configure (`.env`)

The `.env` file holds the settings of the blockchain and, if you publish, your publishing account.
It is a plain text file named exactly `.env`, placed in the project folder
([section 10](#10-where-your-files-are)). Start from `.env.example` (in the project folder, or in
the repository): copy it, rename the copy `.env`, and fill it in with a text editor.

**Without `.env`**, you can prepare data, run, explore and certify analyses (the fingerprint is
computed), but not publish nor check a fingerprint on-chain from the application.

### To check fingerprints on-chain (no wallet needed)

```
INFURA_URL_MAINNET=https://mainnet.base.org
INFURA_URL_TESTNET=https://sepolia.base.org
CONTRACT_PROOF_ADDRESS_MAINNET=0x1890f3a5877266e41F85faC22C389062fFCBD300
CONTRACT_PROOF_ADDRESS_TESTNET=0x1890f3a5877266e41F85faC22C389062fFCBD300
CONTRACT_PROOF_DEPLOY_BLOCK_MAINNET=51832471
```

The two URLs are the public nodes of Base; an account with a node provider (Infura, Alchemy...)
is faster and more reliable for frequent use. The contract address is that of the OmicChain proof
contract (`Proof.sol`). To check a certificate published with another contract, use the address
printed with that certificate.

### To publish

Add a publishing account:

```
ACCOUNT_ADDRESS=0x...        your public address
PRIVATE_KEY=...              the private key of that same address
```

- Use a **wallet dedicated to OmicChain**, holding only what publication needs. A publication costs
  a fraction of a cent of ETH on Base Mainnet; the application shows the estimated cost and your
  balance before publishing.
- **For a first try, use Base Testnet**: it is free (test ETH from a Base Sepolia *faucet*), and a
  proof published there has no public value.
- **Never share your `.env`**, never send it by e-mail, never put it in a repository. Anyone who has
  `PRIVATE_KEY` can publish in your name. See [SECURITY.md](SECURITY.md).

The other variables of `.env.example` are optional: `OMICCHAIN_VERIFY_URL` (your own verification
page, [PORTAL.md](PORTAL.md)) and the `ORCID_*` variables ([section 8](#8-certificate-and-publisher-identity)).

Restart OmicChain after changing `.env`.

---

## 3. A tour of the application

- **Sidebar — Blockchain settings.** *Select Network*: **Base Mainnet** (real, public, paid) or
  **Base Testnet** (free test network). Below: the node, your account and the proof contract in use.
- **How OmicChain works** — a short reminder of the five steps.
- **Certified analyses on this computer** — the analyses already certified in this project. Choose
  one and click **Open** to reload it (proof, certificate) without running it again.
- **Five tabs**, used in order:

| Tab | What you do there |
|---|---|
| **Data & reference** | choose the data (public study, your FASTQ files, a count matrix), describe the samples, choose the comparison; prepare the reference genome |
| **Analysis** | check the samples, look at the quality control, explore the thresholds, run and certify DESeq2 |
| **Blockchain proof** | publish the fingerprint on Base |
| **Certificate** | generate the PDF certificate and the RO-Crate; optionally link your ORCID iD |
| **Verify** | check a fingerprint on-chain; re-run a certified analysis from its crate |

Long tasks (downloads, index building, alignment, counting) run in the background: they are listed
under **Background tasks** at the bottom of the *Data & reference* tab, with their progress, their
log and a **Cancel** button. You can close the browser tab; the tasks go on, and reopening
<http://localhost:8501> shows where they are.

---

## 4. Your first analysis in ten minutes

This uses the simulated example shipped with OmicChain (10 samples: 5 control, 5 treated). Choose
**Base Testnet** in the sidebar.

1. **Data & reference** → *Start from*: **A count matrix**.
2. Load `examples/simulated_counts.tsv` (in the project folder; with the installer it is in
   `%USERPROFILE%\OmicChain\examples`).
3. In *Samples*, load `examples/simulated_samples.tsv` in **…or import a sample sheet**. A column
   `condition` is added; the comparison *condition: control vs treated* is proposed.
4. Click **Use these data for the analysis**.
5. **Analysis** tab: the control and treatment samples are already selected. Look at the quality
   control (PCA, library sizes), then click **Run and certify the analysis**. The composite
   fingerprint appears when it is done (about one minute).
6. **Blockchain proof** tab → **Publish Proof** (on Testnet, with test ETH in your wallet).
7. **Certificate** tab → **Generate Certificate (PDF) + RO-Crate**, then download both files.
8. **Verify** tab → **Verify on-chain** (fingerprint and transaction are already filled in). Then,
   under *Re-run and compare*, load the crate you have just downloaded, keep *Downstream only*, and
   click **Re-run and compare**: the verdict is **REPRODUCED**.

Without a wallet, stop after step 5: the analysis is certified on your computer, it is simply not
published.

---

## 5. Data & reference

*Start from* offers three sources. Whatever the source, its origin (accession, raw files and their
checksums, reference, alignment and counting, your sample annotations) is certified with the
analysis and listed in the certificate.

### 5.1 A public study (GEO / SRA)

1. **Accession**: a GEO series (`GSE…`) or an SRA / ENA project (`SRP…`, `ERP…`, `PRJNA…`,
   `PRJEB…`), for example `GSE52778`. **Studies folder**: where each study gets its own sub-folder.
2. **Get the study's metadata**. OmicChain queries GEO and ENA and lists the samples, with
   warnings if something needs attention (mixed single-end and paired-end samples, for example).
3. **Samples** — describe them if the public metadata are not enough (see
   [5.4](#54-describing-the-samples)).
4. **Download** (optional) — choose the samples and click **Download the selected samples**. Each
   FASTQ file is checked against its md5 published by the ENA. The free disk space is shown. This
   step can be skipped: the alignment downloads what is missing.
5. **Comparison** — the condition column, the reference level (control), the tested level
   (treatment) and the samples. log2 fold-changes are *tested vs reference*. At least two samples
   per group are needed, all of the same sequencing type.
6. **Alignment and counting** — choose the **Index** (see [5.5](#55-reference-genome-and-index)) and
   click **Download, align and count**. Reads are aligned with Rsubread and counted per gene on the
   GENCODE annotation. This takes from tens of minutes to several hours depending on the number and
   size of the samples; it can be resumed if interrupted.
7. When the counts are ready: **Use these counts for the analysis**.

### 5.2 Your FASTQ files

1. **Folder containing your FASTQ files** (sub-folders included). Accepted: `.fastq`, `.fq`,
   `.fastq.gz`, `.fq.gz`. Read 1 / read 2 and Illumina lanes (`…_S1_L001_R1_001.fastq.gz`) are
   recognised and grouped by sample. The files are not copied or modified.
2. **Name of this dataset**, then **Register the files (compute their fingerprints)**: the md5 and
   SHA-256 of every file are recorded. If the folder changes later, OmicChain says so and asks you to
   register the files again.
3. Describe the samples ([5.4](#54-describing-the-samples)), choose the comparison, then align and
   count as in 5.1 (steps 5 to 7).

### 5.3 A count matrix

A TSV or CSV file: gene identifiers in the first column, one column per sample, **raw counts**
(integers, not normalised values, not TPM/FPKM). The original file is kept and its SHA-256 is
certified. Describe the samples ([5.4](#54-describing-the-samples)), choose the comparison, then
**Use these data for the analysis**.

### 5.4 Describing the samples

Public metadata often hide the conditions in the sample titles, and a matrix or FASTQ folder has
no description at all. In *Samples*:

- **New column** → **Add column** (for example `condition`, `cell_line`, `batch`).
- **Fill by keyword**: *If* `title` *contains* `non targeting` *then column* `condition` *= value*
  `control` → **Apply**. The keyword is case-insensitive. Repeat for each value.
- The cells can also be edited directly in the table.
- **…or import a sample sheet**: a TSV or CSV file with a `sample` column; its other columns are
  added.

Your columns can be compared, or used as covariate in the analysis. They, and the rules you
applied, are recorded in the certified provenance. If you change them after the counting, OmicChain
says so: count again so that the certified annotations match the analysis.

### 5.5 Reference genome and index

FASTQ files need a GENCODE human reference (GRCh38) and an Rsubread index, prepared once and
shared by all your projects. In the **Reference (genome, annotation, index)** section:

- **Reference cache folder** and **GENCODE release** (48 by default).
- **Download and build** → **Prepare the gapped index** or **Prepare the full index**. GENCODE is
  downloaded, its md5 checked, and the index built.

| Index | Build | Disk | RAM to align | Use |
|---|---|---|---|---|
| gapped | about 1 h | smaller | about 9 GB | most computers |
| full | about 3 h | larger | about 19 GB | workstations |

Both indexes together take about 22 GB. The type of index is part of the certified provenance: the
same samples aligned on the gapped and on the full index give slightly different counts, hence
different fingerprints.

- **Use an index already built** imports an index you already have (copied into the cache, or used
  where it is). **Verify fingerprints** checks the files of the cache against their recorded
  SHA-256.

---

## 6. Analysis

1. **Samples compared** — *Control Samples* and *Treatment Samples*, pre-filled from the
   comparison. **Covariate (optional)**: a column of your description (cell line, batch, donor...)
   added to the model, which becomes `~ covariate + group`. A column that cannot be used (one value
   only, or confounded with the groups) is not offered and the reason is given.
2. **Quality control of the samples (not certified)** — PCA of the selected samples, library sizes
   and, for FASTQ data, the alignment and assignment rates per sample: to spot an outlier or a
   mislabelled sample before certifying.
3. **Explore the thresholds (not certified)** — **Run exploration** fits DESeq2 once and shows how
   many genes are significant for a range of adjusted p-value and |log2 fold-change| thresholds,
   and which genes are robust across them. **Use padj < … and |log2FC| ≥ … for the analysis** copies
   a pair of thresholds into the parameters. Nothing here is certified or published.
4. **Analysis parameters** — *Adjusted p-value threshold* and *|log2 fold-change| threshold*.
5. **Run and certify the analysis** — runs the pipeline (Snakemake, DESeq2, enrichment, figures,
   report) and computes the composite fingerprint.

Each pair of thresholds (and each covariate) has its own results folder, for example
`results/padj_0.05_lfc_1.0`: running again with other thresholds never overwrites an earlier
analysis. Running again with the *same* thresholds and covariate on other data replaces the analysis
of that folder; a crate already downloaded stays valid. **Certified analysis parameters** shows the
parameters that were certified.

*Advanced: replace the pipeline files* lets you run your own `Snakefile` or R script. Its hash is
certified like any other code, but it is not a known OmicChain version: whoever re-runs your crate is
warned and shown the code before it runs.

---

## 7. Blockchain proof

The tab shows the composite fingerprint and its **identicon** (a small picture computed from the
fingerprint, to compare two fingerprints at a glance).

- Check the network in the sidebar. On **Base Mainnet**, the estimated cost and your balance are
  shown, and you must tick *I confirm the publication on Base Mainnet: it is public, permanent and
  paid in real ETH*.
- **Publish Proof**. After confirmation, the block and the transaction are shown, with a link to the
  block explorer (basescan).

A fingerprint is published once. If the connection is lost after sending, OmicChain remembers the
transaction: use **Retrieve this transaction** instead of publishing again. *Publish it again
anyway* exists but creates a new, paid transaction.

What is published is the fingerprint only: no data, no gene, no sample name leaves your computer.

---

## 8. Certificate and publisher identity

**Generate Certificate (PDF) + RO-Crate**, then download:

- `omicchain_certificate.pdf` — the fingerprint, the network, block and transaction, a QR code
  pointing to the on-chain record, the hash of every component, the analysis parameters, and an
  annex listing every raw file with its checksum;
- `omicchain-crate.zip` — the RO-Crate: every certified file (code, parameters, input table,
  provenance, R environment, results, figures, report) and its manifest. This is the file to share
  with a reviewer or to deposit with your data (Zenodo, journal supplementary material...).

### Publisher identity (ORCID) — optional

By default, a certificate shows the publishing *address*, not a person. **Link my ORCID iD** signs,
with your publishing key, a link between your ORCID iD and that address, after an ORCID login. The
certificate then shows who published it, and anyone can check the link.

This needs your own ORCID API client (`ORCID_CLIENT_ID`, `ORCID_CLIENT_SECRET` in `.env`). Setting
it up takes a few minutes and is described in [IDENTITY.md](IDENTITY.md); try it first with the
ORCID sandbox. Only approve ORCID logins you started yourself.

---

## 9. Verify an analysis

### 9.1 Check a fingerprint on-chain (seconds)

In **Verify a certificate**, paste the **composite fingerprint** and the **transaction ID** printed
on the certificate → **Verify on-chain**. OmicChain reads the transaction and confirms that this
exact fingerprint was published, when, and by which address. Without the transaction ID:
*Advanced: search without a transaction ID*.

Nothing is executed for this check. It can also be done without installing anything, from the
public verification page <https://ur24144-prodicet.github.io/OmicChain/> (the QR code of the
certificate).

### 9.2 Re-run and compare (reproducibility)

Load the **RO-Crate (.zip)** and, recommended, the **fingerprint published on-chain**. Then choose
**What to re-run**:

- **Downstream only** — from the certified expression table: DESeq2, enrichment, figures (minutes);
- **Everything** — from the raw data: the FASTQ files (downloaded again from the ENA, or a folder
  where you already have them) or the original count matrix, then alignment, counting and the
  analysis (hours for FASTQ; the reference is prepared if needed).

A crate made by an early version of OmicChain may lack the sample table of the analysis: the
application then asks for the `metadata.tsv` of the analysis.

The crate says where its code comes from. If it contains code that is not a known OmicChain
version, OmicChain shows it and runs it only after you tick *I have read this code and I accept to
run it on this computer*.

### 9.3 Verdicts

| Verdict | Meaning |
|---|---|
| ✅ **REPRODUCED** | identical fingerprint, bit for bit |
| ✅ **RESULTS REPRODUCED** | same results table; another component differs (R environment, bytes of the input files) and is named |
| ≈ **RESULTS EQUIVALENT** | same code, parameters and data; the results differ only by computing noise: same genes, same order, same direction of change, every value within 0.1 % (relative) |
| ❌ **NOT REPRODUCED** | the results differ, or a check failed; the differing components are listed |
| **NOT VERIFIED** | the re-run could not be done (raw data unavailable, interrupted run, code of the crate refused...) |

The report also shows the computing platform of the certification and of the re-run (system, R,
linear algebra library). **RESULTS EQUIVALENT** is expected between different systems (Windows and
Linux, for example): their numerical libraries round the last digits of the smallest p-values
differently. To obtain **REPRODUCED** bit for bit, verify on the same platform, for example in the
container image ([DOCKER.md](DOCKER.md)).

The same verification runs from the command line, which suits scripts and reviewers:

```
python omicchain_verify.py omicchain-crate.zip --expected <fingerprint read on-chain>
python omicchain_verify.py omicchain-crate.zip --from-raw
```

Details: [VERIFY.md](VERIFY.md).

---

## 10. Where your files are

| | Project folder (analyses, results, `.env`) | Reference genomes and indexes |
|---|---|---|
| Windows installer | `%USERPROFILE%\OmicChain` (e.g. `C:\Users\you\OmicChain`) | `%USERPROFILE%\OmicChain\references` |
| Container image | `omicchain-data`, next to `docker-compose.yml` | `omicchain-refs`, next to `docker-compose.yml` |
| Source code | the folder of the clone | `%LOCALAPPDATA%\OmicChain\references` (Windows), `~/.omicchain/references` (Linux, macOS) |

In the project folder:

| Folder / file | Content |
|---|---|
| `results/` | one folder per certified analysis (`padj_…_lfc_…`): results, figures, report, provenance, fingerprint |
| `studies/` | one folder per dataset: metadata, FASTQ files downloaded, your annotations, counts |
| `jobs/` | logs of the background tasks |
| `.env` | your settings and publishing key — never share it |
| `orcid_link.json` | your ORCID link, if you made one |

The installer keeps the project folder when OmicChain is uninstalled or updated. To update, run the
installer of the new version: your analyses stay where they are.

---

## 11. Questions and problems

**OmicChain does not open in the browser.** Wait a few seconds more (the first start is slower),
then open <http://localhost:8501> yourself. If the black window shows an error, keep it: it says what
went wrong.

**"This OmicChain is reachable from other computers on the network."** The file
`.streamlit/config.toml` is missing from the project folder or was changed. Put back the original
(address `127.0.0.1`) and restart. See [SECURITY.md](SECURITY.md).

**"The wallet does not hold enough ETH".** Add ETH on Base to the address shown in the sidebar
(Base Mainnet), or test ETH from a faucet (Base Testnet). Check that the right network is selected.

**The alignment stops: not enough disk space or memory.** The free disk space and RAM are shown
before the alignment. Free some space, or use the gapped index; the run can then be resumed with the
same button.

**A background task shows "interrupted".** It stopped without finishing (computer restarted,
process killed). Start it again: what was already done (downloads, aligned samples) is kept.

**I ran the same analysis twice and got two different fingerprints.** Something certified differs:
the samples, the thresholds, the covariate, the index type, the GENCODE release, the input data or
your annotations. *Certified analysis parameters* and the certificate list every component and its
hash, which tells which one.

**Can I publish an analysis made on my own data without making the data public?** Yes. Only the
fingerprint is published. The data stay on your computer, and you decide whether to share the
crate.

**I found a bug, or a security problem.** Open an issue on
<https://github.com/UR24144-ProDiCeT/OmicChain/issues>; for a security problem, follow
[SECURITY.md](SECURITY.md).
