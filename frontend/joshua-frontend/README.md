# Joshua - AI Assistant Frontend

Une interface moderne et épurée pour interagir avec des modèles AI, extraite et adaptée du frontend llama.cpp.

![Joshua Interface](https://img.shields.io/badge/Frontend-Modern%20Chat-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ Fonctionnalités

- **Interface épurée** - Design moderne inspiré de ChatGPT
- **Chat en temps réel** - Streaming des réponses
- **Upload de fichiers** - Support images et documents
- **Mode adaptatif** - Responsive design
- **API compatible** - Fonctionne avec llama.cpp et autres backends
- **Mode démo** - Fonctionne sans backend pour les tests

## 🚀 Démarrage rapide

### Option 1: Serveur Python intégré

```bash
cd joshua-frontend
python3 server.py [port] [backend-url]
```

**Exemples:**
```bash
# Serveur sur port 8080, backend llama.cpp sur localhost:8080
python3 server.py

# Serveur sur port 3000, backend custom
python3 server.py 3000 http://localhost:11434

# Mode démo (sans backend)
python3 server.py 8080 http://localhost:9999
```

### Option 2: Serveur web statique

Servez les fichiers via n'importe quel serveur web :

```bash
# Avec Python
python3 -m http.server 8080

# Avec Node.js
npx http-server -p 8080

# Avec PHP
php -S localhost:8080
```

Puis ouvrez: `http://localhost:8080`

## 📁 Structure du projet

```
joshua-frontend/
├── index.html          # Interface principale
├── styles.css          # Styles modernes
├── script.js           # Logique JavaScript
├── server.py           # Serveur Python optionnel
└── README.md          # Cette documentation
```

## 🔧 Configuration

### Backend API

Par défaut, l'interface utilise l'endpoint `/completion` compatible llama.cpp.

Pour changer l'URL du backend, modifiez dans `script.js`:

```javascript
// Ligne 9
this.apiUrl = 'http://votre-backend:port/completion';
```

### Paramètres de génération

Les paramètres par défaut (modifiables dans `script.js`):

```javascript
const params = {
    prompt: prompt,
    stream: true,
    n_predict: 800,
    temperature: 0.7,
    top_k: 40,
    top_p: 0.95,
    stop: ["</s>", "Human:", "User:"]
};
```

## 🎨 Personnalisation

### Changer le nom

Remplacez "Joshua" dans `index.html`:

```html
<h1 class="title">VotreNom</h1>
```

### Modifier les couleurs

Dans `styles.css`, les principales variables de couleur:

```css
/* Couleur principale */
.send-btn { background-color: #2563eb; }

/* Messages utilisateur */
.message.user .message-content { background-color: #2563eb; }

/* Style focus */
.input-wrapper:focus-within { border-color: #2563eb; }
```

### Ajouter des thèmes

Ajoutez des classes CSS pour basculer entre thèmes clair/sombre.

## 🔌 Intégration avec backends

### llama.cpp

Compatible par défaut. Assurez-vous que llama.cpp server tourne avec:

```bash
# Depuis le dossier llama.cpp
./server -m models/votre-modele.gguf --host 0.0.0.0 --port 8080
```

### Ollama

Modifiez l'endpoint pour Ollama:

```javascript
// Dans script.js
this.apiUrl = 'http://localhost:11434/api/generate';
```

### OpenAI API

Pour l'API OpenAI, adaptez les paramètres:

```javascript
const params = {
    messages: [{ role: "user", content: prompt }],
    stream: true,
    model: "gpt-3.5-turbo"
};
```

### API custom

Adaptez le format de requête dans la méthode `llamaStream()`.

## 📱 Fonctionnalités avancées

### Upload d'images

- Cliquez sur 📎 pour uploader
- Support: PNG, JPG, GIF, WebP
- Envoi automatique au backend compatible vision

### Raccourcis clavier

- `Enter` : Envoyer le message
- `Shift + Enter` : Nouvelle ligne
- Auto-redimensionnement du textarea

### Formatage du texte

Support basique Markdown:
- `**gras**` → **gras**
- `*italique*` → *italique*
- `` `code` `` → `code`
- Blocs de code avec ```

## 🛠️ Développement

### Modification en temps réel

1. Modifiez les fichiers CSS/JS
2. Rechargez la page
3. Les changements sont immédiatement visibles

### Debug

Ouvrez la console navigateur (F12) pour:
- Voir les logs de communication API
- Déboguer les erreurs JavaScript
- Monitorer le trafic réseau

### Tests

Test de l'interface sans backend:
```bash
python3 server.py 8080 http://localhost:9999
```

L'interface affichera des réponses de démonstration.

## 📊 Performance

- **Taille totale**: ~15KB (non compressé)
- **Dépendances**: Aucune (Vanilla JavaScript)
- **Compatible**: Tous navigateurs modernes
- **Mobile-friendly**: Design responsive

## 🔍 Troubleshooting

### Erreurs CORS

Si vous voyez des erreurs CORS:
- Utilisez le serveur Python fourni
- Ou servez depuis un serveur HTTP, pas en local `file://`

### Backend non disponible

L'interface montre automatiquement:
- Messages d'erreur clairs
- Mode démo avec réponses simulées
- Instructions de connexion

### Performance lente

- Réduisez `n_predict` dans les paramètres
- Vérifiez la latence réseau au backend
- Utilisez un modèle plus petit

## 🤝 Contribution

Structure modulaire pour faciliter les contributions:

1. **Interface** (`index.html`, `styles.css`)
2. **Logique** (`script.js`)
3. **Backend** (`server.py`)

### Ajout de fonctionnalités

- Nouveaux formats de fichiers → `handleFileUpload()`
- Thèmes → `styles.css`
- APIs → `llamaStream()`

## 📝 Licence

Basé sur le frontend llama.cpp. Code adapté sous licence MIT.

## 🔗 Liens utiles

- [llama.cpp](https://github.com/ggerganov/llama.cpp) - Backend AI original
- [Documentation llama.cpp API](https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md)
- [Modèles GGUF](https://huggingface.co/models?library=gguf)

---

**Fait avec ❤️ - Interface Joshua v1.0**