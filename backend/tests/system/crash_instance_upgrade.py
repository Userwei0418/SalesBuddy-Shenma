"""Test-only abrupt process exit, outside the shipped installation artifact."""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'tools/installation'))
import instance_upgrade

phase=sys.argv.pop(1)
original=instance_upgrade.checkpoint
def crash(root,journal,current):
    original(root,journal,current)
    if current==phase: os._exit(92)
instance_upgrade.checkpoint=crash
instance_upgrade.main()
