"""AI audit and observability (Phase 8): passive, append-only, privacy-preserving.

Audit observes the system; it never authorizes, blocks or alters a request, with one deliberate
exception: a confirmed action is not executed if its ACTION_CONFIRMED event cannot be persisted.
"""
