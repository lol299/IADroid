#!/usr/bin/env bash
# =============================================================================
# IADroid - Correctifs appliqués aux sources de llama.cpp (idempotent)
#
# Usage : bash scripts/patch_llama.sh [dossier_llama.cpp] [revert]
#   - sans "revert" : applique les correctifs (sans effet s'ils sont déjà présents)
#   - avec "revert" : restaure les fichiers d'origine (à faire AVANT un git pull)
#
# Correctif 1 — softmax "wg512" (contexte > 1024 tokens)
#   Au-delà de 1024 tokens de contexte, ggml-vulkan utilise un shader softmax à
#   512 invocations synchronisées par barrière. Le pilote Turnip (Adreno 750) le
#   refuse : "Compute shader ... has workgroup barrier cannot be used because
#   it's impossible to have enough concurrent waves", puis ErrorDeviceLost et
#   llama-server s'arrête. La variante standard boucle sur les colonnes et gère
#   toutes les longueurs ; la perte de vitesse est négligeable.
# =============================================================================

SRC="${1:-$HOME/.iadroid/src/llama.cpp}"
REL="ggml/src/ggml-vulkan/ggml-vulkan.cpp"
F="$SRC/$REL"
MARK="IADroid: wg512"

[ -f "$F" ] || { echo "[IADroid] Sources Vulkan introuvables ($F), aucun correctif appliqué."; exit 0; }

if [ "${2:-}" = "revert" ]; then
    git -C "$SRC" checkout -- "$REL" 2>/dev/null || true
    exit 0
fi

if grep -q "$MARK" "$F"; then
    echo "[IADroid] Correctif softmax Vulkan déjà appliqué."
    exit 0
fi

sed -i -E "s/return src0->ne\[0\] > 1024 \? ctx->device->(pipeline_soft_max_f32(_f16)?)_wg512 : ctx->device->\1;/return ctx->device->\1; \/\/ $MARK plante sous Turnip (Adreno)/" "$F"

if grep -q "$MARK" "$F"; then
    echo "[IADroid] Correctif softmax Vulkan appliqué (contextes > 1024 tokens)."
else
    echo "[IADroid] ATTENTION : le code de llama.cpp a changé, correctif softmax non appliqué."
    echo "          Les conversations de plus de 1024 tokens peuvent planter sous Turnip."
fi
exit 0
