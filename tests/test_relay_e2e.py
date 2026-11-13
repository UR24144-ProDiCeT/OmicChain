import json, subprocess, sys, threading, urllib.request, urllib.parse, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mockorcid as m, omicchain_identity as oid
from eth_account import Account
PAGE = str(Path(__file__).resolve().parent.parent / "portal" / "orcid-callback.html")
RELAY = "https://ur24144-prodicet.github.io/OmicChain/orcid-callback.html"
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)

p = m.Provider(); wallet = Account.create()
seen = {}
def browser(url):
    """Simulated browser: follows ORCID -> relay page (run in jsdom) -> OmicChain."""
    def go():
        u = urllib.parse.urlparse(url)
        c = http.client.HTTPConnection(u.hostname, u.port); c.request("GET", u.path + "?" + u.query)
        loc = c.getresponse().getheader("Location"); seen["orcid_redirect"] = loc
        out = json.loads(subprocess.run(["node", "relay_target.js", PAGE, loc], capture_output=True, text=True, check=True).stdout)
        seen["relay_target"] = out["target"]
        urllib.request.urlopen(out["target"], timeout=10).read()
    threading.Thread(target=go, daemon=True).start()

flow = oid.run_login_flow(client_id=p.client_id, client_secret=p.client_secret, address=wallet.address,
                          base_url=p.base, port=8765, timeout=20, on_authorize_url=browser, redirect_uri=RELAY)
check("1. ORCID redirects to the HTTPS relay page", seen["orcid_redirect"].startswith(RELAY + "?"), seen["orcid_redirect"])
check("2. the relay page redirects to OmicChain on 127.0.0.1:8765", seen["relay_target"].startswith("http://127.0.0.1:8765/callback?"), seen["relay_target"])
check("3. the sign-in succeeds (code exchange accepted by ORCID)", bool(flow.get("id_token")))
link = oid.create_link(flow["id_token"], flow["jwks"], wallet.key, flow["salt"], issuer=flow["issuer"], audience=p.client_id)
rep = oid.verify_link(link, live_jwks=p.jwks, trusted_issuers=[p.base])
check("4. the link obtained verifies: VERIFIED", rep["verdict"] == "VERIFIED", rep["verdict"])
try:
    oid.run_login_flow(client_id="x", client_secret="y", address=wallet.address, base_url=p.base, redirect_uri="http://example.org/cb")
    check("5. non-HTTPS relay address refused", False, "no error")
except oid.IdentityError as e:
    check("5. non-HTTPS relay address refused", "https://" in str(e), str(e))
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
