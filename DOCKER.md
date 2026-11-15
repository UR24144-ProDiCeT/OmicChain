# OmicChain in a container

The OmicChain image is the reference environment of the software: R 4.5.1, the R packages at the
versions of `renv.lock`, Python 3.13 with the versions of `requirements.lock.txt`, and the
application. Use it to run OmicChain without installing R or Python, or to verify a certified
analysis in the environment it was built for.

The image is built and tested by GitHub Actions (`.github/workflows/docker.yml`): the build stops
if the R environment of the image is not, byte for byte, the one recorded in `renv.lock`, and the
image is published only after the whole pipeline ran in it with the real DESeq2.

## What you need

- Docker Desktop (Windows, macOS) or Docker Engine (Linux)
- several GB of disk space for the image; to align FASTQ files, about 6 GB more for the gapped human
  index or 16 GB for the full one, plus the FASTQ files themselves
- for alignment, enough memory given to Docker (Docker Desktop: Settings > Resources): about 8 GB
  with the gapped index, 19 GB with the full one

## Start the application

Create a folder for OmicChain, put `docker-compose.yml` in it, open a terminal there and run:

```
docker compose up
```

Then open <http://localhost:8501>. Stop with Ctrl+C.

Two folders appear next to `docker-compose.yml`:

| Folder | Content |
| --- | --- |
| `omicchain-data` | the project folder: your analyses, results, crates, and your `.env` file |
| `omicchain-refs` | reference genomes and indexes, kept between runs |

Without Docker Compose, the same thing is:

```
docker run --rm -p 127.0.0.1:8501:8501 -p 127.0.0.1:8765:8765 -v ./omicchain-data:/work -v ./omicchain-refs:/refs ghcr.io/ur24144-prodicet/omicchain:latest
```

## Configuration and secrets

Nothing secret is in the image. To publish on the blockchain or to link an ORCID iD, copy
`.env.example` to `omicchain-data/.env` and fill it in, exactly as for a normal installation (see
`INSTALL.md`). The file stays on your computer. Verifying an analysis needs no `.env`.

The application is published on `127.0.0.1` only: it is reachable from your computer, not from
the network. Do not replace `127.0.0.1` by `0.0.0.0` in the port settings unless you protect the
access yourself.

## Verify a certified analysis

Put the crate received from the authors in `omicchain-data`, then:

```
docker compose run --rm omicchain verify crate.zip --expected <fingerprint read on the blockchain>
```

From the raw data (downloads the FASTQ files again and needs the reference index):

```
docker compose run --rm omicchain verify crate.zip --expected <fingerprint> --from-raw --prepare-reference
```

All the options of `VERIFY.md` are available. The same check can be done in the application
(Verify tab).

## Use exactly the same image as the authors

A tag such as `latest` can change; a digest cannot. To use precisely one image, name it by its
digest (shown in the summary of each `docker` run on GitHub, and on the package page):

```
docker pull ghcr.io/ur24144-prodicet/omicchain@sha256:<digest>
```

and put the same reference after `image:` in `docker-compose.yml`.

## Build the image yourself

```
docker compose build
```

The first build compiles the Bioconductor packages and takes about an hour.

## Run the tests of the image

```
docker compose run --rm omicchain test real     # R environment record, whole pipeline with DESeq2, verification
docker compose run --rm omicchain test fast     # application tests
```

## Notes

- **Updates.** At each start, the files of OmicChain in `omicchain-data` are replaced by those of
  the image; your own files are never removed.
- **Linux.** Files are created by the user of the container (root by default). To create them as
  yourself, add `user: "1000:1000"` (your `id -u`:`id -g`) under `omicchain:` in `docker-compose.yml`.
- **Same fingerprint outside the container.** The R environment record lists R, Bioconductor and
  package versions only. A computer with the same versions therefore produces the same record; if
  the results table is also identical, the fingerprint is identical. If a version differs, Verify
  reports `RESULTS REPRODUCED` and names the packages that differ.
