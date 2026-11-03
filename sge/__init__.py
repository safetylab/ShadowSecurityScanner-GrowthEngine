"""Shared library for Shadow Growth Engine agents.

Agents stay thin CLI wrappers; everything reusable (database access,
configuration loading, network limits, feed parsing) lives here so that
security-relevant code has a single place to review and test.
"""

__version__ = "0.2.0"
