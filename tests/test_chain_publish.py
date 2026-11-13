"""Publication on a local EVM (eth-tester), with an RPC that lags like a load-balanced one.
Needs: pip install "eth-tester[py-evm]".  Usage: python tests/test_chain_publish.py

The test contract has the same event and function as the OmicChain proof contract:
  event HashPublished(address indexed publisher, bytes32 indexed hash, uint256 timestamp);
  function publishHash(bytes32 h) external { emit HashPublished(msg.sender, h, block.timestamp); }
(solc 0.8.20)."""
import sys, json, hashlib
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from web3 import Web3, EthereumTesterProvider
from web3.exceptions import BlockNotFound
import omicchain_chain as och

abi = json.loads('[{"anonymous":false,"inputs":[{"indexed":true,"internalType":"address","name":"publisher","type":"address"},{"indexed":true,"internalType":"bytes32","name":"hash","type":"bytes32"},{"indexed":false,"internalType":"uint256","name":"timestamp","type":"uint256"}],"name":"HashPublished","type":"event"},{"inputs":[{"internalType":"bytes32","name":"h","type":"bytes32"}],"name":"publishHash","outputs":[],"stateMutability":"nonpayable","type":"function"}]')
bytecode = "608060405234801561000f575f80fd5b506101648061001d5f395ff3fe608060405234801561000f575f80fd5b5060043610610029575f3560e01c80634d26695f1461002d575b5f80fd5b610047600480360381019061004291906100d2565b610049565b005b803373ffffffffffffffffffffffffffffffffffffffff167f8de86119279015dfdbe2e01438d441bfa71717f8928f58f98883a29291439507426040516100909190610115565b60405180910390a350565b5f80fd5b5f819050919050565b6100b18161009f565b81146100bb575f80fd5b50565b5f813590506100cc816100a8565b92915050565b5f602082840312156100e7576100e661009b565b5b5f6100f4848285016100be565b91505092915050565b5f819050919050565b61010f816100fd565b82525050565b5f6020820190506101285f830184610106565b9291505056fea2646970667358221220723dbeb2f82ab95011345fb5452071b6b2dff0a7821db4d9da651ba83e14ede764736f6c63430008140033"
app_abi = json.load(open(ROOT / "contract_abi_proof.json"))

prov = EthereumTesterProvider()
w3 = Web3(prov)
acct = w3.eth.accounts[0]
key = prov.ethereum_tester.backend.account_keys[0]
addr = w3.eth.contract(abi=abi, bytecode=bytecode).constructor().transact({"from": acct})
caddr = w3.eth.get_transaction_receipt(addr)["contractAddress"]
contract = w3.eth.contract(address=caddr, abi=app_abi)
fails = 0
def check(cond, msg):
    global fails
    print(("OK  " if cond else "FAIL") + " " + msg); fails += 0 if cond else 1

h = hashlib.sha256(b"x").hexdigest()

orig = w3.eth.get_block
calls = []
def broken(*a, **k):
    calls.append(a); raise BlockNotFound("Block with id: '0x331134d' not found")
w3.eth.get_block = broken
sent = []
r = och.publish_hash(w3, contract, acct, key, h, on_sent=sent.append, poll=0.01)
check(r["tx_hash"].startswith("0x") and len(r["tx_hash"]) == 66, f"tx with 0x: {r['tx_hash']}")
check(sent == [r["tx_hash"]], "tx recorded as soon as sent")
check(r["timestamp"] == orig(r["block"])["timestamp"], "timestamp from the event = block timestamp")
check(not calls, "no get_block call")
w3.eth.get_block = orig

n = w3.eth.get_transaction_count(acct)
check(n == 2, f"nonce after 1 deploy + 1 publication = {n}")

orig_r = w3.eth.get_transaction_receipt
state = {"n": 0}
from web3.exceptions import TransactionNotFound
def lagging(tx):
    state["n"] += 1
    if state["n"] < 3: raise TransactionNotFound("not yet")
    return orig_r(tx)
w3.eth.get_transaction_receipt = lagging
h2 = hashlib.sha256(b"y").hexdigest()
r2 = och.publish_hash(w3, contract, acct, key, h2, poll=0.01)
check(state["n"] >= 3 and r2["block"] > r["block"], "waits for lagging receipt")
w3.eth.get_transaction_receipt = orig_r

r3 = och.fetch_proof(w3, contract, r["tx_hash"][2:], h, poll=0.01)
check(r3 == r, "recovery of a sent transaction gives the same proof")

try:
    och.fetch_proof(w3, contract, r["tx_hash"], h2, poll=0.01); check(False, "wrong hash refused")
except och.PublicationError as e:
    check("did not publish this fingerprint" in str(e), "wrong hash refused")

w3.eth.get_transaction_receipt = lambda tx: (_ for _ in ()).throw(TransactionNotFound("x"))
try:
    och.fetch_proof(w3, contract, r["tx_hash"], h, timeout=0.05, poll=0.01); check(False, "timeout")
except och.PublicationError as e:
    check("instead of publishing again" in str(e), "timeout message")
w3.eth.get_transaction_receipt = orig_r

c2 = w3.eth.get_transaction_receipt(w3.eth.contract(abi=abi, bytecode=bytecode).constructor().transact({"from": acct}))["contractAddress"]
other = w3.eth.contract(address=c2, abi=app_abi)
tx = other.functions.publishHash(bytes.fromhex(h)).transact({"from": acct})
try:
    och.fetch_proof(w3, contract, tx, h, poll=0.01); check(False, "other contract refused")
except och.PublicationError:
    check(True, "other contract refused")

from hexbytes import HexBytes
check(och.as_tx_hash(HexBytes(b"\x01"*32)) == "0x" + "01"*32 and och.as_tx_hash("0xAB") == "0xab", "as_tx_hash")
n0 = w3.eth.get_transaction_count(acct)
e = och.publication_estimate(w3, contract, acct, h)
check(e["enough"] and e["cost_wei"] == e["gas"] * e["gas_price_wei"] and e["balance_wei"] == w3.eth.get_balance(acct)
      and w3.eth.get_transaction_count(acct) == n0, f"estimate: cost {e['cost_wei']} wei, nothing sent")
poor = Web3.to_checksum_address("0x" + "9" * 40)
check(och.publication_estimate(w3, contract, poor, h)["enough"] is False, "empty wallet: not enough")
class Dead:
    class eth:
        @property
        def gas_price(self): raise ConnectionError("refused")
    eth = eth()
try:
    och.publication_estimate(Dead(), contract, acct, h); ok = False
except och.PublicationError as exc:
    ok = "could not be reached" in str(exc)
check(ok, "unreachable network -> PublicationError")
print("FAILURES:", fails); sys.exit(1 if fails else 0)
