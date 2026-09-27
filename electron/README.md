# Cuttle Desktop - Electron App

This directory contains the Electron wrapper for the Cuttle AI Agent Framework, converting the web application into a native Windows desktop application.

## 🚀 Quick Start

### Prerequisites

1. **Node.js and npm** (v18 or higher)
   - Download from: https://nodejs.org/
   - Verify installation: `node --version` and `npm --version`

2. **Python** (v3.8 or higher)
   - Already required for Cuttle backend
   - Ensure all Python dependencies are installed (see main README)

### Installation

1. Navigate to the electron directory:
   ```bash
   cd electron
   ```

2. Install Node.js dependencies:
   ```bash
   npm install
   ```

## 🎮 Running the Application

### Development Mode

Run the application in development mode (recommended for testing):

```bash
npm start
```

This will:
- Start the Python Flask backend server
- Launch the Electron window
- Connect to `http://localhost:8080`

### Features in Development Mode
- Hot reload support
- DevTools accessible via `Ctrl+Shift+I` or View menu
- Console logs visible for debugging

## 📦 Building the Application

### Build Options

**1. Build Installer (NSIS)**
```bash
npm run build
```

Creates a Windows installer:
- `dist/Cuttle-1.0.0-x64.exe` - Full installer with wizard
- Allows user to choose installation directory
- Creates desktop and Start menu shortcuts
- Includes uninstaller

**2. Build Portable Version**
```bash
npm run build:portable
```

Creates a portable executable:
- `dist/Cuttle-1.0.0-portable.exe`
- No installation required
- Can run from USB drive
- Doesn't modify system registry

**3. Build Directory (Unpacked)**
```bash
npm run build:dir
```

Creates an unpacked directory for testing:
- `dist/win-unpacked/`
- Useful for debugging packaging issues

### Build Output

All builds are output to the `electron/dist/` directory:
```
electron/dist/
  ├── Cuttle-1.0.0-x64.exe          # Installer
  ├── Cuttle-1.0.0-portable.exe      # Portable version
  └── win-unpacked/                   # Unpacked files
      └── Cuttle.exe
```

## 📋 Application Structure

```
electron/
├── main.js              # Main Electron process
├── preload.js           # Preload script (security bridge)
├── package.json         # Dependencies and build config
├── .npmrc               # npm configuration
└── README.md           # This file

Packaged Application:
├── Cuttle.exe           # Main executable
└── resources/
    └── app/
        └── src/         # Python backend and web files
```

## 🔧 Configuration

### Modify Application Settings

Edit `package.json` to customize:

- **Application Name**: Change `"name"` and `"productName"`
- **Version**: Update `"version"`
- **Icon**: Replace reference to icon file in `"build.win.icon"`
- **Build Targets**: Modify `"build.win.target"` array

### Port Configuration

The Flask server runs on port `8080` by default. To change:

1. Edit `main.js` - change `FLASK_PORT` constant
2. Ensure Python backend uses the same port

## 🐛 Troubleshooting

### Application Won't Start

1. **Check Python Installation**
   ```bash
   python --version
   ```
   Should be Python 3.8+

2. **Verify Python Dependencies**
   ```bash
   cd ../src
   pip install -r ../requirements/requirements.txt
   ```

3. **Check Console Logs**
   - Run in development mode: `npm start`
   - Open DevTools: `Ctrl+Shift+I`
   - Check Console and Terminal output

### Build Fails

1. **Clear npm cache**
   ```bash
   npm cache clean --force
   rm -rf node_modules
   npm install
   ```

2. **Check disk space**
   - Builds require ~500MB free space

3. **Verify file paths**
   - Ensure `../src` directory exists
   - Check icon path is correct

### Server Connection Failed

- Verify Flask server starts successfully
- Check port 8080 is not in use by another application
- Check firewall settings

### Python Process Doesn't Stop

If the Python process remains after closing:
1. Open Task Manager
2. Find `python.exe` processes
3. End processes manually

Or use the included kill script:
```bash
python ../src/kill_bots.py
```

## 🌟 Features

### Menu Bar

- **File**: Home, Exit
- **View**: Reload, DevTools, Zoom controls
- **Navigate**: Quick access to all pages
  - Chat
  - Node Editor
  - Task Management
  - Control Panel
  - Settings
- **Help**: About, Documentation

### Keyboard Shortcuts

- `Ctrl+R` - Reload page
- `Ctrl+Shift+R` - Force reload
- `Ctrl+Shift+I` - Toggle DevTools
- `Ctrl+0` - Reset zoom
- `Ctrl++` - Zoom in
- `Ctrl+-` - Zoom out
- `F11` - Toggle fullscreen

## 🔐 Security

This Electron app uses best practices:

- **Context Isolation**: Enabled
- **Node Integration**: Disabled in renderer
- **Preload Script**: Secure bridge between processes
- **Web Security**: Enabled

## 📝 Development Notes

### Adding New Features

1. **Modify Python Backend**
   - Changes in `../src/` are automatically included in builds
   - No need to rebuild Electron app

2. **Modify Electron Wrapper**
   - Changes in `main.js` or `preload.js` require restart
   - Run `npm start` to test

3. **Add Menu Items**
   - Edit the `template` array in `main.js`
   - Add new navigation items as needed

### Debugging

**Python Backend Issues**:
- Check terminal output when running `npm start`
- Python stdout/stderr is logged to console

**Electron Issues**:
- Use DevTools: `Ctrl+Shift+I`
- Check main process logs in terminal

## 📄 License

MIT License - See LICENSE.txt in the root directory

## 🤝 Support

For issues or questions:
1. Check the main Cuttle documentation
2. Review the troubleshooting section above
3. Open an issue on GitHub

## 🎯 Next Steps

After building your application:

1. **Test the installer**
   - Install on a clean Windows machine
   - Verify all features work correctly

2. **Code Signing** (Optional but recommended)
   - Prevents Windows SmartScreen warnings
   - Requires a code signing certificate

3. **Auto-Updates** (Future enhancement)
   - Implement electron-updater
   - Set up update server

4. **Distribution**
   - Upload to GitHub Releases
   - Create download page
   - Provide checksums for security

---

Built with ❤️ using Electron

