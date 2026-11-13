"""Publication of a fingerprint on-chain and retrieval of its proof."""

import time

from web3 import Web3
from web3.exceptions import BlockNotFound, TransactionNotFound
from web3.logs import DISCARD

__all__ = ["as_tx_hash", "publish_hash", "fetch_proof", "PublicationError"]


class PublicationError(Exception):
    """A publication problem worth showing to the user as is."""


def as_tx_hash(value) -> str:
    """0x-prefixed lowercase transaction hash from bytes, HexBytes or str."""
    text = value.hex() if hasattr(value, "hex") and not isinstance(value, str) else str(value)
    text = text.strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return "0x" + text


def _hash_bytes32(bundle_hash: str) -> bytes:
    cleaned = bundle_hash.strip().lower().removeprefix("0x")
    if len(cleaned) != 64:
        raise PublicationError("The fingerprint must be a 64-character SHA-256 digest.")
    return bytes.fromhex(cleaned)


def _wait_receipt(w3, tx_hash, timeout, poll):
    start = time.monotonic()
    while True:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is not None:
                return receipt
        except TransactionNotFound:
            pass
        if time.monotonic() - start > timeout:
            raise PublicationError(
                f"Transaction {tx_hash} sent, but not confirmed within {timeout} s. It may still be "
                "confirmed later: retrieve it instead of publishing again."
            )
        time.sleep(poll)


def _block_timestamp(w3, number, tries, poll):
    for attempt in range(tries):
        try:
            return w3.eth.get_block(number)["timestamp"]
        except BlockNotFound:
            if attempt == tries - 1:
                raise
            time.sleep(poll)


def fetch_proof(w3, contract, tx_hash, bundle_hash, *, timeout=300, poll=3, block_tries=6) -> dict:
    """Wait for a sent transaction and return {'tx_hash', 'block', 'timestamp'}.

    Checks that the transaction succeeded and that it published THIS
    fingerprint through THIS contract."""
    tx_hash = as_tx_hash(tx_hash)
    receipt = _wait_receipt(w3, tx_hash, timeout, poll)
    if receipt.get("status") != 1:
        raise PublicationError(f"Transaction {tx_hash} failed (reverted): nothing was published.")

    wanted = _hash_bytes32(bundle_hash)
    events = [
        ev for ev in contract.events.HashPublished().process_receipt(receipt, errors=DISCARD)
        if Web3.to_checksum_address(ev["address"]) == Web3.to_checksum_address(contract.address)
        and bytes(ev["args"]["hash"]) == wanted
    ]
    if not events:
        raise PublicationError(
            f"Transaction {tx_hash} succeeded but did not publish this fingerprint with the proof contract."
        )
    timestamp = events[0]["args"].get("timestamp")
    if timestamp is None:
        timestamp = _block_timestamp(w3, receipt["blockNumber"], block_tries, poll)
    return {"tx_hash": tx_hash, "block": receipt["blockNumber"], "timestamp": int(timestamp)}


def publication_estimate(w3, contract, account, bundle_hash) -> dict:
    """What publishing would cost, and whether the wallet can pay it:
    {'gas', 'gas_price_wei', 'cost_wei', 'balance_wei', 'cost_eth', 'balance_eth', 'enough'}.
    Read-only: nothing is sent. Raises PublicationError if the network cannot be reached."""
    try:
        account = Web3.to_checksum_address(account)
        fn = contract.functions.publishHash(_hash_bytes32(bundle_hash))
        try:
            gas = int(fn.estimate_gas({"from": account}) * 1.2)
        except Exception:
            gas = 100000
        gas_price = int(w3.eth.gas_price)
        balance = int(w3.eth.get_balance(account))
    except Exception as exc:
        raise PublicationError(f"the network could not be reached: {exc}") from exc
    cost = gas * gas_price
    return {"gas": gas, "gas_price_wei": gas_price, "cost_wei": cost, "balance_wei": balance,
            "cost_eth": cost / 1e18, "balance_eth": balance / 1e18, "enough": balance >= cost}


def publish_hash(w3, contract, account, private_key, bundle_hash, *, on_sent=None,
                 timeout=300, poll=3, block_tries=6) -> dict:
    """Send publishHash(bundle_hash) and return {'tx_hash', 'block', 'timestamp'}.

    `on_sent(tx_hash)` is called right after the transaction is sent, before
    waiting for it: record the hash there, so that the transaction can be
    retrieved if anything fails afterwards."""
    account = Web3.to_checksum_address(account)
    hash_bytes = _hash_bytes32(bundle_hash)
    fn = contract.functions.publishHash(hash_bytes)
    try:
        gas = int(fn.estimate_gas({"from": account}) * 1.2)
    except Exception:
        gas = 100000
    txn = fn.build_transaction({
        "from": account,
        "nonce": w3.eth.get_transaction_count(account),
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
    })
    signed = w3.eth.account.sign_transaction(txn, private_key=private_key)
    tx_hash = as_tx_hash(w3.eth.send_raw_transaction(signed.raw_transaction))
    if on_sent:
        on_sent(tx_hash)
    return fetch_proof(w3, contract, tx_hash, bundle_hash, timeout=timeout, poll=poll, block_tries=block_tries)
