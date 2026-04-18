"""Layer-2 audit-comment emission on the parent Unit.

Per docs/ExternalRunner.md §5.2, canonical template::

    [ZTMOS][Tn] <short> · run: <id> · event: <id> · key: idem_<hex>

Idempotent by suffix-match on the ``key:`` token.

Implementation lands in M4.
"""
