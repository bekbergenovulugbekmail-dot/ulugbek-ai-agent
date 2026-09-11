"""Adapters for external services.

Each integration is a self-contained package: a thin client that speaks the
service's API, plus :class:`~ulugbek_ai.tools.base.Tool` subclasses that expose
it to the agent. Nothing above this layer knows which services exist — the
registry, the permission system, the approval flow and the UI treat every
integration identically.
"""
