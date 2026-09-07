"""EVM blockchain layer: a `Notary` contract that stores tamper-evident records.

Two backends:
  * ``local``  - an in-process EVM (eth-tester + py-evm). Zero setup, but the
                 chain lives and dies with the process. Verification is
                 demonstrated in-process immediately after recording.
  * ``rpc``    - any reachable JSON-RPC chain (ganache/hardhat node, or a public
                 testnet such as Base Sepolia / Polygon Amoy). Durable records.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_CONTRACT_SOURCE = (Path(__file__).resolve().parent.parent / "contracts" / "Notary.sol").read_text(encoding="utf-8")
_SOLC_VERSION = "0.8.24"

_LOCAL_CHAIN_ID = 1337
_GAS = 4_000_000


# --------------------------------------------------------------------------
# solc
# --------------------------------------------------------------------------
def compile_notary() -> tuple[str, str]:
    solcx = _load_solcx()
    solcx.set_solc_version(_SOLC_VERSION)
    compiled = solcx.compile_source(_CONTRACT_SOURCE, output_values=["abi", "bin"])
    key = next(iter(compiled))
    return compiled[key]["abi"], compiled[key]["bin"]


def _load_solcx():
    import solcx

    try:
        solcx.set_solc_version(_SOLC_VERSION, silent=True)
    except Exception:  # noqa: BLE001
        solcx.install_solc(_SOLC_VERSION, show_progress=False)
        solcx.set_solc_version(_SOLC_VERSION, silent=True)
    return solcx


def _bytes32(hexx: str) -> bytes:
    return bytes.fromhex(hexx[2:] if hexx.startswith("0x") else hexx)


def _display_fp(data: Any) -> str:
    """bytes32 chain value -> hex string."""
    s = bytes(data).hex() if not isinstance(data, str) else data
    return "0x" + s if not s.startswith("0x") else s


# --------------------------------------------------------------------------
# local backend
# --------------------------------------------------------------------------
class LocalChain:
    def __init__(self, provider=None):
        self.provider = provider  # injected for verification reuse within a run
        self.w3 = None
        self.chain_id = _LOCAL_CHAIN_ID

    def connect(self):
        from eth_tester import EthereumTester, PyEVMBackend
        from web3 import Web3, EthereumTesterProvider

        tester = EthereumTester(PyEVMBackend())
        provider = EthereumTesterProvider(tester)
        self.w3 = Web3(provider)
        self.provider = provider
        return self

    def deploy(self, deployer: str, abi: str, bin_hex: str) -> str:
        contract = self.w3.eth.contract(abi=abi, bytecode=bin_hex)
        tx_hash = contract.constructor().transact({"from": deployer, "gas": _GAS})
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return rcpt["contractAddress"]

    def publish(self, contract_addr: str, abi: str, key: str, source: str, url: str, fingerprint: str) -> dict:
        c = self.w3.eth.contract(address=contract_addr, abi=abi)
        tx = c.functions.record(source, url, _bytes32(fingerprint)).transact(
            {"from": key, "gas": _GAS}
        )
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx)
        record_id = c.functions.count().call() - 1
        return {
            "record_id": record_id,
            "tx_hash": rcpt["transactionHash"].hex(),
            "block_number": rcpt["blockNumber"],
        }

    def verify(self, contract_addr: str, abi: str, record_id: int, fingerprint: str) -> dict[str, Any]:
        c = self.w3.eth.contract(address=contract_addr, abi=abi)
        stored = c.functions.get(record_id).call()
        stored_fp = _display_fp(stored[0])
        return {
            "on_chain_fingerprint": stored_fp,
            "on_chain_source": stored[1],
            "on_chain_url": stored[2],
            "on_chain_timestamp": stored[3],
            "match": stored_fp.lower() == str(fingerprint).lower(),
        }


# --------------------------------------------------------------------------
# JSON-RPC backend (ganache/hardhat or public testnet)
# --------------------------------------------------------------------------
class RpcChain:
    def __init__(self, rpc_url: str, private_key: str | None = None, chain_id: int | None = None):
        self.rpc_url = rpc_url
        self.private_key = private_key
        self.expected_chain_id = chain_id
        self.w3 = None
        self.chain_id = None

    def connect(self, retries: int = 30, delay: float = 2.0):
        from web3 import Web3
        import time

        # retry loop: in docker-compose the chain container may still be booting
        self.w3 = None
        for _ in range(max(1, retries)):
            try:
                self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 60}))
                if self.w3.is_connected():
                    break
            except Exception:  # noqa: BLE001
                self.w3 = None
            time.sleep(delay)
        if self.w3 is None or not self.w3.is_connected():
            raise RuntimeError(f"cannot reach JSON-RPC at {self.rpc_url} after {retries} tries")
        self.chain_id = self.w3.eth.chain_id
        if self.expected_chain_id and self.chain_id != self.expected_chain_id:
            raise RuntimeError(f"chain_id mismatch: node={self.chain_id}, expected={self.expected_chain_id}")
        return self

    def deploy(self, deployer: str, abi: str, bin_hex: str) -> str:
        c = self.w3.eth.contract(abi=abi, bytecode=bin_hex)
        tx = c.constructor().build_transaction({"from": deployer, "gas": _GAS})
        tx = self._complete_tx(tx)
        rcpt = self._send(tx)
        return rcpt["contractAddress"]

    def publish(self, contract_addr: str, abi: str, key: str, source: str, url: str, fingerprint: str) -> dict:
        c = self.w3.eth.contract(address=contract_addr, abi=abi)
        tx = c.functions.record(source, url, _bytes32(fingerprint)).build_transaction(
            {"from": key, "gas": _GAS}
        )
        tx = self._complete_tx(tx)
        rcpt = self._send(tx)
        record_id = c.functions.count().call() - 1
        return {
            "record_id": record_id,
            "tx_hash": rcpt["transactionHash"].hex() if rcpt else "",
            "block_number": int(rcpt["blockNumber"]) if rcpt else 0,
        }

    def _complete_tx(self, tx: dict) -> dict:
        latest = self.w3.eth.get_block("latest")
        if latest.get("baseFeePerGas") is not None:
            tx.setdefault("maxPriorityFeePerGas", self.w3.to_wei(2, "gwei"))
            tx.setdefault("maxFeePerGas", int(latest["baseFeePerGas"] * 2) + self.w3.to_wei(2, "gwei"))
        else:
            tx.setdefault("gasPrice", self.w3.eth.gas_price)
        tx.setdefault("nonce", self.w3.eth.get_transaction_count(tx["from"]))
        tx.setdefault("chainId", self.chain_id)
        return tx

    def _send(self, tx: dict) -> Any:
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

    def verify(self, contract_addr: str, abi: str, record_id: int, fingerprint: str) -> dict[str, Any]:
        c = self.w3.eth.contract(address=contract_addr, abi=abi)
        stored = c.functions.get(record_id).call()
        stored_fp = _display_fp(stored[0])
        return {
            "on_chain_fingerprint": stored_fp,
            "on_chain_source": stored[1],
            "on_chain_url": stored[2],
            "on_chain_timestamp": stored[3],
            "match": stored_fp.lower() == str(fingerprint).lower(),
        }


# --------------------------------------------------------------------------
# factory / CLI helpers
# --------------------------------------------------------------------------
def connect_chain(backend: str, rpc_url: str | None = None, private_key: str | None = None,
                  chain_id: int | None = None) -> LocalChain | RpcChain:
    if backend == "local":
        return LocalChain().connect()
    if backend in ("rpc", "jsonrpc"):
        return RpcChain(rpc_url=rpc_url, private_key=private_key, chain_id=chain_id).connect()
    raise ValueError(f"unknown chain backend: {backend}")


def default_from(chain: LocalChain | RpcChain) -> str:
    if isinstance(chain, LocalChain):
        return chain.w3.eth.accounts[0]
    acct = chain.w3.eth.account.from_key(chain.private_key)
    return acct.address


def chain_env() -> dict[str, Any]:
    """Resolve chain settings from environment (used by the CLI)."""
    return {
        "backend": os.environ.get("FACEFINDER_CHAIN", "local"),
        "rpc_url": os.environ.get("RPC_URL") or os.environ.get("FACEFINDER_RPC_URL"),
        "private_key": os.environ.get("PRIVATE_KEY") or os.environ.get("FACEFINDER_PRIVATE_KEY"),
        "chain_id": (int(os.environ["CHAIN_ID"]) if os.environ.get("CHAIN_ID") else None),
    }