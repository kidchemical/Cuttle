"""
Shared Flask-Limiter instance.

Imported by both web_chat_api.py (which calls limiter.init_app(app)) and
auth_api.py (which uses @limiter.limit decorators on blueprint routes).
Keeping this in a separate module breaks the circular-import that would occur
if auth_api.py tried to import the limiter from web_chat_api.py.

Explicit storage_uri="memory://" silences the "no storage specified" warning.
For production, set RATELIMIT_STORAGE_URI to redis://... or similar in app config.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
