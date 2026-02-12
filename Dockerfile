# Dockerfile pour le Pipeline Framework Assistant MCP
FROM python:3.12-slim

# Définir le répertoire de travail
WORKDIR /app

# Installer les dépendances système
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copier les fichiers de requirements
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code source
COPY . .

# Créer un script d'entrée pour démarrer le pipeline dynamiquement
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Exposer le port (sera configuré dynamiquement)
EXPOSE ${WEBSOCKET_PORT:-8768}

# Variables d'environnement par défaut
ENV PIPELINE_NAME="Pipeline Chat WebSocket"
ENV WEBSOCKET_PORT=8768

# Point d'entrée
ENTRYPOINT ["/docker-entrypoint.sh"]