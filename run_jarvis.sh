#!/usr/bin/env bash
# Script de Inicialização do J.A.R.V.I.S.

set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ -z "$GEMINI_API_KEY" ] && [ -z "$GEMINI_API_KEYS" ] \
   && ! grep -qE '^[[:space:]]*GEMINI_API_KEYS?=.+' "$PROJECT_DIR/.env" 2>/dev/null; then
    echo "⚠️  [AVISO]: As chaves Gemini não foram detectadas no ambiente."
    echo "   Configure GEMINI_API_KEY ou GEMINI_API_KEYS no arquivo .env."
    echo ""
fi

echo "⚡ [J.A.R.V.I.S.]: Inicializando subsistemas Stark Industries..."
exec .venv/bin/python server.py
