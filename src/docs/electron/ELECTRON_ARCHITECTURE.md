# 🏗️ Cuttle Desktop - Architecture Overview

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                    ELECTRON WRAPPER                         │
│                    (Native Window)                          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                                                     │   │
│  │              MAIN PROCESS (main.js)                 │   │
│  │                                                     │   │
│  │  • Spawns Python Flask Server                      │   │
│  │  • Creates BrowserWindow                           │   │
│  │  • Manages App Lifecycle                           │   │
│  │  • Handles Menu & Shortcuts                        │   │
│  │                                                     │   │
│  └──────────────────┬──────────────────────────────────┘   │
│                     │                                       │
│                     │ spawns                                │
│                     ▼                                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                                                     │   │
│  │         PYTHON FLASK SERVER (Port 8080)            │   │
│  │                                                     │   │
│  │  • web_chat_api.py                                 │   │
│  │  • Serves HTML/CSS/JS                              │   │
│  │  • Handles API requests                            │   │
│  │  • AI Agent functionality                          │   │
│  │  • Authentication                                  │   │
│  │  • Project Management                              │   │
│  │                                                     │   │
│  └──────────────────┬──────────────────────────────────┘   │
│                     │                                       │
│                     │ HTTP                                  │
│                     ▼                                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                                                     │   │
│  │         RENDERER PROCESS (Browser Window)           │   │
│  │                                                     │   │
│  │  • Displays web content                            │   │
│  │  • Loads http://localhost:8080                     │   │
│  │  • HTML/CSS/JS files                               │   │
│  │  • User Interface                                  │   │
│  │                                                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. Main Process (`electron/main.js`)

**Responsibilities:**
- Launch and manage Python Flask server
- Create and manage native window
- Handle application lifecycle events
- Manage menu bar and shortcuts
- Clean up resources on exit

**Key Functions:**
```javascript
startPythonServer()     // Spawns Python process
checkServerRunning()    // Verifies server is ready
createWindow()          // Creates Electron window
cleanup()               // Stops Python process
```

### 2. Python Flask Server (`src/web_chat_api.py`)

**Responsibilities:**
- Serve web interface (HTML/CSS/JS)
- Handle API requests
- AI agent processing
- User authentication
- Session management
- Project management
- Pipeline execution

**Endpoints:**
```
GET  /                    - Landing page
GET  /chat_page.html      - Chat interface
GET  /node_editor.html    - Node editor
POST /api/chat            - Send messages
GET  /api/sessions        - List sessions
... and more
```

### 3. Renderer Process (Web Interface)

**Responsibilities:**
- Display user interface
- Handle user interactions
- Communicate with backend via HTTP
- Execute client-side JavaScript

**Pages:**
- Landing page
- Chat interface
- Node editor
- Task management
- Control panel
- Settings

### 4. Preload Script (`electron/preload.js`)

**Responsibilities:**
- Secure bridge between main and renderer
- Expose limited APIs to renderer
- Context isolation

**Exposed APIs:**
```javascript
window.electron = {
  platform: 'win32',
  versions: { node, chrome, electron },
  isElectron: true
}
```

## Communication Flow

### User Interaction Flow

```
┌──────────┐
│  User    │
└────┬─────┘
     │ clicks/types
     ▼
┌─────────────────┐
│  Renderer UI    │
│  (Web Browser)  │
└────┬────────────┘
     │ HTTP Request
     ▼
┌──────────────────┐
│  Flask Server    │
│  (Python)        │
└────┬─────────────┘
     │ processes
     ▼
┌──────────────────┐
│  AI Agent /      │
│  Tool Manager    │
└────┬─────────────┘
     │ result
     ▼
┌──────────────────┐
│  Flask Server    │
│  (Python)        │
└────┬─────────────┘
     │ HTTP Response
     ▼
┌─────────────────┐
│  Renderer UI    │
│  (displays)     │
└─────────────────┘
```

### Startup Sequence

```
1. User launches Cuttle.exe
   │
   ▼
2. Electron starts Main Process
   │
   ▼
3. Main Process spawns Python server
   │
   ▼
4. Wait for server to be ready (checks port 8080)
   │
   ▼
5. Create BrowserWindow
   │
   ▼
6. Load http://localhost:8080
   │
   ▼
7. Display UI to user
```

### Shutdown Sequence

```
1. User closes window or clicks Exit
   │
   ▼
2. Main Process receives 'window-all-closed' event
   │
   ▼
3. cleanup() function called
   │
   ▼
4. Python process killed
   │
   ▼
5. Electron app quits
```

## File Structure

### Development Mode

```
Cuttle/
├── electron/
│   ├── main.js              ← Main process
│   ├── preload.js           ← Security bridge
│   └── package.json         ← Config & dependencies
│
└── src/
    ├── web_chat_api.py      ← Flask server
    ├── ai_agent.py          ← AI logic
    ├── tool_manager.py      ← Tools
    └── web/                 ← Web files
        ├── *.html
        ├── js/*.js
        └── css/*.css
```

### Production (Packaged)

```
Cuttle.exe
└── resources/
    ├── app.asar                    ← Electron app (compressed)
    │   └── (main.js, preload.js)
    │
    └── app/                        ← Your application
        └── src/
            ├── web_chat_api.py
            ├── web/
            └── ...
```

## Security Model

### Context Isolation

```
┌─────────────────────────────────────┐
│         MAIN PROCESS                │
│    (Full Node.js access)            │
│                                     │
│  • Can spawn processes              │
│  • Can access filesystem            │
│  • Can use native modules           │
│                                     │
└────────────┬────────────────────────┘
             │
             │ Preload Script (bridge)
             │
┌────────────▼────────────────────────┐
│      RENDERER PROCESS               │
│   (Limited access, isolated)        │
│                                     │
│  • No direct Node.js access         │
│  • No direct file system access     │
│  • Only exposed APIs available      │
│  • Communicates via HTTP            │
│                                     │
└─────────────────────────────────────┘
```

**Security Features:**
- ✅ Context isolation enabled
- ✅ Node integration disabled in renderer
- ✅ Web security enabled
- ✅ Preload script controls API exposure
- ✅ HTTP communication only

## Build Process

### Development Build Flow

```
1. npm install
   │ Downloads dependencies
   │
   ▼
2. npm start
   │ Runs electron .
   │
   ▼
3. Electron loads main.js
   │
   ▼
4. main.js references ../src/web_chat_api.py
   │
   ▼
5. Application runs from source
```

### Production Build Flow

```
1. npm run build
   │
   ▼
2. electron-builder packages app
   │
   ├─► Bundles Electron runtime
   ├─► Compresses main.js, preload.js → app.asar
   ├─► Copies ../src/ → resources/app/src/
   └─► Creates installer
   │
   ▼
3. Output: Cuttle-1.0.0-x64.exe
```

## Packaging Details

### What Gets Included

✅ **Included in Build:**
- Electron runtime
- main.js and preload.js
- All Python files from src/
- Web files (HTML/CSS/JS)
- Images and assets
- Configuration files

❌ **Excluded from Build:**
- `__pycache__/` directories
- `.pyc` files
- `venv/` virtual environments
- `.env` files
- `node_modules/`
- `.git/` repository

### Build Targets

**NSIS Installer:**
- Full installation wizard
- Registry entries
- Start menu shortcuts
- Desktop shortcuts
- Uninstaller
- ~200MB installed size

**Portable:**
- Single .exe file
- No installation required
- No registry changes
- ~150MB file size

## Performance Considerations

### Memory Usage

```
Component               Typical Memory
────────────────────────────────────────
Electron Main Process   ~50-80 MB
Renderer Process        ~100-150 MB
Python Flask Server     ~80-120 MB
────────────────────────────────────────
Total                   ~230-350 MB
```

### Startup Time

```
Phase                   Duration
────────────────────────────────────
Electron init           ~0.5-1s
Python spawn            ~1-2s
Server ready check      ~0.5-1s
Window creation         ~0.5-1s
Page load               ~1-2s
────────────────────────────────────
Total                   ~4-7s
```

## Optimization Tips

### Reduce Build Size

1. **Exclude unnecessary files:**
   ```json
   "files": [
     "!**/test_*.py",
     "!**/docs/**"
   ]
   ```

2. **Use asar archives:**
   - Already enabled by default
   - Compresses files

3. **Remove dev dependencies:**
   ```bash
   npm prune --production
   ```

### Improve Startup Speed

1. **Preload Python modules:**
   - Import commonly used modules early

2. **Optimize Flask:**
   - Use production mode
   - Disable debug mode
   - Minimize startup tasks

3. **Lazy load features:**
   - Load features on-demand
   - Don't initialize everything at startup

## Troubleshooting Architecture

### Common Issues and Causes

**Server Connection Refused:**
- Python not in PATH
- Flask dependencies missing
- Port 8080 already in use
- Firewall blocking connection

**Blank Window:**
- Flask server failed to start
- Wrong URL being loaded
- JavaScript errors in renderer
- CORS issues

**Application Won't Close:**
- Python process not killed properly
- Event handlers not cleaned up
- Async operations still running

**Build Fails:**
- Node modules corrupted
- Disk space insufficient
- File paths too long (Windows)
- Missing dependencies

## Advanced Customization

### Add IPC Communication

**Main Process:**
```javascript
const { ipcMain } = require('electron');
ipcMain.on('channel', (event, arg) => {
  // Handle message
  event.reply('response', data);
});
```

**Preload:**
```javascript
contextBridge.exposeInMainWorld('api', {
  send: (channel, data) => {
    ipcRenderer.send(channel, data);
  }
});
```

### Add System Tray

```javascript
const { Tray, Menu } = require('electron');

let tray = new Tray('icon.png');
tray.setContextMenu(Menu.buildFromTemplate([
  { label: 'Show', click: () => mainWindow.show() },
  { label: 'Quit', click: () => app.quit() }
]));
```

### Add Auto-Updater

```javascript
const { autoUpdater } = require('electron-updater');

autoUpdater.checkForUpdatesAndNotify();
```

## Best Practices

### 1. Error Handling
- Always catch Python spawn errors
- Handle network failures gracefully
- Show user-friendly error messages

### 2. Resource Management
- Clean up Python processes
- Close database connections
- Remove temporary files

### 3. Security
- Keep Electron updated
- Validate all user input
- Use HTTPS for external APIs
- Don't expose sensitive APIs

### 4. User Experience
- Show loading indicators
- Handle offline gracefully
- Provide helpful error messages
- Save user preferences

## Conclusion

The Cuttle Desktop architecture combines:
- **Electron** for native window and packaging
- **Python/Flask** for backend logic
- **HTML/CSS/JS** for user interface

This hybrid approach provides:
- ✅ Native Windows application
- ✅ Rich Python backend
- ✅ Modern web UI
- ✅ Easy distribution

---

For more details, see:
- `electron/README.md` - Detailed Electron docs
- `ELECTRON_SETUP.md` - Setup guide
- Main Cuttle documentation in `src/docs/`

