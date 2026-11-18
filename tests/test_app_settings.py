"""The application without .env, and with a .env that only allows on-chain checks (no publishing account).
Usage: python tests/test_app_settings.py"""
import os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
KEYS = ["INFURA_URL_MAINNET", "INFURA_URL_TESTNET", "CONTRACT_PROOF_ADDRESS_MAINNET", "CONTRACT_PROOF_ADDRESS_TESTNET",
        "CONTRACT_PROOF_DEPLOY_BLOCK_MAINNET", "CONTRACT_PROOF_DEPLOY_BLOCK_TESTNET", "ACCOUNT_ADDRESS", "PRIVATE_KEY"]
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {str(detail)[-1500:]}"))
    if not cond: FAILED.append(name)
def texts(els): return " | ".join(str(e.value) for e in els)

CASES = {
    "no .env": None,
    "read-only .env": "INFURA_URL_MAINNET=http://127.0.0.1:1\nINFURA_URL_TESTNET=http://127.0.0.1:1\n"
                      "CONTRACT_PROOF_ADDRESS_MAINNET=0x1890f3a5877266e41F85faC22C389062fFCBD300\n"
                      "CONTRACT_PROOF_ADDRESS_TESTNET=0x1890f3a5877266e41F85faC22C389062fFCBD300\n"
                      "CONTRACT_PROOF_DEPLOY_BLOCK_MAINNET=51832471\n",
}
for case, dotenv in CASES.items():
    proj = Path(tempfile.mkdtemp(prefix="settings ")) / "proj"
    subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(proj)], check=True, capture_output=True)
    (proj / ".env").unlink(missing_ok=True)
    if dotenv:
        (proj / ".env").write_text(dotenv, encoding="utf-8", newline="\n")
    code = f"""
import os, sys
for k in {KEYS!r}:
    os.environ.pop(k, None)
os.chdir({str(proj)!r}); sys.path.insert(0, {str(proj)!r})
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("OmicChain.py", default_timeout=120); at.run()
verify = next(b for b in at.button if b.label == "Verify on-chain")
print("EXC", len(at.exception), " | ".join(str(e.value) for e in at.exception))
print("SIDEBAR", " | ".join(str(c.value) for c in at.sidebar.caption) + " | " + " | ".join(str(c.value) for c in at.sidebar.code))
print("INFO", " | ".join(str(i.value) for i in at.info))
print("VERIFY_DISABLED", verify.disabled)
print("TABS", len(at.tabs))
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    out = dict(line.split(" ", 1) for line in r.stdout.splitlines() if line.split(" ", 1)[0] in
               ("EXC", "SIDEBAR", "INFO", "VERIFY_DISABLED", "TABS"))
    check(f"{case}: the application starts without error", r.returncode == 0 and out.get("EXC", "").startswith("0") and int(out.get("TABS", "0")) >= 5,
          r.stdout + r.stderr)
    if case == "no .env":
        check(f"{case}: sidebar says what is not set", "not set" in out.get("SIDEBAR", "")
              and "not published or checked on-chain" in out.get("SIDEBAR", ""), out.get("SIDEBAR"))
        check(f"{case}: on-chain check disabled, the missing settings are named", out.get("VERIFY_DISABLED") == "True"
              and "INFURA_URL_MAINNET" in out.get("INFO", "") and "CONTRACT_PROOF_ADDRESS_MAINNET" in out.get("INFO", ""),
              out)
    else:
        check(f"{case}: on-chain check available", out.get("VERIFY_DISABLED") == "False", out)
        check(f"{case}: sidebar says publishing needs an account", "not published" in out.get("SIDEBAR", "")
              and "No publishing account" in out.get("SIDEBAR", ""), out.get("SIDEBAR"))

print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
