# Retained Stop activity review

The Stop UI added in CH-000925 keeps the working bubble, failed agent badge,
query-log link, and “⏹ Stopped generating.” notice. That behavior is preserved.
The working bubble is local activity; it does not become an assistant history
row and disappears on reload. The system notice remains durable.

The original patch excluded stopped activity from two optimistic-message
claim helpers, but other transcript consumers still treated it as a real reply.
A production-page browser regression reproduced a stopped bubble receiving
message index 3, shifting later CH references.

The correction uses one page-owned record selector for transcript navigation,
reply detection, duplicate checks, context selection, and record reconciliation.
Live and stopped activity remain visible but do not consume message indices.
Stop also clears any cached index and pin state from its retained bubble.
Existing execution, cancellation, persistence, and transport owners are unchanged.

Validation against the isolated candidate:

- 56 focused Stop, message, generation, reply-detection, and architecture tests passed.
- 25 browser cases passed with zero skips, including native stream cancellation,
  desktop/phone Stop-resend-reload, and real Flask/auth/SQLite shadow journeys.
- Both new query-link cases failed against the original implementation and passed
  with the correction. A subsequent real answer reading “Agent stopped.” paints
  separately and has the correct CH reference.
- Existing shadow assertions still prove no stale reply/history insertion and no
  release of the newer turn's busy lock. External/vendor execution stays blocked.

Only frontend code and tests changed. No live Flask restart, daemon operation,
paid execution, or user-database mutation was used for this validation.
