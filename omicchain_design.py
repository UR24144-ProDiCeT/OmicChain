"""Experimental design helpers: usable covariates, metadata.tsv, default levels, sample annotations."""

import re
import shlex
import subprocess

import numpy as np
import pandas as pd

from omicchain_names import clean_value

FETCH_FIXED_COLUMNS = ["sample", "title", "source_name", "organism", "library_layout",
                       "library_strategy", "runs", "read_count", "fastq_bytes"]
NOT_COVARIATES = {"sample", "group", "title", "condition"}
R_NAME = re.compile(r"^(?:[A-Za-z]|\.(?![0-9]))[A-Za-z0-9._]*\Z")


def varying_columns(df, exclude=()):
    """Columns whose value is not the same for all samples."""
    return [c for c in df.columns if c not in exclude and df[c].astype(str).nunique(dropna=False) > 1]


def covariate_problem(meta, covariate):
    """None if the covariate can be used with these samples, otherwise the reason."""
    if covariate in NOT_COVARIATES:
        return "reserved column"
    if covariate not in meta.columns:
        return "not a metadata column"
    if not R_NAME.match(covariate):
        return "not a valid R name"
    values = meta[covariate].astype(str).str.strip()
    if meta[covariate].isna().any() or (values == "").any():
        return "empty values for some samples"
    if values.nunique() < 2:
        return "a single value"
    cov = pd.get_dummies(values, drop_first=True).to_numpy(dtype=float)
    grp = (meta["group"].astype(str) == "treatment").to_numpy(dtype=float).reshape(-1, 1)
    X = np.hstack([np.ones((len(meta), 1)), cov, grp])
    if np.linalg.matrix_rank(X) < X.shape[1]:
        return "confounded with the groups"
    if X.shape[0] <= X.shape[1]:
        return "too few samples for this model"
    return None


def covariate_options(meta):
    """Return (usable covariates, {rejected column: reason})."""
    ok, rejected = [], {}
    for c in meta.columns:
        if c in NOT_COVARIATES:
            continue
        why = covariate_problem(meta, c)
        if why is None:
            ok.append(c)
        else:
            rejected[c] = why
    return ok, rejected


def build_metadata(ctrl, trt, source=None):
    """Build metadata.tsv (sample, group, extra columns of the source metadata) for the selected samples."""
    meta = pd.DataFrame({"sample": list(ctrl) + list(trt),
                         "group": ["control"] * len(ctrl) + ["treatment"] * len(trt)})
    if source is not None and "sample" in source.columns:
        extra = source.drop(columns=[c for c in ("group",) if c in source.columns]).drop_duplicates("sample")
        meta = meta.merge(extra, on="sample", how="left")
    return meta


def preselection(source, samples):
    """Control and treatment samples of a source metadata (column group)."""
    if source is None or not {"sample", "group"} <= set(source.columns):
        return [], []
    g = dict(zip(source["sample"].astype(str), source["group"].astype(str)))
    return ([s for s in samples if g.get(s) == "control"], [s for s in samples if g.get(s) == "treatment"])


CONTROL_WORDS = re.compile(r"(untreated|control|ctrl|vehicle|dmso|mock|placebo|unstimulated|non[ _-]?treated)|"
                           r"\b(ctl|wt|wild[ _-]?type|normal|healthy|baseline|none|sham|naive|pbs)\b", re.I)


def default_levels(values):
    """Default (reference, tested) levels: a control-like level if any, otherwise the most frequent ones."""
    counts = pd.Series(list(values)).value_counts()
    levels = sorted(counts.index)
    controls = [lv for lv in levels if CONTROL_WORDS.search(str(lv).replace("_", " "))]
    ref = max(controls, key=lambda lv: counts[lv]) if controls else max(levels, key=lambda lv: (counts[lv], -levels.index(lv)))
    others = [lv for lv in levels if lv != ref]
    test = max(others, key=lambda lv: (counts[lv], -levels.index(lv))) if others else None
    return ref, test


def upstream_config(study, condition, reference_level, test_level, samples=None, index="gapped", gencode="48"):
    cfg = {"study": str(study), "condition": condition, "reference_level": reference_level,
           "test_level": test_level, "index": index, "gencode": str(gencode)}
    if samples:
        cfg["samples"] = ",".join(samples)
    return cfg


def upstream_command(cfg, windows=None):
    """Command line that runs the alignment and counting for a selection (omicchain_upstream.py run)."""
    import os
    windows = (os.name == "nt") if windows is None else windows
    quote = subprocess.list2cmdline if windows else (lambda xs: " ".join(shlex.quote(x) for x in xs))
    args = ["python", "omicchain_upstream.py", "run", f"--study={cfg['study']}", f"--condition={cfg['condition']}",
            f"--reference-level={cfg['reference_level']}", f"--test-level={cfg['test_level']}",
            f"--index={cfg.get('index', 'gapped')}", f"--gencode={cfg.get('gencode', '48')}"]
    if cfg.get("samples"):
        args.append(f"--samples={cfg['samples']}")
    return quote(args)


ANNOTATED_TABLE = "samples_annotated.tsv"
ANNOTATIONS_JSON = "annotations.json"


def column_name_problem(name, existing):
    if not name:
        return "empty name"
    if not R_NAME.match(name):
        return "use letters, digits, '_' or '.', starting with a letter"
    if name in existing or name in FETCH_FIXED_COLUMNS or name == "group":
        return "this column already exists"
    return None


def load_annotations(study):
    """Return (samples.tsv with the user's columns, description of the annotations)."""
    import json
    from pathlib import Path
    study = Path(study)
    base = pd.read_csv(study / "samples.tsv", sep="\t", dtype=str).fillna("")
    info = {"user_columns": [], "rules": []}
    if (study / ANNOTATIONS_JSON).is_file():
        info.update(json.loads((study / ANNOTATIONS_JSON).read_text(encoding="utf-8")))
    cols = [c for c in info["user_columns"] if c not in base.columns]
    info["user_columns"] = cols
    if cols and (study / ANNOTATED_TABLE).is_file():
        ann = pd.read_csv(study / ANNOTATED_TABLE, sep="\t", dtype=str).fillna("")
        keep = ann[["sample"] + [c for c in cols if c in ann.columns]]
        base = base.merge(keep, on="sample", how="left").fillna("")
    for c in cols:
        if c not in base.columns:
            base[c] = ""
    return base, info


def save_annotations(study, table, info):
    """Write samples_annotated.tsv and annotations.json, or remove them when there is no annotation left."""
    import json
    from pathlib import Path
    study = Path(study)
    if not info["user_columns"]:
        for name in (ANNOTATED_TABLE, ANNOTATIONS_JSON):
            (study / name).unlink(missing_ok=True)
        return
    base_cols = list(pd.read_csv(study / "samples.tsv", sep="\t", dtype=str, nrows=0).columns)
    out = table[base_cols + info["user_columns"]].fillna("")
    for c in info["user_columns"]:
        out[c] = out[c].astype(str).map(clean_value)
    out.to_csv(study / ANNOTATED_TABLE, sep="\t", index=False, lineterminator="\n")
    (study / ANNOTATIONS_JSON).write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply_rule(table, source, contains, column, value):
    """Set `value` in `column` for the samples whose `source` contains `contains` (case-insensitive). Return (table, number of samples changed)."""
    hit = table[source].astype(str).str.contains(contains, case=False, regex=False)
    table = table.copy()
    table.loc[hit, column] = value
    return table, int(hit.sum())


def comparable_columns(table, user_columns=()):
    """Columns that can define a comparison: at least two non-empty values."""
    out = []
    for c in table.columns:
        if c in FETCH_FIXED_COLUMNS:
            continue
        vals = table[c].astype(str).str.strip()
        if vals[vals != ""].nunique() >= 2:
            out.append(c)
    return out


def mixed_layouts(table, samples):
    """{layout: [samples]} when the selected samples mix single-end and paired-end libraries."""
    if "library_layout" not in table.columns:
        return {}
    sub = table.set_index("sample").loc[list(samples), "library_layout"]
    groups = {lay: sorted(sub.index[sub == lay]) for lay in sorted(set(sub))}
    return groups if len(groups) > 1 else {}


def import_sample_sheet(table, info, data, filename):
    """Add the columns of a sample sheet (TSV / CSV with a 'sample' column), matched on the sample identifier or title. Return (message, {'table': table} or None)."""
    import hashlib
    import io
    text = data.decode("utf-8-sig", errors="replace")
    sep = "\t" if text.split("\n", 1)[0].count("\t") >= text.split("\n", 1)[0].count(",") else ","
    try:
        sheet = pd.read_csv(io.StringIO(text), sep=sep, dtype=str).fillna("")
    except Exception as e:
        return f"Could not read {filename}: {e}", None
    sheet.columns = [str(c).strip() for c in sheet.columns]
    if "sample" not in sheet.columns:
        return f"{filename}: no 'sample' column (columns: {', '.join(sheet.columns)}).", None
    sheet["sample"] = sheet["sample"].str.strip()
    key = "sample" if sheet["sample"].isin(table["sample"]).any() else "title"
    matched = sheet["sample"].isin(table[key])
    if not matched.any():
        return f"{filename}: none of its samples matches a sample identifier or title of this study.", None
    added, skipped = [], []
    table = table.copy()
    for col in [c for c in sheet.columns if c != "sample"]:
        name = re.sub(r"[^0-9A-Za-z_.]", "_", col).strip("_")
        if name and not re.match(r"^[A-Za-z]", name):
            name = "x_" + name
        if name not in info["user_columns"]:
            why = column_name_problem(name, list(table.columns))
            if why:
                skipped.append(f"{col} ({why})")
                continue
            info["user_columns"].append(name)
            table[name] = ""
        values = dict(zip(sheet["sample"], sheet[col].str.strip()))
        table[name] = [values.get(v, old) for v, old in zip(table[key], table[name])]
        added.append(name)
    if not added:
        return f"{filename}: no column could be imported ({'; '.join(skipped)}).", None
    info.setdefault("imported_sheets", []).append({
        "file": filename, "sha256": hashlib.sha256(data).hexdigest(), "matched_on": key,
        "columns": added, "samples_matched": int(matched.sum()), "samples_in_sheet": len(sheet)})
    msg = (f"{filename}: {len(added)} column(s) imported ({', '.join(added)}), "
           f"{int(matched.sum())}/{len(sheet)} sample(s) matched on {key}.")
    if skipped:
        msg += " Not imported: " + "; ".join(skipped) + "."
    return msg, {"table": table}
