"""Idempotency-key computation and label-based replay protection.

Per docs/ExternalRunner.md §5.3:

- ``compute_key(unit_key, event_id, transition_id)``  →  ``sha256(...)[:12]``.
- ``has_been_applied(unit, key)``                     →  label lookup ``idem:<hex>``.
- ``mark_applied(subtask, key)``                      →  attach label on create.

Guarantees exactly-once execution under polling retry.

Implementation lands in M4.
"""
