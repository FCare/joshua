#!/bin/bash

# Script d'entrée Docker pour démarrer le pipeline dynamiquement
set -e

echo "🐳 Démarrage du Pipeline Framework Assistant MCP"
echo "================================================"

# Variables d'environnement avec valeurs par défaut
PIPELINE_NAME="${PIPELINE_NAME:-Pipeline Chat WebSocket}"
WEBSOCKET_PORT="${WEBSOCKET_PORT:-8768}"

echo "📋 Pipeline sélectionné: $PIPELINE_NAME"
echo "🌐 Port WebSocket: $WEBSOCKET_PORT"
echo "🔑 Clés API configurées:"
echo "   - OpenAI API Key: ${OPENAI_API_KEY:+[SET]} ${OPENAI_API_KEY:-[NOT SET]}"
echo "   - LlamaCP API Key: ${LLAMACPP_API_KEY:+[SET]} ${LLAMACPP_API_KEY:-[NOT SET]}"
echo ""

# Vérifier que les clés API sont définies
if [ -z "$OPENAI_API_KEY" ] && [ -z "$LLAMACPP_API_KEY" ]; then
    echo "❌ ERREUR: Aucune clé API n'est définie!"
    echo "   Définissez OPENAI_API_KEY ou LLAMACPP_API_KEY dans le .env"
    exit 1
fi

# Lister les pipelines disponibles pour vérification
echo "📋 Pipelines disponibles:"
python run_pipeline.py list
echo ""

# Vérifier que le pipeline existe avant de le lancer
if ! python run_pipeline.py list | grep -q "$PIPELINE_NAME"; then
    echo "❌ ERREUR: Pipeline '$PIPELINE_NAME' non trouvé!"
    echo "   Pipelines disponibles:"
    python run_pipeline.py list
    exit 1
fi

echo "🚀 Lancement du pipeline '$PIPELINE_NAME'..."
echo "💡 Connectez-vous sur ws://localhost:$WEBSOCKET_PORT"
echo "🛑 Ctrl+C pour arrêter"
echo ""

# Lancer le pipeline
exec python run_pipeline.py run --pipeline "$PIPELINE_NAME"