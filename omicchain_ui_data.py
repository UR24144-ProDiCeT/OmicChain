"""'Data & reference' step of the application: reference cache, data sources (public study, user FASTQ files, count matrix), sample annotation, comparison, background tasks."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

import omicchain_design as odz
import omicchain_jobs as oj
import omicchain_names as onm
import omicchain_local as ol
import omicchain_matrix as om
import omicchain_reference as oref
import omicchain_upstream as oup

HERE = Path(__file__).resolve().parent
STATUS_ICON = {"running": "⏳", "done": "✅", "failed": "❌", "cancelled": "⏹️", "interrupted": "⚠️"}
INDEX_INFO = {
    "gapped": "~1 h to build (one CPU core), ~6 GB of RAM to build and ~8 GB to align, 5.6 GB on disk. "
              "Fits a 16 GB computer.",
    "full": "~3 h to build (one CPU core), ~17 GB of RAM to build and ~19 GB to align, 15.4 GB on disk. "
            "Needs 32 GB of RAM.",
}


def _fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h} h {m:02d} min" if h else (f"{m} min {s:02d} s" if m else f"{s} s")


def _human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024


def reference_job_key(ref_dir, release, index_type):
    return f"reference|{Path(ref_dir).resolve()}|{release}|{index_type}"


def reference_command(ref_dir, release, index_type, action="prepare", import_path=None, in_place=False,
                      rsubread_version=None):
    cmd = [sys.executable, str(HERE / "omicchain_reference.py"), "--ref-dir", str(ref_dir), action,
           "--gencode", str(release), "--index", index_type]
    if action == "prepare" and import_path:
        cmd += ["--import-index", str(import_path)]
        if in_place:
            cmd.append("--in-place")
        if rsubread_version:
            cmd += ["--rsubread-version", rsubread_version]
    return cmd


def render_jobs(jobs_root, limit=8):
    """List of the background tasks, refreshed while one is running."""
    jobs = oj.list_jobs(jobs_root)
    running = [j["id"] for j in jobs if j["status"] == "running"]

    seen = st.session_state.setdefault("_jobs_running", [])
    if any(jid not in running for jid in seen):
        st.session_state["_jobs_running"] = running
        st.rerun(scope="app")
    st.session_state["_jobs_running"] = running

    if not jobs:
        st.caption("No background task yet.")
        return
    for j in jobs[:limit]:
        icon = STATUS_ICON.get(j["status"], "•")
        head = f"{icon} **{j['label']}** — {j['status']}, {_fmt_duration(j['elapsed_s'])}"
        with st.container(border=True):
            st.markdown(head)
            if j["status"] == "running":
                frac = oj.progress(jobs_root, j["id"])
                if frac is not None:
                    st.progress(min(max(frac, 0.0), 1.0))
                if st.button("Cancel", key=f"cancel_{j['id']}"):
                    oj.cancel(jobs_root, j["id"])
                    st.rerun(scope="app")
            elif j["status"] in ("failed", "interrupted"):
                st.caption("The last lines of the log below explain what happened.")
            with st.expander("Log", expanded=j["status"] in ("failed",)):
                st.code(oj.log_tail(jobs_root, j["id"], lines=25) or "(empty)", language="text")
            if j["status"] != "running" and st.button("Remove from the list", key=f"remove_{j['id']}"):
                oj.remove(jobs_root, j["id"])
                st.rerun(scope="app")


def render_reference(jobs_root, project_dir):
    st.subheader("Reference (genome, annotation, index)")
    st.session_state.setdefault("ref_dir", str(oref.default_ref_dir()))
    st.session_state.setdefault("ref_release", "48")
    c1, c2 = st.columns([3, 1])
    with c1:
        ref_dir = st.text_input("Reference cache folder", key="ref_dir",
                                help="Shared by all your OmicChain projects. About 22 GB with both indexes.")
    with c2:
        release = st.text_input("GENCODE release", key="ref_release")
    ref_dir = Path(ref_dir.strip().strip('"'))
    paths = oref.ref_paths(ref_dir, release.strip())
    man = oref.load_manifest(paths["manifest"]) or {}
    sources = man.get("sources", {})
    if sources:
        st.caption(f"GENCODE v{release}, GRCh38 primary assembly — genome and annotation downloaded "
                   f"(md5 checked against GENCODE).")
    else:
        st.caption(f"GENCODE v{release}: genome and annotation not downloaded yet "
                   "(done automatically, ~1 GB, when an index is prepared).")

    running = {j["key"]: j for j in oj.list_jobs(jobs_root) if j["status"] == "running"}
    for index_type in ("gapped", "full"):
        entry = man.get("indexes", {}).get(index_type)
        key = reference_job_key(ref_dir, release, index_type)
        with st.container(border=True):
            st.markdown(f"**Index {index_type}** — {INDEX_INFO[index_type]}")
            if key in running:
                st.info(f"⏳ Task in progress: {running[key]['label']} (see Background tasks below).")
                continue
            if entry:
                where = "in the cache" if entry["origin"]["method"] in ("built", "imported") else \
                    f"used in place: {entry['path']}"
                st.success(f"✅ Ready — Rsubread {entry.get('rsubread_version')}, {_human(entry['bytes'])}, "
                           f"{entry['origin']['method']} ({where})")
                st.caption(f"Fingerprint: `{entry['fingerprint']}`")
                if st.button("Verify fingerprints", key=f"verify_{index_type}"):
                    oj.start(jobs_root, reference_command(ref_dir, release, index_type, action="verify"),
                             label=f"Verify reference GENCODE v{release} ({index_type})",
                             key=key, cwd=project_dir)
                    st.rerun()
                continue
            st.warning("Not prepared yet.")
            t1, t2 = st.tabs(["Download and build", "Use an index already built"])
            with t1:
                st.caption("Downloads GENCODE (if needed), checks its md5, builds the index with Rsubread "
                           "(R of this project) and records its fingerprint.")
                need_gb, free_b = oref.disk_need_gb(index_type), oref.free_bytes(ref_dir)
                st.caption(f"Needs about {need_gb:g} GB of disk space in the cache folder · free: {_human(free_b)}")
                if free_b < need_gb * 1024 ** 3:
                    st.warning(f"Not enough disk space in {ref_dir} to prepare the {index_type} index: free some, "
                             "or choose a cache folder on another disk.")
                elif st.button(f"Prepare the {index_type} index", key=f"prepare_{index_type}"):
                    oj.start(jobs_root, reference_command(ref_dir, release, index_type),
                             label=f"Prepare reference GENCODE v{release} ({index_type})",
                             key=key, cwd=project_dir)
                    st.rerun()
            with t2:
                path = st.text_input("Index path, without extension (e.g. …\\index_gapped\\GRCh38)",
                                     key=f"import_path_{index_type}")
                mode = st.radio("How", ["Copy it into the cache (safest)", "Use it where it is (no copy)"],
                                key=f"import_mode_{index_type}", horizontal=True)
                version = st.text_input("Rsubread version that built it (optional)", key=f"import_version_{index_type}",
                                        help="If empty, the version installed in this project is recorded.")
                p = Path(path.strip().strip('"')) if path.strip() else None
                if p is not None and not oref.index_files(p):
                    st.error(f"No Rsubread index found at {p} (expected files such as {p.name}.reads).")
                elif p is not None and st.button("Import this index", key=f"import_{index_type}"):
                    oj.start(jobs_root, reference_command(ref_dir, release, index_type, import_path=p,
                                                          in_place=mode.startswith("Use"),
                                                          rsubread_version=version.strip() or None),
                             label=f"Import {index_type} index for GENCODE v{release}", key=key, cwd=project_dir)
                    st.rerun()


def ready_indexes(ref_dir, release):
    man = oref.load_manifest(oref.ref_paths(ref_dir, release)["manifest"]) or {}
    return [t for t in ("gapped", "full") if t in man.get("indexes", {})]


LOADED_FILE = "loaded_data.json"


def loaded_data(project_dir):
    """Data currently loaded for the Analysis step (None if none)."""
    project_dir = Path(project_dir)
    p = project_dir / LOADED_FILE
    if not (p.is_file() and (project_dir / "expression_input.tsv").is_file()
            and (project_dir / "metadata_source.tsv").is_file()):
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def load_counts_into_pipeline(out, project_dir):
    """Copy the counts of a study into the project and preselect the samples."""
    project_dir = Path(project_dir)
    out = Path(out)
    shutil.copyfile(out / "expression_input.tsv", project_dir / "expression_input.tsv")
    shutil.copyfile(out / "metadata.tsv", project_dir / "metadata_source.tsv")
    src = pd.read_csv(out / "metadata.tsv", sep="\t", dtype=str)
    prov = next((out / n for n in ("upstream_provenance.json", "matrix_provenance.json") if (out / n).is_file()), None)
    design = json.loads(prov.read_text(encoding="utf-8")).get("design", {}) if prov else {}
    dataset = out.parent.parent.name if out.parent.name == "upstream" else out.parent.name
    label = dataset.removeprefix("study_").removeprefix("matrix_")
    if design:
        label += f" — {design.get('condition_column')}: {design.get('test_level')} vs {design.get('reference_level')}"
    if prov:
        shutil.copyfile(prov, project_dir / "data_origin.json")
    else:
        (project_dir / "data_origin.json").unlink(missing_ok=True)
    (project_dir / LOADED_FILE).write_text(json.dumps(
        {"label": label, "counts_dir": str(out), "samples": len(src),
         "provenance_file": prov.name if prov else None}, indent=2), encoding="utf-8")
    st.session_state["study_counts_dir"] = str(out)
    st.session_state["ctrl_samples"] = src.loc[src["group"] == "control", "sample"].tolist()
    st.session_state["trt_samples"] = src.loc[src["group"] == "treatment", "sample"].tolist()
    st.session_state.pop("covariate_choice", None)


def render_study_counts(study, index_type, release, design, project_dir, key, table_sha256=None):
    """Counts produced for a study: quality control, warnings, loading into the analysis."""
    out = Path(study) / "upstream" / f"{index_type}_gencode{release}"
    prov_path = out / "upstream_provenance.json"
    if not prov_path.is_file():
        return False
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    d = prov["design"]
    if "group_coding" not in d:
        st.warning(f"The counts in {out.name} were produced by an older version of the alignment workflow "
                   "(no 'condition' column, no possible covariates). Run the alignment again: only the final "
                   "assembly is redone (a few minutes), the alignments are reused.")
        return False
    if (d["condition_column"], d["reference_level"], d["test_level"], d["samples"]) != design:
        st.info(f"Counts from a previous run exist for another selection ({d['condition_column']}: "
                f"{d['reference_level']} vs {d['test_level']}, {len(d['samples'])} samples). Run the alignment "
                "for this selection: the alignments already done are reused.")
        return False
    if table_sha256 and prov["study"].get("samples_table_sha256", table_sha256) != table_sha256:
        st.info("The sample annotations changed since these counts were produced. Run again: only the final "
                "assembly is redone (the alignments are reused).")
        return False
    st.success(f"✅ Counts ready: {len(d['samples'])} samples, {prov['counting']['gene_symbols']} genes, "
               f"library strandSpecific = {prov['counting']['strand_specific']} "
               f"({prov['counting']['strand_mode']}), index {index_type}.")
    qc_path = out / "qc_summary.tsv"
    if qc_path.is_file():
        st.dataframe(pd.read_csv(qc_path, sep="\t"), hide_index=True, width="stretch")
    for w in prov.get("warnings", []):
        st.warning(w)
    if d.get("candidate_covariates"):
        st.caption("Possible covariates (choose one in the Analysis step): " + ", ".join(d["candidate_covariates"]))
    if st.button("Use these counts for the analysis", key=f"load_{key}", type="primary"):
        load_counts_into_pipeline(out, project_dir)
        st.rerun()
    if st.session_state.get("study_counts_dir") == str(out):
        st.caption("✔ Loaded: go to the Analysis step to choose a covariate and run the analysis.")
    return True


def fetch_metadata(accession, study, project_dir):
    """Metadata only (omicchain_fetch.py --dry-run); a few seconds."""
    r = subprocess.run([sys.executable, str(HERE / "omicchain_fetch.py"), accession, "--dry-run", "--out", str(study)],
                       cwd=str(project_dir), capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout + r.stderr)


def _stage(n, title):
    st.markdown(f"#### {n}. {title}")


def render_annotation(study, acc):
    """Sample annotation by the user: added columns, keyword rules, direct editing. Return the updated table."""
    table, info = odz.load_annotations(study)
    ver_key = f"annot_ver_{acc}"
    st.session_state.setdefault(ver_key, 0)

    varying = odz.varying_columns(table, exclude=odz.FETCH_FIXED_COLUMNS + info["user_columns"])
    shown = ["sample", "title"] + varying + info["user_columns"]
    st.caption("The public metadata are not always enough (information only in the title, for example). "
               "Add your own columns (condition, cell_line, batch...) and fill them: they can then be compared "
               "or used as covariate, and they are recorded in the certified provenance.")

    c1, c2 = st.columns([2, 1])
    with c1:
        new_col = st.text_input("New column", key=f"annot_newcol_{acc}", placeholder="condition")
    with c2:
        st.write(""); st.write("")
        add = st.button("Add column", key=f"annot_add_{acc}")
    if add:
        why = odz.column_name_problem(new_col.strip(), list(table.columns))
        if why:
            st.error(f"Column '{new_col.strip()}': {why}.")
        else:
            info["user_columns"].append(new_col.strip()); table[new_col.strip()] = ""
            odz.save_annotations(study, table, info)
            st.session_state[ver_key] += 1
            st.rerun()

    sheet = st.file_uploader("…or import a sample sheet (TSV or CSV with a 'sample' column; its other columns "
                             "are added)", type=["tsv", "csv", "txt"], key=f"annot_sheet_{acc}")
    if sheet is not None and st.session_state.get(f"annot_sheet_id_{acc}") != sheet.file_id:
        st.session_state[f"annot_sheet_id_{acc}"] = sheet.file_id
        msg, ok = odz.import_sample_sheet(table, info, sheet.getvalue(), sheet.name)
        if ok:
            odz.save_annotations(study, ok["table"], info)
            st.session_state[ver_key] += 1
        st.session_state[f"rule_msg_{acc}"] = msg
        st.rerun()
    if st.session_state.get(f"rule_msg_{acc}") and not info["user_columns"]:
        st.warning(st.session_state.pop(f"rule_msg_{acc}"))

    if info["user_columns"]:
        with st.container(border=True):
            st.markdown("**Fill by keyword** — sets a value for every sample whose text contains the keyword "
                        "(case-insensitive).")
            r1, r2, r3, r4 = st.columns(4)
            sources = ["title"] + [c for c in shown if c not in ("sample", "title")]
            with r1:
                src = st.selectbox("If", sources, key=f"rule_src_{acc}")
            with r2:
                kw = st.text_input("contains", key=f"rule_kw_{acc}", placeholder="non targeting")
            with r3:
                col = st.selectbox("then column", info["user_columns"], key=f"rule_col_{acc}")
            with r4:
                val = st.text_input("= value", key=f"rule_val_{acc}", placeholder="control")
            if st.button("Apply", key=f"rule_apply_{acc}"):
                if not kw.strip() or not val.strip():
                    st.error("Give a keyword and a value.")
                else:
                    table, n = odz.apply_rule(table, src, kw.strip(), col, val.strip())
                    info["rules"].append({"if_column": src, "contains": kw.strip(), "column": col,
                                          "value": val.strip(), "matched": n})
                    odz.save_annotations(study, table, info)
                    st.session_state[ver_key] += 1
                    st.session_state[f"rule_msg_{acc}"] = f"{n} sample(s) set to {col} = {val.strip()}."
                    st.rerun()
            if st.session_state.get(f"rule_msg_{acc}"):
                st.caption(st.session_state.pop(f"rule_msg_{acc}"))
            if info["rules"]:
                st.caption("Rules applied: " + " · ".join(
                    f"{r['if_column']} ∋ “{r['contains']}” → {r['column']} = {r['value']} ({r['matched']})"
                    for r in info["rules"]))

    edited = st.data_editor(table[shown], key=f"annot_editor_{acc}_{st.session_state[ver_key]}",
                            disabled=[c for c in shown if c not in info["user_columns"]],
                            hide_index=True, width="stretch")
    if info["user_columns"]:
        changed = any(not edited[c].fillna("").astype(str).str.strip().equals(table[c].astype(str).str.strip())
                      for c in info["user_columns"])
        if changed:
            for c in info["user_columns"]:
                table[c] = edited[c].fillna("").astype(str).str.strip().values
            info["edited_by_hand"] = True
            odz.save_annotations(study, table, info)
        e1, e2 = st.columns([3, 1])
        with e1:
            drop = st.selectbox("Remove a column you added", ["—"] + info["user_columns"], key=f"annot_drop_{acc}")
        with e2:
            st.write(""); st.write("")
            if st.button("Remove", key=f"annot_drop_btn_{acc}") and drop != "—":
                info["user_columns"].remove(drop)
                info["rules"] = [r for r in info["rules"] if r["column"] != drop]
                odz.save_annotations(study, table.drop(columns=[drop]), info)
                st.session_state[ver_key] += 1
                st.rerun()
    return table


def render_geo(jobs_root, project_dir):
    project_dir = Path(project_dir)
    st.session_state.setdefault("studies_root", str(project_dir / "studies"))
    c1, c2 = st.columns([1, 2])
    with c1:
        acc = st.text_input("Accession", key="geo_accession", placeholder="GSE52778",
                            help="GEO series (GSE…), or SRA / ENA project (SRP…, ERP…, PRJNA…, PRJEB…).")
    with c2:
        studies_root = st.text_input("Studies folder", key="studies_root",
                                     help="Each study goes in its own sub-folder (FASTQ, metadata, counts).")
    acc = acc.strip().upper()
    if not acc:
        return
    if onm.accession_problem(acc):
        st.error(f"'{acc[:40]}' is not an accession OmicChain can use: {onm.accession_problem(acc)}.")
        return
    study = Path(studies_root.strip().strip('"')) / f"study_{acc}"
    key = f"study|{study.resolve()}"
    running = [j for j in oj.list_jobs(jobs_root) if j.get("key") == key and j["status"] == "running"]

    _stage(1, "Metadata")
    have_meta = (study / "samples.tsv").is_file()
    if st.button("Refresh the metadata" if have_meta else "Get the study's metadata", key="geo_fetch_meta",
                 disabled=bool(running)):
        with st.spinner(f"Querying GEO and ENA for {acc}..."):
            code, out = fetch_metadata(acc, study, project_dir)
        if code != 0:
            st.session_state["geo_meta_error"] = out
        else:
            st.session_state.pop("geo_meta_error", None)
        st.rerun()
    if st.session_state.get("geo_meta_error"):
        st.error("Could not get the metadata:")
        st.code(st.session_state["geo_meta_error"][-3000:], language="text")
    if not (study / "samples.tsv").is_file():
        return
    manifest = json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8"))
    title = manifest["provenance"].get("geo_series_title")
    st.markdown(f"**{acc}**" + (f" — {title}" if title else "") + f" · folder `{study}`")
    for w in manifest.get("warnings", []):
        st.warning(w)

    _stage(2, "Samples (add your own annotations if needed)")
    table = render_annotation(study, acc)
    by = table.set_index("sample")

    _stage(3, "Download (optional: the alignment downloads what is missing)")
    verified = {f["path"] for f in manifest.get("files", []) if f.get("md5_verified")}
    ena = {r["run_accession"]: r for r in oup.read_tsv(study / "ena_runs.tsv")}

    def files_of(samples):
        out = []
        for s in samples:
            for run in [r for r in by.loc[s, "runs"].split(";") if r]:
                info = ena.get(run, {})
                names = [u.rsplit("/", 1)[-1] for u in info.get("fastq_ftp", "").split(";") if u]
                sizes = info.get("fastq_bytes", "").split(";")
                for i, n in enumerate(names):
                    if len(names) > 1 and not re.search(r"_[12]\.f(ast)?q\.gz$", n):
                        continue
                    out.append((n, int(sizes[i]) if i < len(sizes) and sizes[i].isdigit() else 0))
        return out

    def to_download(samples):
        return sum(b for n, b in files_of(samples) if f"fastq/{n}" not in verified)

    def downloaded(s):
        f = files_of([s])
        return bool(f) and all(f"fastq/{n}" in verified for n, _ in f)

    done = [s for s in table["sample"] if downloaded(s)]
    st.caption(f"{len(done)} / {len(table)} samples downloaded and checked (md5 from ENA).")
    dl = st.multiselect("Samples to download", list(table["sample"]),
                        default=[s for s in table["sample"] if s not in done], key=f"geo_dl_{acc}",
                        format_func=lambda s: f"{s} — {by.loc[s, 'title']}")
    study.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(study).free
    todo = to_download(dl)
    st.caption(f"To download: {_human(todo)} · free disk space: {_human(free)}")
    if dl and todo and not running:
        if todo > free:
            st.error("Not enough disk space.")
        elif st.button("Download the selected samples", key="geo_download"):
            cmd = [sys.executable, str(HERE / "omicchain_fetch.py"), acc, "--out", str(study), "--samples", ",".join(dl)]
            oj.start(jobs_root, cmd, label=f"{acc}: download {len(dl)} sample(s)", key=key, cwd=project_dir)
            st.rerun()

    render_compare_and_run(study, acc, table, jobs_root, project_dir, running, key, to_download, free, first_stage=4)


def select_comparison(table, acc, first_stage, hint="add a column in step 2 (for example 'condition') and fill it"):
    """Choice of the comparison. Return (column, reference level, tested level, samples) or None."""
    by = table.set_index("sample")
    _stage(first_stage, "Comparison")
    conds = odz.comparable_columns(table)
    if not conds:
        st.info(f"No column has at least two values to compare: {hint}.")
        return None
    c1, c2, c3 = st.columns(3)
    if st.session_state.get("geo_condition") not in conds:
        st.session_state.pop("geo_condition", None)
    with c1:
        cond = st.selectbox("Condition column", conds, key="geo_condition")
    values = table[cond].astype(str).str.strip()
    levels = sorted(v for v in values.unique() if v)
    d_ref, d_test = odz.default_levels([v for v in values if v])
    if st.session_state.get("geo_ref") not in levels:
        st.session_state["geo_ref"] = d_ref
    with c2:
        ref_level = st.selectbox("Reference level (control)", levels, key="geo_ref")
    others = [lv for lv in levels if lv != ref_level]
    if st.session_state.get("geo_test") not in others:
        st.session_state["geo_test"] = d_test if d_test in others else others[0]
    with c3:
        test_level = st.selectbox("Tested level (treatment)", others, key="geo_test")
    in_levels = table.loc[values.isin([ref_level, test_level]), "sample"].tolist()
    chosen = st.multiselect("Samples", in_levels, default=in_levels, key=f"geo_samples_{acc}_{cond}_{ref_level}_{test_level}",
                            format_func=lambda s: f"{s} — {by.loc[s, 'title']}")
    n_ref = int((values[table["sample"].isin(chosen)] == ref_level).sum())
    n_test = len(chosen) - n_ref
    st.caption(f"{n_ref} × {ref_level} (control), {n_test} × {test_level} (treatment) — "
               f"log2FC = {test_level} vs {ref_level}.")
    if n_ref < 2 or n_test < 2:
        st.error("At least 2 samples are needed in each group.")
        return None
    mixed = odz.mixed_layouts(table, chosen)
    if mixed:
        st.error("These samples mix sequencing types, which cannot be analysed together: "
                 + "; ".join(f"{lay}: {', '.join(ss)}" for lay, ss in mixed.items())
                 + ". Keep only one type in the selection.")
        return None
    return cond, ref_level, test_level, [s for s in table["sample"] if s in chosen]


def bam_estimate(table, chosen, parallel=2):
    """Estimated disk space taken by the temporary BAM files during the alignment."""
    sizes = []
    if "fastq_bytes" in table.columns:
        for s, raw in zip(table["sample"], table["fastq_bytes"]):
            if s in chosen:
                sizes.append(sum(int(x) for x in str(raw).replace(",", ";").split(";") if x.strip().isdigit()))
    sizes = sorted((x for x in sizes if x), reverse=True)[:parallel]
    return int(1.5 * sum(sizes)) if sizes else parallel * 4 * 1024 ** 3


def render_compare_and_run(study, acc, table, jobs_root, project_dir, running, key, to_download, free, first_stage):
    """Steps shared by the FASTQ sources: comparison, alignment and counting in the background, loading of the counts."""
    sel = select_comparison(table, acc, first_stage)
    if sel is None:
        return
    cond, ref_level, test_level, chosen = sel

    _stage(first_stage + 1, "Alignment and counting")
    release = st.session_state.get("ref_release", "48").strip()
    ref_dir = Path(st.session_state.get("ref_dir", str(oref.default_ref_dir())).strip().strip('"'))
    ready = ready_indexes(ref_dir, release)
    if not ready:
        st.warning(f"No index ready for GENCODE v{release}: prepare one in the Reference section below.")
        return
    index_type = st.radio("Index", ready, horizontal=True, key="geo_index",
                          help="Its type is part of the certified provenance (full and gapped give slightly "
                               "different counts).")
    ram = oup.total_ram_gb()
    if ram and index_type == "full" and ram < 24:
        st.error(f"The full index needs ~19 GB of RAM to align; this computer has {ram:.0f} GB. Use the gapped index.")
        return
    todo = to_download(chosen)
    temp = bam_estimate(table, chosen)
    st.caption((f"Still to download for this comparison: {_human(todo)}" if todo else "Nothing to download.")
               + f" · temporary alignment files: about {_human(temp)} · free disk space: {_human(free)}"
               + (f" · RAM: {ram:.0f} GB" if ram else ""))
    if todo > free:
        st.error(f"Not enough disk space to download the FASTQ files: {_human(todo)} needed, {_human(free)} free.")
        return
    if todo + temp > free:
        st.warning(f"Disk space may run short during the alignment: about {_human(todo + temp)} needed "
                   f"(estimate), {_human(free)} free. Free some space, or the run may stop part-way "
                   "(it can then be resumed).")
    if running:
        st.info(f"⏳ {running[0]['label']} — in progress (see Background tasks below).")
        return
    design = (cond, ref_level, test_level, [s for s in table["sample"] if s in chosen])
    table_sha = oup.sha256_file(oup.samples_table_path(study))
    has_counts = render_study_counts(study, index_type, release, design, project_dir, key=f"geo_{acc}",
                                     table_sha256=table_sha)
    if st.button("Download, align and count" if not has_counts else "Run again (download / align / count)",
                 key="geo_run", type="secondary" if has_counts else "primary"):
        cmd = [sys.executable, str(HERE / "omicchain_upstream.py"), "run", f"--study={study}",
               f"--condition={cond}", f"--reference-level={ref_level}", f"--test-level={test_level}",
               f"--samples={','.join(design[3])}", f"--index={index_type}", f"--gencode={release}",
               f"--ref-dir={ref_dir}"]
        oj.start(jobs_root, cmd, label=f"{acc}: download, align and count ({len(chosen)} samples, {index_type} index)",
                 key=key, cwd=project_dir)
        st.rerun()


def render_local(jobs_root, project_dir):
    """Source: the user's FASTQ files (a folder)."""
    project_dir = Path(project_dir)
    st.session_state.setdefault("studies_root", str(project_dir / "studies"))
    c1, c2 = st.columns([2, 1])
    with c1:
        folder_txt = st.text_input("Folder containing your FASTQ files (sub-folders included)", key="local_folder",
                                   placeholder=r"e.g. D:\sequencing\run_2026_10")
    with c2:
        studies_root = st.text_input("Studies folder", key="studies_root")
    if not folder_txt.strip():
        st.caption("Accepted: .fastq, .fq, .fastq.gz, .fq.gz. Read 1 / read 2 and Illumina lanes "
                   "(…_S1_L001_R1_001.fastq.gz) are recognised and grouped by sample. The files are not copied.")
        return
    folder = Path(folder_txt.strip().strip('"'))
    if not folder.is_dir():
        st.error(f"Folder not found: {folder}")
        return
    name = st.text_input("Name of this dataset", key="local_name", value=f"LOCAL_{ol.safe_name(folder.name)}")
    name = ol.safe_name(name.strip()) or "LOCAL"
    study = Path(studies_root.strip().strip('"')) / f"study_{name}"
    key = f"study|{study.resolve()}"
    running = [j for j in oj.list_jobs(jobs_root) if j.get("key") == key and j["status"] == "running"]

    _stage(1, "Files")
    found = ol.scan(folder)
    if not found["samples"]:
        st.warning("No FASTQ file found in this folder.")
        return
    rows = [{"sample": smp, "layout": info["layout"], "files": sum(len(u) for u in info["units"].values()),
             "lanes / units": len(info["units"]),
             "size": _human(sum(p.stat().st_size for u in info["units"].values() for p in u.values()))}
            for smp, info in sorted(found["samples"].items())]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for prob in found["problems"]:
        st.warning(prob)
    if found["ignored"]:
        st.caption(f"Ignored (not FASTQ): {len(found['ignored'])} file(s).")
    blocking = [p for p in found["problems"] if "same file name" in p or "already read" in p]
    registered = (study / "fetch_manifest.json").is_file()
    current = {str(p) for info in found["samples"].values() for u in info["units"].values() for p in u.values()}
    if registered:
        man = json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8"))
        known = {f["path"]: f for f in man.get("files", [])}
        stale = [p for p in current if p not in known or known[p]["bytes"] != Path(p).stat().st_size
                 or known[p].get("mtime") != int(Path(p).stat().st_mtime)]
        gone = [p for p in known if p not in current]
        if stale or gone:
            st.warning(f"The folder changed since the files were registered ({len(stale)} new or modified, "
                       f"{len(gone)} removed): register them again.")
        else:
            st.success(f"✅ {len(current)} file(s) registered: md5 and SHA-256 recorded in {study.name}.")
    if running:
        st.info(f"⏳ {running[0]['label']} — in progress (see Background tasks below).")
    elif not blocking and st.button("Register the files (compute their fingerprints)" if not registered
                                    else "Register again", key="local_register",
                                    type="primary" if not registered else "secondary"):
        cmd = [sys.executable, str(HERE / "omicchain_local.py"), "register", str(folder), "--study", str(study),
               "--name", name]
        oj.start(jobs_root, cmd, label=f"{name}: register {len(current)} FASTQ file(s)", key=key, cwd=project_dir)
        st.rerun()
    if not registered or not (study / "samples.tsv").is_file():
        return

    _stage(2, "Samples (describe them: condition, cell line, batch...)")
    table = render_annotation(study, name)
    free = shutil.disk_usage(study).free
    render_compare_and_run(study, name, table, jobs_root, project_dir, running, key,
                           to_download=lambda samples: 0, free=free, first_stage=3)


def render_matrix(jobs_root, project_dir):
    """Source: a count matrix."""
    project_dir = Path(project_dir)
    st.session_state.setdefault("studies_root", str(project_dir / "studies"))
    studies_root = Path(st.session_state["studies_root"].strip().strip('"'))
    up = st.file_uploader("Count matrix — TSV or CSV: gene identifiers in the first column, one column per sample, "
                          "raw counts", type=["tsv", "csv", "txt"], key="matrix_upload")
    if up is not None and st.session_state.get("matrix_upload_id") != up.file_id:
        st.session_state["matrix_upload_id"] = up.file_id
        try:
            d, _ = om.import_matrix(up.getvalue(), up.name, studies_root)
            st.session_state["matrix_dataset"] = d.name
            st.session_state.pop("matrix_error", None)
        except ValueError as e:
            st.session_state["matrix_error"] = f"{up.name}: {e}"
        st.rerun()
    if st.session_state.get("matrix_error"):
        st.error(st.session_state["matrix_error"])
    existing = sorted(p.name for p in studies_root.glob("matrix_*") if (p / "samples.tsv").is_file()) \
        if studies_root.is_dir() else []
    if not existing:
        st.caption("Then describe the samples (or import a sample sheet) and choose the comparison.")
        return
    if st.session_state.get("matrix_dataset") not in existing:
        st.session_state["matrix_dataset"] = existing[-1]
    ds = st.selectbox("Dataset", existing, key="matrix_dataset", format_func=lambda n: n[len("matrix_"):])
    study = studies_root / ds
    acc = ds

    _stage(1, "Matrix")
    man = json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8"))["provenance"]
    st.markdown(f"`{man['original_file']}` — {man['genes']} genes × {man['samples']} samples · SHA-256 "
                f"`{man['original_sha256'][:16]}…`")
    for w in json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8")).get("warnings", []):
        st.warning(w)

    _stage(2, "Samples (describe them, or import a sample sheet)")
    table = render_annotation(study, acc)

    sel = select_comparison(table, acc, 3, hint="add a column in step 2 or import a sample sheet")
    if sel is None:
        return
    cond, ref_level, test_level, chosen = sel
    _stage(4, "Use these data")
    out = study / "comparison"
    if st.button("Use these data for the analysis", key=f"matrix_use_{acc}", type="primary"):
        om.comparison_files(study, table, cond, ref_level, test_level, chosen, out)
        load_counts_into_pipeline(out, project_dir)
        st.rerun()
    if st.session_state.get("study_counts_dir") == str(out):
        st.caption("✔ Loaded: go to the Analysis step to choose a covariate and run the analysis.")


def render_data_source(jobs_root, project_dir):
    st.subheader("Data")
    source = st.radio("Start from", ["A public study (GEO / SRA)", "My FASTQ files", "A count matrix"],
                      key="data_source", horizontal=True)
    st.caption("Whatever the source, its origin (accession, raw files and their checksums, reference, alignment and "
               "counting, your sample annotations) is certified with the analysis and listed in the certificate.")
    if source.startswith("A public"):
        render_geo(jobs_root, project_dir)
    elif source.startswith("My FASTQ"):
        render_local(jobs_root, project_dir)
    else:
        render_matrix(jobs_root, project_dir)


def render_data_tab(project_dir):
    jobs_root = Path(project_dir) / "jobs"
    st.header("Data & reference")
    render_data_source(jobs_root, project_dir)
    st.markdown("---")
    render_reference(jobs_root, project_dir)
    st.markdown("---")
    st.subheader("Background tasks")
    st.caption("Long steps run in the background: you can refresh the page or close the browser, "
               "they keep running and are found again here.")
    any_running = any(j["status"] == "running" for j in oj.list_jobs(jobs_root))
    st.fragment(run_every=3 if any_running else None)(render_jobs)(jobs_root)
