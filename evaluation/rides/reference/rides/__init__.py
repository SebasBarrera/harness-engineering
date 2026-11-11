"""Ride-hailing and food delivery platform backend.

Layers: ``core`` (framework-free helpers), ``domain`` (entities and pure rules),
``services`` (use cases over the state), ``persistence`` (SQLite) and the ``Platform`` facade.
"""

from .platform import Platform

__all__ = ["Platform"]
