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


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_evm_address(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 42:
        raise ChainAdapterError("invalid EVM address")
    return value.lower()


@dataclass(frozen=True)
class EvmJsonRpcAdapter:
    rpc_url: str
    network: str
    expected_chain_id: int = 1
    timeout_seconds: float = 10.0
    required_confirmations: int = 6

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
        chain_id = _hex_int(self._rpc.call("eth_chainId", []))
        if chain_id != self.expected_chain_id:
            raise ChainAdapterError("EVM endpoint chain ID does not match configuration")
        if invoice.asset_kind not in {"native", "erc20"}:
            raise ChainAdapterError("EVM adapter supports native and ERC-20 assets")

        tx = self._rpc.call("eth_getTransactionByHash", [tx_hash])
        receipt = self._rpc.call("eth_getTransactionReceipt", [tx_hash])
        if tx is None or receipt is None:
            raise NotFoundError(f"Ethereum transaction {tx_hash} is not yet mined")

        status = _hex_int(receipt.get("status"))
        block_number = _hex_int(receipt.get("blockNumber"))
        if status != 1:
            raise ChainAdapterError(
                f"EVM transaction {tx_hash} did not succeed"
            )
        if block_number is None:
            raise ChainAdapterError(
                f"EVM transaction {tx_hash} has no mined block"
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
                routing_mode="unique_destination",
                routing_reference=None,
            )

        transfer_topic = (
            "0xddf252ad1be2c89b69c2b068fc378daa"
            "952ba7f163c4a11628f55a7eaa9c3b"
        )
        expected_contract = _norm_evm_address(invoice.asset_contract or "")
        matching_logs: list[tuple[dict[str, Any], int, str]] = []
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
            matching_logs.append((log, amount, str(log_index)))

        if len(matching_logs) > 1:
            raise ChainAdapterError(
                "multiple matching ERC-20 transfers found in one transaction; "
                "automatic single-observation verification would be ambiguous"
            )
        if len(matching_logs) == 1:
            _log, amount, log_index_text = matching_logs[0]
            log_index = int(log_index_text)
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
                routing_mode="unique_destination",
                routing_reference=None,
            )

        raise NotFoundError(
            f"no matching ERC-20 transfer found in transaction {tx_hash}"
        )



@dataclass(frozen=True)
class XrplDiscoveryResult:
    observations: tuple[PaymentObservation, ...]
    newest_tx_hash: str | None
    checkpoint_tx_hash: str | None
    checkpoint_ledger_index: int | None
    reached_checkpoint: bool


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
        if not isinstance(delivered, str) or not delivered.isdigit():
            raise ChainAdapterError(
                "XRPL delivered_amount is unavailable or invalid"
            )
        amount = int(delivered)

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
        return list(
            self.discover_recent_payments_with_checkpoint(
                account=account,
                limit=limit,
                stop_after_tx_hash=None,
                max_pages=1,
            ).observations
        )

    def discover_recent_payments_with_checkpoint(
        self,
        *,
        account: str,
        limit: int = 200,
        stop_after_tx_hash: str | None = None,
        max_pages: int = 20,
    ) -> "XrplDiscoveryResult":
        if not account.strip():
            raise ChainAdapterError("XRPL account is required")
        if limit < 1:
            raise ChainAdapterError("XRPL account_tx limit must be positive")
        if max_pages < 1:
            raise ChainAdapterError("XRPL max_pages must be positive")

        observations: list[PaymentObservation] = []
        marker: Any | None = None
        newest_tx_hash: str | None = None
        checkpoint_tx_hash: str | None = None
        checkpoint_ledger_index: int | None = None
        reached_checkpoint = False
        scan_completed = False

        for _ in range(max_pages):
            params: dict[str, Any] = {
                "account": account,
                "ledger_index_min": -1,
                "ledger_index_max": -1,
                "binary": False,
                "forward": False,
                "limit": limit,
                "api_version": 2,
            }
            if marker is not None:
                params["marker"] = marker

            result = self._rpc.call("account_tx", [params])
            if not isinstance(result, dict):
                raise ChainAdapterError("XRPL account_tx response is invalid")

            transactions = result.get("transactions", [])
            if not isinstance(transactions, list):
                raise ChainAdapterError("XRPL account_tx transactions is invalid")

            for item in transactions:
                if not isinstance(item, dict):
                    continue
                tx = item.get("tx_json", item)
                if not isinstance(tx, dict):
                    continue

                tx_hash = str(tx.get("hash", "")).strip()
                if tx_hash and newest_tx_hash is None:
                    newest_tx_hash = tx_hash

                if stop_after_tx_hash and tx_hash == stop_after_tx_hash:
                    checkpoint_tx_hash = tx_hash
                    checkpoint_ledger_index = _as_int(
                        item.get("ledger_index")
                        or tx.get("ledger_index")
                    )
                    reached_checkpoint = True
                    scan_completed = True
                    break

                validated = bool(item.get("validated"))
                if not validated:
                    continue
                if tx.get("TransactionType") != "Payment":
                    continue

                meta = item.get("meta") or {}
                if not isinstance(meta, dict):
                    continue
                if meta.get("TransactionResult") != "tesSUCCESS":
                    continue
                if tx.get("Destination") != account:
                    continue

                delivered = meta.get("delivered_amount")
                if delivered is None:
                    delivered = meta.get("DeliveredAmount")
                if not isinstance(delivered, str) or not delivered.isdigit():
                    continue

                tag = tx.get("DestinationTag")
                observations.append(
                    PaymentObservation(
                        network="xrpl",
                        asset_code="XRP",
                        asset_kind="xrp",
                        destination=account,
                        amount_atomic=int(delivered),
                        chain_event_key=chain_event_key(
                            network="xrpl",
                            tx_hash=tx_hash,
                            asset_kind="xrp",
                        ),
                        tx_hash=tx_hash,
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

            if reached_checkpoint:
                break

            page_marker = result.get("marker")
            if not page_marker:
                if transactions:
                    last = transactions[-1]
                    if isinstance(last, dict):
                        tx = last.get("tx_json", last)
                        if isinstance(tx, dict):
                            checkpoint_tx_hash = str(tx.get("hash", "")).strip() or None
                            checkpoint_ledger_index = _as_int(
                                last.get("ledger_index")
                                or tx.get("ledger_index")
                            )
                reached_checkpoint = stop_after_tx_hash is None
                scan_completed = True
                break
            marker = page_marker

        if not scan_completed:
            raise ChainAdapterError(
                "XRPL discovery page limit reached before a durable checkpoint boundary"
            )

        if stop_after_tx_hash and not reached_checkpoint:
            raise ChainAdapterError(
                "XRPL worker checkpoint was not found within the configured page window"
            )

        if checkpoint_tx_hash is None and transactions:
            last = transactions[-1]
            if isinstance(last, dict):
                tx = last.get("tx_json", last)
                if isinstance(tx, dict):
                    checkpoint_tx_hash = str(tx.get("hash", "")).strip() or None
                    checkpoint_ledger_index = _as_int(
                        last.get("ledger_index")
                        or tx.get("ledger_index")
                    )

        return XrplDiscoveryResult(
            observations=tuple(observations),
            newest_tx_hash=newest_tx_hash,
            checkpoint_tx_hash=checkpoint_tx_hash,
            checkpoint_ledger_index=checkpoint_ledger_index,
            reached_checkpoint=reached_checkpoint,
        )


@dataclass(frozen=True)
class BitcoinCoreRpcAdapter:
    rpc_url: str
    network: str = "bitcoin"
    expected_chain: str = "main"
    timeout_seconds: float = 10.0
    required_confirmations: int = 6

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
        if not self.expected_chain.strip():
            raise ChainAdapterError("Bitcoin expected_chain is required")

        tx = self._rpc.call(
            "getrawtransaction",
            [tx_hash, True],
        )
        if not isinstance(tx, dict):
            raise NotFoundError(f"Bitcoin transaction {tx_hash} not found")

        chain = self._rpc.call("getblockchaininfo", [])
        if not isinstance(chain, dict):
            raise ChainAdapterError("Bitcoin getblockchaininfo response is invalid")
        if chain.get("chain") != self.expected_chain:
            raise ChainAdapterError(
                "Bitcoin endpoint chain does not match configured network"
            )
        if self.required_confirmations < 1:
            raise ChainAdapterError("Bitcoin required_confirmations must be at least 1")

        block_hash = tx.get("blockhash")
        confirmations = int(tx.get("confirmations", 0) or 0)
        if block_hash:
            block = self._rpc.call("getblock", [block_hash, 1])
            if isinstance(block, dict) and int(block.get("confirmations", 0)) == -1:
                finality_status = "orphaned"
            else:
                finality_status = (
                    "final"
                    if confirmations >= self.required_confirmations
                    else "confirmed"
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
                routing_mode="unique_destination",
                routing_reference=None,
            )

        raise NotFoundError(
            f"no output to invoice destination in Bitcoin transaction {tx_hash}"
        )
