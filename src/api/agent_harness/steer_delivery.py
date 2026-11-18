"""Turn-local follow-up receipts, independent of transport and vendor events.

The runner calls this owner only on its event loop. An RPC acknowledgment
means accepted; a matching user-item echo means received. Neither proves the
model addressed the request. Repeated text is tracked per send, not as a set.

This is the **one** echo matcher per run: both the runner's own
``undelivered_steers`` notice and the chat receipt
(``api.agent_harness.steer_receipts``, via the ``steer_id`` each send carries)
read their delivery decision from here, so they cannot disagree. Initial turn
input is never offered to it — only echoes of steers.

Identical text is the hard case. An echo is evidence **scoped to the sends that
were outstanding when it arrived**: it can never confirm a send made later, and
several identical sends are confirmed only when each one can be paired with a
distinct echo that could have been its own. A send the transport then rejects
claims a held echo that could only have been its own — the agent read it, so
the client must not send it again — and the remaining echoes are re-checked
against the sends that are left. A send the vendor **explicitly refuses** is
different: it was definitely not read, so it claims nothing and the echo goes
to the sibling it must have belonged to (``acknowledge(..., refused=True)``).
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


@dataclass
class SteerReceipt:
    text: str
    steer_id: Optional[str] = None
    accepted: bool = False
    received: bool = False
    rejected: bool = False


class SteerDelivery:
    def __init__(self, confirmed: Optional[Callable[[List[SteerReceipt]], None]] = None) -> None:
        self._requests: List[SteerReceipt] = []
        self._item_ids: set[str] = set()
        # Held echoes: {"key": text, "scope": {id(receipt), …}} — the sends that
        # were outstanding when the echo arrived, i.e. all it can ever prove.
        self._held: List[Dict[str, object]] = []
        # Called with the receipts that just became confirmed, so the runner can
        # report each one's ``steer_id`` to the chat receipt owner.
        self.confirmed = confirmed

    def begin(self, text: str, steer_id: Optional[str] = None) -> SteerReceipt:
        receipt = SteerReceipt(text, steer_id)
        self._requests.append(receipt)
        return receipt

    def receive(self, text: str, item_id: Optional[str] = None) -> List[SteerReceipt]:
        """Vendor echo of user input → the sends it confirms, if any.

        An echo with no candidate (the turn's initial input, replayed history)
        is ignored, not banked against a later send.
        """
        if item_id:
            if item_id in self._item_ids:
                return []
            self._item_ids.add(item_id)
        key = (text or "").strip()
        candidates = self._candidates(key)
        if not candidates:
            return []
        self._held.append({"key": key, "scope": {id(r) for r in candidates}})
        return self._resolve(key)

    def acknowledge(self, receipt: SteerReceipt, *, succeeded: bool,
                    refused: bool = False) -> bool:
        """Record the transport's answer for one send. True when it is accepted.

        ``refused=True`` is the **vendor's own** answer that it did not take
        this send (a ``turn/steer`` error response, a closed input the CLI
        reported): the send was definitely not read, so it claims no held echo
        and the echoes in flight are re-checked against the sends that are
        left — an identical sibling may now own one unambiguously.

        ``succeeded=False`` with ``refused=False`` is an **ambiguous** failure
        (timeout, lost or late reply, transport error): acceptance is unknown,
        so an in-scope held echo is claimed as this send's own. Rejecting it
        instead would make the client enqueue input the agent already received.
        """
        if not succeeded and not refused and not receipt.received:
            self._claim_held(receipt)
        receipt.accepted = succeeded or receipt.received
        receipt.rejected = not receipt.accepted
        if receipt.rejected:
            # One candidate fewer: a held echo may now be unambiguous.
            self._resolve(receipt.text.strip())
        return receipt.accepted

    # ── matching ───────────────────────────────────────────────────────────
    def _candidates(self, key: str) -> List[SteerReceipt]:
        return [r for r in self._requests
                if not r.rejected and not r.received and r.text.strip() == key]

    def _resolve(self, key: str) -> List[SteerReceipt]:
        """Confirm the outstanding sends for ``key`` when every one has an echo."""
        candidates = self._candidates(key)
        if not candidates:
            return []
        echoes = [held for held in self._held if held["key"] == key]
        matched = _pair_each(candidates, echoes)
        if matched is None:
            return []
        for index in sorted(matched.values(), reverse=True):
            self._held.remove(echoes[index])
        for receipt in candidates:
            receipt.received = True
        self._notify(candidates)
        return candidates

    def _claim_held(self, receipt: SteerReceipt) -> None:
        """A held echo in whose scope this send lies is proof the agent read it."""
        key = receipt.text.strip()
        for held in self._held:
            if held["key"] == key and id(receipt) in held["scope"]:
                self._held.remove(held)
                receipt.received = True
                self._notify([receipt])
                return

    def _notify(self, receipts: List[SteerReceipt]) -> None:
        if self.confirmed is None or not receipts:
            return
        try:
            self.confirmed(list(receipts))
        except Exception:
            pass  # receipt bookkeeping must never interrupt a vendor reader

    # ── reads ──────────────────────────────────────────────────────────────
    @property
    def accepted_count(self) -> int:
        return sum(receipt.accepted for receipt in self._requests)

    @property
    def received_count(self) -> int:
        return sum(receipt.accepted and receipt.received for receipt in self._requests)

    @property
    def undelivered(self) -> List[str]:
        return [receipt.text for receipt in self._requests if receipt.accepted and not receipt.received]

    def accepted_totals(self) -> Dict[str, int]:
        """How many accepted sends carried each text (the *m* of "n of m")."""
        totals: Dict[str, int] = {}
        for receipt in self._requests:
            if receipt.accepted:
                key = receipt.text.strip()
                totals[key] = totals.get(key, 0) + 1
        return totals

    def closing_notice(self, agent_label: str) -> Optional[str]:
        """The turn's one notice for sends that ended without confirmation."""
        texts = self.undelivered
        if not texts:
            return None
        return undelivered_notice(agent_label, texts, totals=self.accepted_totals())


def _pair_each(candidates, echoes):
    """Pair every candidate with a distinct echo that could have been its own.

    Returns ``{candidate index: echo index}`` or ``None`` when some candidate
    cannot be paired — then no candidate is confirmed, because a receipt must
    never be attributed by a guess.
    """
    pairs: Dict[int, int] = {}

    def assign(ci, seen):
        for ei, held in enumerate(echoes):
            if ei in seen or id(candidates[ci]) not in held["scope"]:
                continue
            seen.add(ei)
            holder = next((c for c, e in pairs.items() if e == ei), None)
            if holder is None or assign(holder, seen):
                pairs[ci] = ei
                return True
        return False

    for index in range(len(candidates)):
        if not assign(index, set()):
            return None
    return pairs


def vendor_refused(error: object) -> bool:
    """True when ``error`` is the vendor's own refusal of a send, not an unknown.

    A JSON-RPC error object answering the request carries a ``code``: the
    server saw the send and declined it. Failures synthesized locally (the loop
    closed, stdin gone, no reply at all) carry only a message, and acceptance
    is then unknown — the conservative branch of ``acknowledge``.
    """
    return isinstance(error, dict) and error.get('code') is not None


def _identical_phrase(texts: List[str], totals: Optional[Dict[str, int]]) -> str:
    """"2 of 3 identical follow-ups", when every unconfirmed text was repeated."""
    if not totals:
        return ""
    counts: Dict[str, int] = {}
    for text in texts:
        key = str(text).strip()
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{n} of {totals[key]} identical follow-ups"
             for key, n in counts.items() if totals.get(key, n) > 1]
    return " and ".join(parts) if parts and len(parts) == len(counts) else ""


def undelivered_notice(agent_label: str, texts: List[str],
                       totals: Optional[Dict[str, int]] = None) -> str:
    """Say that delivery was not confirmed — never ask the user to send it again.

    A resend would let the agent act on the same request twice, which is what
    the receipt machinery exists to prevent; ambiguity can leave a message
    unconfirmed even when the agent did read it.
    """
    lines = "\n".join("- " + " ".join(str(text).split())[:200] for text in texts)
    plural = len(texts) > 1
    subject = _identical_phrase(texts, totals) or f"your follow-up{'s' if plural else ''}"
    them = "them" if plural else "it"
    return (
        f"{agent_label} ended this turn without confirming it read {subject}:\n{lines}\n"
        f"Delivery is not confirmed — {'they' if plural else 'it'} may or may not have been "
        f"read. Cuttle did not resend {them}, so the agent cannot act on the same request twice."
    )
