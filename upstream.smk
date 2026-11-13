import json as _json
import subprocess as _sp
import sys as _sys
import datetime as _dt
from pathlib import Path as _P

_sys.path.insert(0, workflow.basedir)
import omicchain_upstream as ou

try:
    PLAN = ou.load_plan(config)
except ou.UpstreamError as _e:
    raise Exception(f"\n\nOmicChain upstream: {_e}\n")

RSCRIPT = __import__("os").environ.get("OMICCHAIN_RSCRIPT", "Rscript")
BASE = _P(workflow.basedir)
OUT = PLAN["outdir"]
RUNS = PLAN["runs"]
FIRST_RUN = sorted(RUNS)[0]
PAIRED_R = "TRUE" if PLAN["paired"] else "FALSE"

def _r(*args, log):
    """Run Rscript with an argument list (paths with spaces are safe)."""
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        status = _sp.run([RSCRIPT, *map(str, args)], stdout=fh, stderr=_sp.STDOUT, cwd=str(BASE)).returncode
    if status != 0:
        tail = _P(log).read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        raise Exception(f"Rscript failed (code {status}), see {log}:\n" + "\n".join(tail))


rule all:
    input:
        OUT / "expression_input.tsv",
        OUT / "metadata.tsv",
        OUT / "qc_summary.tsv",
        OUT / "upstream_provenance.json"


rule verify_reference:
    output: OUT / "reference_check.json"
    params: fingerprint=PLAN["ref_manifest"]["indexes"][PLAN["index_type"]]["fingerprint"]
    run:
        print(f"Checking the reference fingerprints ({PLAN['index_type']} index)...")
        ou.verify_reference(PLAN, output[0])


rule align:
    input:
        fastq=lambda w: [PLAN["study"] / p for p in RUNS[w.run]["fastq"]],
        ref=OUT / "reference_check.json",
        script=BASE / "upstream_align.R"
    output:
        bam=temp(OUT / "bam" / "{run}.bam"),
        stats=OUT / "align" / "{run}.tsv"
    log: OUT / "logs" / "align_{run}.log"
    threads: PLAN["align_threads"]
    resources: mem_gb=PLAN["mem_gb"]
    run:
        fq, md5 = input.fastq, RUNS[wildcards.run]["md5"]
        args = [fq[0], md5[0]] + ([fq[1], md5[1]] if len(fq) == 2 else [])
        _r(input.script, PLAN["index_base"], output.bam, threads, output.stats, *args, log=log[0])


rule detect_strand:
    input:
        bam=(OUT / "bam" / f"{FIRST_RUN}.bam") if PLAN["strand"] == "auto" else [],
        script=BASE / "upstream_count.R"
    output: OUT / "strand.tsv"
    params: strand=PLAN["strand"]
    log: OUT / "logs" / "strand.log"
    threads: PLAN["count_threads"]
    run:
        if params.strand == "auto":
            _r(input.script, "detect", input.bam, PLAN["gtf"], PAIRED_R, threads, output[0], log=log[0])
        else:
            _P(output[0]).write_text(f"strand\t{params.strand}\n", encoding="utf-8")


rule count:
    input:
        bam=OUT / "bam" / "{run}.bam",
        strand=OUT / "strand.tsv",
        script=BASE / "upstream_count.R"
    output: OUT / "counts" / "{run}.tsv"
    log: OUT / "logs" / "count_{run}.log"
    threads: PLAN["count_threads"]
    run:
        strand = ou.read_kv(input.strand)["strand"]
        _r(input.script, "count", input.bam, PLAN["gtf"], PAIRED_R, strand, threads, output[0], log=log[0])


rule merge:
    input:
        counts=expand(str(OUT / "counts" / "{run}.tsv"), run=sorted(RUNS)),
        align=expand(str(OUT / "align" / "{run}.tsv"), run=sorted(RUNS)),
        strand=OUT / "strand.tsv",
        ref=OUT / "reference_check.json",
        code=BASE / "omicchain_upstream.py",
        samples_table=PLAN["samples_table"]
    output:
        OUT / "expression_input.tsv", OUT / "metadata.tsv", OUT / "counts_by_gene_id.tsv",
        OUT / "qc_summary.tsv", OUT / "upstream_provenance.json"
    params: selection=ou.selection_key(PLAN)
    run:
        prov = ou.merge(PLAN, OUT / "counts", OUT / "align", input.strand, input.ref, OUT)
        (OUT / "upstream_run.json").write_text(_json.dumps({
            "date": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "align_threads": PLAN["align_threads"], "count_threads": PLAN["count_threads"],
            "reference_dir": str(PLAN["ref_root"]), "study_dir": str(PLAN["study"].resolve())}, indent=2))
        c = prov["counting"]
        print(f"\nOmicChain upstream: {len(PLAN['samples'])} sample(s), {len(RUNS)} run(s), "
              f"strandSpecific = {c['strand_specific']} ({c['strand_mode']}), "
              f"{c['gene_symbols']} gene symbols ({c['gene_ids']} gene_id)")
        d = prov["design"]
        print(f"  metadata.tsv: group = control ({PLAN['reference_level']}) / treatment ({PLAN['test_level']}) "
              f"-> log2FC = {PLAN['test_level']} vs {PLAN['reference_level']}")
        if d["candidate_covariates"]:
            print(f"  possible covariate(s): {', '.join(d['candidate_covariates'])}")
        for w in prov["warnings"]:
            print(f"  WARNING: {w}")
        print(f"  Outputs: {OUT}")
