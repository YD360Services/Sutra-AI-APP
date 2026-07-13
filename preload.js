const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // ── Window Controls ──────────────────────────────────────────────
  setIgnoreMouseEvents: (ignore, options) => {
    ipcRenderer.send('set-ignore-mouse-events', ignore, options);
  },
  resizeWindow: (width, height, position, reposition) => {
    ipcRenderer.send('resize-window', width, height, position, reposition);
  },
  closeApp: () => ipcRenderer.send('close-app'),
  minimizeApp: () => ipcRenderer.send('minimize-app'),
  quitApp: () => ipcRenderer.send('quit-app'),
  moveWindow: (x, y) => ipcRenderer.send('move-window', x, y),
  moveWindowDelta: (dx, dy) => ipcRenderer.send('move-window', dx, dy),
  setWindowOpacity: (opacity) => ipcRenderer.send('set-window-opacity', opacity),
  launchToolbar: (config) => ipcRenderer.send('launch-toolbar', config),

  // ── Device / Media ────────────────────────────────────────────────
  getDesktopSources: () => ipcRenderer.invoke('get-desktop-sources'),

  // ── Offline mode (direct API calls — used when no backend URL set) ─
  getDeepgramKey: () => ipcRenderer.invoke('get-deepgram-key'),
  queryGemini: (prompt, base64Image) => ipcRenderer.invoke('query-gemini', prompt, base64Image),
  takeScreenshot: () => ipcRenderer.invoke('take-screenshot'),

  // ── Backend mode (hosted SaaS) ─────────────────────────────────────
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),

  // Create a new session on the backend → returns { session_id, user_id, token }
  createBackendSession: (userId, config) => ipcRenderer.invoke('create-backend-session', userId, config),

  // Send the answer request to the backend (4-layer memory prompt built server-side)
  queryBackend: (payload) => ipcRenderer.invoke('query-backend', payload),
  //  payload: { full_transcript, manual_question?, last_offset, token }

  // Streaming version — chunks arrive via onAnswerChunk / onAnswerStreamEnd / onAnswerStreamError
  queryBackendStream: (payload) => ipcRenderer.invoke('query-backend-stream', payload),
  onAnswerStreamFirst: (cb) => ipcRenderer.on('answer-stream-first', (_, data) => cb(data)),
  onAnswerChunk: (cb) => ipcRenderer.on('answer-stream-chunk', (_, data) => cb(data)),
  onAnswerStreamEnd: (cb) => ipcRenderer.on('answer-stream-end', (_, data) => cb(data)),
  onAnswerStreamError: (cb) => ipcRenderer.on('answer-stream-error', (_, data) => cb(data)),
  removeAnswerStreamListeners: () => {
    ipcRenderer.removeAllListeners('answer-stream-first');
    ipcRenderer.removeAllListeners('answer-stream-chunk');
    ipcRenderer.removeAllListeners('answer-stream-end');
    ipcRenderer.removeAllListeners('answer-stream-error');
  },

  // L4 context management
  getL4Context: () => ipcRenderer.invoke('get-l4-context'),
  saveL4Context: (data) => ipcRenderer.invoke('save-l4-context', data),
  //  data: { resume?, job_description?, code_context? }

  getSavedBounds: () => ipcRenderer.invoke('get-saved-bounds'),
  restoreSavedBounds: () => ipcRenderer.invoke('restore-saved-bounds'),

  // Update a session on the backend (e.g. save duration and complete status)
  updateBackendSession: (sessionId, data) => ipcRenderer.invoke('update-backend-session', sessionId, data),

  // Save a transcript block to the backend database
  saveTranscriptBlock: (sessionId, data) => ipcRenderer.invoke('save-transcript-block', sessionId, data),

  // Upload and solve screenshot on the backend
  solveScreenshotBackend: (payload) => ipcRenderer.invoke('solve-screenshot-backend', payload),

  // Reset session memory (new meeting)
  resetSessionMemory: (token) => ipcRenderer.invoke('reset-session-memory', token),

  // ── Web Authentication Sync Handlers ──────────────────────────────
  openExternalUrl: (url) => ipcRenderer.send('open-external-url', url),
  onSyncCredentials: (cb) => ipcRenderer.on('sync-credentials', (_, data) => cb(data)),
});
