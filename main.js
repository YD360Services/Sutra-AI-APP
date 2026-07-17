const { app, BrowserWindow, ipcMain, screen, desktopCapturer, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const https = require('https');
const { exec } = require('child_process');

let mainWindow;
let activeSessionId = null;
let activeSessionStartTime = null;
let isToolbarMode = false;


// Load environment variables manually to avoid external dependency issues
function loadEnv() {
  const envPath = path.join(__dirname, '.env');
  const env = {};
  if (fs.existsSync(envPath)) {
    try {
      const content = fs.readFileSync(envPath, 'utf8');
      const lines = content.split(/\r?\n/);
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) continue;
        const index = trimmed.indexOf('=');
        if (index > -1) {
          const key = trimmed.substring(0, index).trim();
          const value = trimmed.substring(index + 1).trim();
          env[key] = value.replace(/^['"]|['"]$/g, '');
        }
      }
    } catch (e) {
      console.error('Failed to read .env file:', e.message);
    }
  }
  return env;
}

const env = loadEnv();

const geminiKey = (env.GEMINI_KEY || env.GEMINI_API_KEY || '').trim();

if (geminiKey) {
  console.log('[Stealth Init] Gemini API key successfully loaded');
} else {
  console.warn('[Stealth Init] Warning: GEMINI_KEY is missing in .env');
}

if (env.DEEPGRAM_API_KEY) {
  console.log('[Stealth Init] Deepgram API key successfully loaded');
} else {
  console.warn('[Stealth Init] Warning: DEEPGRAM_API_KEY is missing in .env');
}

const defaultModels = ['gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-1.5-flash-latest', 'gemini-pro'];

// Native HTTPS request to Gemini API
function makeGeminiRequest(key, prompt, base64Image = null, modelIndex = 0) {
  const model = env.GEMINI_MODEL || defaultModels[modelIndex] || 'gemini-1.5-flash';
  return new Promise((resolve, reject) => {
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${key}`;

    const parts = [
      {
        text: prompt
      }
    ];

    if (base64Image) {
      parts.push({
        inlineData: {
          mimeType: 'image/png',
          data: base64Image
        }
      });
    }

    const requestData = JSON.stringify({
      contents: [
        {
          parts: parts
        }
      ],
      generationConfig: {
        maxOutputTokens: 2048,
        temperature: 0.2
      }
    });

    const options = {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(requestData)
      }
    };

    const req = https.request(url, options, (res) => {
      let responseBody = '';
      res.on('data', (chunk) => responseBody += chunk);
      res.on('end', () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            const parsed = JSON.parse(responseBody);
            if (parsed.candidates && parsed.candidates[0] && parsed.candidates[0].content && parsed.candidates[0].content.parts[0]) {
              const text = parsed.candidates[0].content.parts[0].text;
              console.log(`[Gemini API] Success. Full text:\n${text}`);
              console.log(`[Gemini API] Raw Candidate: ${JSON.stringify(parsed.candidates[0])}`);
              resolve(text);
            } else {
              reject(new Error(`Unexpected Gemini response format: ${responseBody}`));
            }
          } catch (e) {
            reject(new Error(`Failed to parse Gemini JSON: ${e.message}`));
          }
        } else {
          try {
            const parsedErr = JSON.parse(responseBody);
            if (parsedErr.error && parsedErr.error.message) {
              const errMsg = parsedErr.error.message;
              // If the model was not found/recognized, attempt next fallback model index
              if (!env.GEMINI_MODEL && (errMsg.includes('not found') || errMsg.includes('NotFound') || errMsg.includes('not recognized') || errMsg.includes('Model')) && modelIndex < defaultModels.length - 1) {
                console.warn(`[Gemini API] Model ${model} failed with: ${errMsg}. Trying fallback ${defaultModels[modelIndex + 1]}...`);
                makeGeminiRequest(key, prompt, base64Image, modelIndex + 1).then(resolve).catch(reject);
                return;
              }
              reject(new Error(`API Error: ${errMsg}`));
              return;
            }
          } catch (e) { }
          reject(new Error(`HTTP ${res.statusCode}: ${responseBody}`));
        }
      });
    });

    req.on('error', (err) => {
      reject(err);
    });

    req.write(requestData);
    req.end();
  });
}

// Call Gemini using the configured API key
async function callGeminiWithRotation(prompt, base64Image = null) {
  if (!geminiKey) {
    throw new Error('No Gemini API key found in your .env file. Please define GEMINI_KEY.');
  }
  console.log('[Gemini API] Sending request using active key.');
  return makeGeminiRequest(geminiKey, prompt, base64Image);
}

function loadSessionConfig() {
  const configPath = path.join(__dirname, 'stealth_session_config.json');
  if (fs.existsSync(configPath)) {
    try {
      const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
      fs.unlinkSync(configPath); // Delete immediately so it's only read once
      return config;
    } catch (e) {
      console.error('Failed to read stealth_session_config.json:', e.message);
    }
  }
  return null;
}

function loadSavedBounds() {
  const filePath = path.join(app.getPath('userData'), 'stealth_window_state.json');
  if (fs.existsSync(filePath)) {
    try {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      if (data && typeof data.x === 'number' && typeof data.y === 'number') {
        let width = data.width || 600;
        let height = data.height || 56;

        // Ignore small dimensions (shrunk state bounds)
        if (width < 100 || height < 100) {
          return null;
        }

        // Sanitize bounds to prevent vertical stick glitches
        if (width < 300) width = 600;
        if (height < 40) height = 56;

        const rect = { x: data.x, y: data.y, width, height };
        const displays = screen.getAllDisplays();
        const isVisible = displays.some(display => {
          const bounds = display.bounds;
          return rect.x < bounds.x + bounds.width &&
            rect.x + rect.width > bounds.x &&
            rect.y < bounds.y + bounds.height &&
            rect.y + rect.height > bounds.y;
        });
        if (isVisible) {
          return rect;
        }
      }
    } catch (e) {
      console.error('Failed to load saved window bounds:', e.message);
    }
  }
  return null;
}

function saveSavedBounds(bounds) {
  const filePath = path.join(app.getPath('userData'), 'stealth_window_state.json');
  try {
    let existing = {};
    if (fs.existsSync(filePath)) {
      try {
        existing = JSON.parse(fs.readFileSync(filePath, 'utf8')) || {};
      } catch (e) {}
    }

    const dataToSave = {
      x: bounds.x,
      y: bounds.y,
      width: (bounds.width >= 100) ? bounds.width : (existing.width || 600),
      height: (bounds.height >= 100) ? bounds.height : (existing.height || 56)
    };

    fs.writeFileSync(filePath, JSON.stringify(dataToSave, null, 2), 'utf8');
  } catch (e) {
    console.error('Failed to save window bounds:', e.message);
  }
}

function createWindow() {

  // Read and apply configuration if launcher passed one
  const config = loadSessionConfig();
  if (config) {
    try {
      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      const contextData = {
        resume: config.resume || '',
        job_description: config.jd || '',
        code_context: '',
        company: config.company || 'Stealth Practice',
        role: config.role || 'Software Engineer'
      };
      fs.writeFileSync(localPath, JSON.stringify(contextData, null, 2), 'utf8');
      console.log('[Stealth Config] Successfully initialized active resume and JD context.');
    } catch (e) {
      console.error('[Stealth Config] Failed to write context data:', e.message);
    }
  }

  // Always boot directly in setup wizard configuration dimensions
  const winWidth = 600;
  const winHeight = 580;

  mainWindow = new BrowserWindow({
    title: "Brazilian Space",
    width: winWidth,
    height: winHeight,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: true,
    skipTaskbar: true,
    type: 'toolbar',
    minWidth: 0,
    minHeight: 0,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  // Position centered at the top of the display
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth, y: screenY, x: screenX } = primaryDisplay.workArea;
  const x = Math.round((screenWidth - winWidth) / 2) + screenX;
  mainWindow.setBounds({ x, y: screenY, width: winWidth, height: winHeight });

  // Load the root index.html (stealth toolbar)
  mainWindow.loadFile(path.join(__dirname, 'frontend', 'index.html'));

  // Enable screen capture protection
  mainWindow.setContentProtection(true);

  mainWindow.show();
  mainWindow.setResizable(false);
  mainWindow.focus();
  // mainWindow.webContents.openDevTools({ mode: 'detach' });

  // Handle click-through toggle
  ipcMain.on('set-ignore-mouse-events', (event, ignore, options) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) {
      win.setIgnoreMouseEvents(ignore, options);
    }
  });

  // Handle open external URL
  ipcMain.on('open-external-url', (event, url) => {
    shell.openExternal(url);
  });

  // Handle desktop sources request for loopback system audio capture
  ipcMain.handle('get-desktop-sources', async () => {
    try {
      const sources = await desktopCapturer.getSources({ types: ['screen', 'window'] });
      return sources.map(s => ({ id: s.id, name: s.name }));
    } catch (e) {
      console.error('Failed to get desktop sources:', e.message);
      return [];
    }
  });

  // Handle request for Deepgram key (offline mode)
  ipcMain.handle('get-deepgram-key', () => {
    return env.DEEPGRAM_API_KEY || '';
  });

  ipcMain.handle('get-saved-bounds', () => {
    return loadSavedBounds();
  });

  ipcMain.handle('restore-saved-bounds', () => {
    if (mainWindow) {
      isToolbarMode = true;
      const bounds = loadSavedBounds();
      if (bounds) {
        mainWindow.setBounds({
          x: Math.round(bounds.x),
          y: Math.round(bounds.y),
          width: Math.round(bounds.width),
          height: Math.round(bounds.height)
        });
        return true;
      }
    }
    return false;
  });


  // Handle taking a screenshot for solving coding questions
  ipcMain.handle('take-screenshot', async () => {
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    try {
      const primaryDisplay = screen.getPrimaryDisplay();
      const { width, height } = primaryDisplay.size;

      // Temporarily disable content protection so desktopCapturer
      // can capture what's on screen (other apps, coding problems etc.)
      // The stealth bar itself is already excluded from screen capture
      // by virtue of the OS-level protection on all other frames.
      mainWindow.setContentProtection(false);
      await wait(150); // brief settle time

      const sources = await desktopCapturer.getSources({
        types: ['screen'],
        thumbnailSize: { width: Math.round(width), height: Math.round(height) }
      });

      // Re-enable content protection immediately after capture
      mainWindow.setContentProtection(true);

      if (sources.length === 0) {
        throw new Error('No screen sources found');
      }

      const imageBuffer = sources[0].thumbnail.toPNG();
      return imageBuffer.toString('base64');
    } catch (e) {
      // Always restore content protection on error
      try { mainWindow.setContentProtection(true); } catch (_) { }
      console.error('[Screenshot IPC] Failed:', e.message);
      throw e;
    }
  });

  // Handle Gemini Query (offline mode — used when no BACKEND_URL set)
  ipcMain.handle('query-gemini', async (event, prompt, base64Image = null) => {
    try {
      // Log offline prompts to backend/logs/prompt_debug/ for debugging
      try {
        const logDir = path.join(__dirname, 'backend', 'logs', 'prompt_debug');
        if (!fs.existsSync(logDir)) {
          fs.mkdirSync(logDir, { recursive: true });
        }
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        const sourceName = base64Image ? 'screenshot' : 'offline_query';
        const fname = path.join(logDir, `${ts}_${sourceName}.txt`);
        fs.writeFileSync(
          fname,
          `=== OFFLINE PROMPT DEBUG LOG ===\n` +
          `Timestamp: ${new Date().toISOString()}\n` +
          `Source   : ${sourceName}\n\n` +
          `${prompt}`,
          'utf8'
        );
      } catch (logErr) {
        console.error('Failed to log offline query:', logErr.message);
      }

      return await callGeminiWithRotation(prompt, base64Image);
    } catch (e) {
      console.error('[Gemini IPC] Failed query:', e.message);
      return `Error: ${e.message}`;
    }
  });

  // ── Backend (Hosted SaaS) IPC handlers ────────────────────────────────────

  // Return the backend URL so renderer can decide online vs offline mode
  ipcMain.handle('get-backend-url', () => {
    return (env.BACKEND_URL || '').trim();
  });

  // Helper: make an authenticated request to the backend
  function backendRequest(method, path, body, token) {
    const backendUrl = (env.BACKEND_URL || '').trim();
    if (!backendUrl) return Promise.reject(new Error('BACKEND_URL not configured.'));

    const url = new URL(path, backendUrl);
    const useHttps = url.protocol === 'https:';
    const lib = useHttps ? https : require('http');

    const bodyStr = body ? JSON.stringify(body) : null;
    const options = {
      method,
      hostname: url.hostname,
      port: url.port || (useHttps ? 443 : 80),
      path: url.pathname + url.search,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(bodyStr ? { 'Content-Length': Buffer.byteLength(bodyStr) } : {}),
      },
    };

    return new Promise((resolve, reject) => {
      const req = lib.request(options, (res) => {
        let raw = '';
        res.on('data', (c) => (raw += c));
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode, data: JSON.parse(raw) });
          } catch {
            resolve({ status: res.statusCode, data: raw });
          }
        });
      });
      req.on('error', reject);
      if (bodyStr) req.write(bodyStr);
      req.end();
    });
  }

  // Create a new session on the backend → returns { session_id, token }
  ipcMain.handle('create-backend-session', async (event, userId, config) => {
    try {
      let companyName = config?.company_name || 'Stealth AI';
      let roleName = config?.role_name || 'Software Engineer';

      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      if (fs.existsSync(localPath)) {
        try {
          const content = JSON.parse(fs.readFileSync(localPath, 'utf8'));
          if (!config?.company_name && content.company) companyName = content.company;
          if (!config?.role_name && content.role) roleName = content.role;
        } catch (e) { }
      }

      const payload = {
        session_name: config?.session_name || `Stealth Session with ${companyName}`,
        company_name: companyName,
        role_name: roleName,
        language: config?.language || 'English',
        audio_source: config?.audio_source || 'browser_tab_audio',
        job_description_id: config?.job_description_id || null
      };
      const pathWithQuery = userId ? `/api/sessions?user_id=${userId}` : '/api/sessions';
      const { data, status } = await backendRequest('POST', pathWithQuery, payload);
      if (status >= 400 || !data?.id) {
        throw new Error(data?.detail || 'Failed to create session on FastAPI backend');
      }

      // Save active session info to track duration if app quits/closes abruptly
      activeSessionId = data.id;
      activeSessionStartTime = Date.now();

      return { session_id: data.id, token: data.id };
    } catch (e) {
      console.error('[Backend IPC] create-backend-session failed:', e.message);
      return { error: e.message };
    }
  });

  // Update a session on the backend (e.g. save duration and complete status)
  ipcMain.handle('update-backend-session', async (event, sessionId, updateData) => {
    try {
      const { data, status } = await backendRequest('PATCH', `/api/sessions/${sessionId}`, updateData);
      if (status >= 400) {
        throw new Error(data?.detail || 'Failed to update session');
      }

      // If completed cleanly, clear active session tracking
      if (sessionId === activeSessionId && updateData?.status === 'completed') {
        activeSessionId = null;
        activeSessionStartTime = null;
      }

      return data;
    } catch (e) {
      console.error('[Backend IPC] update-backend-session failed:', e.message);
      return { error: e.message };
    }
  });

  // Save a transcript block to the backend database
  ipcMain.handle('save-transcript-block', async (event, sessionId, blockData) => {
    try {
      const payload = {
        session_id: sessionId,
        speaker: blockData.speaker || 'interviewer',
        content: blockData.content || '',
        source: blockData.source || 'browser_audio'
      };
      const { data, status } = await backendRequest('POST', '/api/transcripts', payload);
      if (status >= 400) {
        console.error('[Backend IPC] save-transcript-block request failed:', { status, data });
        throw new Error(data?.detail || (typeof data === 'string' ? data : null) || 'Failed to save transcript block');
      }
      return data;
    } catch (e) {
      console.error('[Backend IPC] save-transcript-block failed:', e.message);
      return { error: e.message };
    }
  });

  // Upload and solve screenshot on the backend via multipart/form-data
  ipcMain.handle('solve-screenshot-backend', async (event, { base64Image, sessionToken, model }) => {
    try {
      const backendUrl = (env.BACKEND_URL || '').trim();
      if (!backendUrl) throw new Error('BACKEND_URL not configured');

      const url = new URL('/api/screenshot', backendUrl);
      const useHttps = url.protocol === 'https:';
      const lib = useHttps ? https : require('http');

      const buffer = Buffer.from(base64Image, 'base64');
      const boundary = '----StealthBoundary' + Math.random().toString(16);

      const parts = [];

      // File parameter
      parts.push(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="file"; filename="screenshot.png"\r\n` +
        `Content-Type: image/png\r\n\r\n`
      );
      parts.push(buffer);
      parts.push('\r\n');

      // Session ID parameter (if present)
      if (sessionToken) {
        parts.push(
          `--${boundary}\r\n` +
          `Content-Disposition: form-data; name="session_id"\r\n\r\n` +
          `${sessionToken}\r\n`
        );
      }

      // Model parameter (if present)
      if (model) {
        parts.push(
          `--${boundary}\r\n` +
          `Content-Disposition: form-data; name="model"\r\n\r\n` +
          `${model}\r\n`
        );
      }

      parts.push(`--${boundary}--\r\n`);

      const bodyBuffer = Buffer.concat(
        parts.map(p => (typeof p === 'string' ? Buffer.from(p, 'utf8') : p))
      );

      const options = {
        method: 'POST',
        hostname: url.hostname,
        port: url.port || (useHttps ? 443 : 80),
        path: url.pathname,
        headers: {
          'Content-Type': `multipart/form-data; boundary=${boundary}`,
          'Content-Length': bodyBuffer.length
        }
      };

      return new Promise((resolve, reject) => {
        const req = lib.request(options, (res) => {
          let raw = '';
          res.on('data', (c) => (raw += c));
          res.on('end', () => {
            try {
              const result = JSON.parse(raw);
              if (res.statusCode >= 400) {
                reject(new Error(result.detail || 'Screenshot upload failed'));
              } else {
                resolve(result);
              }
            } catch (e) {
              reject(new Error('Invalid response from backend'));
            }
          });
        });
        req.on('error', reject);
        req.write(bodyBuffer);
        req.end();
      });

    } catch (e) {
      console.error('[Backend IPC] solve-screenshot-backend failed:', e.message);
      return { error: e.message };
    }
  });

  // Query the backend's 4-layer memory AI endpoint
  ipcMain.handle('query-backend', async (event, { full_transcript, manual_question, last_offset, token }) => {
    const startTime = Date.now();
    try {
      // Load local context (L4) if available
      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      let resume = '';
      let jd = '';
      let model = '';
      if (fs.existsSync(localPath)) {
        try {
          const content = JSON.parse(fs.readFileSync(localPath, 'utf8'));
          resume = content.resume || '';
          jd = content.job_description || '';
          model = content.model || '';
        } catch (e) { }
      }

      const newTranscript = (full_transcript && typeof last_offset === 'number') ? full_transcript.slice(last_offset).trim() : (full_transcript || '');
      const payload = {
        session_id: token || null,
        question: manual_question || null,
        transcript: newTranscript || null,
        source_type: manual_question ? 'manual' : 'transcript',
        resume_content: resume || null,
        knowledge_content: jd || null,
        model: model || null
      };

      const { data, status } = await backendRequest(
        'POST', '/api/answer',
        payload,
        token
      );
      if (status >= 400 || !data) return { error: data?.detail || 'Backend error' };

      return {
        answer: data.answer,
        question_detected: data.question || manual_question || '',
        new_offset: full_transcript.length,
        latency_ms: Date.now() - startTime
      };
    } catch (e) {
      console.error('[Backend IPC] query-backend failed:', e.message);
      return { error: e.message };
    }
  });

  // ── STREAMING backend query: pipes /api/answer/stream chunks to renderer in real-time ──
  ipcMain.handle('query-backend-stream', async (event, { full_transcript, manual_question, last_offset, token }) => {
    const startTime = Date.now();
    try {
      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      let resume = '', resume_id = '', jd = '', doc_id = '', doc_type = '', model = '';
      if (fs.existsSync(localPath)) {
        try {
          const content = JSON.parse(fs.readFileSync(localPath, 'utf8'));
          resume = content.resume || '';
          resume_id = content.resume_id || '';
          jd = content.job_description || '';
          doc_id = content.doc_id || '';
          doc_type = content.doc_type || '';
          model = content.model || '';
        } catch (e) { }
      }

      // Format knowledge_content parameter to help the backend query the active database document / prompt
      let knowledgeContent = '';
      if (doc_id && doc_id !== '__upload__') {
        const prefix = (doc_type === 'prompt') ? 'prompt_id' : 'doc_id';
        knowledgeContent = `${prefix}:${doc_id}|`;
      } else {
        knowledgeContent = jd || '';
      }

      const newTranscript = (full_transcript && typeof last_offset === 'number') ? full_transcript.slice(last_offset).trim() : (full_transcript || '');
      const payload = {
        session_id: token || null,
        question: manual_question || null,
        transcript: newTranscript || null,
        source_type: manual_question ? 'manual' : 'transcript',
        resume_content: resume_id || resume || null,
        knowledge_content: knowledgeContent || null,
        model: model || null
      };

      const backendUrl = (env.BACKEND_URL || '').trim();
      if (!backendUrl) throw new Error('BACKEND_URL not configured.');

      const url = new URL('/api/answer/stream', backendUrl);
      const useHttps = url.protocol === 'https:';
      const lib = useHttps ? https : require('http');
      const bodyStr = JSON.stringify(payload);

      return new Promise((resolve, reject) => {
        const options = {
          method: 'POST',
          hostname: url.hostname,
          port: url.port || (useHttps ? 443 : 80),
          path: url.pathname,
          headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(bodyStr),
            ...(token ? { Authorization: `Bearer ${token}` } : {})
          }
        };

        const req = lib.request(options, (res) => {
          if (res.statusCode >= 400) {
            let errBody = '';
            res.on('data', c => errBody += c);
            res.on('end', () => {
              try { resolve({ error: JSON.parse(errBody)?.detail || 'Backend stream error' }); }
              catch { resolve({ error: 'Backend stream error' }); }
            });
            return;
          }

          let buffer = '';
          let firstChunk = true;

          res.on('data', (chunk) => {
            const text = chunk.toString('utf8');
            buffer += text;

            // Parse accumulated JSON buffer — the stream sends raw JSON tokens
            // Forward each character chunk immediately to the renderer
            if (event.sender && !event.sender.isDestroyed()) {
              if (firstChunk) {
                event.sender.send('answer-stream-first', { ttft_ms: Date.now() - startTime });
                firstChunk = false;
              }
              event.sender.send('answer-stream-chunk', { chunk: text });
            }
          });

          res.on('end', () => {
            if (event.sender && !event.sender.isDestroyed()) {
              event.sender.send('answer-stream-end', {
                total_ms: Date.now() - startTime,
                new_offset: (full_transcript || '').length
              });
            }
            resolve({ ok: true });
          });

          res.on('error', (err) => {
            if (event.sender && !event.sender.isDestroyed()) {
              event.sender.send('answer-stream-error', { error: err.message });
            }
            resolve({ error: err.message });
          });
        });

        req.on('error', (err) => {
          if (event.sender && !event.sender.isDestroyed()) {
            event.sender.send('answer-stream-error', { error: err.message });
          }
          resolve({ error: err.message });
        });

        req.write(bodyStr);
        req.end();
      });
    } catch (e) {
      console.error('[Backend IPC] query-backend-stream failed:', e.message);
      return { error: e.message };
    }
  });

  // Get L4 context — load from local file in app data
  ipcMain.handle('get-l4-context', async (event, token) => {
    try {
      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      if (fs.existsSync(localPath)) {
        const content = fs.readFileSync(localPath, 'utf8');
        return JSON.parse(content);
      }
    } catch (e) {
      console.error('[Stealth] Failed to read local L4 context:', e.message);
    }
    return {};
  });

  // Save L4 context — save to local file in app data
  ipcMain.handle('save-l4-context', async (event, { token, resume, resume_id, job_description, code_context, doc_id, doc_type, company, role, model }) => {
    try {
      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      const data = { resume, resume_id, job_description, code_context, doc_id, doc_type, company, role, model };
      fs.writeFileSync(localPath, JSON.stringify(data, null, 2), 'utf8');
      return { success: true };
    } catch (e) {
      console.error('[Stealth] Failed to save local L4 context:', e.message);
      return { error: e.message };
    }
  });

  // Reset session memory (new meeting) on backend
  ipcMain.handle('reset-session-memory', async (event, token) => {
    return { success: true, message: 'Session memory reset.' };
  });

  // Handle resizing window dynamically based on open/close panels
  ipcMain.on('resize-window', (event, width, height, position, reposition = false) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) {
      win.setResizable(true);
      win.setMinimumSize(1, 1);
      const bounds = win.getBounds();
      let x = bounds.x;
      let y = bounds.y;

      if (height === 580) {
        isToolbarMode = false;
      } else {
        isToolbarMode = true;
      }

      if (reposition) {
        if (height !== 580) {
          const savedBounds = loadSavedBounds();
          if (savedBounds) {
            win.setBounds({
              x: Math.round(savedBounds.x),
              y: Math.round(savedBounds.y),
              width: Math.round(savedBounds.width),
              height: Math.round(savedBounds.height)
            });
            win.setResizable(false);
            return;
          }
        }

        const activeDisplay = screen.getDisplayMatching(bounds);
        const { width: activeScreenWidth, height: activeScreenHeight, y: screenY, x: screenX } = activeDisplay.workArea;

        x = Math.round((activeScreenWidth - width) / 2) + screenX;
        y = screenY;

        if (position === 'bottom') {
          y = screenY + activeScreenHeight - height;
        } else if (position === 'left') {
          x = screenX;
          y = Math.round((activeScreenHeight - height) / 2) + screenY;
        } else if (position === 'right') {
          x = screenX + activeScreenWidth - width;
          y = Math.round((activeScreenHeight - height) / 2) + screenY;
        }
      } else {
        if (position === 'bottom') {
          y = bounds.y + (bounds.height - height);
        } else if (position === 'top') {
          // X and Y stay locked to bounds.x and bounds.y
        } else if (position === 'left') {
          // X and Y stay locked to bounds.x and bounds.y
        } else if (position === 'right') {
          x = bounds.x + (bounds.width - width);
        }
      }

      win.setBounds({ x, y, width, height });
      win.setResizable(false);
    }
  });


  // Handle free-drag window movement (from the drag-handle button)
  // Receives dx, dy deltas and applies them to the current window position
  ipcMain.on('move-window', (event, dx, dy) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) {
      const [curX, curY] = win.getPosition();
      win.setPosition(Math.round(curX + dx), Math.round(curY + dy));
    }
  });

  // Handle window opacity changes (used to hide window while dragging)
  ipcMain.on('set-window-opacity', (event, opacity) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) {
      win.setOpacity(Math.max(0, Math.min(1, opacity)));
    }
  });

  // Handle launch event to morph dashboard into floating toolbar
  ipcMain.on('launch-toolbar', (event, config) => {
    if (config) {
      try {
        const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
        const contextData = {
          resume: config.resume || '',
          job_description: config.jd || '',
          code_context: '',
          company: config.company || 'Stealth Practice',
          role: config.role || 'Software Engineer'
        };
        fs.writeFileSync(localPath, JSON.stringify(contextData, null, 2), 'utf8');
        console.log('[Stealth Config] Successfully initialized active resume and JD context via IPC.');
      } catch (e) {
        console.error('[Stealth Config] Failed to write context data via IPC:', e.message);
      }
    }
    triggerLaunchToolbar();
  });

  // Helper to complete active session on quit/close
  async function autoSaveActiveSession() {
    if (activeSessionId) {
      const elapsed = Math.round((Date.now() - activeSessionStartTime) / 1000);
      const sessId = activeSessionId;
      activeSessionId = null;
      activeSessionStartTime = null;
      console.log(`[Stealth Main] Auto-saving active session ${sessId} with duration ${elapsed}s...`);
      try {
        await backendRequest('PATCH', `/api/sessions/${sessId}`, {
          status: 'completed',
          duration_seconds: elapsed
        });
      } catch (err) {
        console.error('Failed to auto-save session on exit:', err.message);
      }
    }
  }

  // Handle app minimize / close
  ipcMain.on('close-app', async () => {
    try {
      await autoSaveActiveSession();
    } catch (e) { }
    app.exit(0);
  });

  // Kill entire Electron process (used by setup-view close button)
  ipcMain.on('quit-app', async () => {
    try {
      await autoSaveActiveSession();
    } catch (e) { }
    app.exit(0);
  });

  ipcMain.on('minimize-app', () => {
    if (mainWindow) {
      mainWindow.minimize();
    }
  });

  mainWindow.on('close', async (e) => {
    if (activeSessionId) {
      e.preventDefault(); // Stop window from closing instantly
      await autoSaveActiveSession();
      mainWindow.destroy(); // Now close cleanly
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.on('move', () => {
    if (isToolbarMode && mainWindow) {
      const bounds = mainWindow.getBounds();
      saveSavedBounds(bounds);
    }
  });

  mainWindow.on('resize', () => {
    if (isToolbarMode && mainWindow) {
      const bounds = mainWindow.getBounds();
      saveSavedBounds(bounds);
    }
  });
}

function triggerLaunchToolbar() {
  if (mainWindow) {
    // 1. Load the root index.html
    mainWindow.loadFile(path.join(__dirname, 'index.html'));

    isToolbarMode = true;

    // 2. Resize and reposition
    const savedBounds = loadSavedBounds();
    if (savedBounds) {
      mainWindow.setBounds({
        x: Math.round(savedBounds.x),
        y: Math.round(savedBounds.y),
        width: Math.round(savedBounds.width),
        height: Math.round(savedBounds.height)
      });
    } else {
      const primaryDisplay = screen.getPrimaryDisplay();
      const { width: screenWidth, y: screenY, x: screenX } = primaryDisplay.workArea;
      const winWidth = 600;
      const winHeight = 56;
      const x = Math.round((screenWidth - winWidth) / 2) + screenX;
      mainWindow.setBounds({ x, y: screenY, width: winWidth, height: winHeight });
    }


    // 3. Configure skip taskbar and high level always-on-top
    mainWindow.setSkipTaskbar(true);
    mainWindow.setAlwaysOnTop(true, 'screen-saver');

    // 4. Force screen capture protection
    mainWindow.setContentProtection(true);

    mainWindow.show();
    mainWindow.focus();
    mainWindow.restore();
  }
}

// Start a local HTTP server to listen for launch triggers from the web browser
const http = require('http');
const server = http.createServer((req, res) => {
  // Set CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const parsedUrl = require('url').parse(req.url, true);

  if (parsedUrl.pathname === '/auth-callback') {
    const { email, token, user_id } = parsedUrl.query;
    if (email && token) {
      if (mainWindow) {
        mainWindow.webContents.send('sync-credentials', { email, token, user_id });
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ success: true }));
    } else {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Missing email or token' }));
    }
  } else if (parsedUrl.pathname === '/launch') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ success: true, message: 'Launching native stealth toolbar' }));

    // Trigger full launcher morphing
    triggerLaunchToolbar();
  } else {
    res.writeHead(404);
    res.end();
  }
});

server.listen(48999, '127.0.0.1', () => {
  console.log('Stealth controller listening on port 48999');
});

// Ensure single instance
const additionalData = { myKey: 'stealth-toolbar' };
const gotTheLock = app.requestSingleInstanceLock(additionalData);

if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    createWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

