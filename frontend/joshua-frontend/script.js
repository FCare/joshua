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
        
        // Authentication
        this.apiBaseUrl = 'https://auth.caronboulme.fr';
        this.isAuthenticated = false;
        this.currentUser = null;
        this.apiKey = null; // API key temporaire pour WebSocket
        
        // Audio properties
        this.audioContext = null;
        this.mediaStream = null;
        this.micProcessor = null;
        this.audioProcessor = null;
        this.inputAnalyser = null;
        this.outputAnalyser = null;
        this.isRecording = false;
        this.isAudioEnabled = false;
        this.isMuted = false; // Par défaut, le son est activé
        this.animationFrames = {
            input: null,
            output: null
        };
        
        this.initElements();
        this.bindEvents();
        this.autoResizeTextarea();
        
        // Initialize mute button state and output visualizer visibility (après initElements)
        this.updateMuteButton();
        this.updateOutputVisualizerVisibility();
        
        // Check authentication before connecting WebSocket
        this.checkAuthentication().then(() => {
            if (this.isAuthenticated) {
                this.fetchWebSocketApiKey().then(() => {
                    this.connectWebSocket();
                });
            } else {
                this.redirectToLogin();
            }
        });
    }

    getWebSocketUrl() {
        // WebSocket direct vers le backend Joshua (bypass Traefik pour WebSocket pur)
        // WebSocket servers purs ne peuvent pas être routés par Traefik
        return `wss://joshua.caronboulme.fr`;
    }

    initElements() {
        this.chatMessages = document.getElementById('chat-messages');
        this.messageInput = document.getElementById('message-input');
        this.sendBtn = document.getElementById('send-btn');
        this.fileUploadBtn = document.getElementById('file-upload-btn');
        this.fileInput = document.getElementById('file-input');
        this.loading = document.getElementById('loading');
        this.subtitle = document.querySelector('.subtitle');
        this.authBtn = document.getElementById('auth-btn');
        this.authText = document.getElementById('auth-text');
        this.logoutBtn = document.getElementById('logout-btn');
        
        // Audio elements
        this.micBtn = document.getElementById('mic-btn');
        this.muteBtn = document.getElementById('mute-btn');
        this.inputVisualizerContainer = document.getElementById('input-visualizer-container');
        this.outputVisualizerContainer = document.getElementById('output-visualizer-container');
        this.inputVisualizer = document.getElementById('input-visualizer');
        this.outputVisualizer = document.getElementById('output-visualizer');
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

        // Auth button
        this.authBtn.addEventListener('click', () => {
            if (this.isAuthenticated) {
                window.location.href = '/profile.html';
            } else {
                this.redirectToLogin();
            }
        });

        // Logout button
        this.logoutBtn.addEventListener('click', () => {
            this.logout();
        });

        // Microphone button
        this.micBtn.addEventListener('click', () => {
            this.toggleAudio();
        });

        // Mute button
        this.muteBtn.addEventListener('click', () => {
            this.toggleMute();
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
        // Construire l'URL WebSocket avec l'API key comme paramètre de query
        if (!this.apiKey) {
            console.error('No API key available for WebSocket connection');
            this.addMessage('Authentication error. Please refresh the page.', 'assistant', true);
            return;
        }
        
        const wsUrl = `${this.getWebSocketUrl()}?api_key=${encodeURIComponent(this.apiKey)}`;
        console.log(`Connecting to WebSocket: ${wsUrl}`);
        
        try {
            this.ws = new WebSocket(wsUrl);
            
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
                    this.handleAudioResponse(message.data);
                    break;
                    
                case 'audio_finished':
                    // Masquer le visualiseur output quand l'audio TTS se termine
                    if (this.outputVisualizerContainer) {
                        this.outputVisualizerContainer.classList.remove('active');
                        setTimeout(() => {
                            this.outputVisualizerContainer.style.display = 'none';
                        }, 300); // Attendre la fin de la transition CSS
                    }
                    break;
                    
                case 'chat_finished':
                    this.setGenerating(false);
                    // Ne pas afficher ce message dans l'interface
                    return; // Sortir immédiatement sans traitement supplémentaire
                    
                default:
                    console.log('Unknown message type:', message.type);
            }
        } catch (error) {
            console.error('Error parsing WebSocket message:', error, data);
        }
    }

    handleChatResponse(message) {
        const text = message.text || message.content || '';
        const metadata = message.metadata || {};
        
        // Filtrer les messages JSON techniques qui ne doivent pas être affichés
        if (this.isSystemMessage(text)) {
            return; // Ne pas afficher les messages système/technique
        }
        
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
    
    isSystemMessage(text) {
        // Détecter les messages JSON techniques qui ne doivent pas être affichés
        if (typeof text !== 'string') return false;
        
        const trimmedText = text.trim();
        
        // Détecter les messages JSON qui commencent par {"type":
        if (trimmedText.startsWith('{"type":')) {
            try {
                const parsed = JSON.parse(trimmedText);
                // Filtrer les messages système spécifiques
                if (parsed.type && ['audio_finished', 'chat_finished', 'audio_chunk'].includes(parsed.type)) {
                    return true;
                }
            } catch (e) {
                // Si ce n'est pas du JSON valide, laisser passer
            }
        }
        
        return false;
    }

    handleTranscription(message) {
        // Handle transcription comme une réponse de chat
        console.log('Transcription:', message.text);
        
        // Traiter comme une réponse de chat pour l'affichage
        this.handleChatResponse(message);
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

    // Authentication methods
    async checkAuthentication() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/verify`, {
                credentials: 'include'
            });
            
            if (response.ok) {
                const data = await response.json();
                this.isAuthenticated = true;
                this.currentUser = data.user;
                this.updateAuthUI();
                console.log('Authentication successful with session cookie for user:', data.user);
                return true;
            } else {
                this.isAuthenticated = false;
                this.currentUser = null;
                this.apiKey = null;
                this.updateAuthUI();
                return false;
            }
        } catch (error) {
            console.error('Authentication check failed:', error);
            this.isAuthenticated = false;
            this.currentUser = null;
            this.apiKey = null;
            this.updateAuthUI();
            return false;
        }
    }

    async fetchWebSocketApiKey() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/auth/session-api-key`, {
                method: 'POST',
                credentials: 'include'
            });
            
            if (response.ok) {
                const data = await response.json();
                this.apiKey = data.api_key;
                console.log(`WebSocket API key obtained (${data.status}), expires: ${data.expires_at}`);
                return true;
            } else {
                console.error('Failed to get WebSocket API key:', response.status);
                return false;
            }
        } catch (error) {
            console.error('Error fetching WebSocket API key:', error);
            return false;
        }
    }

    updateAuthUI() {
        if (this.isAuthenticated && this.currentUser) {
            this.authBtn.style.display = 'flex';
            this.logoutBtn.style.display = 'flex';
            this.authText.textContent = this.currentUser;
            this.subtitle.textContent = `Bienvenue, ${this.currentUser} ! Tapez votre message pour commencer.`;
        } else {
            this.authBtn.style.display = 'none';
            this.logoutBtn.style.display = 'none';
            this.subtitle.textContent = 'Connexion requise pour utiliser Joshua';
        }
    }

    redirectToLogin() {
        window.location.href = '/login.html';
    }

    async logout() {
        try {
            await fetch(`${this.apiBaseUrl}/auth/logout`, {
                credentials: 'include'
            });
        } catch (error) {
            console.error('Logout error:', error);
        } finally {
            this.isAuthenticated = false;
            this.currentUser = null;
            this.apiKey = null; // Effacer l'API key temporaire
            this.disconnect(); // Fermer la connexion WebSocket (méthode existante)
            this.redirectToLogin();
        }
    }

    // ====== AUDIO FUNCTIONALITY ======

    toggleMute() {
        this.isMuted = !this.isMuted;
        this.updateMuteButton();
        this.updateOutputVisualizerVisibility();
    }

    updateMuteButton() {
        const speakerIcon = this.muteBtn.querySelector('.speaker-icon');
        const volumeLines = this.muteBtn.querySelectorAll('.volume-lines');
        const muteXLines = this.muteBtn.querySelectorAll('.mute-x');
        
        if (this.isMuted) {
            // Mode mute : cacher le speaker et les lignes de volume, afficher les X
            speakerIcon.style.display = 'block';
            volumeLines.forEach(line => line.style.display = 'block');
            muteXLines.forEach(line => line.style.display = 'block');
            this.muteBtn.style.color = '#ef4444'; // Rouge pour mute
            this.muteBtn.title = 'Unmute Joshua\'s voice';
        } else {
            // Mode unmute : afficher le speaker et les lignes de volume, cacher les X
            speakerIcon.style.display = 'block';
            volumeLines.forEach(line => line.style.display = 'block');
            muteXLines.forEach(line => line.style.display = 'none');
            this.muteBtn.style.color = ''; // Couleur normale
            this.muteBtn.title = 'Mute Joshua\'s voice';
        }
    }

    updateOutputVisualizerVisibility() {
        console.log('👁️ [VIZ DEBUG] updateOutputVisualizerVisibility called');
        console.log('👁️ [VIZ DEBUG] State:', {
            isMuted: this.isMuted,
            outputVisualizerContainer: !!this.outputVisualizerContainer,
            outputAnalyser: !!this.outputAnalyser,
            animationFrameOutput: !!this.animationFrames.output
        });
        
        // Contrôle uniquement la VISIBILITÉ de la zone selon le mute
        // La zone doit être visible dès qu'on est unmuted
        if (this.outputVisualizerContainer) {
            console.log('👁️ [VIZ DEBUG] Output visualizer container found');
            if (this.isMuted) {
                // Mute : cacher la zone (mais l'animation continue)
                this.outputVisualizerContainer.style.display = 'none';
                this.outputVisualizerContainer.classList.remove('active');
                console.log('🔇 [VIZ DEBUG] Output visualizer hidden - muted');
            } else {
                // Unmute : afficher la zone (même sans audio actuel)
                this.outputVisualizerContainer.style.display = 'block';
                this.outputVisualizerContainer.classList.add('active');
                console.log('🔊 [VIZ DEBUG] Output visualizer shown - unmuted');
                
                // Démarrer l'animation si on a un analyser
                if (this.outputAnalyser && !this.animationFrames.output) {
                    console.log('👁️ [VIZ DEBUG] Starting output visualization from updateOutputVisualizerVisibility');
                    this.startAudioVisualization();
                }
            }
        } else {
            console.log('👁️ [VIZ DEBUG] No output visualizer container found');
        }
    }

    async toggleAudio() {
        if (!this.isAudioEnabled) {
            await this.initAudio();
            // Démarrer l'enregistrement automatiquement après l'initialisation
            if (this.isAudioEnabled) {
                this.startRecording();
            }
        } else {
            if (this.isRecording) {
                this.stopRecording();
            } else {
                this.startRecording();
            }
        }
    }

    async initAudio() {
        try {
            console.log('🎙️ Initializing audio...');
            
            // Check if we're in a secure context
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error('getUserMedia not available. Please use HTTPS.');
            }

            // Check current permission state
            let permissionStatus = null;
            try {
                permissionStatus = await navigator.permissions.query({name: 'microphone'});
                console.log('🔒 Microphone permission status:', permissionStatus.state);
                
                if (permissionStatus.state === 'denied') {
                    throw new Error('Microphone permission denied. Please enable it in browser settings and reload the page.');
                }
            } catch (permError) {
                console.log('Permission API not available, proceeding with getUserMedia...');
            }
            
            // Request microphone permission with simplified constraints first
            console.log('📞 Requesting microphone access...');
            try {
                this.mediaStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true
                    }
                });
            } catch (getUserMediaError) {
                // Retry with minimal constraints
                console.log('🔄 Retrying with minimal audio constraints...');
                this.mediaStream = await navigator.mediaDevices.getUserMedia({
                    audio: true
                });
            }

            console.log('✅ Microphone access granted');

            // Create AudioContext with 24kHz sample rate for TTS compatibility
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 24000  // Match TTS sample rate to avoid pitch/speed issues
            });

            // Resume AudioContext if needed (browser policy)
            if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
            }

            // Load AudioWorklet modules
            console.log('🔧 Loading AudioWorklet modules...');
            try {
                await this.audioContext.audioWorklet.addModule('./joshua-mic-processor.js');
                console.log('✅ joshua-mic-processor.js loaded');
            } catch (modError) {
                console.error('❌ Failed to load joshua-mic-processor.js:', modError);
                throw new Error(`Failed to load microphone processor: ${modError.message}`);
            }
            
            try {
                await this.audioContext.audioWorklet.addModule('./joshua-audio-processor.js');
                console.log('✅ joshua-audio-processor.js loaded');
            } catch (modError) {
                console.error('❌ Failed to load joshua-audio-processor.js:', modError);
                throw new Error(`Failed to load audio processor: ${modError.message}`);
            }

            // Setup microphone input
            await this.setupMicrophoneInput();
            
            // Setup audio output
            await this.setupAudioOutput();
            
            // Setup audio analysis and visualization
            this.setupAudioAnalysis();

            this.isAudioEnabled = true;
            // Ne pas afficher les visualiseurs lors de l'initialisation
            console.log('🎙️ Audio initialized successfully');
        } catch (error) {
            console.error('❌ Audio initialization failed:', error);
            
            let errorMessage = 'Microphone access required for voice input.';
            
            if (error.name === 'NotAllowedError') {
                errorMessage = 'Microphone permission was denied. Please:\n\n' +
                             '1. Click the microphone icon in your browser\'s address bar\n' +
                             '2. Select "Always allow" for microphone access\n' +
                             '3. Refresh the page and try again\n\n' +
                             'Or check your browser settings to enable microphone access for this site.';
            } else if (error.name === 'NotFoundError') {
                errorMessage = 'No microphone found. Please connect a microphone and try again.';
            } else if (error.name === 'NotSupportedError') {
                errorMessage = 'Microphone not supported. Please use a modern browser with HTTPS.';
            } else if (error.message.includes('HTTPS')) {
                errorMessage = 'Microphone access requires HTTPS. Please access the site via https://';
            }
            
            console.error('🎙️ Audio initialization error:', errorMessage);
            
            // Afficher l'erreur de façon discrète dans le titre du bouton micro
            this.micBtn.title = errorMessage.split('\n')[0]; // Première ligne seulement
            this.micBtn.style.color = '#ef4444'; // Rouge pour indiquer l'erreur
        }
    }

    async setupMicrophoneInput() {
        // Create microphone source
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);
        
        // Create microphone processor
        this.micProcessor = new AudioWorkletNode(this.audioContext, 'joshua-mic-processor');
        
        // Connect source to processor
        source.connect(this.micProcessor);
        
        // Listen for audio chunks
        this.micProcessor.port.onmessage = (event) => {
            if (event.data.type === 'audioChunk') {
                this.handleAudioChunk(event.data);
            }
        };
    }

    async setupAudioOutput() {
        // Create audio output processor
        this.audioProcessor = new AudioWorkletNode(this.audioContext, 'joshua-audio-processor');
        
        // Connect to output analyser if available, otherwise directly to destination
        if (this.outputAnalyser) {
            this.audioProcessor.connect(this.outputAnalyser);
            this.outputAnalyser.connect(this.audioContext.destination);
        } else {
            this.audioProcessor.connect(this.audioContext.destination);
        }
    }

    async setupAudioOutputOnly() {
        console.log('🔊 [AUDIO DEBUG] setupAudioOutputOnly called');
        // Create audio output processor for TTS playback only (no microphone)
        this.audioProcessor = new AudioWorkletNode(this.audioContext, 'joshua-audio-processor');
        console.log('🔊 [AUDIO DEBUG] AudioWorkletNode created:', !!this.audioProcessor);
        
        // Create minimal output analyser for TTS
        this.outputAnalyser = this.audioContext.createAnalyser();
        this.outputAnalyser.fftSize = 256;
        this.outputAnalyser.smoothingTimeConstant = 0.8;
        console.log('🔊 [AUDIO DEBUG] Output analyser created with fftSize:', this.outputAnalyser.fftSize);
        
        // Créer un GainNode intermédiaire pour s'assurer que l'audio passe par le graph
        this.outputGain = this.audioContext.createGain();
        this.outputGain.gain.value = 1.0;
        console.log('🔊 [AUDIO DEBUG] Output gain created with value:', this.outputGain.gain.value);
        
        // Connect: audioProcessor -> outputGain -> outputAnalyser -> destination
        this.audioProcessor.connect(this.outputGain);
        console.log('🔊 [AUDIO DEBUG] audioProcessor connected to outputGain');
        this.outputGain.connect(this.outputAnalyser);
        console.log('🔊 [AUDIO DEBUG] outputGain connected to outputAnalyser');
        this.outputAnalyser.connect(this.audioContext.destination);
        console.log('🔊 [AUDIO DEBUG] outputAnalyser connected to destination');
        
        // Ajouter un oscillateur silencieux pour "priming" le graph audio
        this.primeOscillator = this.audioContext.createOscillator();
        this.primeGain = this.audioContext.createGain();
        this.primeGain.gain.value = 0; // Silencieux
        this.primeOscillator.connect(this.primeGain);
        this.primeGain.connect(this.audioProcessor);
        this.primeOscillator.start();
        console.log('🔊 [AUDIO DEBUG] Priming oscillator started (silent)');
        
        console.log('🔊 Audio output setup completed for TTS with primed graph');
    }

    setupAudioAnalysis() {
        // Create analyser nodes for visualization
        this.inputAnalyser = this.audioContext.createAnalyser();
        this.inputAnalyser.fftSize = 256;
        this.inputAnalyser.smoothingTimeConstant = 0.8;
        
        this.outputAnalyser = this.audioContext.createAnalyser();
        this.outputAnalyser.fftSize = 256;
        this.outputAnalyser.smoothingTimeConstant = 0.8;
        
        // Connect input stream to analyser
        const source = this.audioContext.createMediaStreamSource(this.mediaStream);
        source.connect(this.inputAnalyser);
        
        // Setup output analyser connection
        if (this.audioProcessor) {
            this.audioProcessor.disconnect();
            this.audioProcessor.connect(this.outputAnalyser);
            this.outputAnalyser.connect(this.audioContext.destination);
        }
        
        // Start visualization
        this.startAudioVisualization();
    }


    startRecording() {
        if (!this.audioContext) {
            console.warn('Audio not initialized');
            return;
        }

        this.isRecording = true;
        this.micBtn.classList.add('recording');
        
        // Afficher les visualiseurs quand l'enregistrement commence
        this.inputVisualizerContainer.style.display = 'block';
        this.outputVisualizerContainer.style.display = 'block';
        this.inputVisualizerContainer.classList.add('active');
        this.outputVisualizerContainer.classList.add('active');
        
        // Start recording in microphone processor
        this.micProcessor.port.postMessage({ command: 'start' });
        
        console.log('🎙️ Recording started');
    }

    stopRecording() {
        if (!this.isRecording) return;
        
        this.isRecording = false;
        this.micBtn.classList.remove('recording');
        
        // Masquer les visualiseurs quand l'enregistrement s'arrête
        this.inputVisualizerContainer.classList.remove('active');
        this.outputVisualizerContainer.classList.remove('active');
        setTimeout(() => {
            this.inputVisualizerContainer.style.display = 'none';
            this.outputVisualizerContainer.style.display = 'none';
        }, 300); // Attendre la fin de la transition CSS
        
        // Stop recording in microphone processor
        if (this.micProcessor) {
            this.micProcessor.port.postMessage({ command: 'stop' });
        }
        
        // Libérer complètement le microphone et nettoyer
        this.cleanup();
        
        console.log('🎙️ Recording stopped and microphone fully released');
    }

    handleAudioChunk(chunkData) {
        if (!this.ws || !this.isConnected) {
            console.warn('WebSocket not connected, cannot send audio');
            return;
        }

        // Convert float32 PCM to base64 for WebSocket transmission
        const pcmInt16 = new Int16Array(chunkData.data.length);
        for (let i = 0; i < chunkData.data.length; i++) {
            pcmInt16[i] = Math.max(-32768, Math.min(32767, chunkData.data[i] * 32767));
        }

        const audioData = new Uint8Array(pcmInt16.buffer);
        const audioBase64 = btoa(String.fromCharCode.apply(null, audioData));

        // Send audio chunk to Joshua backend
        const audioMessage = {
            type: 'audio',
            data: audioBase64,
            metadata: {
                format: 'pcm16',
                sample_rate: chunkData.sampleRate,
                duration: chunkData.duration,
                samples: chunkData.samples
            }
        };

        this.ws.send(JSON.stringify(audioMessage));
    }

    async handleAudioResponse(audioData) {
        console.log('🎵 [AUDIO DEBUG] handleAudioResponse called with:', typeof audioData, audioData.length || 'no length');
        console.log('🎵 [AUDIO DEBUG] Is muted:', this.isMuted);
        console.log('🎵 [AUDIO DEBUG] AudioProcessor exists:', !!this.audioProcessor);
        console.log('🎵 [AUDIO DEBUG] AudioContext exists:', !!this.audioContext);
        console.log('🎵 [AUDIO DEBUG] OutputAnalyser exists:', !!this.outputAnalyser);
        console.log('🎵 [AUDIO DEBUG] OutputVisualizerContainer exists:', !!this.outputVisualizerContainer);
        console.log('🎵 [AUDIO DEBUG] OutputVisualizerContainer display:', this.outputVisualizerContainer?.style?.display);
        
        // Si le micro est actif, désactiver automatiquement le mute pour éviter le feedback
        if (this.isRecording && this.isMuted) {
            this.isMuted = false;
            this.updateMuteButton();
            console.log('🎵 [AUDIO DEBUG] Auto-unmuted due to recording');
        }
        
        // Si c'est muted, ne pas jouer l'audio et ne pas afficher le visualiseur
        if (this.isMuted) {
            console.log('🎵 [AUDIO DEBUG] Skipping audio processing - muted');
            return;
        }
        
        // Initialize audio output automatically if not already done
        if (!this.audioProcessor || !this.audioContext) {
            console.log('🎵 [AUDIO DEBUG] Initializing audio output for TTS...');
            try {
                // Initialize AudioContext with 24kHz sample rate for TTS compatibility
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                    sampleRate: 24000  // Match TTS sample rate to avoid pitch/speed issues
                });
                console.log('🎵 [AUDIO DEBUG] AudioContext created with sample rate:', this.audioContext.sampleRate);
                
                // Resume AudioContext if needed (browser policy)
                if (this.audioContext.state === 'suspended') {
                    await this.audioContext.resume();
                    console.log('🎵 [AUDIO DEBUG] AudioContext resumed');
                }
                
                // Load audio processor module if not already loaded
                try {
                    await this.audioContext.audioWorklet.addModule('./joshua-audio-processor.js');
                    console.log('🎵 [AUDIO DEBUG] AudioWorklet module loaded successfully');
                } catch (modError) {
                    console.log('🎵 [AUDIO DEBUG] AudioWorklet module load error (might already be loaded):', modError);
                    // Module might already be loaded, continue
                }
                
                // Setup audio output for TTS
                await this.setupAudioOutputOnly();
                console.log('🎵 [AUDIO DEBUG] Audio output setup completed');
            } catch (error) {
                console.error('🎵 [AUDIO DEBUG] Failed to initialize audio output for TTS:', error);
                return;
            }
        }

        // Afficher le visualiseur output quand on reçoit de l'audio TTS (si pas muted)
        if (this.outputVisualizerContainer && !this.isMuted) {
            this.outputVisualizerContainer.style.display = 'block';
            this.outputVisualizerContainer.classList.add('active');
            console.log('🎵 [AUDIO DEBUG] Output visualizer container shown and activated');
        }

        // Start visualization if not already running
        if (!this.animationFrames.output && this.outputAnalyser) {
            console.log('🎵 [AUDIO DEBUG] Starting output visualization animation');
            this.startAudioVisualization();
        }

        try {
            // Decode base64 audio data
            const binaryString = atob(audioData);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            console.log('🎵 [AUDIO DEBUG] Decoded audio bytes length:', bytes.length);

            // Convert to Float32Array for AudioWorklet
            const int16Array = new Int16Array(bytes.buffer);
            const float32Array = new Float32Array(int16Array.length);
            for (let i = 0; i < int16Array.length; i++) {
                float32Array[i] = int16Array[i] / 32767.0;
            }
            console.log('🎵 [AUDIO DEBUG] Converted to float32 samples:', float32Array.length);
            console.log('🎵 [AUDIO DEBUG] Sample values (first 10):', Array.from(float32Array.slice(0, 10)));

            // Send to audio processor
            this.audioProcessor.port.postMessage({
                type: 'audio',
                frame: float32Array
            });
            console.log('🎵 [AUDIO DEBUG] Audio frame sent to AudioWorklet processor');

            // Check if analyser is getting data
            if (this.outputAnalyser) {
                setTimeout(() => {
                    const dataArray = new Uint8Array(this.outputAnalyser.frequencyBinCount);
                    this.outputAnalyser.getByteFrequencyData(dataArray);
                    const maxValue = Math.max(...dataArray);
                    console.log('🎵 [AUDIO DEBUG] Output analyser frequency data max value:', maxValue);
                    console.log('🎵 [AUDIO DEBUG] Output analyser first 10 values:', Array.from(dataArray.slice(0, 10)));
                }, 100); // Check after 100ms
            }
            
        } catch (error) {
            console.error('🎵 [AUDIO DEBUG] Error processing audio response:', error);
        }
    }

    startAudioVisualization() {
        const drawInput = () => {
            if (!this.inputAnalyser || !this.inputVisualizer) {
                this.animationFrames.input = requestAnimationFrame(drawInput);
                return;
            }

            const canvas = this.inputVisualizer;
            const ctx = canvas.getContext('2d');
            const bufferLength = this.inputAnalyser.frequencyBinCount;
            const dataArray = new Uint8Array(bufferLength);
            
            this.inputAnalyser.getByteFrequencyData(dataArray);

            // Clear canvas with transparent background
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            // Draw 16 bars covering 0-10kHz basé sur le sample rate réel de l'AudioContext
            const barCount = 16;
            const nyquistFreq = this.audioContext.sampleRate / 2; // Fréquence de Nyquist
            const maxFreqBins = Math.floor(bufferLength * (10000 / nyquistFreq)); // Bins pour 0-10kHz
            const barWidth = canvas.width / barCount;
            const dataStep = Math.floor(maxFreqBins / barCount);

            for (let i = 0; i < barCount; i++) {
                const dataIndex = i * dataStep;
                let barHeight = (dataArray[dataIndex] / 255) * canvas.height;
                barHeight = Math.max(1, barHeight);

                // Transparency based on intensity: low level = low opacity, high level = opaque
                const intensity = barHeight / canvas.height;
                const opacity = Math.max(0.1, intensity); // minimum 0.1 opacity
                
                // Gray bars with varying opacity
                ctx.fillStyle = `rgba(120, 120, 120, ${opacity})`;
                
                const x = i * barWidth + 1;
                const barWidthAdjusted = barWidth - 2;
                ctx.fillRect(x, canvas.height - barHeight, barWidthAdjusted, barHeight);
            }

            this.animationFrames.input = requestAnimationFrame(drawInput);
        };

        const drawOutput = () => {
            if (!this.outputAnalyser || !this.outputVisualizer) {
                console.log('🎨 [VIZ DEBUG] drawOutput - missing components:', {
                    outputAnalyser: !!this.outputAnalyser,
                    outputVisualizer: !!this.outputVisualizer
                });
                this.animationFrames.output = requestAnimationFrame(drawOutput);
                return;
            }

            const canvas = this.outputVisualizer;
            const ctx = canvas.getContext('2d');
            const bufferLength = this.outputAnalyser.frequencyBinCount;
            const dataArray = new Uint8Array(bufferLength);
            
            this.outputAnalyser.getByteFrequencyData(dataArray);
            
            // Debug frequency data
            const maxValue = Math.max(...dataArray);
            const avgValue = dataArray.reduce((sum, val) => sum + val, 0) / dataArray.length;
            if (maxValue > 0) { // Only log when there's actual audio data
                console.log('🎨 [VIZ DEBUG] Output frequency data - Max:', maxValue, 'Avg:', avgValue.toFixed(2));
            }

            // Clear canvas with transparent background
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            // Draw 16 bars covering 0-10kHz basé sur le sample rate réel de l'AudioContext
            const barCount = 16;
            const nyquistFreq = this.audioContext.sampleRate / 2; // Fréquence de Nyquist
            const maxFreqBins = Math.floor(bufferLength * (10000 / nyquistFreq)); // Bins pour 0-10kHz
            const barWidth = canvas.width / barCount;
            const dataStep = Math.floor(maxFreqBins / barCount);

            let drawnBars = 0;
            for (let i = 0; i < barCount; i++) {
                const dataIndex = i * dataStep;
                let barHeight = (dataArray[dataIndex] / 255) * canvas.height;
                barHeight = Math.max(1, barHeight);

                // Transparency based on intensity: low level = low opacity, high level = opaque
                const intensity = barHeight / canvas.height;
                const opacity = Math.max(0.1, intensity); // minimum 0.1 opacity
                
                // Gray bars with varying opacity
                ctx.fillStyle = `rgba(120, 120, 120, ${opacity})`;
                
                const x = i * barWidth + 1;
                const barWidthAdjusted = barWidth - 2;
                ctx.fillRect(x, canvas.height - barHeight, barWidthAdjusted, barHeight);
                
                if (barHeight > 1) drawnBars++;
            }

            if (drawnBars > 0) {
                console.log('🎨 [VIZ DEBUG] Drew', drawnBars, 'active bars out of', barCount);
            }

            this.animationFrames.output = requestAnimationFrame(drawOutput);
        };

        console.log('🎨 Starting audio visualizations...');
        drawInput();
        drawOutput();
    }

    stopAudioVisualization() {
        if (this.animationFrames.input) {
            cancelAnimationFrame(this.animationFrames.input);
            this.animationFrames.input = null;
        }
        if (this.animationFrames.output) {
            cancelAnimationFrame(this.animationFrames.output);
            this.animationFrames.output = null;
        }
    }

    cleanup() {
        // Stop visualizations
        this.stopAudioVisualization();
        
        // Disconnect and clear AudioNodes references
        if (this.micProcessor) {
            this.micProcessor.disconnect();
            this.micProcessor = null;
        }
        
        if (this.audioProcessor) {
            this.audioProcessor.disconnect();
            this.audioProcessor = null;
        }
        
        if (this.inputAnalyser) {
            this.inputAnalyser.disconnect();
            this.inputAnalyser = null;
        }
        
        if (this.outputAnalyser) {
            this.outputAnalyser.disconnect();
            this.outputAnalyser = null;
        }
        
        // Close audio context
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        
        // Stop media stream
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }
        
        // Reset audio state
        this.isAudioEnabled = false;
        this.isRecording = false;
        
        // Reset visual elements
        if (this.inputVisualizerContainer) {
            this.inputVisualizerContainer.style.display = 'none';
            this.inputVisualizerContainer.classList.remove('active');
        }
        if (this.outputVisualizerContainer) {
            this.outputVisualizerContainer.style.display = 'none';
            this.outputVisualizerContainer.classList.remove('active');
        }
        
        // Reset microphone button to inactive state and color
        this.micBtn.classList.remove('recording', 'listening');
        this.micBtn.style.color = ''; // Reset color
        this.micBtn.title = 'Voice input'; // Reset title
        
        console.log('🎙️ Audio cleanup completed');
    }

    // Méthode pour cleanup complet lors de la fermeture de la page ou reset
    fullCleanup() {
        this.cleanup();
        console.log('🎙️ Full audio cleanup - microphone access released');
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