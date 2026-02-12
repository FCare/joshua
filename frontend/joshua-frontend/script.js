/**
 * Joshua AI Assistant - Frontend JavaScript
 * WebSocket client for Joshua pipeline backend
 */

class JoshuaChat {
    constructor() {
        // WebSocket configuration
        this.wsUrl = this.getWebSocketUrl();
        this.ws = null;
        this.isConnected = false;
        this.isGenerating = false;
        this.uploadedFiles = [];
        this.capabilities = null;
        
        this.initElements();
        this.bindEvents();
        this.autoResizeTextarea();
        this.connectWebSocket();
    }

    getWebSocketUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.hostname;
        
        // Le frontend s'exécute côté navigateur, donc il doit se connecter
        // à l'adresse publique du backend, pas au nom de service Docker
        const port = '8768';  // Port backend exposé
        return `${protocol}//${host}:${port}`;
    }

    initElements() {
        this.chatMessages = document.getElementById('chat-messages');
        this.messageInput = document.getElementById('message-input');
        this.sendBtn = document.getElementById('send-btn');
        this.fileUploadBtn = document.getElementById('file-upload-btn');
        this.fileInput = document.getElementById('file-input');
        this.loading = document.getElementById('loading');
        this.subtitle = document.querySelector('.subtitle');
    }

    bindEvents() {
        // Send button click
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        
        // Enter key handling
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Auto-resize textarea
        this.messageInput.addEventListener('input', () => {
            this.autoResizeTextarea();
            this.updateSendButton();
        });

        // File upload
        this.fileUploadBtn.addEventListener('click', () => {
            this.fileInput.click();
        });

        this.fileInput.addEventListener('change', (e) => {
            this.handleFileUpload(e.target.files);
        });

        // Initial send button state
        this.updateSendButton();
    }

    autoResizeTextarea() {
        const textarea = this.messageInput;
        textarea.style.height = 'auto';
        
        const maxHeight = 120; // 5 lines approximately
        const newHeight = Math.min(textarea.scrollHeight, maxHeight);
        
        textarea.style.height = newHeight + 'px';
        
        if (textarea.scrollHeight > maxHeight) {
            textarea.style.overflowY = 'auto';
        } else {
            textarea.style.overflowY = 'hidden';
        }
    }

    // updateSendButton is now defined later in the file

    async sendMessage() {
        const message = this.messageInput.value.trim();
        if (!message || this.isGenerating || !this.isConnected) return;

        // Add user message to chat
        this.addMessage(message, 'user');
        
        // Clear input
        this.messageInput.value = '';
        this.autoResizeTextarea();
        this.updateSendButton();

        // Show loading
        this.setGenerating(true);

        try {
            // Send message via WebSocket
            this.sendWebSocketMessage(message);
        } catch (error) {
            console.error('Error sending message:', error);
            this.addMessage('Sorry, I encountered an error. Please try again.', 'assistant', true);
            this.setGenerating(false);
        }
    }

    addMessage(content, sender, isError = false) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        if (isError) {
            contentDiv.style.backgroundColor = '#fee2e2';
            contentDiv.style.color = '#dc2626';
            contentDiv.style.borderColor = '#fecaca';
        }
        
        // Basic markdown support
        contentDiv.innerHTML = this.formatMessage(content);
        
        messageDiv.appendChild(contentDiv);
        this.chatMessages.appendChild(messageDiv);
        
        // Scroll to bottom
        this.scrollToBottom();
        
        return contentDiv; // Return for streaming updates
    }

    formatMessage(text) {
        // Basic markdown formatting
        return text
            .replace(/\n/g, '<br>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
    }

    connectWebSocket() {
        console.log(`Connecting to WebSocket: ${this.wsUrl}`);
        
        try {
            this.ws = new WebSocket(this.wsUrl);
            
            this.ws.onopen = () => {
                console.log('WebSocket connected');
                this.isConnected = true;
                this.updateConnectionStatus();
            };
            
            this.ws.onmessage = (event) => {
                this.handleWebSocketMessage(event.data);
            };
            
            this.ws.onclose = (event) => {
                console.log('WebSocket disconnected:', event.code, event.reason);
                this.isConnected = false;
                this.updateConnectionStatus();
                
                // Attempt to reconnect after 3 seconds
                setTimeout(() => {
                    if (!this.isConnected) {
                        this.connectWebSocket();
                    }
                }, 3000);
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.addMessage('Connection error. Attempting to reconnect...', 'assistant', true);
            };
            
        } catch (error) {
            console.error('Failed to create WebSocket:', error);
            this.addMessage('Failed to connect to Joshua. Please refresh the page.', 'assistant', true);
        }
    }

    handleWebSocketMessage(data) {
        try {
            const message = JSON.parse(data);
            
            switch (message.type) {
                case 'connection_established':
                    this.capabilities = message.capabilities;
                    console.log('Connection established. Capabilities:', this.capabilities);
                    this.updateUIBasedOnCapabilities();
                    break;
                    
                case 'chat_response':
                    this.handleChatResponse(message);
                    break;
                    
                case 'transcription':
                    this.handleTranscription(message);
                    break;
                    
                case 'audio_chunk':
                    this.handleAudioChunk(message);
                    break;
                    
                case 'audio_finished':
                    console.log('Audio generation finished');
                    break;
                    
                case 'chat_finished':
                    console.log('Chat response finished');
                    this.setGenerating(false);
                    break;
                    
                default:
                    console.log('Unknown message type:', message.type, message);
            }
        } catch (error) {
            console.error('Error parsing WebSocket message:', error, data);
        }
    }

    handleChatResponse(message) {
        const text = message.text || message.content || '';
        const metadata = message.metadata || {};
        
        if (!this.currentAssistantDiv) {
            this.currentAssistantDiv = this.addMessage('', 'assistant');
            this.currentResponse = '';
        }
        
        this.currentResponse += text;
        this.currentAssistantDiv.innerHTML = this.formatMessage(this.currentResponse);
        this.scrollToBottom();
        
        // If this is a finish type response, mark as complete
        if (metadata.chunk_type === 'finish' || metadata.response_type === 'finish') {
            this.setGenerating(false);
            this.currentAssistantDiv = null;
            this.currentResponse = '';
        }
    }

    handleTranscription(message) {
        // Handle transcription comme une réponse de chat
        console.log('Transcription:', message.text);
        
        // Traiter comme une réponse de chat pour l'affichage
        this.handleChatResponse(message);
    }

    handleAudioChunk(message) {
        // Handle TTS audio if needed
        console.log('Audio chunk received');
    }

    sendWebSocketMessage(text) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            throw new Error('WebSocket not connected');
        }
        
        // ✅ Préparer le message avec texte et images
        const message = {
            type: 'user_message',
            text: text
        };
        
        // ✅ Ajouter les images si il y en a
        if (this.uploadedFiles.length > 0) {
            if (this.uploadedFiles.length === 1) {
                // Une seule image : utiliser le champ "image"
                message.image = this.uploadedFiles[0].data;
            } else {
                // Plusieurs images : utiliser le champ "images"
                message.images = this.uploadedFiles.map(file => file.data);
            }
            
            console.log(`Sending message with ${this.uploadedFiles.length} image(s)`);
            
            // ✅ Vider la liste après envoi
            this.uploadedFiles = [];
        }
        
        // ✅ Envoyer en JSON au lieu de texte brut
        this.ws.send(JSON.stringify(message));
        
        // Create assistant message placeholder for response
        this.currentAssistantDiv = this.addMessage('', 'assistant');
        this.currentResponse = '';
    }

    handleFileUpload(files) {
        for (const file of files) {
            if (file.type.startsWith('image/')) {
                this.processImageFile(file);
            } else {
                // For non-image files, you might want to handle them differently
                console.log('Non-image file uploaded:', file.name);
                // Could show file name in chat or process text files
                this.addMessage(`📄 Uploaded file: ${file.name}`, 'user');
            }
        }
    }

    processImageFile(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const imageData = e.target.result; // Garde le data URL complet !
            
            // Add to uploaded files for API - garder le data URL complet
            this.uploadedFiles.push({
                data: imageData, // ✅ Garder le préfixe data:image/...;base64,
                filename: file.name,
                type: file.type,
                id: this.uploadedFiles.length + 1
            });
            
            // Show image in chat
            const imgElement = `<img src="${imageData}" alt="Uploaded image" style="max-width: 200px; border-radius: 8px; margin: 8px 0;">`;
            this.addMessage(`🖼️ Image uploaded: ${file.name}<br>${imgElement}`, 'user');
            
            console.log('Image uploaded:', file.name, 'Total images:', this.uploadedFiles.length);
        };
        reader.readAsDataURL(file);
    }

    updateConnectionStatus() {
        const status = this.isConnected ? 'Connected' : 'Disconnected';
        const color = this.isConnected ? '#22c55e' : '#ef4444';
        
        // Update send button state
        this.updateSendButton();
        
        // Could add a status indicator in the UI if desired
        console.log(`Connection status: ${status}`);
    }

    updateUIBasedOnCapabilities() {
        if (this.capabilities && this.capabilities.modalities) {
            const modalities = this.capabilities.modalities;
            const inputModalities = modalities.input || [];
            
            // Vérifier si les images sont supportées
            const supportsImages = inputModalities.includes('image') || inputModalities.includes('images');
            const supportsText = inputModalities.includes('text');
            const supportsAudio = inputModalities.includes('audio');
            
            // Afficher/masquer le bouton d'upload selon le support des images
            if (this.fileUploadBtn) {
                if (supportsImages) {
                    this.fileUploadBtn.style.display = 'block';
                    this.fileUploadBtn.title = 'Upload image';
                } else {
                    this.fileUploadBtn.style.display = 'none';
                }
            }
            
            // Mettre à jour le texte d'aide selon les modalités supportées
            if (this.subtitle) {
                let helpText = '';
                const supportedActions = [];
                
                if (supportsText) {
                    supportedActions.push('type a message');
                }
                if (supportsImages) {
                    supportedActions.push('upload images');
                }
                if (supportsAudio) {
                    supportedActions.push('speak');
                }
                
                if (supportedActions.length > 0) {
                    if (supportedActions.length === 1) {
                        helpText = `${supportedActions[0].charAt(0).toUpperCase() + supportedActions[0].slice(1)} to get started`;
                    } else {
                        const lastAction = supportedActions.pop();
                        helpText = `${supportedActions.join(', ').charAt(0).toUpperCase() + supportedActions.join(', ').slice(1)} or ${lastAction} to get started`;
                    }
                } else {
                    helpText = 'Connected to Joshua';
                }
                
                this.subtitle.textContent = helpText;
            }
            
            console.log('UI updated based on capabilities. Text:', supportsText, 'Images:', supportsImages, 'Audio:', supportsAudio);
        } else {
            // Par défaut, cacher le bouton d'upload et afficher texte générique
            if (this.fileUploadBtn) {
                this.fileUploadBtn.style.display = 'none';
            }
            if (this.subtitle) {
                this.subtitle.textContent = 'Type a message to get started';
            }
        }
    }

    updateSendButton() {
        const hasText = this.messageInput.value.trim().length > 0;
        const hasImages = this.uploadedFiles.length > 0;
        const canSend = (hasText || hasImages) && !this.isGenerating && this.isConnected;
        this.sendBtn.disabled = !canSend;
        
        // Update button title based on state
        if (!this.isConnected) {
            this.sendBtn.title = 'Connecting to Joshua...';
        } else if (this.isGenerating) {
            this.sendBtn.title = 'Joshua is responding...';
        } else if (!hasText && !hasImages) {
            this.sendBtn.title = 'Type a message or upload an image to send';
        } else if (hasImages && !hasText) {
            this.sendBtn.title = `Send ${this.uploadedFiles.length} image(s)`;
        } else if (hasText && hasImages) {
            this.sendBtn.title = `Send message with ${this.uploadedFiles.length} image(s)`;
        } else {
            this.sendBtn.title = 'Send message';
        }
        
        // ✅ Mettre à jour le style du bouton si des images sont prêtes
        if (hasImages) {
            this.sendBtn.style.backgroundColor = '#10b981'; // Vert pour indiquer les images
            this.sendBtn.innerHTML = hasText ? '📤' : '🖼️'; // Icône différente
        } else {
            this.sendBtn.style.backgroundColor = ''; // Couleur par défaut
            this.sendBtn.innerHTML = '➤'; // Icône normale
        }
    }

    setGenerating(generating) {
        this.isGenerating = generating;
        this.updateSendButton();
        
        if (generating) {
            this.loading.style.display = 'flex';
        } else {
            this.loading.style.display = 'none';
        }
    }

    scrollToBottom() {
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }

    // Method to stop generation if needed
    stopGeneration() {
        // For WebSocket, we could send a stop signal if the backend supports it
        this.setGenerating(false);
    }

    // Method to configure WebSocket URL
    setWebSocketUrl(url) {
        this.wsUrl = url;
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.close();
        }
        this.connectWebSocket();
    }

    // Method to add a welcome message
    addWelcomeMessage() {
        this.addMessage("👋 Hello! I'm Joshua, your AI assistant powered by Qwen3 VL 8B. Ask me anything!", 'assistant');
    }

    // Cleanup method
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        this.isConnected = false;
        this.updateConnectionStatus();
    }
}

// Initialize the chat when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.joshua = new JoshuaChat();
    
    // Show welcome message after connection is established
    setTimeout(() => {
        if (window.joshua.isConnected) {
            // Ne pas afficher automatiquement le message de bienvenue
            // window.joshua.addWelcomeMessage();
        } else {
            // Wait for connection - no auto welcome message
            const checkConnection = setInterval(() => {
                if (window.joshua.isConnected) {
                    // window.joshua.addWelcomeMessage();
                    clearInterval(checkConnection);
                }
            }, 500);
            
            // Stop checking after 10 seconds
            setTimeout(() => clearInterval(checkConnection), 10000);
        }
    }, 1000);
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (window.joshua) {
        window.joshua.disconnect();
    }
});

// Export for potential external use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = JoshuaChat;
}