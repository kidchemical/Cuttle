# 🖥️ Cuttle Desktop - Windows Application

> **Transform your Cuttle web application into a native Windows desktop app!**

[![Electron](https://img.shields.io/badge/Electron-28.0-blue.svg)](https://www.electronjs.org/)
[![Node](https://img.shields.io/badge/Node.js-18%2B-green.svg)](https://nodejs.org/)
[![Python](https://img.shields.io/badge/Python-3.8%2B-yellow.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE.txt)

## 🎯 What is This?

Your Cuttle application is now available as a **native Windows desktop application** using Electron! This means:

✨ **No browser required** - Runs as a standalone .exe  
✨ **Professional installer** - Easy distribution to users  
✨ **Portable version** - Single file, no installation needed  
✨ **Native features** - System tray, shortcuts, menu bar  
✨ **Better UX** - Dedicated window, custom icon, faster  

## 🚀 Quick Start (60 seconds)

### 1️⃣ Install Node.js
Download from: **https://nodejs.org/** (choose LTS version)

### 2️⃣ Run the Application
Double-click: **`start_electron.bat`**

That's it! The application will start automatically. 🎉

## 📦 Building an Installer

Want to create an installer to share with others?

Double-click: **`build_electron.bat`**

Choose what you want to build:
1. **Installer** - Professional setup wizard
2. **Portable** - Single .exe file
3. **Both** - Get both versions

Your build will be in: `electron/dist/`

## 📚 Documentation

We've created comprehensive documentation for you:

| Document | Description |
|----------|-------------|
| **[ELECTRON_SETUP.md](ELECTRON_SETUP.md)** | 📖 Complete setup guide with all details |
| **[electron/README.md](electron/README.md)** | 📖 Technical documentation |
| **[ELECTRON_ARCHITECTURE.md](ELECTRON_ARCHITECTURE.md)** | 🏗️ System architecture & internals |
| **[QUICK_START.txt](electron/QUICK_START.txt)** | ⚡ Super quick reference guide |

## 🎮 Features

### Application Features

- 🪟 **Native Windows Window** with custom icon
- 📋 **Menu Bar** with File, View, Navigate, Help menus
- ⌨️ **Keyboard Shortcuts** (Ctrl+R reload, Ctrl+Shift+I DevTools, etc.)
- 🔗 **Desktop Shortcuts** created by installer
- 🎨 **Professional Look** - appears as a real Windows app

### Build Features

- 📦 **NSIS Installer** - Full installation wizard
- 🚀 **Portable Version** - No installation required
- 🎯 **Easy Distribution** - Share a single .exe file
- 📁 **Clean Packaging** - All dependencies included

### Developer Features

- 🔧 **Hot Reload** in development mode
- 🐛 **DevTools Access** for debugging
- 📊 **Console Logging** for troubleshooting
- ⚙️ **Easy Configuration** in package.json

## 🛠️ Project Structure

```
Cuttle/
├── 📄 start_electron.bat       ← Quick launcher
├── 📄 build_electron.bat       ← Build wizard
├── 📄 ELECTRON_SETUP.md        ← Setup guide
├── 📄 ELECTRON_ARCHITECTURE.md ← Architecture docs
│
├── electron/                   ← Electron app
│   ├── main.js                ← Main process
│   ├── preload.js             ← Security bridge
│   ├── package.json           ← Config
│   ├── README.md              ← Detailed docs
│   └── dist/                  ← Built apps (after build)
│
└── src/                        ← Your Python app
    ├── web_chat_api.py        ← Flask server
    └── web/                   ← Web interface
```

## 💻 Usage

### Development Mode

For testing and development:

```bash
cd electron
npm start
```

Features:
- Opens DevTools automatically (optional)
- Shows console logs
- Fast restart
- No build required

### Building for Production

Create distributable versions:

```bash
cd electron

# Build installer
npm run build

# Build portable version
npm run build:portable

# Build both
npm run build -- --win
```

### Running Built Application

After building:
1. Navigate to `electron/dist/`
2. Find `Cuttle-1.0.0-x64.exe` (installer) or `Cuttle-1.0.0-portable.exe`
3. Double-click to run

## ⚙️ Configuration

### Change Application Name

Edit `electron/package.json`:
```json
{
  "name": "your-app-name",
  "productName": "Your App Name",
  "version": "1.0.0"
}
```

### Change Icon

Replace `src/img/cuttle_logo.ico` with your icon (must be .ico format)

### Change Port

Edit `electron/main.js`:
```javascript
const FLASK_PORT = 8080; // Change to your port
```

## 🔥 Commands Reference

| Command | Description |
|---------|-------------|
| `npm start` | Run in development mode |
| `npm run build` | Build installer (NSIS) |
| `npm run build:portable` | Build portable .exe |
| `npm run build:dir` | Build unpacked (for testing) |

## 📋 Requirements

### For Development/Running:
- ✅ **Node.js** 18+ (https://nodejs.org/)
- ✅ **Python** 3.8+ (already required for Cuttle)
- ✅ **npm** (comes with Node.js)

### For Building:
- ✅ Everything above
- ✅ ~500MB free disk space
- ✅ Windows 10/11

## 🐛 Troubleshooting

### Application won't start?

**1. Check Node.js:**
```bash
node --version
npm --version
```

**2. Install dependencies:**
```bash
cd electron
npm install
```

**3. Check Python:**
```bash
python --version
```

### Build fails?

**1. Clear cache:**
```bash
cd electron
rm -rf node_modules
npm cache clean --force
npm install
```

**2. Check disk space** (need ~500MB free)

### Port already in use?

**1. Kill existing processes:**
```bash
python src/kill_bots.py
```

**2. Or change port in `electron/main.js`**

### More help?

See the [Troubleshooting section](ELECTRON_SETUP.md#-troubleshooting) in ELECTRON_SETUP.md

## 🎯 Distribution Checklist

Before sharing your application:

- [ ] Update version number in `package.json`
- [ ] Test installer on clean Windows machine
- [ ] Verify all features work correctly
- [ ] Create release notes
- [ ] Build both installer and portable versions
- [ ] Test on Windows 10 and 11
- [ ] Consider code signing (removes SmartScreen warnings)

## 🚀 What's Included?

### Files Created

```
✅ electron/main.js           - Main Electron process
✅ electron/preload.js        - Security bridge
✅ electron/package.json      - Dependencies & build config
✅ electron/README.md         - Detailed documentation
✅ electron/.gitignore        - Git ignore rules
✅ start_electron.bat         - Quick launcher script
✅ build_electron.bat         - Interactive build wizard
✅ ELECTRON_SETUP.md          - Complete setup guide
✅ ELECTRON_ARCHITECTURE.md   - Architecture overview
✅ LICENSE.txt                - MIT License
```

### What Gets Packaged

When you build the application:
- ✅ Electron runtime (~100MB)
- ✅ Your Python backend (`src/` directory)
- ✅ Web interface (HTML/CSS/JS)
- ✅ All images and assets
- ✅ Configuration files
- ✅ Dependencies

## 🎨 Screenshots

Your application will look like this:

```
┌─────────────────────────────────────────────────────┐
│  Cuttle                                    ▢ ❐ ✕   │ ← Native window
├─────────────────────────────────────────────────────┤
│  File  View  Navigate  Help                         │ ← Menu bar
├─────────────────────────────────────────────────────┤
│                                                     │
│              [Your Web Interface]                   │
│                                                     │
│         Landing Page / Chat / Node Editor          │
│                                                     │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## 📖 Learn More

### Electron Resources
- [Electron Documentation](https://www.electronjs.org/docs)
- [Electron Builder](https://www.electron.build/)

### Cuttle Resources
- Main documentation: `src/docs/`
- Setup guides: `ELECTRON_SETUP.md`
- Architecture: `ELECTRON_ARCHITECTURE.md`

## 🤝 Contributing

Want to improve the Electron wrapper?

1. Edit files in `electron/` directory
2. Test with `npm start`
3. Build with `npm run build`
4. Submit improvements

## 📝 License

MIT License - See [LICENSE.txt](LICENSE.txt)

## 💡 Tips

💡 **First time?** Just double-click `start_electron.bat`  
💡 **Building?** Double-click `build_electron.bat`  
💡 **Debugging?** Press `Ctrl+Shift+I` in the app  
💡 **Fast testing?** Use `npm run build:dir` for unpacked build  
💡 **Sharing?** Use the installer from `electron/dist/`  

## 🎉 Success!

Your Cuttle application is now available as a native Windows application!

- ✅ Native .exe executable
- ✅ Professional installer
- ✅ Portable version
- ✅ Easy to distribute
- ✅ Better user experience

**Happy building!** 🚀

---

**Questions?** Check the documentation in `ELECTRON_SETUP.md` or `electron/README.md`

**Issues?** See the troubleshooting sections in the docs

**Want more?** Read `ELECTRON_ARCHITECTURE.md` for technical details

