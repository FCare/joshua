"""
API HTTP pour Joshua - Endpoints auxiliaires.

Fournit des endpoints HTTP pour des opérations comme l'obtention de tokens OAuth.
"""

import logging
import os

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(title="Joshua HTTP API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

AUTHENTIK_URL = os.getenv("AUTHENTIK_URL", "https://sso.caronboulme.fr")
JOSHUA_CLIENT_ID = os.getenv("JOSHUA_CLIENT_ID")
JOSHUA_CLIENT_SECRET = os.getenv("JOSHUA_CLIENT_SECRET")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/get-oauth-token")
async def get_oauth_token(
    x_authentik_username: str = Header(None),
    x_authentik_uid: str = Header(None),
):
    """Obtient un access token OAuth pour l'utilisateur authentifié.

    L'utilisateur doit être authentifié via Authentik ForwardAuth.
    Traefik injecte les headers X-Authentik-*.

    Retourne un access token utilisable comme password MQTT.
    """
    if not x_authentik_username:
        raise HTTPException(
            status_code=401,
            detail="Non authentifié - header X-Authentik-Username manquant"
        )

    if not JOSHUA_CLIENT_ID or not JOSHUA_CLIENT_SECRET:
        logger.error("JOSHUA_CLIENT_ID ou JOSHUA_CLIENT_SECRET non configurés")
        raise HTTPException(
            status_code=500,
            detail="Configuration OAuth manquante"
        )

    logger.info(f"Demande de token OAuth pour {x_authentik_username}")

    try:
        # Pour l'instant, utiliser Client Credentials pour obtenir un token
        # TODO: Implémenter Token Exchange quand Authentik le supportera
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{AUTHENTIK_URL}/application/o/token/",
                data={
                    "grant_type": "client_credentials",
                    "client_id": JOSHUA_CLIENT_ID,
                    "client_secret": JOSHUA_CLIENT_SECRET,
                },
            )

            if resp.status_code == 200:
                data = resp.json()
                access_token = data.get("access_token")
                expires_in = data.get("expires_in")

                if access_token:
                    logger.info(f"Token OAuth obtenu pour {x_authentik_username} (expire dans {expires_in}s)")
                    return {
                        "access_token": access_token,
                        "expires_in": expires_in,
                        "token_type": "Bearer",
                        "username": x_authentik_username,
                    }

            logger.error(f"Échec obtention token: HTTP {resp.status_code}")
            raise HTTPException(
                status_code=502,
                detail=f"Échec obtention token OAuth: {resp.status_code}"
            )

    except httpx.RequestError as e:
        logger.error(f"Erreur connexion Authentik: {e}")
        raise HTTPException(
            status_code=502,
            detail="Erreur connexion au serveur d'authentification"
        )


if __name__ == "__main__":
    import uvicorn
    # Port 8770 pour l'API HTTP (8769 est utilisé par le WebSocket)
    uvicorn.run(app, host="0.0.0.0", port=8770)
