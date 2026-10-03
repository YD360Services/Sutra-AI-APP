const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');
const crypto = require('crypto');
const { spawn } = require('child_process');

const PRIMARY_MANIFEST_URL = 'https://www.roundmateai.com/manifest.json';
const FALLBACK_MANIFEST_URL = 'https://roundmateai.com/downloads/manifest.json';

class AppUpdater {
  constructor(currentVersion, manifestUrl = PRIMARY_MANIFEST_URL) {
    this.currentVersion = currentVersion;
    this.manifestUrl = manifestUrl;
  }

  async checkForUpdates() {
    try {
      let manifest;
      try {
        manifest = await this.fetchManifest(this.manifestUrl);
      } catch (err) {
        console.warn('[AppUpdater] Primary manifest fetch failed, trying fallback:', err.message);
        manifest = await this.fetchManifest(FALLBACK_MANIFEST_URL);
      }

      if (!manifest || !manifest.version) return { hasUpdate: false };

      const isNewer = this.compareVersions(manifest.version, this.currentVersion) > 0;
      if (!isNewer) return { hasUpdate: false, currentVersion: this.currentVersion, latestVersion: manifest.version };

      const release = manifest.releases && (manifest.releases['windows-x64'] || manifest.releases['win64']);
      if (!release || !release.url) return { hasUpdate: false };

      return {
        hasUpdate: true,
        version: manifest.version,
        currentVersion: this.currentVersion,
        downloadUrl: release.url,
        sha256: release.sha256,
        size: release.size,
        releaseNotes: manifest.releaseNotes || 'Bug fixes and performance improvements.'
      };
    } catch (err) {
      console.error('[AppUpdater] Failed to check for updates:', err.message);
      return { hasUpdate: false, error: err.message };
    }
  }

  fetchManifest(url) {
    return new Promise((resolve, reject) => {
      const client = url.startsWith('https://') ? https : http;
      client.get(url, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          return this.fetchManifest(res.headers.location).then(resolve).catch(reject);
        }
        if (res.statusCode !== 200) {
          return reject(new Error(`HTTP status ${res.statusCode}`));
        }
        let rawData = '';
        res.on('data', (chunk) => { rawData += chunk; });
        res.on('end', () => {
          try {
            resolve(JSON.parse(rawData));
          } catch (e) {
            reject(e);
          }
        });
      }).on('error', (err) => reject(err));
    });
  }

  compareVersions(v1, v2) {
    if (!v1 || !v2) return 0;
    const p1 = String(v1).replace(/^v/i, '').split('.').map(Number);
    const p2 = String(v2).replace(/^v/i, '').split('.').map(Number);
    for (let i = 0; i < Math.max(p1.length, p2.length); i++) {
      const num1 = p1[i] || 0;
      const num2 = p2[i] || 0;
      if (num1 > num2) return 1;
      if (num1 < num2) return -1;
    }
    return 0;
  }

  verifySha256(filePath, expectedSha256) {
    return new Promise((resolve) => {
      if (!fs.existsSync(filePath)) return resolve(false);
      const hash = crypto.createHash('sha256');
      const stream = fs.createReadStream(filePath);

      stream.on('data', (data) => hash.update(data));
      stream.on('end', () => {
        const computed = hash.digest('hex');
        resolve(computed.toLowerCase() === String(expectedSha256).toLowerCase());
      });
      stream.on('error', () => resolve(false));
    });
  }

  downloadUpdate(downloadUrl, targetPath, expectedSha256, onProgress) {
    return new Promise((resolve, reject) => {
      const tempPath = targetPath + '.downloading';
      const fileStream = fs.createWriteStream(tempPath);
      let downloadedBytes = 0;

      const client = downloadUrl.startsWith('https://') ? https : http;
      client.get(downloadUrl, (response) => {
        if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
          fileStream.close();
          fs.unlink(tempPath, () => {});
          return this.downloadUpdate(response.headers.location, targetPath, expectedSha256, onProgress)
            .then(resolve)
            .catch(reject);
        }

        if (response.statusCode !== 200) {
          fileStream.close();
          fs.unlink(tempPath, () => {});
          return reject(new Error(`Download failed with status code ${response.statusCode}`));
        }

        const totalBytes = parseInt(response.headers['content-length'] || '0', 10);

        response.on('data', (chunk) => {
          downloadedBytes += chunk.length;
          fileStream.write(chunk);
          if (onProgress && totalBytes > 0) {
            onProgress({
              transferred: downloadedBytes,
              total: totalBytes,
              percent: Math.min(100, Math.round((downloadedBytes / totalBytes) * 100))
            });
          }
        });

        response.on('end', async () => {
          fileStream.end(async () => {
            if (expectedSha256) {
              const matches = await this.verifySha256(tempPath, expectedSha256);
              if (!matches) {
                fs.unlink(tempPath, () => {});
                return reject(new Error('Downloaded installer failed SHA-256 integrity verification.'));
              }
            }
            if (fs.existsSync(targetPath)) {
              try { fs.unlinkSync(targetPath); } catch {}
            }
            fs.renameSync(tempPath, targetPath);
            resolve(targetPath);
          });
        });
      }).on('error', (err) => {
        fileStream.close();
        fs.unlink(tempPath, () => {});
        reject(err);
      });
    });
  }

  installAndRelaunch(installerPath, silent = true) {
    if (!fs.existsSync(installerPath)) {
      throw new Error(`Installer executable not found: ${installerPath}`);
    }

    const args = silent ? ['/S'] : [];
    const child = spawn(installerPath, args, {
      detached: true,
      stdio: 'ignore'
    });
    child.unref();

    setTimeout(() => {
      try {
        const { app } = require('electron');
        if (app && typeof app.quit === 'function') {
          app.quit();
          return;
        }
      } catch {}
      process.exit(0);
    }, 1500);
  }
}

module.exports = AppUpdater;
