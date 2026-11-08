#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# IADroid - Script de compilation de llama.cpp avec CMake & Ninja
# Support : Vulkan (GPU Adreno/Mali) + CPU (NEON)
# ==============================================================================

set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

TARGET_BACKEND="vulkan"
FORCE_REBUILD=0

# Analyse des arguments
for arg in "$@"; do
    case $arg in
        --cpu)
            TARGET_BACKEND="cpu"
            ;;
        --gpu|--vulkan)
            TARGET_BACKEND="vulkan"
            ;;
        --clean|--rebuild)
            FORCE_REBUILD=1
            ;;
        *)
            echo "Option inconnue : $arg"
            echo "Usage: ./build_llama.sh [--gpu|--cpu] [--rebuild]"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}====================================================${NC}"
echo -e "${GREEN}       IADroid - Compilation de llama.cpp            ${NC}"
echo -e "${CYAN}====================================================${NC}"
echo -e "${CYAN}[INFO]${NC} Backend sélectionné : ${YELLOW}$TARGET_BACKEND${NC}"

# Vérification des outils nécessaires
for tool in git cmake clang ninja; do
    if ! command -v $tool >/dev/null 2>&1; then
        echo -e "${RED}[ERREUR]${NC} L'outil '$tool' est requis. Installez-le avec : pkg install $tool"
        exit 1
    fi
done

if [ "$TARGET_BACKEND" == "vulkan" ] && ! command -v glslc >/dev/null 2>&1; then
    echo -e "${YELLOW}[NOTE]${NC} Le compilateur de shaders 'glslc' est manquant pour Vulkan. Installation automatique..."
    pkg install -y shaderc || true
fi

IADROID_DIR="$HOME/.iadroid"
SRC_DIR="$IADROID_DIR/src/llama.cpp"
BIN_DIR="$IADROID_DIR/bin"

mkdir -p "$IADROID_DIR/src" "$BIN_DIR"

# 1. Récupération ou mise à jour du dépôt llama.cpp
if [ ! -d "$SRC_DIR/.git" ]; then
    echo -e "${CYAN}[1/4]${NC} Clonage du dépôt officiel llama.cpp..."
    git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "$SRC_DIR"
else
    echo -e "${CYAN}[1/4]${NC} Dépôt existant détecté. Mise à jour..."
    cd "$SRC_DIR"
    bash "$(dirname "$0")/patch_llama.sh" "$SRC_DIR" revert
    git pull || true
fi

cd "$SRC_DIR"

# Correctifs IADroid sur les sources llama.cpp (voir scripts/patch_llama.sh)
bash "$(dirname "$0")/patch_llama.sh" "$SRC_DIR"

# 2. Nettoyage si demandé
if [ $FORCE_REBUILD -eq 1 ] && [ -d "build" ]; then
    echo -e "${YELLOW}[INFO]${NC} Suppression du dossier de build précédent..."
    rm -rf build
fi

# 3. Configuration CMake
echo -e "${CYAN}[2/4]${NC} Configuration avec CMake..."
PREFIX_PATH="${PREFIX:-/data/data/com.termux/files/usr}"
CMAKE_ARGS="-G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=$PREFIX_PATH"

if [ "$TARGET_BACKEND" == "vulkan" ]; then
    echo -e "${GREEN}[INFO]${NC} Activation de l'accélération GPU Vulkan (-DGGML_VULKAN=ON)..."
    CMAKE_ARGS="$CMAKE_ARGS -DGGML_VULKAN=ON"
else
    echo -e "${YELLOW}[INFO]${NC} Compilation CPU uniquement (-DGGML_VULKAN=OFF)..."
    CMAKE_ARGS="$CMAKE_ARGS -DGGML_VULKAN=OFF"
fi

cmake -B build $CMAKE_ARGS

# 4. Compilation avec protection contre l'OOM Killer d'Android (Signal 9)
# Activer le Wake Lock Termux pour empêcher Android de mettre le CPU en veille
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock 2>/dev/null || true
fi

# Calcul du nombre de threads sécurisé selon la RAM disponible (évite le Signal 9)
TOTAL_RAM=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 4000)
CPU_TOTAL=$(nproc 2>/dev/null || echo 4)

if [ "$TOTAL_RAM" -ge 12000 ]; then
    SAFE_JOBS=4
elif [ "$TOTAL_RAM" -ge 7000 ]; then
    SAFE_JOBS=3
else
    SAFE_JOBS=2
fi

echo -e "${CYAN}[3/4]${NC} Compilation en cours avec ${YELLOW}$SAFE_JOBS threads sécurisés${NC} (RAM totale: ${TOTAL_RAM} Mo)..."
echo -e "      [dim]Ninja reprendra automatiquement là où la compilation s'était arrêtée.[/dim]"
cmake --build build --config Release -j"$SAFE_JOBS"

# 5. Détection des binaires générés et création des liens
echo -e "${CYAN}[4/4]${NC} Finalisation et vérification des binaires..."

# Dans les versions récentes de llama.cpp, les binaires sont dans build/bin/
CLI_PATH=""
SERVER_PATH=""

if [ -f "build/bin/llama-cli" ]; then
    CLI_PATH="build/bin/llama-cli"
    SERVER_PATH="build/bin/llama-server"
elif [ -f "build/llama-cli" ]; then
    CLI_PATH="build/llama-cli"
    SERVER_PATH="build/llama-server"
fi

if [ -z "$CLI_PATH" ] || [ ! -f "$CLI_PATH" ]; then
    echo -e "${RED}[ERREUR]${NC} Le binaire llama-cli est introuvable après la compilation."
    exit 1
fi

cp "$CLI_PATH" "$BIN_DIR/llama-cli"
[ -f "$SERVER_PATH" ] && cp "$SERVER_PATH" "$BIN_DIR/llama-server"
chmod +x "$BIN_DIR/llama-cli" "$BIN_DIR/llama-server" 2>/dev/null || true

echo -e "\n${GREEN}====================================================${NC}"
echo -e "${GREEN}   Compilation terminée avec succès !                ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "Binaires installés dans : ${CYAN}$BIN_DIR${NC}"
echo -e "Test de version : "
"$BIN_DIR/llama-cli" --version 2>&1 | head -n 3 || true

if [ "$TARGET_BACKEND" == "vulkan" ]; then
    echo -e "\n${GREEN}[VULKAN ACTIF]${NC} Vous pouvez décharger les couches sur le GPU avec le paramètre : ${YELLOW}-ngl 99${NC}"
    echo -e "                  Pour forcer le mode CPU : ${YELLOW}-ngl 0${NC}"
fi
