"""Certification core: composite fingerprint driven by a manifest (omicchain.json), RO-Crate, crate verification."""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

__all__ = [
    "ManifestError",
    "load_manifest",
    "certify_input_paths",
    "certify",
    "compute_bundle_hash",
    "sha256_file",
    "read_key_value_file",
    "load_bundle_manifest",
    "add_blockchain_proof",
    "add_orcid_identity",
    "remove_orcid_identity",
    "make_crate_zip",
    "verify_crate",
]

BUNDLE_SCHEMA = "omicchain.bundle/1"
CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
CRATE_SPEC = "https://w3id.org/ro/crate/1.1"

STAGING_DIRS = {"workflow": "code", "code": "code", "input": "inputs"}

LANGUAGES = {
    "snakemake": {
        "@id": "https://w3id.org/workflowhub/workflow-ro-crate#snakemake",
        "@type": "ComputerLanguage",
        "name": "Snakemake",
        "url": "https://snakemake.readthedocs.io",
    },
    "r": {
        "@id": "https://www.r-project.org/",
        "@type": "ComputerLanguage",
        "name": "R",
        "url": "https://www.r-project.org/",
    },
    "python": {
        "@id": "https://www.python.org/",
        "@type": "ComputerLanguage",
        "name": "Python",
        "url": "https://www.python.org/",
    },
}

ENCODING_FORMATS = {
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".r": "text/plain",
    ".smk": "text/plain",
    ".json": "application/json",
    ".lock": "application/json",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".png": "image/png",
    ".pdf": "application/pdf",
}


class ManifestError(ValueError):
    """Raised when omicchain.json is missing something or is inconsistent."""


def sha256_file(path, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's raw bytes, as a lowercase hex string."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_bundle_hash(component_hashes) -> str:
    """Composite fingerprint from an ordered list of hex digests (or of
    dicts having a 'sha256' key)."""
    digests = [
        c["sha256"] if isinstance(c, dict) else c for c in component_hashes
    ]
    return hashlib.sha256("".join(digests).encode()).hexdigest()


def read_key_value_file(path) -> dict:
    """Parse a simple key=value text file (e.g. analysis_parameters.txt)."""
    out = {}
    p = Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def load_manifest(path) -> dict:
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(m) -> None:
    if not isinstance(m, dict):
        raise ManifestError("The manifest must be a JSON object.")

    components = m.get("components")
    if not isinstance(components, list) or not components:
        raise ManifestError("'components' must be a non-empty list.")

    seen = set()
    for c in components:
        for key in ("id", "label", "path"):
            if not c.get(key):
                raise ManifestError(f"Component {c!r} lacks '{key}'.")
        if c["id"] in seen:
            raise ManifestError(f"Duplicate component id: {c['id']!r}.")
        seen.add(c["id"])

    for section in ("extra_files", "inputs"):
        for e in m.get(section, []):
            for key in ("label", "path"):
                if not e.get(key):
                    raise ManifestError(f"Entry {e!r} in '{section}' lacks '{key}'.")

    if sum(1 for c in components if c.get("role") == "workflow") > 1:
        raise ManifestError("At most one component may have role 'workflow'.")


def _fill(template: str, values: dict) -> str:
    """Replace {key} placeholders found in `values`; leave others as is."""
    return re.sub(
        r"\{([A-Za-z0-9_]+)\}",
        lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0),
        template,
    )


def _results_abs(results_dir, base_dir) -> Path:
    rd = Path(results_dir)
    return rd if rd.is_absolute() else Path(base_dir) / rd


def _resolve(template: str, base_dir, results_dir) -> Path:
    p = Path(template.replace("{results_dir}", Path(results_dir).as_posix()))
    return p if p.is_absolute() else Path(base_dir) / p


def _entries(manifest: dict, base_dir=None, results_dir=None) -> list:
    """Flat list of every file the crate may describe, certified first.
    An "optional" component is kept only if its file exists (with base_dir /
    results_dir given) -- absent, it is left out of the fingerprint entirely, so
    analyses that do not have it keep exactly the same fingerprint as before."""
    entries = []
    for c in manifest["components"]:
        if c.get("optional"):
            if base_dir is None or not _resolve(c["path"], base_dir, Path(results_dir)).is_file():
                continue
        entries.append({**c, "certified": True})
    for e in manifest.get("extra_files", []):
        entries.append({**e, "id": None, "certified": False})
    include = manifest.get("crate", {}).get("include_inputs", False)
    for e in manifest.get("inputs", []):
        if include is True or (isinstance(include, list) and Path(e["path"]).name in include):
            entries.append({**e, "id": None, "certified": False, "role": "input"})
    return entries


def certify_input_paths(manifest: dict, results_dir) -> list:
    """Paths (relative to the project root, POSIX style) that must exist
    before certification -- meant to be used as Snakemake rule inputs."""
    rd = Path(results_dir).as_posix()
    return [e["path"].replace("{results_dir}", rd) for e in _entries(manifest)]


def _crate_path(entry: dict, source: Path, results_abs: Path) -> str:
    try:
        return source.resolve().relative_to(results_abs.resolve()).as_posix()
    except ValueError:
        folder = STAGING_DIRS.get(entry.get("role"), "files")
        return f"{folder}/{source.name}"


def certify(manifest_path, results_dir, base_dir=".", stage=True, write_crate=True):
    """Compute hashes, bundle fingerprint, bundle_manifest.json,
    bundle_hash.txt and (optionally) the RO-Crate.

    Returns a dict: {"bundle_hash", "components", "bundle_manifest_path"}.
    """
    manifest = load_manifest(manifest_path)
    results_abs = _results_abs(results_dir, base_dir)
    results_abs.mkdir(parents=True, exist_ok=True)

    entries = _entries(manifest, base_dir, Path(results_dir))
    missing = []
    crate_paths = {}
    for e in entries:
        e["source"] = _resolve(e["path"], base_dir, Path(results_dir))
        if not e["source"].is_file():
            missing.append(str(e["source"]))
            continue
        e["crate_path"] = _crate_path(e, e["source"], results_abs)
        if e["crate_path"] in crate_paths:
            raise ManifestError(
                f"Two entries map to the same crate path {e['crate_path']!r}: "
                f"{crate_paths[e['crate_path']]} and {e['source']}."
            )
        crate_paths[e["crate_path"]] = str(e["source"])
    if missing:
        raise FileNotFoundError(
            "Cannot certify, these files are missing:\n  " + "\n  ".join(missing)
        )

    for e in entries:
        e["sha256"] = sha256_file(e["source"])
        e["size"] = e["source"].stat().st_size

    certified = [e for e in entries if e["certified"]]
    bundle_hash = compute_bundle_hash(certified)

    if stage:
        _stage_files(entries, results_abs)

    bundle_manifest = {
        "schema": BUNDLE_SCHEMA,
        "tool": {
            "name": "OmicChain",
            "version": os.getenv("OMICCHAIN_VERSION", "unversioned"),
        },
        "algorithm": "sha256",
        "combination": (
            "bundle_hash = SHA-256 of the concatenation of the components' "
            "SHA-256 hex digests, in the order listed below"
        ),
        "components": [
            {
                "id": e["id"],
                "label": e["label"],
                "role": e.get("role", "file"),
                "crate_path": e["crate_path"],
                "sha256": e["sha256"],
                "size": e["size"],
            }
            for e in certified
        ],
        "bundle_hash": bundle_hash,
    }
    bm_path = results_abs / "bundle_manifest.json"
    _write_json(bm_path, bundle_manifest)
    (results_abs / "bundle_hash.txt").write_text(bundle_hash)

    if write_crate:
        for fname, label in (
            ("bundle_manifest.json", "Bundle manifest (component hashes and composite fingerprint)"),
            ("bundle_hash.txt", "Composite fingerprint (SHA-256, plain text)"),
        ):
            fp = results_abs / fname
            entries.append({
                "id": None, "label": label, "role": "bundle", "certified": False,
                "source": fp, "crate_path": fname,
                "sha256": sha256_file(fp), "size": fp.stat().st_size,
            })
        parameters = {}
        pfile = manifest.get("crate", {}).get("parameters_file")
        if pfile:
            parameters = read_key_value_file(
                _resolve(pfile, base_dir, Path(results_dir))
            )
        crate = _build_crate(manifest, entries, bundle_hash, parameters)
        _write_crate(results_abs / "ro-crate-metadata.json", crate)

    return {
        "bundle_hash": bundle_hash,
        "components": bundle_manifest["components"],
        "bundle_manifest_path": str(bm_path),
    }


def _stage_files(entries, results_abs: Path) -> None:
    """Copy files that live outside the results folder into the crate
    layout (code/, inputs/, ...) so that the folder is self-contained."""
    for e in entries:
        dest = results_abs / e["crate_path"]
        try:
            if dest.resolve() == e["source"].resolve():
                continue
        except OSError:
            pass
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and sha256_file(dest) == e["sha256"]:
            continue
        shutil.copyfile(e["source"], dest)


def _write_json(path: Path, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _compact(crate: dict) -> dict:
    """RO-Crate 1.1 recommends single values rather than one-element arrays."""
    for entity in crate["@graph"]:
        for key, value in list(entity.items()):
            if key != "@id" and isinstance(value, list) and len(value) == 1:
                entity[key] = value[0]
    return crate


def _write_crate(path: Path, crate: dict) -> None:
    _write_json(path, _compact(crate))


def load_bundle_manifest(results_dir) -> dict:
    """Read results_dir/bundle_manifest.json (returns {} if absent)."""
    p = Path(results_dir) / "bundle_manifest.json"
    if not p.is_file():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _encoding_format(name: str):
    ext = Path(name).suffix.lower()
    return ENCODING_FORMATS.get(ext) or mimetypes.guess_type(name)[0]


def _file_entity(e: dict, position=None, total=None) -> dict:
    role = e.get("role")
    types = ["File"]
    if role in ("workflow", "code"):
        types.append("SoftwareSourceCode")
    if role == "workflow":
        types.append("ComputationalWorkflow")

    if e["certified"]:
        note = f"Part of the composite fingerprint (component {position} of {total})."
    else:
        note = "Included for reference; not part of the composite fingerprint."

    ent = {
        "@id": quote(e["crate_path"]),
        "@type": types if len(types) > 1 else "File",
        "name": e["label"],
        "description": f"{e['label']}. {note}",
        "contentSize": str(e["size"]),
        "identifier": f"sha256:{e['sha256']}",
    }
    fmt = e.get("encodingFormat") or _encoding_format(e["crate_path"])
    if not fmt and role in ("workflow", "code"):
        fmt = "text/plain"
    if fmt:
        ent["encodingFormat"] = fmt
    lang = LANGUAGES.get(e.get("language", ""))
    if lang:
        ent["programmingLanguage"] = {"@id": lang["@id"]}
    return ent


def _build_crate(manifest, entries, bundle_hash, parameters) -> dict:
    cfg = manifest.get("crate", {})
    certified = [e for e in entries if e["certified"]]
    total = len(certified)

    graph = []
    graph.append({
        "@id": "ro-crate-metadata.json",
        "@type": "CreativeWork",
        "conformsTo": {"@id": CRATE_SPEC},
        "about": {"@id": "./"},
    })

    name = _fill(
        cfg.get("name", "OmicChain certified analysis"), parameters
    )
    description = _fill(
        cfg.get("description", "Analysis certified with OmicChain."), parameters
    )
    description += (
        f" Composite fingerprint (SHA-256): {bundle_hash}. It is the SHA-256 "
        "of the concatenation of the SHA-256 digests of the certified "
        "components, in the order listed in bundle_manifest.json."
    )

    lic = cfg.get("license")
    if lic and lic.get("id"):
        license_ref = {"@id": lic["id"]}
        license_entity = {
            "@id": lic["id"],
            "@type": "CreativeWork",
            "name": lic.get("name", lic["id"]),
            "description": lic.get("description", f"License: {lic['id']}"),
        }
    else:
        license_ref = {"@id": "#license-unspecified"}
        license_entity = {
            "@id": "#license-unspecified",
            "@type": "CreativeWork",
            "name": "License not specified",
            "description": (
                "The authors have not chosen a license for this research "
                "object yet. Set 'crate.license' in omicchain.json before "
                "sharing it."
            ),
        }

    additional = [{"@id": "#bundle-fingerprint"}]
    param_entities = []
    for key, value in parameters.items():
        pid = "#param-" + re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        additional.append({"@id": pid})
        param_entities.append({
            "@id": pid,
            "@type": "PropertyValue",
            "name": key,
            "value": value,
        })

    parts = []
    file_entities = []
    languages = {}
    main_workflow = None
    for e in entries:
        position = (certified.index(e) + 1) if e["certified"] else None
        ent = _file_entity(e, position, total)
        file_entities.append(ent)
        parts.append({"@id": ent["@id"]})
        if e.get("role") == "workflow" and main_workflow is None:
            main_workflow = ent["@id"]
        lang = LANGUAGES.get(e.get("language", ""))
        if lang:
            languages[lang["@id"]] = lang

    root = {
        "@id": "./",
        "@type": "Dataset",
        "name": name,
        "description": description,
        "datePublished": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "license": license_ref,
        "additionalProperty": additional,
        "hasPart": parts,
    }
    if main_workflow:
        root["mainEntity"] = {"@id": main_workflow}

    people = []
    orgs = {}
    for i, a in enumerate(cfg.get("authors", []), start=1):
        if not a.get("name"):
            continue
        pid = a.get("id") or f"#author-{i}"
        person = {"@id": pid, "@type": "Person", "name": a["name"]}
        aff = a.get("affiliation")
        if aff:
            aff = {"name": aff} if isinstance(aff, str) else aff
            oid = aff.get("id") or "#org-" + re.sub(r"[^A-Za-z0-9]+", "-", aff["name"]).strip("-").lower()
            org = {"@id": oid, "@type": "Organization", "name": aff["name"]}
            if aff.get("url"):
                org["url"] = aff["url"]
            orgs[oid] = org
            person["affiliation"] = {"@id": oid}
        people.append(person)
    if people:
        root["author"] = [{"@id": p["@id"]} for p in people]

    publisher_entity = None
    pub = cfg.get("publisher")
    if pub and pub.get("name"):
        publisher_entity = {
            "@id": pub.get("id") or "#publisher",
            "@type": "Organization",
            "name": pub["name"],
        }
        if pub.get("url"):
            publisher_entity["url"] = pub["url"]
        root["publisher"] = {"@id": publisher_entity["@id"]}

    graph.append(root)
    graph.extend(people)
    graph.extend(orgs.values())
    if publisher_entity:
        graph.append(publisher_entity)
    graph.extend(file_entities)
    graph.extend(languages.values())
    graph.append(license_entity)
    graph.append({
        "@id": "#bundle-fingerprint",
        "@type": "PropertyValue",
        "name": "OmicChain composite fingerprint",
        "propertyID": "sha256",
        "value": bundle_hash,
    })
    graph.extend(param_entities)

    return {"@context": CRATE_CONTEXT, "@graph": graph}


def add_blockchain_proof(
    results_dir,
    network,
    contract,
    tx_hash,
    block_number,
    timestamp,
    publisher,
    tx_url=None,
    extra_files=None,
):
    """Record the on-chain publication in the crate, and add companion
    files (certificate PDF, identicon...) found in results_dir.

    extra_files: iterable of (filename, label) relative to results_dir.
    Idempotent: calling it again replaces the previous proof entity.
    """
    rd = Path(results_dir)
    crate_path = rd / "ro-crate-metadata.json"
    if not crate_path.is_file():
        raise FileNotFoundError(f"{crate_path} not found: run certification first.")
    with open(crate_path, encoding="utf-8") as f:
        crate = json.load(f)

    graph = [e for e in crate["@graph"] if e.get("@id") != "#blockchain-proof"]
    root = next(e for e in graph if e.get("@id") == "./")

    tx = tx_hash if str(tx_hash).startswith("0x") else f"0x{tx_hash}"
    proof = {
        "@id": "#blockchain-proof",
        "@type": "PublicationEvent",
        "name": f"On-chain publication of the composite fingerprint ({network})",
        "startDate": str(timestamp),
        "identifier": tx,
        "description": (
            f"Network: {network}. Contract: {contract}. Block: {block_number}. "
            f"Publisher: {publisher}. Transaction: {tx}."
        ),
    }
    if tx_url:
        proof["url"] = tx_url
    graph.append(proof)
    root["publication"] = {"@id": "#blockchain-proof"}

    existing_ids = {e.get("@id") for e in graph}
    parts = _as_list(root.get("hasPart"))
    root["hasPart"] = parts
    for filename, label in (extra_files or []):
        p = rd / filename
        if not p.is_file():
            continue
        fid = quote(Path(filename).as_posix())
        entity = {
            "@id": fid,
            "@type": "File",
            "name": label,
            "description": f"{label}. Included for reference; not part of the composite fingerprint.",
            "contentSize": str(p.stat().st_size),
            "identifier": f"sha256:{sha256_file(p)}",
        }
        fmt = _encoding_format(filename)
        if fmt:
            entity["encodingFormat"] = fmt
        graph = [e for e in graph if e.get("@id") != fid]
        graph.append(entity)
        if {"@id": fid} not in parts:
            parts.append({"@id": fid})

    crate["@graph"] = graph
    _write_crate(crate_path, crate)
    return str(crate_path)


IDENTITY_MARK = "ORCID iD confirmed by an OmicChain identity link (see orcid_link.json)."


def _strip_identity(graph, root):
    """Remove what a previous add_orcid_identity() put in the crate (and only that:
    an author declared by hand in the manifest is left alone)."""
    managed = {e["@id"] for e in graph if e.get("description") == IDENTITY_MARK}
    drop = managed | {"orcid_link.json"}
    kept = [e for e in graph if e.get("@id") not in drop]
    root["hasPart"] = [r for r in _as_list(root.get("hasPart")) if r.get("@id") not in drop]
    authors = [a for a in _as_list(root.get("author")) if a.get("@id") not in managed]
    if authors:
        root["author"] = authors
    else:
        root.pop("author", None)
    return kept


def remove_orcid_identity(results_dir):
    """Take the ORCID link out of the crate (no-op if it was never added)."""
    rd = Path(results_dir)
    crate_path = rd / "ro-crate-metadata.json"
    if not crate_path.is_file():
        return None
    with open(crate_path, encoding="utf-8") as f:
        crate = json.load(f)
    root = next(e for e in crate["@graph"] if e.get("@id") == "./")
    crate["@graph"] = _strip_identity(crate["@graph"], root)
    _write_crate(crate_path, crate)
    (rd / "orcid_link.json").unlink(missing_ok=True)
    return str(crate_path)


def add_orcid_identity(results_dir, link_path, orcid, name=None, as_author=True):
    """Put the ORCID link file in the crate and, if `as_author`, declare the
    ORCID iD as an author of the research object.

    The verification itself is done by omicchain_identity (this module has no
    dependency): call this only with the result of that check, and use
    as_author=False when the link is not confirmed with ORCID (the file is then
    shipped so that anyone can verify it, without asserting authorship).

    Calling it again replaces what it added before, so the crate always matches
    the latest check.
    """
    rd = Path(results_dir)
    crate_path = rd / "ro-crate-metadata.json"
    if not crate_path.is_file():
        raise FileNotFoundError(f"{crate_path} not found: run certification first.")
    with open(crate_path, encoding="utf-8") as f:
        crate = json.load(f)

    dest = rd / "orcid_link.json"
    if Path(link_path).resolve() != dest.resolve():
        shutil.copyfile(link_path, dest)

    root = next(e for e in crate["@graph"] if e.get("@id") == "./")
    graph = _strip_identity(crate["@graph"], root)
    graph.append({
        "@id": "orcid_link.json",
        "@type": "File",
        "name": "ORCID identity link",
        "description": (
            "Links an ORCID iD to the Ethereum address that published the fingerprint: an "
            "ORCID-signed token plus a wallet signature. Verify it with "
            "`python omicchain_identity.py verify orcid_link.json`. Included for reference; "
            "not part of the composite fingerprint."
        ),
        "contentSize": str(dest.stat().st_size),
        "encodingFormat": "application/json",
        "identifier": f"sha256:{sha256_file(dest)}",
    })
    root["hasPart"] = _as_list(root.get("hasPart")) + [{"@id": "orcid_link.json"}]

    if as_author:
        pid = f"https://orcid.org/{orcid}"
        if not any(e.get("@id") == pid for e in graph):
            graph.append({"@id": pid, "@type": "Person", "name": name or orcid,
                          "description": IDENTITY_MARK})
        authors = _as_list(root.get("author"))
        if {"@id": pid} not in authors:
            authors.append({"@id": pid})
        root["author"] = authors

    crate["@graph"] = graph
    _write_crate(crate_path, crate)
    return str(crate_path)


def make_crate_zip(results_dir, zip_path=None) -> Path:
    """Zip the crate: ro-crate-metadata.json at the root plus every file
    the metadata declares in hasPart (and nothing else)."""
    rd = Path(results_dir)
    crate_path = rd / "ro-crate-metadata.json"
    with open(crate_path, encoding="utf-8") as f:
        crate = json.load(f)
    root = next(e for e in crate["@graph"] if e.get("@id") == "./")

    zip_path = Path(zip_path) if zip_path else rd / "omicchain-crate.zip"
    names = ["ro-crate-metadata.json"]
    for part in _as_list(root.get("hasPart")):
        rel = unquote(part["@id"])
        if (rd / rel).is_file():
            names.append(rel)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in names:
            z.write(rd / rel, arcname=rel)
    return zip_path


def verify_crate(crate_dir, expected_bundle_hash=None) -> dict:
    """Re-hash the certified components found in crate_dir and compare
    with bundle_manifest.json (and, optionally, with a fingerprint obtained
    elsewhere, e.g. read from the blockchain).

    Returns {"ok": bool, "bundle_hash_recomputed": str, "bundle_hash_declared":
    str, "components": [{id, label, expected, actual, ok}], "matches_expected":
    bool|None}.
    """
    d = Path(crate_dir)
    bm = load_bundle_manifest(d)
    if not bm:
        raise FileNotFoundError(f"No bundle_manifest.json in {d}")

    report = []
    actual_hashes = []
    for c in bm["components"]:
        p = d / c["crate_path"]
        actual = sha256_file(p) if p.is_file() else None
        report.append({
            "id": c["id"],
            "label": c["label"],
            "expected": c["sha256"],
            "actual": actual,
            "ok": actual == c["sha256"],
        })
        actual_hashes.append(actual or "")

    recomputed = compute_bundle_hash(actual_hashes)
    declared = bm["bundle_hash"]
    ok = all(r["ok"] for r in report) and recomputed == declared
    return {
        "ok": ok,
        "bundle_hash_recomputed": recomputed,
        "bundle_hash_declared": declared,
        "components": report,
        "matches_expected": (
            None if expected_bundle_hash is None
            else recomputed == expected_bundle_hash.strip().lower()
        ),
    }


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="omicchain_core",
        description="OmicChain generic certification layer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("certify", help="hash components and write the bundle + RO-Crate")
    c.add_argument("--manifest", default="omicchain.json")
    c.add_argument("--results-dir", required=True)
    c.add_argument("--base-dir", default=".")

    v = sub.add_parser("verify", help="re-verify a crate directory")
    v.add_argument("crate_dir")
    v.add_argument("--expected", help="fingerprint to compare with (e.g. from the blockchain)")

    args = parser.parse_args(argv)

    if args.command == "certify":
        res = certify(args.manifest, args.results_dir, args.base_dir)
        print(f"bundle_hash = {res['bundle_hash']}")
        return 0

    report = verify_crate(args.crate_dir, args.expected)
    for r in report["components"]:
        print(f"[{'OK' if r['ok'] else 'FAIL'}] {r['label']}")
    print(f"declared  : {report['bundle_hash_declared']}")
    print(f"recomputed: {report['bundle_hash_recomputed']}")
    if report["matches_expected"] is not None:
        print(f"matches expected fingerprint: {report['matches_expected']}")
    print("RESULT:", "VERIFIED" if report["ok"] and report["matches_expected"] is not False else "NOT VERIFIED")
    return 0 if report["ok"] and report["matches_expected"] is not False else 1


if __name__ == "__main__":
    sys.exit(_main())


def write_data_origin(origin_path, expression_path, dest_path) -> dict:
    """Copy the provenance of the loaded data (upstream_provenance.json from the
    FASTQ workflow, or matrix_provenance.json) into the results, after checking
    that the expression table analysed is exactly the one this provenance
    describes. Deterministic: same inputs, same bytes."""
    origin = json.loads(Path(origin_path).read_text(encoding="utf-8"))
    expected = (origin.get("outputs") or {}).get("expression_input.tsv")
    actual = sha256_file(expression_path)
    if expected != actual:
        raise ValueError(
            "expression_input.tsv is not the expression table described by data_origin.json "
            f"(expected SHA-256 {expected}, found {actual}). Load the data again from the "
            "Data & reference tab.")
    origin["certified_link"] = {"expression_input_sha256": actual, "matches_data_origin": True}
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_text(json.dumps(origin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return origin
