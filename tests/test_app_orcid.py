"""Full path in the application (AppTest) with a mock ORCID and a mock Rscript.
Usage: python tests/test_app_orcid.py"""
import json, os, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE))
PROJ = Path("/tmp/omicchain_testproj")
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)

import mockorcid as m
import omicchain_identity as oidn
from eth_account import Account

wallet = Account.create(); ADDR = wallet.address
p = m.Provider()
oidn.ORCID_BASES["production"] = p.base
oidn.webbrowser.open = lambda url: (m.browser_hook()(url), True)[1]

os.chdir(PROJ); sys.path.insert(0, str(PROJ))
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": ADDR, "PRIVATE_KEY": wallet.key.hex(),
                   "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
for k in ("ORCID_CLIENT_ID", "ORCID_CLIENT_SECRET", "ORCID_ENV", "OMICCHAIN_VERIFY_URL"): os.environ.pop(k, None)

from streamlit.testing.v1 import AppTest
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)

def pdf_text(path): return " ".join(subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout.split())
def btn(at, text): return next(b for b in at.button if text in b.label)
def texts(elements): return " | ".join(e.value for e in elements)

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=180)
at.run()
check("0. the application loads without exception", not at.exception, texts(at.exception))
btn(at, "Run and certify the analysis").click().run()
check("1. pipeline run from the application", "bundle_hash" in at.session_state, texts(at.error))
rd = Path(at.session_state["results_dir"])
for k, v in {"tx_hash": "ab" * 32, "network_used": "Base Mainnet", "proof_block": 7, "proof_timestamp": 1758888023,
             "explorer_used": "https://basescan.org", "proof_contract": "0x" + "11" * 20}.items(): at.session_state[k] = v
at.run()
check("2. no link: information message, no error", any("No ORCID link" in i.value for i in at.info), texts(at.info))

btn(at, "Link my ORCID iD").click().run()
check("3. ORCID credentials missing: clear message, nothing started", any("ORCID_CLIENT_ID" in e.value for e in at.error), texts(at.error))

os.environ.update({"ORCID_CLIENT_ID": p.client_id, "ORCID_CLIENT_SECRET": p.client_secret})
btn(at, "Link my ORCID iD").click().run()
check("4. link button: orcid_link.json created", (PROJ / "orcid_link.json").is_file(), texts(at.error))
check("4. ... and the application shows the link as confirmed", any("confirmed with ORCID" in s.value for s in at.success), texts(at.success) + texts(at.warning))

btn(at, "Generate Certificate").click().run()
check("5. generation without exception", not at.exception, texts(at.exception))
check("5. confirmed identity message", any("shown on the certificate." in s.value for s in at.success), texts(at.success))
t = pdf_text(rd / "omicchain_certificate.pdf")
check("5. certificate: ORCID iD and 'confirmed with ORCID' mention", m.ORCID_ID in t and "confirmed with ORCID" in t, t[-250:])
g = json.loads((rd / "ro-crate-metadata.json").read_text())["@graph"]; ids = [e["@id"] for e in g]
check("5. crate: ORCID author + orcid_link.json", "https://orcid.org/" + m.ORCID_ID in ids and "orcid_link.json" in ids)
v = subprocess.run([sys.executable, str(HERE / "validate_offline.py"), "-y", "validate", str(rd)], capture_output=True, text=True, timeout=150).stdout
check("5. crate valid RO-Crate 1.1", "is a valid" in v and "not a valid" not in v, v[-200:])
rep = oidn.verify_link(json.loads((rd / "orcid_link.json").read_text()), live_jwks=p.jwks, trusted_issuers=[p.base])
check("5. the link shipped in the crate verifies: VERIFIED", rep["verdict"] == "VERIFIED", rep["verdict"])

p.stop()
btn(at, "Generate Certificate").click().run()
t = pdf_text(rd / "omicchain_certificate.pdf")
check("6. ORCID unreachable: the certificate says 'NOT confirmed'", "NOT confirmed" in t and m.ORCID_ID in t, t[-250:])
check("6. ... and the application warns", any("NOT confirmed" in w.value for w in at.warning), texts(at.warning))
g = json.loads((rd / "ro-crate-metadata.json").read_text())["@graph"]; ids = [e["@id"] for e in g]
check("6. crate: file shipped but the author is no longer claimed", "orcid_link.json" in ids and "https://orcid.org/" + m.ORCID_ID not in ids, str(ids))

btn(at, "Remove the ORCID link").click().run()
check("7. link removal: file removed", not (PROJ / "orcid_link.json").exists())
btn(at, "Generate Certificate").click().run()
t = pdf_text(rd / "omicchain_certificate.pdf")
g = json.loads((rd / "ro-crate-metadata.json").read_text())["@graph"]; ids = [e["@id"] for e in g]
check("8. no link: no identity line in the certificate", "Publisher identity" not in t, t[-250:])
check("8. no link: the crate keeps no leftover (author, file)",
      "orcid_link.json" not in ids and not any(i.startswith("https://orcid.org/") for i in ids) and not (rd / "orcid_link.json").exists(), str(ids))
check("8. still no exception", not at.exception, texts(at.exception))
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
