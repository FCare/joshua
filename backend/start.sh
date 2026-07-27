#!/bin/bash
# Script de démarrage pour Joshua backend
# Lance à la fois le serveur WebSocket et l'API HTTP

set -e

echo "🚀 Démarrage de Joshua backend..."

# Lancer l'API HTTP en arrière-plan
echo "📡 Démarrage de l'API HTTP sur le port 8769..."
python http_api.py &
HTTP_PID=$!

# Attendre que l'API HTTP soit prête
sleep 2

# Lancer le serveur WebSocket
echo "🔌 Démarrage du serveur WebSocket..."
python run_server.py run --pipeline "${PIPELINE_NAME}" --port "${WEBSOCKET_PORT}"

# Si le WebSocket s'arrête, arrêter aussi l'API HTTP
kill $HTTP_PID 2>/dev/null || true
