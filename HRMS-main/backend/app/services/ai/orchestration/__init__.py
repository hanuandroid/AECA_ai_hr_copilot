"""Copilot router (Phase 6): LangGraph dispatcher over the Policy RAG, SQL and Action services.

Routing is not authorization. Each specialised service re-authorizes the trusted AIUserContext.
"""
