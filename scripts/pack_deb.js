const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const APP_DIR = path.resolve(__dirname, '..');
const DIST_BIN = path.join(APP_DIR, 'dist_bin');
const LINUX_UNPACKED = path.join(DIST_BIN, 'linux-unpacked');
const FRONTEND_PUBLIC = path.resolve(APP_DIR, '..', 'Sutra-AI-Frontend', 'public');

if (!fs.existsSync(LINUX_UNPACKED)) {
  console.error('[ERROR] linux-unpacked directory not found in', DIST_BIN);
  process.exit(1);
}

const STAGING_DIR = path.join(DIST_BIN, 'deb-staging');
if (fs.existsSync(STAGING_DIR)) {
  fs.rmSync(STAGING_DIR, { recursive: true, force: true });
}
fs.mkdirSync(STAGING_DIR, { recursive: true });

console.log('[1/5] Setting up Debian file hierarchy...');
const DATA_DIR = path.join(STAGING_DIR, 'data');
const OPT_RM = path.join(DATA_DIR, 'opt', 'RM');
const USR_BIN = path.join(DATA_DIR, 'usr', 'bin');
const USR_APPS = path.join(DATA_DIR, 'usr', 'share', 'applications');
const USR_ICONS = path.join(DATA_DIR, 'usr', 'share', 'icons', 'hicolor', '512x512', 'apps');

fs.mkdirSync(OPT_RM, { recursive: true });
fs.mkdirSync(USR_BIN, { recursive: true });
fs.mkdirSync(USR_APPS, { recursive: true });
fs.mkdirSync(USR_ICONS, { recursive: true });

// Copy all linux-unpacked files to /opt/RM
console.log('[2/5] Copying binaries into /opt/RM...');
cpSyncRecursive(LINUX_UNPACKED, OPT_RM);

// Copy Icon
const iconSrc = path.join(APP_DIR, 'icon.png');
if (fs.existsSync(iconSrc)) {
  fs.copyFileSync(iconSrc, path.join(USR_ICONS, 'sutra-ai.png'));
  fs.copyFileSync(iconSrc, path.join(USR_ICONS, 'rm.png'));
  fs.copyFileSync(iconSrc, path.join(OPT_RM, 'icon.png'));
}

// Create launcher shell script in /usr/bin/sutra-ai and /usr/bin/roundmate
// CRITICAL: NEVER install /usr/bin/rm because it conflicts with GNU coreutils (/bin/rm)!
const launcherScript = `#!/bin/sh
exec /opt/RM/rm "$@"
`;
fs.writeFileSync(path.join(USR_BIN, 'sutra-ai'), launcherScript, { mode: 0o755 });
fs.writeFileSync(path.join(USR_BIN, 'roundmate'), launcherScript, { mode: 0o755 });
fs.writeFileSync(path.join(USR_BIN, 'roundmate-ai'), launcherScript, { mode: 0o755 });

// Create Desktop entry
const desktopEntry = `[Desktop Entry]
Name=Sutra AI
Comment=Sutra AI (RoundMate AI) - AI Copilot for Technical Interviews
Exec=/opt/RM/rm %U
Terminal=false
Type=Application
Icon=sutra-ai
StartupWMClass=RM
Categories=Development;Utility;Office;
MimeType=x-scheme-handler/roundmate;x-scheme-handler/sutra;
`;
fs.writeFileSync(path.join(USR_APPS, 'sutra-ai.desktop'), desktopEntry);
fs.writeFileSync(path.join(USR_APPS, 'roundmate.desktop'), desktopEntry);

// Calculate installed size in KB
let totalBytes = 0;
function calcSize(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      calcSize(fullPath);
    } else {
      totalBytes += fs.statSync(fullPath).size;
    }
  }
}
calcSize(DATA_DIR);
const installedSizeKb = Math.ceil(totalBytes / 1024);

console.log(`[3/5] Installed size: ${installedSizeKb} KB. Generating control files...`);
const CONTROL_DIR = path.join(STAGING_DIR, 'control');
fs.mkdirSync(CONTROL_DIR, { recursive: true });

const controlContent = `Package: sutra-ai
Version: 1.0.1
Section: devel
Priority: optional
Architecture: amd64
Installed-Size: ${installedSizeKb}
Maintainer: RoundMate AI <support@roundmateai.com>
Depends: libnotify4, libxtst6, libnss3, libasound2, libatk-bridge2.0-0, libgtk-3-0
Description: Sutra AI (RoundMate AI) - AI Copilot for Technical Interviews
 Sutra AI is an enterprise-grade AI copilot for live technical interviews,
 system design, and coding evaluations.
`;
fs.writeFileSync(path.join(CONTROL_DIR, 'control'), controlContent.replace(/\r\n/g, '\n'));

const postinstContent = `#!/bin/sh
set -e
chmod +x /opt/RM/rm 2>/dev/null || true
chmod +x /opt/RM/chrome-sandbox 2>/dev/null || true
chmod 4755 /opt/RM/chrome-sandbox 2>/dev/null || true
chmod +x /usr/bin/sutra-ai 2>/dev/null || true
chmod +x /usr/bin/roundmate 2>/dev/null || true
chmod +x /usr/bin/roundmate-ai 2>/dev/null || true
if which update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications 2>/dev/null || true
fi
if which gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
exit 0
`;
fs.writeFileSync(path.join(CONTROL_DIR, 'postinst'), postinstContent.replace(/\r\n/g, '\n'), { mode: 0o755 });

const prermContent = `#!/bin/sh
set -e
exit 0
`;
fs.writeFileSync(path.join(CONTROL_DIR, 'prerm'), prermContent.replace(/\r\n/g, '\n'), { mode: 0o755 });

// Archive data.tar.gz and control.tar.gz using bsdtar
console.log('[4/5] Archiving data.tar.gz and control.tar.gz...');
const dataTarGz = path.join(STAGING_DIR, 'data.tar.gz');
const controlTarGz = path.join(STAGING_DIR, 'control.tar.gz');

// Pack control.tar.gz
execSync(`tar -czf "${controlTarGz}" -C "${CONTROL_DIR}" .`, { stdio: 'inherit' });

// Pack data.tar.gz
execSync(`tar -czf "${dataTarGz}" -C "${DATA_DIR}" .`, { stdio: 'inherit' });

// Create debian-binary
const debianBinary = path.join(STAGING_DIR, 'debian-binary');
fs.writeFileSync(debianBinary, '2.0\n');

// 5. Build .deb archive (standard 'ar' format)
console.log('[5/5] Combining into Debian .deb package...');
const debFileName = 'sutra-ai_1.0.1_amd64.deb';
const outputDeb = path.join(DIST_BIN, debFileName);
const legacyDeb = path.join(DIST_BIN, 'rm_1.0.1_amd64.deb');

createArArchive(outputDeb, [
  { name: 'debian-binary', path: debianBinary },
  { name: 'control.tar.gz', path: controlTarGz },
  { name: 'data.tar.gz', path: dataTarGz }
]);

// Also copy as rm_1.0.1_amd64.deb for backward compatibility
fs.copyFileSync(outputDeb, legacyDeb);

const debStats = fs.statSync(outputDeb);
console.log(`\n✓ SUCCESS! Created: ${outputDeb}`);
console.log(`  File size: ${(debStats.size / (1024 * 1024)).toFixed(2)} MB`);

// Sync both to Frontend Public
if (fs.existsSync(FRONTEND_PUBLIC)) {
  fs.copyFileSync(outputDeb, path.join(FRONTEND_PUBLIC, debFileName));
  fs.copyFileSync(outputDeb, path.join(FRONTEND_PUBLIC, 'rm_1.0.1_amd64.deb'));
  console.log(`✓ Copied to Frontend public folder (${debFileName} and rm_1.0.1_amd64.deb)`);
}

function cpSyncRecursive(src, dest) {
  const entries = fs.readdirSync(src, { withFileTypes: true });
  for (const entry of entries) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      fs.mkdirSync(destPath, { recursive: true });
      cpSyncRecursive(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

// AR Archive Builder according to GNU / Debian specifications
function createArArchive(outFile, files) {
  const fd = fs.openSync(outFile, 'w');
  // Global header: `!<arch>\n`
  fs.writeSync(fd, Buffer.from('!<arch>\n'));

  for (const file of files) {
    const stat = fs.statSync(file.path);
    const size = stat.size;
    const mtime = Math.floor(stat.mtimeMs / 1000);
    const mode = '100644';
    const uid = '0';
    const gid = '0';

    // 60-byte header:
    // name (16), mtime (12), uid (6), gid (6), mode (8), size (10), trailer (2)
    const headerBuf = Buffer.alloc(60, 0x20); // fill with spaces
    headerBuf.write(file.name, 0, 'utf8');
    headerBuf.write(String(mtime), 16, 'utf8');
    headerBuf.write(String(uid), 28, 'utf8');
    headerBuf.write(String(gid), 34, 'utf8');
    headerBuf.write(mode, 40, 'utf8');
    headerBuf.write(String(size), 48, 'utf8');
    headerBuf[58] = 0x60; // '`'
    headerBuf[59] = 0x0a; // '\n'

    fs.writeSync(fd, headerBuf);

    // Write file body
    const data = fs.readFileSync(file.path);
    fs.writeSync(fd, data);

    // Padding if odd size
    if (size % 2 !== 0) {
      fs.writeSync(fd, Buffer.from('\n'));
    }
  }

  fs.closeSync(fd);
}
