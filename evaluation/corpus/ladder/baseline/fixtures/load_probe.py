"""Probe helper: load an inventory file through a pathlib.Path and print how many items it has."""

import json
import pathlib
import sys

from inventory import Inventory

inventory = Inventory.load(pathlib.Path(sys.argv[1]))
print(json.dumps({"items": len(inventory.list_items())}))
