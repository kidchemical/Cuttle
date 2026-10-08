"""Turn-local follow-up receipts, independent of transport and vendor events.

The runner calls this owner only on its event loop. An RPC acknowledgment
means accepted; a matching user-item echo means received. Neither proves the
model addressed the request. Repeated text is tracked per send, not as a set.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SteerReceipt:
    text: str
    accepted: bool = False
    received: bool = False
    rejected: bool = False


class SteerDelivery:
    def __init__(self) -> None:
        self._requests: List[SteerReceipt] = []
        self._item_ids: set[str] = set()

    def begin(self, text: str) -> SteerReceipt:
        receipt = SteerReceipt(text)
        self._requests.append(receipt)
        return receipt

    def receive(self, text: str, item_id: Optional[str]) -> bool:
        if item_id:
            if item_id in self._item_ids:
                return False
            self._item_ids.add(item_id)
        for receipt in self._requests:
            if not receipt.rejected and not receipt.received and receipt.text.strip() == text.strip():
                receipt.received = True
                return True
        return False

    def acknowledge(self, receipt: SteerReceipt, *, succeeded: bool) -> bool:
        # An echoed request survives a lost/late RPC reply. Rejecting it would
        # cause the client to enqueue input the running agent already received.
        receipt.accepted = succeeded or receipt.received
        receipt.rejected = not receipt.accepted
        return receipt.accepted

    @property
    def accepted_count(self) -> int:
        return sum(receipt.accepted for receipt in self._requests)

    @property
    def received_count(self) -> int:
        return sum(receipt.accepted and receipt.received for receipt in self._requests)

    @property
    def undelivered(self) -> List[str]:
        return [receipt.text for receipt in self._requests if receipt.accepted and not receipt.received]


def undelivered_notice(agent_label: str, texts: List[str]) -> str:
    lines = "\n".join("- " + text.partition("\n")[0].strip()[:200] for text in texts)
    return (
        f"{agent_label} ended this turn without confirming it read your follow-up"
        f"{'s' if len(texts) > 1 else ''}:\n{lines}\nSend it again to continue."
    )
