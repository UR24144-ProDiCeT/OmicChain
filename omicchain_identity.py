"""Optional link between the publishing account and an ORCID iD (signed, verifiable offline)."""

import argparse
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    import jwt
    from jwt.algorithms import RSAAlgorithm
except ImportError as exc:
    raise ImportError(
        'PyJWT with cryptography is required for the ORCID link: python -m pip install "pyjwt[crypto]"'
    ) from exc

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address

__all__ = [
    "IdentityError",
    "orcid_checksum_ok",
    "normalize_orcid",
    "derive_nonce",
    "link_message",
    "verify_id_token",
    "create_link",
    "verify_link",
    "identity_status",
    "run_login_flow",
    "fetch_json",
]

LINK_SCHEMA = "omicchain.orcid-link/1"
NONCE_PREFIX = "omicchain-orcid-link-v1"
ORCID_BASES = {"production": "https://orcid.org", "sandbox": "https://sandbox.orcid.org"}
ORCID_ID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
REQUIRED_LINK_KEYS = ("schema", "orcid", "address", "salt", "issuer", "id_token", "jwk",
                      "message", "signature", "created_at")


class IdentityError(Exception):
    """Raised for any problem with the ORCID link (kept readable for users)."""


def orcid_checksum_ok(orcid: str) -> bool:
    """ISO 7064 mod 11-2 check character of an ORCID iD (0000-0000-0000-0000)."""
    if not ORCID_ID_RE.match(orcid or ""):
        return False
    digits = orcid.replace("-", "")
    total = 0
    for ch in digits[:15]:
        total = (total + int(ch)) * 2
    result = (12 - total % 11) % 11
    return digits[15] == ("X" if result == 10 else str(result))


def normalize_orcid(sub: str) -> str:
    """Accept the bare iD or its URL form; return the bare, checksum-valid iD."""
    value = str(sub or "").strip()
    match = re.match(r"^https?://(?:sandbox\.)?orcid\.org/(.+)$", value)
    if match:
        value = match.group(1)
    if not orcid_checksum_ok(value):
        raise IdentityError(f"The token's subject is not a valid ORCID iD: {sub!r}")
    return value


def derive_nonce(address: str, salt: str) -> str:
    """The nonce sent to ORCID. Anyone can recompute it from the address and
    the salt stored in the link, which is what binds the token to the address."""
    text = f"{NONCE_PREFIX}|{address.lower()}|{salt}"
    return hashlib.sha256(text.encode()).hexdigest()


def link_message(orcid: str, address: str, token_sha256: str) -> str:
    return (
        "OmicChain identity link v1\n"
        f"ORCID iD: {orcid}\n"
        f"Ethereum address: {to_checksum_address(address)}\n"
        f"ORCID id_token SHA-256: {token_sha256}"
    )


def _token_digest(id_token: str) -> str:
    return hashlib.sha256(id_token.encode()).hexdigest()


def _find_jwk(jwks: dict, kid):
    keys = (jwks or {}).get("keys") or []
    if kid is None:
        if len(keys) == 1:
            return keys[0]
        raise IdentityError("The token has no key id and the key list has several keys.")
    for key in keys:
        if key.get("kid") == kid:
            return key
    raise IdentityError(f"The signing key {kid!r} is not in the key list.")


def verify_id_token(id_token, jwk, *, expected_nonce, issuer, audience=None,
                    enforce_exp=True, leeway=60) -> dict:
    """Verify signature (RS256 only), issuer, nonce and, optionally, audience and
    expiry. Returns the claims with an added, normalized 'orcid' entry."""
    try:
        public_key = RSAAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(
            id_token,
            key=public_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            leeway=leeway,
            options={
                "require": ["exp", "iat", "iss", "sub"],
                "verify_exp": enforce_exp,
                "verify_aud": audience is not None,
            },
        )
    except jwt.PyJWTError as exc:
        raise IdentityError(f"Invalid ORCID id_token: {exc}") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise IdentityError(f"Unusable signing key: {exc}") from exc

    if not hmac.compare_digest(str(claims.get("nonce", "")), str(expected_nonce)):
        raise IdentityError(
            "The id_token's nonce does not match this Ethereum address: the ORCID "
            "login was not requested for it."
        )
    claims["orcid"] = normalize_orcid(claims["sub"])
    return claims


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_link(id_token, jwks, private_key, salt, *, issuer, audience=None) -> dict:
    """Check the id_token (live: it must still be valid) and countersign it
    with the wallet. Returns the link, ready to be saved as JSON."""
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
    except jwt.PyJWTError as exc:
        raise IdentityError(f"Malformed id_token: {exc}") from exc
    jwk = _find_jwk(jwks, kid)

    address = Account.from_key(private_key).address
    claims = verify_id_token(
        id_token, jwk, expected_nonce=derive_nonce(address, salt),
        issuer=issuer, audience=audience, enforce_exp=True,
    )
    message = link_message(claims["orcid"], address, _token_digest(id_token))
    signed = Account.sign_message(encode_defunct(text=message), private_key=private_key)
    return {
        "schema": LINK_SCHEMA,
        "orcid": claims["orcid"],
        "address": address,
        "salt": salt,
        "issuer": issuer,
        "audience": claims.get("aud"),
        "id_token": id_token,
        "jwk": jwk,
        "message": message,
        "signature": "0x" + signed.signature.hex().removeprefix("0x"),
        "created_at": _utc_now(),
    }


def _same_key(a: dict, b: dict) -> bool:
    return a.get("kty") == b.get("kty") and a.get("n") == b.get("n") and a.get("e") == b.get("e")


def verify_link(link: dict, *, live_jwks=None, trusted_issuers=None) -> dict:
    """Check a link file. `live_jwks` is ORCID's published key list: it is the
    only trust anchor (see the module notes). Returns a report with a
    'verdict': VERIFIED | CONSISTENT BUT KEY NOT CONFIRMED | NOT VERIFIED."""
    trusted = list(trusted_issuers or [ORCID_BASES["production"]])
    checks = []

    def record(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    missing = [k for k in REQUIRED_LINK_KEYS if k not in (link or {})]
    if not record("link has all required fields", not missing,
                  "missing: " + ", ".join(missing) if missing else ""):
        return {"ok": False, "verdict": "NOT VERIFIED", "orcid": None, "address": None,
                "key_confirmed": None, "checks": checks}

    orcid, address = link["orcid"], link["address"]
    record("schema is supported", link["schema"] == LINK_SCHEMA, str(link["schema"]))
    record("issuer is a recognised ORCID host", link["issuer"] in trusted, str(link["issuer"]))

    claims = None
    try:
        expected_nonce = derive_nonce(address, link["salt"])
        aud = link.get("audience") if isinstance(link.get("audience"), str) else None
        claims = verify_id_token(
            link["id_token"], link["jwk"], expected_nonce=expected_nonce,
            issuer=link["issuer"], audience=aud,
            enforce_exp=False,
        )
        record("id_token signature, issuer and nonce (bound to the address)", True)
    except IdentityError as exc:
        record("id_token signature, issuer and nonce (bound to the address)", False, str(exc))

    if claims is not None:
        record("ORCID iD in the link matches the token", claims["orcid"] == orcid,
               f"token: {claims['orcid']}, link: {orcid}")

    try:
        expected_message = link_message(orcid, address, _token_digest(link["id_token"]))
        record("signed message names this ORCID iD, address and token",
               link["message"] == expected_message)
        recovered = Account.recover_message(
            encode_defunct(text=link["message"]), signature=link["signature"])
        record("wallet signature comes from the stated address",
               recovered.lower() == address.lower(), f"recovered {recovered}")
    except Exception as exc:
        record("wallet signature comes from the stated address", False, f"{type(exc).__name__}: {exc}")

    key_confirmed = None
    if live_jwks is not None:
        kid = link["jwk"].get("kid")
        published = next((k for k in (live_jwks.get("keys") or []) if k.get("kid") == kid), None)
        key_confirmed = bool(published and _same_key(published, link["jwk"]))
        record("signing key is published by ORCID", key_confirmed,
               "" if key_confirmed else
               "the key is not (or no longer) in ORCID's key list: it may have been rotated, "
               "or the token may be forged")

    ok = all(c["ok"] for c in checks)
    if ok and key_confirmed is True:
        verdict = "VERIFIED"
    elif ok and key_confirmed is None:
        verdict = "CONSISTENT BUT KEY NOT CONFIRMED"
    else:
        verdict = "NOT VERIFIED"
    return {"ok": ok, "verdict": verdict, "orcid": orcid, "address": address,
            "key_confirmed": key_confirmed, "checks": checks,
            "claims": claims if claims is not None else {}}


def identity_status(link_path, *, publisher_address=None, fetch=True) -> dict:
    """One-stop summary of an orcid_link.json, for the certificate, the crate
    and the interface.

    state:
      none              no link file
      verified          valid AND confirmed with ORCID's published keys
      unconfirmed       valid but ORCID's key list could not be reached
      sandbox           link made on ORCID's sandbox: never presented as a real identity
      address-mismatch  the link belongs to another address than the publisher's
      invalid           the file is damaged, forged or inconsistent
    Only 'verified' (and, with a clear label, 'unconfirmed') may be shown as identity.
    """
    path = Path(link_path)
    base_result = {"state": "none", "orcid": None, "address": None, "name": None, "report": None,
                   "verified_at": None, "message": "No ORCID link yet (optional): it would show your ORCID iD on the certificate."}
    if not path.is_file():
        return base_result

    def result(state, message, report=None, **extra):
        out = {**base_result, "state": state, "message": message, "report": report}
        if report:
            out.update(orcid=report.get("orcid"), address=report.get("address"))
        out.update(extra)
        return out

    try:
        link = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return result("invalid", f"The ORCID link file cannot be read: {exc}")

    env_name = next((n for n, b in ORCID_BASES.items() if b == link.get("issuer")), None)
    if env_name is None:
        report = verify_link(link, live_jwks=None, trusted_issuers=[])
        return result("invalid", "The ORCID link names an issuer that is not ORCID.", report)
    base = ORCID_BASES[env_name]

    live = None
    if fetch:
        try:
            live = fetch_json(_discover(base)["jwks_uri"])
        except IdentityError:
            live = None
    report = verify_link(link, live_jwks=live, trusted_issuers=[base])

    if not report["ok"] or report["verdict"] == "NOT VERIFIED":
        failing = next((c for c in report["checks"] if not c["ok"]), None)
        return result("invalid", "The ORCID link is not valid: " +
                      (failing["name"] if failing else "inconsistent"), report)

    claims = report.get("claims") or {}
    name = " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x) or claims.get("name")
    info = {"name": name or None, "verified_at": _utc_now(), "env": env_name}

    if publisher_address and report["address"].lower() != str(publisher_address).lower():
        return result("address-mismatch",
                      f"This ORCID link belongs to {report['address']}, not to the publishing address "
                      f"{publisher_address}.", report, **info)
    if env_name == "sandbox":
        return result("sandbox", "This is a SANDBOX ORCID link: for testing only, it is never shown "
                      "as an identity.", report, **info)
    if report["verdict"] == "VERIFIED":
        return result("verified", f"ORCID iD {report['orcid']} is linked to the publishing address and "
                      "confirmed with ORCID's published keys.", report, **info)
    return result("unconfirmed", f"ORCID iD {report['orcid']} is linked, but ORCID's key list could not be "
                  "reached: the link is consistent, not confirmed.", report, **info)


def fetch_json(url, data=None, headers=None, timeout=20) -> dict:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        snippet = exc.read(300).decode("utf-8", "replace")
        raise IdentityError(f"{url} answered HTTP {exc.code}: {snippet}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise IdentityError(f"Could not read {url}: {exc}") from exc


def _discover(base_url: str) -> dict:
    config = fetch_json(base_url.rstrip("/") + "/.well-known/openid-configuration")
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not config.get(key):
            raise IdentityError(f"The OpenID discovery document lacks '{key}'.")
    return config


DEFAULT_REDIRECT_HOST = "127.0.0.1"


def run_login_flow(*, client_id, client_secret, address, base_url, port=8765, timeout=300,
                   open_browser=True, on_authorize_url=None, redirect_host=None,
                   redirect_uri=None) -> dict:
    """Run the ORCID login and return {'id_token', 'jwks', 'salt', 'issuer'}.

    The answer is always received on this computer, at
    http://127.0.0.1:<port>/callback. What is announced to ORCID (and must be
    registered, character for character, in the ORCID client) is either:
      - that local address itself (default; accepted by the ORCID sandbox), with
        `redirect_host` = 127.0.0.1 or localhost, or
      - `redirect_uri`: an HTTPS relay page (portal/orcid-callback.html) that
        forwards the answer to the local address. Needed in production, where
        ORCID only accepts HTTPS redirect URIs. The relay page forwards to port
        8765, so keep the default port in that case.
    """
    if redirect_uri:
        redirect_uri = redirect_uri.strip()
        if not redirect_uri.lower().startswith("https://"):
            raise IdentityError(
                f"ORCID_REDIRECT_URI must be an https:// address (got {redirect_uri!r}). "
                "Leave it empty to use the local http://127.0.0.1 address (sandbox)."
            )
    else:
        host = (redirect_host or DEFAULT_REDIRECT_HOST).strip()
        if host not in ("127.0.0.1", "localhost"):
            raise IdentityError(
                f"ORCID_REDIRECT_HOST must be 127.0.0.1 or localhost (got {host!r}): the ORCID "
                "callback is only received on this computer."
            )
        redirect_uri = f"http://{host}:{port}/callback"
    config = _discover(base_url)
    state = secrets.token_urlsafe(24)
    salt = secrets.token_hex(16)

    outcome = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _reply(self, status, title, text):
            page = (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
                    f"<body style='font-family:sans-serif;max-width:40em;margin:3em auto'>"
                    f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p></body>")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                return self._reply(404, "Not found", "")
            params = urllib.parse.parse_qs(parsed.query)
            got_state = (params.get("state") or [""])[0]
            if not hmac.compare_digest(got_state, state):
                return self._reply(400, "Rejected", "Unexpected state.")
            if "error" in params:
                outcome["error"] = (params.get("error_description") or params["error"])[0]
                self._reply(200, "Link cancelled", "ORCID did not authorize the request. You can close this tab.")
            elif "code" in params:
                outcome["code"] = params["code"][0]
                self._reply(200, "ORCID login received", "You can close this tab and go back to OmicChain.")
            else:
                self._reply(400, "Rejected", "No authorization code.")

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise IdentityError(f"Cannot listen on port {port} for the ORCID callback: {exc}") from exc

    try:
        url = config["authorization_endpoint"] + "?" + urllib.parse.urlencode({
            "client_id": client_id, "response_type": "code", "scope": "openid",
            "redirect_uri": redirect_uri, "state": state,
            "nonce": derive_nonce(address, salt),
        })
        if on_authorize_url:
            on_authorize_url(url)
        else:
            print("Open this address to log in with ORCID:\n  " + url)
            if open_browser:
                webbrowser.open(url)

        server.timeout = 0.25
        deadline = time.monotonic() + timeout
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if "error" in outcome:
        raise IdentityError(f"ORCID authorization refused: {outcome['error']}")
    if "code" not in outcome:
        raise IdentityError(f"No answer from ORCID within {timeout} seconds.")

    tokens = fetch_json(config["token_endpoint"], data={
        "grant_type": "authorization_code", "code": outcome["code"],
        "redirect_uri": redirect_uri, "client_id": client_id, "client_secret": client_secret,
    })
    if not tokens.get("id_token"):
        raise IdentityError("ORCID's token response has no id_token (is the 'openid' scope enabled?).")
    return {
        "id_token": tokens["id_token"],
        "jwks": fetch_json(config["jwks_uri"]),
        "salt": salt,
        "issuer": config["issuer"],
    }


def _cmd_link(args) -> int:
    from dotenv import dotenv_values

    env = dotenv_values(args.env)
    client_id = env.get("ORCID_CLIENT_ID") or os.getenv("ORCID_CLIENT_ID")
    client_secret = env.get("ORCID_CLIENT_SECRET") or os.getenv("ORCID_CLIENT_SECRET")
    private_key = env.get("PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
    declared = env.get("ACCOUNT_ADDRESS") or os.getenv("ACCOUNT_ADDRESS")
    orcid_env = args.orcid_env or env.get("ORCID_ENV") or "production"
    if orcid_env not in ORCID_BASES:
        raise SystemExit(f"ORCID_ENV must be one of: {', '.join(ORCID_BASES)}")
    missing = [n for n, v in (("ORCID_CLIENT_ID", client_id), ("ORCID_CLIENT_SECRET", client_secret),
                              ("PRIVATE_KEY", private_key)) if not v]
    if missing:
        raise SystemExit("Missing in .env: " + ", ".join(missing))

    address = Account.from_key(private_key).address
    if declared and declared.lower() != address.lower():
        raise SystemExit(f"ACCOUNT_ADDRESS ({declared}) is not the address of PRIVATE_KEY ({address}).")

    base = ORCID_BASES[orcid_env]
    print(f"Linking {address} to an ORCID iD on {base}")
    try:
        flow = run_login_flow(client_id=client_id, client_secret=client_secret, address=address,
                              base_url=base, port=args.port, timeout=args.timeout,
                              redirect_host=env.get("ORCID_REDIRECT_HOST") or os.getenv("ORCID_REDIRECT_HOST"),
                              redirect_uri=env.get("ORCID_REDIRECT_URI") or os.getenv("ORCID_REDIRECT_URI"),
                              open_browser=not args.no_browser)
        link = create_link(flow["id_token"], flow["jwks"], private_key, flow["salt"],
                           issuer=flow["issuer"], audience=client_id)
    except IdentityError as exc:
        raise SystemExit(f"Error: {exc}")
    Path(args.out).write_text(json.dumps(link, indent=2) + "\n", encoding="utf-8")
    print(f"ORCID iD {link['orcid']} linked to {address}.\nWrote {args.out}")
    if orcid_env == "sandbox":
        print("Note: this is a SANDBOX identity; it proves nothing about a real ORCID account.")
    return 0


def _cmd_verify(args) -> int:
    link = json.loads(Path(args.link).read_text(encoding="utf-8"))
    orcid_env = args.orcid_env
    if orcid_env not in ORCID_BASES:
        raise SystemExit(f"--orcid-env must be one of: {', '.join(ORCID_BASES)}")
    base = ORCID_BASES[orcid_env]

    live = None
    if not args.offline:
        try:
            live = fetch_json(_discover(base)["jwks_uri"])
        except IdentityError as exc:
            print(f"Warning: could not fetch ORCID's key list ({exc}); continuing offline.")
    report = verify_link(link, live_jwks=live, trusted_issuers=[base])
    for c in report["checks"]:
        print(f"[{'OK' if c['ok'] else 'FAIL'}] {c['name']}" + (f"  ({c['detail']})" if c["detail"] and not c["ok"] else ""))
    print(f"\nORCID iD: {report['orcid']}\nAddress : {report['address']}\nVERDICT : {report['verdict']}")
    if report["verdict"].startswith("CONSISTENT"):
        print("The link is internally consistent, but ORCID's key list could not be checked: "
              "the ORCID claim is NOT proven.")
    return 0 if report["verdict"] == "VERIFIED" else 1


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="omicchain_identity",
                                     description="Link an ORCID iD to the publishing Ethereum address.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("link", help="log in with ORCID and write the link file")
    p.add_argument("--env", default=".env")
    p.add_argument("--out", default="orcid_link.json")
    p.add_argument("--orcid-env", choices=list(ORCID_BASES), help="default: ORCID_ENV from .env, else production")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--no-browser", action="store_true", help="print the address instead of opening it")
    p.set_defaults(func=_cmd_link)

    v = sub.add_parser("verify", help="verify a link file")
    v.add_argument("link")
    v.add_argument("--orcid-env", choices=list(ORCID_BASES), default="production")
    v.add_argument("--offline", action="store_true", help="do not fetch ORCID's key list")
    v.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(_main())
