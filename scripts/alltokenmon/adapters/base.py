"""Shared immutable contracts for local source discovery."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple


@dataclass(frozen=True)
class DiscoveryContext:
    os_name: str
    home: Path
    env: Mapping[str, str]


@dataclass(frozen=True)
class SourceSpec:
    runtime: str
    source_kind: str
    patterns: Tuple[str, ...]
    recursive: bool
    cache_only: bool
    roots: Callable[[DiscoveryContext], Tuple[Path, ...]]
    matcher: Optional[Callable[[Path], bool]] = None
