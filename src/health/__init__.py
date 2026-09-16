"""Kubernetes health-probe support.

See src/health/checks.py for what /health/live and /health/ready (src/
health/routes.py) actually validate, and why they deliberately validate
different things.
"""
from __future__ import annotations

from .checks import CheckResult, DependencyHealth
from .routes import health_bp

__all__ = ["CheckResult", "DependencyHealth", "health_bp"]
