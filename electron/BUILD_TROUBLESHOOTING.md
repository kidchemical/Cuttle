# 🔧 Electron Build Troubleshooting

## Common Build Errors and Solutions

### ❌ EACCES: permission denied (Virtual Environment)

**Error:**
```
EACCES: permission denied, lstat '/path/to/Cuttle/src\.venv_wsl\bin\python3.12'
```

**Cause:**
electron-builder is trying to package your Python virtual environment directory (`.venv`, `.venv_wsl`, `venv`, etc.), which:
- Contains platform-specific binaries
- Has symlinks that cause permission issues on Windows
- Is very large (hundreds of MB)
- Shouldn't be packaged anyway

**Solution:**
✅ **Fixed!** The `package.json` has been updated to exclude virtual environments.

The build configuration now excludes:
- `**/.venv/**` - Python virtual environments
- `**/.venv_*/**` - WSL virtual environments
- `**/venv/**` - Standard venv directories
- `**/venv_*/**` - Named venv directories

**Verify the fix:**
1. Make sure your virtual environment is in the `src/` directory
2. Common names: `.venv`, `venv`, `.venv_wsl`, `.venv_windows`
3. All of these are now excluded from the build

---

### ❌ Files too large / Build takes forever

**Cause:**
Large files or directories being included in the build.

**Solution:**
The build now excludes:
- Virtual environments (see above)
- `output/**` - Output files
- `logs/**` - Log files
- `tests/**` - Test files
- `temp_*.png` - Temporary screenshots
- Database journals
- Cache directories

**To exclude more files:**
Edit `electron/package.json` and add patterns to the `filter` array:
```json
"filter": [
  "**/*",
  "!**/your-large-directory/**"
]
```

---

### ❌ EPERM: operation not permitted

**Cause:**
File is locked by another process (antivirus, running app, etc.)

**Solution:**
1. Close the Cuttle application if running
2. Kill any Python processes:
   ```bash
   python src/kill_bots.py
   ```
3. Temporarily disable antivirus (if safe to do so)
4. Close any IDEs that might have files open
5. Try building again

---

### ❌ Cannot find module 'electron-builder'

**Cause:**
Dependencies not installed or corrupted.

**Solution:**
```bash
cd electron
rm -rf node_modules
npm cache clean --force
npm install
```

---

### ❌ Path too long (Windows)

**Error:**
```
ENAMETOOLONG: name too long
```

**Cause:**
Windows has a 260 character path limit (older systems).

**Solution:**

**Option 1: Enable long paths (Windows 10+)**
1. Open Registry Editor (`regedit`)
2. Navigate to: `HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\FileSystem`
3. Set `LongPathsEnabled` to `1`
4. Restart computer

**Option 2: Move project closer to root**
```bash
# Move project to shorter path
/path/to/Cuttle  →  C:\Cuttle
```

---

### ❌ Out of memory / JavaScript heap out of memory

**Error:**
```
FATAL ERROR: Reached heap limit
```

**Cause:**
Large files or too many files being processed.

**Solution:**

**Option 1: Increase Node memory**
```bash
cd electron
$env:NODE_OPTIONS="--max-old-space-size=4096"
npm run build
```

**Option 2: Exclude more files**
Add more exclusions to `package.json` filter.

---

### ❌ Disk space issues

**Error:**
```
ENOSPC: no space left on device
```

**Cause:**
Not enough disk space. Build requires ~500MB-1GB free.

**Solution:**
1. Free up disk space
2. Clean npm cache: `npm cache clean --force`
3. Delete old builds: `rm -rf electron/dist`
4. Try building again

---

### ❌ Python not found when running built app

**Cause:**
Built app is looking for Python but can't find it.

**Current Setup:**
The app expects Python to be installed on the user's machine and in PATH.

**Options:**

**Option 1: Require Python (Current)**
- User must install Python separately
- Simplest setup
- Smaller installer (~150-200MB)

**Option 2: Bundle Python (Advanced)**
1. Download Python embeddable package
2. Include in build
3. Update `main.js` to use bundled Python
4. Larger installer (~250-300MB)

**To bundle Python:**
1. Download Python embeddable: https://www.python.org/downloads/windows/
2. Extract to `electron/python/`
3. Update `package.json`:
```json
"extraResources": [
  {
    "from": "python",
    "to": "python"
  }
]
```
4. Update `main.js` to use bundled Python

---

### ❌ App won't start after building

**Symptoms:**
- Installer works but app crashes
- Blank window
- Immediate close

**Solutions:**

**1. Check Python installation**
```bash
python --version
pip list
```

**2. Test in development first**
```bash
cd electron
npm start
```
If it works in dev but not built, it's a packaging issue.

**3. Check console logs**
Built app logs are in:
- Windows: `%APPDATA%\Cuttle\logs\`
- Or run from command line to see logs

**4. Verify file paths**
Check that `main.js` uses correct paths for packaged app:
```javascript
const isDev = !app.isPackaged;
const scriptPath = isDev
  ? path.join(__dirname, '..', 'src', 'web_chat_api.py')
  : path.join(process.resourcesPath, 'app', 'src', 'web_chat_api.py');
```

---

## Build Best Practices

### ✅ Before Building

1. **Test in development mode**
   ```bash
   npm start
   ```
   Make sure everything works!

2. **Update version number**
   Edit `package.json`: `"version": "1.0.1"`

3. **Clean previous builds**
   ```bash
   rm -rf dist
   ```

4. **Close all apps**
   - Close Cuttle if running
   - Kill Python processes
   - Close IDEs

### ✅ Build Commands

**For testing:**
```bash
npm run build:dir
```
- Fastest build
- Unpacked files
- Easy to debug

**For distribution:**
```bash
npm run build
```
- Creates installer
- Takes longer
- Production-ready

**For portable version:**
```bash
npm run build:portable
```
- Single .exe file
- No installation
- Great for testing

### ✅ After Building

1. **Test the installer**
   - Install on clean machine if possible
   - Test all features
   - Check for errors

2. **Check file size**
   - Installer: ~150-200MB is normal
   - Portable: ~150MB is normal
   - If larger, check what's included

3. **Scan for viruses**
   - Some antiviruses flag unsigned apps
   - Test with Windows Defender
   - Consider code signing

---

## File Size Optimization

### Current Build Size
- **Installed**: ~200-250MB
  - Electron runtime: ~100MB
  - Your app files: ~50-100MB
  - Dependencies: ~50MB

### What's Included
✅ Should be included:
- Python scripts (`.py`)
- Web files (`.html`, `.css`, `.js`)
- Images and assets
- Configuration files
- Required data files

❌ Should NOT be included:
- Virtual environments
- Test files
- Development tools
- Cached files
- Temporary files
- Log files

### Check What's Being Packaged

After building unpacked version:
```bash
cd electron/dist/win-unpacked/resources/app/src
dir
```

Look for:
- ❌ `.venv` folders → should be excluded
- ❌ `__pycache__` folders → should be excluded
- ❌ `test_*.py` files → should be excluded
- ✅ `.py` files → should be included
- ✅ `web/` folder → should be included

---

## Platform-Specific Issues

### Windows-Specific

**Antivirus False Positives:**
- Windows Defender may flag unsigned apps
- Add exception or code sign
- Users may see SmartScreen warning

**Long Path Issues:**
- Enable long paths in registry
- Or move project closer to root

**Permission Issues:**
- Run as administrator if needed
- Check folder permissions

### WSL (Windows Subsystem for Linux)

**If using WSL:**
1. Virtual environments should be excluded (✅ done)
2. Build from Windows PowerShell, not WSL
3. Don't include WSL-specific files

---

## Advanced Configuration

### Exclude Additional Files

Edit `electron/package.json`:
```json
"filter": [
  "**/*",
  "!**/your-file-pattern/**"
]
```

Patterns:
- `!**/dirname/**` - Exclude directory
- `!**/*.ext` - Exclude file type
- `!**/prefix*/**` - Exclude by prefix

### Include Additional Resources

```json
"extraResources": [
  {
    "from": "path/to/files",
    "to": "destination",
    "filter": ["**/*"]
  }
]
```

### Custom Icon

Replace `src/img/cuttle_logo.ico` or update:
```json
"win": {
  "icon": "path/to/your/icon.ico"
}
```

Icon requirements:
- Format: `.ico`
- Sizes: 256x256, 128x128, 64x64, 48x48, 32x32, 16x16
- Use online converter if needed

---

## Getting Help

### Debug Information to Provide

When asking for help, include:

1. **Full error message**
2. **Build command used**
3. **Node.js version**: `node --version`
4. **npm version**: `npm --version`
5. **Operating system**: Windows 10/11
6. **Build output**: Full console output

### Useful Commands

```bash
# Check versions
node --version
npm --version
python --version

# Clean everything
cd electron
rm -rf node_modules dist
npm cache clean --force

# Reinstall
npm install

# Test in dev mode
npm start

# Build for testing
npm run build:dir

# Check what's packaged
cd dist/win-unpacked/resources/app/src
dir
```

---

## Success Checklist

After a successful build:

- [ ] Build completes without errors
- [ ] Output files exist in `electron/dist/`
- [ ] Installer file is ~150-200MB
- [ ] Can run installer
- [ ] Application starts correctly
- [ ] All features work
- [ ] No console errors
- [ ] Python backend starts
- [ ] Web interface loads

---

## Quick Reference

| Issue | Solution |
|-------|----------|
| Permission denied | Exclude virtual environments |
| Path too long | Enable long paths or move project |
| Out of memory | Increase Node heap size |
| Build too large | Check filter exclusions |
| App won't start | Check Python is installed |
| Missing modules | `npm install` |
| Cache issues | `npm cache clean --force` |

---

**Still having issues?**
1. Check `ELECTRON_SETUP.md` troubleshooting section
2. Review `electron/README.md`
3. Check Electron Builder docs: https://www.electron.build/

Good luck! 🚀

