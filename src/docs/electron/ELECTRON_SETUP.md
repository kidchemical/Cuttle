# 🖥️ Cuttle Desktop - Electron Setup Guide

This guide will help you convert your Cuttle web application into a native Windows desktop application using Electron.

## 📋 What Has Been Created

Your project now includes a complete Electron wrapper:

```
Cuttle/
├── electron/                    # Electron application
│   ├── main.js                 # Main Electron process
│   ├── preload.js              # Security bridge
│   ├── package.json            # Node dependencies & build config
│   ├── .npmrc                  # npm configuration
│   ├── .gitignore              # Git ignore rules
│   └── README.md               # Detailed documentation
├── start_electron.bat          # Quick launcher script
├── build_electron.bat          # Build wizard script
├── LICENSE.txt                 # MIT License
└── ELECTRON_SETUP.md          # This file
```

## 🚀 Quick Start (3 Steps)

### Step 1: Install Node.js

Download and install Node.js (v18 or higher) from:
👉 **https://nodejs.org/**

Choose the "LTS" (Long Term Support) version.

Verify installation:
```bash
node --version
npm --version
```

### Step 2: Install Dependencies

Double-click `start_electron.bat` or run:

```bash
cd electron
npm install
```

This will download Electron and electron-builder (~200MB).

### Step 3: Run the Application

**Option A: Use the launcher script** (Easiest)
- Double-click `start_electron.bat`

**Option B: Manual start**
```bash
cd electron
npm start
```

The application will:
1. ✅ Start the Python Flask backend
2. ✅ Open a native window
3. ✅ Load the Cuttle interface

## 📦 Building the Application

### Interactive Build Wizard

Double-click `build_electron.bat` and choose:
1. **Full Installer** - Professional installer with wizard
2. **Portable Version** - Single .exe, no installation required
3. **Both** - Creates both installer and portable version
4. **Directory** - Unpacked files for testing

### Manual Build Commands

```bash
cd electron

# Build installer
npm run build

# Build portable version
npm run build:portable

# Build unpacked directory (for testing)
npm run build:dir
```

### Build Output

All builds are saved to `electron/dist/`:

```
electron/dist/
├── Cuttle-1.0.0-x64.exe          # ⭐ Installer (recommended)
├── Cuttle-1.0.0-portable.exe     # ⭐ Portable version
└── win-unpacked/                  # Unpacked files
    └── Cuttle.exe
```

## 🎯 Features

### What You Get

✅ **Native Windows Application**
- Runs as a standalone .exe
- No browser required
- Desktop shortcuts
- Start menu integration

✅ **Professional Installer**
- Custom installation directory
- Desktop & Start menu shortcuts
- Proper uninstaller
- Windows-compliant

✅ **Portable Version**
- Single executable
- No installation required
- Run from USB drive
- Perfect for distribution

✅ **Enhanced User Experience**
- Native window with custom icon
- Application menu bar
- Keyboard shortcuts
- Better performance than browser

### Application Menu

**File**
- Home - Return to landing page
- Exit - Close application

**View**
- Reload / Force Reload
- Toggle DevTools (for debugging)
- Zoom controls
- Fullscreen toggle

**Navigate**
- Chat - AI chat interface
- Node Editor - Visual pipeline builder
- Task Management - Task tracking
- Control Panel - System controls
- Settings - Configuration

**Help**
- About - Application info
- Documentation - Open docs

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+R` | Reload page |
| `Ctrl+Shift+R` | Force reload |
| `Ctrl+Shift+I` | Toggle DevTools |
| `Ctrl+0` | Reset zoom |
| `Ctrl++` | Zoom in |
| `Ctrl+-` | Zoom out |
| `F11` | Toggle fullscreen |

## 🔧 Configuration

### Change Application Name

Edit `electron/package.json`:
```json
{
  "name": "your-app-name",
  "productName": "Your App Name",
  "version": "1.0.0",
  "description": "Your description"
}
```

### Change Icon

1. Replace `src/img/cuttle_logo.ico` with your icon
2. Icon must be `.ico` format
3. Recommended sizes: 256x256, 128x128, 64x64, 48x48, 32x32, 16x16

### Change Port

The Flask server runs on port 8080 by default.

To change it:
1. Edit `electron/main.js` - change `FLASK_PORT` constant
2. Ensure Python backend uses the same port

### Customize Build

Edit `electron/package.json` under `"build"` section:

```json
"build": {
  "appId": "com.yourcompany.yourapp",
  "productName": "Your App Name",
  "win": {
    "target": ["nsis", "portable"],
    "icon": "../src/img/your_icon.ico"
  }
}
```

## 🐛 Troubleshooting

### "Node.js is not installed"

**Solution**: Install Node.js from https://nodejs.org/

### "Python process failed to start"

**Solutions**:
1. Verify Python is installed: `python --version`
2. Install Python dependencies:
   ```bash
   cd src
   pip install -r ../requirements/requirements.txt
   ```
3. Check Python is in system PATH

### "Port 8080 is already in use"

**Solutions**:
1. Close any other applications using port 8080
2. Change the port in `electron/main.js`
3. Kill existing Python processes:
   ```bash
   python src/kill_bots.py
   ```

### Build fails with "Cannot find module"

**Solution**:
```bash
cd electron
rm -rf node_modules
npm cache clean --force
npm install
```

### Application window is blank

**Solutions**:
1. Check if Flask server is running (look at console output)
2. Open DevTools: `Ctrl+Shift+I` and check for errors
3. Try force reload: `Ctrl+Shift+R`

### Windows SmartScreen warning

**Explanation**: Windows shows warnings for unsigned applications.

**Solutions**:
1. Click "More info" → "Run anyway" (for testing)
2. Get a code signing certificate (for distribution)

### Python process doesn't stop

**Solution**:
1. Open Task Manager
2. Find and end `python.exe` processes
3. Or run: `python src/kill_bots.py`

## 📊 Technical Details

### How It Works

1. **Electron Main Process** (`main.js`)
   - Spawns Python Flask server
   - Creates native window
   - Manages application lifecycle

2. **Python Backend** (Flask)
   - Runs on `localhost:8080`
   - Handles all API requests
   - Provides web interface

3. **Electron Renderer**
   - Displays web content in native window
   - Communicates with backend via HTTP

### Security

- ✅ Context isolation enabled
- ✅ Node integration disabled in renderer
- ✅ Preload script for secure bridge
- ✅ Web security enabled

### File Structure When Packaged

```
Cuttle.exe                      # Main executable
└── resources/
    ├── app.asar                # Electron app
    └── app/
        └── src/                # Your Python app
            ├── web_chat_api.py
            ├── web/            # HTML/JS/CSS
            └── ...             # All other files
```

## 🚀 Distribution

### For End Users

1. **Build the installer**:
   ```bash
   cd electron
   npm run build
   ```

2. **Share the installer**:
   - Upload `dist/Cuttle-1.0.0-x64.exe` to your website
   - Share via GitHub Releases
   - Distribute on USB drives

3. **Users run the installer**:
   - Double-click the .exe
   - Follow installation wizard
   - Launch from desktop shortcut

### For Developers

1. **Share the portable version**:
   - `dist/Cuttle-1.0.0-portable.exe`
   - Single file, no installation
   - Perfect for testing

## 🔐 Code Signing (Optional)

To remove Windows SmartScreen warnings:

1. **Get a certificate**:
   - Purchase from a Certificate Authority
   - Or use DigiCert, Sectigo, etc.
   - Costs ~$100-300/year

2. **Configure electron-builder**:
   ```json
   "win": {
     "certificateFile": "path/to/certificate.pfx",
     "certificatePassword": "your-password"
   }
   ```

3. **Build signed app**:
   ```bash
   npm run build
   ```

## 📚 Additional Resources

### Electron Documentation
- https://www.electronjs.org/docs

### Electron Builder Documentation
- https://www.electron.build/

### Flask Documentation
- https://flask.palletsprojects.com/

### Cuttle Documentation
- See `src/docs/` directory

## 🎨 Customization Ideas

### Add System Tray Icon

Add to `main.js`:
```javascript
const { Tray } = require('electron');
let tray = new Tray('path/to/icon.png');
```

### Add Auto-Updates

1. Install electron-updater:
   ```bash
   npm install electron-updater
   ```

2. Configure update server in package.json

### Add Native Notifications

Use Electron's Notification API:
```javascript
const { Notification } = require('electron');
new Notification({ title: 'Title', body: 'Message' }).show();
```

## ✅ Checklist

Before distributing your application:

- [ ] Test installer on clean Windows machine
- [ ] Verify all features work correctly
- [ ] Update version number in package.json
- [ ] Create release notes
- [ ] Test portable version
- [ ] Check antivirus doesn't flag the app
- [ ] Verify Python dependencies are included
- [ ] Test on different Windows versions (10, 11)
- [ ] Create user documentation
- [ ] Consider code signing certificate

## 🎉 Success!

You now have:
- ✅ Native Windows application
- ✅ Professional installer
- ✅ Portable executable
- ✅ Build automation scripts
- ✅ Complete documentation

Your Cuttle application is ready to be distributed as a Windows desktop app!

## 💡 Tips

1. **Development**: Use `npm start` for quick testing
2. **Testing Builds**: Use `npm run build:dir` for faster builds
3. **Distribution**: Use `npm run build` for production
4. **Updates**: Just rebuild and share new version
5. **Debugging**: Use DevTools (`Ctrl+Shift+I`) when running

## 🆘 Need Help?

If you encounter issues:

1. Check the troubleshooting section above
2. Review `electron/README.md` for detailed info
3. Check Electron and electron-builder documentation
4. Open an issue on GitHub

---

**Built with ❤️ using Electron**

Happy building! 🚀

