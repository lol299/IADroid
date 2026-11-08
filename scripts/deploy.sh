#!/usr/bin/env bash
# =============================================================================
# IADroid - Déploiement de développement PC -> téléphone (Termux) via SSH
#
# Copie le code de travail actuel (y compris les modifications non commitées)
# dans ~/IADroid-dev sur le téléphone, SANS passer par GitHub.
# La version installée ~/IADroid (commande `iadroid`, /update) n'est pas touchée.
#
# Usage (depuis Git Bash sur le PC, à la racine du projet) :
#   bash scripts/deploy.sh          # envoie les fichiers
#   bash scripts/deploy.sh run      # envoie puis lance IADroid-dev dans le terminal
#
# Configuration : créez un fichier .deploy.env (ignoré par git) à la racine :
#   PHONE_HOST=192.168.5.208
#   PHONE_USER=u0_a606
#   PHONE_PORT=8022
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -f .deploy.env ] && . ./.deploy.env
PHONE_HOST="${PHONE_HOST:?Définissez PHONE_HOST dans .deploy.env}"
PHONE_USER="${PHONE_USER:?Définissez PHONE_USER dans .deploy.env}"
PHONE_PORT="${PHONE_PORT:-8022}"
REMOTE_DIR="${REMOTE_DIR:-IADroid-dev}"

SSH=(ssh -p "$PHONE_PORT" -o ConnectTimeout=5 "$PHONE_USER@$PHONE_HOST")

# Fichiers suivis + nouveaux fichiers non ignorés (respecte .gitignore)
FILES=$(git ls-files -co --exclude-standard)

echo "→ Envoi vers $PHONE_USER@$PHONE_HOST:~/$REMOTE_DIR ..."
# shellcheck disable=SC2086
tar -czf - $FILES | "${SSH[@]}" "mkdir -p ~/$REMOTE_DIR && tar -xzf - -C ~/$REMOTE_DIR && chmod +x ~/$REMOTE_DIR/iadroid.py ~/$REMOTE_DIR/scripts/*.sh"
echo "✓ Déployé ($(echo "$FILES" | wc -l) fichiers)."

if [ "${1:-}" = "run" ]; then
    exec ssh -t -p "$PHONE_PORT" "$PHONE_USER@$PHONE_HOST" "cd ~/$REMOTE_DIR && python3 iadroid.py"
else
    echo "Pour tester sur le téléphone : python3 ~/$REMOTE_DIR/iadroid.py"
fi
