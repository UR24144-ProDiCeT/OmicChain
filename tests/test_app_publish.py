"""Publish tab: no double publication, recovery of a sent transaction, tx with 0x.
Usage: python tests/test_app_publish.py"""
import os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
PROJ = Path(tempfile.mkdtemp()) / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
from eth_account import Account
w = Account.create()
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex()})
os.chdir(PROJ); sys.path.insert(0, str(PROJ))
import omicchain_chain as och
from streamlit.testing.v1 import AppTest

TX = "0x" + "ab" * 32
BUNDLE = "cd" * 32
calls = {"publish": 0, "fetch": 0}
mode = {"publish_fails_after_send": False}
def fake_publish(w3, contract, account, key, bundle, *, on_sent=None, **k):
    calls["publish"] += 1
    on_sent and on_sent(TX)
    if mode["publish_fails_after_send"]:
        raise och.PublicationError("Block with id: '0x331134d' not found")
    return {"tx_hash": TX, "block": 123, "timestamp": 1700000000}
def fake_fetch(w3, contract, tx, bundle, **k):
    calls["fetch"] += 1
    return {"tx_hash": och.as_tx_hash(tx), "block": 123, "timestamp": 1700000000}
och.publish_hash, och.fetch_proof = fake_publish, fake_fetch
wallet = {"balance": 10 ** 16}
def fake_estimate(w3, contract, account, bundle):
    cost = 60000 * 10 ** 7
    return {"gas": 60000, "gas_price_wei": 10 ** 7, "cost_wei": cost, "balance_wei": wallet["balance"], "cost_eth": cost / 1e18,
            "balance_eth": wallet["balance"] / 1e18, "enough": wallet["balance"] >= cost}
och.publication_estimate = fake_estimate
def confirm(at):
    next(c for c in at.checkbox if "I confirm the publication on Base Mainnet" in c.label).check().run()

FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)
def btn(at, label): return next(b for b in at.button if label in b.label)
def md(at): return " | ".join(m.value for m in at.markdown)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=60)
at.session_state["bundle_hash"] = BUNDLE; at.run()
check("0. main network: cost and balance shown, button disabled without confirmation", btn(at, "Publish Proof").disabled
      and "Estimated cost" in md(at) and "0.00000060 ETH" in md(at) and "0.010000 ETH" in md(at), md(at)[-600:])
confirm(at)
check("0. confirmation ticked -> button enabled", not btn(at, "Publish Proof").disabled)
btn(at, "Publish Proof").click().run()
check("1. published once, no exception", calls["publish"] == 1 and not at.exception, [e.value for e in at.exception])
check("1. transaction link with 0x, without 0x0x", f"[{TX}](" in md(at) and "0x0x" not in md(at), md(at))
check("1. tx in session with 0x", at.session_state["tx_hash"] == TX)
check("1. block shown", "`123`" in md(at), md(at))
check("2. already published -> button disabled at once", btn(at, "Publish Proof").disabled)
at.checkbox[0].check().run()
check("2. box ticked -> republication still subject to confirmation", btn(at, "Publish Proof").disabled)
confirm(at)
check("2. box ticked + confirmation -> republication possible", not btn(at, "Publish Proof").disabled)
at.session_state["bundle_hash"] = "ef" * 32; at.run()
check("3. new fingerprint -> the previous confirmation does not apply to it", btn(at, "Publish Proof").disabled)
confirm(at)
check("3. new fingerprint confirmed -> button enabled", not btn(at, "Publish Proof").disabled)

calls.update(publish=0, fetch=0); mode["publish_fails_after_send"] = True
at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=60)
at.session_state["bundle_hash"] = BUNDLE; at.run()
confirm(at); btn(at, "Publish Proof").click().run()
check("4. error shown", any("not found" in e.value for e in at.error), [e.value for e in at.error])
at.run()
check("4. recovery button offered", any("Retrieve this transaction" in b.label for b in at.button))
btn(at, "Retrieve this transaction").click().run()
check("4. recovered without a new publication", calls == {"publish": 1, "fetch": 1}, calls)
check("4. proof saved", at.session_state["tx_hash"] == TX and at.session_state["proof_block"] == 123)
at.run()
check("4. then: already published, no recovery button",
      btn(at, "Publish Proof").disabled and not any("Retrieve" in b.label for b in at.button))
check("no exception", not at.exception, [e.value for e in at.exception])

from certificate_utils import generate_certificate_pdf
import pypdf
out = PROJ / "c.pdf"
generate_certificate_pdf(output_path=out, bundle_hash=BUNDLE, blockies_path=None, network="Base Sepolia",
                         explorer="https://sepolia.basescan.org", tx_hash=TX[2:], block_number=1,
                         blockchain_timestamp=1700000000, proof_contract="0x" + "22" * 20, publisher_address=w.address)
txt = "".join(p.extract_text() for p in pypdf.PdfReader(out).pages).replace("\n", "")
check("5. PDF: tx with 0x (given without)", TX in txt and "0x0x" not in txt, txt[:300])
wallet["balance"] = 10
at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=60)
at.session_state["bundle_hash"] = "12" * 32; at.run(); confirm(at)
check("8. insufficient balance: message, button disabled even when confirmed", btn(at, "Publish Proof").disabled
      and any("not hold enough ETH" in e.value for e in at.error), [e.value for e in at.error])
wallet["balance"] = 10 ** 16
at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=60)
at.session_state["bundle_hash"] = "12" * 32; at.run()
at.sidebar.selectbox[0].set_value("Base Testnet").run()
check("9. test network: no confirmation asked, 'no public value' mention", not btn(at, "Publish Proof").disabled
      and not any("I confirm" in c.label for c in at.checkbox) and any("no public value" in c.value for c in at.caption))
def unreachable(*a, **k): raise och.PublicationError("the network could not be reached: refused")
och.publication_estimate = unreachable
at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=60)
at.session_state["bundle_hash"] = "12" * 32; at.run()
check("10. estimate impossible: reported, no exception and no permanent block", not at.exception
      and any("could not be estimated" in c.value for c in at.caption) and btn(at, "Publish Proof").disabled, [e.value for e in at.exception])
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
