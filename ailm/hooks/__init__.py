"""Pluggy-based hook system for ailm."""

from ailm.hooks.manager import HookManager
from ailm.hooks.specs import AilmHookSpec, hookimpl, hookspec

__all__ = [
    "AilmHookSpec",
    "HookManager",
    "hookimpl",
    "hookspec",
]
