"""Aristotle extension entry point — ADR-014 §6.4.

The platform's ExtensionHost discovers this via importlib.metadata:
    entry_points(group="aip.extensions") → aristotle.entrypoint:get_manifest

get_manifest() returns a Manifest instance constructed in Python from the
packaged extension.yaml. The host then resolves the extension's on-disk
root via importlib.resources.files("aristotle") to find migrations,
workflows, and hooks.py.

This is the production discovery path — replaces the filesystem + sys.path
hack. The extension is pip-installable: `pip install git+https://github.com/
freedomgeneration1111-sudo/AIP_Aristotle.git`.
"""

from __future__ import annotations

from importlib import resources

import yaml

from aip.adapter.extensions.manifest import Manifest


def get_manifest() -> Manifest:
    """Load and validate the extension.yaml packaged with this extension.

    Returns a Manifest instance. The host uses this to register the
    extension without parsing YAML itself — the entry-point contract is
    "return a Manifest", not "point at a YAML file".
    """
    # Load the extension.yaml from the package (works in both dev -e installs
    # and pip-installed wheels).
    with resources.files("aristotle").joinpath("extension.yaml").open("r") as f:
        raw = yaml.safe_load(f)
    return Manifest.model_validate(raw)
