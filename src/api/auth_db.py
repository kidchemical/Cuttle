#!/usr/bin/env python3
"""
Database models and management for user authentication and chat sessions
"""

import sqlite3
import hashlib
import secrets
import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone

# Lazy: read-only agent tools (python -m api.chat_cli) must not require bcrypt
# just to open the SQLite transcript store. Password hashing still needs it.


def _bcrypt_mod():
    try:
        import bcrypt as _bcrypt
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "bcrypt is required for password hashing. "
            "Use Cuttle's venv: .venv\\Scripts\\python.exe"
        ) from e
    return _bcrypt

from core.runtime_paths import data_db_dir

_CHAT_HANDLE_RE = re.compile(r'^(?:ch[-\s]?)?0*(\d+)$', re.IGNORECASE)

# Database path
DB_PATH = data_db_dir() / 'cuttle_auth.db'

class AuthDatabase:
    """Manages user authentication and chat session data"""
    
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()
    
    def _init_database(self):
        """Initialize database tables"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                display_name TEXT,
                auth_provider TEXT NOT NULL,
                provider_user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                profile_image TEXT
            )
        ''')
        
        # Auth sessions table (for login sessions/tokens)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_token TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        
        # Chat sessions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        
        # Chat messages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,
                FOREIGN KEY (chat_session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
        ''')
        
        # OAuth state table (for OAuth flow)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS oauth_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_token TEXT UNIQUE NOT NULL,
                provider TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                link_user_id INTEGER
            )
        ''')
        try:
            cursor.execute('ALTER TABLE oauth_states ADD COLUMN link_user_id INTEGER')
        except sqlite3.OperationalError:
            pass  # column already exists
        
        # Create indexes for performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(session_token)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(chat_session_id)')

        # Auto-naming: 1 = name was set automatically (safe to overwrite), 0 = user-chosen
        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN name_auto INTEGER DEFAULT 1')
        except sqlite3.OperationalError:
            pass  # column already exists

        # Favorites: starred chats float to the top of each history category
        try:
            cursor.execute('ALTER TABLE chat_sessions ADD COLUMN starred INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass  # column already exists

        # Per-chat working project (cwd for /cursor, /antigravity, etc.)
        for col_sql in (
            'ALTER TABLE chat_sessions ADD COLUMN project_id INTEGER',
            'ALTER TABLE chat_sessions ADD COLUMN project_name TEXT',
            'ALTER TABLE chat_sessions ADD COLUMN project_path TEXT',
        ):
            try:
                cursor.execute(col_sql)
            except sqlite3.OperationalError:
                pass  # column already exists

        # Discord (and later Telegram) bridges: one CH session per remote identity.
        for col_sql in (
            "ALTER TABLE chat_sessions ADD COLUMN origin TEXT DEFAULT 'web'",
            'ALTER TABLE chat_sessions ADD COLUMN discord_user_id TEXT',
            'ALTER TABLE chat_sessions ADD COLUMN discord_channel_id TEXT',
            'ALTER TABLE chat_sessions ADD COLUMN discord_username TEXT',
            'ALTER TABLE chat_sessions ADD COLUMN followup_queue TEXT',
        ):
            try:
                cursor.execute(col_sql)
            except sqlite3.OperationalError:
                pass
        cursor.execute(
            '''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_discord_dm
            ON chat_sessions(discord_user_id)
            WHERE origin = 'discord_dm' AND is_active = 1 AND discord_user_id IS NOT NULL
            '''
        )
        cursor.execute(
            '''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_discord_guild
            ON chat_sessions(discord_user_id, discord_channel_id)
            WHERE origin = 'discord_guild' AND is_active = 1
              AND discord_user_id IS NOT NULL AND discord_channel_id IS NOT NULL
            '''
        )

        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS chat_session_discord_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_session_id INTEGER NOT NULL,
                discord_user_id TEXT NOT NULL UNIQUE,
                discord_username TEXT,
                discord_channel_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
            '''
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_discord_links_session '
            'ON chat_session_discord_links(chat_session_id)'
        )

        # Sub-agent child chats: real chat_sessions rows nested under a parent.
        try:
            cursor.execute(
                'ALTER TABLE chat_sessions ADD COLUMN parent_session_id INTEGER'
            )
        except sqlite3.OperationalError:
            pass
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_chat_sessions_parent '
            'ON chat_sessions(parent_session_id)'
        )
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS subagent_batches (
                id TEXT PRIMARY KEY,
                parent_session_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                collect TEXT NOT NULL DEFAULT 'all',
                lifetime TEXT NOT NULL DEFAULT 'one_shot',
                status TEXT NOT NULL DEFAULT 'running',
                watch_id TEXT,
                widget_id TEXT,
                attach_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                metadata TEXT,
                FOREIGN KEY (parent_session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
            '''
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_subagent_batches_parent '
            'ON subagent_batches(parent_session_id)'
        )
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS subagent_children (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                session_id INTEGER NOT NULL,
                sort_index INTEGER NOT NULL DEFAULT 0,
                label TEXT,
                agent TEXT,
                model TEXT,
                effort TEXT,
                prompt TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                error TEXT,
                pid INTEGER,
                owner_pid INTEGER,
                query_id TEXT,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                FOREIGN KEY (batch_id) REFERENCES subagent_batches(id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
            '''
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_subagent_children_batch '
            'ON subagent_children(batch_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_subagent_children_session '
            'ON subagent_children(session_id)'
        )
        for col_sql in (
            'ALTER TABLE chat_sessions ADD COLUMN display_name TEXT',
            'ALTER TABLE chat_sessions ADD COLUMN avatar TEXT',
            'ALTER TABLE chat_sessions ADD COLUMN agent_profile_id TEXT',
            'ALTER TABLE subagent_children ADD COLUMN profile_id TEXT',
            'ALTER TABLE subagent_children ADD COLUMN display_name TEXT',
            'ALTER TABLE subagent_children ADD COLUMN avatar TEXT',
            # owner_pid: process running the child turn, so an orphaned 'running'
            # row (host killed mid-turn) can be reconciled instead of spinning
            # forever. NOT the `pid` column: that one is SIGTERMed on cancel and
            # must never name a Cuttle host process.
            'ALTER TABLE subagent_children ADD COLUMN owner_pid INTEGER',
            # query_id: the harness query log for this turn, captured at
            # query_started so a child pane can inspect a live sub-agent run.
            'ALTER TABLE subagent_children ADD COLUMN query_id TEXT',
            'ALTER TABLE subagent_children ADD COLUMN live_status TEXT',
            'ALTER TABLE subagent_children ADD COLUMN live_status_at TEXT',
            'ALTER TABLE subagent_children ADD COLUMN live_turn_id TEXT',
        ):
            try:
                cursor.execute(col_sql)
            except sqlite3.OperationalError:
                pass
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_profiles (
                id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                avatar TEXT,
                agent TEXT,
                model TEXT,
                effort TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            '''
        )

        # Durable chat widgets (Tasks, …) — session- and/or project-scoped
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS chat_widgets (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                title TEXT,
                description TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT 'session',
                session_id INTEGER,
                project_path TEXT,
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                edit_mode TEXT NOT NULL DEFAULT 'agent',
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            '''
        )
        try:
            cursor.execute(
                "ALTER TABLE chat_widgets ADD COLUMN edit_mode TEXT NOT NULL DEFAULT 'agent'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                "ALTER TABLE chat_widgets ADD COLUMN description TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_chat_widgets_user_status '
            'ON chat_widgets(user_id, status)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_chat_widgets_session '
            'ON chat_widgets(session_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_chat_widgets_project '
            'ON chat_widgets(user_id, project_path)'
        )

        # Local username accounts (email remains for OAuth / legacy)
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN username TEXT')
        except sqlite3.OperationalError:
            pass  # column already exists
        cursor.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username '
            'ON users(username) WHERE username IS NOT NULL'
        )
        
        conn.commit()
        conn.close()
    
    def _get_connection(self):
        """Get database connection (WAL + busy_timeout to survive lock storms)."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        return conn
    
    # ==================== User Management ====================

    def layout_test_accounts(self) -> List[Dict[str, Any]]:
        """Inventory the exact usernames emitted by the old Apps layout tests.

        No credentials or message contents leave this maintenance interface.
        """
        conn = self._get_connection()
        try:
            rows = conn.execute('''
                SELECT u.id, u.username, u.email, u.auth_provider,
                       u.provider_user_id, u.is_active,
                       (SELECT COUNT(*) FROM chat_messages m JOIN chat_sessions s
                        ON s.id = m.chat_session_id WHERE s.user_id = u.id) AS messages,
                       (SELECT COUNT(*) FROM chat_widgets w WHERE w.user_id = u.id) AS widgets,
                       (SELECT COUNT(*) FROM subagent_batches b WHERE b.user_id = u.id) AS batches
                FROM users u ORDER BY u.id
            ''').fetchall()
            return [dict(row) for row in rows
                    if re.fullmatch(r'(?:lo|lp)_[0-9a-f]{10}', row['username'] or '')]
        finally:
            conn.close()

    def retire_empty_layout_test_accounts(self, user_ids: List[int]) -> List[int]:
        """Reversibly disable confirmed empty layout fixtures and revoke login tokens.

        Recheck identity/content under a write transaction, so an audit followed
        by new user activity cannot retire a now-used account. No user/chat rows
        are deleted. The oldest active account is always protected.
        """
        conn = self._get_connection()
        retired = []
        try:
            conn.execute('BEGIN IMMEDIATE')
            oldest = conn.execute('SELECT MIN(id) FROM users WHERE is_active = 1').fetchone()[0]
            for uid in set(map(int, user_ids)):
                row = conn.execute('SELECT * FROM users WHERE id = ?', (uid,)).fetchone()
                if not row or uid == oldest or not row['is_active']:
                    continue
                name = row['username'] or ''
                if (not re.fullmatch(r'(?:lo|lp)_[0-9a-f]{10}', name)
                        or row['auth_provider'] != 'local' or row['provider_user_id']
                        or row['email'] != name + '@local'):
                    continue
                used = conn.execute('''
                    SELECT EXISTS(SELECT 1 FROM chat_messages m JOIN chat_sessions s
                                  ON s.id = m.chat_session_id WHERE s.user_id = ?)
                         OR EXISTS(SELECT 1 FROM chat_widgets WHERE user_id = ?)
                         OR EXISTS(SELECT 1 FROM subagent_batches WHERE user_id = ?)
                ''', (uid, uid, uid)).fetchone()[0]
                if used:
                    continue
                conn.execute('UPDATE users SET is_active = 0 WHERE id = ?', (uid,))
                conn.execute('DELETE FROM auth_sessions WHERE user_id = ?', (uid,))
                retired.append(uid)
            conn.commit()
            return sorted(retired)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def restore_layout_test_account(self, user_id: int) -> bool:
        """Undo retirement without restoring previously revoked login tokens."""
        conn = self._get_connection()
        try:
            row = conn.execute('SELECT * FROM users WHERE id = ?', (int(user_id),)).fetchone()
            if (not row or not re.fullmatch(r'(?:lo|lp)_[0-9a-f]{10}', row['username'] or '')
                    or row['auth_provider'] != 'local'
                    or row['email'] != row['username'] + '@local' or row['provider_user_id']):
                return False
            conn.execute('UPDATE users SET is_active = 1 WHERE id = ?', (int(user_id),))
            conn.commit()
            return True
        finally:
            conn.close()
    
    def create_user(self, email: str, display_name: str, auth_provider: str,
                   password: Optional[str] = None, provider_user_id: Optional[str] = None,
                   profile_image: Optional[str] = None,
                   username: Optional[str] = None) -> Optional[int]:
        """Create a new user"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            password_hash = None
            if password:
                password_hash = self._hash_password(password)

            uname = (username or '').strip().lower() or None
            
            cursor.execute('''
                INSERT INTO users (email, password_hash, display_name, auth_provider, 
                                 provider_user_id, profile_image, username)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (email, password_hash, display_name, auth_provider, provider_user_id, profile_image, uname))
            
            user_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            # Create default chat session for new user
            self.create_chat_session(user_id, "Default Chat")
            
            return user_id
        except sqlite3.IntegrityError:
            return None  # User already exists
        except Exception as e:
            print(f"Error creating user: {e}")
            return None
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by local username (case-insensitive)."""
        uname = (username or '').strip().lower()
        if not uname:
            return None
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE lower(username) = ?', (uname,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_by_login(self, login: str) -> Optional[Dict[str, Any]]:
        """Resolve user by username or email."""
        ident = (login or '').strip()
        if not ident:
            return None
        if '@' in ident:
            return self.get_user_by_email(ident.lower())
        return self.get_user_by_username(ident) or self.get_user_by_email(ident.lower())
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None

    def get_user_by_provider_user_id(self, provider_user_id: str) -> Optional[Dict[str, Any]]:
        """Find a user already linked to this OAuth subject id."""
        pid = (provider_user_id or '').strip()
        if not pid:
            return None
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE provider_user_id = ?', (pid,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def link_oauth_provider(
        self,
        user_id: int,
        provider: str,
        provider_user_id: str,
        profile_image: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Attach an OAuth identity + avatar to an existing local account.

        Keeps ``auth_provider='local'`` and password so username login still works.
        ``provider`` is accepted for API clarity; the subject id is stored on the user row.
        """
        _ = provider  # reserved for multi-provider rows later
        uid = int(user_id)
        pid = (provider_user_id or '').strip()
        if not pid:
            return False, 'missing_provider_user_id'

        user = self.get_user_by_id(uid)
        if not user or not user.get('is_active', 1):
            return False, 'user_not_found'

        other = self.get_user_by_provider_user_id(pid)
        if other and int(other['id']) != uid:
            return False, 'provider_already_linked'

        conn = self._get_connection()
        cursor = conn.cursor()
        if profile_image:
            cursor.execute(
                '''
                UPDATE users
                SET provider_user_id = ?, profile_image = ?
                WHERE id = ?
                ''',
                (pid, profile_image, uid),
            )
        else:
            cursor.execute(
                '''
                UPDATE users
                SET provider_user_id = ?
                WHERE id = ?
                ''',
                (pid, uid),
            )
        conn.commit()
        conn.close()
        return True, 'ok'

    def update_profile_image(self, user_id: int, profile_image: Optional[str]) -> None:
        """Update avatar URL (e.g. after Google sign-in / refresh)."""
        if not profile_image:
            return
        conn = self._get_connection()
        conn.execute(
            'UPDATE users SET profile_image = ? WHERE id = ?',
            (profile_image, int(user_id)),
        )
        conn.commit()
        conn.close()

    def get_first_active_user_id(self) -> Optional[int]:
        """Oldest active account — the hub owner on a single-user Cuttle install."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id FROM users WHERE is_active = 1 ORDER BY id ASC LIMIT 1'
        )
        row = cursor.fetchone()
        conn.close()
        return int(row['id']) if row else None
    
    def verify_password(self, login: str, password: str) -> Optional[int]:
        """Verify user password (username or email) and return user ID if valid.

        Supports transparent migration from legacy SHA-256 hashes to bcrypt:
        on successful login with an old hash, the hash is re-stored as bcrypt.
        """
        user = self.get_user_by_login(login)
        if not user or not user.get('is_active', 1) or not user.get('password_hash'):
            return None

        stored_hash = user['password_hash']

        # Detect legacy SHA-256 hash (64-char hex, not a bcrypt hash)
        if not stored_hash.startswith('$2'):
            legacy_hash = hashlib.sha256(password.encode()).hexdigest()
            if legacy_hash != stored_hash:
                return None
            # Migration: re-hash with bcrypt and update DB
            new_hash = self._hash_password(password)
            conn = self._get_connection()
            conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user['id']))
            conn.commit()
            conn.close()
            self._update_last_login(user['id'])
            return user['id']

        # bcrypt verification
        bcrypt = _bcrypt_mod()
        if bcrypt.checkpw(password.encode(), stored_hash.encode()):
            self._update_last_login(user['id'])
            return user['id']

        return None

    def _hash_password(self, password: str) -> str:
        """Hash password using bcrypt (work factor 12)."""
        bcrypt = _bcrypt_mod()
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    
    def _update_last_login(self, user_id: int):
        """Update last login timestamp"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user_id,))
        
        conn.commit()
        conn.close()
    
    # ==================== Auth Session Management ====================
    
    def create_auth_session(self, user_id: int, expires_hours: int = 720) -> str:
        """Create auth session and return token (default 30 days)"""
        session_token = secrets.token_urlsafe(32)
        # SQLite CURRENT_TIMESTAMP is UTC, even when the host runs in PDT/etc.
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=expires_hours)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO auth_sessions (user_id, session_token, expires_at)
            VALUES (?, ?, ?)
        ''', (user_id, session_token, expires_at))
        
        conn.commit()
        conn.close()
        
        return session_token
    
    def verify_auth_session(self, session_token: str) -> Optional[Dict[str, Any]]:
        """Verify auth session and return user info if valid"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT u.*, s.session_token 
            FROM auth_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_token = ? AND s.expires_at > CURRENT_TIMESTAMP AND u.is_active = 1
        ''', (session_token,))
        
        row = cursor.fetchone()
        
        if row:
            # Update last activity
            cursor.execute('''
                UPDATE auth_sessions 
                SET last_activity = CURRENT_TIMESTAMP 
                WHERE session_token = ?
            ''', (session_token,))
            conn.commit()
        
        conn.close()
        
        return dict(row) if row else None
    
    def delete_auth_session(self, session_token: str):
        """Delete (logout) auth session"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM auth_sessions WHERE session_token = ?', (session_token,))
        
        conn.commit()
        conn.close()
    
    def cleanup_expired_sessions(self):
        """Remove expired auth sessions"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM auth_sessions WHERE expires_at < CURRENT_TIMESTAMP')
        
        conn.commit()
        conn.close()
    
    # ==================== Chat Session Management ====================
    
    def create_chat_session(self, user_id: int, session_name: str = None) -> int:
        """Create a new chat session for user"""
        name_auto = 1
        if session_name:
            # Caller supplied an explicit name — treat as user-chosen, never overwrite.
            name_auto = 0
        else:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM chat_sessions WHERE user_id = ?', (user_id,))
            count = cursor.fetchone()['count']
            conn.close()
            session_name = f"Chat Session {count + 1}"

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO chat_sessions (user_id, session_name, name_auto)
            VALUES (?, ?, ?)
        ''', (user_id, session_name, name_auto))
        
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return session_id

    def find_discord_chat_session(
        self,
        *,
        origin: str,
        discord_user_id: str,
        discord_channel_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Active bridged session for a Discord identity (DM or guild channel)."""
        origin = (origin or '').strip()
        uid = str(discord_user_id or '').strip()
        if not origin or not uid:
            return None
        conn = self._get_connection()
        cursor = conn.cursor()
        if origin == 'discord_dm':
            cursor.execute(
                '''
                SELECT * FROM chat_sessions
                WHERE origin = ? AND discord_user_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
                ''',
                (origin, uid),
            )
        else:
            chan = str(discord_channel_id or '').strip()
            if not chan:
                conn.close()
                return None
            cursor.execute(
                '''
                SELECT * FROM chat_sessions
                WHERE origin = ? AND discord_user_id = ? AND discord_channel_id = ?
                  AND is_active = 1
                ORDER BY id DESC LIMIT 1
                ''',
                (origin, uid, chan),
            )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def create_discord_chat_session(
        self,
        owner_user_id: int,
        *,
        origin: str,
        discord_user_id: str,
        discord_channel_id: str = '',
        discord_username: str = '',
        session_name: str = '',
    ) -> int:
        """Create a CH session owned by the hub user, tagged with a Discord identity."""
        name = (session_name or discord_username or 'Discord').strip()[:80] or 'Discord'
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO chat_sessions (
                user_id, session_name, name_auto, origin,
                discord_user_id, discord_channel_id, discord_username
            )
            VALUES (?, ?, 0, ?, ?, ?, ?)
            ''',
            (
                int(owner_user_id),
                name,
                (origin or 'discord_dm').strip(),
                str(discord_user_id or '').strip() or None,
                str(discord_channel_id or '').strip() or None,
                (discord_username or '').strip()[:80] or None,
            ),
        )
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return session_id

    def create_subagent_chat_session(
        self,
        user_id: int,
        *,
        parent_session_id: int,
        session_name: str,
        project_id: Optional[int] = None,
        project_name: Optional[str] = None,
        project_path: Optional[str] = None,
        display_name: Optional[str] = None,
        avatar: Optional[str] = None,
        agent_profile_id: Optional[str] = None,
    ) -> int:
        """Create a real CH session owned by the same user, nested under a parent chat."""
        name = (session_name or "Subagent").strip()[:80] or "Subagent"
        shown = (display_name or name).strip()[:80] or name
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO chat_sessions (
                user_id, session_name, name_auto, origin, parent_session_id,
                project_id, project_name, project_path,
                display_name, avatar, agent_profile_id
            )
            VALUES (?, ?, 0, 'subagent', ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                int(user_id),
                name,
                int(parent_session_id),
                int(project_id) if project_id is not None else None,
                (project_name or "").strip() or None,
                (project_path or "").strip() or None,
                shown,
                (avatar or "").strip()[:200] or None,
                (agent_profile_id or "").strip()[:40] or None,
            ),
        )
        session_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return int(session_id)

    def session_nesting_depth(self, session_id: int) -> int:
        """How many parent hops this session has (0 = top-level chat)."""
        depth = 0
        seen = set()
        current = int(session_id)
        conn = self._get_connection()
        cursor = conn.cursor()
        while current and current not in seen and depth < 32:
            seen.add(current)
            cursor.execute(
                "SELECT parent_session_id FROM chat_sessions WHERE id = ?",
                (current,),
            )
            row = cursor.fetchone()
            if not row:
                break
            parent = row["parent_session_id"]
            if parent is None:
                break
            try:
                current = int(parent)
            except (TypeError, ValueError):
                break
            depth += 1
        conn.close()
        return depth

    def find_discord_link(self, discord_user_id: str) -> Optional[Dict[str, Any]]:
        uid = str(discord_user_id or "").strip()
        if not uid:
            return None
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT l.*, cs.user_id AS owner_user_id, cs.is_active
            FROM chat_session_discord_links l
            JOIN chat_sessions cs ON cs.id = l.chat_session_id
            WHERE l.discord_user_id = ? AND cs.is_active = 1
            LIMIT 1
            ''',
            (uid,),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_discord_identities(self, owner_user_id: int) -> List[Dict[str, Any]]:
        """Known Discord people for this hub user (links + origin-tagged sessions)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT l.discord_user_id, l.discord_username, l.discord_channel_id,
                   l.chat_session_id, cs.session_name
            FROM chat_session_discord_links l
            JOIN chat_sessions cs ON cs.id = l.chat_session_id
            WHERE cs.user_id = ? AND cs.is_active = 1
            ''',
            (int(owner_user_id),),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        seen = {str(r.get("discord_user_id") or "") for r in rows}
        cursor.execute(
            '''
            SELECT discord_user_id, discord_username, discord_channel_id,
                   id AS chat_session_id, session_name
            FROM chat_sessions
            WHERE user_id = ? AND is_active = 1
              AND discord_user_id IS NOT NULL
              AND origin LIKE 'discord%'
            ''',
            (int(owner_user_id),),
        )
        for r in cursor.fetchall():
            d = dict(r)
            uid = str(d.get("discord_user_id") or "")
            if uid and uid not in seen:
                seen.add(uid)
                rows.append(d)
        conn.close()
        return rows

    def list_discord_links_for_sessions(self, session_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        ids = [int(x) for x in (session_ids or []) if x is not None]
        if not ids:
            return {}
        conn = self._get_connection()
        cursor = conn.cursor()
        q = ",".join("?" * len(ids))
        cursor.execute(
            f'''
            SELECT chat_session_id, discord_user_id, discord_username, discord_channel_id
            FROM chat_session_discord_links
            WHERE chat_session_id IN ({q})
            ''',
            ids,
        )
        out: Dict[int, List[Dict[str, Any]]] = {}
        for row in cursor.fetchall():
            d = dict(row)
            sid = int(d["chat_session_id"])
            out.setdefault(sid, []).append(d)
        conn.close()
        return out

    def upsert_discord_link(
        self,
        chat_session_id: int,
        *,
        discord_user_id: str,
        discord_username: str = "",
        discord_channel_id: str = "",
    ) -> None:
        uid = str(discord_user_id or "").strip()
        if not uid:
            raise ValueError("discord_user_id required")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO chat_session_discord_links (
                chat_session_id, discord_user_id, discord_username, discord_channel_id
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                chat_session_id = excluded.chat_session_id,
                discord_username = COALESCE(excluded.discord_username, discord_username),
                discord_channel_id = COALESCE(excluded.discord_channel_id, discord_channel_id)
            ''',
            (
                int(chat_session_id),
                uid,
                (discord_username or "").strip()[:80] or None,
                str(discord_channel_id or "").strip() or None,
            ),
        )
        conn.commit()
        conn.close()

    def set_session_name(self, session_id: int, name: str, auto: bool = True) -> bool:
        """Set a chat session's display name.

        auto=True (LLM/fallback naming) only overwrites names that were
        themselves auto-generated; auto=False (manual rename) always wins
        and locks the name against future auto-renames.
        """
        name = (name or '').strip()
        if not name:
            return False

        conn = self._get_connection()
        cursor = conn.cursor()
        if auto:
            cursor.execute('''
                UPDATE chat_sessions
                SET session_name = ?
                WHERE id = ? AND (name_auto = 1 OR name_auto IS NULL)
            ''', (name[:80], session_id))
        else:
            cursor.execute('''
                UPDATE chat_sessions
                SET session_name = ?, name_auto = 0
                WHERE id = ?
            ''', (name[:80], session_id))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def rename_chat_session(self, session_id: int, user_id: int, name: str) -> bool:
        """Manual rename for an owned active session (locks name_auto = 0)."""
        name = (name or '').strip()
        if not name:
            return False
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE chat_sessions
            SET session_name = ?, name_auto = 0
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (name[:80], session_id, user_id),
        )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def get_session_naming_info(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Return name, name_auto, and user-message count for the auto-namer."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT cs.session_name, cs.name_auto,
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.chat_session_id = cs.id AND cm.role = 'user') AS user_message_count
            FROM chat_sessions cs
            WHERE cs.id = ?
        ''', (session_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def get_user_chat_sessions(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all chat sessions for a user (list hides empty never-messaged sessions).

        Empty sessions stay active so deep-links like ``/?chat=<id>`` keep working
        until the first message — we used to soft-delete them here, which raced
        with opening a brand-new chat and made the second tab 404 on live-status.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT cs.*, 
                   COUNT(cm.id) as message_count,
                   MAX(cm.timestamp) as last_message_time
            FROM chat_sessions cs
            LEFT JOIN chat_messages cm ON cs.id = cm.chat_session_id
            WHERE cs.user_id = ? AND cs.is_active = 1
            GROUP BY cs.id
            HAVING COUNT(cm.id) > 0
            ORDER BY COALESCE(MAX(cm.timestamp), cs.last_activity) DESC
        ''', (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]

    @staticmethod
    def _like_contains(term: str) -> str:
        escaped = (
            (term or '')
            .replace('\\', '\\\\')
            .replace('%', '\\%')
            .replace('_', '\\_')
        )
        return f'%{escaped}%'

    @staticmethod
    def _clip_search_snippet(text: str, query: str, radius: int = 42) -> str:
        raw = re.sub(r'\s+', ' ', str(text or '')).strip()
        if not raw:
            return ''
        needle = (query or '').strip()
        idx = raw.lower().find(needle.lower()) if needle else -1
        if idx < 0:
            return raw[:96]
        start = max(0, idx - radius)
        end = min(len(raw), idx + max(len(needle), 1) + radius)
        piece = raw[start:end]
        if start:
            piece = '…' + piece
        if end < len(raw):
            piece += '…'
        return piece

    def search_user_chats(
        self,
        user_id: int,
        query: str,
        *,
        limit: int = 80,
    ) -> List[Dict[str, Any]]:
        """Title-first chat search, then message-body matches.

        Returns session dicts (same shape as ``get_user_chat_sessions``) plus
        ``match`` (``title`` | ``content``) and optional ``snippet``.
        """
        q = (query or '').strip()[:200]
        if not q:
            return []
        like = self._like_contains(q.lower())
        handle_id = None
        m = _CHAT_HANDLE_RE.match(q.strip())
        if m:
            try:
                handle_id = int(m.group(1))
            except (TypeError, ValueError):
                handle_id = None

        conn = self._get_connection()
        cursor = conn.cursor()
        title_sql = '''
            SELECT cs.*,
                   COUNT(cm.id) as message_count,
                   MAX(cm.timestamp) as last_message_time
            FROM chat_sessions cs
            LEFT JOIN chat_messages cm ON cs.id = cm.chat_session_id
            WHERE cs.user_id = ? AND cs.is_active = 1
              AND (
                    LOWER(IFNULL(cs.session_name, '')) LIKE ? ESCAPE '\\'
                 OR LOWER(CAST(cs.id AS TEXT)) LIKE ? ESCAPE '\\'
                 OR LOWER(printf('CH-%06d', cs.id)) LIKE ? ESCAPE '\\'
                 OR LOWER(IFNULL(cs.discord_username, '')) LIKE ? ESCAPE '\\'
                 OR LOWER(IFNULL(cs.project_name, '')) LIKE ? ESCAPE '\\'
                 OR (? IS NOT NULL AND cs.id = ?)
              )
            GROUP BY cs.id
            HAVING COUNT(cm.id) > 0
            ORDER BY COALESCE(MAX(cm.timestamp), cs.last_activity) DESC
            LIMIT ?
        '''
        cursor.execute(
            title_sql,
            (user_id, like, like, like, like, like, handle_id, handle_id, int(limit)),
        )
        title_rows = [dict(row) for row in cursor.fetchall()]
        title_ids = {int(r['id']) for r in title_rows if r.get('id') is not None}

        content_sql = '''
            SELECT cs.*,
                   COUNT(cm.id) as message_count,
                   MAX(cm.timestamp) as last_message_time,
                   (
                     SELECT cm2.content FROM chat_messages cm2
                     WHERE cm2.chat_session_id = cs.id
                       AND LOWER(IFNULL(cm2.content, '')) LIKE ? ESCAPE '\\'
                     ORDER BY cm2.id ASC
                     LIMIT 1
                   ) AS match_snippet
            FROM chat_sessions cs
            INNER JOIN chat_messages cm ON cs.id = cm.chat_session_id
            WHERE cs.user_id = ? AND cs.is_active = 1
              AND cs.id IN (
                    SELECT DISTINCT chat_session_id FROM chat_messages
                    WHERE LOWER(IFNULL(content, '')) LIKE ? ESCAPE '\\'
              )
            GROUP BY cs.id
            HAVING COUNT(cm.id) > 0
            ORDER BY COALESCE(MAX(cm.timestamp), cs.last_activity) DESC
            LIMIT ?
        '''
        cursor.execute(content_sql, (like, user_id, like, int(limit)))
        content_rows = [dict(row) for row in cursor.fetchall()]
        conn.close()

        out: List[Dict[str, Any]] = []
        for row in title_rows:
            row['match'] = 'title'
            row['snippet'] = ''
            out.append(row)
        for row in content_rows:
            sid = row.get('id')
            if sid is not None and int(sid) in title_ids:
                continue
            row['match'] = 'content'
            row['snippet'] = self._clip_search_snippet(row.pop('match_snippet', '') or '', q)
            out.append(row)
            if len(out) >= int(limit):
                break
        for row in title_rows:
            row.pop('match_snippet', None)
        return out[: int(limit)]
    
    def get_chat_session(self, session_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Get specific chat session (verifies user ownership).

        Reactivates an owned session that was soft-deleted while still empty, so
        deep-links opened before the first message keep working. A session that
        already has messages is never revived: that soft-delete came from the
        user's trash button, and an in-flight poll (messages / live status /
        cancel) would otherwise resurrect the chat right after they deleted it.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM chat_sessions 
            WHERE id = ? AND user_id = ? AND is_active = 1
        ''', (session_id, user_id))
        
        row = cursor.fetchone()
        if not row:
            # Owned but inactive — revive it (common after the old empty-session GC).
            cursor.execute('''
                UPDATE chat_sessions
                SET is_active = 1, last_activity = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND is_active = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM chat_messages cm WHERE cm.chat_session_id = chat_sessions.id
                  )
            ''', (session_id, user_id))
            if cursor.rowcount:
                conn.commit()
                cursor.execute('''
                    SELECT * FROM chat_sessions
                    WHERE id = ? AND user_id = ? AND is_active = 1
                ''', (session_id, user_id))
                row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    def delete_chat_session(self, session_id: int, user_id: int) -> bool:
        """Delete (deactivate) a chat session"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE chat_sessions 
            SET is_active = 0 
            WHERE id = ? AND user_id = ?
        ''', (session_id, user_id))
        
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        
        return affected > 0

    def set_session_starred(self, session_id: int, user_id: int, starred: bool) -> bool:
        """Favorite / unfavorite a chat session owned by the user."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE chat_sessions
            SET starred = ?
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (1 if starred else 0, session_id, user_id),
        )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    @staticmethod
    def _parse_followup_queue(raw: Any) -> List[Dict[str, Any]]:
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in data:
            norm = AuthDatabase._normalize_followup_item(item)
            if norm:
                out.append(norm)
        return out[:40]

    @staticmethod
    def _normalize_followup_item(raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        content = str(raw.get('content') or '')[:20000]
        raw_msg = str(raw.get('rawMessage') or raw.get('raw_message') or content)[:20000]
        if not content.strip() and not raw_msg.strip() and not raw.get('attachments'):
            return None
        fid = str(raw.get('id') or '').strip()[:80]
        if not fid:
            fid = 'fq_' + uuid.uuid4().hex[:12]
        try:
            created = int(raw.get('created') or 0)
        except (TypeError, ValueError):
            created = 0
        atts = raw.get('attachments') if isinstance(raw.get('attachments'), list) else []
        return {
            'id': fid,
            'content': content or raw_msg,
            'rawMessage': raw_msg or content,
            'created': created,
            'attachments': atts[:20],
            'paused': bool(raw.get('paused')),
        }

    def get_followup_queue(self, session_id: int, user_id: int) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT followup_queue FROM chat_sessions
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (session_id, user_id),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return []
        return self._parse_followup_queue(row['followup_queue'] if 'followup_queue' in row.keys() else None)

    def set_followup_queue(
        self, session_id: int, user_id: int, items: List[Any]
    ) -> Optional[List[Dict[str, Any]]]:
        normalized: List[Dict[str, Any]] = []
        for item in items or []:
            norm = self._normalize_followup_item(item)
            if norm:
                normalized.append(norm)
        normalized = normalized[:40]
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE chat_sessions
            SET followup_queue = ?, last_activity = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (json.dumps(normalized), session_id, user_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return normalized if ok else None

    def append_followup(
        self, session_id: int, user_id: int, item: Any
    ) -> Optional[List[Dict[str, Any]]]:
        norm = self._normalize_followup_item(item)
        if not norm:
            return self.get_followup_queue(session_id, user_id)
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT followup_queue FROM chat_sessions
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (session_id, user_id),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        items = self._parse_followup_queue(row['followup_queue'] if 'followup_queue' in row.keys() else None)
        if not any(x.get('id') == norm['id'] for x in items):
            items.append(norm)
        items = items[:40]
        cursor.execute(
            '''
            UPDATE chat_sessions
            SET followup_queue = ?, last_activity = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (json.dumps(items), session_id, user_id),
        )
        conn.commit()
        conn.close()
        return items

    def take_followup_queue(self, session_id: int, user_id: int) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """
        Atomically take unpaused follow-ups. Paused items stay in the queue.

        Returns ``{"taken": [...], "remaining": [...]}`` or None if session missing.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT followup_queue FROM chat_sessions
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (session_id, user_id),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        items = self._parse_followup_queue(row['followup_queue'] if 'followup_queue' in row.keys() else None)
        taken = [x for x in items if not x.get('paused')]
        remaining = [x for x in items if x.get('paused')]
        cursor.execute(
            '''
            UPDATE chat_sessions
            SET followup_queue = ?, last_activity = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (json.dumps(remaining), session_id, user_id),
        )
        conn.commit()
        conn.close()
        return {'taken': taken, 'remaining': remaining}

    def set_session_project(
        self,
        session_id: int,
        user_id: int,
        *,
        project_id: Optional[int] = None,
        project_name: Optional[str] = None,
        project_path: Optional[str] = None,
    ) -> bool:
        """Persist the chat's working project (cwd) on the auth session row."""
        conn = self._get_connection()
        cursor = conn.cursor()
        name = (project_name or '').strip() or None
        path = (project_path or '').strip() or None
        pid = project_id
        if pid is not None:
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                pid = None
        cursor.execute(
            '''
            UPDATE chat_sessions
            SET project_id = ?, project_name = ?, project_path = ?
            WHERE id = ? AND user_id = ? AND is_active = 1
            ''',
            (pid, name, path, session_id, user_id),
        )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def get_session_project(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Return project fields for a chat session (any owner)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT project_id, project_name, project_path
            FROM chat_sessions
            WHERE id = ? AND is_active = 1
            ''',
            (session_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            'project_id': row['project_id'],
            'project_name': row['project_name'],
            'project_path': row['project_path'],
        }
    
    def update_session_activity(self, session_id: int):
        """Update last activity timestamp for session"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE chat_sessions 
            SET last_activity = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (session_id,))
        
        conn.commit()
        conn.close()
    
    # ==================== Chat Message Management ====================
    
    def add_message(self, chat_session_id: int, role: str, content: str, 
                   metadata: Optional[Dict] = None) -> int:
        """Add a message to chat session"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        metadata_json = json.dumps(metadata) if metadata else None
        
        cursor.execute('''
            INSERT INTO chat_messages (chat_session_id, role, content, metadata)
            VALUES (?, ?, ?, ?)
        ''', (chat_session_id, role, content, metadata_json))
        
        message_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Update session activity
        self.update_session_activity(chat_session_id)
        
        return message_id

    def get_completion_message(self, chat_session_id: int, delivery_key: str) -> Optional[Dict]:
        """Recover the canonical persisted completion receipt."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM chat_messages WHERE chat_session_id = ? "
                "AND json_valid(metadata) AND json_extract(metadata, '$.delivery_key') = ?",
                (chat_session_id, delivery_key)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_message_once(self, chat_session_id: int, content: str, *,
                         delivery_key: str, metadata: Optional[Dict] = None) -> Optional[int]:
        """Atomically persist a server completion, including cross-process retries.

        The message itself is the receipt: deletion of its chat cannot leave an
        orphan delivery claim, and a crash cannot separate receipt from insert.
        """
        conn = self._get_connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM chat_sessions WHERE id = ?',
                                (chat_session_id,)).fetchone():
                return None
            row = conn.execute(
                "SELECT id FROM chat_messages WHERE chat_session_id = ? "
                "AND json_valid(metadata) AND json_extract(metadata, '$.delivery_key') = ?",
                (chat_session_id, delivery_key)).fetchone()
            if row:
                return int(row[0])
            payload = {**(metadata or {}), 'delivery_key': delivery_key}
            cursor = conn.execute(
                "INSERT INTO chat_messages (chat_session_id, role, content, metadata) "
                "VALUES (?, 'assistant', ?, ?)",
                (chat_session_id, content, json.dumps(payload)))
            conn.execute('UPDATE chat_sessions SET last_activity = CURRENT_TIMESTAMP WHERE id = ?',
                         (chat_session_id,))
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def update_message_content(
        self,
        message_id: int,
        content: str,
        metadata: Optional[Dict] = None,
    ) -> bool:
        """Replace message content (and optional metadata) by id."""
        conn = self._get_connection()
        cursor = conn.cursor()
        if metadata is not None:
            cursor.execute(
                '''
                UPDATE chat_messages
                SET content = ?, metadata = ?
                WHERE id = ?
                ''',
                (content, json.dumps(metadata), int(message_id)),
            )
        else:
            cursor.execute(
                '''
                UPDATE chat_messages SET content = ? WHERE id = ?
                ''',
                (content, int(message_id)),
            )
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def merge_message_metadata_by_query(
        self,
        chat_session_id: int,
        query_id: str,
        patch: Dict[str, Any],
    ) -> Optional[int]:
        """Merge ``patch`` into the assistant message carrying ``query_id``.

        Keys whose value is None are removed. Returns the message id, or None.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT id, metadata FROM chat_messages
            WHERE chat_session_id = ? AND role = 'assistant' AND metadata LIKE ?
            ORDER BY id DESC
            ''',
            (int(chat_session_id), f'%{query_id}%'),
        )
        target = None
        for row in cursor.fetchall():
            try:
                meta = json.loads(row['metadata'] or '{}')
            except (TypeError, ValueError):
                continue
            if isinstance(meta, dict) and str(meta.get('query_id') or '') == str(query_id):
                target = (row['id'], meta)
                break
        if target is None:
            conn.close()
            return None
        message_id, meta = target
        for key, value in patch.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        cursor.execute(
            'UPDATE chat_messages SET metadata = ? WHERE id = ?',
            (json.dumps(meta), int(message_id)),
        )
        conn.commit()
        conn.close()
        return int(message_id)

    def find_messages_containing(
        self,
        chat_session_id: int,
        needle: str,
        *,
        role: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Recent messages whose content contains ``needle`` (newest first)."""
        if not needle:
            return []
        conn = self._get_connection()
        cursor = conn.cursor()
        if role:
            cursor.execute(
                '''
                SELECT * FROM chat_messages
                WHERE chat_session_id = ? AND role = ? AND content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                ''',
                (int(chat_session_id), role, f'%{needle}%', int(limit)),
            )
        else:
            cursor.execute(
                '''
                SELECT * FROM chat_messages
                WHERE chat_session_id = ? AND content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                ''',
                (int(chat_session_id), f'%{needle}%', int(limit)),
            )
        rows = cursor.fetchall()
        conn.close()
        out = []
        for row in rows:
            msg = dict(row)
            if msg.get('metadata'):
                try:
                    msg['metadata'] = json.loads(msg['metadata'])
                except Exception:
                    pass
            out.append(msg)
        return out
    
    def get_messages(
        self,
        chat_session_id: int,
        limit: Optional[int] = None,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get messages from chat session.

        Pagination (id-ordered, oldest→newest in the returned list):
        - ``limit`` alone → newest N messages
        - ``before_id`` + ``limit`` → N messages with id < before_id (older page)
        - ``after_id`` → messages with id > after_id (sync / tail)
        - no limit/before/after → full transcript
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        sid = int(chat_session_id)
        reverse = False

        if after_id is not None:
            if limit:
                cursor.execute(
                    '''
                    SELECT * FROM chat_messages
                    WHERE chat_session_id = ? AND id > ?
                    ORDER BY id ASC
                    LIMIT ?
                    ''',
                    (sid, int(after_id), int(limit)),
                )
            else:
                cursor.execute(
                    '''
                    SELECT * FROM chat_messages
                    WHERE chat_session_id = ? AND id > ?
                    ORDER BY id ASC
                    ''',
                    (sid, int(after_id)),
                )
        elif before_id is not None:
            lim = int(limit) if limit else 10
            cursor.execute(
                '''
                SELECT * FROM chat_messages
                WHERE chat_session_id = ? AND id < ?
                ORDER BY id DESC
                LIMIT ?
                ''',
                (sid, int(before_id), lim),
            )
            reverse = True
        elif limit:
            cursor.execute(
                '''
                SELECT * FROM chat_messages
                WHERE chat_session_id = ?
                ORDER BY id DESC
                LIMIT ?
                ''',
                (sid, int(limit)),
            )
            reverse = True
        else:
            cursor.execute(
                '''
                SELECT * FROM chat_messages
                WHERE chat_session_id = ?
                ORDER BY id ASC
                ''',
                (sid,),
            )

        rows = cursor.fetchall()
        conn.close()

        messages = [dict(row) for row in rows]

        for msg in messages:
            if msg.get('metadata'):
                try:
                    msg['metadata'] = json.loads(msg['metadata'])
                except Exception:
                    msg['metadata'] = None

        if reverse:
            messages.reverse()

        return messages

    def get_message_by_share_index(
        self,
        chat_session_id: int,
        share_index: int,
    ) -> Optional[Dict[str, Any]]:
        """Resolve UI share-ref bubble ``N`` (``CH-<session>-N``).

        Index is **1-based among user+assistant rows only**, ordered by
        ``chat_messages.id``. System notices (Stop generating, project-change,
        …) are excluded — matching ``.message:not(.system)`` / message-nav in
        ``chat_page.js``. Counting every row (including stops) desyncs agents
        from the handle the user copied.
        """
        try:
            idx = int(share_index)
        except (TypeError, ValueError):
            return None
        if idx < 1:
            return None

        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM chat_messages
            WHERE chat_session_id = ?
              AND role IN ('user', 'assistant')
            ORDER BY id ASC
            LIMIT 1 OFFSET ?
            ''',
            (int(chat_session_id), idx - 1),
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        msg = dict(row)
        if msg.get('metadata'):
            try:
                msg['metadata'] = json.loads(msg['metadata'])
            except Exception:
                msg['metadata'] = None
        return msg

    def message_page_meta(
        self,
        chat_session_id: int,
        oldest_id: Optional[int],
    ) -> Dict[str, Any]:
        """Pagination flags for a loaded window starting at ``oldest_id``.

        ``older_visible_count`` is non-system rows with id < oldest_id — used so
        CH-xxx-N share indices stay absolute while bubbles lazy-load.
        """
        if oldest_id is None:
            return {'has_more': False, 'older_visible_count': 0}

        conn = self._get_connection()
        cursor = conn.cursor()
        sid = int(chat_session_id)
        oid = int(oldest_id)
        cursor.execute(
            '''
            SELECT COUNT(*) AS c FROM chat_messages
            WHERE chat_session_id = ? AND id < ?
            ''',
            (sid, oid),
        )
        older_total = int((cursor.fetchone() or {'c': 0})['c'] or 0)
        cursor.execute(
            '''
            SELECT COUNT(*) AS c FROM chat_messages
            WHERE chat_session_id = ? AND id < ? AND role != 'system'
            ''',
            (sid, oid),
        )
        older_visible = int((cursor.fetchone() or {'c': 0})['c'] or 0)
        conn.close()
        return {
            'has_more': older_total > 0,
            'older_visible_count': older_visible,
        }
    
    # ==================== OAuth State Management ====================
    
    def create_oauth_state(self, provider: str, link_user_id: Optional[int] = None) -> str:
        """Create OAuth state token. Optional link_user_id = attach provider to that account."""
        state_token = secrets.token_urlsafe(32)
        # SQLite CURRENT_TIMESTAMP is UTC — store expires_at in UTC too.
        expires_at = datetime.utcnow() + timedelta(minutes=10)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO oauth_states (state_token, provider, expires_at, link_user_id)
            VALUES (?, ?, ?, ?)
        ''', (state_token, provider, expires_at.strftime('%Y-%m-%d %H:%M:%S'), link_user_id))
        
        conn.commit()
        conn.close()
        
        return state_token

    def consume_oauth_state(self, state_token: str, provider: str) -> Optional[Dict[str, Any]]:
        """Verify + consume OAuth state. Returns row dict (incl. link_user_id) or None."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT * FROM oauth_states
            WHERE state_token = ? AND provider = ? AND expires_at > CURRENT_TIMESTAMP
        ''', (state_token, provider))

        row = cursor.fetchone()
        if row:
            cursor.execute('DELETE FROM oauth_states WHERE state_token = ?', (state_token,))
            conn.commit()
            data = dict(row)
            conn.close()
            return data

        conn.close()
        return None
    
    def verify_oauth_state(self, state_token: str, provider: str) -> bool:
        """Verify OAuth state token (legacy bool API)."""
        return self.consume_oauth_state(state_token, provider) is not None
    
    def cleanup_expired_oauth_states(self):
        """Remove expired OAuth states"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM oauth_states WHERE expires_at < CURRENT_TIMESTAMP')
        
        conn.commit()
        conn.close()

    # ==================== Chat widgets ====================

    @staticmethod
    def _decode_widget_row(row) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        d = dict(row)
        payload = d.get("payload")
        if isinstance(payload, str):
            try:
                d["payload"] = json.loads(payload)
            except Exception:
                d["payload"] = {}
        return d

    def get_chat_session_by_id(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Load a chat session row by id (no ownership check — internal use)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM chat_sessions WHERE id = ?", (int(session_id),))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_chat_widget(
        self, widget_id: str, *, user_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                "SELECT * FROM chat_widgets WHERE id = ? AND user_id = ?",
                (str(widget_id), int(user_id)),
            )
        else:
            cursor.execute(
                "SELECT * FROM chat_widgets WHERE id = ?", (str(widget_id),)
            )
        row = cursor.fetchone()
        conn.close()
        return self._decode_widget_row(row)

    def list_chat_widgets(
        self,
        *,
        user_id: int,
        session_id: Optional[int] = None,
        project_path: Optional[str] = None,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        """Widgets visible for a chat: session-scoped for this id and/or project-scoped."""
        conn = self._get_connection()
        cursor = conn.cursor()
        proj = (project_path or "").strip().replace("\\", "/")
        while proj.endswith("/"):
            proj = proj[:-1]
        clauses = ["user_id = ?", "status = ?"]
        params: List[Any] = [int(user_id), str(status or "active")]
        scope_bits = []
        if session_id is not None:
            scope_bits.append("(scope = 'session' AND session_id = ?)")
            params.append(int(session_id))
        if proj:
            scope_bits.append("(scope = 'project' AND project_path = ?)")
            params.append(proj)
        if not scope_bits:
            # No session/project filter — return all active for user (rare).
            pass
        else:
            clauses.append("(" + " OR ".join(scope_bits) + ")")
        sql = (
            "SELECT * FROM chat_widgets WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id ASC"
        )
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
        conn.close()
        return [self._decode_widget_row(r) for r in rows if r]

    def upsert_chat_widget(
        self,
        *,
        widget_id: str,
        user_id: int,
        wtype: str,
        title: str,
        scope: str,
        session_id: Optional[int],
        project_path: str,
        payload: Any,
        status: str = "active",
        edit_mode: str = "agent",
        description: str = "",
    ) -> Optional[Dict[str, Any]]:
        if isinstance(payload, (dict, list)):
            payload_s = json.dumps(payload, ensure_ascii=False)
        else:
            payload_s = str(payload or "{}")
        scope_n = "project" if str(scope).lower() == "project" else "session"
        edit_n = "shared" if str(edit_mode or "").strip().lower() == "shared" else "agent"
        desc_n = str(description or "").strip()
        proj = (project_path or "").strip().replace("\\", "/")
        while proj.endswith("/"):
            proj = proj[:-1]
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT revision FROM chat_widgets WHERE id = ? AND user_id = ?",
            (str(widget_id), int(user_id)),
        )
        prev = cursor.fetchone()
        rev = int(prev["revision"] if prev else 0) + 1
        cursor.execute(
            '''
            INSERT INTO chat_widgets (
                id, user_id, type, title, description, scope, session_id, project_path,
                payload, status, edit_mode, revision, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                user_id = excluded.user_id,
                type = excluded.type,
                title = excluded.title,
                description = excluded.description,
                scope = excluded.scope,
                session_id = excluded.session_id,
                project_path = excluded.project_path,
                payload = excluded.payload,
                status = excluded.status,
                edit_mode = excluded.edit_mode,
                revision = excluded.revision,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (
                str(widget_id),
                int(user_id),
                str(wtype),
                str(title or ""),
                desc_n,
                scope_n,
                int(session_id) if session_id is not None and scope_n == "session" else None,
                proj if scope_n == "project" else "",
                payload_s,
                str(status or "active"),
                edit_n,
                rev,
            ),
        )
        conn.commit()
        cursor.execute(
            "SELECT * FROM chat_widgets WHERE id = ?", (str(widget_id),)
        )
        row = cursor.fetchone()
        conn.close()
        return self._decode_widget_row(row)

    def widgets_revision_for(
        self,
        *,
        user_id: int,
        session_id: Optional[int] = None,
        project_path: Optional[str] = None,
    ) -> int:
        rows = self.list_chat_widgets(
            user_id=user_id,
            session_id=session_id,
            project_path=project_path,
            status="active",
        )
        rev = 0
        for w in rows:
            try:
                rev = max(rev, int(w.get("revision") or 0))
            except Exception:
                pass
        return rev


# Singleton instance
_db_instance = None

def get_auth_db() -> AuthDatabase:
    """Get singleton database instance"""
    global _db_instance
    if _db_instance is None:
        # Pass DB_PATH explicitly so monkeypatches / env redirects apply.
        _db_instance = AuthDatabase(DB_PATH)
    return _db_instance
