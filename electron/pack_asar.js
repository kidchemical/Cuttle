// Targeted asar packer — packs only app source files, excludes dist/ and large binaries
const { createPackageWithOptions } = require('@electron/asar');
const path = require('path');

const src = path.join(__dirname);
const dest = path.join(__dirname, 'dist', 'win-unpacked', 'resources', 'app.asar');

createPackageWithOptions(src, dest, {
    unpack: '*.{node,dll}',
    globOptions: {
        dot: true,
        ignore: [
            // Exclude build output (contains Cuttle.exe, Electron binaries, etc.)
            path.join(src, 'dist', '**'),
            // Exclude electron binary inside node_modules (too large for asar limit)
            path.join(src, 'node_modules', 'electron', 'dist', '**'),
            // Exclude git
            path.join(src, '.git', '**'),
        ]
    }
}).then(() => {
    console.log('asar packed successfully to:', dest);
}).catch(err => {
    console.error('Pack failed:', err);
    process.exit(1);
});
