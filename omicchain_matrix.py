"""A count matrix as a data source: read and check it, import it as a dataset, write the files of a comparison with a deterministic provenance."""

import csv
import datetime as _dt
import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "0.2"
FIXED = ["sample", "title", "source_name", "organism", "library_layout", "library_strategy",
         "runs", "read_count", "fastq_bytes"]
RESERVED = {"sample", "group", "condition", "title"}


def read_matrix(data, filename):
    """Read a count matrix. Return (DataFrame genes x samples or None, errors, warnings)."""
    text = data.decode("utf-8-sig", errors="replace")
    first = text.split("\n", 1)[0]
    sep = "\t" if first.count("\t") >= first.count(",") and first.count("\t") else ("," if "," in first else "\t")
    errors, warnings = [], []
    try:
        df = pd.read_csv(io.StringIO(text), sep=sep, index_col=0)
    except Exception as e:
        return None, [f"{filename}: could not be read ({e})"], []
    df.index = df.index.astype(str).str.strip()
    df.columns = [str(c).strip() for c in df.columns]
    if df.shape[1] < 2:
        errors.append(f"{filename}: fewer than 2 sample columns (separator detected: "
                      f"{'tab' if sep == chr(9) else 'comma'}).")
        return None, errors, warnings
    dup_genes = df.index[df.index.duplicated()].unique().tolist()
    if dup_genes:
        errors.append(f"{len(dup_genes)} gene identifier(s) appear more than once (e.g. {', '.join(dup_genes[:5])}).")
    dup_samples = [c for c in set(df.columns) if list(df.columns).count(c) > 1]
    if dup_samples:
        errors.append("duplicated sample name(s): " + ", ".join(sorted(dup_samples)))
    bad_names = [c for c in df.columns if re.search(r"^Unnamed|^\s*$", c)]
    if bad_names:
        errors.append("column(s) without a sample name: " + ", ".join(bad_names))
    num = df.apply(pd.to_numeric, errors="coerce")
    non_num = [c for c in df.columns if num[c].isna().sum() > df[c].isna().sum()]
    if non_num:
        errors.append("non-numeric values in: " + ", ".join(non_num[:10]) +
                      " (is the first column the gene identifier, and are there only count columns?)")
    if num.isna().any().any() and not non_num:
        errors.append(f"{int(num.isna().sum().sum())} empty value(s).")
    if (num < 0).any().any():
        errors.append("negative values: not counts.")
    if errors:
        return None, errors, warnings
    frac = float((np.abs(num - np.round(num)) > 1e-6).to_numpy().mean())
    if frac > 0:
        warnings.append(f"{100 * frac:.1f} % of the values are not integers: DESeq2 needs raw counts "
                        "(normalised values, TPM or FPKM are not appropriate). Values will be rounded.")
    if len(num) < 1000:
        warnings.append(f"only {len(num)} genes: is this the complete matrix?")
    return num, errors, warnings


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "matrix"


def import_matrix(data, filename, studies_root, name=None):
    """Create or replace the dataset <studies_root>/matrix_<name>/. Return (folder, warnings)."""
    df, errors, warnings = read_matrix(data, filename)
    if errors:
        raise ValueError(" ".join(errors))
    name = safe_name(name or Path(filename).stem)
    d = Path(studies_root) / f"matrix_{name}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"original_{safe_name(filename)}").write_bytes(data)
    out = df.round().astype("int64")
    out.index.name = "gene"
    out.to_csv(d / "counts.tsv", sep="\t", lineterminator="\n")
    with open(d / "samples.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(FIXED)
        for s in out.columns:
            w.writerow([s, s, "", "", "", "", "", int(out[s].sum()), ""])
    manifest = {"tool": "omicchain_matrix", "version": VERSION,
                "date": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "provenance": {"input": f"MATRIX_{name}", "source": "user-provided count matrix",
                               "original_file": filename, "original_sha256": hashlib.sha256(data).hexdigest(),
                               "genes": int(out.shape[0]), "samples": int(out.shape[1]),
                               "rounded_values": any("not integers" in w for w in warnings)},
                "warnings": warnings, "files": []}
    (d / "fetch_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return d, warnings


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_expression(study, samples, dest):
    """Write expression_input.tsv: the `samples` columns of counts.tsv."""
    counts = pd.read_csv(Path(study) / "counts.tsv", sep="\t", index_col=0)
    counts[list(samples)].to_csv(dest, sep="\t", lineterminator="\n")


def comparison_files(study, table, condition, ref_level, test_level, samples, out_dir):
    """Write expression_input.tsv, metadata.tsv and matrix_provenance.json for a comparison in out_dir."""
    study, out_dir = Path(study), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_expression(study, samples, out_dir / "expression_input.tsv")
    t = table.set_index("sample").loc[samples]
    coding = {ref_level: "control", test_level: "treatment"}
    meta = pd.DataFrame({"sample": samples, "group": [coding[v] for v in t[condition]],
                         "condition": t[condition].tolist(), "title": t["title"].tolist()})
    covariates = []
    for c in t.columns:
        if c in FIXED or c == condition or c in RESERVED:
            continue
        vals = t[c].astype(str).str.strip()
        if vals.nunique() >= 2 and (vals != "").all():
            meta[c] = vals.tolist(); covariates.append(c)
    meta.to_csv(out_dir / "metadata.tsv", sep="\t", index=False, lineterminator="\n")
    man = json.loads((study / "fetch_manifest.json").read_text(encoding="utf-8"))["provenance"]
    ann = None
    if (study / "annotations.json").is_file() and (study / "samples_annotated.tsv").is_file():
        info = json.loads((study / "annotations.json").read_text(encoding="utf-8"))
        ann = {"user_columns": info.get("user_columns", []), "rules": info.get("rules", []),
               "imported_sheets": info.get("imported_sheets", []), "edited_by_hand": bool(info.get("edited_by_hand")),
               "samples_annotated_sha256": _sha(study / "samples_annotated.tsv")}
    code = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    prov = {"schema": "omicchain.matrix/1",
            "tool": {"name": "omicchain_matrix", "version": VERSION, "code_sha256": {"omicchain_matrix.py": code}},
            "source": {"original_file": man["original_file"], "original_sha256": man["original_sha256"],
                       "genes": man["genes"], "samples": man["samples"], "rounded_values": man["rounded_values"]},
            "user_annotations": ann,
            "design": {"condition_column": condition, "reference_level": ref_level, "test_level": test_level,
                       "samples": samples, "group_coding": {"control": ref_level, "treatment": test_level},
                       "candidate_covariates": covariates},
            "outputs": {n: _sha(out_dir / n) for n in ("expression_input.tsv", "metadata.tsv")}}
    (out_dir / "matrix_provenance.json").write_text(json.dumps(prov, indent=2, ensure_ascii=False) + "\n",
                                                    encoding="utf-8")
    return prov
