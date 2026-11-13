# Using OmicChain's certification layer with another pipeline

`omicchain_core.py` knows nothing about DESeq2, Streamlit or Snakemake. It reads
a **manifest** (`omicchain.json`) listing the files that make up an analysis,
and produces:

| File | Content |
|---|---|
| `bundle_manifest.json` | SHA-256 of every certified component + the composite fingerprint |
| `bundle_hash.txt` | the composite fingerprint alone (what gets published on-chain) |
| `ro-crate-metadata.json` | an [RO-Crate 1.1](https://www.researchobject.org/ro-crate/) description of the analysis |

The composite fingerprint is `SHA-256( sha256_1 + sha256_2 + … + sha256_n )`,
the `sha256_i` being the lowercase hex digests of the components, concatenated
as text **in the order of the manifest**.

## Minimal manifest

```json
{
  "components": [
    {"id": "script",   "label": "Variant calling script", "path": "analyse.py",       "role": "code", "language": "python"},
    {"id": "versions", "label": "Tool versions",          "path": "out/versions.txt", "role": "environment"},
    {"id": "variants", "label": "Called variants",        "path": "out/variants.tsv", "role": "output"}
  ],
  "crate": {"name": "Variant calling run", "description": "Certified with OmicChain."}
}
```

- `components` — **certified**: their hashes make up the fingerprint. Each needs
  `id`, `label`, `path`; `role` and `language` are optional.
- `{results_dir}` in a path is replaced by the results folder given on the
  command line (see the DESeq2 manifest for an example).
- `extra_files` (optional) — listed in the crate, **not** part of the fingerprint
  (figures, reports…).
- `inputs` (optional) — raw input data. `"crate": {"include_inputs": ...}` says which are
  copied into the crate: `true` (all), `false` (none) or a list of file names. The DESeq2
  manifest uses `true`: with the sample metadata and the expression matrix inside, the crate
  alone is enough to re-run the analysis. Use `false` (or a list) if these data must not be
  shared with the crate: the reviewer is then asked for the missing files. Their hashes can still be certified through a
  provenance file (that is what the DESeq2 pipeline does).
- Files outside the results folder are copied into the folder (`code/`, `inputs/`)
  so that it is self-contained.

## Commands

```
python omicchain_core.py certify --manifest omicchain.json --results-dir out
python omicchain_core.py verify  out --expected <fingerprint read from the blockchain>
```

`verify` re-hashes every component found in the folder, recomputes the
fingerprint and compares it with the one declared and, if given, the expected
one. Exit code 0 = verified, 1 = not verified. It works on a crate you received
as a zip, once extracted.

## Optional crate metadata

```json
"crate": {
  "authors":   [{"name": "…", "id": "https://orcid.org/…", "affiliation": {"name": "…", "url": "https://…"}}],
  "publisher": {"name": "…", "url": "https://…"},
  "license":   {"id": "https://creativecommons.org/licenses/by/4.0/", "name": "CC BY 4.0"}
}
```

Nothing is filled in by default. Until `license` is set, the crate states that no
license has been chosen.

## From Python

```python
import omicchain_core as occ
result = occ.certify("omicchain.json", "out")
print(result["bundle_hash"])
```


## Optional components

A component may be marked `"optional": true`. It is part of the fingerprint only when its file
exists; when it is absent, it is left out entirely, so analyses without it keep exactly the
fingerprint they had before the component was added to the manifest. OmicChain uses this for
`data_origin` (`{results_dir}/data_origin.json`): present when the data were loaded from the
Data & reference tab, absent otherwise. The workflow is responsible for producing the file
(rule `data_origin` in the Snakefile).
