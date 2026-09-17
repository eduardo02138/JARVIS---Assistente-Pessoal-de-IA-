#!/usr/bin/env bash
# Script de Inicialização do J.A.R.V.I.S.

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ -z "$GEMINI_API_KEY" ]; then
    echo "⚠️  [AVISO]: A variável GEMINI_API_KEY não foi detectada no ambiente."
    echo "   Você pode passá-la agora ou digitá-la diretamente no painel de configurações do HUD."
    echo "   Exemplo: export GEMINI_API_KEY='sua_chave'"
    echo ""
fi

echo "⚡ [J.A.R.V.I.S.]: Inicializando subsistemas Stark Industries..."
exec .venv/bin/python server.py
