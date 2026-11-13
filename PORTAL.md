# OmicChain public verification page

A single static HTML file (`portal/index.html`) that lets anyone check, without
installing anything, that a fingerprint was published to the OmicChain proof
contract — at which block, at what time, and by which address. It is what the
QR code of the certificate opens.

It has no server, no dependency (no CDN, no library) and holds no key: it only
makes read-only calls to a public JSON-RPC node.

## Build it

```
python build_portal.py
```

This reads your `.env` and writes `portal/index.html`. Only these **public**
values are used; `INFURA_URL_*` and `PRIVATE_KEY` are never read:

| `.env` variable | Role |
|---|---|
| `CONTRACT_PROOF_ADDRESS_MAINNET` / `_TESTNET` | contract to query (a network without an address is left out) |
| `CONTRACT_PROOF_DEPLOY_BLOCK_MAINNET` / `_TESTNET` | recommended: bounds the search by fingerprint alone |
| `OMICCHAIN_VERIFY_URL` | where you host the page; makes the certificate QR code point to it |

Edit `portal/verify.template.html`, never `index.html` (it is overwritten at each build).

## Publish it

Upload `index.html` to any static host (GitHub Pages, your institution's web
space…). Then set `OMICCHAIN_VERIFY_URL` in `.env` to its address and generate
the certificate again: the QR code now carries
`<url>?h=<fingerprint>&tx=<transaction>&net=<network>` and the page verifies
as soon as it opens. Without `OMICCHAIN_VERIFY_URL`, the QR code keeps pointing
to the transaction on the block explorer.

## Design choices

- The contract address is **fixed in the page**, not read from the link: otherwise a
  crafted link could point to a fake contract that "confirms" anything. Only
  events emitted by the configured contract are accepted.
- With a transaction ID the check is a single request. With the fingerprint alone,
  the page searches from the deployment block and, if the node refuses a wide
  search, falls back to smaller block ranges (with a progress bar and a cancel button).
- The RPC endpoint can be changed under *Advanced*. The page trusts whatever
  that endpoint answers; the explorer links let you cross-check.

## Limits

- It checks that the fingerprint was **published**, not the analysis files. To check
  those, use the RO-Crate and `python omicchain_core.py verify <folder> --expected <fingerprint>`.
- An address is not an identity: the page cannot tell you who the publisher is.
- It only understands the current contract (`bytes32` indexed fingerprint). Certificates
  issued with the previous contract (string argument) are not supported.
- Public Base endpoints are documented as rate-limited and not meant for production.
