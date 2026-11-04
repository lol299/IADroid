#!/usr/bin/env python3
"""
IADroid - Point d'entrée principal
"""

import sys
import os

# Ajout du dossier courant au PYTHONPATH pour les imports relatifs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from iadroid.cli import IADroidCLI
from iadroid.preset_manager import PresetManager
from iadroid.wizard import PresetWizard
from iadroid.banner import print_help_table

def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        manager = PresetManager()
        wizard = PresetWizard(manager)

        if arg in ("--help", "-h"):
            print_help_table()
            return
        elif arg in ("--update", "-u"):
            from iadroid.updater import run_update_interactive
            run_update_interactive()
            return
        elif arg in ("--list", "-l"):
            manager.display_presets_table()
            return
        elif arg == "--create-preset":
            wizard.run_wizard()
            return
        elif arg == "--run":
            cli = IADroidCLI()
            cli.run_interactive_session()
            return
        elif not arg.startswith("-"):
            # Exécution d'un prompt direct passé en argument (mode one-shot)
            query = " ".join(sys.argv[1:])
            cli = IADroidCLI()
            cli.stream_response(query)
            return

    # Lancement du mode interactif par défaut
    cli = IADroidCLI()
    cli.start()

if __name__ == "__main__":
    main()
