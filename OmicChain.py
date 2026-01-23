# This software is licensed under the GNU LGPL v3.
# © 2026 [Pierre-Olivier Guichet / UR24144 ProDiCeT, University of Poitiers]

##############################################
# OmicChain.py – Complete & Corrected Version
##############################################

import os
import json
import hashlib
import subprocess
import time
from pathlib import Path

import streamlit as st
import pandas as pd
import requests
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import TransactionNotFound
from PIL import Image

#############################################################
# ENV + CONFIG
#############################################################

load_dotenv()

PINATA_API_KEY = os.getenv("PINATA_API_KEY")
PINATA_API_SECRET = os.getenv("PINATA_API_SECRET")

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
ACCOUNT_ADDRESS = os.getenv("ACCOUNT_ADDRESS")

RPC_MAINNET = os.getenv("INFURA_URL_MAINNET")
RPC_TESTNET = os.getenv("INFURA_URL_TESTNET")

PROOF_MAINNET = os.getenv("CONTRACT_PROOF_ADDRESS_MAINNET")
PROOF_TESTNET = os.getenv("CONTRACT_PROOF_ADDRESS_TESTNET")

NFT_MAINNET = os.getenv("CONTRACT_NFT_ADDRESS_MAINNET")
NFT_TESTNET = os.getenv("CONTRACT_NFT_ADDRESS_TESTNET")

BASE_DIR = Path.cwd()
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

ABI_PROOF_PATH = BASE_DIR / "contract_abi_proof.json"
ABI_NFT_PATH = BASE_DIR / "contract_abi_nft.json"

BANNER_PATH = BASE_DIR / "omicchain_banner.jpg"

#############################################################
# HELPERS
#############################################################

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

def safe_read_file_text(p: Path):
    if p.exists():
        return p.read_text().strip()
    return ""

def upload_file_to_pinata(file_path, key, secret):
    url = "https://api.pinata.cloud/pinning/pinFileToIPFS"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        headers = {
            "pinata_api_key": key,
            "pinata_secret_api_key": secret
        }
        r = requests.post(url, files=files, headers=headers)
        r.raise_for_status()
        return r.json()["IpfsHash"]

def upload_json_to_pinata(payload, key, secret):
    url = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
    headers = {
        "pinata_api_key": key,
        "pinata_secret_api_key": secret,
        "Content-Type": "application/json"
    }
    r = requests.post(url, json={"pinataContent": payload, "pinataOptions": {"cidVersion": 1}}, headers=headers)
    r.raise_for_status()
    return r.json()["IpfsHash"]

def wait_for_receipt(w3, tx_hash, timeout=300):
    start = time.time()
    while True:
        try:
            return w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            if time.time() - start > timeout:
                raise TimeoutError("Transaction timeout")
            time.sleep(3)

def build_contract(w3, address, abi_path):
    abi = load_abi(abi_path)
    if not abi:
        return None
    return w3.eth.contract(address=checksum(address), abi=abi)

def sign_and_send_tx(w3, txn, private_key):
    signed = w3.eth.account.sign_transaction(txn, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()

#############################################################
# SMART CONTRACT OPERATIONS
#############################################################

def publish_proof_onchain(bundle_hash, w3, contract_proof):
    account = checksum(ACCOUNT_ADDRESS)
    nonce = w3.eth.get_transaction_count(account)
    gas_price = w3.eth.gas_price

    try:
        est = contract_proof.functions.publishHash(bundle_hash).estimate_gas({"from": account})
        gas = int(est * 1.2)
    except:
        gas = 200000

    txn = contract_proof.functions.publishHash(bundle_hash).build_transaction({
        "from": account,
        "nonce": nonce,
        "gas": gas,
        "gasPrice": gas_price
    })

    tx_hash = sign_and_send_tx(w3, txn, PRIVATE_KEY)
    receipt = wait_for_receipt(w3, tx_hash)

    return {
        "tx_hash": receipt.transactionHash.hex(),
        "block": receipt.blockNumber
    }

def mint_nft_onchain(image_path, recipient, report_hash, proof_tx, w3, contract_nft):
    cid_img = upload_file_to_pinata(image_path, PINATA_API_KEY, PINATA_API_SECRET)

    metadata = {
        "name": f"BioProof {report_hash[:8]}",
        "description": f"Certified Pipeline {report_hash}",
        "image": f"ipfs://{cid_img}",
        "attributes": [
            {"trait_type": "Pipeline Hash", "value": report_hash},
            {"trait_type": "Proof Tx", "value": proof_tx}
        ]
    }

    cid_meta = upload_json_to_pinata(metadata, PINATA_API_KEY, PINATA_API_SECRET)
    uri = f"ipfs://{cid_meta}"

    account = checksum(ACCOUNT_ADDRESS)
    recipient = checksum(recipient)

    nonce = w3.eth.get_transaction_count(account)
    gas_price = w3.eth.gas_price

    try:
        est = contract_nft.functions.mintNFT(recipient, uri, report_hash, proof_tx).estimate_gas({"from": account})
        gas = int(est * 1.2)
    except:
        gas = 500000

    txn = contract_nft.functions.mintNFT(recipient, uri, report_hash, proof_tx).build_transaction({
        "from": account,
        "nonce": nonce,
        "gas": gas,
        "gasPrice": gas_price
    })

    tx_hash = sign_and_send_tx(w3, txn, PRIVATE_KEY)
    receipt = wait_for_receipt(w3, tx_hash)

    token_id = None
    for log in receipt.logs:
        if log.address.lower() == contract_nft.address.lower():
            if len(log.topics) >= 4:
                token_id = int(log.topics[3].hex(), 16)

    return {
        "tx_hash": receipt.transactionHash.hex(),
        "block": receipt.blockNumber,
        "metadata_cid": cid_meta,
        "metadata_url": f"https://gateway.pinata.cloud/ipfs/{cid_meta}",
        "token_id": token_id
    }

#############################################################
# STREAMLIT UI
#############################################################

st.set_page_config(page_title="OmicChain - Proof of Integrity for RNASeq pipeline", page_icon="🧬", layout="wide")

# ---- OmicChain Custom UI / CSS ----
st.markdown("""
<style>

body {
    font-family: 'Inter', sans-serif;
}

/* ------ TABS ------ */
.stTabs [data-baseweb="tab-list"] {
    gap: 20px !important;
    margin-top: 10px;
}

.stTabs [data-baseweb="tab"] {
    background: #f0f4ff;
    padding: 16px 24px !important;
    font-size: 1.2rem !important;
    border-radius: 14px !important;
    font-weight: 600;
    color: #234a7c !important;
    border: 1px solid #d6e2ff !important;
}

.stTabs [aria-selected="true"] {
    background: #001027 !important;
    color: white !important;
    border: none !important;
    box-shadow: 0px 3px 12px rgba(0,0,0,0.15);
}

/* ------ CARD STYLE ------ */
.block-container {
    padding-top: 2rem !important;
}

/* ------ SIDEBAR ------ */
[data-testid="stSidebar"] {
    background: #001027;
}

[data-testid="stSidebar"] * {
    color: white !important;
}

.sidebar-content {
    padding: 20px;
}

.stSidebar .stSelectbox label {
    color: #001027 !important;
}

/* ------ BUTTONS ------ */
.stButton>button {
    background: #001027;
    color: white;
    padding: 10px 18px;
    font-size: 1.1rem;
    border-radius: 12px;
    border: none;
    box-shadow: 0 4px 14px rgba(0,0,0,0.2);
}

.stButton>button:hover {
    background: #001027;
    scale: 1.02;
}

/* ------ BANNER ------ */
.om-banner {
    margin-top: -25px;
    margin-bottom: 15px;
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 0px 4px 22px rgba(0,0,0,0.2);
}

/* Titles */
h1, h2, h3 {
    font-weight: 800 !important;
}

</style>
""", unsafe_allow_html=True)


# Banner
st.title("OmicChain – Proof of Integrity for RNASeq pipeline")
try:
    img = Image.open(BANNER_PATH)
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        st.markdown('<div class="om-banner">', unsafe_allow_html=True)
        st.image(img, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
except:
    st.warning("Banner image not found. Please place 'omicchain_banner.png' in the project directory.")

st.markdown("---")

#############################################################
# SIDEBAR
#############################################################

st.sidebar.header("⚙️ Blockchain Settings")

network = st.sidebar.selectbox(
    "Select Network",
    ["Base Mainnet", "Base Testnet"]
)

if network == "Base Mainnet":
    RPC = RPC_MAINNET
    PROOF = PROOF_MAINNET
    NFT = NFT_MAINNET
    explorer = "https://basescan.org"
else:
    RPC = RPC_TESTNET
    PROOF = PROOF_TESTNET
    NFT = NFT_TESTNET
    explorer = "https://sepolia.basescan.org"

w3 = get_web3(RPC)

st.sidebar.info(f"""
**Active Network**: {network}  
**RPC**: `{RPC[:66]}`  
**Account**: `{ACCOUNT_ADDRESS[:42]}`  
**Proof Contract**: `{PROOF[:42]}`  
**NFT Contract**: `{NFT[:42]}`
""")

#############################################################
# TABS
#############################################################

tab1, tab2, tab3 = st.tabs(["📊 Pipeline", "🔐 Blockchain Proof", "🎨 Mint NFT"])

#############################################################
# TAB 1 – Pipeline
#############################################################

with tab1:
    st.header("1️⃣ Upload & Execute Pipeline")

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

    st.markdown("---")
    
    expr = st.file_uploader("Expression Table", type=["tsv"])

    if expr:
        df = pd.read_csv(expr, sep="\t", index_col=0)
        df.to_csv(BASE_DIR / "expression_input.tsv", sep="\t")

        st.subheader("Sample Selection")

        samples = df.columns.tolist()

        col1, col2 = st.columns(2)
        
        with col1:
            ctrl = st.multiselect("🔵 Control Samples", samples)
        with col2:
            trt = st.multiselect("🔴 Treatment Samples", [s for s in samples if s not in ctrl])

        if ctrl and trt:
            meta = pd.DataFrame({
                "sample": ctrl + trt,
                "group": ["control"] * len(ctrl) + ["treatment"] * len(trt)
            })
            meta.to_csv(BASE_DIR / "metadata.tsv", sep="\t", index=False)
            st.success("✅ metadata.tsv generated.")

    if st.button("▶️ Execute Snakemake Pipeline", type="primary"):
        with st.spinner("Running pipeline..."):
            try:
                subprocess.run(["snakemake", "--unlock"], check=False)
                subprocess.run(["snakemake", "-j4"], check=True)
                st.success("✅ Snakemake pipeline completed.")
            except Exception as e:
                st.error(f"❌ Snakemake error: {e}")

            # Read the 3 hashes
            h1 = safe_read_file_text(RESULTS_DIR / "snakefile_hash.txt")
            h2 = safe_read_file_text(RESULTS_DIR / "rscript_hash.txt")
            h3 = safe_read_file_text(RESULTS_DIR / "top_genes_hash.txt")

            bundle = hashlib.sha256((h1 + h2 + h3).encode()).hexdigest()

            st.success("✅ Pipeline hash generated!")
            st.code(f"Combined Hash: {bundle}")
            st.session_state["bundle_hash"] = bundle

#############################################################
# TAB 2 – Proof
#############################################################

with tab2:
    st.header("2️⃣ Publish Blockchain Proof")

    if st.session_state.get("bundle_hash"):
        st.info(f"**Pipeline Hash**: `{st.session_state['bundle_hash']}`")
    else:
        st.warning("⚠️ No hash available. Execute the pipeline first.")

    if st.button("📤 Publish Proof", type="primary"):
        bundle = st.session_state.get("bundle_hash")
        if not bundle:
            st.error("❌ No hash available.")
        else:
            contract = build_contract(w3, PROOF, ABI_PROOF_PATH)
            if not contract:
                st.error("❌ Could not load proof contract.")
            else:
                with st.spinner(f"Publishing proof on {network}..."):
                    try:
                        receipt = publish_proof_onchain(bundle, w3, contract)
                        st.success(f"✅ Proof published on {network}!")
                        st.session_state["tx_hash"] = receipt["tx_hash"]
                        st.session_state["network_used"] = network
                        
                        st.markdown(f"**Block**: `{receipt['block']}`")
                        st.markdown(
                            f"**Transaction**: [0x{receipt['tx_hash']}]({explorer}/tx/0x{receipt['tx_hash']})"
                        )
                    except Exception as e:
                        st.error(f"❌ Error: {e}")

#############################################################
# TAB 3 – NFT
#############################################################

with tab3:
    st.header("3️⃣ Mint NFT")

    bundle = st.session_state.get("bundle_hash")
    proof_tx = st.session_state.get("tx_hash")
    network_used = st.session_state.get("network_used", network)

    if bundle:
        st.info(f"**Pipeline Hash**: `{bundle[:64]}`")
    if proof_tx:
        st.info(f"**Proof Transaction**: `{proof_tx[:66]}...`")
    
    if network_used:
        st.success(f"**Network**: {network_used}")

    recipient = st.text_input("Recipient Address", value=ACCOUNT_ADDRESS or "", placeholder="0x...")

    if st.button("🎁 Mint NFT", type="primary"):
        if not bundle or not proof_tx:
            st.error("❌ Please publish the proof first.")
        else:
            contract = build_contract(w3, NFT, ABI_NFT_PATH)
            if not contract:
                st.error("❌ Could not load NFT contract.")
            else:
                path_img = str(RESULTS_DIR / "combined_figures.png")

                if not Path(path_img).exists():
                    st.error("❌ combined_figures.png not found.")
                else:
                    with st.spinner("Minting NFT..."):
                        try:
                            result = mint_nft_onchain(
                                path_img,
                                recipient,
                                bundle,
                                proof_tx,
                                w3,
                                contract
                            )
                            st.success("🎉 NFT minted successfully!")

                            st.markdown(
                                f"**NFT Transaction**: [0x{result['tx_hash']}]({explorer}/tx/0x{result['tx_hash']})"
                            )
                            st.markdown(f"**Block**: `{result['block']}`")
                            st.markdown(f"**Metadata CID**: `{result['metadata_cid']}`")
                            st.markdown(f"**Metadata URL**: [View on IPFS]({result['metadata_url']})")
                            if result['token_id']:
                                st.markdown(f"**Token ID**: `{result['token_id']}`")
                                st.markdown(f"**OpenSea**: [View NFT](https://opensea.io/assets/base/{NFT}/{result['token_id']})")
                        except Exception as e:
                            st.error(f"❌ Error: {e}")