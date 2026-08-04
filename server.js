const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const PORT = 2999;
const HOST = '127.0.0.1';

// Track if stealth is currently running to prevent double-launches
let stealthProcess = null;
let isStealthRunning = false;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css',
  '.js':   'text/javascript',
  '.png':  'image/png',
  '.ico':  'image/x-icon',
};

function launchStealth() {
  if (isStealthRunning) {
    return { success: false, message: 'Stealth toolbar is already running.' };
  }

  const electronPath = require('electron');
  const args = ['.', '--stealth'];

  console.log('\x1b[35m%s\x1b[0m', '🚀 Browser requested Stealth Mode launch...');

  stealthProcess = spawn(electronPath, args, {
    cwd: __dirname,
    stdio: 'inherit',
    detached: false
  });

  isStealthRunning = true;

  stealthProcess.on('close', (code) => {
    console.log('\x1b[90m%s\x1b[0m', `👁️  Stealth Toolbar closed (exit ${code}). Ready to launch again.`);
    stealthProcess = null;
    isStealthRunning = false;
  });

  stealthProcess.on('error', (err) => {
    console.error('\x1b[31m%s\x1b[0m', '❌ Failed to launch Electron:', err.message);
    stealthProcess = null;
    isStealthRunning = false;
  });

  console.log('\x1b[32m%s\x1b[0m', '✅ Stealth Toolbar launched from browser button!');
  return { success: true, message: 'Stealth toolbar launched successfully!' };
}

function killStealth() {
  if (!stealthProcess || !isStealthRunning) {
    return { success: false, message: 'No stealth toolbar is currently running.' };
  }
  try {
    stealthProcess.kill();
    stealthProcess = null;
    isStealthRunning = false;
    return { success: true, message: 'Stealth toolbar stopped.' };
  } catch (e) {
    return { success: false, message: 'Failed to stop: ' + e.message };
  }
}

const server = http.createServer((req, res) => {
  // CORS headers so the browser page can call us freely
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = req.url.split('?')[0];

  // ── API Endpoints ─────────────────────────────────────────────────
  if (url === '/launch' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => {
      body += chunk.toString();
    });
    req.on('end', async () => {
      let payload = {};
      try {
        if (body) payload = JSON.parse(body);
      } catch (e) { }

      // Write session config file
      try {
        fs.writeFileSync(
          path.join(__dirname, 'stealth_session_config.json'),
          JSON.stringify(payload, null, 2),
          'utf8'
        );
      } catch (err) {
        console.error('Failed to write temp session config:', err.message);
      }

      if (isStealthRunning) {
        // Forward the payload to the running Electron process on port 48999
        console.log('\x1b[35m%s\x1b[0m', '🔄 Stealth is already running. Forwarding config update to port 48999...');
        const forwarded = await new Promise((resolve) => {
          const dataStr = JSON.stringify(payload);
          const fReq = http.request({
            hostname: '127.0.0.1',
            port: 48999,
            path: '/launch',
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Content-Length': Buffer.byteLength(dataStr)
            }
          }, (fRes) => {
            resolve(fRes.statusCode === 200);
          });
          fReq.on('error', () => resolve(false));
          fReq.write(dataStr);
          fReq.end();
        });

        if (forwarded) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: true, message: 'Updated session config in running app.', running: true }));
          return;
        }
      }

      const result = launchStealth();
      res.writeHead(result.success ? 200 : 409, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ...result, running: isStealthRunning }));
    });
    return;
  }

  if (url === '/stop' && req.method === 'POST') {
    const result = killStealth();
    res.writeHead(result.success ? 200 : 409, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ...result, running: isStealthRunning }));
    return;
  }

  if (url === '/status' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ running: isStealthRunning }));
    return;
  }

  // ── Static File Serving ───────────────────────────────────────────
  let filePath;
  if (url === '/' || url === '/index') {
    filePath = path.join(__dirname, 'frontend', 'stealth-popup.html');
  } else {
    filePath = path.join(__dirname, 'frontend', url);
  }

  const ext = path.extname(filePath);
  const contentType = MIME[ext] || 'application/octet-stream';

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(data);
  });
});

server.listen(PORT, HOST, () => {
  console.log('');
  console.log('\x1b[36m%s\x1b[0m', '  ╔══════════════════════════════════════════╗');
  console.log('\x1b[36m%s\x1b[0m', '  ║      👁️  Stealth Toolbar Server            ║');
  console.log('\x1b[36m%s\x1b[0m', '  ╚══════════════════════════════════════════╝');
  console.log('');
  console.log('\x1b[32m%s\x1b[0m', `  ✅ Server running at http://${HOST}:${PORT}`);
  console.log('\x1b[33m%s\x1b[0m', `  🌐 Open in browser → http://localhost:${PORT}`);
  console.log('\x1b[90m%s\x1b[0m', '  Click "Launch Stealth Toolbar" in the page to activate.');
  console.log('');
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error('\x1b[31m%s\x1b[0m', `❌ Port ${PORT} is already in use. Is the server already running?`);
  } else {
    console.error('Server error:', err);
  }
  process.exit(1);
});
