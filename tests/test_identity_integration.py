"""Integration tests of the ORCID identity link: states, valid crate, certificate line.
Run:  python tests/test_identity_integration.py   (from the project folder)"""
import json, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT))
import mockorcid as m
import omicchain_identity as oid
import omicchain_core as occ
from certificate_utils import generate_certificate_pdf
from eth_account import Account

FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)

p = m.Provider(); wallet, other = Account.create(), Account.create(); ADDR = wallet.address
tmp = Path(tempfile.mkdtemp()); LINK = tmp / "orcid_link.json"

def make_link(path):
    flow = m.run_flow(p, ADDR)
    link = oid.create_link(flow["id_token"], flow["jwks"], wallet.key, flow["salt"], issuer=flow["issuer"], audience=p.client_id)
    path.write_text(json.dumps(link, indent=2)); return link

check("1. no file -> 'none'", oid.identity_status(tmp / "missing.json")["state"] == "none")
oid.ORCID_BASES["production"] = p.base; oid.ORCID_BASES["sandbox"] = "http://sandbox.invalid"
make_link(LINK)
st = oid.identity_status(LINK, publisher_address=ADDR)
check("2. valid link, key confirmed -> 'verified'", st["state"] == "verified" and st["orcid"] == m.ORCID_ID, st["message"])
check("2. name read from the verified token", st["name"] == "Josiah Carberry", st["name"])
check("2. address compared case-insensitively", oid.identity_status(LINK, publisher_address=ADDR.lower())["state"] == "verified")
check("3. other publishing address -> 'address-mismatch'", oid.identity_status(LINK, publisher_address=other.address)["state"] == "address-mismatch")
bad = json.loads(LINK.read_text()); bad["orcid"] = "0000-0001-5109-3700"; (tmp / "bad.json").write_text(json.dumps(bad))
st = oid.identity_status(tmp / "bad.json", publisher_address=ADDR)
check("4. modified link -> 'invalid'", st["state"] == "invalid" and "ORCID iD" in st["message"], st["message"])
(tmp / "garbage.json").write_text("{not json")
check("5. unreadable file -> 'invalid' without exception", oid.identity_status(tmp / "garbage.json")["state"] == "invalid")
alien = json.loads(LINK.read_text()); alien["issuer"] = "https://evil.example"; (tmp / "alien.json").write_text(json.dumps(alien))
check("6. unknown issuer -> 'invalid'", oid.identity_status(tmp / "alien.json")["state"] == "invalid")
evil_pem, evil_jwk = m.make_key("key1"); salt = "00" * 16
forged = oid.create_link(p.make_token(oid.derive_nonce(other.address, salt), pem=evil_pem), {"keys": [evil_jwk]}, other.key, salt, issuer=p.base, audience=p.client_id)
(tmp / "forged.json").write_text(json.dumps(forged))
check("7. forged link (attacker key) -> 'invalid' online", oid.identity_status(tmp / "forged.json", publisher_address=other.address)["state"] == "invalid")
check("7. ... offline never 'verified'", oid.identity_status(tmp / "forged.json", publisher_address=other.address, fetch=False)["state"] == "unconfirmed")
oid.ORCID_BASES["production"] = "http://prod.invalid"; oid.ORCID_BASES["sandbox"] = p.base
check("8. sandbox link -> 'sandbox' (never an identity)", oid.identity_status(LINK, publisher_address=ADDR)["state"] == "sandbox")
oid.ORCID_BASES["production"] = p.base; oid.ORCID_BASES["sandbox"] = "http://sandbox.invalid"
p.stop()
check("9. ORCID unreachable -> 'unconfirmed'", oid.identity_status(LINK, publisher_address=ADDR)["state"] == "unconfirmed")

proj = tmp / "proj"; R = proj / "results" / "run"; R.mkdir(parents=True)
for f, c in {"Snakefile": "rule all: pass", "run_deseq2.R": "# r", "de_core.R": "# core"}.items(): (proj / f).write_text(c)
for f, c in {"analysis_parameters.txt": "padj_threshold=0.05\nlfc_threshold=1.0\n", "data_provenance.txt": "x=1\n", "renv.lock": "{}", "top_genes.tsv": "Gene\tlog2FoldChange\nTP53\t3.5\n"}.items(): (R / f).write_text(c)
for f in ("heatmap.tiff", "volcano.tiff", "dotplot_go.tiff", "combined_figures.png", "report.pdf"): (R / f).write_bytes(b"fake")
(proj / "metadata.tsv").write_text("sample\tgroup\nA\tcontrol\n"); (proj / "expression_input.tsv").write_text("gene\tA\nG1\t5\n")
shutil.copy(ROOT / "omicchain.json", proj / "omicchain.json")
occ.certify(proj / "omicchain.json", str(R), proj)
graph = lambda: json.loads((R / "ro-crate-metadata.json").read_text())["@graph"]
def validate(level=None):
    cmd = [sys.executable, str(HERE / "validate_offline.py"), "-y", "validate"] + (["--requirement-severity", level] if level else []) + [str(R)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=150).stdout
    return "is a valid" in out and "not a valid" not in out

occ.add_orcid_identity(R, LINK, m.ORCID_ID, name="Josiah Carberry", as_author=True)
g = graph(); root = next(e for e in g if e["@id"] == "./"); person = next(e for e in g if e["@id"] == "https://orcid.org/" + m.ORCID_ID)
check("10. ORCID author (Person) linked to the root", person["@type"] == "Person" and person["name"] == "Josiah Carberry" and root["author"] == {"@id": person["@id"]})
check("11. orcid_link.json declared in the crate", "orcid_link.json" in [e["@id"] for e in g] and (R / "orcid_link.json").is_file())
occ.add_orcid_identity(R, LINK, m.ORCID_ID, name="Josiah Carberry", as_author=True)
ids = [e["@id"] for e in graph()]
check("12. idempotent: no duplicated identifier", len(ids) == len(set(ids)))
check("13. crate valid RO-Crate 1.1 (required)", validate())
occ.add_blockchain_proof(R, network="Base Mainnet", contract="0x" + "11" * 20, tx_hash="0x" + "ab" * 32, block_number=1,
                         timestamp="2026-10-01T00:00:00Z", publisher=ADDR, extra_files=[("omicchain_certificate.pdf", "Certificate (PDF)")])
check("14. identity then proof: crate still valid", validate())
check("15. the zip holds the ORCID link", "orcid_link.json" in zipfile.ZipFile(occ.make_crate_zip(R)).namelist())
occ.certify(proj / "omicchain.json", str(R), proj); occ.add_orcid_identity(R, LINK, m.ORCID_ID, as_author=False)
g = graph(); root = next(e for e in g if e["@id"] == "./")
check("16. not confirmed: file delivered, no author claimed", "author" not in root and "orcid_link.json" in [e["@id"] for e in g])
man = json.loads((proj / "omicchain.json").read_text()); man["crate"]["authors"] = [{"name": "Test Author", "id": "https://orcid.org/0000-0001-5109-3700"}]
(proj / "omicchain_a.json").write_text(json.dumps(man)); occ.certify(proj / "omicchain_a.json", str(R), proj)
occ.add_orcid_identity(R, LINK, m.ORCID_ID, name="Josiah Carberry", as_author=True)
root = next(e for e in graph() if e["@id"] == "./")
check("17. manifest author kept + verified ORCID added", len(root["author"]) == 2, str(root["author"]))
check("18. crate valid with two authors", validate())

occ.certify(proj / "omicchain.json", str(R), proj)
occ.add_orcid_identity(R, LINK, m.ORCID_ID, name="Josiah Carberry", as_author=True)
occ.add_orcid_identity(R, LINK, m.ORCID_ID, as_author=False)
g = graph(); root = next(e for e in g if e["@id"] == "./")
check("22. verified then not confirmed: the former author is removed from the crate",
      "author" not in root and "https://orcid.org/" + m.ORCID_ID not in [e["@id"] for e in g], str(root.get("author")))
occ.add_orcid_identity(R, LINK, m.ORCID_ID, name="Josiah Carberry", as_author=True)
occ.remove_orcid_identity(R)
g = graph(); root = next(e for e in g if e["@id"] == "./")
check("23. link removed: no author, file or reference left", "author" not in root and "orcid_link.json" not in [e["@id"] for e in g]
      and not (R / "orcid_link.json").exists() and {"@id": "orcid_link.json"} not in root["hasPart"])
check("24. crate valid after removal", validate())
occ.remove_orcid_identity(R)
check("25. removal with nothing to remove: no effect, no error", validate())
occ.certify(proj / "omicchain_a.json", str(R), proj)
occ.add_orcid_identity(R, LINK, "0000-0001-5109-3700", as_author=True)
g = graph(); same = [e for e in g if e["@id"] == "https://orcid.org/0000-0001-5109-3700"]
check("26. iD already declared in the manifest: a single entity, original name kept", len(same) == 1 and same[0]["name"] == "Test Author", str(same))
occ.remove_orcid_identity(R)
root = next(e for e in graph() if e["@id"] == "./")
check("27. the removal does not touch the author declared by hand", root["author"] == {"@id": "https://orcid.org/0000-0001-5109-3700"}, str(root.get("author")))
check("28. crate valid in this case too", validate())

def pdf_text(identity, name):
    out = tmp / f"{name}.pdf"
    generate_certificate_pdf(output_path=out, bundle_hash="ab" * 32, network="Base Mainnet", explorer="https://basescan.org",
                             tx_hash="0x" + "cd" * 32, block_number=1, blockchain_timestamp=1758888023, proof_contract="0x" + "11" * 20,
                             publisher_address=ADDR, components=[{"label": "Snakefile", "sha256": "ef" * 32}],
                             analysis_parameters={}, publisher_identity=identity)
    return " ".join(subprocess.run(["pdftotext", "-layout", str(out), "-"], capture_output=True, text=True).stdout.split()), out
t, _ = pdf_text(None, "none"); check("19. without identity: no 'Publisher identity' line", "Publisher identity" not in t)
t, pdf_ok = pdf_text({"orcid": m.ORCID_ID, "status": "verified", "verified_at": "2026-10-27T10:00:00Z"}, "ok")
check("20. verified: ORCID iD + confirmation date", m.ORCID_ID in t and "confirmed with ORCID" in t and "2026-10-27" in t, t[-300:])
t, _ = pdf_text({"orcid": m.ORCID_ID, "status": "unconfirmed", "verified_at": "2026-10-27T10:00:00Z"}, "unc")
check("21. not confirmed: explicit 'NOT confirmed' mention", "NOT confirmed" in t and "confirmed with ORCID's published" not in t, t[-300:])
shutil.copy(pdf_ok, tmp / "last_identity_certificate.pdf")
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
