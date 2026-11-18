"""Turn-local follow-up receipts, independent of transport and vendor events.

The runner calls this owner only on its event loop. An RPC acknowledgment
means accepted; a matching user-item echo means received. Neither proves the
model addressed the request. Repeated text is tracked per send, not as a set.

This is the **one** echo matcher per run: both the runner's own
``undelivered_steers`` notice and the chat receipt
(``api.agent_harness.steer_receipts``, via the ``steer_id`` each send carries)
read their delivery decision from here, so they cannot disagree. Initial turn
input is never offered to it — only echoes of steers.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class SteerReceipt:
    text: str
    steer_id: Optional[str] = None
    accepted: bool = False
    received: bool = False
    rejected: bool = False


class SteerDelivery:
    def __init__(self) -> None:
        self._requests: List[SteerReceipt] = []
        self._item_ids: set[str] = set()
        self._held: Dict[str, int] = {}

    def begin(self, text: str, steer_id: Optional[str] = None) -> SteerReceipt:
        receipt = SteerReceipt(text, steer_id)
        self._requests.append(receipt)
        return receipt

    def receive(self, text: str, item_id: Optional[str] = None) -> List[SteerReceipt]:
        """Vendor echo of user input → the sends it confirms, if any.

        Identical concurrent sends are confirmed only once as many echoes have
        arrived as there are candidates: an echo that could belong to either
        send confirms neither, because a receipt must never be attributed by a
        guess. An echo with no candidate (the turn's initial input, replayed
        history) is ignored, not banked against a later send.
        """
        if item_id:
            if item_id in self._item_ids:
                return []
            self._item_ids.add(item_id)
        key = (text or "").strip()
        candidates = [r for r in self._requests
                      if not r.rejected and not r.received and r.text.strip() == key]
        if not candidates:
            return []
        held = self._held.get(key, 0) + 1
        if len(candidates) > held:
            self._held[key] = held
            return []
        self._held[key] = 0
        for receipt in candidates:
            receipt.received = True
        return candidates

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
