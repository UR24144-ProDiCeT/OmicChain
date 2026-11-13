"""Fake OpenID Connect provider standing in for ORCID (tests only)."""
import json, secrets, socket, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jwt
from jwt.algorithms import RSAAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import omicchain_identity as oid

ORCID_ID = "0000-0002-1825-0097"

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

def make_key(kid):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    jwk = json.loads(RSAAlgorithm.to_jwk(priv.public_key())); jwk.update(kid=kid, use="sig", alg="RS256")
    return pem, jwk

class Provider:
    def __init__(self, client_id="APP-TEST", client_secret="s3cret"):
        self.client_id, self.client_secret = client_id, client_secret
        self.pem, self.jwk = make_key("key1")
        self.codes, self.behavior = {}, {}
        outer = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _json(self, obj, status=200):
                self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps(obj).encode())
            def do_GET(self):
                u = urllib.parse.urlparse(self.path); q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                if u.path == "/.well-known/openid-configuration":
                    return self._json({"issuer": outer.behavior.get("issuer", outer.base), "authorization_endpoint": outer.base + "/oauth/authorize",
                                       "token_endpoint": outer.base + "/oauth/token", "jwks_uri": outer.base + "/oauth/jwks"})
                if u.path == "/oauth/jwks":
                    return self._json({"keys": [outer.jwk]})
                if u.path == "/oauth/authorize":
                    redirect = q["redirect_uri"]
                    if outer.behavior.get("deny"):
                        loc = redirect + "?" + urllib.parse.urlencode({"error": "access_denied", "error_description": "user said no", "state": q["state"]})
                    else:
                        code = secrets.token_hex(8); outer.codes[code] = q
                        loc = redirect + "?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
                    self.send_response(302); self.send_header("Location", loc); self.end_headers(); return
                self.send_response(404); self.end_headers()
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                f = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
                req = outer.codes.get(f.get("code"))
                ok = (req and f.get("grant_type") == "authorization_code" and f.get("client_id") == outer.client_id
                      and f.get("client_secret") == outer.client_secret and f.get("redirect_uri") == req["redirect_uri"])
                if not ok: return self._json({"error": "invalid_grant"}, 400)
                self._json({"access_token": "ignored", "token_type": "bearer",
                            "id_token": outer.make_token(req["nonce"], **outer.behavior.get("token", {}))})
        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def jwks(self): return {"keys": [self.jwk]}

    def stop(self):
        self.httpd.shutdown(); self.httpd.server_close()

    def make_token(self, nonce, *, sub=ORCID_ID, aud=None, exp_in=600, iss=None, pem=None, kid="key1", extra=None):
        now = int(time.time())
        claims = {"iss": iss or self.base, "sub": sub, "aud": aud or self.client_id, "iat": now, "exp": now + exp_in,
                  "nonce": nonce, "given_name": "Josiah", "family_name": "Carberry", **(extra or {})}
        return jwt.encode(claims, pem or self.pem, algorithm="RS256", headers={"kid": kid})

def browser_hook(errors=None):
    def on_url(url):
        def go():
            try: urllib.request.urlopen(url, timeout=10).read()
            except Exception as e:
                if errors is not None: errors.append(e)
        threading.Thread(target=go, daemon=True).start()
    return on_url

def run_flow(p, address, **kw):
    kw.setdefault("on_authorize_url", browser_hook()); kw.setdefault("timeout", 10); kw.setdefault("port", free_port())
    return oid.run_login_flow(client_id=p.client_id, client_secret=p.client_secret, address=address, base_url=p.base, **kw)
