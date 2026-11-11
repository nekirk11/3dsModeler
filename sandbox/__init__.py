"""Browser sandbox for the experimental context-menu prototype.

The sandbox is a thin, loopback-only renderer around the shared Python
controller.  Importing this package has no side effects.
"""

from .server import SandboxServer, main

__all__ = ["SandboxServer", "main"]
