"""Runtime adapter contracts and source registry."""

from .base import DiscoveryContext, SourceSpec
from .registry import RUNTIME_IDS, SOURCE_SPECS, validate_registry

__all__ = (
    "DiscoveryContext",
    "RUNTIME_IDS",
    "SOURCE_SPECS",
    "SourceSpec",
    "validate_registry",
)
