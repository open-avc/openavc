"""
OpenAVC Update System.

Provides version checking, download, apply, backup, and rollback for
self-updating OpenAVC installations. See Implementation Design Section 10.5.
"""

from server.updater.checker import UpdateChecker
from server.updater.manager import UpdateManager
from server.updater.platform import detect_deployment_type, DeploymentType

__all__ = ["UpdateChecker", "UpdateManager", "detect_deployment_type", "DeploymentType"]
