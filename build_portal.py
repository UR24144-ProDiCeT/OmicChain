"""Build the public verification page (index.html) from the template and the contract settings."""

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import dotenv_values

HASH_PUBLISHED_TOPIC0 = "0x8de86119279015dfdbe2e01438d441bfa71717f8928f58f98883a29291439507"
EVENT_SIGNATURE = "HashPublished(address,bytes32,uint256)"

MARKER = re.compile(r"/\*CONFIG_START\*/.*?/\*CONFIG_END\*/", re.DOTALL)
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

NETWORKS = {
    "base": {
        "label": "Base Mainnet",
        "chainId": 8453,
        "rpc": "https://mainnet.base.org",
        "explorer": "https://basescan.org",
        "env_suffix": "MAINNET",
    },
    "base-sepolia": {
        "label": "Base Sepolia (testnet)",
        "chainId": 84532,
        "rpc": "https://sepolia.base.org",
        "explorer": "https://sepolia.basescan.org",
        "env_suffix": "TESTNET",
    },
}


def _selfcheck() -> None:
    """If web3 is available, make sure the fixed topic0 is the right one."""
    try:
        from web3 import Web3
    except ImportError:
        return
    computed = "0x" + Web3.keccak(text=EVENT_SIGNATURE).hex().removeprefix("0x")
    if computed != HASH_PUBLISHED_TOPIC0:
        raise SystemExit(
            f"Internal error: topic0 mismatch ({computed} != {HASH_PUBLISHED_TOPIC0})."
        )


def build_config(env: dict, rpc_overrides: dict) -> tuple:
    """Return (config, notes). Networks without a valid contract are left out."""
    networks, notes = {}, []
    for key, spec in NETWORKS.items():
        suffix = spec["env_suffix"]
        address = (env.get(f"CONTRACT_PROOF_ADDRESS_{suffix}") or "").strip()
        if not address:
            notes.append(f"{spec['label']}: CONTRACT_PROOF_ADDRESS_{suffix} not set -> network left out.")
            continue
        if not ADDRESS.match(address):
            raise SystemExit(f"CONTRACT_PROOF_ADDRESS_{suffix} is not a valid address: {address!r}")

        raw_block = (env.get(f"CONTRACT_PROOF_DEPLOY_BLOCK_{suffix}") or "").strip()
        if raw_block and not raw_block.isdigit():
            raise SystemExit(f"CONTRACT_PROOF_DEPLOY_BLOCK_{suffix} must be a number: {raw_block!r}")
        deploy_block = int(raw_block) if raw_block else 0
        if not raw_block:
            notes.append(
                f"{spec['label']}: CONTRACT_PROOF_DEPLOY_BLOCK_{suffix} not set -> searching by "
                "fingerprint alone will scan from block 0 (slow); the transaction ID path is unaffected."
            )

        networks[key] = {
            "label": spec["label"],
            "chainId": spec["chainId"],
            "rpc": rpc_overrides.get(key) or spec["rpc"],
            "explorer": spec["explorer"],
            "contract": address,
            "deployBlock": deploy_block,
        }
    return {"topic0": HASH_PUBLISHED_TOPIC0, "networks": networks}, notes


def build(template_path: Path, out_path: Path, env: dict, rpc_overrides: dict) -> tuple:
    _selfcheck()
    config, notes = build_config(env, rpc_overrides)
    if not config["networks"]:
        raise SystemExit(
            "No network configured: set CONTRACT_PROOF_ADDRESS_MAINNET and/or "
            "CONTRACT_PROOF_ADDRESS_TESTNET in your .env."
        )
    template = template_path.read_text(encoding="utf-8")
    if not MARKER.search(template):
        raise SystemExit(f"CONFIG markers not found in {template_path}.")
    payload = json.dumps(config, indent=2).replace("</", "<\\/")
    html = MARKER.sub(lambda _m: "/*CONFIG_START*/" + payload + "/*CONFIG_END*/", template, count=1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8", newline="\n")
    return config, notes


def find_template(explicit=None) -> Path:
    """Locate verify.template.html. Downloaded files often arrive flat, without
    their portal/ sub-folder, so both layouts are accepted."""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"Template not found: {path}")
        return path
    here = Path(__file__).resolve().parent
    candidates = [
        here / "portal" / "verify.template.html",
        here / "verify.template.html",
        Path.cwd() / "portal" / "verify.template.html",
        Path.cwd() / "verify.template.html",
    ]
    candidates = list(dict.fromkeys(candidates))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "verify.template.html not found. Looked in:\n  "
        + "\n  ".join(str(c) for c in candidates)
        + "\nPut it next to build_portal.py (or in a 'portal' folder next to it), "
        "or pass --template <path>."
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the OmicChain public verification page.")
    parser.add_argument("--env", default=".env", help="path of the .env file (default: .env)")
    parser.add_argument("--template", default=None,
                        help="path of verify.template.html (default: found next to this script)")
    parser.add_argument("--out", default="portal/index.html")
    parser.add_argument("--rpc-mainnet", help="RPC endpoint to use for Base Mainnet instead of the public one")
    parser.add_argument("--rpc-testnet", help="RPC endpoint to use for Base Sepolia instead of the public one")
    args = parser.parse_args(argv)

    env_path = Path(args.env)
    if not env_path.is_file():
        raise SystemExit(f"{env_path} not found.")
    env = dotenv_values(env_path)

    config, notes = build(
        find_template(args.template),
        Path(args.out),
        env,
        {"base": args.rpc_mainnet, "base-sepolia": args.rpc_testnet},
    )
    print(f"Wrote {args.out}")
    for key, net in config["networks"].items():
        print(f"  - {net['label']}: contract {net['contract']}, RPC {net['rpc']}")
    for note in notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
