#!/usr/bin/env python3
"""
IADroid - Launcher alternatif
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from iadroid import __version__
from iadroid.cli import IADroidCLI

if __name__ == "__main__":
    cli = IADroidCLI()
    cli.start()
