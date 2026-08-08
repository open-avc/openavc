"""
OpenAVC Update System.

Provides version checking, download, apply, backup, and rollback for
self-updating OpenAVC installations. See Implementation Design Section 10.5.
"""

from openavc.updater.checker import UpdateChecker
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import detect_deployment_type, DeploymentType

__all__ = ["UpdateChecker", "UpdateManager", "detect_deployment_type", "DeploymentType"]
