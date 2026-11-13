# This software is licensed under the GNU LGPL v3.
# © 2026 [Pierre-Olivier Guichet / UR24144 ProDiCeT, University of Poitiers]


import os
import shutil
import tempfile
import json
import hashlib
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import TransactionNotFound
import omicchain_chain as och
from PIL import Image

from identicon_utils import generate_blockies
import omicchain_core as occ
import omicchain_identity as oidn
import omicchain_verify as ov
import omicchain_design as odz
import omicchain_ui_data as oui
import omicchain_explore as oex
import omicchain_ui_verify as ouv
import omicchain_ui_style as ous
import omicchain_qc as oqc
import omicchain_history as ohi
from eth_account import Account
from certificate_utils import generate_certificate_pdf


load_dotenv()

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ACCOUNT_ADDRESS = os.getenv("ACCOUNT_ADDRESS")

RPC_MAINNET = os.getenv("INFURA_URL_MAINNET")
RPC_TESTNET = os.getenv("INFURA_URL_TESTNET")

PROOF_MAINNET = os.getenv("CONTRACT_PROOF_ADDRESS_MAINNET")
PROOF_TESTNET = os.getenv("CONTRACT_PROOF_ADDRESS_TESTNET")

PROOF_DEPLOY_BLOCK_MAINNET = os.getenv("CONTRACT_PROOF_DEPLOY_BLOCK_MAINNET")
PROOF_DEPLOY_BLOCK_TESTNET = os.getenv("CONTRACT_PROOF_DEPLOY_BLOCK_TESTNET")

BASE_DIR = Path.cwd()
RESULTS_ROOT = BASE_DIR / "results"
RESULTS_ROOT.mkdir(exist_ok=True)

ABI_PROOF_PATH = BASE_DIR / "contract_abi_proof.json"

BANNER_PATH = BASE_DIR / "omicchain_banner.jpg"


def load_abi(path: Path):
    if not path.exists():
        st.error(f"Missing ABI: {path}")
        return None
    try:
        return json.load(open(path, "r"))
    except Exception as e:
        st.error(f"Error loading ABI: {e}")
        return None

@st.cache_resource
def get_web3(rpc_url: str):
    if not rpc_url:
        return None
    try:
        return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
    except Exception as e:
        st.error(f"Web3 Error: {e}")
        return None

def checksum(addr: str):
    try:
        return Web3.to_checksum_address(addr)
    except:
        return addr


def hash_hex_to_bytes32(hash_hex: str) -> bytes:
    """Convert a 64-char SHA-256 hex digest (with or without '0x') to the
    32 raw bytes expected by the contract's `bytes32` parameter. Raises
    ValueError with a clear message if the input isn't a valid digest."""
    cleaned = hash_hex.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) != 64:
        raise ValueError(
            f"Expected a 64-character SHA-256 hex digest, got {len(cleaned)} characters."
        )
    return bytes.fromhex(cleaned)

def safe_read_file_text(p: Path):
    if p.exists():
        return p.read_text().strip()
    return ""


def build_verify_url(bundle_hash: str, tx_hash: str, network_label: str):
    """Link to the public verification page, or None if OMICCHAIN_VERIFY_URL
    is not set in .env. Carries the fingerprint, the transaction and the
    network, so the page can verify immediately when the QR code is scanned."""
    base = (os.getenv("OMICCHAIN_VERIFY_URL") or "").strip()
    if not base:
        return None
    net = "base" if network_label == "Base Mainnet" else "base-sepolia"
    tx = tx_hash if str(tx_hash).startswith("0x") else f"0x{tx_hash}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}h={bundle_hash}&tx={tx}&net={net}"


ORCID_LINK_PATH = BASE_DIR / "orcid_link.json"
ORCID_PORT = 8765


@st.cache_data(ttl=600, show_spinner=False)
def cached_identity_status(path_str: str, mtime: float, publisher: str) -> dict:
    """Identity status of the ORCID link, cached for a few minutes; `mtime` invalidates the cache when the link file changes."""
    return oidn.identity_status(path_str, publisher_address=publisher)


def _iso_timestamp(ts) -> str:
    """Unix timestamp / datetime / str -> ISO 8601 (UTC), for the RO-Crate."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(ts)


def _fmt_threshold(x: float) -> str:
    """Mirrors the Snakefile's `_fmt_threshold`: keep naming in sync so the
    app always looks in the same folder Snakemake actually wrote to."""
    x = float(x)
    return f"{x:.1f}" if x.is_integer() else f"{x:g}"


def results_dir_for(padj: float, lfc: float, covariate: str = "") -> Path:
    """Per-analysis results folder, e.g. results/padj_0.05_lfc_1.0 — one
    folder per (padj, lfc) combination so that re-running with different
    thresholds never overwrites a previous analysis. With a covariate in the
    model, a distinct folder (..._cov_<name>), as in the Snakefile."""
    name = f"padj_{_fmt_threshold(padj)}_lfc_{_fmt_threshold(lfc)}"
    return RESULTS_ROOT / (f"{name}_cov_{covariate}" if covariate else name)


def snakemake_config_args(padj, lfc, covariate=""):
    args = [f"padj_threshold={padj}", f"lfc_threshold={lfc}"]
    if covariate:
        args.append(f"covariate={covariate}")
    return args


def active_results_dir() -> Path:
    """The results folder tied to the analysis actually executed (and whose
    bundle_hash is in session_state) — not necessarily the one matching the
    number_input widgets' current position, which the user may have changed
    since running the pipeline."""
    stored = st.session_state.get("results_dir")
    return Path(stored) if stored else RESULTS_ROOT


def _parse_analysis_parameters(raw_text: str) -> dict:
    """Parse the key=value lines written by the Snakemake rule write_parameters."""
    params = {}
    for line in raw_text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            params[key.strip()] = value.strip()
    return params


def _parse_grid_manifest(raw_text: str):
    """Parse results/_exploration/grid_manifest.txt into:
    - padj_grid / lfc_grid (lists of float, in the order Snakemake tested)
    - n_combinations (int)
    - combo_counts: {(padj, lfc): n_significant}
    """
    padj_grid, lfc_grid, n_combinations = [], [], 0
    combo_counts = {}
    for line in raw_text.splitlines():
        if line.startswith("padj_grid="):
            padj_grid = [float(x) for x in line.split("=", 1)[1].split(",") if x]
        elif line.startswith("lfc_grid="):
            lfc_grid = [float(x) for x in line.split("=", 1)[1].split(",") if x]
        elif line.startswith("n_combinations="):
            n_combinations = int(line.split("=", 1)[1])
        elif line.startswith("padj=") and "n_significant=" in line:
            try:
                fields = dict(part.split("=", 1) for part in line.split(","))
                combo_counts[(float(fields["padj"]), float(fields["lfc"]))] = int(
                    fields["n_significant"]
                )
            except (KeyError, ValueError):
                continue
    return padj_grid, lfc_grid, n_combinations, combo_counts


@st.cache_data(show_spinner=False)
def get_identicon_image(hash_value: str):
    """Deterministic Blockies representation of a hash, generated in-memory
    for inline display. The canonical file used for the certificate is the
    one produced by the Snakemake rule `generate_identicons`, inside the
    analysis' own results folder (see active_results_dir())."""
    return generate_blockies(hash_value)


def display_hash_with_identicon(hash_value: str, label: str, code_chars: int = None):
    """Display a hash together with its deterministic Blockies representation."""
    if not hash_value:
        return

    icon = get_identicon_image(hash_value)
    col_hash, col_icon = st.columns([4, 1])

    with col_hash:
        ous.label(label)
        shown = hash_value[:code_chars] if code_chars else hash_value
        st.code(shown)

    with col_icon:
        st.image(icon, width=128)


def build_contract(w3, address, abi_path):
    abi = load_abi(abi_path)
    if not abi:
        return None
    return w3.eth.contract(address=checksum(address), abi=abi)


def publish_proof_onchain(bundle_hash, w3, contract_proof, on_sent=None):
    """Publish the fingerprint (see omicchain_chain.publish_hash)."""
    return och.publish_hash(w3, contract_proof, ACCOUNT_ADDRESS, PRIVATE_KEY,
                            bundle_hash, on_sent=on_sent)


def store_proof(receipt, bundle_hash):
    st.session_state["tx_hash"] = receipt["tx_hash"]
    st.session_state["network_used"] = network
    st.session_state["proof_timestamp"] = receipt["timestamp"]
    st.session_state["proof_block"] = receipt["block"]
    st.session_state["proof_contract"] = PROOF
    st.session_state["explorer_used"] = explorer
    st.session_state["proof_bundle"] = bundle_hash
    st.session_state.pop("pending_tx", None)
    try:
        ohi.save_proof(active_results_dir(), st.session_state)
    except OSError:
        pass


st.set_page_config(page_title="OmicChain - Analytical Integrity for RNASeq workflow", page_icon="🧬", layout="wide")

ous.apply()


st.sidebar.header("Blockchain settings")

network = st.sidebar.selectbox(
    "Select Network",
    ["Base Mainnet", "Base Testnet"]
)

if network == "Base Mainnet":
    RPC = RPC_MAINNET
    PROOF = PROOF_MAINNET
    explorer = "https://basescan.org"
    PROOF_DEPLOY_BLOCK = PROOF_DEPLOY_BLOCK_MAINNET
else:
    RPC = RPC_TESTNET
    PROOF = PROOF_TESTNET
    explorer = "https://sepolia.basescan.org"
    PROOF_DEPLOY_BLOCK = PROOF_DEPLOY_BLOCK_TESTNET

w3 = get_web3(RPC)

for _label, _value in (("RPC", RPC.split("//")[-1].split("/")[0]), ("Account", ACCOUNT_ADDRESS[:42]), ("Proof contract", PROOF[:42])):
    st.sidebar.caption(_label)
    st.sidebar.code(_value, language=None, wrap_lines=True)

_orcid = None
if ORCID_LINK_PATH.is_file():
    try:
        _orcid = json.loads(ORCID_LINK_PATH.read_text(encoding="utf-8")).get("orcid")
    except (OSError, ValueError):
        _orcid = None
ous.header(network, ACCOUNT_ADDRESS, _orcid, image=ous.brand_image(BASE_DIR))

_addr = st.get_option("server.address")
if _addr not in ("127.0.0.1", "localhost", "::1"):
    st.error("🔓 **This OmicChain is reachable from other computers on the network** (Streamlit server address: "
             f"{_addr or 'all interfaces'}). Anyone who can reach it could publish with your key or start tasks. "
             "Keep the file `.streamlit/config.toml` provided with OmicChain (address = \"127.0.0.1\") in the "
             "project folder and restart.")

with st.expander("How OmicChain works"):
    st.markdown(
        "1. **Data & reference** — start from a public study (GEO / SRA: FASTQ downloaded from the ENA), your own "
        "FASTQ files, or a count matrix. FASTQ files are aligned and counted with Rsubread on a GENCODE reference. "
        "Describe the samples and choose the comparison.\n"
        "2. **Analysis** — choose the samples, an optional covariate and the thresholds (the exploration helps), "
        "then run DESeq2. Every component is hashed: code, parameters, input data, **data origin** (the raw files "
        "and how they were processed), R environment and results. Their hashes make one **composite fingerprint**.\n"
        "3. **Blockchain proof** — the fingerprint is published on Base: a public, dated, unalterable record.\n"
        "4. **Certificate** — a PDF (with an annex listing every raw file and its checksum) and an RO-Crate with "
        "all the certified files.\n"
        "5. **Verify** — anyone can check the fingerprint on-chain, and re-run the analysis from the crate: from the "
        "expression table (minutes) or from the raw data (FASTQ downloaded again or provided, or the original "
        "matrix).")


with st.expander("Certified analyses on this computer"):
    ohi.render(RESULTS_ROOT, st.session_state, st)


tab0, tab1, tab2, tab3, tab4 = st.tabs(
    ["Data & reference", "Analysis", "Blockchain proof", "Certificate", "Verify"]
)


with tab0:
    oui.render_data_tab(BASE_DIR)

EXPLORATION_DIR = BASE_DIR / "results" / "_exploration"


with tab1:
    st.header("Analysis")

    loaded = oui.loaded_data(BASE_DIR)
    have_expr = loaded is not None
    if not have_expr:
        st.info("No data loaded yet. In the Data & reference step, start from a public study (GEO / SRA), "
                "your FASTQ files or a count matrix, choose the comparison, then load the data here.")
    else:
        st.info(f"Data loaded from the Data & reference step: **{loaded['label']}** "
                f"({loaded['samples']} samples).")
        if "ctrl_samples" not in st.session_state and "trt_samples" not in st.session_state:
            src0 = pd.read_csv(BASE_DIR / "metadata_source.tsv", sep="\t", dtype=str)
            st.session_state["ctrl_samples"] = src0.loc[src0["group"] == "control", "sample"].tolist()
            st.session_state["trt_samples"] = src0.loc[src0["group"] == "treatment", "sample"].tolist()

    if have_expr:
        df = pd.read_csv(BASE_DIR / "expression_input.tsv", sep="\t", index_col=0, nrows=0)

        st.subheader("Samples compared")

        samples = df.columns.tolist()
        source_meta = (pd.read_csv(BASE_DIR / "metadata_source.tsv", sep="\t", dtype=str)
                       if (BASE_DIR / "metadata_source.tsv").exists() else None)
        for k in ("ctrl_samples", "trt_samples"):
            if k in st.session_state:
                st.session_state[k] = [x for x in st.session_state[k] if x in samples]

        col1, col2 = st.columns(2)
        
        with col1:
            ctrl = st.multiselect("Control Samples", samples, key="ctrl_samples")
        with col2:
            if "trt_samples" in st.session_state:
                st.session_state["trt_samples"] = [x for x in st.session_state["trt_samples"] if x not in ctrl]
            trt = st.multiselect("Treatment Samples", [s for s in samples if s not in ctrl], key="trt_samples")

        if ctrl and trt:
            meta = odz.build_metadata(ctrl, trt, source_meta)
            meta.to_csv(BASE_DIR / "metadata.tsv", sep="\t", index=False)

            options, rejected = odz.covariate_options(meta)
            if st.session_state.get("covariate_choice") not in [None, "(none)"] + options:
                st.session_state.pop("covariate_choice")
            if options or rejected:
                choice = st.selectbox(
                    "Covariate (optional)", ["(none)"] + options, key="covariate_choice",
                    help="A column of the metadata to account for in the model: design ~ covariate + group "
                         "(e.g. cell line or donor when each one received both conditions). "
                         "It is written in the certified analysis parameters.")
                st.session_state["covariate"] = "" if choice == "(none)" else choice
                if rejected:
                    st.caption("Not usable as covariate: "
                               + "; ".join(f"{c} ({why})" for c, why in rejected.items()))
                if st.session_state["covariate"]:
                    st.caption(f"Model: ~ {st.session_state['covariate']} + group")
            else:
                st.session_state["covariate"] = ""
        else:
            st.session_state["covariate"] = ""

        if ctrl and trt:
            st.markdown("---")
            st.subheader("Quality control of the samples (not certified)")
            oqc.render(BASE_DIR, meta, st.session_state.get("covariate", ""), (loaded or {}).get("counts_dir"))

        st.markdown("---")
        st.subheader("Explore the thresholds (not certified)")
        covariate = st.session_state.get("covariate", "")

        if not (BASE_DIR / "metadata.tsv").exists():
            st.info("Select control and treatment samples above first.")
        else:
            st.markdown(
                "Before committing to specific **padj / |log2FC|** thresholds "
                "for the certified analysis, see how many genes stay "
                "significant across a range of combinations. The DESeq2 fit "
                "runs **once** (it doesn't depend on these thresholds) and "
                "every combination is obtained by filtering that same result "
                "— fast, and nothing here is published on-chain."
            )

            if st.button("Run exploration", type="primary"):
                with st.spinner("Running exploration..."):
                    try:
                        subprocess.run(["snakemake", "--unlock"], check=False)
                        subprocess.run(["snakemake", "exploration", "-j4"]
                                       + (["--config", f"covariate={covariate}"] if covariate else []),
                                       check=True)
                        oex.save_signature(EXPLORATION_DIR, oex.data_signature(BASE_DIR, covariate), covariate)
                        st.success("✅ Exploration completed.")
                    except Exception as e:
                        st.error(f"❌ Exploration error: {e}")

            manifest_text = safe_read_file_text(EXPLORATION_DIR / "grid_manifest.txt")
            padj_grid, lfc_grid = None, None
            if manifest_text:
                padj_grid, lfc_grid, _, _ = _parse_grid_manifest(manifest_text)
            oex.render(EXPLORATION_DIR, BASE_DIR, covariate, padj_grid, lfc_grid)
            robustness_path = EXPLORATION_DIR / "robustness_table.tsv"
            if robustness_path.exists() and oex.stored_signature(EXPLORATION_DIR) and \
                    oex.stored_signature(EXPLORATION_DIR).get("signature") == oex.data_signature(BASE_DIR, covariate):
                with st.expander("Gene robustness across all the threshold pairs"):
                    st.caption("robustness_score = fraction of the threshold pairs in which the gene is significant "
                               "(1.0 = significant whatever the thresholds).")
                    st.dataframe(pd.read_csv(robustness_path, sep="\t"), width="stretch", hide_index=True, height=350)


    st.markdown("---")
    st.subheader("Analysis parameters")

    col1, col2 = st.columns(2)

    with col1:
        st.session_state.setdefault("padj_threshold", 0.05)
        st.session_state.setdefault("lfc_threshold", 1.0)
        padj_threshold = st.number_input(
            "Adjusted p-value threshold",
            min_value=0.0001,
            max_value=0.5,
            step=0.01,
            format="%.4f",
            help="A gene is significant when its adjusted p-value (Benjamini-Hochberg) is below this threshold.",
            key="padj_threshold",
        )

    with col2:
        lfc_threshold = st.number_input(
            "|log2 fold-change| threshold",
            min_value=0.0,
            max_value=10.0,
            step=0.1,
            format="%.1f",
            help="... and when its |log2 fold-change| (treatment vs control) is at least this value.",
            key="lfc_threshold",
        )


    ready_to_run = have_expr and bool(st.session_state.get("ctrl_samples")) and bool(st.session_state.get("trt_samples"))
    if not ready_to_run:
        st.caption("Load data and choose the control and treatment samples to run the analysis.")
    if st.button("Run and certify the analysis", type="primary", disabled=not ready_to_run,
                 help="Runs the DESeq2 pipeline (Snakemake) and computes the composite fingerprint of the code, "
                      "parameters, data, data origin, R environment and results."):
        with st.spinner("Running pipeline..."):
            pipeline_ok = False
            try:
                subprocess.run(["snakemake", "--unlock"], check=False)
                subprocess.run(
                    ["snakemake", "-j4", "--config",
                     *snakemake_config_args(padj_threshold, lfc_threshold,
                                            st.session_state.get("covariate", ""))],
                    check=True,
                )
                pipeline_ok = True
                st.success("✅ Snakemake pipeline completed.")
            except Exception as e:
                st.error(f"❌ Snakemake error: {e}")

            results_dir = results_dir_for(padj_threshold, lfc_threshold,
                                          st.session_state.get("covariate", ""))
            st.session_state["results_dir"] = str(results_dir)

            if pipeline_ok:
                bundle_manifest = occ.load_bundle_manifest(results_dir)
                bundle = bundle_manifest.get("bundle_hash", "")
                if bundle:
                    st.success("✅ Composite fingerprint computed.")
                    st.session_state["bundle_hash"] = bundle
                else:
                    st.error(
                        f"❌ {results_dir / 'bundle_manifest.json'} not found: "
                        "the certification step did not complete."
                    )

    params_text = safe_read_file_text(active_results_dir() / "analysis_parameters.txt")
    if params_text:
        with st.expander("Certified analysis parameters"):
            st.code(params_text)

    if st.session_state.get("bundle_hash"):
        display_hash_with_identicon(
            st.session_state["bundle_hash"], "Composite fingerprint"
        )

    st.markdown("---")
    with st.expander("Advanced: replace the pipeline files"):
        st.caption("For developers. The Snakefile and the R script are part of the certified fingerprint: "
                   "a modified version gives a different fingerprint.")
        col1, col2 = st.columns(2)
        with col1:
            snakefile = st.file_uploader("Snakefile", type=["smk"])
            if snakefile:
                (BASE_DIR / "Snakefile").write_bytes(snakefile.read())
                st.success("✅ Snakefile loaded.")
        with col2:
            r_script = st.file_uploader("R Script", type=["R"])
            if r_script:
                (BASE_DIR / "run_deseq2.R").write_bytes(r_script.read())
                st.success("✅ R script loaded.")


with tab2:
    st.header("Publish the blockchain proof")

    if st.session_state.get("bundle_hash"):
        display_hash_with_identicon(
            st.session_state["bundle_hash"], "Composite fingerprint"
        )
    else:
        st.warning("⚠️ No hash available. Execute the pipeline first.")

    bundle = st.session_state.get("bundle_hash")
    pending = st.session_state.get("pending_tx")
    already = bool(bundle) and st.session_state.get("proof_bundle") == bundle

    if pending and pending.get("bundle") == bundle and not already:
        st.warning(
            f"⏳ A transaction was already sent for this fingerprint: `{pending['tx_hash']}`. "
            "Retrieve it rather than publishing again (each publication costs gas)."
        )
        if st.button("Retrieve this transaction", type="primary"):
            contract = build_contract(w3, PROOF, ABI_PROOF_PATH)
            with st.spinner("Waiting for the confirmation..."):
                try:
                    receipt = och.fetch_proof(w3, contract, pending["tx_hash"], bundle)
                    store_proof(receipt, bundle)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error: {e}")

    if already:
        tx_done = och.as_tx_hash(st.session_state["tx_hash"])
        explorer_done = st.session_state.get("explorer_used", explorer)
        st.success(
            f"✅ Proof published on {st.session_state.get('network_used', network)}. "
            "Continue with the certificate tab."
        )
        st.markdown(f"**Block**: `{st.session_state.get('proof_block')}`")
        st.markdown(f"**Transaction**: [{tx_done}]({explorer_done}/tx/{tx_done})")
        republish = st.checkbox("Publish it again anyway (new transaction, costs gas)")
    else:
        republish = True

    can_pay, confirmed = True, True
    if bundle and republish:
        try:
            est = och.publication_estimate(w3, build_contract(w3, PROOF, ABI_PROOF_PATH), ACCOUNT_ADDRESS, bundle)
        except Exception as e:
            est = None
            st.caption(f"The cost could not be estimated ({e}).")
        if est:
            st.markdown(f"**Estimated cost**: {est['cost_eth']:.8f} ETH · **Wallet balance**: "
                        f"{est['balance_eth']:.6f} ETH (`{ous.short_address(ACCOUNT_ADDRESS)}`)")
            if not est["enough"]:
                can_pay = False
                st.error("❌ The wallet does not hold enough ETH on this network to pay for the publication.")
        if network == "Base Mainnet":
            confirmed = st.checkbox(
                "I confirm the publication on Base Mainnet: it is public, permanent and paid in real ETH",
                key=f"confirm_mainnet_{bundle}")
        else:
            st.caption("Base Testnet is a free test network: a proof published there has no public value. "
                       "Select Base Mainnet in the sidebar for a real certificate.")

    if st.button("Publish Proof", type="primary", disabled=not republish or not confirmed or not can_pay):
        if not bundle:
            st.error("❌ No hash available.")
        else:
            contract = build_contract(w3, PROOF, ABI_PROOF_PATH)
            if not contract:
                st.error("❌ Could not load proof contract.")
            else:
                def _remember(tx):
                    st.session_state["pending_tx"] = {"tx_hash": tx, "bundle": bundle}

                with st.spinner(f"Publishing proof on {network}..."):
                    try:
                        receipt = publish_proof_onchain(bundle, w3, contract, on_sent=_remember)
                        store_proof(receipt, bundle)
                    except Exception as e:
                        st.error(f"❌ Error: {e}")
                        if st.session_state.get("pending_tx", {}).get("bundle") == bundle:
                            st.info(
                                "The transaction was sent: use **Retrieve this transaction** "
                                "(shown at the next page refresh) instead of publishing again."
                            )
                    else:
                        st.rerun()


with tab3:
    st.header("Analytical integrity certificate")

    bundle = st.session_state.get("bundle_hash")
    proof_tx = st.session_state.get("tx_hash")
    network_used = st.session_state.get("network_used", network)
    proof_block = st.session_state.get("proof_block")
    proof_timestamp = st.session_state.get("proof_timestamp")
    explorer_used = st.session_state.get("explorer_used", explorer)

    if bundle:
        display_hash_with_identicon(bundle, "Composite analytical fingerprint")
    else:
        st.warning("⚠️ Execute the pipeline first.")

    if proof_tx:
        st.success("✅ Blockchain proof available.")
        st.markdown(f"**Network**: `{network_used}`")
        st.markdown(f"**Block**: `{proof_block}`")
        st.markdown(f"**Transaction**: [{och.as_tx_hash(proof_tx)}]({explorer_used}/tx/{och.as_tx_hash(proof_tx)})")
    else:
        st.info("Publish the blockchain proof in the previous tab before generating the certificate.")

    with st.expander("Publisher identity (ORCID) — optional"):
        st.caption(
            "Links your ORCID iD to the address that publishes your fingerprints, so the "
            "certificate can show who published it. Only approve ORCID logins you started "
            "yourself: ORCID's consent screen does not display the address being linked."
        )
        link_mtime = ORCID_LINK_PATH.stat().st_mtime if ORCID_LINK_PATH.is_file() else 0.0
        identity = cached_identity_status(str(ORCID_LINK_PATH), link_mtime, str(ACCOUNT_ADDRESS))
        if identity["state"] == "verified":
            st.success("✅ " + identity["message"])
        elif identity["state"] == "none":
            st.info(identity["message"])
        else:
            st.warning("⚠️ " + identity["message"] + (
                " It will not be shown as an identity on the certificate."
                if identity["state"] in ("invalid", "address-mismatch", "sandbox") else ""))

        orcid_client_id = os.getenv("ORCID_CLIENT_ID")
        orcid_client_secret = os.getenv("ORCID_CLIENT_SECRET")
        c_link, c_remove = st.columns(2)
        with c_link:
            if st.button("Link my ORCID iD"):
                if not (orcid_client_id and orcid_client_secret and PRIVATE_KEY):
                    st.error("Set ORCID_CLIENT_ID and ORCID_CLIENT_SECRET in .env first (see IDENTITY.md).")
                else:
                    orcid_env = os.getenv("ORCID_ENV") or "production"
                    try:
                        wallet_address = Account.from_key(PRIVATE_KEY).address
                        if ACCOUNT_ADDRESS and ACCOUNT_ADDRESS.lower() != wallet_address.lower():
                            raise oidn.IdentityError(
                                f"ACCOUNT_ADDRESS ({ACCOUNT_ADDRESS}) is not the address of PRIVATE_KEY ({wallet_address})."
                            )
                        if orcid_env not in oidn.ORCID_BASES:
                            raise oidn.IdentityError("ORCID_ENV must be 'production' or 'sandbox'.")
                        with st.spinner("Waiting for the ORCID login in your browser (up to 5 minutes)…"):
                            flow = oidn.run_login_flow(
                                client_id=orcid_client_id, client_secret=orcid_client_secret,
                                address=wallet_address, base_url=oidn.ORCID_BASES[orcid_env],
                                port=ORCID_PORT, timeout=300,
                                redirect_host=os.getenv("ORCID_REDIRECT_HOST"),
                                redirect_uri=os.getenv("ORCID_REDIRECT_URI"),
                            )
                            link = oidn.create_link(
                                flow["id_token"], flow["jwks"], PRIVATE_KEY, flow["salt"],
                                issuer=flow["issuer"], audience=orcid_client_id,
                            )
                        ORCID_LINK_PATH.write_text(json.dumps(link, indent=2) + "\n", encoding="utf-8")
                        cached_identity_status.clear()
                        st.rerun()
                    except oidn.IdentityError as link_err:
                        st.error(f"❌ {link_err}")
        with c_remove:
            if ORCID_LINK_PATH.is_file() and st.button("Remove the ORCID link"):
                ORCID_LINK_PATH.unlink()
                cached_identity_status.clear()
                st.rerun()

    if bundle and proof_tx:
        if st.button("Generate Certificate (PDF) + RO-Crate", type="primary"):
            st.session_state.pop("cert_artifacts", None)
            try:
                results_dir = active_results_dir()
                cert_messages = []

                bundle_manifest = occ.load_bundle_manifest(results_dir)
                if not bundle_manifest:
                    raise FileNotFoundError(
                        f"{results_dir / 'bundle_manifest.json'} not found -- run the pipeline first."
                    )

                analysis_parameters = _parse_analysis_parameters(
                    safe_read_file_text(results_dir / "analysis_parameters.txt")
                )

                identity_now = oidn.identity_status(ORCID_LINK_PATH, publisher_address=ACCOUNT_ADDRESS)
                publisher_identity = None
                if identity_now["state"] in ("verified", "unconfirmed"):
                    publisher_identity = {
                        "orcid": identity_now["orcid"],
                        "status": identity_now["state"],
                        "verified_at": identity_now["verified_at"],
                    }
                    cert_messages.append((
                        "success" if identity_now["state"] == "verified" else "warning",
                        f"🆔 ORCID iD {identity_now['orcid']} shown on the certificate"
                        + ("." if identity_now["state"] == "verified"
                           else " as NOT confirmed (ORCID's key list could not be reached)."),
                    ))
                elif identity_now["state"] != "none":
                    cert_messages.append(("info", f"ℹ️ No ORCID identity included: {identity_now['message']}"))

                pdf_path = results_dir / "omicchain_certificate.pdf"
                generate_certificate_pdf(
                    output_path=pdf_path,
                    bundle_hash=bundle,
                    blockies_path=results_dir / "identicon_blockies.png",
                    network=network_used,
                    explorer=explorer_used,
                    tx_hash=proof_tx,
                    block_number=proof_block,
                    blockchain_timestamp=proof_timestamp,
                    proof_contract=st.session_state.get("proof_contract", PROOF),
                    publisher_address=ACCOUNT_ADDRESS,
                    components=bundle_manifest["components"],
                    analysis_parameters=analysis_parameters,
                    verify_url=build_verify_url(bundle, proof_tx, network_used),
                    publisher_identity=publisher_identity,
                    data_origin=(json.loads((results_dir / "data_origin.json").read_text(encoding="utf-8"))
                                 if (results_dir / "data_origin.json").is_file() else None),
                )
                cert_messages.append(("success", "✅ PDF certificate generated."))

                crate_zip_path = None
                try:
                    tx_full = str(proof_tx) if str(proof_tx).startswith("0x") else f"0x{proof_tx}"
                    occ.add_blockchain_proof(
                        results_dir,
                        network=network_used,
                        contract=st.session_state.get("proof_contract", PROOF),
                        tx_hash=tx_full,
                        block_number=proof_block,
                        timestamp=_iso_timestamp(proof_timestamp),
                        publisher=ACCOUNT_ADDRESS,
                        tx_url=f"{explorer_used}/tx/{tx_full}",
                        extra_files=[
                            ("omicchain_certificate.pdf", "Certificate (PDF)"),
                            ("identicon_blockies.png", "Identicon of the composite fingerprint"),
                        ],
                    )
                    if not publisher_identity:
                        occ.remove_orcid_identity(results_dir)
                    if publisher_identity:
                        occ.add_orcid_identity(
                            results_dir, ORCID_LINK_PATH, publisher_identity["orcid"],
                            name=identity_now.get("name"),
                            as_author=publisher_identity["status"] == "verified",
                        )
                    check = occ.verify_crate(results_dir, expected_bundle_hash=bundle)
                    if check["ok"] and check["matches_expected"]:
                        cert_messages.append((
                            "success",
                            "✅ RO-Crate self-check: every certified file matches "
                            "the fingerprint published on-chain.",
                        ))
                    else:
                        bad = [c["label"] for c in check["components"] if not c["ok"]]
                        cert_messages.append((
                            "warning",
                            "⚠️ RO-Crate self-check FAILED: the files in this folder no "
                            "longer match the published fingerprint"
                            + (f" (changed: {', '.join(bad)})." if bad else "."),
                        ))
                    crate_zip_path = occ.make_crate_zip(results_dir)
                except Exception as crate_err:
                    cert_messages.append(
                        ("warning", f"⚠️ RO-Crate could not be finalised: {crate_err}")
                    )

                st.session_state["cert_artifacts"] = {
                    "bundle": bundle,
                    "pdf_path": str(pdf_path),
                    "crate_path": str(crate_zip_path) if crate_zip_path else None,
                    "messages": cert_messages,
                }
            except Exception as e:
                st.error(f"❌ Certificate generation error: {e}")

        artifacts = st.session_state.get("cert_artifacts")
        if artifacts and artifacts.get("bundle") == bundle:
            for level, text in artifacts["messages"]:
                getattr(st, level)(text)

            pdf_file = Path(artifacts["pdf_path"])
            crate_file = Path(artifacts["crate_path"]) if artifacts.get("crate_path") else None

            dl1, dl2 = st.columns(2)
            with dl1:
                if pdf_file.is_file():
                    st.download_button(
                        label="Download Certificate (PDF)",
                        data=pdf_file.read_bytes(),
                        file_name="omicchain_certificate.pdf",
                        mime="application/pdf",
                    )
            with dl2:
                if crate_file and crate_file.is_file():
                    st.download_button(
                        label="Download RO-Crate (.zip)",
                        data=crate_file.read_bytes(),
                        file_name="omicchain-crate.zip",
                        mime="application/zip",
                    )


with tab4:
    st.header("Verify a certificate")
    st.markdown(
        "Paste the **composite fingerprint** and the **transaction ID** "
        "shown on an OmicChain certificate to confirm, independently, "
        "that this exact hash was published on-chain."
    )
    st.caption(
        "The proof contract only *emits an event* when a hash is published — "
        "it has no lookup function to query a hash directly — so this reads "
        "the transaction receipt itself rather than searching the whole chain."
    )

    default_hash = st.session_state.get("bundle_hash", "")
    default_tx = st.session_state.get("tx_hash", "")

    col_v1, col_v2 = st.columns(2)
    with col_v1:
        verify_hash_input = st.text_input(
            "Composite fingerprint (SHA-256)",
            value=default_hash,
            key="verify_hash_input",
        ).strip().lower()
    with col_v2:
        verify_tx_input = st.text_input(
            "Transaction ID",
            value=default_tx,
            key="verify_tx_input",
            help="As shown on the certificate, with or without the leading 0x.",
        ).strip()

    if st.button("Verify on-chain", type="primary"):
        if not verify_hash_input or not verify_tx_input:
            st.error("❌ Please provide both the fingerprint and the transaction ID.")
        else:
            tx_hash_norm = verify_tx_input if verify_tx_input.startswith("0x") else f"0x{verify_tx_input}"

            contract = build_contract(w3, PROOF, ABI_PROOF_PATH)
            if not contract:
                st.error("❌ Could not load the proof contract.")
            else:
                events = None
                receipt = None
                try:
                    with st.spinner(f"Fetching transaction on {network}..."):
                        receipt = w3.eth.get_transaction_receipt(tx_hash_norm)
                        events = contract.events.HashPublished().process_receipt(receipt)
                except TransactionNotFound:
                    st.error(
                        f"❌ Transaction not found on **{network}**. "
                        "Double-check you selected the same network the "
                        "certificate was published on (Mainnet vs Testnet), and "
                        "that the transaction ID is correct."
                    )
                except Exception as e:
                    st.error(f"❌ Error while querying the blockchain: {e}")

                if events is not None:
                    if not events:
                        st.error(
                            "❌ This transaction exists, but it doesn't contain a "
                            "`HashPublished` event for this contract. Wrong "
                            "transaction ID, or wrong network selected?"
                        )
                    else:
                        published_hash = events[0]["args"]["hash"].hex().lower()
                        publisher = events[0]["args"]["publisher"]
                        block_number = receipt.blockNumber
                        block = w3.eth.get_block(block_number)
                        ts = datetime.fromtimestamp(block["timestamp"], tz=timezone.utc)

                        if published_hash == verify_hash_input:
                            st.success("✅ Verified — this exact fingerprint was published on-chain.")
                            col_a, col_b = st.columns([4, 1])
                            with col_a:
                                st.markdown(f"**Published by**: `{publisher}`")
                                st.markdown(f"**Block**: `{block_number}`")
                                st.markdown(f"**Timestamp (UTC)**: `{ts.strftime('%Y-%m-%d %H:%M:%S')}`")
                                st.markdown(f"[🔗 View on {network} explorer]({explorer}/tx/{tx_hash_norm})")
                            with col_b:
                                st.image(get_identicon_image(verify_hash_input), width=96)
                        else:
                            st.error(
                                "❌ Mismatch — this transaction is valid and published "
                                "a hash, but it is **not** the fingerprint you provided."
                            )
                            col_a, col_b = st.columns(2)
                            with col_a:
                                st.markdown("**You provided:**")
                                st.code(verify_hash_input)
                                st.image(get_identicon_image(verify_hash_input), width=96)
                            with col_b:
                                st.markdown("**Actually published in this tx:**")
                                st.code(published_hash)
                                st.image(get_identicon_image(published_hash), width=96)

    st.markdown("---")
    with st.expander("Advanced: search without a transaction ID"):
        st.caption(
            "Because `hash` is indexed in the event, it's possible to look "
            "it up directly across a range of blocks — no transaction ID "
            "needed. This queries the node for matching logs, so a very "
            "wide block range can be slow or rejected by some RPC "
            "providers; narrow it down if that happens."
        )

        default_from_block = int(PROOF_DEPLOY_BLOCK) if PROOF_DEPLOY_BLOCK else 0

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            search_hash_input = st.text_input(
                "Composite fingerprint (SHA-256)",
                value=default_hash,
                key="search_hash_input",
            ).strip().lower()
        with col_s2:
            search_from_block = st.number_input(
                "From block",
                min_value=0,
                value=default_from_block,
                step=1,
                help=(
                    "Defaults to the contract's deployment block if "
                    "CONTRACT_PROOF_DEPLOY_BLOCK_* is set in .env; "
                    "otherwise 0 (may be slow on a busy mainnet)."
                ),
            )

        if st.button("Search on-chain by hash"):
            if not search_hash_input:
                st.error("❌ Please provide a fingerprint to search for.")
            else:
                try:
                    hash_bytes = hash_hex_to_bytes32(search_hash_input)
                except ValueError as e:
                    st.error(f"❌ Invalid fingerprint: {e}")
                    hash_bytes = None

                if hash_bytes is not None:
                    contract = build_contract(w3, PROOF, ABI_PROOF_PATH)
                    if not contract:
                        st.error("❌ Could not load the proof contract.")
                    else:
                        try:
                            with st.spinner(f"Searching {network} from block {search_from_block}..."):
                                event_topic0 = Web3.keccak(
                                    text="HashPublished(address,bytes32,uint256)"
                                )
                                raw_logs = w3.eth.get_logs({
                                    "address": checksum(PROOF),
                                    "fromBlock": int(search_from_block),
                                    "toBlock": "latest",
                                    "topics": [event_topic0, None, hash_bytes],
                                })
                                found_events = [
                                    contract.events.HashPublished().process_log(log)
                                    for log in raw_logs
                                ]
                        except Exception as e:
                            st.error(
                                f"❌ Search failed: {e}\n\n"
                                "Some RPC providers cap the number of blocks per "
                                "request. Try narrowing the starting block, or "
                                "use the block explorer's event log directly."
                            )
                            found_events = None

                        if found_events is not None:
                            if not found_events:
                                st.warning(
                                    "No publication of this fingerprint found "
                                    f"on **{network}** in the searched range."
                                )
                            else:
                                st.success(
                                    f"✅ Found {len(found_events)} publication(s) "
                                    "of this fingerprint."
                                )
                                for ev in found_events:
                                    tx_hex = ev["transactionHash"].hex()
                                    tx_hex = tx_hex if tx_hex.startswith("0x") else f"0x{tx_hex}"
                                    ts = datetime.fromtimestamp(
                                        ev["args"]["timestamp"], tz=timezone.utc
                                    )
                                    st.markdown(
                                        f"- **Published by**: `{ev['args']['publisher']}` · "
                                        f"**Block**: `{ev['blockNumber']}` · "
                                        f"**Timestamp (UTC)**: `{ts.strftime('%Y-%m-%d %H:%M:%S')}` · "
                                        f"[🔗 tx]({explorer}/tx/{tx_hex})"
                                    )

    st.caption(
        "Alternatively, every publication is also listed on the contract's "
        f"page on the block explorer — look for the **HashPublished** "
        f"events: [{PROOF[:10]}…]({explorer}/address/{PROOF})"
    )

    st.markdown("---")
    ouv.render_rerun(BASE_DIR, default_expected=st.session_state.get("verify_hash_input", ""))

_loaded = oui.loaded_data(BASE_DIR)
_bundle = st.session_state.get("bundle_hash")
_published = bool(_bundle) and st.session_state.get("proof_bundle") == _bundle and bool(st.session_state.get("tx_hash"))
ous.step_status([
    (_loaded["label"] + " loaded") if _loaded else "No data loaded yet",
    "Certified: fingerprint computed" if _bundle else "Choose, run, certify",
    "Published on-chain" if _published else "Not published yet",
    "PDF and RO-Crate",
    "Any certificate",
])
