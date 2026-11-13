"""Certified analyses found in results/: list them and reopen one (fingerprint, parameters, blockchain proof)."""

import datetime as _dt
import json
import re
from pathlib import Path

PROOF_FILE = "blockchain_proof.json"
PROOF_KEYS = ("tx_hash", "network_used", "proof_timestamp", "proof_block", "proof_contract", "explorer_used",
              "proof_bundle")


def save_proof(results_dir, state):
    """Write the blockchain proof next to the published results."""
    proof = {k: state.get(k) for k in PROOF_KEYS}
    Path(results_dir, PROOF_FILE).write_text(json.dumps(proof, indent=2, default=str) + "\n", encoding="utf-8")


def _proof_from_crate(folder):
    """Proof of an analysis published before blockchain_proof.json existed (read from the crate)."""
    p = folder / "ro-crate-metadata.json"
    try:
        graph = json.loads(p.read_text(encoding="utf-8")).get("@graph", [])
    except (OSError, ValueError):
        return None
    ev = next((e for e in graph if e.get("@id") == "#blockchain-proof"), None)
    if not ev:
        return None
    desc = ev.get("description", "")
    grab = lambda label: (re.search(label + r": ([^.\s]+)", desc) or [None, None])[1]
    net = re.search(r"Network: (.+?)\. Contract", desc)
    url = ev.get("url") or ""
    block = grab("Block")
    return {"tx_hash": ev.get("identifier"), "network_used": net.group(1) if net else None,
            "proof_timestamp": int(ev["startDate"]) if str(ev.get("startDate", "")).isdigit() else ev.get("startDate"),
            "proof_block": int(block) if block and block.isdigit() else block, "proof_contract": grab("Contract"),
            "explorer_used": url.split("/tx/")[0] if "/tx/" in url else None, "proof_bundle": None}


def _params(folder):
    out = {}
    try:
        for line in (folder / "analysis_parameters.txt").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _data_label(folder):
    try:
        o = json.loads((folder / "data_origin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "data loaded without recorded origin"
    d = o.get("design") or {}
    what = (o.get("source") or {}).get("original_file") if str(o.get("schema", "")).startswith("omicchain.matrix") \
        else (o.get("study") or {}).get("accession")
    comp = f" — {d.get('test_level')} vs {d.get('reference_level')}" if d.get("test_level") else ""
    return f"{what or '?'}{comp}"


def list_analyses(results_root):
    """Return the analyses (most recent first) as dicts: folder, bundle, date, params, data, proof,
    has_certificate, has_crate."""
    out = []
    root = Path(results_root)
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        h = folder / "bundle_hash.txt"
        if not folder.is_dir() or folder.name.startswith("_") or not h.is_file() \
                or not (folder / "bundle_manifest.json").is_file():
            continue
        bundle = h.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", bundle):
            continue
        proof = None
        try:
            proof = json.loads((folder / PROOF_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            proof = _proof_from_crate(folder)
            if proof:
                proof["proof_bundle"] = bundle
        if proof and (proof.get("proof_bundle") != bundle or not proof.get("tx_hash")):
            proof = None
        out.append({"folder": folder, "bundle": bundle, "mtime": h.stat().st_mtime,
                    "date": _dt.datetime.fromtimestamp(h.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "params": _params(folder), "data": _data_label(folder), "proof": proof,
                    "has_certificate": (folder / "omicchain_certificate.pdf").is_file(),
                    "has_crate": (folder / "omicchain-crate.zip").is_file()})
    return sorted(out, key=lambda a: -a["mtime"])


def table(analyses):
    import pandas as pd
    rows = []
    for a in analyses:
        p = a["params"]
        rows.append({"Certified": a["date"], "Data": a["data"],
                     "padj <": p.get("padj_threshold", ""), "|log2FC| ≥": p.get("lfc_threshold", ""),
                     "Covariate": p.get("covariate", "") or "—",
                     "Published": (a["proof"].get("network_used") or "yes") if a["proof"] else "not yet",
                     "Fingerprint": a["bundle"][:16] + "…"})
    return pd.DataFrame(rows)


def restore(analysis, state):
    """Reopen an analysis: its folder, fingerprint and proof become those of the session."""
    state["results_dir"] = str(analysis["folder"])
    state["bundle_hash"] = analysis["bundle"]
    state.pop("pending_tx", None)
    for k in PROOF_KEYS:
        state.pop(k, None)
    if analysis["proof"]:
        for k in PROOF_KEYS:
            if analysis["proof"].get(k) is not None:
                state[k] = analysis["proof"][k]
        state["proof_bundle"] = analysis["bundle"]


def render(results_root, state, st):
    """List of the certified analyses, with a button to reopen one."""
    analyses = list_analyses(results_root)
    if not analyses:
        st.caption("No certified analysis yet: they are listed here once the analysis has run.")
        return
    st.dataframe(table(analyses), hide_index=True, width="stretch")
    labels = {i: f"{a['date']} · {a['data']} · padj {a['params'].get('padj_threshold', '?')}, "
                 f"|log2FC| {a['params'].get('lfc_threshold', '?')}"
                 + (f", {a['params']['covariate']}" if a["params"].get("covariate") else "")
              for i, a in enumerate(analyses)}
    c1, c2 = st.columns([4, 1])
    with c1:
        i = st.selectbox("Analysis", list(labels), format_func=labels.get, key="history_choice",
                         label_visibility="collapsed")
    with c2:
        go = st.button("Open", key="history_open")
    current = state.get("bundle_hash") == analyses[i]["bundle"] and state.get("results_dir") == str(analyses[i]["folder"])
    if go:
        restore(analyses[i], state)
        st.rerun()
    if current:
        st.caption("This analysis is the one currently open: its fingerprint, proof and certificate are in the "
                   "Blockchain proof and Certificate steps.")
    st.caption("One folder per set of parameters: running again with the same thresholds and covariate on other "
               "data replaces the previous analysis here (a crate already downloaded stays valid).")
