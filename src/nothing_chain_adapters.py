"""Live blockchain observation adapters.

The adapters are read-only. They never sign or send transactions and never
accept client-supplied confirmation data as authoritative.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Mapping

from src.nothing_payments import (
    PaymentInvoice,
    PaymentObservation,
    chain_event_key,
)
from src.nothing_store import NotFoundError


class ChainAdapterError(RuntimeError):
    """Raised when an upstream blockchain data source cannot be trusted."""


class JsonRpcHttpClient:
    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        if not url.lower().startswith("https://"):
            raise ChainAdapterError("blockchain RPC endpoints must use HTTPS")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def call(self, method: str, params: list[Any]) -> Any:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ChainAdapterError(
                f"blockchain RPC request failed: {method}"
            ) from exc

        if not isinstance(body, dict):
            raise ChainAdapterError("blockchain RPC response is not an object")
        if body.get("error") is not None:
            raise ChainAdapterError(
                f"blockchain RPC returned an error for {method}"
            )
        return body.get("result")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hex_int(value: str | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ChainAdapterError("expected JSON-RPC hex quantity")
    return int(value, 16)


def _norm_evm_address(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 42:
        raise ChainAdapterError("invalid EVM address")
    return value.lower()


@dataclass(frozen=True)
class EvmJsonRpcAdapter:
    rpc_url: str
    network: str
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_rpc",
            JsonRpcHttpClient(
                self.rpc_url,
                timeout_seconds=self.timeout_seconds,
            ),
        )

    def verify(self, tx_hash: str, invoice: PaymentInvoice) -> PaymentObservation:
        if invoice.network != self.network:
            raise ChainAdapterError("invoice network does not match adapter")
        if invoice.asset_kind not in {"native", "erc20"}:
            raise ChainAdapterError("EVM adapter supports native and ERC-20 assets")

        tx = self._rpc.call("eth_getTransactionByHash", [tx_hash])
        receipt = self._rpc.call("eth_getTransactionReceipt", [tx_hash])
        if tx is None or receipt is None:
            raise NotFoundError(f"Ethereum transaction {tx_hash} is not yet mined")

        status = _hex_int(receipt.get("status"))
        block_number = _hex_int(receipt.get("blockNumber"))
        if status != 1 or block_number is None:
            return PaymentObservation(
                network=self.network,
                asset_code=invoice.asset_code,
                asset_kind=invoice.asset_kind,
                destination=invoice.destination,
                amount_atomic=0,
                chain_event_key=chain_event_key(
                    network=self.network,
                    tx_hash=tx_hash,
                    asset_kind=invoice.asset_kind,
                    event_index=0 if invoice.asset_kind == "erc20" else None,
                ),
                tx_hash=tx_hash,
                block_reference=receipt.get("blockHash"),
                confirmation_count=0,
                finality_status="confirmed" if block_number else "pending",
                success=False,
                observed_at=_utc_now(),
                source="evm-json-rpc",
                asset_contract=invoice.asset_contract,
            )

        head = _hex_int(self._rpc.call("eth_blockNumber", []))
        confirmations = (
            max(0, head - block_number + 1)
            if head is not None
            else 0
        )

        finalized_number: int | None = None
        try:
            finalized_block = self._rpc.call(
                "eth_getBlockByNumber",
                ["finalized", False],
            )
            finalized_number = _hex_int(
                finalized_block.get("number")
                if isinstance(finalized_block, dict)
                else None
            )
        except ChainAdapterError:
            finalized_number = None

        final = (
            finalized_number is not None
            and block_number <= finalized_number
        )
        destination = _norm_evm_address(invoice.destination)

        if invoice.asset_kind == "native":
            tx_to = tx.get("to")
            value = _hex_int(tx.get("value"))
            if tx_to is None or value is None:
                raise ChainAdapterError("native transaction is missing to/value")
            if _norm_evm_address(tx_to) != destination:
                amount = 0
            else:
                amount = value
            return PaymentObservation(
                network=self.network,
                asset_code=invoice.asset_code,
                asset_kind="native",
                destination=invoice.destination,
                amount_atomic=amount,
                chain_event_key=chain_event_key(
                    network=self.network,
                    tx_hash=tx_hash,
                    asset_kind="native",
                ),
                tx_hash=tx_hash,
                block_reference=receipt.get("blockHash"),
                confirmation_count=confirmations,
                finality_status="final" if final else "confirmed",
                success=amount > 0,
                observed_at=_utc_now(),
                source="evm-json-rpc",
            )

        transfer_topic = (
            "0xddf252ad1be2c89b69c2b068fc378daa"
            "952ba7f163c4a11628f55a7eaa9c3b"
        )
        expected_contract = _norm_evm_address(invoice.asset_contract or "")
        for log in receipt.get("logs", []):
            if _norm_evm_address(log.get("address", "")) != expected_contract:
                continue
            topics = log.get("topics", [])
            if len(topics) < 3 or topics[0].lower() != transfer_topic:
                continue
            encoded_to = topics[2]
            if not isinstance(encoded_to, str) or len(encoded_to) != 66:
                continue
            log_to = "0x" + encoded_to[-40:]
            if _norm_evm_address(log_to) != destination:
                continue
            data = log.get("data")
            if not isinstance(data, str) or not data.startswith("0x"):
                continue
            amount = int(data, 16)
            log_index = _hex_int(log.get("logIndex"))
            if log_index is None:
                raise ChainAdapterError("ERC-20 transfer log has no logIndex")
            return PaymentObservation(
                network=self.network,
                asset_code=invoice.asset_code,
                asset_kind="erc20",
                destination=invoice.destination,
                amount_atomic=amount,
                chain_event_key=chain_event_key(
                    network=self.network,
                    tx_hash=tx_hash,
                    asset_kind="erc20",
                    event_index=log_index,
                ),
                tx_hash=tx_hash,
                block_reference=receipt.get("blockHash"),
                confirmation_count=confirmations,
                finality_status="final" if final else "confirmed",
                success=True,
                observed_at=_utc_now(),
                source="evm-json-rpc",
                asset_contract=invoice.asset_contract,
            )

        raise NotFoundError(
            f"no matching ERC-20 transfer found in transaction {tx_hash}"
        )


@dataclass(frozen=True)
class XrplJsonRpcAdapter:
    rpc_url: str = "https://xrplcluster.com/"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_rpc",
            JsonRpcHttpClient(
                self.rpc_url,
                timeout_seconds=self.timeout_seconds,
            ),
        )

    def verify(
        self,
        tx_hash: str,
        invoice: PaymentInvoice,
    ) -> PaymentObservation:
        if invoice.network != "xrpl" or invoice.asset_code != "XRP":
            raise ChainAdapterError("XRPL adapter only verifies XRP invoices")
        result = self._rpc.call(
            "tx",
            [{"transaction": tx_hash, "binary": False, "api_version": 2}],
        )
        if not isinstance(result, dict):
            raise NotFoundError(f"XRPL transaction {tx_hash} not found")

        validated = bool(result.get("validated"))
        meta = result.get("meta") or {}
        tx_result = meta.get("TransactionResult")
        success = validated and tx_result == "tesSUCCESS"
        destination = result.get("Destination")
        tag = result.get("DestinationTag")
        delivered = meta.get("delivered_amount")
        if delivered is None:
            delivered = meta.get("DeliveredAmount")
        if isinstance(delivered, str):
            amount = int(delivered)
        else:
            amount = 0

        routing_reference = str(tag) if tag is not None else None
        finality = "final" if validated else "pending"
        return PaymentObservation(
            network="xrpl",
            asset_code="XRP",
            asset_kind="xrp",
            destination=str(destination or invoice.destination),
            amount_atomic=amount if destination == invoice.destination else 0,
            chain_event_key=chain_event_key(
                network="xrpl",
                tx_hash=tx_hash,
                asset_kind="xrp",
            ),
            tx_hash=tx_hash,
            block_reference=str(
                result.get("ledger_index")
                or result.get("inLedger")
                or ""
            ),
            confirmation_count=1 if validated else 0,
            finality_status=finality,
            success=success,
            observed_at=_utc_now(),
            source="xrpl-json-rpc",
            routing_mode=(
                "xrp_destination_tag"
                if routing_reference is not None
                else "manual_shared"
            ),
            routing_reference=routing_reference,
        )

    def discover_recent_payments(
        self,
        *,
        account: str,
        limit: int = 200,
    ) -> list[PaymentObservation]:
        result = self._rpc.call(
            "account_tx",
            [
                {
                    "account": account,
                    "ledger_index_min": -1,
                    "ledger_index_max": -1,
                    "binary": False,
                    "forward": False,
                    "limit": limit,
                    "api_version": 2,
                }
            ],
        )
        if not isinstance(result, dict):
            raise ChainAdapterError("XRPL account_tx response is invalid")

        observations: list[PaymentObservation] = []
        for item in result.get("transactions", []):
            if not isinstance(item, dict):
                continue
            if item.get("tx_json", {}).get("TransactionType") != "Payment":
                continue
            tx = item.get("tx_json", item)
            meta = item.get("meta", {}) or {}
            validated = bool(item.get("validated"))
            if not validated or meta.get("TransactionResult") != "tesSUCCESS":
                continue
            destination = tx.get("Destination")
            if destination != account:
                continue
            delivered = meta.get("delivered_amount")
            if delivered is None:
                delivered = meta.get("DeliveredAmount")
            if not isinstance(delivered, str):
                continue
            amount_value = delivered
            tag = tx.get("DestinationTag")
            observations.append(
                PaymentObservation(
                    network="xrpl",
                    asset_code="XRP",
                    asset_kind="xrp",
                    destination=destination,
                    amount_atomic=int(amount_value),
                    chain_event_key=chain_event_key(
                        network="xrpl",
                        tx_hash=str(tx.get("hash", "")),
                        asset_kind="xrp",
                    ),
                    tx_hash=str(tx.get("hash", "")),
                    block_reference=str(
                        item.get("ledger_index")
                        or tx.get("ledger_index")
                        or ""
                    ),
                    confirmation_count=1,
                    finality_status="final",
                    success=True,
                    observed_at=_utc_now(),
                    source="xrpl-json-rpc-account-tx",
                    routing_mode=(
                        "xrp_destination_tag"
                        if tag is not None
                        else "manual_shared"
                    ),
                    routing_reference=(
                        str(tag) if tag is not None else None
                    ),
                )
            )
        return observations


@dataclass(frozen=True)
class BitcoinCoreRpcAdapter:
    rpc_url: str
    network: str = "bitcoin"
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_rpc",
            JsonRpcHttpClient(
                self.rpc_url,
                timeout_seconds=self.timeout_seconds,
            ),
        )

    def verify(
        self,
        tx_hash: str,
        invoice: PaymentInvoice,
    ) -> PaymentObservation:
        if invoice.network != self.network:
            raise ChainAdapterError("Bitcoin network does not match adapter")
        if invoice.asset_kind != "btc_utxo":
            raise ChainAdapterError("Bitcoin adapter requires btc_utxo")

        tx = self._rpc.call(
            "getrawtransaction",
            [tx_hash, True],
        )
        if not isinstance(tx, dict):
            raise NotFoundError(f"Bitcoin transaction {tx_hash} not found")

        chain = self._rpc.call("getblockchaininfo", [])
        if not isinstance(chain, dict):
            raise ChainAdapterError("Bitcoin getblockchaininfo response is invalid")
        best_height = int(chain["blocks"])

        block_hash = tx.get("blockhash")
        confirmations = int(tx.get("confirmations", 0) or 0)
        if block_hash:
            block = self._rpc.call("getblock", [block_hash, 1])
            if isinstance(block, dict) and int(block.get("confirmations", 0)) == -1:
                finality_status = "orphaned"
            else:
                finality_status = (
                    "final" if confirmations >= 1 else "confirmed"
                )
        else:
            finality_status = "pending"

        for index, output in enumerate(tx.get("vout", [])):
            script = output.get("scriptPubKey", {}) or {}
            address = script.get("address")
            if address != invoice.destination:
                continue
            value_btc = Decimal(str(output.get("value", "0")))
            amount_atomic = int(
                value_btc * Decimal(100_000_000)
            )
            return PaymentObservation(
                network=self.network,
                asset_code="BTC",
                asset_kind="btc_utxo",
                destination=invoice.destination,
                amount_atomic=amount_atomic,
                chain_event_key=chain_event_key(
                    network=self.network,
                    tx_hash=tx_hash,
                    asset_kind="btc_utxo",
                    event_index=index,
                ),
                tx_hash=tx_hash,
                block_reference=block_hash,
                confirmation_count=max(0, confirmations),
                finality_status=finality_status,
                success=finality_status != "orphaned",
                observed_at=_utc_now(),
                source="bitcoin-core-rpc",
            )

        raise NotFoundError(
            f"no output to invoice destination in Bitcoin transaction {tx_hash}"
        )
