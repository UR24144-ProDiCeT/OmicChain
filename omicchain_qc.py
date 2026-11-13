"""Quality control of the selected samples before the analysis: PCA, library sizes, alignment rates. Not certified."""

from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

N_TOP = 500
GROUP_COLORS = {"control": "#1d5fa8", "treatment": "#c2410c"}


def log_cpm(counts):
    lib = counts.sum(axis=0).replace(0, np.nan)
    return np.log2(counts.div(lib, axis=1) * 1e6 + 1).fillna(0.0)


def pca(counts, n_top=N_TOP):
    """PCA of the samples (log2 CPM, most variable genes). Return (coordinates, variance ratio per axis, number of genes used), or None with too little data."""
    if counts.shape[1] < 3:
        return None
    lc = log_cpm(counts)
    expressed = lc[(lc > 1).sum(axis=1) >= 2]
    if len(expressed) < 10:
        return None
    var = expressed.var(axis=1)
    top = expressed.loc[var.sort_values(ascending=False, kind="stable").index[:n_top]]
    x = top.to_numpy(dtype=float)
    x = x - x.mean(axis=1, keepdims=True)
    _, s, vt = np.linalg.svd(x, full_matrices=False)
    total = float((s ** 2).sum())
    if total <= 0:
        return None
    k = min(4, len(s))
    coords = pd.DataFrame((vt[:k].T * s[:k]), index=counts.columns, columns=[f"PC{i + 1}" for i in range(k)])
    return coords, [float(v) for v in (s[:k] ** 2) / total], len(top)


def explained_by(values, factor):
    """Share of the variance of `values` (one PCA axis) explained by a factor (eta squared, 0..1)."""
    values = np.asarray(values, dtype=float)
    total = float(((values - values.mean()) ** 2).sum())
    if total <= 0:
        return 0.0
    between = 0.0
    factor = np.asarray([str(f) for f in factor])
    for level in np.unique(factor):
        v = values[factor == level]
        between += len(v) * float((v.mean() - values.mean()) ** 2)
    return between / total


def factor_summary(coords, meta, columns):
    """What each metadata column explains of the first two axes: [{'column', 'PC1', 'PC2'}]."""
    m = meta.set_index("sample").loc[coords.index]
    rows = []
    for c in columns:
        if c in m.columns and m[c].astype(str).nunique() >= 2:
            rows.append({"column": c, "PC1": explained_by(coords["PC1"], m[c]),
                         "PC2": explained_by(coords["PC2"], m[c]) if "PC2" in coords else 0.0})
    return rows


def advice(summary, covariate):
    """Advice sentence derived from factor_summary (None when nothing stands out)."""
    by = {r["column"]: r for r in summary}
    grp = by.get("group")
    if grp is None:
        return None
    others = [r for r in summary if r["column"] != "group"]
    best = max(others, key=lambda r: r["PC1"], default=None)
    if best and best["PC1"] >= 0.6 and grp["PC1"] < 0.4 and best["column"] != covariate:
        return ("warning", f"The main axis of variation (PC1) follows **{best['column']}** ({best['PC1']:.0%}) rather "
                           f"than the groups ({grp['PC1']:.0%}). Consider **{best['column']}** as covariate above: the "
                           "model then compares the groups within each of its levels.")
    if grp["PC1"] >= 0.6 or grp["PC2"] >= 0.6:
        axis = "PC1" if grp["PC1"] >= grp["PC2"] else "PC2"
        return ("success", f"The groups separate on {axis} ({max(grp['PC1'], grp['PC2']):.0%} of that axis is "
                           "explained by control / treatment).")
    if max(grp["PC1"], grp["PC2"]) < 0.3:
        return ("info", "The groups do not separate on the first two axes: the treatment effect is small compared "
                        "with the other differences between samples (check the design, or a covariate).")
    return None


def mapping_table(counts_dir, samples):
    """Alignment statistics per sample (qc_summary.tsv), or None for a count matrix."""
    p = Path(counts_dir or "") / "qc_summary.tsv"
    if not counts_dir or not p.is_file():
        return None
    qc = pd.read_csv(p, sep="\t")
    need = {"sample", "fragments", "mapped", "assigned"}
    if not need <= set(qc.columns):
        return None
    g = qc.groupby("sample", sort=False)[["fragments", "mapped", "assigned"]].sum()
    g = g.loc[[s for s in samples if s in g.index]]
    if g.empty:
        return None
    return pd.DataFrame({"sample": g.index, "fragments (M)": (g["fragments"] / 1e6).round(2),
                         "aligned %": (100 * g["mapped"] / g["fragments"]).round(1),
                         "assigned to genes %": (100 * g["assigned"] / g["fragments"]).round(1)})


@st.cache_data(show_spinner=False)
def _load_counts(path, mtime, samples):
    return pd.read_csv(path, sep="\t", index_col=0, usecols=lambda c: True)[list(samples)]


def render(project_dir, meta, covariate="", counts_dir=None):
    """Show the PCA, the library sizes and the alignment rates for the selected samples."""
    expr = Path(project_dir) / "expression_input.tsv"
    samples = tuple(meta["sample"])
    counts = _load_counts(str(expr), expr.stat().st_mtime, samples)
    lib = counts.sum(axis=0)
    res = pca(counts)
    extra = [c for c in meta.columns if c not in ("sample", "group", "title", "condition")
             and meta[c].astype(str).nunique() >= 2]

    left, right = st.columns([3, 2], gap="large")
    with left:
        if res is None:
            st.info("The PCA needs at least 3 samples and 10 expressed genes.")
        else:
            coords, ratio, n_genes = res
            shape_by = st.selectbox("Shape of the points", ["(none)"] + extra, key="qc_shape",
                                    index=(1 + extra.index(covariate)) if covariate in extra else (1 if extra else 0))
            d = coords.reset_index(names="sample").merge(meta, on="sample")
            enc = dict(
                x=alt.X("PC1:Q", title=f"PC1 ({ratio[0]:.0%} of the variance)"),
                y=alt.Y("PC2:Q", title=f"PC2 ({ratio[1]:.0%})") if len(ratio) > 1 else alt.value(0),
                color=alt.Color("group:N", scale=alt.Scale(domain=list(GROUP_COLORS), range=list(GROUP_COLORS.values())),
                                legend=alt.Legend(title=None, orient="top")),
                tooltip=["sample"] + [c for c in ("title", "group", "condition") if c in d.columns] + extra)
            if shape_by != "(none)":
                enc["shape"] = alt.Shape(f"{shape_by}:N", legend=alt.Legend(title=shape_by, orient="top"))
            points = alt.Chart(d).mark_point(size=130, filled=True, opacity=0.9).encode(**enc)
            labels = alt.Chart(d).mark_text(dy=-13, fontSize=10, color="#33454d").encode(x="PC1:Q", y="PC2:Q", text="sample:N")
            st.altair_chart((points + labels).properties(height=360).interactive(), width="stretch")
            st.caption(f"PCA of the {len(samples)} selected samples: log2 CPM, the {n_genes} most variable genes. "
                       "Samples that are close have similar expression profiles.")
            summary = factor_summary(coords, meta, ["group"] + extra)
            tip = advice(summary, covariate)
            if tip:
                getattr(st, tip[0])(tip[1])
            if len(summary) > 1:
                st.dataframe(pd.DataFrame([{"Column": r["column"], "explains of PC1": f"{r['PC1']:.0%}",
                                            "of PC2": f"{r['PC2']:.0%}"} for r in summary]),
                             hide_index=True, width="stretch")
    with right:
        ld = pd.DataFrame({"sample": list(samples), "counts (M)": (lib / 1e6).round(3).values}).merge(
            meta[["sample", "group"]], on="sample")
        bars = alt.Chart(ld).mark_bar().encode(
            y=alt.Y("sample:N", sort=list(samples), title=None, axis=alt.Axis(labelOverlap=False, labelLimit=140)), x=alt.X("counts (M):Q", title="Counts assigned to genes (millions)"),
            color=alt.Color("group:N", scale=alt.Scale(domain=list(GROUP_COLORS), range=list(GROUP_COLORS.values())), legend=None),
            tooltip=["sample", "group", "counts (M)"])
        st.altair_chart(bars.properties(height=alt.Step(24)), width="stretch")
        med = float(lib.median())
        small = [s for s in samples if med > 0 and lib[s] < med / 3]
        if small:
            st.warning("Library much smaller than the others (less than a third of the median): " + ", ".join(small)
                       + ". DESeq2 normalises library size, but such a sample has little statistical weight.")
        else:
            st.caption("Library sizes: DESeq2 normalises them; a sample far below the others would be flagged here.")
        mt = mapping_table(counts_dir, samples)
        if mt is not None:
            st.dataframe(mt, hide_index=True, width="stretch")
            st.caption("Alignment (Rsubread) and assignment to genes (featureCounts), per sample.")
