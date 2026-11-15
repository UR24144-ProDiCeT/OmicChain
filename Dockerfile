# OmicChain reference image: R 4.5.1 with the R packages of renv.lock, Python 3.13 with the
# packages of requirements.lock.txt, and the application. No secret is ever copied in the image.
FROM rocker/r-ver:4.5.1

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC

RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common ca-certificates curl git \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      python3.13 python3.13-venv python3.13-dev build-essential cmake pkg-config poppler-utils \
      libcurl4-openssl-dev libssl-dev libxml2-dev zlib1g-dev libbz2-dev liblzma-dev libicu-dev \
      libpng-dev libtiff-dev libjpeg-dev libfontconfig1-dev libfreetype6-dev libharfbuzz-dev \
      libfribidi-dev libcairo2-dev libglpk-dev libgmp-dev libsqlite3-dev \
 && rm -rf /var/lib/apt/lists/*

# R packages, at the versions recorded in renv.lock (binaries from Posit Package Manager when
# they exist, sources otherwise). Bioconductor release: the one named in renv.lock.
COPY renv.lock /opt/omicchain/renv.lock
RUN . /etc/os-release && export VERSION_CODENAME \
 && Rscript -e 'options(repos = c(CRAN = sprintf("https://packagemanager.posit.co/cran/__linux__/%s/latest", Sys.getenv("VERSION_CODENAME"))), Ncpus = parallel::detectCores())' \
            -e 'install.packages(c("renv", "BiocManager", "jsonlite"))' \
            -e 'lock <- jsonlite::fromJSON("/opt/omicchain/renv.lock", simplifyVector = FALSE)' \
            -e 'BiocManager::install(version = lock$Bioconductor$Version, ask = FALSE, update = FALSE)' \
            -e 'options(renv.config.pak.enabled = FALSE, renv.consent = TRUE)' \
            -e 'renv::restore(lockfile = "/opt/omicchain/renv.lock", library = .libPaths()[1], repos = getOption("repos"), prompt = FALSE)' \
 && rm -rf /tmp/Rtmp* /root/.cache

# Python packages, at the versions OmicChain is tested with (plus the test-only packages).
COPY requirements.lock.txt requirements-dev.txt /opt/omicchain/
RUN python3.13 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r /opt/omicchain/requirements.lock.txt -r /opt/omicchain/requirements-dev.txt
ENV PATH=/opt/venv/bin:$PATH

COPY . /opt/omicchain

# The build stops here if the R environment of the image is not, byte for byte, the one of renv.lock.
ENV RENV_CONFIG_AUTOLOADER_ENABLED=false
RUN cd /opt/omicchain \
 && Rscript snapshot_renv.R /tmp/image.lock \
 && (cmp /tmp/image.lock renv.lock || (diff /tmp/image.lock renv.lock; echo "The R environment of the image differs from renv.lock"; exit 1)) \
 && rm /tmp/image.lock

ARG OMICCHAIN_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="OmicChain" \
      org.opencontainers.image.description="Certified and independently verifiable RNA-seq differential expression analyses" \
      org.opencontainers.image.source="https://github.com/UR24144-ProDiCeT/OmicChain" \
      org.opencontainers.image.licenses="LGPL-3.0-only" \
      org.opencontainers.image.version="${OMICCHAIN_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

# /work: the project folder of the user (analyses, results, .env). /refs: reference genomes and indexes.
ENV OMICCHAIN_CONTAINER=1 \
    OMICCHAIN_IMAGE_VERSION=${OMICCHAIN_VERSION} \
    OMICCHAIN_IMAGE_REVISION=${VCS_REF} \
    OMICCHAIN_REF_DIR=/refs \
    HOME=/home/omicchain \
    XDG_CACHE_HOME=/home/omicchain/.cache
RUN mkdir -p /work /refs /home/omicchain/.cache && chmod -R 1777 /work /refs /home/omicchain

WORKDIR /work
EXPOSE 8501 8765
ENTRYPOINT ["sh", "/opt/omicchain/docker/entrypoint.sh"]
CMD ["app"]
