"""Checks on names coming from outside (accessions, GEO / ENA answers, file names, crate content) before they are used in paths or commands. Each *_problem function returns None when the name is fine, otherwise the reason."""

import re

ACCESSION = re.compile(r"(GSE\d{1,9}|[SED]RP\d{1,9}|PRJ(?:NA|EB|DB|E|D|N)[A-Z]?\d{1,9})\Z")
RUN_ACCESSION = re.compile(r"[SED]RR\d{1,12}\Z")
FORBIDDEN = set('/\\:*?"<>|,;') | {chr(i) for i in range(32)} | {chr(127)}
RESERVED_WINDOWS = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
MAX_ID = 150


def accession_problem(text):
    if not ACCESSION.match(str(text or "")):
        return "expected a GEO series (GSE...) or an SRA / ENA study (SRP..., ERP..., DRP..., PRJNA..., PRJEB...)"
    return None


def id_problem(text):
    """None if `text` can be used as a file name and as a list item, otherwise the reason."""
    if not isinstance(text, str) or not text:
        return "empty name"
    if text != text.strip():
        return "starts or ends with a space"
    if len(text) > MAX_ID:
        return f"longer than {MAX_ID} characters"
    bad = sorted({c for c in text if c in FORBIDDEN})
    if bad:
        shown = " ".join("tab" if c == "\t" else "newline" if c in "\r\n" else repr(c)[1:-1] if ord(c) >= 32 else "control character"
                         for c in bad)
        return f"contains {shown} (not allowed: / \\ : * ? \" < > | , ; and control characters)"
    if text[0] in ".-":
        return "starts with '.' or '-'"
    if text.endswith("."):
        return "ends with '.'"
    if text.split(".")[0].lower() in RESERVED_WINDOWS:
        return "is a reserved file name on Windows"
    return None


def check_ids(names, what):
    """Messages "<what> '<name>': <reason>" for the refused names."""
    out = []
    for n in names:
        why = id_problem(n)
        if why:
            out.append(f"{what} {str(n)[:60]!r}: {why}")
    return out


def relpath_problem(path):
    """None if `path` is a relative path that stays inside its folder, otherwise the reason."""
    if not isinstance(path, str) or not path:
        return "empty path"
    if "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return "absolute path or backslash"
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "empty, '.' or '..' component"
    for p in parts:
        if any(ord(c) < 32 for c in p) or any(c in p for c in ':*?"<>|'):
            return "forbidden character"
    return None


def clean_value(text):
    """Annotation value without tabs, line breaks or other control characters."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).strip()
