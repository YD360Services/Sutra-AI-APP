const { app, BrowserWindow, ipcMain, screen, desktopCapturer, shell, globalShortcut, clipboard, Tray, Menu, nativeImage } = require('electron');
app.setName('RM');
app.name = 'RM';
const path = require('path');
const fs = require('fs');
const https = require('https');
const { exec } = require('child_process');
const WebSocket = require('ws');

let tray = null;
let isQuitting = false;

// Helper to keep window bounds strictly inside the screen workspace.
// Ensures navbar, transcript layer, and answer panel always remain fully visible on screen.
function clampBoundsToScreen(x, y, width, height) {
  try {
    const activeDisplay = screen.getDisplayMatching({ x, y, width, height });
    const { x: screenX, y: screenY, width: screenWidth, height: screenHeight } = activeDisplay.workArea;

    // Window must stay fully inside the screen workArea on all sides (left, right, top, bottom)
    const clampedX = Math.max(screenX, Math.min(x, screenX + screenWidth - width));
    const clampedY = Math.max(screenY, Math.min(y, screenY + screenHeight - height));
    return { x: clampedX, y: clampedY, width, height };
  } catch (e) {
    return { x, y, width, height };
  }
}

let mainWindow;
let activeSessionId = null;
let activeSessionStartTime = null;
let isToolbarMode = false;

// ── Low-Level Stealth Keyboard Capture Process ───
let stealthKeyHookProcess = null;
let isStealthTypingActive = false;

function ensureKeyHookBinary() {
  const unpackedExe = path.join(process.resourcesPath || '', 'app.asar.unpacked', 'stealth_keyhook.exe');
  const localExe = path.join(__dirname, 'stealth_keyhook.exe');
  const userDataExe = path.join(app.getPath('userData'), 'stealth_keyhook.exe');

  const candidatePaths = [
    app.isPackaged ? unpackedExe : localExe,
    unpackedExe,
    userDataExe,
    localExe
  ];

  for (const p of candidatePaths) {
    if (p && !p.includes('.asar\\') && !p.includes('.asar/') && fs.existsSync(p)) return p;
  }

  // If precompiled exe not found, compile from C# source (built into all Windows machines)
  const candidateCsPaths = [
    app.isPackaged ? path.join(process.resourcesPath || '', 'app.asar.unpacked', 'stealth_keyhook.cs') : path.join(__dirname, 'stealth_keyhook.cs'),
    path.join(process.resourcesPath || '', 'app.asar.unpacked', 'stealth_keyhook.cs'),
    path.join(process.resourcesPath || '', 'stealth_keyhook.cs'),
    path.join(__dirname, 'stealth_keyhook.cs')
  ];

  let csPath = candidateCsPaths.find(p => p && !p.includes('.asar\\') && !p.includes('.asar/') && fs.existsSync(p));
  if (csPath) {
    const cscCandidates = [
      'C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe',
      'C:\\Windows\\Microsoft.NET\\Framework\\v4.0.30319\\csc.exe'
    ];
    const cscPath = cscCandidates.find(p => fs.existsSync(p));
    if (cscPath) {
      try {
        const targetExe = path.join(app.getPath('userData'), 'stealth_keyhook.exe');
        const { execSync } = require('child_process');
        console.log('[Stealth KeyHook] Compiling stealth_keyhook.cs to:', targetExe);
        execSync(`"${cscPath}" /target:exe /out:"${targetExe}" /optimize+ "${csPath}"`);
        if (fs.existsSync(targetExe)) {
          console.log('[Stealth KeyHook] Successfully compiled stealth_keyhook.exe');
          return targetExe;
        }
      } catch (e) {
        console.error('[Stealth KeyHook] Failed to compile keyhook:', e.message);
      }
    }
  }
  return null;
}

function initStealthKeyHook() {
  if (process.platform !== 'win32') return;
  if (stealthKeyHookProcess) return;

  const exePath = ensureKeyHookBinary();
  if (!exePath) {
    console.warn('[Stealth KeyHook] stealth_keyhook.exe binary not found.');
    return;
  }

  try {
    const { spawn } = require('child_process');
    stealthKeyHookProcess = spawn(exePath, [], {
      stdio: ['pipe', 'pipe', 'ignore'],
      windowsHide: true
    });

    let buffer = '';
    stealthKeyHookProcess.stdout.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop(); // keep remainder

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        if (trimmed.startsWith('KEY:')) {
          try {
            const jsonStr = trimmed.substring(4);
            const keyData = JSON.parse(jsonStr);

            if (keyData.action === 'enter' || keyData.action === 'escape') {
              isStealthTypingActive = false;
            } else if (keyData.action === 'toggle') {
              toggleStealthTyping();
              continue;
            }

            if (mainWindow && !mainWindow.isDestroyed()) {
              mainWindow.webContents.send('stealth-key-input', keyData);
            }
          } catch (err) {
            console.error('[Stealth KeyHook] Parse error:', err.message, trimmed);
          }
        }
      }
    });

    stealthKeyHookProcess.on('error', (err) => {
      console.warn('[Stealth KeyHook] Process error:', err.message);
    });

    stealthKeyHookProcess.on('exit', (code) => {
      console.log(`[Stealth KeyHook] Process exited with code ${code}`);
      stealthKeyHookProcess = null;
    });

    console.log('[Stealth KeyHook] Initialized successfully');
  } catch (e) {
    console.error('[Stealth KeyHook] Initialization failed:', e.message);
  }
}

function setStealthTyping(active, bounds = null) {
  isStealthTypingActive = Boolean(active);
  if (stealthKeyHookProcess && stealthKeyHookProcess.stdin && !stealthKeyHookProcess.stdin.destroyed) {
    let cmd = 'STOP_TYPING\n';
    if (isStealthTypingActive) {
      if (bounds && typeof bounds.minX === 'number') {
        cmd = `START_TYPING ${Math.round(bounds.minX)} ${Math.round(bounds.minY)} ${Math.round(bounds.maxX)} ${Math.round(bounds.maxY)}\n`;
      } else if (mainWindow && !mainWindow.isDestroyed()) {
        try {
          const winBounds = mainWindow.getBounds();
          cmd = `START_TYPING ${winBounds.x} ${winBounds.y} ${winBounds.x + winBounds.width} ${winBounds.y + winBounds.height}\n`;
        } catch (e) {
          cmd = 'START_TYPING\n';
        }
      } else {
        cmd = 'START_TYPING\n';
      }
    }
    try {
      stealthKeyHookProcess.stdin.write(cmd);
    } catch (e) { }
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('stealth-typing-state', { active: isStealthTypingActive });
  }
  console.log(`[Stealth KeyHook] Stealth typing state: ${isStealthTypingActive}`);
}

function toggleStealthTyping(bounds = null) {
  setStealthTyping(!isStealthTypingActive, bounds);
}

// Persistent Bounds (Width, Height, X, Y, Panel Size) file path
function getBoundsFilePath() {
  try {
    return path.join(app.getPath('userData'), 'stealth_window_state.json');
  } catch (e) {
    return path.join(__dirname, 'stealth_window_state.json');
  }
}

function loadSavedBounds() {
  try {
    const file = getBoundsFilePath();
    if (fs.existsSync(file)) {
      const data = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (data && typeof data.x === 'number' && typeof data.y === 'number') {
        let width = data.width || 600;
        let height = data.height || 56;

        // Ensure minimum sensible bounds
        if (width < 48) width = 600;
        if (height < 36) height = 56;

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
          const clamped = clampBoundsToScreen(rect.x, rect.y, rect.width, rect.height);
          return { ...data, ...clamped };
        }
      }
    }
  } catch (e) {
    console.error('[Stealth Bounds] Error reading saved bounds:', e.message);
  }
  return null;
}

function saveSavedBounds(bounds) {
  try {
    if (!bounds || typeof bounds !== 'object') return;
    const file = getBoundsFilePath();
    const existing = loadSavedBounds() || {};
    const merged = { ...existing, ...bounds };
    fs.writeFileSync(file, JSON.stringify(merged, null, 2), 'utf8');
  } catch (e) {
    console.error('[Stealth Bounds] Error saving bounds:', e.message);
  }
}


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

const defaultModels = ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-1.5-flash-latest', 'gemini-pro'];

// Native HTTPS request to Gemini API
function makeGeminiRequest(key, prompt, base64Image = null, modelIndex = 0) {
  let model = env.GEMINI_MODEL || defaultModels[modelIndex] || 'gemini-1.5-flash';
  if (model === 'gemini-2.0-flash') model = 'gemini-2.5-flash';
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
              const errLower = errMsg.toLowerCase();
              // If the model was not found/recognized/no longer available, attempt next fallback model index
              if ((!env.GEMINI_MODEL || env.GEMINI_MODEL === 'gemini-2.0-flash') && (errLower.includes('not found') || errLower.includes('not recognized') || errLower.includes('no longer available') || errLower.includes('model')) && modelIndex < defaultModels.length - 1) {
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





function createWindow() {

  // Read and apply configuration if launcher passed one
  const config = loadSessionConfig();
  if (config) {
    try {
      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      const contextData = {
        resume: config.resume_id || config.resume || '',
        resume_id: config.resume_id || '',
        job_description: config.jd || config.job_description || '',
        code_context: '',
        company: config.company || '',
        role: config.role || '',
        model: config.model || '',
        language: config.language || '',
        doc_id: config.doc_id || '',
        auto_start: true,
        is_web_launch: true
      };
      fs.writeFileSync(localPath, JSON.stringify(contextData, null, 2), 'utf8');
      console.log('[Stealth Config] Successfully initialized active resume, JD, company, and role context:', contextData);
    } catch (e) {
      console.error('[Stealth Config] Failed to write context data:', e.message);
    }
  } else {
    // Normal desktop app launch: remove stale context so wizard starts fresh without prefilled data
    try {
      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      if (fs.existsSync(localPath)) {
        fs.unlinkSync(localPath);
      }
    } catch (e) { }
  }

  // Always boot directly in setup wizard configuration dimensions
  const winWidth = 600;
  const winHeight = 580;

  mainWindow = new BrowserWindow({
    title: "RM",
    width: winWidth,
    height: winHeight,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: true,
    skipTaskbar: false,
    focusable: true,
    minWidth: 0,
    minHeight: 0,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
      sandbox: false,
      allowRunningInsecureContent: false,
    },
  });

  // Keep window visible on top of applications
  mainWindow.setAlwaysOnTop(true, 'screen-saver', 1);
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  // Restore last saved window position, or default to centered at top of screen
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth, y: screenY, x: screenX } = primaryDisplay.workArea;
  const savedBounds = loadSavedBounds();
  if (savedBounds) {
    // User had moved the toolbar — restore their preferred position
    mainWindow.setBounds({
      x: savedBounds.x,
      y: savedBounds.y,
      width: winWidth,
      height: winHeight
    });
    console.log('[Stealth] Restored saved window position:', savedBounds.x, savedBounds.y);
  } else {
    // First launch or no saved state — center at top of screen (original default)
    const x = Math.round((screenWidth - winWidth) / 2) + screenX;
    mainWindow.setBounds({ x, y: screenY, width: winWidth, height: winHeight });
    console.log('[Stealth] No saved bounds — using default top-center position');
  }

  // Load the root index.html (stealth toolbar)
  mainWindow.loadFile(path.join(__dirname, 'frontend', 'index.html'));

  // Enable screen capture protection
  mainWindow.setContentProtection(true);

  mainWindow.show();
  mainWindow.setFocusable(true);
  mainWindow.focus();
  mainWindow.setResizable(false);
  initStealthKeyHook();

  // Forward all renderer logs to the terminal
  mainWindow.webContents.on('console-message', (event, level, message, line, sourceId) => {
    console.log(`[Renderer] ${message}`);
  });

  // Prevent accidental reloads (Ctrl+R / F5) in production unless devtools are open
  mainWindow.webContents.on('before-input-event', (event, input) => {
    const isDev = !app.isPackaged;
    const isDevToolsOpen = mainWindow.webContents.isDevToolsOpened();
    if (!isDev && !isDevToolsOpen) {
      if ((input.control && input.key.toLowerCase() === 'r') || input.key === 'F5') {
        event.preventDefault();
      }
    }
  });

  // mainWindow.webContents.openDevTools({ mode: 'detach' });

  // Handle click-through toggle
  ipcMain.on('set-ignore-mouse-events', (event, ignore, options) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win && !win.isDestroyed()) {
      try {
        win.setIgnoreMouseEvents(Boolean(ignore), options || { forward: true });
      } catch (e) {
        console.warn('[IPC] setIgnoreMouseEvents error:', e.message);
      }
    }
  });

  // Handle focusable state — kept here for setup wizard / modal flows only.
  // During live toolbar mode we NEVER call setFocusable(true) to avoid OS window activation.
  ipcMain.on('set-focusable', (event, focusable) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win && !win.isDestroyed() && typeof win.setFocusable === 'function') {
      win.setFocusable(Boolean(focusable));
      // NOTE: Deliberately do NOT call win.focus() here.
    }
  });

  // Route keyboard input to webContents WITHOUT activating the native OS window.
  // This is the key trick: webContents.focus() sends key events to the renderer
  // while the native window keeps WS_EX_NOACTIVATE — so background apps never blur.
  ipcMain.on('focus-webcontents', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win && !win.isDestroyed()) {
      try {
        event.sender.focus(); // focuses the webContents, not the native window
      } catch (e) {
        console.warn('[IPC] focus-webcontents error:', e.message);
      }
    }
  });

  // Stealth typing mode toggle handler
  ipcMain.on('set-stealth-typing', (event, active, bounds) => {
    setStealthTyping(active, bounds);
  });

  // Clipboard read handler
  ipcMain.handle('read-clipboard-text', () => {
    try {
      return clipboard.readText();
    } catch (e) {
      return '';
    }
  });

  // Clipboard write handler
  ipcMain.on('write-clipboard-text', (event, text) => {
    try {
      clipboard.writeText(String(text || ''));
    } catch (e) { }
  });

  // Register dynamic global shortcuts with OS window manager
  ipcMain.on('register-global-shortcuts', (event, shortcuts) => {
    try {
      globalShortcut.unregisterAll();
      if (!shortcuts || typeof shortcuts !== 'object') return;

      for (const [action, config] of Object.entries(shortcuts)) {
        if (!config || !config.key) continue;
        let accelerator = [];
        if (config.ctrl) accelerator.push('CommandOrControl');
        if (config.alt) accelerator.push('Alt');
        if (config.shift) accelerator.push('Shift');

        let rawKey = String(config.key).trim();
        let k = rawKey;

        if (rawKey === ' ' || rawKey.toLowerCase() === 'space') {
          k = 'Space';
        } else if (rawKey === 'ArrowUp' || rawKey.toLowerCase() === 'up') {
          k = 'Up';
        } else if (rawKey === 'ArrowDown' || rawKey.toLowerCase() === 'down') {
          k = 'Down';
        } else if (rawKey === 'ArrowLeft' || rawKey.toLowerCase() === 'left') {
          k = 'Left';
        } else if (rawKey === 'ArrowRight' || rawKey.toLowerCase() === 'right') {
          k = 'Right';
        } else if (rawKey.toLowerCase() === 'enter' || rawKey.toLowerCase() === 'return') {
          k = 'Return';
        } else if (rawKey.toLowerCase() === 'esc' || rawKey.toLowerCase() === 'escape') {
          k = 'Escape';
        } else if (rawKey.length === 1) {
          k = rawKey.toUpperCase();
        }

        accelerator.push(k);
        const shortcutStr = accelerator.join('+');

        try {
          const registered = globalShortcut.register(shortcutStr, () => {
            if (mainWindow && !mainWindow.isDestroyed()) {
              if (action === 'askQuestion') {
                toggleStealthTyping();
              }
              mainWindow.webContents.send('global-shortcut-triggered', action);
            }
          });
          if (!registered) {
            console.warn(`[Shortcuts] Failed to register: ${shortcutStr} for ${action}`);
          } else {
            console.log(`[Shortcuts] Registered: ${shortcutStr} -> ${action}`);
          }
        } catch (err) {
          console.warn(`[Shortcuts] Could not register ${shortcutStr}:`, err.message);
        }
      }
    } catch (e) {
      console.error('[Shortcuts] Error registering shortcuts:', e.message);
    }
  });

  // Handle content protection toggle (Dev Stealth Mode ON/OFF)
  ipcMain.on('set-content-protection', (event, enable) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win && !win.isDestroyed() && typeof win.setContentProtection === 'function') {
      const shouldProtect = Boolean(enable);
      win.setContentProtection(shouldProtect);
      console.log(`[Stealth Mode] Content protection toggled to: ${shouldProtect}`);
    }
  });

  // Handle open external URL — only allow https:// and http:// schemes
  ipcMain.on('open-external-url', (event, url) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === 'https:' || parsed.protocol === 'http:') {
        shell.openExternal(url);
      } else {
        console.warn('[Security] Blocked external URL with disallowed protocol:', parsed.protocol);
      }
    } catch (e) {
      console.warn('[Security] Invalid URL passed to open-external-url:', url);
    }
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
    return env.DEEPGRAM_API_KEY || '3cad982200a3b1b6be970a367c19c5032092d982';
  });

  // ── Native Deepgram nova-3 Live Transcription Bridge ───
  let liveSTTSocket = null;
  let audioChunkCount = 0;

  ipcMain.on('start-transcription', (event, config = {}) => {
    const language = config.language || 'en';
    const dgKey = env.DEEPGRAM_API_KEY || '3cad982200a3b1b6be970a367c19c5032092d982';

    // If socket is already open and ready, inform client immediately
    if (liveSTTSocket && liveSTTSocket.readyState === WebSocket.OPEN) {
      event.sender.send('transcription-status', { status: 'listening', provider: 'deepgram' });
      return;
    }

    if (liveSTTSocket) {
      try { liveSTTSocket.close(); } catch (e) { }
      liveSTTSocket = null;
    }

    audioChunkCount = 0;
    const langQuery = (language && language !== 'multi') ? `&language=${encodeURIComponent(language)}` : '';
    // Use raw 16kHz linear PCM for zero-header stream reliability
    const dgUrl = `wss://api.deepgram.com/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&channels=1&smart_format=true&interim_results=true&endpointing=100${langQuery}`;

    console.log(`[Deepgram STT] Connecting to live WebSocket: ${dgUrl}`);
    try {
      liveSTTSocket = new WebSocket(dgUrl, {
        headers: { Authorization: `Token ${dgKey}` }
      });

      liveSTTSocket.on('open', () => {
        console.log('[Deepgram STT] Connected successfully to Deepgram WebSocket (Linear16 PCM)!');
        event.sender.send('transcription-status', { status: 'listening', provider: 'deepgram' });
      });

      liveSTTSocket.on('message', (msgBuffer) => {
        try {
          const data = JSON.parse(msgBuffer.toString());
          const alt = (data.channel && data.channel.alternatives && data.channel.alternatives[0])
            || (data.results && data.results.channels && data.results.channels[0] && data.results.channels[0].alternatives && data.results.channels[0].alternatives[0]);
          const transcript = alt ? alt.transcript : '';
          const isFinal = (data.is_final !== undefined) ? data.is_final : (data.speech_final || false);

          if (transcript && transcript.trim()) {
            console.log(`[Deepgram STT] Live Transcript: "${transcript.trim()}" (final: ${isFinal})`);
            event.sender.send('transcription-chunk', { text: transcript.trim(), is_final: isFinal });
          }
        } catch (e) {
          console.error('[Deepgram STT] Error parsing Deepgram message:', e.message);
        }
      });

      liveSTTSocket.on('error', (err) => {
        console.error('[Deepgram STT] WebSocket Error:', err.message || err);
        event.sender.send('transcription-status', { status: 'error', provider: 'deepgram', error: err.message || '401 Unauthorized' });
      });

      liveSTTSocket.on('close', (code, reason) => {
        const reasonStr = reason ? reason.toString() : '';
        console.log(`[Deepgram STT] WebSocket Closed (code: ${code}, reason: ${reasonStr})`);
        event.sender.send('transcription-status', { status: 'closed', provider: 'deepgram', code, reason: reasonStr });
      });
    } catch (err) {
      console.error('[Deepgram STT] Failed to initialize connection:', err.message);
      event.sender.send('transcription-status', { status: 'error', provider: 'deepgram', error: err.message });
    }
  });

  ipcMain.on('send-audio-chunk', (event, chunkBuffer) => {
    if (liveSTTSocket && liveSTTSocket.readyState === WebSocket.OPEN) {
      try {
        const buf = Buffer.from(chunkBuffer);
        liveSTTSocket.send(buf);
        audioChunkCount++;
        if (audioChunkCount === 1 || audioChunkCount % 40 === 0) {
          console.log(`[Deepgram STT] Actively streaming audio... (${audioChunkCount} chunks sent, ${buf.length} bytes each)`);
        }
      } catch (e) {
        console.error('[Deepgram STT] Error sending audio buffer:', e.message);
      }
    }
  });

  ipcMain.on('stop-transcription', () => {
    if (liveSTTSocket) {
      console.log('[Deepgram STT] Stopping transcription session...');
      try { liveSTTSocket.close(); } catch (e) { }
      liveSTTSocket = null;
    }
  });

  ipcMain.handle('get-saved-bounds', () => {
    return loadSavedBounds();
  });

  ipcMain.handle('save-window-bounds', (event, bounds) => {
    saveSavedBounds(bounds);
    return true;
  });

  ipcMain.handle('restore-saved-bounds', () => {
    if (mainWindow) {
      isToolbarMode = true;
      const bounds = loadSavedBounds();
      if (bounds) {
        mainWindow.setBounds(clampBoundsToScreen(
          Math.round(bounds.x),
          Math.round(bounds.y),
          Math.round(bounds.width),
          Math.round(bounds.height)
        ));
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
    // Input validation
    if (typeof prompt !== 'string' || prompt.length > 50000) {
      return 'Error: Invalid prompt input';
    }
    try {
      // Log offline prompts to userData/logs for debugging
      try {
        const logDir = path.join(app.getPath('logs'), 'prompt_debug');
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

      const normalizeModelIdentifier = (modelStr) => {
        if (!modelStr) return 'gemini-3.6-flash';
        const m = modelStr.toLowerCase().trim();
        if (m.includes('3.7') || m.includes('lite') || m.includes('flash-lite')) {
          return 'gemini-3.5-flash-lite';
        }
        if (m.includes('gemini') || m.includes('flash') || m.includes('3.6') || m.includes('3.1') || m.includes('pro') || m.includes('2')) {
          return 'gemini-3.6-flash';
        }
        if (m.includes('sonnet')) {
          return 'claude-sonnet-4-5-20250929';
        }
        if (m.includes('haiku') || m.includes('claude')) {
          return 'claude-haiku-4-5-20251001';
        }
        if (m.includes('llama') || m.includes('groq') || m.includes('scout') || m.includes('20b') || m.includes('oss')) {
          return 'openai/gpt-oss-120b';
        }
        if (m.includes('o3') || m.includes('gptoss')) {
          return 'o3-mini';
        }
        if (m.includes('5.4') || m.includes('5.5') || m.includes('5.6') || m.includes('5-mini') || m.includes('5.4-mini') || m.includes('5.5-mini')) {
          return 'gpt-5.4-mini';
        }
        if (m.includes('4o-mini')) {
          return 'gpt-4o-mini';
        }
        if (m.includes('4o')) {
          return 'gpt-4o';
        }
        if (m.includes('gpt') || m.includes('mini')) {
          return 'gpt-5.4-mini';
        }
        return modelStr;
      };

      const newTranscript = (full_transcript && typeof last_offset === 'number') ? full_transcript.slice(last_offset).trim() : (full_transcript || '');
      const payload = {
        session_id: token || null,
        question: manual_question || null,
        transcript: newTranscript || null,
        source_type: manual_question ? 'manual' : 'transcript',
        resume_content: resume || null,
        knowledge_content: jd || null,
        model: normalizeModelIdentifier(model)
      };

      const { data, status } = await backendRequest(
        'POST', '/api/answer',
        payload,
        token
      );
      if (status >= 400 || !data) return { error: data?.detail || 'Backend error' };

      return {
        answer: data.answer || '',
        question: data.question || manual_question || '',
        new_offset: (full_transcript || '').length
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

      const normalizeModelIdentifier = (modelStr) => {
        if (!modelStr) return 'gemini-3.6-flash';
        const m = modelStr.toLowerCase().trim();
        if (m.includes('gemini') || m.includes('flash') || m.includes('3.6') || m.includes('3.7') || m.includes('3.1') || m.includes('2')) {
          return 'gemini-3.6-flash';
        }
        if (m.includes('sonnet')) {
          return 'claude-sonnet-4-5-20250929';
        }
        if (m.includes('haiku') || m.includes('claude')) {
          return 'claude-haiku-4-5-20251001';
        }
        if (m.includes('llama') || m.includes('groq') || m.includes('scout') || m.includes('20b') || m.includes('oss')) {
          return 'openai/gpt-oss-120b';
        }
        if (m.includes('o3') || m.includes('gptoss')) {
          return 'o3-mini';
        }
        if (m.includes('5.6') || m.includes('4o')) {
          return 'gpt-4o';
        }
        if (m.includes('gpt') || m.includes('5.5') || m.includes('mini')) {
          return 'gpt-4o-mini';
        }
        return modelStr;
      };

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
        model: normalizeModelIdentifier(model)
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

  // Save L4 context — save to local file in app data (with input validation)
  ipcMain.handle('save-l4-context', async (event, { token, resume, resume_id, job_description, code_context, doc_id, doc_type, company, role, model }) => {
    try {
      // Validate string fields — reject if any field is oversized
      const MAX_STR = 500000;
      const MAX_SHORT = 500;
      if (resume && typeof resume === 'string' && resume.length > MAX_STR) return { error: 'resume too large' };
      if (job_description && typeof job_description === 'string' && job_description.length > MAX_STR) return { error: 'job_description too large' };
      if (company && typeof company === 'string' && company.length > MAX_SHORT) return { error: 'company too long' };
      if (role && typeof role === 'string' && role.length > MAX_SHORT) return { error: 'role too long' };

      const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
      const data = {
        resume: typeof resume === 'string' ? resume : '',
        resume_id: typeof resume_id === 'string' ? resume_id.substring(0, 200) : '',
        job_description: typeof job_description === 'string' ? job_description : '',
        code_context: typeof code_context === 'string' ? code_context.substring(0, MAX_STR) : '',
        doc_id: typeof doc_id === 'string' ? doc_id.substring(0, 200) : '',
        doc_type: typeof doc_type === 'string' ? doc_type.substring(0, 50) : '',
        company: typeof company === 'string' ? company.substring(0, MAX_SHORT) : '',
        role: typeof role === 'string' ? role.substring(0, MAX_SHORT) : '',
        model: typeof model === 'string' ? model.substring(0, 100) : '',
        auto_start: false,
        is_web_launch: false
      };
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
  ipcMain.on('resize-window', (event, width, height, position, reposition = false, targetX = null, targetY = null) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) {
      // NOTE: do NOT call setResizable(true/false) or setMinimumSize here —
      // toggling DWM window attributes on every frame causes visible jank/lag.
      // setBounds() works fine regardless of the resizable state.
      const bounds = win.getBounds();
      let x = bounds.x;
      let y = bounds.y;

      if (height === 580) {
        isToolbarMode = false;
      } else {
        isToolbarMode = true;
      }

      if (reposition) {
        const savedBounds = loadSavedBounds();
        const activeDisplay = screen.getDisplayMatching(bounds);
        const { width: activeScreenWidth, height: activeScreenHeight, y: screenY, x: screenX } = activeDisplay.workArea;

        if (height !== 580) {
          // Toolbar collapse mode: restore exact saved size + position if available
          if (savedBounds) {
            win.setBounds(clampBoundsToScreen(
              Math.round(savedBounds.x),
              Math.round(savedBounds.y),
              Math.round(width || savedBounds.width),
              Math.round(height || savedBounds.height)
            ));
            return;
          }
        }

        // Setup wizard (height=580) or no saved bounds:
        // Respect explicit left/right/bottom snap positions exactly,
        // but for 'top' restore saved X and Y (not hardcoded to roof).
        if (position === 'bottom') {
          x = savedBounds ? Math.round(savedBounds.x) : Math.round((activeScreenWidth - width) / 2) + screenX;
          y = screenY + activeScreenHeight - height;
        } else if (position === 'left') {
          x = screenX; // snap to left edge exactly
          y = savedBounds ? Math.round(savedBounds.y) : Math.round((activeScreenHeight - height) / 2) + screenY;
        } else if (position === 'right') {
          x = screenX + activeScreenWidth - width; // snap to right edge exactly
          y = savedBounds ? Math.round(savedBounds.y) : Math.round((activeScreenHeight - height) / 2) + screenY;
        } else {
          // 'top' (default) — restore saved position or center horizontally
          x = savedBounds ? Math.round(savedBounds.x) : Math.round((activeScreenWidth - width) / 2) + screenX;
          y = savedBounds ? Math.round(savedBounds.y) : screenY;
        }
      } else {
        if (targetX !== null) {
          x = targetX;
        } else if (position === 'top' || position === 'bottom') {
          // Keep toolbar horizontally stable — only adjust x when width actually changes
          if (width !== bounds.width) {
            x = bounds.x - Math.round((width - bounds.width) / 2);
          }
        }

        if (targetY !== null) {
          y = targetY;
        } else if (position === 'bottom') {
          // When bottom-docked, anchor the bottom edge so it expands upwards only when height changes
          if (height !== bounds.height) {
            y = bounds.y + (bounds.height - height);
          }
        } else if (position === 'left') {
          // X and Y stay locked to bounds.x and bounds.y
        } else if (position === 'right') {
          if (width !== bounds.width) {
            x = bounds.x + (bounds.width - width);
          }
        }
      }

      const clamped = clampBoundsToScreen(x, y, width, height);
      if (clamped.x !== bounds.x || clamped.y !== bounds.y || clamped.width !== bounds.width || clamped.height !== bounds.height) {
        win.setBounds(clamped);
      }
    }
  });


  // Handle free-drag window movement (from the drag-handle button)
  // Receives dx, dy deltas and applies them to the current window position
  ipcMain.on('move-window', (event, dx, dy) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) {
      const bounds = win.getBounds();
      const cursorPoint = screen.getCursorScreenPoint();
      const activeDisplay = screen.getDisplayNearestPoint(cursorPoint);
      const { x: screenX, y: screenY, width: screenWidth, height: screenHeight } = activeDisplay.workArea;

      let newX = Math.round(bounds.x + dx);
      let newY = Math.round(bounds.y + dy);

      // Clamp window bounds to ensure it stays fully inside the screen workArea
      newX = Math.max(screenX, Math.min(newX, screenX + screenWidth - bounds.width));
      newY = Math.max(screenY, Math.min(newY, screenY + screenHeight - bounds.height));

      win.setPosition(newX, newY);
    }
  });

  // Handle absolute window positioning dragging (to prevent cumulative sync lag)
  ipcMain.on('move-window-absolute', (event, x, y) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (win) {
      const bounds = win.getBounds();
      const cursorPoint = screen.getCursorScreenPoint();
      const activeDisplay = screen.getDisplayNearestPoint(cursorPoint);
      const { x: screenX, y: screenY, width: screenWidth, height: screenHeight } = activeDisplay.workArea;

      let newX = Math.round(x);
      let newY = Math.round(y);

      // Clamp coordinates to screen work area
      newX = Math.max(screenX, Math.min(newX, screenX + screenWidth - bounds.width));
      newY = Math.max(screenY, Math.min(newY, screenY + screenHeight - bounds.height));

      win.setPosition(newX, newY);
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

  // User Account & Tokens status handler for Desktop App
  ipcMain.handle('get-user-account', async () => {
    try {
      const accountPath = path.join(app.getPath('userData'), 'stealth_account.json');
      if (fs.existsSync(accountPath)) {
        return JSON.parse(fs.readFileSync(accountPath, 'utf8'));
      }
      const contextPath = path.join(app.getPath('userData'), 'stealth_context.json');
      if (fs.existsSync(contextPath)) {
        const ctx = JSON.parse(fs.readFileSync(contextPath, 'utf8'));
        if (ctx.tokens_balance !== undefined || ctx.is_pro !== undefined) {
          return {
            isPro: ctx.is_pro === true || ctx.is_pro === 'true',
            tokens: { balance: Number(ctx.tokens_balance) || 0 },
            subscription: {
              isActive: ctx.is_pro === true || ctx.is_pro === 'true',
              planTitle: ctx.plan_title || 'Pro Pass',
              expiresAt: ctx.expires_at || null
            }
          };
        }
      }
    } catch (e) {
      console.error('[Account] Error reading local account in IPC:', e.message);
    }
    return null;
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

  // Handle app close (hides to tray to keep loopback port 48999 active)
  ipcMain.on('close-app', async () => {
    if (mainWindow) {
      mainWindow.hide();
    }
  });

  // Explicit quit of entire process
  ipcMain.on('quit-app', async () => {
    isQuitting = true;
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
    if (!isQuitting) {
      e.preventDefault();
      if (activeSessionId) {
        await autoSaveActiveSession();
      }
      mainWindow.hide();
    } else {
      if (activeSessionId) {
        e.preventDefault();
        await autoSaveActiveSession();
        mainWindow.destroy();
      }
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.on('move', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      const bounds = mainWindow.getBounds();
      try {
        mainWindow.webContents.send('window-moved', bounds);
      } catch (e) { }
    }
  });

  mainWindow.on('moved', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      const bounds = mainWindow.getBounds();
      const clamped = clampBoundsToScreen(bounds.x, bounds.y, bounds.width, bounds.height);
      if (clamped.x !== bounds.x || clamped.y !== bounds.y) {
        mainWindow.setPosition(clamped.x, clamped.y);
      }
      const finalBounds = mainWindow.getBounds();
      saveSavedBounds(finalBounds);
      try {
        mainWindow.webContents.send('window-moved', finalBounds);
      } catch (e) { }
    }
  });

  mainWindow.on('resize', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      const bounds = mainWindow.getBounds();
      if (isToolbarMode || bounds.height < 200) {
        saveSavedBounds(bounds);
      }
    }
  });
}

function triggerLaunchToolbar() {
  if (mainWindow) {
    // 1. Load the root index.html
    mainWindow.loadFile(path.join(__dirname, 'frontend', 'index.html'));

    isToolbarMode = true;

    // 2. Resize and reposition
    const savedBounds = loadSavedBounds();
    if (savedBounds) {
      mainWindow.setBounds(clampBoundsToScreen(
        Math.round(savedBounds.x),
        Math.round(savedBounds.y),
        Math.round(savedBounds.width),
        Math.round(savedBounds.height)
      ));
    } else {
      const primaryDisplay = screen.getPrimaryDisplay();
      const { width: screenWidth, y: screenY, x: screenX } = primaryDisplay.workArea;
      const winWidth = 600;
      const winHeight = 56;
      const x = Math.round((screenWidth - winWidth) / 2) + screenX;
      mainWindow.setBounds(clampBoundsToScreen(x, screenY, winWidth, winHeight));
    }


    // 3. Configure skip taskbar and high level always-on-top
    mainWindow.setSkipTaskbar(true);
    mainWindow.setAlwaysOnTop(true, 'screen-saver', 1);
    mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    mainWindow.setFocusable(false);

    // 4. Force screen capture protection
    mainWindow.setContentProtection(true);

    mainWindow.showInactive();
    mainWindow.restore();
  }
}

// Start a local HTTP server to listen for launch triggers from the web browser
const http = require('http');

// Allowed origins for the internal controller (localhost, Render backend, and production web app)
const ALLOWED_ORIGINS = new Set([
  'http://localhost:5173', 'http://127.0.0.1:5173',
  'http://localhost:3000', 'http://127.0.0.1:3000',
  'http://localhost:4173', 'http://127.0.0.1:4173',
  'http://localhost:2999', 'http://127.0.0.1:2999',
  'http://localhost:8080', 'http://127.0.0.1:8080',
  'https://roundmateai.com', 'https://www.roundmateai.com',
  'http://roundmateai.com', 'http://www.roundmateai.com',
  'https://round-mate-ai.onrender.com', 'http://round-mate-ai.onrender.com'
]);

const server = http.createServer((req, res) => {
  const origin = req.headers['origin'] || '';
  // Check if origin matches allowed localhost, web domains, or onrender backend
  const isAllowedOrigin = !origin ||
    origin.startsWith('http://localhost') ||
    origin.startsWith('http://127.0.0.1') ||
    origin.endsWith('.onrender.com') ||
    ALLOWED_ORIGINS.has(origin);

  if (!isAllowedOrigin) {
    res.writeHead(403, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Forbidden: cross-origin request blocked' }));
    return;
  }

  // Set CORS and Chrome Private Network Access (PNA) headers
  if (origin) res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With');
  res.setHeader('Access-Control-Allow-Private-Network', 'true');
  res.setHeader('Vary', 'Origin');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const parsedUrl = require('url').parse(req.url, true);

  if (parsedUrl.pathname === '/auth-callback') {
    // Accept credentials via POST body or query params
    let body = '';
    req.on('data', chunk => { body += chunk.toString(); });
    req.on('end', () => {
      try {
        const payload = JSON.parse(body || '{}');
        const { email, token, user_id, subscription, tokens, isPro } = payload;
        // Fallback to query params for backward compat
        const qEmail = parsedUrl.query?.email;
        const qToken = parsedUrl.query?.token;
        const qUserId = parsedUrl.query?.user_id;
        const finalEmail = email || qEmail;
        const finalToken = token || qToken;
        const finalUserId = user_id || qUserId;

        if (finalEmail && finalToken) {
          if (mainWindow) {
            mainWindow.webContents.send('sync-credentials', {
              ...payload,
              email: finalEmail,
              token: finalToken,
              user_id: finalUserId,
              subscription: subscription || payload.subscription,
              tokens: tokens || payload.tokens,
              isPro: isPro ?? payload.isPro
            });
          }
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: true }));
        } else {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Missing email or token' }));
        }
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid JSON body' }));
      }
    });
  } else if (parsedUrl.pathname === '/sync-account') {
    if (req.method === 'POST') {
      let body = '';
      req.on('data', chunk => { body += chunk.toString(); });
      req.on('end', () => {
        try {
          const accountData = JSON.parse(body || '{}');
          const accountPath = path.join(app.getPath('userData'), 'stealth_account.json');
          fs.writeFileSync(accountPath, JSON.stringify(accountData, null, 2), 'utf8');
          console.log('[Stealth Server] Synchronized user account tokens on port 48999:', accountData);

          if (mainWindow) {
            mainWindow.webContents.send('account-synced', accountData);
          }
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: true, message: 'Account synced' }));
        } catch (e) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Failed to sync account: ' + e.message }));
        }
      });
    } else {
      res.writeHead(405);
      res.end();
    }
  } else if (parsedUrl.pathname === '/launch') {
    if (req.method === 'POST') {
      let body = '';
      req.on('data', chunk => { body += chunk.toString(); });
      req.on('end', () => {
        try {
          const config = JSON.parse(body || '{}');
          // Write to local context
          const localPath = path.join(app.getPath('userData'), 'stealth_context.json');
          const contextData = {
            resume: config.resume_id || config.resume || '',
            resume_id: config.resume_id || '',
            job_description: config.jd || config.job_description || '',
            code_context: '',
            company: config.company || '',
            role: config.role || '',
            model: config.model || '',
            language: config.language || '',
            doc_id: config.doc_id || '',
            auto_start: true,
            is_web_launch: true,
            tokens_balance: config.tokens_balance,
            is_pro: config.is_pro,
            plan_title: config.plan_title,
            expires_at: config.expires_at
          };
          fs.writeFileSync(localPath, JSON.stringify(contextData, null, 2), 'utf8');

          if (config.tokens_balance !== undefined || config.is_pro !== undefined) {
            const accountPath = path.join(app.getPath('userData'), 'stealth_account.json');
            const accountData = {
              isPro: config.is_pro === true || config.is_pro === 'true',
              tokens: { balance: Number(config.tokens_balance) || 0 },
              subscription: {
                isActive: config.is_pro === true || config.is_pro === 'true',
                planTitle: config.plan_title || 'Pro Pass',
                expiresAt: config.expires_at || null
              }
            };
            fs.writeFileSync(accountPath, JSON.stringify(accountData, null, 2), 'utf8');
            if (mainWindow) {
              mainWindow.webContents.send('account-synced', accountData);
            }
          }

          console.log('[Stealth Server] Updated session context on port 48999:', contextData);

          if (mainWindow) {
            // Forward the updated session parameters to the renderer UI
            const mappedConfig = {
              session_name: config.session_name,
              company: config.company,
              role: config.role,
              jd: config.jd || config.job_description,
              type: config.type,
              model: config.model,
              language: config.language,
              resume_id: config.resume_id,
              doc_id: config.doc_id,
              prompt_id: config.prompt_id,
              auto_answer: config.auto_answer,
              save_transcript: config.save_transcript,
              auto_start: true,
              is_web_launch: true
            };
            mainWindow.webContents.send('deep-link-session', mappedConfig);
          }
        } catch (e) {
          console.error('[Stealth Server] Failed to update context:', e.message);
        }
      });
    }

    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ success: true, message: 'Launching native stealth toolbar' }));

    // Trigger full launcher morphing
    triggerLaunchToolbar();
  } else {
    res.writeHead(404);
    res.end();
  }
});

// ─── Deep Link Protocol Handler (sutra:// & roundmate://) ─────────────────────
// Parses a roundmate://start-session?company=...&role=... URL into a session config
function parseDeepLinkUrl(urlStr) {
  try {
    const url = new URL(urlStr);
    const params = url.searchParams;
    const config = {};
    if (params.get('session_name')) config.session_name = params.get('session_name');
    if (params.get('company')) config.company = params.get('company');
    if (params.get('role')) config.role = params.get('role');
    if (params.get('jd')) config.jd = params.get('jd');
    if (params.get('job_description')) config.jd = params.get('job_description');
    if (params.get('type')) config.type = params.get('type');
    if (params.get('model')) config.model = params.get('model');
    if (params.get('language')) config.language = params.get('language');
    if (params.get('resume_id')) config.resume_id = params.get('resume_id');
    if (params.get('doc_id')) config.doc_id = params.get('doc_id');
    if (params.get('prompt_id')) config.prompt_id = params.get('prompt_id');
    if (params.get('auto_answer')) config.auto_answer = params.get('auto_answer') === 'true';
    if (params.get('save_transcript')) config.save_transcript = params.get('save_transcript') === 'true';
    if (params.get('tokens_balance') !== null) config.tokens_balance = params.get('tokens_balance');
    if (params.get('is_pro') !== null) config.is_pro = params.get('is_pro');
    if (params.get('plan_title') !== null) config.plan_title = params.get('plan_title');
    if (params.get('expires_at') !== null) config.expires_at = params.get('expires_at');

    if (config.tokens_balance !== undefined || config.is_pro !== undefined) {
      try {
        const accountPath = path.join(app.getPath('userData'), 'stealth_account.json');
        const accountData = {
          isPro: config.is_pro === true || config.is_pro === 'true',
          tokens: { balance: Number(config.tokens_balance) || 0 },
          subscription: {
            isActive: config.is_pro === true || config.is_pro === 'true',
            planTitle: config.plan_title || 'Pro Pass',
            expiresAt: config.expires_at || null
          }
        };
        fs.writeFileSync(accountPath, JSON.stringify(accountData, null, 2), 'utf8');
      } catch (_) {}
    }

    if (Object.keys(config).length > 0) {
      config.auto_start = true;
      config.is_web_launch = true;
      return config;
    }
    return null;
  } catch (e) {
    console.error('[DeepLink] Failed to parse URL:', urlStr, e.message);
    return null;
  }
}

// Write deep link config so createWindow() can read it via loadSessionConfig()
function applyDeepLinkConfig(deepLinkUrl) {
  const config = parseDeepLinkUrl(deepLinkUrl);
  if (!config) return;
  try {
    const configPath = path.join(__dirname, 'stealth_session_config.json');
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2), 'utf8');
    console.log('[DeepLink] Wrote session config from deep link:', config);
  } catch (e) {
    console.error('[DeepLink] Failed to write session config:', e.message);
  }
}

// Register roundmate:// (and legacy sutra://) protocol — handles development vs packaged environment parameter matching
if (process.defaultApp) {
  if (process.argv.length >= 2) {
    app.setAsDefaultProtocolClient('roundmate', process.execPath, [path.resolve(process.argv[1])]);
    app.setAsDefaultProtocolClient('sutra', process.execPath, [path.resolve(process.argv[1])]);
  }
} else {
  app.setAsDefaultProtocolClient('roundmate');
  app.setAsDefaultProtocolClient('sutra');
}

// ── Dev/Production userData Parity Fix ───────────────────────────────────────
// In dev mode (npm start), Electron uses "Roaming\Electron" as userData, but
// the installed app uses "Roaming\RM". This means stealth_context.json,
// stealth_window_state.json, and all saved session state are MISSING in dev.
// Fix: force dev mode to use the same folder as production so both environments
// behave identically. This must be called before app.requestSingleInstanceLock().
if (!app.isPackaged) {
  app.setPath('userData', path.join(app.getPath('appData'), 'RM'));
  console.log('[Dev Mode] userData forced to:', app.getPath('userData'));
}
// ─────────────────────────────────────────────────────────────────────────────

// Ensure single instance
const additionalData = { myKey: 'stealth-toolbar' };
const gotTheLock = app.requestSingleInstanceLock(additionalData);

if (!gotTheLock) {
  app.quit();
} else {
  // Windows / Linux: second-instance fires when a roundmate:// or sutra:// URL is clicked while app is already running
  app.on('second-instance', (_event, argv) => {
    // argv includes the deep link URL on Windows
    const deepLinkArg = argv.find(arg => arg.startsWith('roundmate://') || arg.startsWith('sutra://'));
    if (deepLinkArg) {
      applyDeepLinkConfig(deepLinkArg);
      if (mainWindow) {
        // Send config directly to renderer
        const config = parseDeepLinkUrl(deepLinkArg);
        if (config) {
          mainWindow.webContents.send('deep-link-session', config);
        }
      }
    }
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
    }
  });

  function setupSystemTray() {
    if (tray) return;
    try {
      const iconPath = path.join(__dirname, 'icon.ico');
      if (fs.existsSync(iconPath)) {
        let trayIcon = nativeImage.createFromPath(iconPath);
        if (process.platform === 'darwin') {
          trayIcon = trayIcon.resize({ width: 18, height: 18 });
          trayIcon.setTemplateImage(true);
        }
        tray = new Tray(trayIcon);
        const contextMenu = Menu.buildFromTemplate([
          {
            label: 'Show RoundMate AI',
            click: () => {
              if (mainWindow) {
                mainWindow.show();
                mainWindow.focus();
              }
            }
          },
          {
            label: 'Sync Status (Port 48999 Active)',
            enabled: false
          },
          { type: 'separator' },
          {
            label: 'Quit RoundMate AI',
            click: async () => {
              isQuitting = true;
              try {
                await autoSaveActiveSession();
              } catch (e) { }
              app.quit();
            }
          }
        ]);

        tray.setToolTip('RoundMate AI (Stealth Assistant Active)');
        tray.setContextMenu(contextMenu);
        tray.on('double-click', () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
        });
        tray.on('click', () => {
          if (process.platform === 'darwin' || process.platform === 'linux') {
            if (mainWindow) {
              mainWindow.isVisible() ? mainWindow.hide() : (mainWindow.show(), mainWindow.focus());
            }
          }
        });
      }
    } catch (err) {
      console.warn('[System Tray] Could not initialize tray:', err.message);
    }
  }

  app.whenReady().then(() => {
    // Check if launched via roundmate:// or sutra:// deep link on Windows (URL will be in argv)
    const deepLinkArg = process.argv.find(arg => arg.startsWith('roundmate://') || arg.startsWith('sutra://'));
    if (deepLinkArg) {
      applyDeepLinkConfig(deepLinkArg);
    }

    createWindow();
    setupSystemTray();

    // Start local server only on the single main instance
    server.listen(48999, '127.0.0.1', () => {
      console.log('Stealth controller listening on port 48999');
    });

    // macOS: handle open-url event for roundmate:// or sutra:// links
    app.on('open-url', (event, url) => {
      event.preventDefault();
      applyDeepLinkConfig(url);
      if (mainWindow) {
        const config = parseDeepLinkUrl(url);
        if (config) mainWindow.webContents.send('deep-link-session', config);
        if (mainWindow.isMinimized()) mainWindow.restore();
      }
    });

    app.on('activate', () => {
      if (mainWindow) {
        mainWindow.show();
        mainWindow.focus();
      } else if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });
}

app.on('before-quit', () => {
  isQuitting = true;
  if (stealthKeyHookProcess) {
    try {
      stealthKeyHookProcess.stdin.write('EXIT\n');
      stealthKeyHookProcess.kill();
    } catch (e) { }
  }
});

app.on('window-all-closed', () => {
  if (isQuitting) {
    app.quit();
  }
  // Keep background daemon running in tray across Windows, macOS, and Linux so loopback port 48999 catches web logins
});
