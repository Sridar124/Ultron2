"""Ultron Connect subsystem.

This package adds the local gateway, device registry, pairing flow, and
protocol definitions used by Ultron AI to reach companion devices.
"""

from .service import UltronConnectService, get_service

__all__ = ["UltronConnectService", "get_service"]
