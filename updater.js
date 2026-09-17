const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawn } = require('child_process');

const DEFAULT_MANIFEST_URL = 'https://roundmateai.com/downloads/manifest.json';

class AppUpdater {
  constructor(currentVersion, manifestUrl = DEFAULT_MANIFEST_URL) {
    this.currentVersion = currentVersion;
    this.manifestUrl = manifestUrl;
  }

  async checkForUpdates() {
    try {
      const manifest = await this.fetchManifest(this.manifestUrl);
      if (!manifest || !manifest.version) return { hasUpdate: false };

      const isNewer = this.compareVersions(manifest.version, this.currentVersion) > 0;
      if (!isNewer) return { hasUpdate: false, currentVersion: this.currentVersion };

      const release = manifest.releases && manifest.releases['windows-x64'];
      if (!release || !release.url) return { hasUpdate: false };

      return {
        hasUpdate: true,
        version: manifest.version,
        downloadUrl: release.url,
        sha256: release.sha256,
        size: release.size,
        releaseNotes: manifest.releaseNotes || ''
      };
    } catch (err) {
      console.error('[AppUpdater] Failed to check for updates:', err.message);
      return { hasUpdate: false, error: err.message };
    }
  }

  fetchManifest(url) {
    return new Promise((resolve, reject) => {
      if (!url.startsWith('https://')) {
        return reject(new Error('Manifest URL must use HTTPS protocol.'));
      }

      https.get(url, (res) => {
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
    const p1 = v1.split('.').map(Number);
    const p2 = v2.split('.').map(Number);
    for (let i = 0; i < Math.max(p1.length, p2.length); i++) {
      const num1 = p1[i] || 0;
      const num2 = p2[i] || 0;
      if (num1 > num2) return 1;
      if (num1 < num2) return -1;
    }
    return 0;
  }

  verifySha256(filePath, expectedSha256) {
    return new Promise((resolve, reject) => {
      if (!fs.existsSync(filePath)) return resolve(false);
      const hash = crypto.createHash('sha256');
      const stream = fs.createReadStream(filePath);

      stream.on('data', (data) => hash.update(data));
      stream.on('end', () => {
        const computed = hash.digest('hex');
        resolve(computed.toLowerCase() === expectedSha256.toLowerCase());
      });
      stream.on('error', (err) => reject(err));
    });
  }
}

module.exports = AppUpdater;
