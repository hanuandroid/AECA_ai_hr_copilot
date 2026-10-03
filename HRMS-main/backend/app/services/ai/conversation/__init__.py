"""Copilot conversations (Phase 7): owner-only persistent chat history and bounded follow-up context.

Conversation history is context, never authorization: identity, role and scope are rebuilt from the
authenticated request on every turn, and history reaches the router only as untrusted data.
"""
