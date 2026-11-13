"""'Re-run and compare' section of the Verify step: downstream only, or from the raw data as a background task."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import omicchain_jobs as oj
import omicchain_reference as oref
import omicchain_replay as orp
import omicchain_verify as ov

HERE = Path(__file__).resolve().parent
SCOPES = ["Downstream only — from the certified expression table (minutes)",
          "Everything — from the raw data: FASTQ files or count matrix (hours for FASTQ)"]


def reference_status(ref_dir, origin):
    """'ok', 'missing' or 'different': does the cached reference have the certified fingerprints?"""
    ref = origin.get("reference") or {}
    man = oref.load_manifest(oref.ref_paths(ref_dir, ref.get("gencode_release"))["manifest"])
    entry = (man or {}).get("indexes", {}).get(ref.get("index_type"))
    if not entry:
        return "missing"
    same = (entry.get("fingerprint") == ref.get("index_fingerprint")
            and man["sources"]["genome"]["sha256"] == ref.get("genome_sha256")
            and man["sources"]["gtf"]["sha256"] == ref.get("gtf_sha256"))
    return "ok" if same else "different"


def raw_command(crate, metadata, report_json, project_dir, *, expected=None, fastq_dir=None, matrix=None,
                ref_dir=None, prepare_reference=False, cores=None, trust_crate_code=False):
    cmd = [sys.executable, str(HERE / "omicchain_verify.py"), str(crate), "--from-raw",
           "--project", str(project_dir), "--report-json", str(report_json), "--cores", str(cores or os.cpu_count() or 4)]
    if metadata:
        cmd += ["--metadata", str(metadata)]
    if expected:
        cmd += ["--expected", expected]
    if fastq_dir:
        cmd += ["--fastq-dir", str(fastq_dir)]
    if matrix:
        cmd += ["--matrix", str(matrix)]
    if ref_dir:
        cmd += ["--ref-dir", str(ref_dir)]
    if prepare_reference:
        cmd.append("--prepare-reference")
    if trust_crate_code:
        cmd.append("--trust-crate-code")
    return cmd


def _save_upload(upload, folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(upload.getvalue())
    return p


def render_rerun(project_dir, default_expected=""):
    project_dir = Path(project_dir)
    jobs_root = project_dir / "jobs"
    st.subheader("Re-run and compare (reproducibility)")
    st.markdown(
        "Re-runs a certified analysis from its **RO-Crate**, with the code taken from the crate and the certified "
        "parameters, and checks that the same fingerprint comes out. Your own results are not touched.")
    rr_crate = st.file_uploader("RO-Crate (.zip)", type=["zip"], key="rerun_crate")
    expected = st.text_input("Fingerprint published on-chain (recommended)", value=default_expected,
                             key="rerun_expected",
                             help="Read it on the certificate, or check it on-chain above first.").strip().lower()

    origin, crate_path, upload_dir = None, None, None
    if rr_crate is not None:
        data = rr_crate.getvalue()
        upload_dir = project_dir / "verify_runs" / "uploads" / hashlib.sha256(data).hexdigest()[:16]
        crate_path = upload_dir / "crate.zip"
        if not crate_path.is_file():
            _save_upload(rr_crate, upload_dir, "crate.zip")
        origin = ov.crate_origin(crate_path)

    inside = crate_inputs(crate_path) if crate_path else {"metadata": True, "expression": True}
    rr_meta = None
    if not inside["metadata"]:
        rr_meta = st.file_uploader(
            "Sample metadata of the analysis (metadata.tsv)", type=["tsv"], key="rerun_meta",
            help="This crate does not include it. It is the metadata.tsv that OmicChain wrote in the project "
                 "folder for this analysis (columns sample, group...) — not the sample sheet you imported.")

    trusted = _render_code_check(crate_path, project_dir) if crate_path else True
    scope = st.radio("What to re-run", SCOPES, key="rerun_scope")
    if scope == SCOPES[0]:
        _render_downstream(project_dir, rr_crate, rr_meta, expected, trusted, inside)
    else:
        _render_raw(project_dir, jobs_root, rr_crate, rr_meta, expected, origin, crate_path, upload_dir, trusted)

    rr = st.session_state.get("rerun_report")
    if rr:
        render_report(rr)


def _render_code_check(crate_path, project_dir):
    """Say where the code of the crate comes from; unknown code is run only after explicit acceptance. Return True, 'accepted' or False."""
    check = ov.crate_code_check(crate_path, project_dir)
    if check is None:
        st.error("This file is not an OmicChain RO-Crate (no readable bundle_manifest.json).")
        return False
    if check["known"]:
        st.caption("🛡️ Code of the crate: " + "; ".join(sorted({f["origin"] for f in check["files"]})) + ".")
        return True
    unknown = [f for f in check["files"] if not f["known"]]
    st.error("🛡️ **This crate contains code that is not a known OmicChain version** ("
             + ", ".join(f["file"] for f in unknown) + "). Re-running the analysis executes this code on your "
             "computer, with your user rights. Continue only if you trust the author of the crate or have read "
             "the code below. Checking the fingerprint on-chain (above) never executes anything.")
    for f in unknown:
        with st.expander(f"Read {f['file']} ({f['label']})"):
            st.code(ov.crate_code_text(crate_path, f["crate_path"]),
                    language="r" if f["file"].lower().endswith(".r") else "python")
    ok = st.checkbox("I have read this code and I accept to run it on this computer", key="rerun_trust_code")
    return "accepted" if ok else False


def crate_inputs(crate_path):
    """Which input data the crate (.zip) includes: {'metadata': bool, 'expression': bool}."""
    import zipfile
    try:
        with zipfile.ZipFile(crate_path) as z:
            names = set(z.namelist())
    except (OSError, zipfile.BadZipFile):
        names = set()
    return {"metadata": "inputs/metadata.tsv" in names, "expression": "inputs/expression_input.tsv" in names}


def _render_downstream(project_dir, rr_crate, rr_meta, expected, trusted, inside):
    rr_expr = None
    if not inside["expression"]:
        rr_expr = st.file_uploader("Expression matrix", type=["tsv"], key="rerun_expr",
                                   help="This crate does not include it: the expression_input.tsv of the analysis.")
    elif rr_crate is not None and inside["metadata"]:
        st.caption("📦 The crate includes the expression table and the metadata: nothing else to provide.")
    if st.button("Re-run and compare", type="primary", disabled=rr_crate is None or not trusted, key="rerun_go"):
        st.session_state.pop("rerun_report", None)
        tmp = Path(tempfile.mkdtemp(prefix="omicchain-upload-"))
        try:
            crate = _save_upload(rr_crate, tmp, "crate.zip")
            expr = _save_upload(rr_expr, tmp, "expression_input.tsv") if rr_expr is not None else None
            meta = _save_upload(rr_meta, tmp, "metadata.tsv") if rr_meta is not None else None
            with st.spinner("Re-running the analysis (this takes as long as the original one)…"):
                st.session_state["rerun_report"] = ov.rerun_verify(
                    crate, expression=expr, metadata=meta, expected=expected or None, project=project_dir,
                    log=lambda *_: None, trust_crate_code=trusted == "accepted")
        except Exception as e:
            st.error(f"❌ Re-run verification error: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _render_raw(project_dir, jobs_root, rr_crate, rr_meta, expected, origin, crate_path, upload_dir, trusted=True):
    if rr_crate is None:
        st.caption("Load the RO-Crate: the certified data origin it contains says which raw data are needed.")
        return
    if origin is None:
        st.warning("This crate has no certified data origin (data loaded by hand, or before OmicChain recorded it): "
                   "only the downstream part can be re-run.")
        return
    info = orp.describe(origin)
    fastq_dir = matrix = None
    meta_in_crate = crate_inputs(crate_path)["metadata"]
    ready = (rr_meta is not None or meta_in_crate) and bool(trusted)
    if info["kind"] == "matrix":
        st.info(f"The analysis started from a **count matrix**: `{info['file']}` (SHA-256 `{info['sha256'][:16]}…`). "
                "Give that file: its SHA-256 is checked, the expression table is rebuilt and compared, then the "
                "analysis is re-run.")
        up = st.file_uploader("Original count matrix", key="rerun_matrix")
        if up is not None:
            matrix = _save_upload(up, upload_dir, "matrix_" + Path(up.name).name)
        ready = ready and matrix is not None
    else:
        if info["kind"] == "public":
            st.info(f"Public study **{info['accession']}**: {info['files']} FASTQ files ({info['runs']} runs, "
                    f"{info['samples']} samples) are downloaded again from the ENA and must have the certified md5, "
                    f"then aligned and counted again (GENCODE v{info['gencode']}, {info['index_type']} index). "
                    "Count about as long as the original download and alignment.")
            fastq_dir = st.text_input("Folder that may already contain these FASTQ files (optional)",
                                      key="rerun_fastq_dir",
                                      help="Files found there with the certified md5 are not downloaded again "
                                           "(e.g. the fastq folder of the study).").strip().strip('"')
        else:
            st.info(f"**FASTQ files provided by the user**: {info['files']} files ({info['samples']} samples). Give "
                    "the folder that contains them: their md5 and SHA-256 must be the certified ones. They are then "
                    f"aligned and counted again (GENCODE v{info['gencode']}, {info['index_type']} index).")
            fastq_dir = st.text_input("Folder with the FASTQ files", key="rerun_fastq_dir").strip().strip('"')
            ready = ready and bool(fastq_dir)
        if fastq_dir and not Path(fastq_dir).is_dir():
            st.error(f"Folder not found: {fastq_dir}")
            ready = False
        ref_dir = st.session_state.get("ref_dir") or str(oref.default_ref_dir())
        status = reference_status(ref_dir, origin)
        if status == "ok":
            st.success(f"✅ Reference GENCODE v{info['gencode']} / {info['index_type']} index in {ref_dir}: "
                       "certified fingerprints.")
        elif status == "missing":
            st.warning(f"The reference GENCODE v{info['gencode']} / {info['index_type']} index is not in {ref_dir}. "
                       "Prepare it in the Data & reference step, or let the verification prepare it.")
        else:
            st.error(f"The {info['index_type']} index in {ref_dir} is not the certified one (another fingerprint: "
                     "built with another Rsubread version?). The verification would stop: build the index with "
                     "the Rsubread version recorded in the certificate.")
            ready = False
        prepare = status == "missing" and st.checkbox(
            "Download and build the reference if needed (long: about 1 h for the gapped index, 3 h for full)",
            key="rerun_prepare_ref")
        if status == "missing" and not prepare:
            ready = False
    if info["kind"] != "matrix":
        free_b = oref.free_bytes(project_dir)
        st.caption(f"Free disk space for the replay (downloads, alignments): {free_b / 1024 ** 3:.0f} GB in {project_dir}.")
        if free_b < 30 * 1024 ** 3:
            st.warning("Less than 30 GB free: a FASTQ study usually needs more (the FASTQ files, plus temporary "
                       "alignment files). An interrupted replay resumes where it stopped.")
    if rr_meta is None and not meta_in_crate:
        st.caption("This crate does not include the metadata: give the metadata.tsv of the analysis (above).")
    report_json = upload_dir / "report.json"
    key = f"verify-raw|{crate_path}"
    running = [j for j in oj.list_jobs(jobs_root) if j.get("key") == key and j["status"] == "running"]
    if st.button("Re-run everything from the raw data", type="primary", disabled=not ready or bool(running),
                 key="rerun_raw_go"):
        meta = _save_upload(rr_meta, upload_dir, "metadata.tsv") if rr_meta is not None else None
        report_json.unlink(missing_ok=True)
        st.session_state.pop("rerun_report", None)
        st.session_state.pop("rerun_report_from", None)
        cmd = raw_command(crate_path, meta, report_json, project_dir, expected=expected or None,
                          fastq_dir=fastq_dir or None, matrix=matrix,
                          ref_dir=None if info["kind"] == "matrix" else ref_dir,
                          prepare_reference=info["kind"] != "matrix" and bool(prepare),
                          trust_crate_code=trusted == "accepted")
        job = oj.start(jobs_root, cmd, label=f"Verification from the raw data ({info.get('accession') or info.get('file')})",
                       key=key, cwd=project_dir)
        st.session_state["rerun_raw_job"] = {"id": job, "report": str(report_json)}
        st.rerun()
    job = st.session_state.get("rerun_raw_job")
    if job:
        any_running = (oj.status(jobs_root, job["id"]) or {}).get("status") == "running"
        st.fragment(run_every=3 if any_running else None)(_render_job)(jobs_root, job)


def _render_job(jobs_root, job):
    """Progress of the background verification; the report is loaded when it ends."""
    info = oj.status(jobs_root, job["id"])
    if not info:
        return
    with st.container(border=True):
        st.markdown(f"**{info['label']}** — {info['status']}")
        if info["status"] == "running":
            frac = oj.progress(jobs_root, job["id"])
            if frac is not None:
                st.progress(min(max(frac, 0.0), 1.0))
            st.caption("Runs in the background: you can close the page and come back. Running it again after an "
                       "interruption resumes the downloads and alignments already done.")
            if st.button("Cancel", key=f"rerun_cancel_{job['id']}"):
                oj.cancel(jobs_root, job["id"])
                st.rerun(scope="app")
        with st.expander("Log", expanded=info["status"] in ("failed", "interrupted")):
            st.code(oj.log_tail(jobs_root, job["id"], lines=30) or "(empty)", language="text")
    report = Path(job["report"])
    if info["status"] != "running" and report.is_file() and st.session_state.get("rerun_report_from") != str(report):
        st.session_state["rerun_report"] = json.loads(report.read_text(encoding="utf-8"))
        st.session_state["rerun_report_from"] = str(report)
        st.rerun(scope="app")


def render_report(rr):
    """Result of a verification."""
    for stp in rr["steps"]:
        (st.write if stp["ok"] else st.error)(
            ("✅ " if stp["ok"] else "❌ ") + stp["name"] + (f" — {stp['detail']}" if stp["detail"] else ""))
    for w in rr.get("warnings", []):
        st.warning("⚠️ " + w)
    raw = rr.get("raw") or {}
    if raw.get("outputs"):
        st.dataframe(pd.DataFrame([{"Rebuilt from the raw data": o["name"], "Compared with the certified one":
                                    "same" if o["same"] else "DIFFERENT"} for o in raw["outputs"]]),
                     hide_index=True, width="stretch")
    if raw.get("comparison"):
        c = raw["comparison"]
        st.info(f"Expression table: {c['genes'][0]} genes certified, {c['genes'][1]} rebuilt; "
                f"{c.get('genes_with_different_counts', '?')} gene(s) with other counts (largest difference "
                f"{c.get('max_count_difference', '?')}).")
    if rr.get("components"):
        st.dataframe(pd.DataFrame([{"Component": c["label"], "Re-run": "same" if c["same"] else "DIFFERENT"}
                                   for c in rr["components"]]), hide_index=True, width="stretch")
    if rr.get("certified_bundle"):
        st.markdown(f"**Certified fingerprint**: `{rr['certified_bundle']}`")
    if rr.get("rerun_bundle"):
        st.markdown(f"**Re-run fingerprint**: `{rr['rerun_bundle']}`")
    for line in rr.get("environment_changes", []):
        st.write(f"• R environment: {line}")
    for label, cmp in rr.get("table_comparison", {}).items():
        lines = [f"{label}: {cmp['rows'][0]} rows certified, {cmp['rows'][1]} rows re-run"]
        if "n_only_certified" in cmp:
            lines.append(f"genes only in the certified table: {cmp['n_only_certified']}, "
                         f"only in the re-run: {cmp['n_only_rerun']}")
        for col, d in cmp.get("max_differences", {}).items():
            lines.append(f"{col}: max absolute difference {d['max_abs']:.3g}, max relative {d['max_rel']:.3g}")
        st.info("\n\n".join(lines) + "\n\n(information only: the fingerprint is not reproduced)")
    raw_scope = rr.get("scope") == "raw"
    if rr["verdict"] == "REPRODUCED":
        st.success("✅ REPRODUCED " + ("FROM THE RAW DATA — the raw files have the certified checksums, and the "
                                       "re-run from them gives exactly the certified fingerprint."
                                       if raw_scope else "— the re-run gives exactly the certified fingerprint."))
    elif rr["verdict"] == "RESULTS REPRODUCED":
        st.warning("⚠️ RESULTS REPRODUCED" + (" from the raw data" if raw_scope else "") + " — identical results "
                   "table, but the fingerprint differs because of: " + ", ".join(rr.get("different", [])))
    else:
        st.error(f"❌ {rr['verdict']}")
    if rr.get("log_tail"):
        with st.expander("End of the re-run log"):
            st.code("\n".join(rr["log_tail"]))
    if rr.get("workdir") and not rr.get("workdir_removed"):
        st.caption(f"Re-run folder kept for inspection: {rr['workdir']}")
    if rr.get("raw_workdir") and not rr.get("raw_workdir_removed"):
        st.caption(f"Raw-data replay folder kept (running again resumes there): {rr['raw_workdir']}")
