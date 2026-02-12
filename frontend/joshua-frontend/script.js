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
                    this.showCapabilities();
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
        // Handle ASR transcription if needed
        console.log('Transcription:', message.text);
    }

    handleAudioChunk(message) {
        // Handle TTS audio if needed
        console.log('Audio chunk received');
    }

    sendWebSocketMessage(text) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            throw new Error('WebSocket not connected');
        }
        
        // Send text message to WebSocket
        this.ws.send(text);
        
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
            const imageData = e.target.result;
            
            // Add to uploaded files for API
            this.uploadedFiles.push({
                data: imageData.replace(/data:image\/[^;]+;base64,/, ''),
                id: this.uploadedFiles.length + 1
            });
            
            // Show image in chat
            const imgElement = `<img src="${imageData}" alt="Uploaded image" style="max-width: 200px; border-radius: 8px; margin: 8px 0;">`;
            this.addMessage(`🖼️ Image uploaded:<br>${imgElement}`, 'user');
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

    showCapabilities() {
        if (this.capabilities && this.capabilities.modalities) {
            const modalities = this.capabilities.modalities;
            const features = this.capabilities.features || [];
            
            let capText = "🔧 Pipeline capabilities:\n";
            capText += `• Input: ${modalities.input?.join(', ') || 'text'}\n`;
            capText += `• Output: ${modalities.output?.join(', ') || 'text'}\n`;
            capText += `• Features: ${features.join(', ')}`;
            
            this.addMessage(capText, 'assistant');
        }
    }

    updateSendButton() {
        const hasText = this.messageInput.value.trim().length > 0;
        const canSend = hasText && !this.isGenerating && this.isConnected;
        this.sendBtn.disabled = !canSend;
        
        // Update button title based on state
        if (!this.isConnected) {
            this.sendBtn.title = 'Connecting to Joshua...';
        } else if (this.isGenerating) {
            this.sendBtn.title = 'Joshua is responding...';
        } else if (!hasText) {
            this.sendBtn.title = 'Type a message to send';
        } else {
            this.sendBtn.title = 'Send message';
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
            window.joshua.addWelcomeMessage();
        } else {
            // Wait for connection and show message
            const checkConnection = setInterval(() => {
                if (window.joshua.isConnected) {
                    window.joshua.addWelcomeMessage();
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