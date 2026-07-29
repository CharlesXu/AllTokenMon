"""Kilo Code editor task adapter."""

from pathlib import Path
from typing import Sequence

from ..schema import AdapterResult
from .base import DiscoveryContext, SourceSpec
from .cline_family import parse_cline_family, scan_cline_family


def parse_kilocode(paths: Sequence[Path]) -> AdapterResult:
    return parse_cline_family("kilocode", paths)


def scan(
    context: DiscoveryContext, specs: Sequence[SourceSpec]
) -> AdapterResult:
    return scan_cline_family("kilocode", context, specs)
