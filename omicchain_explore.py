"""Threshold exploration of the Analysis step: volcano plot, counts per pair of thresholds. Not certified."""

import hashlib
import json
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

DEFAULT_PADJ_GRID = [0.001, 0.01, 0.05, 0.1]
DEFAULT_LFC_GRID = [0.0, 0.5, 1.0, 1.5, 2.0]
UP, DOWN, NS = "Up-regulated", "Down-regulated", "Not significant"


def data_signature(project_dir, covariate):
    project_dir = Path(project_dir)
    h = hashlib.sha256()
    for name in ("expression_input.tsv", "metadata.tsv"):
        p = project_dir / name
        h.update(name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest() if p.is_file() else b"-")
    h.update(("covariate=" + (covariate or "")).encode())
    return h.hexdigest()


def save_signature(exploration_dir, signature, covariate):
    Path(exploration_dir).mkdir(parents=True, exist_ok=True)
    (Path(exploration_dir) / "signature.json").write_text(
        json.dumps({"signature": signature, "covariate": covariate or ""}), encoding="utf-8")


def stored_signature(exploration_dir):
    p = Path(exploration_dir) / "signature.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def classify(df, padj, lfc):
    sig = df["padj"].notna() & (df["padj"] < padj) & (df["log2FoldChange"].abs() >= lfc)
    out = np.where(sig & (df["log2FoldChange"] > 0), UP, np.where(sig & (df["log2FoldChange"] < 0), DOWN, NS))
    return pd.Series(out, index=df.index)


def count_grid(df, padj_grid, lfc_grid):
    rows = []
    for p in padj_grid:
        for l in lfc_grid:
            c = classify(df, p, l)
            rows.append({"padj": p, "lfc": l, "up": int((c == UP).sum()), "down": int((c == DOWN).sum())})
    g = pd.DataFrame(rows)
    g["total"] = g["up"] + g["down"]
    return g


def _snap(value, grid, default):
    grid = list(grid)
    return value if value in grid else (default if default in grid else grid[0])


def _apply_thresholds(padj, lfc):
    st.session_state["padj_threshold"] = float(padj)
    st.session_state["lfc_threshold"] = float(lfc)
    st.session_state["explore_applied"] = (float(padj), float(lfc))


def render(exploration_dir, project_dir, covariate, padj_grid=None, lfc_grid=None):
    exploration_dir = Path(exploration_dir)
    full_path = exploration_dir / "full_results.tsv"
    if not full_path.is_file():
        st.info("Run the exploration above to see results here.")
        return
    stored = stored_signature(exploration_dir)
    if not stored or stored.get("signature") != data_signature(project_dir, covariate):
        st.warning("The exploration results on disk were computed for other data, another sample selection or "
                   "another covariate. Run the exploration again to see results for the current analysis.")
        return

    df = pd.read_csv(full_path, sep="\t")
    padj_grid = sorted(set(padj_grid or DEFAULT_PADJ_GRID) | {0.05})
    lfc_grid = sorted(set(lfc_grid or DEFAULT_LFC_GRID) | {0.0, 1.0})
    model = f"~ {covariate} + group" if covariate else "~ group"
    st.caption(f"DESeq2 fit on the current data ({model}); {int(df['padj'].notna().sum())} genes tested "
               f"(the others have no adjusted p-value: too few counts or outliers).")

    cur_p = st.session_state.get("padj_threshold", 0.05)
    cur_l = st.session_state.get("lfc_threshold", 1.0)
    st.session_state.setdefault("explore_padj", _snap(cur_p, padj_grid, 0.05))
    st.session_state.setdefault("explore_lfc", _snap(cur_l, lfc_grid, 1.0))
    if st.session_state["explore_padj"] not in padj_grid:
        st.session_state["explore_padj"] = _snap(cur_p, padj_grid, 0.05)
    if st.session_state["explore_lfc"] not in lfc_grid:
        st.session_state["explore_lfc"] = _snap(cur_l, lfc_grid, 1.0)
    c1, c2 = st.columns(2)
    with c1:
        p = st.select_slider("Adjusted p-value threshold (padj <)", options=padj_grid, key="explore_padj",
                             format_func=lambda x: f"{x:g}")
    with c2:
        l = st.select_slider("|log2 fold-change| threshold (≥)", options=lfc_grid, key="explore_lfc",
                             format_func=lambda x: f"{x:g}")

    cls = classify(df, p, l)
    n_up, n_down = int((cls == UP).sum()), int((cls == DOWN).sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("Significant genes", n_up + n_down)
    m2.metric("Up-regulated", n_up)
    m3.metric("Down-regulated", n_down)

    st.session_state.setdefault("explore_n_labels", 20)
    n_labels = int(st.session_state["explore_n_labels"])
    vd = df[df["padj"].notna()].copy()
    vd["regulation"] = cls[vd.index]
    vd["-log10(padj)"] = -np.log10(vd["padj"].clip(lower=1e-300))
    y_line = -np.log10(p)
    base = alt.Chart(vd).mark_circle(size=16, opacity=0.65).encode(
        x=alt.X("log2FoldChange:Q", title="log2 fold-change"),
        y=alt.Y("-log10(padj):Q", title="-log10(adjusted p-value)"),
        color=alt.Color("regulation:N", scale=alt.Scale(domain=[UP, DOWN, NS], range=["#c0392b", "#2471a3", "#c8c8c8"]),
                        legend=alt.Legend(title=None, orient="top")),
        tooltip=["Gene", alt.Tooltip("log2FoldChange:Q", format=".2f"), alt.Tooltip("padj:Q", format=".2e")])
    layers = [base, alt.Chart(pd.DataFrame({"y": [y_line]})).mark_rule(strokeDash=[4, 4], color="#555").encode(y="y:Q")]
    if l > 0:
        layers.append(alt.Chart(pd.DataFrame({"x": [-l, l]})).mark_rule(strokeDash=[4, 4], color="#555").encode(x="x:Q"))
    if n_labels:
        lab = vd.nsmallest(n_labels, "padj")
        for part, align, dx in ((lab[lab["log2FoldChange"] >= 0], "right", -5), (lab[lab["log2FoldChange"] < 0], "left", 5)):
            if len(part):
                layers.append(alt.Chart(part).mark_text(fontSize=9, dx=dx, dy=-4, align=align, color="#222").encode(
                    x="log2FoldChange:Q", y="-log10(padj):Q", text="Gene:N"))

    grid = count_grid(df, padj_grid, lfc_grid)
    grid["padj_label"] = grid["padj"].map(lambda x: f"{x:g}")
    grid["lfc_label"] = grid["lfc"].map(lambda x: f"{x:g}")
    grid["selected"] = (grid["padj"] == p) & (grid["lfc"] == l)
    padj_order = [f"{x:g}" for x in padj_grid]
    lfc_order = [f"{x:g}" for x in lfc_grid]
    heat = alt.Chart(grid).mark_rect().encode(
        x=alt.X("lfc_label:O", sort=lfc_order, title="|log2 fold-change| ≥", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("padj_label:O", sort=padj_order, title="padj <"),
        color=alt.Color("total:Q", scale=alt.Scale(scheme="blues"), legend=None),
        tooltip=[alt.Tooltip("padj_label:N", title="padj <"), alt.Tooltip("lfc_label:N", title="|log2FC| ≥"),
                 "up:Q", "down:Q", "total:Q"])
    outline = alt.Chart(grid[grid["selected"]]).mark_rect(fill=None, stroke="#e67e22", strokeWidth=3).encode(
        x=alt.X("lfc_label:O", sort=lfc_order), y=alt.Y("padj_label:O", sort=padj_order))
    text = alt.Chart(grid).mark_text(fontSize=12).encode(
        x=alt.X("lfc_label:O", sort=lfc_order), y=alt.Y("padj_label:O", sort=padj_order), text="total:Q",
        color=alt.condition("datum.total > " + str(max(1, grid["total"].max()) * 0.55), alt.value("white"), alt.value("black")))

    col_v, col_h = st.columns([3, 2], gap="large")
    with col_v:
        st.altair_chart(alt.layer(*layers).properties(height=430).interactive(), width="stretch")
        st.slider("Gene names shown (smallest padj; the final volcano plot shows 50)", 0, 100, step=5,
                  key="explore_n_labels")
        st.caption("Volcano plot — dashed lines: the thresholds chosen above; names: the genes with the smallest "
                   "padj. Hover a point to see the gene; scroll to zoom, drag to move, double-click to reset.")
    with col_h:
        st.altair_chart(alt.layer(heat, outline, text).properties(height=max(160, 46 * len(padj_grid))), width="stretch")
        st.caption("Number of significant genes for each pair of thresholds (orange: the pair chosen above). "
                   "Hover a cell for up / down.")

    with st.expander(f"Significant genes at padj < {p:g} and |log2FC| ≥ {l:g} ({n_up + n_down})"):
        sig = df[cls != NS].sort_values("padj")
        st.dataframe(sig, hide_index=True, width="stretch", height=min(400, 35 * (len(sig) + 1)))

    st.button(f"Use padj < {p:g} and |log2FC| ≥ {l:g} for the analysis", type="primary",
              on_click=_apply_thresholds, args=(p, l), key="explore_apply")
    if st.session_state.get("explore_applied") == (float(p), float(l)):
        st.success(f"Analysis parameters set to padj {p:g} and |log2FC| {l:g} (below): run the pipeline to certify it.")
