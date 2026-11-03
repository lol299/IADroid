#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# IADroid - Script de diagnostic d'environnement (Termux / Android ARM64)
# ==============================================================================

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}====================================================${NC}"
echo -e "${GREEN}       IADroid - Diagnostic Système Termux           ${NC}"
echo -e "${CYAN}====================================================${NC}"

# 1. Vérification de l'environnement Termux
if [ -z "$PREFIX" ] || [ ! -d "/data/data/com.termux" ]; then
    echo -e "${YELLOW}[ATTENTION]${NC} Ce script ne semble pas s'exécuter dans l'environnement standard Termux."
else
    echo -e "${GREEN}[OK]${NC} Environnement Termux détecté ($PREFIX)."
fi

# 2. Vérification de l'architecture CPU
ARCH=$(uname -m)
echo -e "${CYAN}[INFO]${NC} Architecture CPU : ${YELLOW}$ARCH${NC}"
if [ "$ARCH" != "aarch64" ]; then
    echo -e "${YELLOW}[AVERTISSEMENT]${NC} L'architecture détectée n'est pas aarch64 (ARM64). IADroid est optimisé pour ARM64."
else
    echo -e "${GREEN}[OK]${NC} Architecture ARM64 compatible."
fi

# 3. Vérification du nombre de cœurs CPU & Mémoire RAM
CORES=$(nproc 2>/dev/null || echo 4)
echo -e "${GREEN}[OK]${NC} Cœurs CPU détectés : ${YELLOW}$CORES${NC}"

if command -v free >/dev/null 2>&1; then
    TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
    AVAIL_RAM=$(free -m | awk '/^Mem:/{print $7}')
    echo -e "${CYAN}[INFO]${NC} RAM Totale : ${YELLOW}${TOTAL_RAM} Mo${NC} | Disponible : ${YELLOW}${AVAIL_RAM} Mo${NC}"
fi

# 4. Vérification de l'accès au stockage Android
if [ -d "$HOME/storage/shared" ]; then
    echo -e "${GREEN}[OK]${NC} Accès au stockage Android (/sdcard) actif."
else
    echo -e "${YELLOW}[NOTE]${NC} Accès au stockage Android non configuré."
    echo -e "       Vous pouvez exécuter : ${CYAN}termux-setup-storage${NC} pour stocker vos modèles GGUF dans vos dossiers habituels (ex: /sdcard/Download)."
fi

# 5. Diagnostic GPU Vulkan
echo -e "\n${CYAN}--- Test du support GPU Vulkan ---${NC}"
HAS_VULKAN_LIB=0
if [ -f "/system/lib64/libvulkan.so" ] || [ -f "$PREFIX/lib/libvulkan.so" ]; then
    HAS_VULKAN_LIB=1
    echo -e "${GREEN}[OK]${NC} Bibliothèque Vulkan détectée sur l'appareil."
fi

VULKAN_DEV_FOUND=0
if command -v vulkaninfo >/dev/null 2>&1; then
    VULKAN_OUTPUT=$(vulkaninfo --summary 2>/dev/null)
    if [ $? -eq 0 ] && [ -n "$VULKAN_OUTPUT" ]; then
        GPU_NAME=$(echo "$VULKAN_OUTPUT" | grep -i "deviceName" | head -n 1 | awk -F '=' '{print $2}' | xargs)
        DRIVER_INFO=$(echo "$VULKAN_OUTPUT" | grep -i "driverInfo" | head -n 1 | awk -F '=' '{print $2}' | xargs)
        API_VER=$(echo "$VULKAN_OUTPUT" | grep -i "apiVersion" | head -n 1 | awk -F '=' '{print $2}' | xargs)
        
        echo -e "${GREEN}[OK]${NC} Puce GPU Vulkan identifiée : ${GREEN}${GPU_NAME:-Inconnu}${NC}"
        [ -n "$API_VER" ] && echo -e "     API Version : ${YELLOW}$API_VER${NC}"
        [ -n "$DRIVER_INFO" ] && echo -e "     Pilote : ${YELLOW}$DRIVER_INFO${NC}"
        VULKAN_DEV_FOUND=1
    fi
fi

if [ $VULKAN_DEV_FOUND -eq 0 ]; then
    if [ $HAS_VULKAN_LIB -eq 1 ]; then
        echo -e "${YELLOW}[NOTE]${NC} Pilote Vulkan système présent. Une compilation Vulkan devrait fonctionner."
    else
        echo -e "${YELLOW}[AVERTISSEMENT]${NC} Impossible d'interroger Vulkan directement via vulkaninfo."
        echo -e "               Une compilation CPU avec accélération NEON reste disponible en fallback."
    fi
fi

echo -e "\n${GREEN}[FIN]${NC} Diagnostic terminé.\n"
