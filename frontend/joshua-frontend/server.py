#!/usr/bin/env python3
"""
Simple HTTP server for Joshua frontend
Can be used for development or as a proxy to llama.cpp backend
"""

import http.server
import socketserver
import json
import urllib.request
import urllib.parse
from urllib.error import URLError
import threading
import sys
import os

PORT = 8080
LLAMA_BACKEND_URL = "http://localhost:8080"  # Default llama.cpp server URL

class JoshuaHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(__file__), **kwargs)
    
    def do_POST(self):
        """Handle POST requests - proxy to llama.cpp backend"""
        if self.path == '/completion':
            self.handle_completion_request()
        else:
            self.send_error(404, "Not Found")
    
    def handle_completion_request(self):
        """Proxy completion requests to llama.cpp backend"""
        try:
            # Read request data
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            # Forward to llama.cpp backend
            req = urllib.request.Request(
                f"{LLAMA_BACKEND_URL}/completion",
                data=post_data,
                headers={
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream'
                }
            )
            
            try:
                with urllib.request.urlopen(req) as response:
                    # Forward response headers
                    self.send_response(response.getcode())
                    for header, value in response.headers.items():
                        if header.lower() not in ['server', 'date']:
                            self.send_header(header, value)
                    self.end_headers()
                    
                    # Stream response data
                    while True:
                        chunk = response.read(1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        
            except URLError as e:
                # llama.cpp backend not available - send mock response
                print(f"Backend not available ({e}), sending mock response")
                self.send_mock_response(json.loads(post_data.decode()))
                
        except Exception as e:
            print(f"Error handling completion request: {e}")
            self.send_error(500, f"Internal Server Error: {e}")
    
    def send_mock_response(self, request_data):
        """Send a mock response when llama.cpp backend is not available"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        
        # Mock streaming response
        mock_responses = [
            "Hello! I'm Joshua, but I'm currently running in demo mode. ",
            "The llama.cpp backend is not connected, so this is a mock response. ",
            f"Your message was: '{request_data.get('prompt', 'No prompt')}'. ",
            "To connect to a real AI model, please start a llama.cpp server on port 8080 ",
            "or configure the backend URL in the server.py file."
        ]
        
        for i, text in enumerate(mock_responses):
            response = {
                "content": text,
                "stop": i == len(mock_responses) - 1
            }
            
            sse_data = f"data: {json.dumps(response)}\n\n"
            self.wfile.write(sse_data.encode())
            self.wfile.flush()
            
            # Small delay to simulate streaming
            import time
            time.sleep(0.1)
        
        # Send done signal
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
    
    def end_headers(self):
        # Add CORS headers for development
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests"""
        self.send_response(200)
        self.end_headers()

def main():
    # Change working directory to script location
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # Parse command line arguments
    if len(sys.argv) > 1:
        try:
            global PORT
            PORT = int(sys.argv[1])
        except ValueError:
            print(f"Invalid port number: {sys.argv[1]}")
            sys.exit(1)
    
    if len(sys.argv) > 2:
        global LLAMA_BACKEND_URL
        LLAMA_BACKEND_URL = sys.argv[2]
    
    # Start server
    with socketserver.TCPServer(("", PORT), JoshuaHTTPHandler) as httpd:
        print(f"🚀 Joshua frontend server starting on port {PORT}")
        print(f"📡 Backend URL: {LLAMA_BACKEND_URL}")
        print(f"🌐 Open your browser to: http://localhost:{PORT}")
        print("📋 Press Ctrl+C to stop the server")
        print()
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Server stopped")

if __name__ == "__main__":
    main()