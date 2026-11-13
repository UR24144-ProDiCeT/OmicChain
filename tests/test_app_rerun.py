"""The 'Re-run and compare' button of the application (AppTest, mock Rscript).  Usage: python tests/test_app_rerun.py"""
import os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
PROJ = Path(tempfile.mkdtemp()) / "proj"
subprocess.run([sys.executable, str(HERE / "make_test_project.py"), str(PROJ)], check=True, capture_output=True)
from eth_account import Account
w = Account.create()
os.environ.update({"INFURA_URL_MAINNET": "http://127.0.0.1:1", "INFURA_URL_TESTNET": "http://127.0.0.1:1",
                   "CONTRACT_PROOF_ADDRESS_MAINNET": "0x" + "11" * 20, "CONTRACT_PROOF_ADDRESS_TESTNET": "0x" + "22" * 20,
                   "ACCOUNT_ADDRESS": w.address, "PRIVATE_KEY": w.key.hex(),
                   "PATH": str(PROJ / "mockbin") + os.pathsep + os.environ["PATH"]})
for k in ("MOCK_NOISE", "MOCK_DESEQ2_VERSION", "MOCK_FAIL"): os.environ.pop(k, None)
os.chdir(PROJ); sys.path.insert(0, str(PROJ))
r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.05", "lfc_threshold=1.0"], capture_output=True, text=True)
assert r.returncode == 0, r.stdout[-1500:]
import omicchain_core as occ
R = PROJ / "results" / "padj_0.05_lfc_1.0"; BUNDLE = (R / "bundle_hash.txt").read_text()
ZIP = occ.make_crate_zip(R).read_bytes(); EXPR = (PROJ / "expression_input.tsv").read_bytes(); META = (PROJ / "metadata.tsv").read_bytes()

from streamlit.testing.v1 import AppTest
FAILED = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond: FAILED.append(name)
def texts(els): return " | ".join(e.value for e in els)

import io, zipfile
def strip_inputs(data):
    """Crate without the input data (older crates, or "include_inputs": false)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if not item.filename.startswith("inputs/"):
                zout.writestr(item, zin.read(item.filename))
    return out.getvalue()
OLD_ZIP = strip_inputs(ZIP)
labels = lambda at: [u.label for u in at.get("file_uploader")]

def run_case(expr=EXPR, expected=BUNDLE, crate=None):
    """crate=None: full crate, nothing else to provide; otherwise older crate + data."""
    at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240); at.run()
    {u.label: u for u in at.get("file_uploader")}["RO-Crate (.zip)"].upload("omicchain-crate.zip", crate or ZIP, "application/zip")
    at.run()
    if crate:
        ups = {u.label: u for u in at.get("file_uploader")}
        ups["Expression matrix"].upload("expression_input.tsv", expr, "text/tab-separated-values")
        ups["Sample metadata of the analysis (metadata.tsv)"].upload("metadata.tsv", META, "text/tab-separated-values")
        at.run()
    next(t for t in at.text_input if "Fingerprint published" in t.label).set_value(expected).run()
    next(b for b in at.button if "Re-run and compare" in b.label).click().run()
    return at

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240); at.run()
btn = next(b for b in at.button if "Re-run and compare" in b.label)
check("0. without a crate, the button is disabled", btn.disabled and not at.exception, texts(at.exception))

at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240); at.run()
{u.label: u for u in at.get("file_uploader")}["RO-Crate (.zip)"].upload("omicchain-crate.zip", ZIP, "application/zip"); at.run()
check("1. full crate: no other file asked (neither metadata nor expression table)",
      "Expression matrix" not in labels(at) and not any("metadata" in l.lower() for l in labels(at))
      and any("nothing else to provide" in c.value for c in at.caption), labels(at))
at = run_case()
check("1. crate alone + published fingerprint -> REPRODUCED message", any("REPRODUCED — the re-run gives exactly" in s.value for s in at.success), texts(at.success) + texts(at.error))
check("1. table per component: all 'same'", at.dataframe and all(v == "same" for v in at.dataframe[-1].value["Re-run"]), "")
check("1. both fingerprints shown and equal", sum(BUNDLE in m.value for m in at.markdown) == 2, "")
check("1. no exception", not at.exception, texts(at.exception))
at.run()
check("2. the result stays shown after a page rerun", any("REPRODUCED" in s.value for s in at.success))

at = run_case(crate=OLD_ZIP)
check("3. older crate (no data): metadata and table asked, then REPRODUCED", any("REPRODUCED — the re-run gives exactly" in s.value for s in at.success),
      texts(at.success) + texts(at.error))
at = run_case(expr=EXPR.replace(b"80", b"81"), crate=OLD_ZIP)
check("3. wrong data -> stop reported, no re-run", any("not the one used" in e.value for e in at.error)
      and not any("Re-run" in d.value.columns for d in at.dataframe), texts(at.error))

os.environ["MOCK_NOISE"] = "1e-9"; at = run_case(); os.environ.pop("MOCK_NOISE")
check("4. numeric difference -> NOT REPRODUCED + figures shown", any("NOT REPRODUCED" in e.value for e in at.error)
      and any("information only" in i.value and "log2FoldChange" in i.value for i in at.info), texts(at.error) + texts(at.info))
check("4. ... component 'Top DE genes table' marked DIFFERENT", "DIFFERENT" in list(at.dataframe[-1].value["Re-run"]))
check("5. the original results of the project were not modified", (R / "bundle_hash.txt").read_text() == BUNDLE)
check("6. known code: mention without blocking, no network warning", any("Code of the crate" in c.value for c in at.caption)
      and not any("reachable from other computers" in e.value for e in at.error), texts(at.caption) + texts(at.error))
snake_ok = (PROJ / "Snakefile").read_text()
(PROJ / "Snakefile").write_text(snake_ok + "\n# line added by a stranger\n")
r = subprocess.run([sys.executable, "-m", "snakemake", "-j2", "--config", "padj_threshold=0.01", "lfc_threshold=1.0"], capture_output=True, text=True)
assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
ALIEN = occ.make_crate_zip(PROJ / "results" / "padj_0.01_lfc_1.0").read_bytes()
(PROJ / "Snakefile").write_text(snake_ok)
at = AppTest.from_file(str(PROJ / "OmicChain.py"), default_timeout=240); at.run()
ups = {u.label: u for u in at.get("file_uploader")}
ups["RO-Crate (.zip)"].upload("alien.zip", ALIEN, "application/zip")
at.run()
go = lambda: next(b for b in at.button if "Re-run and compare" in b.label)
check("7. unknown code: alert naming the file, button disabled", any("not a known OmicChain version" in e.value and "Snakefile" in e.value for e in at.error)
      and go().disabled and not at.exception, texts(at.error) + texts(at.exception))
check("7. ... the code can be read in the page", any("line added by a stranger" in c.value for c in at.code), [c.value[-80:] for c in at.code])
next(c for c in at.checkbox if "I have read this code" in c.label).check().run()
check("8. explicit agreement -> button enabled", not go().disabled)
go().click().run()
check("8. ... re-run: REPRODUCED, with the warning about the code", any("REPRODUCED" in x.value for x in at.success)
      and any("not a known OmicChain version" in w.value for w in at.warning), texts(at.success) + texts(at.error) + texts(at.warning))
print("\n" + (f"{len(FAILED)} CHECK(S) FAILED: {FAILED}" if FAILED else "ALL CHECKS PASS"))
sys.exit(1 if FAILED else 0)
