from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import os

app = Flask(__name__)

# CORS Configuration with allowlist
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:8080",
            "app://cuttle"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

# Secure cookie configuration
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

@app.route('/api/chat', methods=['POST'])
def chat():
    # Implementation of chat endpoint
    pass

@app.route('/api/auth', methods=['POST'])
def auth():
    # Implementation of auth endpoint
    pass

if __name__ == '__main__':
    app.run(debug=False, port=8080)
