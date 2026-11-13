import json, subprocess, sys, urllib.parse
from pathlib import Path
PAGE = str(Path(__file__).resolve().parent.parent / "portal" / "orcid-callback.html")
BASE = "https://ur24144-prodicet.github.io/OmicChain/orcid-callback.html"
FAILED = []
def run(query):
    out = subprocess.run(["node", "relay_target.js", PAGE, BASE + query], capture_output=True, text=True, check=True).stdout
    return json.loads(out)
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)
def parts(t):
    u = urllib.parse.urlparse(t); return u.scheme + "://" + u.netloc + u.path, dict(urllib.parse.parse_qsl(u.query))

r = run("?code=AbC123&state=s-t_8"); dest, q = parts(r["target"])
check("1. code + state -> forwarded to 127.0.0.1:8765/callback", dest == "http://127.0.0.1:8765/callback" and q == {"code": "AbC123", "state": "s-t_8"}, r)
check("1. fallback link identical to the target", r["link"] == r["target"], r)
r = run("?error=access_denied&error_description=User+denied&state=xyz"); dest, q = parts(r["target"])
check("2. ORCID refusal -> error forwarded as is", q == {"error": "access_denied", "error_description": "User denied", "state": "xyz"}, r)
r = run("")
check("3. opened without parameters -> forwards nothing", r["target"] is None and r["title"] == "Nothing to forward", r)
r = run("?code=abc")
check("4. code without state -> forwards nothing", r["target"] is None, r)
r = run("?code=abc&state=s&redirect_uri=https://evil.example/steal&next=https://evil.example&target=https://evil.example")
dest, q = parts(r["target"])
check("5. HIJACK: destination imposed in the URL ignored", dest == "http://127.0.0.1:8765/callback" and set(q) == {"code", "state"}, r)
r = run("?code=" + urllib.parse.quote('"><img src=x onerror="window.PWNED=1">') + "&state=" + urllib.parse.quote("<script>window.PWNED=2</script>"))
dest, q = parts(r["target"])
check("6. INJECTION: nothing is executed or inserted in the page", r["pwned"] is None and r["imgs"] == 0 and dest == "http://127.0.0.1:8765/callback", r)
check("6. ... the value is forwarded encoded, intact", q["code"] == '"><img src=x onerror="window.PWNED=1">', q)
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
