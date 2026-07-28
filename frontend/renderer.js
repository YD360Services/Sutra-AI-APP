// Setup form elements
const setupView = document.getElementById('setup-view');
const toolbarView = document.getElementById('toolbar-view');
const startSessionBtn = document.getElementById('start-session-btn');
const stopSessionBtn = document.getElementById('stop-session-btn');
const setupCompany = document.getElementById('setup-company');
const setupRole = document.getElementById('setup-role');
const setupJd = document.getElementById('setup-jd');

const setupResumeSelect = document.getElementById('setup-resume-select');
const setupResumeFile = document.getElementById('setup-resume-file');
const setupDocSelect = document.getElementById('setup-doc-select');
const setupDocFile = document.getElementById('setup-doc-file');

// UI Buttons & Panels
const aiBtn = document.getElementById('ai-btn');
const captureBtn = document.getElementById('capture-btn');
const panelsContainer = document.getElementById('panels');
const aiPanel = document.getElementById('ai-panel');

// Window Controls
const positionBtn = document.getElementById('position-btn');
const closeBtn = document.getElementById('close-btn'); // may be absent in toolbar view
const settingsPopup = document.getElementById('settings-popup');
const copyAllAnswerBtn = document.getElementById('copy-all-answer-btn');
const copyCodeBtn = document.getElementById('copy-code-btn');
const codeScreenshotBtn = document.getElementById('code-screenshot-btn');

// Size Definitions
const WIDTH = 640;
const COLLAPSED_HEIGHT = 56;
const EXPANDED_HEIGHT = 664; // Toolbar 48px + margin 8px + panels 600px + padding/buffer = 664px
const MAX_HEIGHT = 1500; // Hard cap — never exceeded


let currentWidth = WIDTH;
let currentHeight = EXPANDED_HEIGHT;
// Use a counter instead of a boolean so nested/overlapping programmatic
// resizes don't accidentally clear each other's guards.
let pendingProgrammaticResizes = 0;
let isDraggingWindow = false;

// Active state tracking
let activeTab = null; // 'ai', 'code', or null
let toolbarPosition = 'top'; // 'top' or 'bottom'

function updateDynamicToolbarPosition() {
  const screenHeight = window.screen.availHeight || 1080;
  const windowY = window.screenY;

  if (windowY > screenHeight / 2) {
    toolbarPosition = 'bottom';
  } else {
    toolbarPosition = 'top';
  }

  const appContainer = document.querySelector('.app-container');
  if (appContainer) {
    appContainer.classList.toggle('position-bottom', toolbarPosition === 'bottom');
  }
}

let hasActiveAnswer = false;
let shouldSaveTranscript = true;
let autoAnswerTimeoutId = null;
let userOpacity = 1.0;
let answerHistory = [];
let currentAnswerIndex = -1;
let isShrunk = false;
let isAnswerExpanded = false;
let isStealthHoverEnabled = true;

// ── Backend Session State ──────────────────────────────────────────────────────
let backendUrl = '';       // set at startup — empty = offline mode
let sessionToken = '';     // session UUID (also used as token)
let activeSessionId = '';  // session UUID — same as sessionToken, stored separately for clarity
let lastAnswerOffset = 0;  // transcript 

// Cache storage for loaded resources
let backendResumes = [];
let backendDocs = [];

// Safe localStorage wrapper to prevent crashes in sandboxed/restricted environments
function safeGetItem(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    console.warn('[Stealth] localStorage.getItem failed:', e.message);
    return null;
  }
}

function safeSetItem(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.warn('[Stealth] localStorage.setItem failed:', e.message);
  }
}

let USER_ID = '856fdc6d-19b9-547e-be7b-0df7fa5b505b';

function normalizeUserId(value) {
  if (!value) return USER_ID;
  if (typeof value !== 'string') return String(value);
  const trimmed = value.trim();
  if (!trimmed) return USER_ID;
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  if (uuidPattern.test(trimmed)) return trimmed;
  return trimmed;
}

async function syncUserEmail(email) {
  if (!email) return;
  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
    const res = await fetch(`${base}/api/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        firebase_uid: `mock-uid-${email}`,
        email: email,
        name: email.split('@')[0],
        is_mock: true
      })
    });
    if (res.ok) {
      const data = await res.json();
      if (data && data.id) {
        USER_ID = data.id;
        safeSetItem('stealth_user_id', USER_ID);
        if (data.login_token) {
          safeSetItem('stealth_login_token', data.login_token);
        }
        console.log('[Stealth Settings] User synchronized successfully. Mapped USER_ID:', USER_ID);
        // Reload recent sessions if page is currently visible
        if (typeof recentSessionsView !== 'undefined' && recentSessionsView && recentSessionsView.style.display !== 'none') {
          loadRecentSessions();
        }
        // Reload user-specific resumes/docs dropdowns
        if (typeof loadDropdowns === 'function') {
          loadDropdowns();
        }
      }
    } else {
      console.warn('[Stealth Settings] User synchronization endpoint returned:', res.status);
    }
  } catch (err) {
    console.error('[Stealth Settings] Failed to synchronize user email with backend:', err.message);
  }
}

// ── Offline 4-Layer Memory State ────────────────────────────────────────────────
let offlineRecentQA = [];
let offlineRollingSummary = '';
let offlineUserContext = { resume: '', job_description: '', code_context: '', company: '', role: '' };

// ── Load dropdown options from backend ──────────────────────────────────────────
function setDropdownStatus(type, message) {
  const banner = document.getElementById('dropdown-status-msg');
  const text = document.getElementById('dropdown-status-text');
  if (!banner || !text) return;

  if (type === 'hidden') {
    banner.style.display = 'none';
    return;
  }
  // Show banner
  banner.style.display = 'flex';
  text.textContent = message;

  if (type === 'error') {
    banner.style.background = 'rgba(239,68,68,0.08)';
    banner.style.borderColor = 'rgba(239,68,68,0.25)';
    banner.style.color = '#fca5a5';
  } else if (type === 'loading') {
    banner.style.background = 'rgba(20, 184, 166,0.06)';
    banner.style.borderColor = 'rgba(20, 184, 166,0.2)';
    banner.style.color = '#2dd4bf';
  } else if (type === 'success') {
    banner.style.background = 'rgba(16,185,129,0.06)';
    banner.style.borderColor = 'rgba(16,185,129,0.2)';
    banner.style.color = '#34d399';
    // Auto-hide success banner after 2s
    setTimeout(() => setDropdownStatus('hidden'), 2000);
  }
}

async function loadDropdowns() {
  setDropdownStatus('loading', '⏳ Loading resumes and documents...');

  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';

    let resumeError = false;
    let docError = false;

    // Fetch resumes list
    try {
      const resResumes = await fetch(`${base}/api/resumes?user_id=${encodeURIComponent(normalizeUserId(USER_ID))}`);
      if (resResumes.ok) {
        backendResumes = await resResumes.json();
        // Clear existing options right before appending to avoid race condition duplicates
        while (setupResumeSelect.options.length > 1) setupResumeSelect.remove(1);
        backendResumes.forEach(r => {
          const opt = document.createElement('option');
          opt.value = r.id;
          opt.textContent = r.file_name;
          if (r.is_active) opt.selected = true;
          setupResumeSelect.appendChild(opt);
        });
        console.log(`[Stealth] Loaded ${backendResumes.length} resumes`);
      } else {
        console.warn('[Stealth] Resume fetch failed:', resResumes.status);
        resumeError = true;
      }
    } catch (fetchErr) {
      console.warn('[Stealth] Resume fetch error:', fetchErr.message);
      resumeError = true;
    }

    // Fetch knowledge/reference docs list
    try {
      const resDocs = await fetch(`${base}/api/knowledge?user_id=${encodeURIComponent(normalizeUserId(USER_ID))}`);
      if (resDocs.ok) {
        backendDocs = await resDocs.json();
        console.log(`[Stealth] Loaded ${backendDocs.length} docs/prompts`);

        // Clear existing options and groups right before appending to avoid race condition duplicates
        while (setupDocSelect.options.length > 1) setupDocSelect.remove(1);
        const existingGroups = setupDocSelect.querySelectorAll('optgroup');
        existingGroups.forEach(g => g.remove());

        const promptsGroup = document.createElement('optgroup');
        promptsGroup.label = 'Prompt Instructions';
        const docsGroup = document.createElement('optgroup');
        docsGroup.label = 'Reference Documents';

        backendDocs.forEach(d => {
          const opt = document.createElement('option');
          opt.value = d.id;
          opt.textContent = d.document_name;
          const isPrompt = d.document_type === 'prompt' ||
            d.document_name.toLowerCase().includes('prompt') ||
            d.document_name.toLowerCase().includes('instruction');
          if (isPrompt) promptsGroup.appendChild(opt);
          else docsGroup.appendChild(opt);
        });

        if (promptsGroup.children.length > 0) setupDocSelect.appendChild(promptsGroup);
        if (docsGroup.children.length > 0) setupDocSelect.appendChild(docsGroup);
      } else {
        console.warn('[Stealth] Docs fetch failed:', resDocs.status);
        docError = true;
      }
    } catch (fetchErr) {
      console.warn('[Stealth] Docs fetch error:', fetchErr.message);
      docError = true;
    }

    // Add Resume upload option at bottom of resume dropdown if not already present
    if (![...setupResumeSelect.options].some(o => o.value === '__upload__')) {
      const resumeUploadOpt = document.createElement('option');
      resumeUploadOpt.value = '__upload__';
      resumeUploadOpt.textContent = '⬆ Upload new resume...';
      setupResumeSelect.appendChild(resumeUploadOpt);
    }



    if (resumeError || docError) {
      const parts = [];
      if (resumeError) parts.push('resumes');
      if (docError) parts.push('documents');
      setDropdownStatus('error', `⚠️ Could not load ${parts.join(' & ')} — backend may be offline. Click ↺ Retry.`);
    } else {
      const total = backendResumes.length + backendDocs.length;
      setDropdownStatus('success', `✓ Loaded ${backendResumes.length} resume(s) and ${backendDocs.length} document(s)`);
      updateResumeJdScore();
    }

  } catch (e) {
    console.error('[Stealth] Failed to load dropdowns from backend:', e.message);
    setDropdownStatus('error', `⚠️ Backend unreachable (${e.message}). Start the backend then click ↺ Retry.`);
  }
}

// Wire up the retry button
document.addEventListener('DOMContentLoaded', () => {
  const retryBtn = document.getElementById('dropdown-retry-btn');
  if (retryBtn) {
    retryBtn.addEventListener('click', () => loadDropdowns());
  }
});

// Initialise setup form on load
(async () => {
  // Load local context (L4) on startup for offline use
  try {
    offlineUserContext = await window.electronAPI.getL4Context() || { resume: '', job_description: '', code_context: '', company: '', role: '' };
    console.log('[Stealth] Loaded local L4 context successfully');
  } catch (e) {
    console.error('[Stealth] Failed to load local L4 context:', e.message);
  }

  // Load dropdown options
  await loadDropdowns();
  updateResumeJdScore();

  // Reset window size to setup dimensions so it's fully visible and centered on load/reload
  try {
    window.electronAPI.resizeWindow(600, 580, 'top', true);
  } catch (e) {
    console.error('[Stealth] Failed to resize window on load:', e.message);
  }

  // Set the panel width CSS variable from localStorage on startup
  const savedPanelWidth = safeGetItem('stealth_panelWidth') || '620';
  document.documentElement.style.setProperty('--panel-width', savedPanelWidth + 'px');

  // Initially show setup form in index.html
  setupView.style.display = 'flex';
  toolbarView.style.display = 'none';
  updateWizardView();
})();

// ── Setup View Header Buttons ─────────────────────────────────────────────────

const recentSessionsView = document.getElementById('recent-sessions-view');
const recentSessionsTable = document.getElementById('recent-sessions-table');
const sessionsCountBadge = document.getElementById('sessions-count-badge');
// (setupView already declared at top of file)


// Helper: format duration seconds → M:SS
function formatDuration(seconds) {
  if (!seconds) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

// Helper: mode badge color
function getModeBadge(sessionName) {
  const n = (sessionName || '').toLowerCase();
  if (n.includes('coding test')) return { bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.3)', color: '#fbbf24', label: 'Coding Test' };
  if (n.includes('hr')) return { bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.3)', color: '#4ade80', label: 'HR Round' };
  return { bg: 'rgba(20, 184, 166,0.12)', border: 'rgba(20, 184, 166,0.3)', color: '#2dd4bf', label: 'Interview+Coding' };
}

// Show/hide recent sessions page
async function showRecentSessionsPage() {
  console.log('[Stealth Debug] showRecentSessionsPage called');
  setupView.style.display = 'none';
  recentSessionsView.style.display = 'flex';
  console.log('[Stealth Debug] setupView hidden, recentSessionsView displayed');
  // Resize window to give sessions table room
  pendingProgrammaticResizes++;
  window.electronAPI.resizeWindow(800, 580, 'top', true);
  window.electronAPI.setIgnoreMouseEvents(false, {});
  await loadRecentSessions();
}

function hideRecentSessionsPage() {
  console.log('[Stealth Debug] hideRecentSessionsPage called');
  recentSessionsView.style.display = 'none';
  setupView.style.display = 'flex';
  console.log('[Stealth Debug] recentSessionsView hidden, setupView displayed');
  // Resize window back to default setup wizard bounds
  pendingProgrammaticResizes++;
  window.electronAPI.resizeWindow(600, 580, 'top', true);
}

async function loadRecentSessions() {
  recentSessionsTable.innerHTML = `<div style="text-align:center;padding:30px 0;color:var(--text-muted);font-size:12px;">
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom:8px;opacity:0.4;display:block;margin:0 auto 8px;"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
    Loading sessions...
  </div>`;

  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
    const res = await fetch(`${base}/api/sessions?user_id=${USER_ID}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const sessions = await res.json();

    if (sessionsCountBadge) sessionsCountBadge.textContent = `${sessions.length} total`;

    if (!sessions || sessions.length === 0) {
      recentSessionsTable.innerHTML = `<div style="text-align:center;padding:40px 0;color:var(--text-muted);font-size:12px;">No sessions found.</div>`;
      return;
    }

    recentSessionsTable.innerHTML = '';

    sessions.forEach(s => {
      const badge = getModeBadge(s.session_name);
      const date = s.created_at ? new Date(s.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
      const duration = formatDuration(s.duration_seconds);
      let title = s.company_name || '—';
      if (title === 'Stealth Practice' || title === 'Stealth AI') {
        title = s.session_name || '—';
      }
      title = title.replace(/\s*\(.*\)$/, '')
        .replace(/^Mock Prep Session with\s+/i, '')
        .replace(/^Stealth Session with\s+/i, '');

      const description = `${s.role_name || ''}${s.company_name ? ` (${s.company_name})` : ''}`;

      const row = document.createElement('div');
      row.style.cssText = 'display:grid;grid-template-columns:2fr 2.3fr 1.5fr 1fr 0.8fr 1.2fr 2.2fr;gap:4px;align-items:center;padding:6px 6px;border-radius:7px;border:1px solid rgba(255,255,255,0.03);transition:background 0.15s;';
      row.innerHTML = `
        <div style="font-size:11px;font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${title}">${title}</div>
        <div style="font-size:10px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${description}">${description || '—'}</div>
        <div><span style="display:inline-block;padding:2px 6px;border-radius:20px;font-size:9px;font-weight:700;background:${badge.bg};border:1px solid ${badge.border};color:${badge.color};white-space:nowrap;">${badge.label}</span></div>
        <div style="font-size:10px;font-weight:700;color:var(--text-secondary);">${duration}</div>
        <div style="font-size:10px;color:var(--text-secondary);text-align:center;">${s.ai_usage ?? 0}</div>
        <div style="font-size:10px;color:var(--text-muted);">${date}</div>
        <div>
          <div style="display: flex; gap: 5px; align-items: center;">
            <!-- Transcript Button -->
            <button class="session-transcript-btn interactive" data-id="${s.id}" title="View Transcript" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(20, 184, 166,0.12);border:1px solid rgba(20, 184, 166,0.25);color:#2dd4bf;outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
            </button>
            <!-- Summary Button -->
            <button class="session-summary-btn interactive" data-id="${s.id}" title="View Summary" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.25);color:#4ade80;outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
            </button>
            <!-- Edit Button -->
            <button class="session-edit-btn interactive" data-id="${s.id}" title="Edit Config" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);color:rgba(255,255,255,0.7);outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
            </button>
            <!-- Delete Button -->
            <button class="session-delete-btn interactive" data-id="${s.id}" title="Delete Session" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);color:#ef4444;outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
        </div>
      `;

      row.addEventListener('mouseenter', () => row.style.background = 'rgba(255,255,255,0.04)');
      row.addEventListener('mouseleave', () => row.style.background = '');

      // Helper to pre-fill wizard
      const prefillWizard = async () => {
        if (s.company_name && setupCompany) setupCompany.value = s.company_name;
        if (s.role_name && setupRole) setupRole.value = s.role_name;

        // Auto-fill Job Description from session object or context
        if (setupJd) {
          if (s.job_description && typeof s.job_description === 'object' && s.job_description.description) {
            setupJd.value = s.job_description.description;
          } else if (typeof s.job_description === 'string' && s.job_description.trim()) {
            setupJd.value = s.job_description.trim();
          } else if (offlineUserContext && offlineUserContext.job_description) {
            setupJd.value = offlineUserContext.job_description;
          }
        }

        // Match session type badge
        document.querySelectorAll('.type-badge').forEach(b => {
          b.classList.remove('active');
          if ((s.session_name || '').toLowerCase().includes(b.dataset.type.toLowerCase())) {
            b.classList.add('active');
          }
        });
        if (!document.querySelector('.type-badge.active')) {
          document.querySelector('.type-badge')?.classList.add('active');
        }
        hideRecentSessionsPage();
        currentStep = 1;
        updateWizardView();
      };

      // Edit button click
      row.querySelector('.session-edit-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        prefillWizard();
      });

      // View Transcript button click
      row.querySelector('.session-transcript-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
          const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
          const [tRes, aRes] = await Promise.all([
            fetch(`${base}/api/sessions/${s.id}/transcripts`),
            fetch(`${base}/api/sessions/${s.id}/answers`)
          ]);

          if (!tRes.ok) throw new Error(`Transcripts: HTTP ${tRes.status}`);
          if (!aRes.ok) throw new Error(`Answers: HTTP ${aRes.status}`);

          const transcripts = await tRes.json();
          const answers = await aRes.json();

          if ((!transcripts || transcripts.length === 0) && (!answers || answers.length === 0)) {
            showModalOverlay(`Transcript — ${title}`, `<div style="text-align:center;padding:40px 0;color:var(--text-muted);font-size:11px;">No transcript records found for this session.</div>`);
            return;
          }

          // Combine and sort chronologically
          const timeline = [
            ...transcripts.map(t => ({ ...t, type: 'audio' })),
            ...answers.map(a => ({ ...a, type: 'ai' }))
          ];
          timeline.sort((x, y) => new Date(x.created_at) - new Date(y.created_at));

          // Generate beautiful timeline HTML
          const html = timeline.map(b => {
            const timeStr = b.created_at ? new Date(b.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '';

            if (b.type === 'audio') {
              // Handle all speaker types
              let speakerLabel, speakerColor, borderColor;
              if (b.speaker === 'interviewer') {
                speakerLabel = 'Interviewer'; speakerColor = '#5eead4'; borderColor = 'rgba(94,234,212,0.4)';
              } else if (b.speaker === 'you') {
                speakerLabel = 'You'; speakerColor = '#4ade80'; borderColor = 'rgba(74,222,128,0.4)';
              } else if (b.speaker === 'full_session') {
                speakerLabel = 'Full Session Transcript'; speakerColor = '#a78bfa'; borderColor = 'rgba(167,139,250,0.4)';
              } else {
                speakerLabel = 'Audio'; speakerColor = '#9ca3af'; borderColor = 'rgba(156,163,175,0.3)';
              }
              return `
                <div style="margin-bottom: 12px; padding: 6px 10px; background: rgba(255,255,255,0.02); border-left: 2px solid ${borderColor}; border-radius: 0 6px 6px 0;">
                  <div style="font-size: 9px; font-weight: 700; color: ${speakerColor}; margin-bottom: 2px;">
                    [${timeStr}] ${speakerLabel}
                  </div>
                  <div style="font-size: 10.5px; color: #e4e4e7; white-space: pre-wrap; line-height: 1.4;">${b.content}</div>
                </div>
              `;

            } else {
              return `
                <div style="margin-bottom: 16px; padding: 10px 12px; background: rgba(20, 184, 166, 0.05); border-left: 2px solid #14b8a6; border-radius: 0 8px 8px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                  <div style="font-size: 9px; font-weight: 800; color: #5eead4; margin-bottom: 6px; display: flex; align-items: center; gap: 4px;">
                    <span style="display: inline-block; width: 4px; height: 4px; background: #5eead4; border-radius: 50%;"></span>
                    [${timeStr}] AI
                  </div>
                  <div style="margin-bottom: 6px; font-size: 10.5px; color: #e4e4e7; line-height: 1.4;">
                    <span style="margin-right: 4px;">💬</span><strong>Question:</strong> ${b.question}
                  </div>
                  <div style="border-top: 1px dashed rgba(20, 184, 166, 0.2); margin: 6px 0; padding-top: 6px;">
                    <div style="font-size: 10.5px; color: #2dd4bf; font-weight: 600; margin-bottom: 4px; line-height: 1.4;">
                      <span style="margin-right: 4px;">⭐️</span><strong>Answer:</strong>
                    </div>
                    <div style="white-space: pre-wrap; font-size: 10.5px; line-height: 1.5; color: #fff;">${b.answer}</div>
                  </div>
                </div>
              `;
            }
          }).join('');

          // Generate raw export string for copy to clipboard
          const rawText = timeline.map(b => {
            const timeStr = b.created_at ? new Date(b.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '';
            if (b.type === 'audio') {
              let speakerLabel;
              if (b.speaker === 'interviewer') speakerLabel = 'Interviewer';
              else if (b.speaker === 'you') speakerLabel = 'You';
              else if (b.speaker === 'full_session') speakerLabel = 'Full Session Transcript';
              else speakerLabel = 'Audio'; // 'system', 'mixed_audio', etc.
              return `[${timeStr}] ${speakerLabel}\n${b.content}`;
            } else {
              return `[${timeStr}] AI\n💬 **Question**: ${b.question}\n\n---\n\n⭐️ **Answer**:  \n${b.answer}`;
            }
          }).join('\n\n');

          // Create container with a Copy Button at the top
          const wrapperHtml = `
            <div style="display: flex; flex-direction: column; gap: 12px; height: 100%;">
              <div style="display: flex; justify-content: flex-end; margin-bottom: 4px;">
                <button id="copy-timeline-btn" class="interactive" style="background: rgba(20, 184, 166,0.15); border: 1px solid rgba(20, 184, 166,0.3); color: #5eead4; border-radius: 6px; padding: 4px 12px; font-size: 10px; font-weight: 600;  outline: none; transition: all 0.2s; -webkit-app-region: no-drag;">
                  Copy Raw Timeline
                </button>
              </div>
              <div style="flex: 1;">
                ${html}
              </div>
            </div>
          `;

          showModalOverlay(`Transcript — ${title}`, wrapperHtml);

          // Add copy click listener
          const copyBtn = document.getElementById('copy-timeline-btn');
          if (copyBtn) {
            copyBtn.addEventListener('click', () => {
              navigator.clipboard.writeText(rawText).then(() => {
                copyBtn.textContent = 'Copied!';
                copyBtn.style.background = 'rgba(74, 222, 128, 0.15)';
                copyBtn.style.borderColor = 'rgba(74, 222, 128, 0.3)';
                copyBtn.style.color = '#4ade80';
                setTimeout(() => {
                  copyBtn.textContent = 'Copy Raw Timeline';
                  copyBtn.style.background = 'rgba(20, 184, 166,0.15)';
                  copyBtn.style.borderColor = 'rgba(20, 184, 166,0.3)';
                  copyBtn.style.color = '#5eead4';
                }, 2000);
              });
            });
          }
        } catch (err) {
          showInlineError('Failed to load transcript: ' + err.message, recentSessionsTable);
        }
      });

      // View Summary button click
      row.querySelector('.session-summary-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
          const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
          const res = await fetch(`${base}/api/sessions/${s.id}`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const sessionDetail = await res.json();

          const summaryText = sessionDetail.summary || `No summary generated yet for this session. Complete the session or generate one from the web dashboard.`;

          const html = `
            <div style="background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); padding: 12px; border-radius: 8px;">
              <div style="font-size: 11px; white-space: pre-wrap; line-height: 1.6; color: rgba(255,255,255,0.95);">${summaryText}</div>
            </div>
          `;

          showModalOverlay(`Session Summary — ${title}`, html);
        } catch (err) {
          showInlineError('Failed to load session summary: ' + err.message, recentSessionsTable);
        }
      });

      // Delete button click
      row.querySelector('.session-delete-btn').addEventListener('click', async (e) => {
        e.stopPropagation();
        // Inline confirm via modal overlay instead of OS confirm()
        showModalOverlay('Delete Session', `
          <div style="text-align:center;padding:8px 0;">
            <div style="font-size:12px;color:rgba(255,255,255,0.8);margin-bottom:20px;">Are you sure you want to permanently delete <strong>${title}</strong>?</div>
            <div style="display:flex;gap:10px;justify-content:center;">
              <button id="confirm-delete-yes" style="padding:8px 22px;background:rgba(239,68,68,0.2);border:1px solid rgba(239,68,68,0.5);color:#fca5a5;border-radius:7px;font-size:11px;">Delete</button>
              <button id="confirm-delete-no" style="padding:8px 22px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:rgba(255,255,255,0.7);border-radius:7px;font-size:11px;">Cancel</button>
            </div>
          </div>
        `);
        // Wire up confirm buttons after modal renders
        setTimeout(() => {
          const yesBtn = document.getElementById('confirm-delete-yes');
          const noBtn = document.getElementById('confirm-delete-no');
          const overlay = document.getElementById('stealth-modal-overlay');
          if (noBtn) noBtn.addEventListener('click', () => overlay && overlay.remove());
          if (yesBtn) yesBtn.addEventListener('click', async () => {
            if (overlay) overlay.remove();
            try {
              const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
              const deleteRes = await fetch(`${base}/api/sessions/${s.id}`, { method: 'DELETE' });
              if (deleteRes.ok) {
                await loadRecentSessions();
              } else {
                showInlineError('Failed to delete session.', recentSessionsTable);
              }
            } catch (err) {
              console.error('[Recent Sessions] Delete error:', err.message);
              showInlineError('Delete failed: ' + err.message, recentSessionsTable);
            }
          });
        }, 50);
      });

      recentSessionsTable.appendChild(row);
    });

  } catch (e) {
    recentSessionsTable.innerHTML = `<div style="text-align:center;padding:30px 0;color:#ef4444;font-size:12px;">Failed to load sessions: ${e.message}</div>`;
    console.error('[Recent Sessions] Load error:', e.message);
  }
}

// ── Inline Error Banner ────────────────────────────────────────────────────────
// Shows a self-dismissing red error strip inside `container` (defaults to setupView)
// instead of raising a blocking OS alert().
function showInlineError(msg, container) {
  const target = container || document.getElementById('setup-view') || document.body;
  const existing = target.querySelector('.inline-error-banner');
  if (existing) existing.remove();

  const banner = document.createElement('div');
  banner.className = 'inline-error-banner';
  banner.style.cssText = [
    'display:flex', 'align-items:center', 'gap:8px',
    'background:rgba(239,68,68,0.12)', 'border:1px solid rgba(239,68,68,0.45)',
    'color:#fca5a5', 'border-radius:8px', 'padding:9px 12px',
    'font-size:11px', 'line-height:1.4', 'margin:6px 0',
    'animation:fadeInDown 0.2s ease', 'position:relative', 'z-index:9'
  ].join(';');
  banner.innerHTML = `<span style="font-size:14px;flex-shrink:0;">⚠️</span><span style="flex:1;">${msg}</span>`
    + `<button onclick="this.parentElement.remove()" style="background:none;border:none;color:#fca5a5;font-size:14px;padding:0 0 0 6px;line-height:1;">✕</button>`;
  target.prepend(banner);
  setTimeout(() => { if (banner.parentElement) banner.remove(); }, 5000);
}

// Modal overlay helper to display transcripts and summaries dynamically
function showModalOverlay(title, contentHtml) {
  let modal = document.getElementById('stealth-modal-overlay');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'stealth-modal-overlay';
    modal.style.cssText = `
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(10, 10, 12, 0.95);
      backdrop-filter: blur(10px);
      z-index: 10000;
      display: flex;
      flex-direction: column;
      padding: 16px;
      box-sizing: border-box;
      animation: fadeIn 0.15s ease-out;
    `;
    document.body.appendChild(modal);
  }

  modal.innerHTML = `
    <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px; margin-bottom: 12px; flex-shrink: 0;">
      <h3 style="margin: 0; font-size: 11px; font-weight: 700; color: #fff; text-transform: uppercase; letter-spacing: 0.05em;">${title}</h3>
      <button id="modal-close-btn" class="interactive" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; border-radius: 6px; padding: 4px 10px; font-size: 10px; font-weight: 600;  outline: none; transition: background 0.2s;">
        Close
      </button>
    </div>
    <div style="flex: 1; overflow-y: auto; font-size: 11px; color: rgba(255,255,255,0.85); line-height: 1.5; padding-right: 4px;">
      ${contentHtml}
    </div>
  `;

  modal.querySelector('#modal-close-btn').addEventListener('click', () => {
    modal.remove();
  });
}

// Quit app button — forcefully kills entire Electron process
const quitAppBtn = document.getElementById('quit-app-btn');
if (quitAppBtn) {
  quitAppBtn.addEventListener('click', () => {
    console.log('[Stealth] Main close button clicked.');
    window.electronAPI.quitApp();
  });
}

// Recent-view quit button
const recentViewQuitBtn = document.getElementById('recent-view-quit-btn');
if (recentViewQuitBtn) {
  recentViewQuitBtn.addEventListener('click', () => {
    console.log('[Stealth] Recent sessions view close button clicked.');
    window.electronAPI.quitApp();
  });
}

// Recent Sessions button → toggle to recent sessions page
const recentSessionsBtn = document.getElementById('recent-sessions-btn');
if (recentSessionsBtn) {
  recentSessionsBtn.addEventListener('click', () => showRecentSessionsPage());
}

// Back button on recent sessions page → back to setup
const recentSessionsBackBtn = document.getElementById('recent-sessions-back-btn');
if (recentSessionsBackBtn) {
  recentSessionsBackBtn.addEventListener('click', () => hideRecentSessionsPage());
}

// Dropdown change handlers to open file selector
setupResumeSelect.addEventListener('change', () => {
  if (setupResumeSelect.value === '__upload__') {
    setupResumeFile.click();
    setupResumeSelect.value = '';
    return;
  }
  updateResumeJdScore();
});

let scoreDebounceTimeout;
function debounceUpdateScore() {
  clearTimeout(scoreDebounceTimeout);
  scoreDebounceTimeout = setTimeout(() => {
    updateResumeJdScore();
  }, 800);
}

setupJd.addEventListener('input', debounceUpdateScore);
setupCompany.addEventListener('input', debounceUpdateScore);
setupRole.addEventListener('input', debounceUpdateScore);

// Extra Context Action Buttons
const inputPromptBtn = document.getElementById('input-prompt-btn');
const uploadDocBtn = document.getElementById('upload-doc-btn');

const promptModal = document.getElementById('prompt-modal');
const modalPromptName = document.getElementById('modal-prompt-name');
const modalPromptContent = document.getElementById('modal-prompt-content');
const modalPromptCancel = document.getElementById('modal-prompt-cancel');
const modalPromptSave = document.getElementById('modal-prompt-save');

if (inputPromptBtn && promptModal) {
  inputPromptBtn.addEventListener('click', () => {
    modalPromptName.value = '';
    modalPromptContent.value = '';
    promptModal.style.display = 'flex';
  });
}

if (modalPromptCancel && promptModal) {
  modalPromptCancel.addEventListener('click', () => {
    promptModal.style.display = 'none';
  });
}

if (modalPromptSave && promptModal) {
  modalPromptSave.addEventListener('click', async () => {
    const promptName = modalPromptName.value.trim();
    const promptContent = modalPromptContent.value.trim();
    if (!promptName || !promptContent) return;

    modalPromptSave.disabled = true;
    modalPromptSave.textContent = 'Saving...';

    try {
      const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
      const res = await fetch(`${base}/api/knowledge`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          user_id: USER_ID,
          document_name: promptName,
          document_type: 'prompt',
          content: promptContent
        })
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      backendDocs.push(data);
      await loadDropdowns();
      setupDocSelect.value = data.id;
      promptModal.style.display = 'none';
    } catch (e) {
      console.error('[Stealth] Custom prompt save failed:', e.message);
      showInlineError('Failed to save custom prompt instructions.', document.getElementById('setup-step-2'));
    } finally {
      modalPromptSave.disabled = false;
      modalPromptSave.textContent = 'Save';
    }
  });
}

if (uploadDocBtn) {
  uploadDocBtn.addEventListener('click', () => {
    setupDocFile.click();
  });
}

// Resume-JD Match Scoring function
async function updateResumeJdScore() {
  const scoreSpan = document.getElementById('resume-jd-score');
  if (!scoreSpan) return;

  const resumeId = setupResumeSelect.value;
  if (!resumeId) {
    scoreSpan.style.display = 'none';
    return;
  }

  const resumeObj = backendResumes.find(r => r.id === resumeId);
  if (!resumeObj) {
    scoreSpan.style.display = 'none';
    return;
  }

  scoreSpan.style.display = 'inline-block';
  scoreSpan.textContent = '⏳ Scoring...';
  scoreSpan.style.color = '#38bdf8';
  scoreSpan.style.background = 'rgba(56,189,248,0.1)';

  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
    const res = await fetch(`${base}/api/answers/transcript`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        resume_content: resumeObj.parsed_content,
        jd_content: setupJd.value.trim(),
        role: setupRole.value.trim() || 'Software Engineer',
        company: setupCompany.value.trim() || 'Target Company'
      })
    });

    if (res.ok) {
      const data = await res.json();
      const scoreNum = parseInt(data.answer);
      if (!isNaN(scoreNum)) {
        scoreSpan.textContent = `🎯 Match: ${scoreNum}%`;
        if (scoreNum >= 80) {
          scoreSpan.style.color = '#10b981';
          scoreSpan.style.background = 'rgba(16,185,129,0.1)';
        } else if (scoreNum >= 50) {
          scoreSpan.style.color = '#f59e0b';
          scoreSpan.style.background = 'rgba(245,158,11,0.1)';
        } else {
          scoreSpan.style.color = '#ef4444';
          scoreSpan.style.background = 'rgba(239,68,68,0.1)';
        }
      } else {
        scoreSpan.textContent = '⚠️ Match: N/A';
      }
    } else {
      scoreSpan.textContent = '⚠️ Match: Error';
    }
  } catch (e) {
    console.error('[Stealth] Score calculation error:', e);
    scoreSpan.textContent = '⚠️ Match: Offline';
  }
}

// File upload event handlers
setupResumeFile.addEventListener('change', async () => {
  if (!setupResumeFile.files.length) return;
  const file = setupResumeFile.files[0];
  const formData = new FormData();
  formData.append('file', file);
  formData.append('user_id', normalizeUserId(USER_ID));

  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
    const res = await fetch(`${base}/api/resumes/upload`, {
      method: 'POST',
      body: formData
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    backendResumes.push(data);
    // Refresh dropdowns and pre-select the newly uploaded resume
    await loadDropdowns();
    setupResumeSelect.value = data.id;
  } catch (e) {
    console.error('[Stealth] Resume upload failed:', e.message);
    showInlineError('Failed to upload resume — make sure the backend is running.', document.getElementById('setup-step-2'));
  } finally {
    setupResumeFile.value = '';
  }
});

setupDocFile.addEventListener('change', async () => {
  if (!setupDocFile.files.length) return;
  const file = setupDocFile.files[0];
  const formData = new FormData();
  formData.append('file', file);
  formData.append('document_type', 'document');
  formData.append('user_id', normalizeUserId(USER_ID));

  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
    const res = await fetch(`${base}/api/knowledge/upload`, {
      method: 'POST',
      body: formData
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    backendDocs.push(data);
    // Refresh dropdowns and pre-select the newly uploaded doc
    await loadDropdowns();
    setupDocSelect.value = data.id;
  } catch (e) {
    console.error('[Stealth] Document upload failed:', e.message);
    showInlineError('Failed to upload reference document — make sure the backend is running.', document.getElementById('setup-step-2'));
  } finally {
    setupDocFile.value = '';
  }
});

// Dropdown/Badges session type bindings
const typeBadges = document.querySelectorAll('.type-badge');
let selectedSessionType = 'Interview+Coding';

typeBadges.forEach(badge => {
  badge.addEventListener('click', () => {
    typeBadges.forEach(b => b.classList.remove('active'));
    badge.classList.add('active');
    selectedSessionType = badge.getAttribute('data-type');
  });
});

// Step navigation elements
const setupBackBtn = document.getElementById('setup-back-btn');
const setupNextBtn = document.getElementById('setup-next-btn');

const step1Container = document.getElementById('setup-step-1');
const step2Container = document.getElementById('setup-step-2');
const step3Container = document.getElementById('setup-step-3');

const stepNum1 = document.getElementById('step-num-1');
const stepNum2 = document.getElementById('step-num-2');
const stepNum3 = document.getElementById('step-num-3');

const stepText1 = document.getElementById('step-text-1');
const stepText2 = document.getElementById('step-text-2');
const stepText3 = document.getElementById('step-text-3');

const stepLine1 = document.getElementById('step-line-1');
const stepLine2 = document.getElementById('step-line-2');

let currentStep = 1;

function updateWizardView() {
  step1Container.style.display = 'none';
  step2Container.style.display = 'none';
  step3Container.style.display = 'none';

  const indicators = [
    { num: stepNum1, text: stepText1 },
    { num: stepNum2, text: stepText2 },
    { num: stepNum3, text: stepText3 }
  ];

  indicators.forEach((ind, i) => {
    const stepIdx = i + 1;
    if (stepIdx < currentStep) {
      ind.num.style.background = '#10b981';
      ind.num.style.borderColor = '#10b981';
      ind.num.textContent = '✓';
      ind.num.style.color = '#fff';
      ind.text.style.color = '#10b981';
    } else if (stepIdx === currentStep) {
      ind.num.style.background = '#ffffff';
      ind.num.style.borderColor = '#ffffff';
      ind.num.textContent = stepIdx;
      ind.num.style.color = '#0a0a0f';
      ind.text.style.color = 'var(--text-primary)';
    } else {
      ind.num.style.background = 'rgba(255,255,255,0.04)';
      ind.num.style.borderColor = 'rgba(255,255,255,0.08)';
      ind.num.textContent = stepIdx;
      ind.num.style.color = 'var(--text-muted)';
      ind.text.style.color = 'var(--text-muted)';
    }
  });

  stepLine1.style.background = currentStep > 1 ? '#ffffff' : 'rgba(255,255,255,0.06)';
  stepLine2.style.background = currentStep > 2 ? '#ffffff' : 'rgba(255,255,255,0.06)';

  if (currentStep === 1) {
    step1Container.style.display = 'flex';
    step1Container.style.flexDirection = 'column';
    setupBackBtn.style.display = 'none';
    setupNextBtn.style.display = 'block';
    startSessionBtn.style.display = 'none';
  } else if (currentStep === 2) {
    step2Container.style.display = 'flex';
    step2Container.style.flexDirection = 'column';
    setupBackBtn.style.display = 'block';
    setupNextBtn.style.display = 'block';
    startSessionBtn.style.display = 'none';
  } else if (currentStep === 3) {
    step3Container.style.display = 'flex';
    step3Container.style.flexDirection = 'column';
    setupBackBtn.style.display = 'block';
    setupNextBtn.style.display = 'none';
    startSessionBtn.style.display = 'block';
  }
}

setupNextBtn.addEventListener('click', () => {
  if (currentStep === 1) {
    if (!setupCompany.value.trim()) {
      showInlineError('Please enter a company name.', document.getElementById('setup-step-1'));
      return;
    }
    if (!setupRole.value.trim()) {
      showInlineError('Please enter a target role.', document.getElementById('setup-step-1'));
      return;
    }
    if (!setupJd.value.trim()) {
      showInlineError('Please paste the target job description (required).', document.getElementById('setup-step-1'));
      return;
    }
  }
  if (currentStep === 2) {
    if (!setupResumeSelect.value) {
      showInlineError('Please select a resume (required for background profiling).', document.getElementById('setup-step-2'));
      return;
    }
  }
  if (currentStep < 3) {
    currentStep++;
    updateWizardView();
  }
});

setupBackBtn.addEventListener('click', () => {
  if (currentStep > 1) {
    currentStep--;
    updateWizardView();
  }
});

// ── Edit Session Feature ──────────────────────────────────────────────────────
// Tracks the current live session data so the edit modal can be pre-populated.
let liveSessionData = {
  company: '',
  role: '',
  jd: '',
  resumeId: '',
  docId: ''
};

const editSessionModal = document.getElementById('edit-session-modal');
const editSessionCloseBtn = document.getElementById('edit-session-close');
const editSessionCancelBtn = document.getElementById('edit-session-cancel');
const editSessionSaveBtn = document.getElementById('edit-session-save');
const editSessionStatus = document.getElementById('edit-session-status');
const editCompanyInput = document.getElementById('edit-company');
const editRoleInput = document.getElementById('edit-role');
const editJdInput = document.getElementById('edit-jd');
const editResumeSelect = document.getElementById('edit-resume-select');
const editDocSelect = document.getElementById('edit-doc-select');
const editSessionBtn = document.getElementById('edit-session-btn');

/** Opens the edit session modal and populates all fields with live session data */
function openEditSessionModal() {
  if (!editSessionModal) return;

  // Populate fields with current live session data
  if (editCompanyInput) editCompanyInput.value = liveSessionData.company || '';
  if (editRoleInput) editRoleInput.value = liveSessionData.role || '';
  if (editJdInput) editJdInput.value = liveSessionData.jd || '';

  // Populate resume options from cached list
  if (editResumeSelect) {
    editResumeSelect.innerHTML = '<option value="">-- No Resume --</option>';
    backendResumes.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.id;
      opt.textContent = r.file_name || r.id;
      if (r.id === liveSessionData.resumeId) opt.selected = true;
      editResumeSelect.appendChild(opt);
    });
  }

  // Populate doc options from cached list
  if (editDocSelect) {
    editDocSelect.innerHTML = '<option value="">-- No Document --</option>';
    backendDocs.forEach(d => {
      const opt = document.createElement('option');
      opt.value = d.id;
      opt.textContent = d.document_name || d.id;
      if (d.id === liveSessionData.docId) opt.selected = true;
      editDocSelect.appendChild(opt);
    });
  }

  // Hide status, reset button
  if (editSessionStatus) { editSessionStatus.style.display = 'none'; editSessionStatus.textContent = ''; }
  if (editSessionSaveBtn) { editSessionSaveBtn.disabled = false; editSessionSaveBtn.textContent = ''; editSessionSaveBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Update Session'; }

  editSessionModal.style.display = 'flex';
  window.electronAPI.setIgnoreMouseEvents(false);
  updateWindowSize();
}

function closeEditSessionModal() {
  if (editSessionModal) editSessionModal.style.display = 'none';
  updateWindowSize();
}

if (editSessionBtn) {
  editSessionBtn.addEventListener('click', () => {
    openEditSessionModal();
  });
}
if (editSessionCloseBtn) editSessionCloseBtn.addEventListener('click', closeEditSessionModal);
if (editSessionCancelBtn) editSessionCancelBtn.addEventListener('click', closeEditSessionModal);

if (editSessionSaveBtn) {
  editSessionSaveBtn.addEventListener('click', async () => {
    const newCompany = (editCompanyInput?.value || '').trim() || liveSessionData.company;
    const newRole = (editRoleInput?.value || '').trim() || liveSessionData.role;
    const newJd = (editJdInput?.value || '').trim();
    const newResumeId = editResumeSelect?.value || '';
    const newDocId = editDocSelect?.value || '';

    if (!newJd) {
      if (editSessionStatus) {
        editSessionStatus.style.display = 'block';
        editSessionStatus.style.background = 'rgba(239,68,68,0.12)';
        editSessionStatus.style.border = '1px solid rgba(239,68,68,0.3)';
        editSessionStatus.style.color = '#fca5a5';
        editSessionStatus.textContent = '⚠️ Job Description is required.';
      }
      return;
    }

    editSessionSaveBtn.disabled = true;
    editSessionSaveBtn.textContent = 'Updating...';

    // 1. Patch backend session metadata (company/role)
    if (backendUrl && sessionToken) {
      try {
        await window.electronAPI.updateBackendSession(sessionToken, {
          company_name: newCompany,
          role_name: newRole,
          session_name: `${newCompany} (${selectedSessionType})`
        });
      } catch (e) {
        console.warn('[EditSession] Backend patch failed:', e.message);
      }
    }

    // 2. Update the L4 context so the AI uses updated data for future answers
    const resumeObj = backendResumes.find(r => r.id === newResumeId);
    const resume = resumeObj ? resumeObj.parsed_content : '';
    const docObj = backendDocs.find(d => d.id === newDocId);
    const docText = docObj ? docObj.content : '';
    const isPrompt = docObj ? (docObj.document_type === 'prompt' || docObj.document_name.toLowerCase().includes('prompt') || docObj.document_name.toLowerCase().includes('instruction')) : false;

    try {
      const currentModel = document.getElementById('setup-model-select')?.value || 'gemini-flash';
      await window.electronAPI.saveL4Context({
        resume,
        resume_id: newResumeId || '',
        job_description: newJd,
        code_context: docText,
        doc_id: newDocId || '',
        doc_type: isPrompt ? 'prompt' : 'document',
        company: newCompany,
        role: newRole,
        model: currentModel
      });
      offlineUserContext = { resume, job_description: newJd, code_context: docText, company: newCompany, role: newRole };
    } catch (e) {
      console.error('[EditSession] Failed to update context:', e.message);
    }

    // 3. Update local live session tracking state
    liveSessionData = { company: newCompany, role: newRole, jd: newJd, resumeId: newResumeId, docId: newDocId };

    // 4. Show success and close
    if (editSessionStatus) {
      editSessionStatus.style.display = 'block';
      editSessionStatus.style.background = 'rgba(16,185,129,0.12)';
      editSessionStatus.style.border = '1px solid rgba(16,185,129,0.3)';
      editSessionStatus.style.color = '#10b981';
      editSessionStatus.textContent = '✓ Session context updated — AI will use new data for next answer.';
    }
    editSessionSaveBtn.disabled = false;
    editSessionSaveBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Update Session';

    setTimeout(() => closeEditSessionModal(), 1800);
  });
}


let sessionTimerInterval = null;
let sessionSecondsElapsed = 0;
const sessionTimerElement = document.getElementById('session-timer');

function startSessionTimer() {
  if (sessionTimerInterval) clearInterval(sessionTimerInterval);
  sessionSecondsElapsed = 0;
  const stopBtnTimer = document.getElementById('stop-btn-timer');
  if (sessionTimerElement) {
    sessionTimerElement.textContent = '00:00';
    // kept hidden — stop button shows the time now
  }
  if (stopBtnTimer) stopBtnTimer.textContent = '00:00';

  sessionTimerInterval = setInterval(() => {
    sessionSecondsElapsed++;
    const m = Math.floor(sessionSecondsElapsed / 60).toString().padStart(2, '0');
    const s = (sessionSecondsElapsed % 60).toString().padStart(2, '0');
    const timeStr = `${m}:${s}`;
    if (sessionTimerElement) sessionTimerElement.textContent = timeStr;
    const stopBtnTimer = document.getElementById('stop-btn-timer');
    if (stopBtnTimer) stopBtnTimer.textContent = timeStr;
  }, 1000);
}

function stopSessionTimer() {
  if (sessionTimerInterval) {
    clearInterval(sessionTimerInterval);
    sessionTimerInterval = null;
  }
  sessionSecondsElapsed = 0;
  if (sessionTimerElement) sessionTimerElement.style.display = 'none';
  const stopBtnTimer = document.getElementById('stop-btn-timer');
  if (stopBtnTimer) stopBtnTimer.textContent = '00:00';
}

// Start session button event handler
startSessionBtn.addEventListener('click', async () => {
  hasActiveAnswer = false;
  const company = setupCompany.value.trim() || 'Stealth Practice';
  const role = setupRole.value.trim() || 'Software Engineer';
  const jd = setupJd.value.trim();

  // Find the selected resume & JD doc contents from cache
  const selectedResumeId = setupResumeSelect.value;
  const selectedDocId = setupDocSelect.value;

  const resumeObj = backendResumes.find(r => r.id === selectedResumeId);
  const resume = resumeObj ? resumeObj.parsed_content : '';

  const docObj = backendDocs.find(d => d.id === selectedDocId);
  const docText = docObj ? docObj.content : '';

  // Options from step 3
  const preferredModel = document.getElementById('setup-model-select').value;
  const autoAnswer = document.getElementById('setup-auto-answer').checked;
  const saveTranscript = document.getElementById('setup-save-transcript').checked;
  shouldSaveTranscript = saveTranscript;

  startSessionBtn.disabled = true;
  startSessionBtn.textContent = 'Starting Session...';

  // 1. Save L4 context (including model so backend always uses the right one)
  const isPrompt = docObj ? (docObj.document_type === 'prompt' || docObj.document_name.toLowerCase().includes('prompt') || docObj.document_name.toLowerCase().includes('instruction')) : false;
  try {
    await window.electronAPI.saveL4Context({
      resume,
      resume_id: selectedResumeId || '',
      job_description: jd,
      code_context: docText,
      doc_id: selectedDocId || '',
      doc_type: isPrompt ? 'prompt' : 'document',
      company,
      role,
      model: preferredModel
    });
    offlineUserContext = { resume, job_description: jd, code_context: docText, company, role };
  } catch (e) {
    console.error('[Stealth] Failed to save active context:', e.message);
  }

  // 2. Initialize backend session
  try {
    backendUrl = (await window.electronAPI.getBackendUrl()) || '';
    if (backendUrl) {
      const session = await window.electronAPI.createBackendSession(USER_ID, {
        session_name: `${company} (${selectedSessionType})`,
        company_name: company,
        role_name: role,
        language: 'English',
        audio_source: 'browser_tab_audio',
        model: preferredModel,
        auto_answer: autoAnswer,
        save_transcript: saveTranscript
      });
      if (session && session.token) {
        sessionToken = session.token;
        activeSessionId = session.session_id || session.token;
        console.log(`[Backend] Session created: ${activeSessionId}`);
        // Clear any stale localStorage transcript for this session
        safeSetItem('stealth_transcript_buffer', '');
        safeSetItem('stealth_transcript_session', activeSessionId);
      } else {
        console.warn('[Backend] Session creation failed — falling back to offline mode.');
        backendUrl = '';
      }
    }
  } catch (e) {
    console.error('[Backend] Init error — running offline:', e.message);
    backendUrl = '';
  }

  // 3. Switch views and collapse window to toolbar size
  setupView.style.display = 'none';
  toolbarView.style.display = 'flex';
  // Initialize transcript block placeholder state
  if (transcriptBlock) {
    transcriptBlock.textContent = '';
    transcriptBlock.dataset.placeholder = 'true';
  }
  const logoutTextSpan = document.getElementById('settings-logout-text');
  if (logoutTextSpan) logoutTextSpan.textContent = 'Exit';

  // Capture current session data for the Edit Session modal
  liveSessionData = {
    company,
    role,
    jd,
    resumeId: selectedResumeId || '',
    docId: selectedDocId || ''
  };

  // Hide cursor from screen-share capture while in stealth overlay mode
  document.body.classList.add('stealth-active');
  isStealthHoverEnabled = true;
  toggleStealthTooltips(true);
  document.querySelector('.app-container').style.opacity = Math.min(1.0, userOpacity);

  // Load saved bounds if any
  const savedState = await window.electronAPI.getSavedBounds();
  if (savedState) {
    currentWidth = Math.max(WIDTH, savedState.width || WIDTH);
    currentHeight = savedState.height || COLLAPSED_HEIGHT;
    await window.electronAPI.restoreSavedBounds();

    // Restore the active panel state
    const savedActiveTab = safeGetItem('stealth_activeTab') || null;
    if (savedActiveTab === 'ai' || savedActiveTab === 'code') {
      openPanel(savedActiveTab);
    } else {
      closeAllPanels();
    }
  } else {
    // Collapse window to default collapsed state (600x56)
    currentWidth = WIDTH;
    currentHeight = COLLAPSED_HEIGHT;
    pendingProgrammaticResizes++;
    window.electronAPI.resizeWindow(WIDTH, COLLAPSED_HEIGHT, 'top', true);
  }

  // Clear answerBlock and show Intro message
  const chatBlock = document.getElementById('answer-block');
  const dbIntroduction = resumeObj ? resumeObj.introduction : '';
  if (chatBlock) {
    chatBlock.innerHTML = '';
    // Trigger an initial query to absorb the first-request latency and generate a polished self-intro
    setTimeout(() => {
      let introPrompt = "Based on my uploaded resume, can you provide a strong 3-minute self-introduction that I can use for this interview?";
      if (dbIntroduction && dbIntroduction.trim()) {
        introPrompt = `Please polish, improve the grammar, and optimize the flow of this self-introduction from my profile to make it sound highly professional, natural, and perfect for a 3-minute verbal delivery:\n\n"${dbIntroduction.trim()}"`;
      }
      queryAssistant(introPrompt, true);
    }, 500);
  }

  // Start live session ticking timer
  startSessionTimer();

  // Start listening to user automatically
  if (!isRecording) {
    toggleSource('system').catch(err => {
      console.error('[Stealth] Auto-start recording failed:', err);
    });
  }

  startSessionBtn.disabled = false;
  startSessionBtn.textContent = 'Start Session & Collapse';
});


// Stop session button event handler
stopSessionBtn.addEventListener('click', async () => {
  // Expand window back to 600x580 setup state
  pendingProgrammaticResizes++;
  window.electronAPI.resizeWindow(600, 580, 'top', true);

  // Close any active panel/tabs
  if (activeTab) {
    const activePanel = aiPanel;
    activePanel.classList.remove('active');
    panelsContainer.classList.remove('active');
    activeTab = null;
  }

  // Reset session & save ending duration/status to backend (non-blocking)
  if (sessionToken) {
    const tokenToReset = sessionToken;
    const sessionToReset = activeSessionId;
    const seconds = sessionSecondsElapsed;
    const finalTranscript = accumulatedTranscript.trim();
    sessionToken = '';      // clear immediately
    activeSessionId = '';   // clear immediately

    // Fire-and-forget backend updates so they don't block the UI transition
    Promise.resolve()
      .then(async () => {
        // Flush the full accumulated transcript as one final block if we have content
        if (finalTranscript && shouldSaveTranscript && sessionToReset) {
          try {
            await window.electronAPI.saveTranscriptBlock(sessionToReset, {
              speaker: 'full_session',
              content: finalTranscript,
              source: 'session_end_flush'
            });
            console.log('[Transcript] Full session transcript flushed to backend.');
          } catch (e) {
            console.warn('[Transcript] Flush failed — transcript saved in localStorage:', e.message);
          }
        }
        // Clear localStorage buffer after successful flush
        safeSetItem('stealth_transcript_buffer', '');
        safeSetItem('stealth_transcript_session', '');
        return window.electronAPI.updateBackendSession(tokenToReset, {
          status: 'completed',
          duration_seconds: seconds
        });
      })
      .then(() => window.electronAPI.resetSessionMemory(tokenToReset))
      .catch(e => console.error('[Stealth] Failed to complete session on backend:', e.message));
  } else {
    // Even offline, clear localStorage buffer
    safeSetItem('stealth_transcript_buffer', '');
    safeSetItem('stealth_transcript_session', '');
  }

  // Clear live transcript state
  accumulatedTranscript = '';
  lastAnswerOffset = 0;

  // Clear transcript chunk buffer and cancel any pending flush timer
  if (transcriptFlushTimer) {
    clearTimeout(transcriptFlushTimer);
    transcriptFlushTimer = null;
  }
  transcriptChunkBuffer = '';

  // Reset step wizard back to Step 1
  currentStep = 1;
  updateWizardView();

  // Clear answer history
  answerHistory = [];
  currentAnswerIndex = -1;
  renderActiveAnswer();

  // Restore cursor visibility when exiting stealth mode
  isStealthHoverEnabled = false;
  isShrunk = false;  // ensure shrunk state is cleared so click-through isn't triggered
  window.electronAPI.setIgnoreMouseEvents(false);
  document.body.classList.remove('stealth-active');
  toggleStealthTooltips(false);
  document.body.classList.remove('hover-active');
  document.querySelector('.app-container').style.opacity = Math.min(1.0, userOpacity);

  // Stop live session ticking timer
  stopSessionTimer();

  // Switch views
  toolbarView.style.display = 'none';
  setupView.style.display = 'flex';
  const logoutTextSpan = document.getElementById('settings-logout-text');
  if (logoutTextSpan) logoutTextSpan.textContent = 'Logout';
  // Guarantee the window is fully interactive after returning to setup portal
  window.electronAPI.setIgnoreMouseEvents(false);


  // Clear all setup form fields so wizard starts fresh (no stale company/role data)
  setupCompany.value = '';
  setupRole.value = '';
  if (setupJd) setupJd.value = '';

  // Reload dropdown options fresh from backend
  loadDropdowns();
});


// -------------------------------------------------------------
// 1. CLICK-THROUGH HOVER TRACKER
// -------------------------------------------------------------
// We establish click-through on transparent parts by default.
// When moving the mouse, we check if the cursor is above a designated ".interactive" element.
// Resizing state
let isResizingPanel = false;
let isDraggingSlider = false;
let justExpanded = false;
let justExpandedTimeout;
let startWidth, startHeight;
let startPanelWidth;
let startX, startY;
let startMouseX, startMouseY;
let lastResizeTime = 0;

// Track last known cursor position so we can re-evaluate click-through
// even without a fresh mouse move (e.g. after OS native drag or state changes)
let _lastClientX = 0;
let _lastClientY = 0;
let isMouseInsideWindow = false;

/**
 * Central function to evaluate and set the correct click-through state.
 * Call this after ANY state change that could affect interactivity.
 * @param {number} [clientX] - cursor X in client coords (uses last known if omitted)
 * @param {number} [clientY] - cursor Y in client coords (uses last known if omitted)
 */
function updateClickThrough(clientX, clientY) {
  if (isDraggingWindow) return;
  if (justExpanded) {
    window.electronAPI.setIgnoreMouseEvents(false);
    return;
  }

  const x = clientX !== undefined ? clientX : _lastClientX;
  const y = clientY !== undefined ? clientY : _lastClientY;

  // Check if over any explicit drag region using bounding client rect,
  // since elements with -webkit-app-region: drag are ignored by elementFromPoint.
  const dragHandles = [
    document.getElementById('position-btn'),
    document.getElementById('setup-position-btn'),
    document.getElementById('recent-position-btn'),
    document.getElementById('diamond-btn')
  ];

  let isOverDragHandle = false;
  for (const btn of dragHandles) {
    if (btn && btn.style.display !== 'none' && btn.offsetParent !== null) {
      const rect = btn.getBoundingClientRect();
      if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
        isOverDragHandle = true;
        break;
      }
    }
  }

  const el = document.elementFromPoint(x, y);
  const toolbarWrapper = document.querySelector('.toolbar-wrapper');
  let isOverTooltipArea = false;
  if (toolbarWrapper && !activeTab) {
    const rect = toolbarWrapper.getBoundingClientRect();
    // Keep window interactive when cursor is over the tooltips rendering below the toolbar wrapper (extending 85px down)
    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= (rect.bottom + 85)) {
      isOverTooltipArea = true;
    }
  }

  const isInteractive = isOverDragHandle || isOverTooltipArea || (el && (
    el.closest('.interactive') ||
    el.closest('.setup-view-container') ||
    el.closest('#settings-popup') ||
    el.closest('.panels-container') ||
    el.tagName === 'BUTTON' ||
    el.tagName === 'INPUT' ||
    el.tagName === 'TEXTAREA' ||
    el.tagName === 'SELECT'
  ));

  if (isInteractive) {
    window.electronAPI.setIgnoreMouseEvents(false);
  } else {
    // Pass clicks through gaps
    window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
  }

  const appCont = document.querySelector('.app-container');
  if (appCont) {
    if (isDraggingSlider || window._isPreviewingOpacity) {
      appCont.style.opacity = Math.min(1.0, userOpacity);
    } else {
      appCont.style.opacity = isMouseInsideWindow ? '1' : Math.min(1.0, userOpacity);
    }
  }
}

window.addEventListener('mouseleave', () => {
  isMouseInsideWindow = false;
  const appCont = document.querySelector('.app-container');
  if (appCont && !isDraggingSlider && !window._isPreviewingOpacity) {
    appCont.style.opacity = Math.min(1.0, userOpacity);
  }
  updateClickThrough();
  updateWindowSize();
});

window.addEventListener('mouseenter', () => {
  isDraggingWindow = false;
  isMouseInsideWindow = true;
  const appCont = document.querySelector('.app-container');
  if (appCont && !isDraggingSlider && !window._isPreviewingOpacity) {
    appCont.style.opacity = '1';
  }
  updateClickThrough();
  updateWindowSize();
});

window.addEventListener('pointerup', (e) => {
  if (isResizingPanel) {
    isResizingPanel = false;
    document.body.classList.remove('resizing');

    // Save panel width from CSS variable
    const panelWidthStr = document.documentElement.style.getPropertyValue('--panel-width');
    let finalPanelWidth = 620;
    if (panelWidthStr) {
      finalPanelWidth = parseFloat(panelWidthStr);
      safeSetItem('stealth_panelWidth', finalPanelWidth);
    }

    safeSetItem('stealth_panelHeight', currentHeight);

    // Perform final, unthrottled resize to align the window boundaries exactly
    const finalWinWidth = Math.max(WIDTH, finalPanelWidth + 20);
    // Lock X to startX — toolbar never shifts
    pendingProgrammaticResizes++;
    window.electronAPI.resizeWindow(finalWinWidth, currentHeight, toolbarPosition, false, startX, startY);

    // Release pointer capture
    const expandAnswerBtn = document.getElementById('expand-answer-btn');
    if (expandAnswerBtn) {
      try { expandAnswerBtn.releasePointerCapture(e.pointerId); } catch (err) { }
    }

    // Re-evaluate click-through after resize ends so buttons don't freeze
    requestAnimationFrame(() => updateClickThrough());
  }
  isDraggingWindow = false;
  isDraggingSlider = false;

  // After any pointer release, re-evaluate click-through using last known position
  // This fixes the case where OS-level drag ends and pointermove never fires
  requestAnimationFrame(() => updateClickThrough());
});

window.addEventListener('pointercancel', () => {
  isResizingPanel = false;
  isDraggingWindow = false;
  isDraggingSlider = false;
  document.body.classList.remove('resizing');
  updateClickThrough();
});

window.addEventListener('pointermove', (e) => {
  // Always track cursor position for state-change re-evaluations
  _lastClientX = e.clientX;
  _lastClientY = e.clientY;

  // Handle panel resizing — apply double arrow cursor strictly while actively expanding
  if (isResizingPanel) {
    document.body.classList.add('resizing');
    const dx = e.screenX - startMouseX;
    const dy = e.screenY - startMouseY;

    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      isDragClick = true;
    }

    // Scale delta direction depending on where the toolbar is docked
    let dxSigned = dx;
    let dySigned = dy;
    if (toolbarPosition === 'bottom') {
      dySigned = -dy;
    }
    if (toolbarPosition === 'right') {
      dxSigned = -dx;
    }

    // Resize only height during drag; panel width grows to the right from startX (toolbar stays static)
    const screenWidth = window.screen.availWidth || 1920;
    const screenHeight = window.screen.availHeight || 1080;
    const maxWinHeight = Math.min(MAX_HEIGHT, screenHeight - 40);
    const newPanelWidth = Math.max(300, Math.min(screenWidth - 40, startPanelWidth + dxSigned));
    const newHeight = Math.max(300, Math.min(maxWinHeight, startHeight + dySigned));

    // Update the panel width CSS variable dynamically
    document.documentElement.style.setProperty('--panel-width', newPanelWidth + 'px');
    currentHeight = newHeight;

    const panelsContainer = document.getElementById('panels');
    if (panelsContainer) {
      panelsContainer.style.height = (newHeight - COLLAPSED_HEIGHT - 12) + 'px';
      panelsContainer.style.maxHeight = 'none';
    }

    const answerBlock = document.getElementById('answer-block');
    if (answerBlock) {
      const headerHeight = 45;
      const navHeight = 35;
      const inputHeight = 55;
      const computedAnswerHeight = newHeight - COLLAPSED_HEIGHT - 12 - headerHeight - navHeight - inputHeight;
      answerBlock.style.height = Math.max(100, computedAnswerHeight) + 'px';
      answerBlock.style.maxHeight = 'none';
    }

    // Throttle the native OS window resizing IPC calls to prevent DWM thread blocks
    const now = Date.now();
    if (now - lastResizeTime > 30) {
      lastResizeTime = now;
      const newWinWidth = Math.max(WIDTH, newPanelWidth + 20);
      // Lock X to startX — toolbar position never shifts during resize
      pendingProgrammaticResizes++;
      window.electronAPI.resizeWindow(newWinWidth, newHeight, toolbarPosition, false, startX, startY);
    }
    return;
  }

  updateClickThrough(e.clientX, e.clientY);
});

// Initialize with click-through disabled at startup
window.electronAPI.setIgnoreMouseEvents(false);

// -------------------------------------------------------------
// 2. WINDOW CONTROL ACTIONS
// -------------------------------------------------------------




if (closeBtn) {
  closeBtn.addEventListener('click', () => {
    window.electronAPI.closeApp();
  });
}

// Listen for global Escape key to close open panels
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (activeTab) {
      closeAllPanels();
    }
  }
});

// -------------------------------------------------------------
// 3. TAB / PANEL TOGGLING & RESIZING
// -------------------------------------------------------------
function updateWindowSize(reposition = false) {
  // NEVER resize while user is dragging
  if (isDraggingWindow || isResizingPanel) return;

  // If minimized (shrunk), do not let updateWindowSize override the small 48x48 bounds
  if (isShrunk) return;

  // If in setup wizard view, do not resize the window (keep 600x580 bounds)
  if (setupView && setupView.style.display !== 'none') {
    return;
  }

  // Detect and update the toolbar position dynamically based on window location
  updateDynamicToolbarPosition();

  if (!activeTab) {
    const settingsPopupEl = document.getElementById('settings-popup');
    const settingsOpen = settingsPopupEl && settingsPopupEl.style.display === 'flex';
    const editModalEl = document.getElementById('edit-session-modal');
    const editModalOpen = editModalEl && editModalEl.style.display === 'flex';

    let targetHeight = 120;
    if (settingsOpen || editModalOpen) {
      targetHeight = 480;
    } else {
      targetHeight = 120; // 120px allows hover tooltips below toolbar to render 100% complete without clipping
    }
    pendingProgrammaticResizes++;
    window.electronAPI.resizeWindow(WIDTH, targetHeight, toolbarPosition, reposition);
  } else {
    const answerBlock = document.getElementById('answer-block');
    const codeDisplayPre = document.getElementById('code-display');

    const screenHeight = window.screen.availHeight || 1080;
    const maxWinHeight = Math.min(MAX_HEIGHT, screenHeight - 40);
    const maxAnswerHeight = Math.max(150, screenHeight - 250);

    // ACTUAL LAYOUT HEIGHTS (measured from DOM/CSS):
    // panels margin-top=56 + panelHeader(pad12x2+content20)=44 + panelContent(pad16top)=16
    // + transcriptRow(label18+gap4+input38)=60 + gap10 + answerNavRow=28 + answer-block + pad16bot=16 + inputArea(pad12x2+input32)=56
    // Fixed overhead WITH transcript = 56+44+16+60+10+28+16+56 = 286px
    // Fixed overhead WITHOUT transcript = 286 - 60 - 10 = 216px
    // So: compact(transcript visible) answer = 390-286 = 104px → use 100px
    //     expanded(transcript hidden) answer = 390-216 = 174px → use 170px
    const COMPACT_ANSWER_H = 100;
    const EXPANDED_ANSWER_H = 170;

    const transcriptSection = document.querySelector('.block-container:first-child');

    if (window.isCustomResized) {
      // During drag: keep transcript hidden in expanded mode
      if (transcriptSection) transcriptSection.style.display = isAnswerExpanded ? 'none' : '';
      const overheadH = isAnswerExpanded ? 216 : 286;
      const computedAnswerHeight = Math.min(EXPANDED_ANSWER_H, Math.max(60, currentHeight - overheadH));
      if (answerBlock) {
        answerBlock.style.height = computedAnswerHeight + 'px';
        answerBlock.style.maxHeight = computedAnswerHeight + 'px';
        answerBlock.style.overflowY = 'auto';
      }
      if (codeDisplayPre) {
        codeDisplayPre.style.height = computedAnswerHeight + 'px';
        codeDisplayPre.style.maxHeight = computedAnswerHeight + 'px';
        codeDisplayPre.style.overflowY = 'auto';
      }
      const panelsContainer = document.getElementById('panels');
      if (panelsContainer) {
        panelsContainer.style.height = 'auto';
        panelsContainer.style.maxHeight = 'none';
      }
      if (copyAllAnswerBtn) copyAllAnswerBtn.style.display = 'inline-block';
    } else {
      // Reset all drag-imposed styles so CSS defaults take over
      const panelsContainer = document.getElementById('panels');
      if (panelsContainer) {
        panelsContainer.style.height = 'auto';
        panelsContainer.style.maxHeight = 'none';
        panelsContainer.style.width = '';        // reset to CSS var(--panel-width)
        panelsContainer.style.maxWidth = '';     // reset to CSS max-width: 100%
        panelsContainer.style.overflow = '';     // reset to CSS overflow: visible
      }

      if (isAnswerExpanded) {
        // Hide transcript section to give answer block more vertical space
        if (transcriptSection) transcriptSection.style.display = 'none';
        if (answerBlock) {
          answerBlock.style.height = EXPANDED_ANSWER_H + 'px';
          answerBlock.style.maxHeight = EXPANDED_ANSWER_H + 'px';
          answerBlock.style.overflowY = 'auto';
        }
        if (codeDisplayPre) {
          codeDisplayPre.style.height = EXPANDED_ANSWER_H + 'px';
          codeDisplayPre.style.maxHeight = EXPANDED_ANSWER_H + 'px';
          codeDisplayPre.style.overflowY = 'auto';
        }
        if (copyAllAnswerBtn) copyAllAnswerBtn.style.display = 'inline-block';
      } else {
        // Show transcript section in compact mode
        if (transcriptSection) transcriptSection.style.display = '';
        if (answerBlock) {
          answerBlock.style.height = COMPACT_ANSWER_H + 'px';
          answerBlock.style.maxHeight = COMPACT_ANSWER_H + 'px';
          answerBlock.style.overflowY = 'auto';
        }
        if (codeDisplayPre) {
          codeDisplayPre.style.height = COMPACT_ANSWER_H + 'px';
          codeDisplayPre.style.maxHeight = COMPACT_ANSWER_H + 'px';
          codeDisplayPre.style.overflowY = 'auto';
        }
        if (copyAllAnswerBtn) copyAllAnswerBtn.style.display = 'inline-block';
      }
    }

    setTimeout(() => {
      const appContainer = document.querySelector('.app-container');
      const rect = appContainer ? appContainer.getBoundingClientRect() : { height: 0 };
      const settingsPopupEl = document.getElementById('settings-popup');
      const settingsOpen = settingsPopupEl && settingsPopupEl.style.display === 'flex';
      const settingsBuffer = settingsOpen ? 180 : 0;

      // HEIGHT: when custom resized, trust currentHeight (drag handler already clamped to screen)
      let targetHeight;
      if (window.isCustomResized) {
        targetHeight = currentHeight; // set by drag handler, already screen-clamped
      } else {
        const contentHeight = appContainer ? appContainer.scrollHeight : Math.round(rect.height);
        const screenH = window.screen.availHeight;
        targetHeight = Math.min(screenH - 40, contentHeight + 12 + settingsBuffer);
      }

      // WIDTH: when custom resized use currentWidth, else use saved/default panel width
      let targetWinWidth;
      if (window.isCustomResized) {
        targetWinWidth = currentWidth || WIDTH;
      } else if (isAnswerExpanded) {
        const savedExpWidth = parseFloat(safeGetItem('stealth_expandedPanelWidth') || '780');
        targetWinWidth = Math.max(780, savedExpWidth);
      } else {
        const currentPanelWidth = parseFloat(safeGetItem('stealth_panelWidth') || '620');
        targetWinWidth = Math.max(WIDTH, currentPanelWidth + 20);
      }

      pendingProgrammaticResizes++;
      window.electronAPI.resizeWindow(targetWinWidth, targetHeight, toolbarPosition, reposition);
    }, 30);
  }
}

function closeAllPanels() {
  activeTab = null;
  hasActiveAnswer = false;
  isAnswerExpanded = false;

  // Deactivate buttons
  aiBtn.classList.remove('active');
  captureBtn.classList.remove('active');

  const expandAnswerBtn = document.getElementById('expand-answer-btn');
  if (expandAnswerBtn) {
    expandAnswerBtn.classList.remove('active');
    expandAnswerBtn.style.color = 'var(--text-secondary)';
    expandAnswerBtn.style.borderColor = 'rgba(255, 255, 255, 0.08)';
    expandAnswerBtn.style.background = 'rgba(255, 255, 255, 0.04)';
  }

  // Hide panel content
  panelsContainer.classList.remove('active');
  aiPanel.classList.remove('active');

  // Reset heights
  const answerBlock = document.getElementById('answer-block');
  const codeDisplayPre = document.getElementById('code-display');
  if (answerBlock) answerBlock.style.height = '110px';
  if (codeDisplayPre) codeDisplayPre.style.height = '310px';

  // Save to localStorage
  safeSetItem('stealth_activeTab', '');

  // Resize window
  updateWindowSize();
}

function openPanel(tabName) {
  activeTab = tabName;

  // Deactivate all buttons
  aiBtn.classList.toggle('active', tabName === 'ai');
  captureBtn.classList.toggle('active', tabName === 'code');

  // Show target panel content
  panelsContainer.classList.add('active');
  aiPanel.classList.toggle('active', tabName === 'ai');

  // Save to localStorage
  safeSetItem('stealth_activeTab', tabName);

  // Resize window
  updateWindowSize();
}

function toggleTab(tabName) {
  if (activeTab === tabName) {
    closeAllPanels();
  } else {
    openPanel(tabName);
  }
}

aiBtn.addEventListener('click', () => toggleTab('ai'));


// ── Drag-to-Move: hold position-btn and drag to move the window ──────────────
// Using pointer capture to drag the window smoothly via IPC delta movements
const dragButtons = [
  document.getElementById('setup-position-btn'),
  document.getElementById('recent-position-btn'),
  document.getElementById('position-btn')
];
dragButtons.forEach(btn => {
  if (btn) {
    btn.addEventListener('pointerdown', () => {
      isDraggingWindow = true;
      const appCont = document.querySelector('.app-container');
      if (appCont) appCont.style.opacity = Math.min(1.0, userOpacity);
    });
  }
});

// -------------------------------------------------------------
// 4. MOCK AI AND UTILITY LOGIC
// -------------------------------------------------------------

// -- AI HELP PANEL REAL-TIME SPEECH & ANSWERING --
const recordBtn = document.getElementById('record-btn');
const recordText = document.getElementById('record-text');
const recordDot = recordBtn.querySelector('.record-dot');
const transcriptBlock = document.getElementById('transcript-block');
const clearTranscriptBtn = document.getElementById('clear-transcript-btn');
const copyTranscriptBtn = document.getElementById('copy-transcript-btn');
const answerBlock = document.getElementById('answer-block');
const aiAnswerBtn = document.getElementById('ai-answer-btn');
const copyAnswerBtn = document.getElementById('copy-answer-btn');
const aiInput = document.getElementById('ai-input');
const aiSend = document.getElementById('ai-send');

let mediaRecorder = null;
let dgSocket = null;
let isRecording = false;
let isRecordingSystem = false;
let isRecordingMic = false;
let accumulatedTranscript = '';
let activeMicStream = null;
let activeSystemStream = null;
let micSourceNode = null;
let systemSourceNode = null;
let audioCtx = null;
let audioDestNode = null;

// ── Transcript Buffering ─────────────────────────────────────────────────────
// Accumulate is_final chunks and only flush to the backend as a meaningful block
// after a silence gap (TRANSCRIPT_FLUSH_SILENCE_MS) or when enough words pile up.
const TRANSCRIPT_FLUSH_SILENCE_MS = 8000; // 8 s of no new speech → flush
const TRANSCRIPT_FLUSH_WORD_THRESHOLD = 80; // flush early if buffer hits this many words
let transcriptChunkBuffer = '';           // pending text not yet saved to backend
let transcriptFlushTimer = null;          // setTimeout handle for silence-based flush

// Flush the current buffer to the backend as one coherent block, then reset.
function flushTranscriptBuffer() {
  if (transcriptFlushTimer) {
    clearTimeout(transcriptFlushTimer);
    transcriptFlushTimer = null;
  }
  const content = transcriptChunkBuffer.trim();
  transcriptChunkBuffer = '';
  if (!content || !sessionToken || !shouldSaveTranscript) return;
  window.electronAPI.saveTranscriptBlock(sessionToken, {
    speaker: 'system',
    content: content,
    source: 'mixed_audio'
  }).catch(e => console.warn('[Save Transcript] Buffered backend save failed:', e.message));
  console.log(`[Transcript Buffer] Flushed ${content.split(/\s+/).length} words to backend.`);
}

// New Mic Button elements
const micBtnAi = document.getElementById('mic-btn-ai');
const micText = document.getElementById('mic-text');

// Toggle recording state for system capture
async function toggleRecording() {
  await toggleSource('system');
}

// Toggle recording state for mic capture
async function toggleMicRecording() {
  await toggleSource('mic');
}

// Toggle audio capture for a specific source ('system' or 'mic')
async function toggleSource(source) {
  const isCurrentlyRecordingAny = isRecordingSystem || isRecordingMic;

  if (source === 'system') {
    isRecordingSystem = !isRecordingSystem;
  } else if (source === 'mic') {
    isRecordingMic = !isRecordingMic;
  }

  isRecording = isRecordingSystem || isRecordingMic;

  // Case 1: Both are now inactive → Stop recording completely
  if (!isRecording) {
    stopRecording();
    return;
  }

  // Case 2: First source starts → Initialize Speech-to-Text Socket and Audio Context
  if (!isCurrentlyRecordingAny) {
    try {
      // Check if this is a live interview session
      const isLiveSession = (selectedSessionType === 'Interview+Coding');

      // Initialize Web Audio API nodes
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioDestNode = audioCtx.createMediaStreamDestination();

      // Direct Deepgram WebSocket — no backend hop for minimum latency during live sessions
      const dgKey = await window.electronAPI.getDeepgramKey();
      if (!dgKey || dgKey.startsWith('your_')) {
        showInlineError('Deepgram API key is missing — define DEEPGRAM_API_KEY in your .env file.', answerBlock);
        resetRecordButton();
        isRecordingSystem = false;
        isRecordingMic = false;
        isRecording = false;
        return;
      }
      dgSocket = new WebSocket('wss://api.deepgram.com/v1/listen?model=nova-3&interim_results=true&smart_format=true&endpointing=100', ['token', dgKey]);
      console.log('[Stealth] Connecting direct to Deepgram for live session...');

      dgSocket.onopen = async () => {
        console.log('[Deepgram Live] Connected successfully.');
        if (audioCtx && audioCtx.state === 'suspended') {
          try { await audioCtx.resume(); } catch (e) { }
        }

        recordBtn.style.pointerEvents = 'auto';
        if (micBtnAi) micBtnAi.style.pointerEvents = 'auto';

        const existingText = transcriptBlock.textContent.trim();
        if (!existingText || transcriptBlock.dataset.placeholder === 'true') {
          transcriptBlock.textContent = '';
          transcriptBlock.dataset.placeholder = 'false';
        } else {
          // User has typed something manually — keep it
          accumulatedTranscript = existingText;
        }

        // Initialize MediaRecorder from mixed destination node
        mediaRecorder = new MediaRecorder(audioDestNode.stream, { mimeType: 'audio/webm' });

        mediaRecorder.ondataavailable = (event) => {
          if (event.data.size > 0 && dgSocket && dgSocket.readyState === WebSocket.OPEN) {
            dgSocket.send(event.data);
          }
        };

        // Stream audio slices in 250ms intervals
        mediaRecorder.start(250);
      };

      dgSocket.onmessage = (message) => {
        try {
          const data = JSON.parse(message.data);
          const transcript = data.channel?.alternatives?.[0]?.transcript;
          if (transcript && transcript.trim()) {
            const cleanTranscript = transcript.trim();
            if (data.is_final) {
              // Confirmed text: update accumulatedTranscript and show in white
              accumulatedTranscript += (accumulatedTranscript ? ' ' : '') + cleanTranscript;
              transcriptBlock.textContent = accumulatedTranscript;
              transcriptBlock.dataset.placeholder = 'false';

              // Save transcript block to backend and localStorage
              if (shouldSaveTranscript) {
                const existingBuffer = safeGetItem('stealth_transcript_buffer') || '';
                const updatedBuffer = existingBuffer ? existingBuffer + ' ' + cleanTranscript : cleanTranscript;
                safeSetItem('stealth_transcript_buffer', updatedBuffer);

                if (sessionToken) {
                  transcriptChunkBuffer += (transcriptChunkBuffer ? ' ' : '') + cleanTranscript;
                  if (transcriptFlushTimer) clearTimeout(transcriptFlushTimer);
                  transcriptFlushTimer = setTimeout(flushTranscriptBuffer, TRANSCRIPT_FLUSH_SILENCE_MS);
                  const wordCount = transcriptChunkBuffer.split(/\s+/).filter(Boolean).length;
                  if (wordCount >= TRANSCRIPT_FLUSH_WORD_THRESHOLD) flushTranscriptBuffer();
                }
              }

              // --- AUTO ANSWER LOGIC ---
              const autoAnswerCheckbox = document.getElementById('setup-auto-answer');
              const autoAnswerActive = autoAnswerCheckbox ? autoAnswerCheckbox.checked : false;

              if (autoAnswerActive && !answerBlock.classList.contains('loading')) {
                const score = questionScore(cleanTranscript);
                if (score > 0) {
                  console.log(`[Auto-Answer] Question detected: "${cleanTranscript}". Querying AI...`);
                  if (autoAnswerTimeoutId) { clearTimeout(autoAnswerTimeoutId); autoAnswerTimeoutId = null; }
                  queryAssistant(null, false);
                } else {
                  if (autoAnswerTimeoutId) clearTimeout(autoAnswerTimeoutId);
                  autoAnswerTimeoutId = setTimeout(() => {
                    if (!answerBlock.classList.contains('loading')) {
                      console.log('[Auto-Answer] 1-second gap detected. Querying AI...');
                      queryAssistant(null, false);
                    }
                    autoAnswerTimeoutId = null;
                  }, 1000);
                }
              }
            } else {
              // Interim text: gray preview — does NOT update accumulatedTranscript
              transcriptBlock.innerHTML = accumulatedTranscript + (accumulatedTranscript ? ' ' : '') + `<span style="color: var(--text-muted); font-style: italic;">${cleanTranscript}</span>`;
            }
            transcriptBlock.scrollLeft = transcriptBlock.scrollWidth;
          }
        } catch (e) {
          console.error('[Deepgram Live] Error parsing message:', e);
        }
      };

      dgSocket.onerror = (err) => {
        console.error('[Deepgram Live] Connection error:', err);
        stopRecording();
      };

      dgSocket.onclose = () => {
        console.log('[Deepgram Live] Connection closed.');
        if (isRecordingSystem || isRecordingMic) stopRecording();
      };


    } catch (err) {
      console.error('[Audio Capture] Failed to initialize:', err);
      showInlineError(`Audio capture error: ${err.message}`, answerBlock);
      resetRecordButton();
      isRecordingSystem = false;
      isRecordingMic = false;
      isRecording = false;
      return;
    }
  }

  // Handle stream creation/destruction and UI updates for the active source
  if (source === 'system') {
    if (isRecordingSystem) {
      // Start system loopback
      recordText.textContent = 'Connecting...';
      recordBtn.style.pointerEvents = 'none';
      try {
        console.log('[Audio Capture] Attempting loopback screen-audio capture...');
        const sources = await window.electronAPI.getDesktopSources();
        const screenSource = sources.find(s => s.id.startsWith('screen'));
        if (screenSource) {
          activeSystemStream = await navigator.mediaDevices.getUserMedia({
            audio: {
              mandatory: {
                chromeMediaSource: 'desktop',
                chromeMediaSourceId: screenSource.id
              }
            },
            video: {
              mandatory: {
                chromeMediaSource: 'desktop',
                chromeMediaSourceId: screenSource.id
              }
            }
          });
          // Keep video tracks alive to prevent Chromium/Electron from closing the stream
          console.log('[Audio Capture] Acquired system loopback tracks:', activeSystemStream.getTracks().map(t => `${t.kind}: state=${t.readyState}, enabled=${t.enabled}`));

          systemSourceNode = audioCtx.createMediaStreamSource(activeSystemStream);
          systemSourceNode.connect(audioDestNode);
          if (audioCtx && audioCtx.state === 'suspended') { try { await audioCtx.resume(); } catch (e) { } }

          recordBtn.style.pointerEvents = 'auto';
          recordBtn.classList.add('recording');
          recordDot.classList.add('recording');
          recordText.textContent = 'Listening';
          console.log('[Audio Capture] Successfully acquired system loopback stream.');
        } else {
          console.warn('[Audio Capture] No screen source found for loopback capture.');
          isRecordingSystem = false;
          recordBtn.style.pointerEvents = 'auto';
          recordText.textContent = 'Speaker';
        }
      } catch (sysErr) {
        console.warn('[Audio Capture] Loopback capture failed:', sysErr.message);
        isRecordingSystem = false;
        recordBtn.style.pointerEvents = 'auto';
        recordText.textContent = 'Speaker';
      }
    } else {
      // Stop system loopback
      if (systemSourceNode) {
        try { systemSourceNode.disconnect(); } catch (e) { }
        systemSourceNode = null;
      }
      if (activeSystemStream) {
        activeSystemStream.getTracks().forEach(track => track.stop());
        activeSystemStream = null;
      }
      recordBtn.classList.remove('recording');
      recordDot.classList.remove('recording');
      recordText.textContent = 'Speaker';
    }
  } else if (source === 'mic') {
    if (isRecordingMic) {
      // Start microphone
      if (micText) micText.textContent = 'Connecting...';
      if (micBtnAi) micBtnAi.style.pointerEvents = 'none';
      try {
        console.log('[Audio Capture] Attempting hardware microphone capture...');
        activeMicStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        micSourceNode = audioCtx.createMediaStreamSource(activeMicStream);
        micSourceNode.connect(audioDestNode);
        if (audioCtx && audioCtx.state === 'suspended') { try { await audioCtx.resume(); } catch (e) { } }

        if (micBtnAi) {
          micBtnAi.style.pointerEvents = 'auto';
          micBtnAi.classList.add('recording');
          micText.textContent = 'Mic Active';
          micBtnAi.style.background = 'rgba(255, 255, 255, 0.18)';
          micBtnAi.style.borderColor = 'rgba(255, 255, 255, 0.5)';
          micBtnAi.style.color = '#ffffff';
        }
        console.log('[Audio Capture] Successfully acquired microphone stream.');
      } catch (micErr) {
        console.warn('[Audio Capture] Microphone capture failed:', micErr.message);
        isRecordingMic = false;
        if (micBtnAi) {
          micBtnAi.style.pointerEvents = 'auto';
          micText.textContent = 'Mic';
        }
      }
    } else {
      // Stop microphone
      if (micSourceNode) {
        try { micSourceNode.disconnect(); } catch (e) { }
        micSourceNode = null;
      }
      if (activeMicStream) {
        activeMicStream.getTracks().forEach(track => track.stop());
        activeMicStream = null;
      }
      if (micBtnAi) {
        micBtnAi.classList.remove('recording');
        micText.textContent = 'Mic';
        micBtnAi.style.background = 'rgba(255, 255, 255, 0.08)';
        micBtnAi.style.borderColor = 'rgba(255, 255, 255, 0.22)';
        micBtnAi.style.color = '#ffffff';
      }
    }
  }

  isRecording = isRecordingSystem || isRecordingMic;
}

function stopRecording() {
  isRecordingSystem = false;
  isRecordingMic = false;
  isRecording = false;

  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try {
      mediaRecorder.stop();
    } catch (e) { }
  }
  mediaRecorder = null;

  if (dgSocket) {
    try {
      dgSocket.close();
    } catch (e) { }
  }
  dgSocket = null;

  if (systemSourceNode) {
    try { systemSourceNode.disconnect(); } catch (e) { }
    systemSourceNode = null;
  }
  if (activeSystemStream) {
    activeSystemStream.getTracks().forEach(track => track.stop());
    activeSystemStream = null;
  }

  if (micSourceNode) {
    try { micSourceNode.disconnect(); } catch (e) { }
    micSourceNode = null;
  }
  if (activeMicStream) {
    activeMicStream.getTracks().forEach(track => track.stop());
    activeMicStream = null;
  }

  if (audioCtx) {
    audioCtx.close().catch(e => { });
    audioCtx = null;
  }
  audioDestNode = null;

  resetRecordButton();
  console.log('[Audio Capture] Capture stopped.');
}

function resetRecordButton() {
  isRecording = false;
  recordingSource = null;

  recordBtn.style.pointerEvents = 'auto';
  recordBtn.classList.remove('recording');
  recordDot.classList.remove('recording');
  recordText.textContent = 'Speaker';

  if (micBtnAi) {
    micBtnAi.style.pointerEvents = 'auto';
    micBtnAi.classList.remove('recording');
    micText.textContent = 'Mic';
    micBtnAi.style.background = 'rgba(255, 255, 255, 0.08)';
    micBtnAi.style.borderColor = 'rgba(255, 255, 255, 0.22)';
    micBtnAi.style.color = '#ffffff';
  }
}

// Clear Transcript
clearTranscriptBtn.addEventListener('click', () => {
  accumulatedTranscript = '';
  lastAnswerOffset = 0;
  transcriptBlock.textContent = '';
  transcriptBlock.dataset.placeholder = 'true';
});

// Sync user-typed/pasted text in the contenteditable transcript box → accumulatedTranscript
transcriptBlock.addEventListener('input', () => {
  const typed = transcriptBlock.textContent.trim();
  accumulatedTranscript = typed;
  transcriptBlock.dataset.placeholder = typed ? 'false' : 'true';
});

// Prevent newlines/formatting on paste — keep as plain text
transcriptBlock.addEventListener('paste', (e) => {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData('text/plain');
  document.execCommand('insertText', false, text);
});

// Copy Transcript
copyTranscriptBtn.addEventListener('click', () => {
  const text = accumulatedTranscript.trim() || transcriptBlock.textContent.trim();
  if (text) {
    navigator.clipboard.writeText(text);
    copyTranscriptBtn.textContent = 'Copied!';
    setTimeout(() => { copyTranscriptBtn.textContent = 'Copy'; }, 1500);
  }
});

// Copy Answer (header button — copies current visible text)
copyAnswerBtn.addEventListener('click', () => {
  const text = answerBlock.textContent.trim();
  if (text && !text.startsWith('Click the "Answer" button')) {
    navigator.clipboard.writeText(text);
    copyAnswerBtn.textContent = 'Copied!';
    setTimeout(() => { copyAnswerBtn.textContent = 'Copy'; }, 1500);
  }
});

// Copy All Answer button (sticky inside answer block)
if (copyAllAnswerBtn) {
  copyAllAnswerBtn.addEventListener('click', () => {
    const text = answerBlock.textContent.trim();
    if (text) {
      navigator.clipboard.writeText(text);
      copyAllAnswerBtn.textContent = '✓ Copied!';
      setTimeout(() => { copyAllAnswerBtn.textContent = '⎘ Copy All'; }, 1500);
    }
  });
}

// Call AI — routes through backend (4-layer memory) or offline Gemini (local 4-layer memory)
// Call AI — routes through backend (4-layer memory) or offline Gemini (local 4-layer memory)
async function queryAssistant(manualQuestionText, isManual = false) {
  const _ttftStart = Date.now();
  answerBlock.classList.add('loading');
  updateWindowSize();

  // Always sync latest transcript text from DOM (including gray interim text) for automatic queries
  if (!isManual && transcriptBlock) {
    const currentDOMText = transcriptBlock.textContent.trim();
    if (currentDOMText && transcriptBlock.dataset.placeholder !== 'true') {
      accumulatedTranscript = currentDOMText;
    }
  }

  try {
    let answer;
    let currentQuestion = '';

    if (backendUrl && sessionToken) {
      // Resolve the current question on the frontend to display it at the top
      if (isManual) {
        currentQuestion = manualQuestionText;
      } else {
        try {
          const detected = detectLatestQuestion(accumulatedTranscript, lastAnswerOffset);
          currentQuestion = detected.question || '';
        } catch (e) {
          currentQuestion = '';
        }
      }

      // Add new entry to history
      const newEntry = { question: currentQuestion, answer: '', totalTimeSec: '' };
      answerHistory.push(newEntry);
      currentAnswerIndex = answerHistory.length - 1;
      updateAnswerNav();

      // ── BACKEND STREAMING MODE: text appears word-by-word in real-time ──────
      const payload = {
        full_transcript: accumulatedTranscript,
        manual_question: isManual ? manualQuestionText : null,
        last_offset: lastAnswerOffset,
        token: sessionToken,
      };

      // Clean up any stale listeners from a previous call
      window.electronAPI.removeAnswerStreamListeners();

      renderAnswerToDOM(answerBlock, 'Stealth AI is thinking...', currentQuestion);
      let rawBuffer = '';
      let answerStarted = false;
      let answerDone = false;
      let ttftMs = null;

      hasActiveAnswer = true;
      answerBlock.classList.remove('loading');

      await new Promise((resolve) => {
        // First chunk received — measure TTFT
        window.electronAPI.onAnswerStreamFirst((data) => {
          ttftMs = data.ttft_ms;
          console.log(`[Stream TTFT] First token in ${ttftMs}ms`);
        });

        let isJsonStream = null;

        // Each raw chunk from the HTTP stream
        window.electronAPI.onAnswerChunk((data) => {
          rawBuffer += data.chunk;

          // Detect stream format on the first non-empty chunk
          if (isJsonStream === null) {
            const trimmed = rawBuffer.trim();
            if (trimmed.length > 0) {
              isJsonStream = trimmed.startsWith('{');
            }
          }

          if (isJsonStream === false) {
            // Plain text stream
            newEntry.answer = rawBuffer;
            renderAnswerToDOM(answerBlock, rawBuffer, currentQuestion);
            answerBlock.scrollTop = 0;
            updateWindowSize();
          } else if (isJsonStream === true) {
            // JSON stream extraction
            if (!answerDone) {
              if (!answerStarted) {
                const answerKeyIdx = rawBuffer.indexOf('"answer"');
                if (answerKeyIdx !== -1) {
                  const colonIdx = rawBuffer.indexOf(':', answerKeyIdx);
                  if (colonIdx !== -1) {
                    let valStart = colonIdx + 1;
                    while (valStart < rawBuffer.length && rawBuffer[valStart] === ' ') valStart++;
                    if (rawBuffer[valStart] === '"') {
                      answerStarted = true;
                      rawBuffer = rawBuffer.slice(valStart + 1);
                    }
                  }
                }
              }

              if (answerStarted) {
                let displayText = rawBuffer
                  .replace(/\\n/g, '\n')
                  .replace(/\\t/g, '\t')
                  .replace(/\\"/g, '"')
                  .replace(/\\\\/g, '\\');

                displayText = displayText.replace(/"\s*\}?\s*$/, '');
                newEntry.answer = displayText;
                renderAnswerToDOM(answerBlock, displayText, currentQuestion);
                answerBlock.scrollTop = 0;
                updateWindowSize();
              }
            }
          }
        });

        // Stream finished
        window.electronAPI.onAnswerStreamEnd((data) => {
          if (data.new_offset != null) lastAnswerOffset = data.new_offset;
          answerDone = true;

          // Show TTFT badge
          const _ttftMs = ttftMs || (Date.now() - _ttftStart);
          const _ttftSec = (_ttftMs / 1000).toFixed(1);
          newEntry.totalTimeSec = `first token ${_ttftSec}s · total ${(data.total_ms / 1000).toFixed(1)}s`;

          renderActiveAnswer();

          window.electronAPI.removeAnswerStreamListeners();
          resolve();
        });

        // Stream error
        window.electronAPI.onAnswerStreamError((data) => {
          window.electronAPI.removeAnswerStreamListeners();
          resolve();
          throw new Error(data.error || 'Stream error');
        });

        // Kick off the stream
        window.electronAPI.queryBackendStream(payload).then((res) => {
          if (res && res.error) {
            window.electronAPI.removeAnswerStreamListeners();
            answerBlock.innerHTML = `<span style="color:var(--accent-danger);">Error: ${res.error}</span>`;
            resolve();
          }
        }).catch((e) => {
          window.electronAPI.removeAnswerStreamListeners();
          resolve();
        });
      });

      return;

    } else {
      // ── OFFLINE MODE: local 4-layer memory system ─────────────────────────
      const startTime = Date.now();
      let latestQuestion;
      let newOffset = accumulatedTranscript.length;

      if (isManual && manualQuestionText && manualQuestionText.trim().length > 0) {
        latestQuestion = manualQuestionText.trim();
      } else {
        const detected = detectLatestQuestion(accumulatedTranscript, lastAnswerOffset);
        latestQuestion = detected.question;
        newOffset = detected.newOffset;

        if (!latestQuestion) {
          throw new Error('No new question detected in transcript since last answer.');
        }
      }

      currentQuestion = latestQuestion;

      // Add to history
      const newEntry = { question: currentQuestion, answer: '', totalTimeSec: '' };
      answerHistory.push(newEntry);
      currentAnswerIndex = answerHistory.length - 1;
      updateAnswerNav();

      // Load user context (L4), recent Q&A (L2), and rolling summary (L3) from local memory
      const recentQA = offlineRecentQA;
      const rollingSummary = offlineRollingSummary;
      const userContext = offlineUserContext;

      // Get 60-90s sliding window of transcript
      const transcriptWindow = getTranscriptWindow(accumulatedTranscript, 250);

      // Build compact prompt
      renderAnswerToDOM(answerBlock, 'Stealth AI is thinking...', latestQuestion);
      const prompt = buildPrompt({
        latestQuestion,
        recentQA,
        rollingSummary,
        userContext,
        transcriptWindow,
      });

      // Call local Gemini API
      answer = await window.electronAPI.queryGemini(prompt);
      const latencyMs = Date.now() - startTime;
      console.log(`[Offline AI] Answered in ${latencyMs}ms — Q: "${latestQuestion.substring(0, 60)}..."`);

      // Update transcript cursor
      if (!isManual) {
        lastAnswerOffset = newOffset;
      }

      // Background updates (asynchronously, does not block answer typewriter display)
      setTimeout(async () => {
        try {
          // Update L2 (recent Q&A)
          offlineRecentQA.push({ q: latestQuestion, a: answer });
          if (offlineRecentQA.length > 3) {
            offlineRecentQA.shift();
          }

          // Update L3 (rolling summary) in background (offline only)
          if (!backendUrl || !sessionToken) {
            const summaryPrompt = buildSummaryPrompt({
              existingSummary: offlineRollingSummary,
              latestQuestion,
              latestAnswer: answer,
              transcriptWindow,
            });
            const newSummary = await window.electronAPI.queryGemini(summaryPrompt);
            offlineRollingSummary = newSummary;
            console.log('[Offline Memory L3] Rolling summary updated successfully in background');
          }
        } catch (bgErr) {
          console.error('[Offline BG Memory Update] Failed:', bgErr.message);
        }
      }, 50);
    }

    hasActiveAnswer = true;
    answerBlock.classList.remove('loading');

    // Typewriter effect
    let idx = 0;
    function typeResponse() {
      if (idx < answer.length) {
        const chunkSize = 6;
        newEntry.answer = answer.substring(0, idx + chunkSize);
        renderAnswerToDOM(answerBlock, newEntry.answer, currentQuestion);
        idx += chunkSize;
        answerBlock.scrollTop = 0;
        updateWindowSize();
        setTimeout(typeResponse, 10);
      } else {
        newEntry.answer = answer;
        const _ttftMs = Date.now() - _ttftStart;
        const _ttftSec = (_ttftMs / 1000).toFixed(1);
        newEntry.totalTimeSec = `${_ttftSec}s`;

        renderActiveAnswer();
      }
    }
    typeResponse();

  } catch (err) {
    answerBlock.classList.remove('loading');
    updateWindowSize();
    const msg = err.message || '';
    if (msg.includes('quota') || msg.includes('Quota') || msg.includes('limit') || msg.includes('rate')) {
      answerBlock.innerHTML = `
        <div style="color: #fca5a5; font-size: 11.5px; border: 1px solid rgba(239,68,68,0.25); background: rgba(239,68,68,0.08); padding: 10px; border-radius: 8px; line-height: 1.45;">
          <strong style="color: #ef4444; display: block; margin-bottom: 4px;">⚠️ Quota Exceeded (Gemini API)</strong>
          All available API keys have hit their free-tier request limits.
          <ul style="margin-left: 14px; margin-top: 6px; display: flex; flex-direction: column; gap: 4px;">
            <li>Please wait ~30-60 seconds for the rate limits to cool down, then try again.</li>
            <li>Verify you have valid active API keys configured.</li>
          </ul>
        </div>
      `;
    } else {
      answerBlock.innerHTML = `<span style="color: var(--accent-danger);">Error: ${msg}</span>`;
    }
  }
}

function updateAnswerNav() {
  const prevBtn = document.getElementById('prev-answer-btn');
  const nextBtn = document.getElementById('next-answer-btn');
  const label = document.getElementById('answer-nav-label');

  if (!prevBtn || !nextBtn || !label) return;

  if (answerHistory.length === 0) {
    label.textContent = 'No answers yet';
    prevBtn.style.opacity = '0.3';
    prevBtn.style.pointerEvents = 'none';
    nextBtn.style.opacity = '0.3';
    nextBtn.style.pointerEvents = 'none';
    return;
  }

  label.textContent = `Answer ${currentAnswerIndex + 1} of ${answerHistory.length}`;

  // Previous button state
  if (currentAnswerIndex > 0) {
    prevBtn.style.opacity = '1.0';
    prevBtn.style.pointerEvents = 'auto';
  } else {
    prevBtn.style.opacity = '0.3';
    prevBtn.style.pointerEvents = 'none';
  }

  // Next button state
  if (currentAnswerIndex < answerHistory.length - 1) {
    nextBtn.style.opacity = '1.0';
    nextBtn.style.pointerEvents = 'auto';
  } else {
    nextBtn.style.opacity = '0.3';
    nextBtn.style.pointerEvents = 'none';
  }
}

function renderActiveAnswer() {
  if (currentAnswerIndex < 0 || currentAnswerIndex >= answerHistory.length) {
    answerBlock.innerHTML = 'Click the "Answer" button to extract the latest question from the transcript and get an instant response...';
    updateAnswerNav();
    return;
  }
  const entry = answerHistory[currentAnswerIndex];
  renderAnswerToDOM(answerBlock, entry.answer, entry.question);

  if (entry.totalTimeSec) {
    const badge = document.createElement('div');
    badge.className = 'ttft-badge';
    badge.style.cssText = 'margin-top:6px;font-size:10px;color:rgba(255,255,255,0.35);text-align:right;padding-right:2px;letter-spacing:0.3px;';
    badge.textContent = `⏱ ${entry.totalTimeSec}`;
    answerBlock.appendChild(badge);
  }

  updateAnswerNav();
  answerBlock.scrollTop = 0; // scroll to top when switching
  updateWindowSize();
}

// Nav buttons wiring
const prevAnswerBtn = document.getElementById('prev-answer-btn');
const nextAnswerBtn = document.getElementById('next-answer-btn');
if (prevAnswerBtn) {
  prevAnswerBtn.addEventListener('click', () => {
    if (currentAnswerIndex > 0) {
      currentAnswerIndex--;
      renderActiveAnswer();
    }
  });
}
if (nextAnswerBtn) {
  nextAnswerBtn.addEventListener('click', () => {
    if (currentAnswerIndex < answerHistory.length - 1) {
      currentAnswerIndex++;
      renderActiveAnswer();
    }
  });
}

// Answer Button: detect latest question in transcript → backend or offline
aiAnswerBtn.addEventListener('click', () => {
  // Sync full text from DOM including both confirmed (white) and interim (gray) text
  const currentDOMText = transcriptBlock.textContent.trim();
  if (currentDOMText && transcriptBlock.dataset.placeholder !== 'true') {
    accumulatedTranscript = currentDOMText;
  }

  const currentText = accumulatedTranscript.trim();
  if (!currentText) {
    // Stealth mode only — show inline red banner in the answer block
    showInlineError('No transcript yet — type/paste a question or start recording to capture audio.', answerBlock);
    return;
  }

  // Trigger inquiry (both backend and offline logic now handled automatically in queryAssistant)
  queryAssistant(null, false);
});

// Manual Question Submission
function handleManualAISubmit() {
  const query = aiInput.value.trim();
  if (!query) return;
  aiInput.value = '';

  // Trigger query with manual context
  queryAssistant(query, true);
}

aiSend.addEventListener('click', handleManualAISubmit);
aiInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') handleManualAISubmit();
});

recordBtn.addEventListener('click', toggleRecording);
if (micBtnAi) {
  micBtnAi.addEventListener('click', toggleMicRecording);
}

async function solveFromScreenshot() {
  if (captureBtn) {
    captureBtn.disabled = true;
    captureBtn.textContent = 'Capture...';
  }
  const _screenshotStart = Date.now();
  hasActiveAnswer = true;
  if (activeTab !== 'ai') openPanel('ai');

  // Add new entry to history for horizontal carousel
  const newEntry = { question: '[Screen Capture]', answer: '⏳ Taking screenshot...', totalTimeSec: '' };
  answerHistory.push(newEntry);
  currentAnswerIndex = answerHistory.length - 1;
  updateAnswerNav();
  renderActiveAnswer();

  try {
    const base64Image = await window.electronAPI.takeScreenshot();
    newEntry.answer = '⚙️ Analyzing with vision AI...';
    renderActiveAnswer();

    const prompt = `Analyze the provided screenshot of the screen. Find the question, coding problem, or conceptual statement visible on the screen.

Rules:
1. If the screenshot shows a coding problem or request to write code:
   - CRITICAL: You MUST write the code solution in the EXACT programming language shown or implied in the screenshot's code editor, starter code, or description (e.g., if you see Java syntax, classes, or imports, you MUST write the solution in Java. If you see C++, write it in C++. If you see JS, write it in JS). Do NOT default to Python unless the screenshot explicitly requests Python.
   - At the very top, write a single line summarizing the basic question in 1 sentence. Start the line with "// Question: " (or appropriate comment syntax for the language).
   - Followed by a blank line.
   - Then write the clean, fully functional, optimal code solution in that detected language.
   - The code solution MUST NOT contain any comments (no inline comments, no block comments, no docstrings) or markdown code block formatting (like \`\`\`).
2. If the screenshot shows a conceptual, theoretical, or verbal question (such as explaining OOP, architecture, system design, or definitions):
   - Do NOT output any code blocks or code syntax.
   - Provide a natural, conversational explanation in plain English paragraphs, as if speaking to an interviewer. Keep it concise.`;

    let answer;
    if (backendUrl) {
      const preferredModel = document.getElementById('setup-model-select').value;
      const res = await window.electronAPI.solveScreenshotBackend({
        base64Image,
        sessionToken,
        model: preferredModel
      });
      if (res.error) throw new Error(res.error);
      answer = res.answer;
    } else {
      answer = await window.electronAPI.queryGemini(prompt, base64Image);
    }

    newEntry.answer = answer;
    const _screenshotMs = Date.now() - _screenshotStart;
    const _screenshotSec = (_screenshotMs / 1000).toFixed(1);
    newEntry.totalTimeSec = `${_screenshotSec}s`;

    renderActiveAnswer();
  } catch (err) {
    console.error('[Solve Screen Error]', err);
    newEntry.answer = `// Error: ${err.message}`;
    renderActiveAnswer();
  } finally {
    if (captureBtn) {
      captureBtn.disabled = false;
      captureBtn.innerHTML = `
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
          <circle cx="12" cy="13" r="4"></circle>
        </svg>
        Capture
      `;
    }
  }
}

if (captureBtn) {
  captureBtn.addEventListener('click', solveFromScreenshot);
}
function cleanGeminiCodeOutput(text) {
  if (!text) return '';
  let cleaned = text.trim();
  cleaned = cleaned.replace(/^```[a-zA-Z]*\n/, '');
  cleaned = cleaned.replace(/\n```$/, '');
  cleaned = cleaned.replace(/^```/, '');
  return cleaned.trim();
}

// ── Built-in Syntax Highlighter ───────────────────────────────────────────────
// Detects language from the first fence line (```python → "python") or from code patterns.
function detectLang(rawAnswer) {
  // 1. Check for markdown code block fence anywhere in the response
  const m = rawAnswer.match(/```(\w+)/);
  if (m) return m[1].toLowerCase();

  // Clean the text for heuristic analysis
  const clean = rawAnswer.replace(/^\/\/.*$/gm, '').trim();

  // 2. Detect strongly-typed languages first
  if (/#include|std::|cout|cin|<iostream>/i.test(clean)) return 'cpp';
  if (/Console\.WriteLine|using System/i.test(clean)) return 'csharp';
  if (/System\.out\.print|public static void main|import java\./.test(clean)) return 'java';

  // Curly braces check: Python does not use braces for class/function blocks
  const hasBraces = /\{[\s\S]*\}/.test(clean);

  // 3. Detect Python (only if no braces wrapping code)
  if (/def\s+[\w_]+\(|elif\s+|lambda\s+|self\.|import\s+sys|import\s+math|import\s+os/.test(clean) && !hasBraces) {
    return 'python';
  }

  // 4. Default structural indicators
  if (/public\s+class|void|class\s+[\w_]+\s+impl/.test(clean)) return 'java';
  if (/function\s+|const\s+|let\s+|var\s+|console\.log/.test(clean)) return 'javascript';
  if (/fun\s+|val\s+|println/.test(clean)) return 'kotlin';
  if (/func\s+|package\s+|fmt\./.test(clean)) return 'go';

  // Fallbacks
  if (clean.includes('class ') && hasBraces) {
    if (/public|private|protected/.test(clean)) return 'java';
    return 'javascript';
  }
  if (clean.includes('class ') && !hasBraces) return 'python';

  return 'code';
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function syntaxHighlight(code, lang) {
  // Per-language token configs
  const LANGS = {
    python: {
      keywords: /\b(False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b/g,
      builtins: /\b(print|len|range|enumerate|zip|map|filter|sorted|list|dict|set|tuple|str|int|float|bool|type|isinstance|hasattr|getattr|setattr|open|super|property|staticmethod|classmethod|abs|max|min|sum|any|all|input|repr|id|hash|chr|ord)\b/g,
      types: /\b(int|str|float|bool|list|dict|set|tuple|bytes|List|Dict|Set|Tuple|Optional|Union|Any|Callable|Iterable|Iterator|Generator|Type|ClassVar|Final|Literal|TypeVar|Generic|Protocol|Awaitable|Coroutine|AsyncIterator|AsyncGenerator)\b/g,
      decorator: /^[ \t]*(@\w[\w.]*)/gm,
      self: /\b(self|cls)\b/g,
    },
    java: {
      keywords: /\b(abstract|assert|break|case|catch|class|const|continue|default|do|else|enum|extends|final|finally|for|goto|if|implements|import|instanceof|interface|native|new|package|private|protected|public|return|static|strictfp|super|switch|synchronized|this|throw|throws|transient|try|var|void|volatile|while)\b/g,
      types: /\b(boolean|byte|char|double|float|int|long|short|String|Object|Integer|Double|Long|Float|Boolean|List|Map|Set|Queue|Stack|ArrayList|HashMap|HashSet|LinkedList|Optional|Stream|StringBuilder)\b/g,
    },
    cpp: {
      keywords: /\b(auto|break|case|catch|class|const|constexpr|continue|default|delete|do|else|enum|explicit|extern|false|final|for|friend|goto|if|inline|mutable|namespace|new|noexcept|nullptr|operator|override|private|protected|public|register|return|sizeof|static|static_cast|struct|switch|template|this|throw|true|try|typedef|typename|union|using|virtual|void|volatile|while)\b/g,
      types: /\b(int|char|double|float|long|short|bool|string|vector|map|unordered_map|set|unordered_set|pair|queue|stack|priority_queue|deque|list|array|tuple|optional|variant|shared_ptr|unique_ptr|weak_ptr|size_t|uint8_t|uint16_t|uint32_t|uint64_t|int64_t|int32_t)\b/g,
    },
    javascript: {
      keywords: /\b(async|await|break|case|catch|class|const|continue|debugger|default|delete|do|else|export|extends|false|finally|for|from|function|if|import|in|instanceof|let|new|null|of|return|static|super|switch|this|throw|true|try|typeof|undefined|var|void|while|with|yield)\b/g,
      builtins: /\b(console|Math|Array|Object|String|Number|Boolean|JSON|Promise|Date|RegExp|Error|Map|Set|WeakMap|WeakSet|Symbol|Proxy|Reflect|Intl|setTimeout|setInterval|clearTimeout|clearInterval|fetch|document|window|process|require|module|exports)\b/g,
    },
    go: {
      keywords: /\b(break|case|chan|const|continue|default|defer|else|fallthrough|for|func|go|goto|if|import|interface|map|package|range|return|select|struct|switch|type|var)\b/g,
      types: /\b(bool|byte|complex64|complex128|error|float32|float64|int|int8|int16|int32|int64|rune|string|uint|uint8|uint16|uint32|uint64|uintptr|any)\b/g,
      builtins: /\b(append|cap|close|complex|copy|delete|imag|len|make|new|panic|print|println|real|recover)\b/g,
    },
    kotlin: {
      keywords: /\b(abstract|actual|as|break|by|catch|class|companion|const|constructor|continue|crossinline|data|do|dynamic|else|enum|expect|external|false|final|finally|for|fun|get|if|import|in|infix|init|inline|inner|interface|internal|is|it|lateinit|noinline|null|object|open|operator|out|override|package|private|protected|public|reified|return|sealed|set|super|suspend|tailrec|this|throw|true|try|typealias|typeof|val|var|vararg|when|where|while)\b/g,
      types: /\b(Boolean|Byte|Char|Double|Float|Int|Long|Short|String|Unit|Any|Nothing|Array|List|MutableList|Map|MutableMap|Set|MutableSet|Pair|Triple|Result|Sequence|Flow|Channel|Job|Deferred|CoroutineScope|Dispatchers)\b/g,
    },
    csharp: {
      keywords: /\b(abstract|as|base|break|case|catch|checked|class|const|continue|default|delegate|do|else|enum|event|explicit|extern|false|finally|fixed|for|foreach|goto|if|implicit|in|interface|internal|is|lock|namespace|new|null|object|operator|out|override|params|private|protected|public|readonly|ref|return|sealed|sizeof|stackalloc|static|struct|switch|this|throw|true|try|typeof|unchecked|unsafe|using|virtual|void|volatile|while|async|await|dynamic|var|yield)\b/g,
      types: /\b(bool|byte|char|decimal|double|float|int|long|sbyte|short|string|uint|ulong|ushort|nint|nuint|List|Dictionary|HashSet|Queue|Stack|Array|Tuple|Task|ValueTask|IEnumerable|IQueryable|ICollection|IList|IDictionary|Nullable|Action|Func|Predicate|EventHandler)\b/g,
    },
  };

  const cfg = LANGS[lang] || LANGS['javascript'];
  const lines = code.split('\n');
  const result = [];

  for (const line of lines) {
    // Process one line at a time for safety (comments can only be line-scoped here)
    let out = '';
    let rest = line;
    let col = 0;

    // Collect tokens: [{start, end, cls}] sorted by position
    const tokens = [];

    const addToks = (re, cls) => {
      re.lastIndex = 0;
      let m2;
      while ((m2 = re.exec(rest)) !== null) {
        tokens.push({ start: m2.index, end: m2.index + m2[0].length, cls, text: m2[0] });
      }
    };

    // Line/block comments
    const commentRe = lang === 'python' ? /(#.*)$/ :
      lang === 'cpp' || lang === 'java' || lang === 'javascript' || lang === 'csharp' || lang === 'kotlin' ? /(\/\/.*)$/ :
        lang === 'go' ? /(\/\/.*)$/ : null;
    if (commentRe) {
      const cm = rest.match(commentRe);
      if (cm) tokens.push({ start: cm.index, end: cm.index + cm[0].length, cls: 'tok-comment', text: cm[0] });
    }

    // Strings — double quotes
    { const re = /"(?:[^"\\]|\\.)*"/g; addToks(re, 'tok-string'); }
    // Strings — single quotes
    { const re = /'(?:[^'\\]|\\.)*'/g; addToks(re, 'tok-string2'); }
    // Template literals (JS)
    if (lang === 'javascript') {
      const re = /`(?:[^`\\]|\\.)*`/g; addToks(re, 'tok-string2');
    }
    // Numbers
    { const re = /\b(0x[0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?[lLfFdD]?)\b/g; addToks(re, 'tok-number'); }
    // Decorators (Python)
    if (lang === 'python' && cfg.decorator) {
      cfg.decorator.lastIndex = 0;
      let m2;
      while ((m2 = cfg.decorator.exec(rest)) !== null) {
        tokens.push({ start: m2.index, end: m2.index + m2[1].length, cls: 'tok-decorator', text: m2[1] });
      }
    }
    // self/cls
    if (cfg.self) addToks(cfg.self, 'tok-self');
    // Builtins
    if (cfg.builtins) addToks(cfg.builtins, 'tok-builtin');
    // Types
    if (cfg.types) addToks(cfg.types, 'tok-type');
    // Keywords (after builtins/types so they take priority if overlapping)
    if (cfg.keywords) addToks(cfg.keywords, 'tok-keyword');
    // Function definitions: name(
    { const re = /\b([A-Za-z_]\w*)\s*(?=\()/g; addToks(re, 'tok-func'); }
    // Operators
    { const re = /(\+\+|--|->|=>|===|!==|==|!=|<=|>=|&&|\|\||[+\-*/%=<>!&|^~?])/g; addToks(re, 'tok-operator'); }
    // Punctuation
    { const re = /[()[\]{},.:;]/g; addToks(re, 'tok-punct'); }

    // Sort tokens by start position; later (higher-priority) wins over earlier
    tokens.sort((a, b) => a.start - b.start);

    // De-overlap: keep only non-overlapping tokens (first one wins at each char)
    const used = new Uint8Array(rest.length + 1);
    const finalToks = [];
    for (const t of tokens) {
      let blocked = false;
      for (let i = t.start; i < t.end; i++) { if (used[i]) { blocked = true; break; } }
      if (!blocked) {
        for (let i = t.start; i < t.end; i++) used[i] = 1;
        finalToks.push(t);
      }
    }
    finalToks.sort((a, b) => a.start - b.start);

    // Build HTML for the line
    let pos = 0;
    for (const t of finalToks) {
      if (t.start > pos) out += escapeHtml(rest.slice(pos, t.start));
      out += `<span class="${t.cls}">${escapeHtml(rest.slice(t.start, t.end))}</span>`;
      pos = t.end;
    }
    if (pos < rest.length) out += escapeHtml(rest.slice(pos));
    result.push(out);
  }

  return result.join('\n');
}

// Render highlighted code into #code-display
function setCodeDisplay(rawAnswer) {
  const preCel = document.getElementById('code-display');
  const codeCel = preCel ? preCel.querySelector('code') : null;
  if (!codeCel) return;

  const lang = detectLang(rawAnswer);
  const cleanCode = cleanGeminiCodeOutput(rawAnswer);
  preCel.setAttribute('data-lang', lang);
  codeCel.innerHTML = syntaxHighlight(cleanCode, lang);
  preCel.style.display = 'block';
  // Show the copy button whenever code is displayed
  if (copyCodeBtn) copyCodeBtn.style.display = 'inline-block';
}

if (codeScreenshotBtn) {
  codeScreenshotBtn.addEventListener('click', solveFromScreenshot);
}



// -------------------------------------------------------------
// 5. OFFLINE 4-LAYER MEMORY HELPER FUNCTIONS
// -------------------------------------------------------------

const QUESTION_KEYWORDS = [
  'what', 'why', 'how', 'when', 'where', 'who', 'which',
  'can you', 'could you', 'would you', 'should', 'do you',
  'explain', 'describe', 'tell me', 'is there', 'are there',
];

function splitIntoSentences(text) {
  return text
    .split(/(?<=[.?!])\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 5);
}

function questionScore(sentence) {
  const lower = sentence.toLowerCase();
  let score = 0;
  if (sentence.endsWith('?')) score += 2;
  if (QUESTION_KEYWORDS.some((kw) => lower.startsWith(kw))) score += 1;
  return score;
}

function detectLatestQuestion(fullTranscript, lastAnsweredOffset = 0) {
  const newText = fullTranscript.slice(lastAnsweredOffset).trim();

  if (!newText || newText.length < 10) {
    return { question: null, newOffset: lastAnsweredOffset };
  }

  const sentences = splitIntoSentences(newText);

  if (sentences.length === 0) {
    return { question: null, newOffset: lastAnsweredOffset };
  }

  let bestSentence = null;
  let bestScore = 0;

  for (let i = sentences.length - 1; i >= 0; i--) {
    const score = questionScore(sentences[i]);
    if (score > bestScore) {
      bestScore = score;
      bestSentence = sentences[i];
      break;
    }
  }

  if (!bestSentence || bestScore === 0) {
    bestSentence = sentences.slice(-2).join(' ');
  }

  return {
    question: bestSentence,
    newOffset: fullTranscript.length,
  };
}

function getTranscriptWindow(fullTranscript, maxWords = 250) {
  const words = fullTranscript.trim().split(/\s+/);
  if (words.length <= maxWords) return fullTranscript;
  return words.slice(-maxWords).join(' ');
}

function formatRecentQA(recentQA = []) {
  if (!recentQA || recentQA.length === 0) return '';
  return recentQA
    .slice(-3)
    .map(({ q, a }, i) => `Q${i + 1}: ${q}\nA${i + 1}: ${a}`)
    .join('\n\n');
}

function buildPrompt({ latestQuestion, recentQA, rollingSummary, userContext, transcriptWindow }) {
  const parts = [];

  parts.push(
    `You are a real person sitting in a technical interview today. You are the candidate, not an AI.
Your only job is to answer the interviewer's question the way a real, experienced engineer would naturally say it out loud.

HOW TO ANSWER:
- Speak in one or two fluid, conversational paragraphs.
- NEVER use numbered lists (like 1., 2., 3.), bullet points, dashes, or numbered steps.
- NEVER use bold text, asterisks (**), or any markdown headings.
- Talk the way a confident engineer talks in a real conversation.
- Use casual contractions: I've, I'd, I'm, that's, it's, we've, didn't, don't.
- Start directly with the point. Do NOT warm up with filler.
- Keep the response short and clean — under 150 words.
- Do NOT say: "Certainly", "Great question", "Absolutely", "Of course", "Sure", "Here's the answer", "As an AI".
- Sound like a real person talking, not a document being read.`
  );

  if (userContext) {
    const { resume, job_description, code_context } = userContext;
    const ctxParts = [];
    if (resume) ctxParts.push(`RESUME:\n${resume.substring(0, 400)}`);
    if (job_description) ctxParts.push(`JOB DESCRIPTION:\n${job_description.substring(0, 300)}`);
    if (code_context) ctxParts.push(`CODE CONTEXT:\n${code_context.substring(0, 400)}`);
    if (ctxParts.length > 0) {
      parts.push(`\n[USER CONTEXT]\n${ctxParts.join('\n\n')}`);
    }
  }

  if (rollingSummary && rollingSummary.trim().length > 0) {
    parts.push(`\n[SESSION SUMMARY]\n${rollingSummary.substring(0, 600)}`);
  }

  const qaFormatted = formatRecentQA(recentQA);
  if (qaFormatted) {
    parts.push(`\n[RECENT Q&A]\n${qaFormatted}`);
  }

  if (transcriptWindow && transcriptWindow.trim().length > 0) {
    parts.push(`\n[RECENT AUDIO TRANSCRIPT]\n${transcriptWindow}`);
  }

  parts.push(`\n[QUESTION TO ANSWER]\n${latestQuestion}`);
  parts.push(`\nAnswer (plain spoken English only, no formatting):`);

  return parts.join('\n');
}


function buildSummaryPrompt({ existingSummary, latestQuestion, latestAnswer, transcriptWindow }) {
  return `You are summarizing a live session for a real-time AI assistant.

EXISTING SUMMARY:
${existingSummary || '(none yet)'}

NEW TRANSCRIPT EXCERPT:
${transcriptWindow || ''}

LATEST Q&A:
Q: ${latestQuestion}
A: ${latestAnswer}

Task: Update the session summary in under 400 words. Keep it factual. Focus on topics discussed, technical concepts, and key decisions. Do not mention meta-details about the AI.

Updated summary:`;
}

// Helpers to parse markdown, format code, and render styled HTML inside the Electron app
function parseMarkdownToHTML(text) {
  const segments = [];
  let currentIndex = 0;
  while (currentIndex < text.length) {
    const codeBlockStart = text.indexOf('```', currentIndex);
    if (codeBlockStart === -1) {
      segments.push({ type: 'text', content: text.slice(currentIndex) });
      break;
    }
    if (codeBlockStart > currentIndex) {
      segments.push({ type: 'text', content: text.slice(currentIndex, codeBlockStart) });
    }
    const nextStart = codeBlockStart + 3;
    const codeBlockEnd = text.indexOf('```', nextStart);
    if (codeBlockEnd === -1) {
      // Unclosed code block — treat remainder as code
      const langMatch = text.slice(nextStart).match(/^([a-zA-Z0-9+#-]+)?\n/);
      const lang = langMatch ? langMatch[1] : '';
      const content = langMatch ? text.slice(nextStart + langMatch[0].length) : text.slice(nextStart);
      segments.push({ type: 'code', lang, content });
      break;
    }
    const langMatch = text.slice(nextStart, codeBlockEnd).match(/^([a-zA-Z0-9+#-]+)?\n/);
    const lang = langMatch ? langMatch[1] : '';
    const content = langMatch ? text.slice(nextStart + langMatch[0].length, codeBlockEnd) : text.slice(nextStart, codeBlockEnd);
    segments.push({ type: 'code', lang, content });
    currentIndex = codeBlockEnd + 3;
  }
  return segments;
}

function highlightCodeLine(line) {
  if (!line) return '<div style="height:14px;"></div>';
  const tokenRegex = /(\/\/.*|#.*|--.*)|("[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*')|\b(def|class|return|if|else|elif|for|while|import|from|const|let|var|function|select|where|from|join|on|group|by|order|limit|null|true|false|None|self|and|or|not|in|as|try|except|catch|finally|throw|new|public|private|protected|static|void|int|str|float|bool|list|dict|set|tuple)\b|(\b\d+\b)|\b([a-zA-Z_][a-zA-Z0-9_]*)(?=\()|(\s+)|([^\s\w]+)|([a-zA-Z_][a-zA-Z0-9_]*)/g;

  let match;
  let html = '';
  tokenRegex.lastIndex = 0;
  while ((match = tokenRegex.exec(line)) !== null) {
    const [
      _,
      comment,
      stringToken,
      keyword,
      numberToken,
      funcName,
      spaces,
      operator,
      identifier
    ] = match;

    if (comment) {
      html += `<span style="color: #64748b; font-style: italic;">${escapeHTML(comment)}</span>`;
    } else if (stringToken) {
      html += `<span style="color: #34d399; font-weight: 500;">${escapeHTML(stringToken)}</span>`;
    } else if (keyword) {
      html += `<span style="color: #2dd4bf; font-weight: bold;">${escapeHTML(keyword)}</span>`;
    } else if (numberToken) {
      html += `<span style="color: #fbbf24;">${escapeHTML(numberToken)}</span>`;
    } else if (funcName) {
      html += `<span style="color: #38bdf8; font-weight: 600;">${escapeHTML(funcName)}</span>`;
    } else if (spaces) {
      html += spaces;
    } else if (operator) {
      html += `<span style="color: #f472b6;">${escapeHTML(operator)}</span>`;
    } else if (identifier) {
      if (['self', 'None', 'true', 'false', 'null'].includes(identifier)) {
        html += `<span style="color: #818cf8; font-weight: 600;">${escapeHTML(identifier)}</span>`;
      } else {
        html += escapeHTML(identifier);
      }
    }
  }
  return html || escapeHTML(line);
}

function escapeHTML(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderAnswerToDOM(container, text, questionText = '') {
  container.innerHTML = '';

  if (questionText) {
    const qDiv = document.createElement('div');
    qDiv.style.marginBottom = '12px';
    qDiv.style.padding = '8px 12px';
    qDiv.style.background = 'rgba(0,0,0,0.2)';
    qDiv.style.borderRadius = '6px';
    qDiv.style.borderLeft = '3px solid #2dd4bf';
    qDiv.style.display = 'flex';
    qDiv.style.alignItems = 'flex-start';
    qDiv.style.gap = '6px';

    const qBadge = document.createElement('span');
    qBadge.textContent = 'Q:';
    qBadge.style.color = '#2dd4bf';
    qBadge.style.fontWeight = '800';
    qBadge.style.fontSize = '1em';
    qBadge.style.flexShrink = '0';

    const qText = document.createElement('span');
    qText.textContent = questionText;
    qText.style.color = 'rgba(255, 255, 255, 0.7)';
    qText.style.fontSize = '1em';
    qText.style.fontStyle = 'italic';
    qText.style.lineHeight = '1.4';

    qDiv.appendChild(qBadge);
    qDiv.appendChild(qText);
    container.appendChild(qDiv);
  }

  const segments = parseMarkdownToHTML(text);

  segments.forEach(segment => {
    if (segment.type === 'code') {
      const codeWrapper = document.createElement('div');
      codeWrapper.style.margin = '10px 0';
      codeWrapper.style.borderRadius = '8px';
      codeWrapper.style.border = '1px solid rgba(20, 184, 166, 0.3)';
      codeWrapper.style.background = '#05060f';
      codeWrapper.style.fontFamily = 'monospace';
      codeWrapper.style.fontSize = '0.9em';
      codeWrapper.style.overflow = 'hidden';

      const header = document.createElement('div');
      header.style.display = 'flex';
      header.style.justifyContent = 'space-between';
      header.style.alignItems = 'center';
      header.style.background = 'rgba(13, 148, 136, 0.1)';
      header.style.padding = '4px 12px';
      header.style.borderBottom = '1px solid rgba(20, 184, 166, 0.2)';

      const langLabel = document.createElement('span');
      langLabel.textContent = (segment.lang || 'code').toUpperCase();
      langLabel.style.color = '#5eead4';
      langLabel.style.fontSize = '0.75em';
      langLabel.style.fontWeight = 'bold';
      langLabel.style.letterSpacing = '0.5px';

      const copyBtn = document.createElement('button');
      copyBtn.textContent = 'Copy';
      copyBtn.style.background = 'rgba(13, 148, 136, 0.2)';
      copyBtn.style.border = '1px solid rgba(20, 184, 166, 0.2)';
      copyBtn.style.color = '#5eead4';
      copyBtn.style.fontSize = '0.75em';
      copyBtn.style.padding = '2px 8px';
      copyBtn.style.borderRadius = '4px';
      copyBtn
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(segment.content.trim());
        copyBtn.textContent = 'Copied!';
        setTimeout(() => copyBtn.textContent = 'Copy', 1500);
      };

      header.appendChild(langLabel);
      header.appendChild(copyBtn);
      codeWrapper.appendChild(header);

      const pre = document.createElement('pre');
      pre.style.margin = '0';
      pre.style.padding = '8px 12px';
      pre.style.overflowX = 'auto';
      pre.style.whiteSpace = 'pre-wrap';
      pre.style.color = '#cbd5e1';

      const lines = segment.content.split('\n');
      const highlightedLines = lines.map(line => highlightCodeLine(line));
      pre.innerHTML = highlightedLines.join('\n');

      codeWrapper.appendChild(pre);
      container.appendChild(codeWrapper);
    } else {
      const textDiv = document.createElement('div');
      textDiv.style.lineHeight = '1.7';
      textDiv.style.color = '#e2e8f0';
      textDiv.style.fontSize = '1em';

      // Split into lines and render bullet lines specially
      const lines = segment.content.split('\n');
      lines.forEach(line => {
        const trimmed = line.trim();
        if (trimmed.startsWith('- ') || trimmed.startsWith('• ')) {
          // Bullet line — render as a styled bullet row
          const bulletRow = document.createElement('div');
          bulletRow.style.display = 'flex';
          bulletRow.style.alignItems = 'flex-start';
          bulletRow.style.gap = '8px';
          bulletRow.style.marginBottom = '8px';
          bulletRow.style.paddingLeft = '4px';

          const dot = document.createElement('span');
          dot.textContent = '•';
          dot.style.color = '#2dd4bf';
          dot.style.fontWeight = 'bold';
          dot.style.fontSize = '1.1em';
          dot.style.flexShrink = '0';
          dot.style.lineHeight = '1.6';

          const bulletContent = document.createElement('span');
          bulletContent.style.flex = '1';
          bulletContent.style.lineHeight = '1.6';

          // Strip leading dash/bullet char
          let content = trimmed.startsWith('- ') ? trimmed.slice(2) : trimmed.slice(2);
          // Render **heading:** in purple bold, rest in normal text
          let html = escapeHTML(content).replace(
            /\*\*([^*]+)\*\*/g,
            '<strong style="color:#5eead4;font-weight:700;letter-spacing:0.01em;">$1</strong>'
          );
          bulletContent.innerHTML = html;

          bulletRow.appendChild(dot);
          bulletRow.appendChild(bulletContent);
          textDiv.appendChild(bulletRow);
        } else if (trimmed === '') {
          // Empty line — small spacer
          const spacer = document.createElement('div');
          spacer.style.height = '4px';
          textDiv.appendChild(spacer);
        } else {
          // Regular non-bullet line
          const lineDiv = document.createElement('div');
          lineDiv.style.marginBottom = '4px';
          lineDiv.style.lineHeight = '1.6';
          let html = escapeHTML(trimmed).replace(
            /\*\*([^*]+)\*\*/g,
            '<strong style="color:#5eead4;font-weight:700;">$1</strong>'
          );
          lineDiv.innerHTML = html;
          textDiv.appendChild(lineDiv);
        }
      });

      container.appendChild(textDiv);
    }
  });
}

const settingsBtn = document.getElementById('settings-btn');
const opacitySlider = document.getElementById('opacity-slider');
const opacityDisplay = document.getElementById('opacity-display');
const fontSizeInput = document.getElementById('font-size-input');
const userEmailDisplay = document.getElementById('user-email-display');
const mainAnswerBlock = document.getElementById('answer-block');
const mainTranscriptBlock = document.getElementById('transcript-block');
const zoomSlider = document.getElementById('zoom-slider');
const zoomDisplay = document.getElementById('zoom-display');

// Load settings from localStorage
async function loadAllSettings() {
  const savedEmail = safeGetItem('stealth_user_email');
  const savedUserId = safeGetItem('stealth_user_id');
  if (savedUserId) {
    USER_ID = normalizeUserId(savedUserId);
  }
  if (savedEmail) {
    if (userEmailDisplay) {
      userEmailDisplay.textContent = savedEmail;
    }
    await syncUserEmail(savedEmail); // Dynamically sync user ID
  } else {
    if (userEmailDisplay) {
      userEmailDisplay.textContent = 'premium@stealth.ai';
    }
  }

  const savedOpacity = safeGetItem('stealth_opacity');
  if (savedOpacity !== null) {
    userOpacity = Math.max(0.20, Math.min(1.0, parseFloat(savedOpacity)));
    if (opacitySlider) opacitySlider.value = userOpacity;
    if (opacityDisplay) opacityDisplay.textContent = Math.round(userOpacity * 100) + '%';
    // Apply user opacity to the app container (both steps wizard and active session)
    const appCont = document.querySelector('.app-container');
    if (appCont) appCont.style.opacity = Math.min(1.0, userOpacity);
  } else {
    userOpacity = 1.0;
    if (opacityDisplay && opacitySlider) {
      opacitySlider.value = 1.0;
      opacityDisplay.textContent = '100%';
    }
  }

  const savedDevStealth = safeGetItem('stealth_dev_mode_enabled');
  if (savedDevStealth !== null) {
    updateDevStealthUI(savedDevStealth === 'true');
  } else {
    updateDevStealthUI(true);
  }

  const savedFontSize = safeGetItem('stealth_font_size');
  if (savedFontSize !== null) {
    if (fontSizeInput) fontSizeInput.value = savedFontSize;
    const val = savedFontSize + 'px';
    if (mainAnswerBlock) mainAnswerBlock.style.fontSize = val;
    if (mainTranscriptBlock) mainTranscriptBlock.style.fontSize = val;
  }

  const savedZoom = safeGetItem('stealth_zoom');
  if (savedZoom !== null) {
    const val = parseFloat(savedZoom);
    if (zoomSlider) zoomSlider.value = savedZoom;
    if (zoomDisplay) zoomDisplay.textContent = Math.round(val * 100) + '%';
    document.body.style.zoom = '1.0';
  }

  if (window.electronAPI && window.electronAPI.setZoomFactor) {
    window.electronAPI.setZoomFactor(1.0);
  }
}

// ── Web Authentication Sync DOM & Handlers ──────────────────────────
const setupSyncView = document.getElementById('setup-sync-view');
const setupStepsWrapper = document.getElementById('setup-steps-wrapper');
const manualSyncBtn = document.getElementById('manual-sync-btn');

async function verifySessionOnStartup() {
  const email = safeGetItem('stealth_user_email');
  const token = safeGetItem('stealth_login_token');

  if (!email || !token) {
    // Not logged in -> Show sync page and trigger browser sync
    showSyncPage();
    return;
  }

  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
    const res = await fetch(`${base}/api/auth/check-session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: USER_ID, login_token: token })
    });

    if (res.ok) {
      const data = await res.json();
      if (data.valid) {
        showSetupWizard();
      } else {
        console.log('[Stealth Sync] Session invalid on backend. Logging out.');
        logoutLocalUser();
      }
    } else {
      console.warn('[Stealth Sync] Backend check failed, offline fallback enabled.');
      showSetupWizard();
    }
  } catch (err) {
    console.error('[Stealth Sync] Failed to verify session with backend:', err);
    showSetupWizard(); // Offline fallback
  }
}

function showSyncPage() {
  if (setupSyncView) setupSyncView.style.display = 'flex';
  if (setupStepsWrapper) setupStepsWrapper.style.display = 'none';
  triggerBrowserSync();
}

function showSetupWizard() {
  if (setupSyncView) setupSyncView.style.display = 'none';
  if (setupStepsWrapper) setupStepsWrapper.style.display = 'flex';
}

async function triggerBrowserSync() {
  try {
    const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
    const syncUrl = 'http://localhost:5173/sync?port=48999';
    window.electronAPI.openExternalUrl(syncUrl);
  } catch (err) {
    console.error('[Stealth Sync] Failed to launch external browser sync page:', err);
  }
}

function logoutLocalUser() {
  safeSetItem('stealth_user_email', '');
  safeSetItem('stealth_login_token', '');
  if (userEmailDisplay) userEmailDisplay.textContent = 'Not Synced';
  showSyncPage();
}

// Call settings initialization and startup verification
async function initApp() {
  await loadAllSettings();
  await verifySessionOnStartup();
}
initApp();

// Run periodic session check every 30 seconds to detect concurrent logins/logouts
setInterval(async () => {
  const email = safeGetItem('stealth_user_email');
  const token = safeGetItem('stealth_login_token');

  if (email && token) {
    try {
      const base = (await window.electronAPI.getBackendUrl()) || 'http://localhost:8000';
      const res = await fetch(`${base}/api/auth/check-session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: USER_ID, login_token: token })
      });
      if (res.ok) {
        const data = await res.json();
        if (!data.valid) {
          console.log('[Stealth Sync] Session invalidated by another login. Logging out.');
          logoutLocalUser();
        }
      }
    } catch (err) {
      console.warn('[Stealth Sync] Periodic session check connection error:', err.message);
    }
  }
}, 30000);

// Listen for credentials sent from system browser via Electron local HTTP server
if (window.electronAPI && window.electronAPI.onSyncCredentials) {
  window.electronAPI.onSyncCredentials((data) => {
    console.log('[Stealth Sync] Credentials received from browser:', data);
    if (data.email && data.token) {
      safeSetItem('stealth_user_email', data.email);
      safeSetItem('stealth_login_token', data.token);
      if (data.user_id) {
        USER_ID = data.user_id;
        safeSetItem('stealth_user_id', data.user_id);
      }
      if (userEmailDisplay) {
        userEmailDisplay.textContent = data.email;
      }
      syncUserEmail(data.email);
      showSetupWizard();
    }
  });
}

if (manualSyncBtn) {
  manualSyncBtn.addEventListener('click', triggerBrowserSync);
}

const setupSettingsBtn = document.getElementById('setup-settings-btn');

function toggleSettingsPopup() {
  console.log('[Stealth Debug] toggleSettingsPopup called');
  const popup = document.getElementById('settings-popup');
  if (popup) {
    const isShowing = popup.style.display === 'flex';
    popup.style.display = isShowing ? 'none' : 'flex';
    console.log('[Stealth Debug] settings-popup display toggled to:', popup.style.display);
  } else {
    console.log('[Stealth Debug] settings-popup element not found');
  }
  updateWindowSize();
}

if (settingsBtn) {
  settingsBtn.addEventListener('click', toggleSettingsPopup);
}

if (setupSettingsBtn) {
  setupSettingsBtn.addEventListener('click', toggleSettingsPopup);
}

const settingsCloseBtn = document.getElementById('settings-close-btn');
if (settingsCloseBtn) {
  settingsCloseBtn.addEventListener('click', () => {
    const popup = document.getElementById('settings-popup');
    if (popup) {
      popup.style.display = 'none';
    }
    updateWindowSize();
  });
}

function logoutLocalUser() {
  console.log('[Stealth Logout] Logging out account...');
  try {
    localStorage.removeItem('stealth_user_email');
    localStorage.removeItem('stealth_user_id');
    localStorage.removeItem('stealth_login_token');
    localStorage.removeItem('stealth_session_token');
  } catch (e) { }
  USER_ID = '856fdc6d-19b9-547e-be7b-0df7fa5b505b';
  if (userEmailDisplay) {
    userEmailDisplay.textContent = 'Not Synced';
  }
  sessionToken = null;
  if (isRecording) {
    stopRecording();
  }
  showSetupWizard();
  if (typeof showModalOverlay === 'function') {
    showModalOverlay('Logged Out', '<div style="text-align:center;padding:24px 0;color:var(--text-primary);font-size:13px;">Your account has been logged out successfully.</div>');
  }
}

const settingsLogoutBtn = document.getElementById('settings-logout-btn');
if (settingsLogoutBtn) {
  settingsLogoutBtn.addEventListener('click', () => {
    console.log('[Stealth] Exit button clicked — directly killing application process.');
    window.electronAPI.quitApp();
  });
}

const settingsDashboardBtn = document.getElementById('settings-dashboard-btn');
if (settingsDashboardBtn) {
  settingsDashboardBtn.addEventListener('click', () => {
    window.electronAPI.openExternalUrl('http://localhost:5173');
  });
}

const opacityMinus = document.getElementById('opacity-minus');
const opacityPlus = document.getElementById('opacity-plus');

window._opacityPreviewTimeout = null;
window._isPreviewingOpacity = false;

function applyOpacity(val) {
  userOpacity = Math.max(0.20, Math.min(1.0, parseFloat(val)));
  if (opacitySlider) opacitySlider.value = userOpacity;
  safeSetItem('stealth_opacity', userOpacity.toString());
  if (opacityDisplay) opacityDisplay.textContent = Math.round(userOpacity * 100) + '%';

  const appCont = document.querySelector('.app-container');
  if (appCont) {
    window._isPreviewingOpacity = true;
    appCont.style.opacity = Math.min(1.0, userOpacity);
    
    clearTimeout(window._opacityPreviewTimeout);
    window._opacityPreviewTimeout = setTimeout(() => {
      window._isPreviewingOpacity = false;
      updateClickThrough();
    }, 1500);
  }
}

if (opacityMinus) {
  opacityMinus.addEventListener('click', () => {
    const curr = parseFloat(opacitySlider?.value || '1.0');
    applyOpacity((curr - 0.05).toFixed(2));
  });
}
if (opacityPlus) {
  opacityPlus.addEventListener('click', () => {
    const curr = parseFloat(opacitySlider?.value || '1.0');
    applyOpacity((curr + 0.05).toFixed(2));
  });
}

if (opacitySlider) {
  opacitySlider.addEventListener('pointerdown', () => {
    isDraggingSlider = true;
    const appCont = document.querySelector('.app-container');
    if (appCont) appCont.style.opacity = Math.min(1.0, userOpacity);
  });
  opacitySlider.addEventListener('input', (e) => {
    applyOpacity(e.target.value);
  });
}

// ── Developer Stealth Mode Toggle ─────────────────────────────────────────────
let isDevStealthModeEnabled = true;
const devStealthToggleBtn = document.getElementById('dev-stealth-toggle-btn');
const stealthModeLabel = document.getElementById('stealth-mode-label');

function updateDevStealthUI(enabled) {
  isDevStealthModeEnabled = Boolean(enabled);
  safeSetItem('stealth_dev_mode_enabled', isDevStealthModeEnabled ? 'true' : 'false');

  if (window.electronAPI && window.electronAPI.setStealthMode) {
    window.electronAPI.setStealthMode(isDevStealthModeEnabled);
  }

  if (devStealthToggleBtn) {
    devStealthToggleBtn.textContent = isDevStealthModeEnabled ? 'ON' : 'OFF';
    devStealthToggleBtn.style.background = isDevStealthModeEnabled ? 'rgba(20, 184, 166, 0.15)' : 'rgba(255, 255, 255, 0.06)';
    devStealthToggleBtn.style.borderColor = isDevStealthModeEnabled ? 'rgba(20, 184, 166, 0.4)' : 'rgba(255, 255, 255, 0.15)';
    devStealthToggleBtn.style.color = isDevStealthModeEnabled ? '#2dd4bf' : 'var(--text-secondary)';
  }

  if (stealthModeLabel) {
    stealthModeLabel.textContent = isDevStealthModeEnabled ? 'ON (Protected)' : 'OFF (Normal)';
    stealthModeLabel.style.color = isDevStealthModeEnabled ? '#2dd4bf' : 'var(--text-secondary)';
  }
}

if (devStealthToggleBtn) {
  devStealthToggleBtn.addEventListener('click', () => {
    updateDevStealthUI(!isDevStealthModeEnabled);
  });
}

const fontSizeMinus = document.getElementById('font-size-minus');
const fontSizePlus = document.getElementById('font-size-plus');

function applyFontSize(val) {
  val = Math.max(8, Math.min(36, parseFloat(val)));
  if (fontSizeInput) fontSizeInput.value = val;
  safeSetItem('stealth_font_size', val.toString());
  const valPx = val + 'px';
  if (mainAnswerBlock) mainAnswerBlock.style.fontSize = valPx;
  if (mainTranscriptBlock) mainTranscriptBlock.style.fontSize = valPx;
}

if (fontSizeMinus) {
  fontSizeMinus.addEventListener('click', () => {
    const curr = parseFloat(fontSizeInput?.value || '12.5');
    applyFontSize(curr - 0.5);
  });
}
if (fontSizePlus) {
  fontSizePlus.addEventListener('click', () => {
    const curr = parseFloat(fontSizeInput?.value || '12.5');
    applyFontSize(curr + 0.5);
  });
}

if (fontSizeInput) {
  fontSizeInput.addEventListener('pointerdown', () => {
    isDraggingSlider = true;
    const appCont = document.querySelector('.app-container');
    if (appCont) appCont.style.opacity = userOpacity;
  });
  fontSizeInput.addEventListener('input', (e) => {
    applyFontSize(e.target.value);
  });
}

const shrinkBtn = document.getElementById('shrink-btn');
const diamondBtn = document.getElementById('diamond-btn');
const appContainer = document.querySelector('.app-container');

async function toggleShrunk(forceShrink = null) {
  window.isCustomResized = false;
  if (forceShrink !== null) {
    if (isShrunk === forceShrink) return;
    isShrunk = forceShrink;
  } else {
    isShrunk = !isShrunk;
  }

  if (isShrunk) {
    // Add instant-hide class to kill all CSS transitions/animations immediately
    appContainer.classList.add('instant-hide');
    appContainer.style.visibility = 'hidden';
    appContainer.style.pointerEvents = 'none';
    appContainer.style.opacity = '0';
    if (diamondBtn) {
      diamondBtn.style.display = 'flex';
      diamondBtn.style.position = 'fixed';
      diamondBtn.style.top = '4px';
      diamondBtn.style.left = '4px';
    }
    pendingProgrammaticResizes++;
    window.electronAPI.resizeWindow(48, 48, toolbarPosition, false);
    updateClickThrough(); // always interactive in diamond mode
  } else {
    isDraggingWindow = false; // Reset drag flag immediately on expand
    justExpanded = true;
    clearTimeout(justExpandedTimeout);
    justExpandedTimeout = setTimeout(() => {
      justExpanded = false;
      updateClickThrough(); // re-evaluate once guard expires
    }, 300);

    if (diamondBtn) {
      diamondBtn.style.display = 'none';
    }
    // Restore appContainer visibility
    appContainer.style.transition = ''; // restore CSS transition for smooth expand
    appContainer.classList.remove('instant-hide');
    appContainer.style.visibility = '';
    appContainer.style.pointerEvents = '';
    appContainer.style.opacity = '';
    appContainer.style.display = 'flex';

    if (setupView && setupView.style.display !== 'none') {
      pendingProgrammaticResizes++;
      window.electronAPI.resizeWindow(600, 580, 'top', false);
    } else {
      updateWindowSize();
    }

    // Smoothly fade in content once the OS has completed window bounds expansion
    setTimeout(() => {
      if (document.body.classList.contains('stealth-active')) {
        appContainer.style.opacity = userOpacity;
        document.body.classList.add('hover-active');
      } else {
        appContainer.style.opacity = '1.0';
      }
      updateClickThrough();
    }, 100);
  }
}

const setupShrinkBtn = document.getElementById('setup-shrink-btn');
if (setupShrinkBtn) {
  setupShrinkBtn.addEventListener('click', () => {
    toggleShrunk(true);
  });
}

if (shrinkBtn) {
  shrinkBtn.addEventListener('click', () => {
    toggleShrunk(true);
  });
}

if (diamondBtn) {
  let _diamondDragging = false;
  let _diamondStartX = 0;
  let _diamondStartY = 0;
  let _diamondInitialWinX = 0;
  let _diamondInitialWinY = 0;
  const DRAG_THRESHOLD = 4; // px — below this = click, above = drag

  diamondBtn.addEventListener('pointerdown', (e) => {
    _diamondDragging = false;
    _diamondStartX = e.screenX;
    _diamondStartY = e.screenY;
    _diamondInitialWinX = window.screenX;
    _diamondInitialWinY = window.screenY;
    diamondBtn.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  diamondBtn.addEventListener('pointermove', (e) => {
    if (!diamondBtn.hasPointerCapture(e.pointerId)) return;
    const totalMoved = Math.abs(e.screenX - _diamondStartX) + Math.abs(e.screenY - _diamondStartY);
    if (totalMoved > DRAG_THRESHOLD) {
      _diamondDragging = true;
      diamondBtn
    }
    if (_diamondDragging) {
      const dx = e.screenX - _diamondStartX;
      const dy = e.screenY - _diamondStartY;
      const targetX = _diamondInitialWinX + dx;
      const targetY = _diamondInitialWinY + dy;
      window.electronAPI.moveWindowAbsolute(targetX, targetY);
    }
    e.preventDefault();
  });

  diamondBtn.addEventListener('pointerup', (e) => {
    diamondBtn
    if (diamondBtn.hasPointerCapture(e.pointerId)) {
      diamondBtn.releasePointerCapture(e.pointerId);
    }
    if (!_diamondDragging) {
      // It was a tap/click — expand the window
      toggleShrunk(false);
    }
    _diamondDragging = false;
    e.preventDefault();
  });
}

const expandAnswerBtn = document.getElementById('expand-answer-btn');

if (expandAnswerBtn) {
  let isBtnDragging = false;
  let startScreenX = 0;
  let startScreenY = 0;
  let startWinX = 0;    // window.screenX at drag start
  let startWinY = 0;    // window.screenY at drag start
  let startWinW = 0;    // window.innerWidth at drag start
  let startWinH = 0;    // window.innerHeight at drag start
  const RESIZE_THRESHOLD = 4;

  // ── Fixed non-answer overhead (px): toolbar-margin(56) + panelHeader(44) + panelContent-padding(32) + transcriptRow(60) + gap(10) + navRow(28) + inputArea(56) = 286
  // When transcript hidden: 286 - 60 - 10 = 216
  const OVERHEAD_WITH_TRANSCRIPT = 286;
  const OVERHEAD_NO_TRANSCRIPT = 216;

  expandAnswerBtn.addEventListener('pointerdown', (e) => {
    isBtnDragging = false;
    startScreenX = e.screenX;
    startScreenY = e.screenY;
    startWinX = window.screenX;
    startWinY = window.screenY;
    startWinW = window.innerWidth || currentWidth || 640;
    startWinH = window.innerHeight || currentHeight || 380;
    expandAnswerBtn.setPointerCapture(e.pointerId);
    e.preventDefault();
  });

  expandAnswerBtn.addEventListener('pointermove', (e) => {
    if (!expandAnswerBtn.hasPointerCapture(e.pointerId)) return;
    const dx = e.screenX - startScreenX;
    const dy = e.screenY - startScreenY;
    if (Math.abs(dx) > RESIZE_THRESHOLD || Math.abs(dy) > RESIZE_THRESHOLD) {
      isBtnDragging = true;
    }
    if (!isBtnDragging) { e.preventDefault(); return; }

    // ── Screen limits (no going outside the screen)
    const screenW = window.screen.availWidth;
    const screenH = window.screen.availHeight;
    const screenLeft = 0;
    const screenTop = 0;

    // ── 4-direction expansion: window grows outward from its initial edges
    // RIGHT: dx > 0 → right edge moves right
    // LEFT:  dx < 0 → left edge moves left (x decreases, width increases)
    // DOWN:  dy > 0 → bottom edge moves down
    // UP:    dy < 0 → top edge moves up (y decreases, height increases)
    let newX = startWinX;
    let newY = startWinY;
    let newW = startWinW;
    let newH = startWinH;

    if (dx > 0) {
      // Expand right
      newW = startWinW + dx;
    } else {
      // Expand left
      newX = startWinX + dx;
      newW = startWinW - dx; // dx is negative so this grows
    }

    if (dy > 0) {
      // Expand down
      newH = startWinH + dy;
    } else {
      // Expand up
      newY = startWinY + dy;
      newH = startWinH - dy; // dy is negative so this grows
    }

    // ── Clamp to screen bounds — never go outside
    if (newX < screenLeft) { newW -= (screenLeft - newX); newX = screenLeft; }
    if (newY < screenTop)  { newH -= (screenTop - newY);  newY = screenTop;  }
    if (newX + newW > screenLeft + screenW) newW = screenLeft + screenW - newX;
    if (newY + newH > screenTop + screenH)  newH = screenTop + screenH - newY;

    // ── Enforce minimum size
    newW = Math.max(360, newW);
    newH = Math.max(200, newH);

    // ── Answer block: fill available vertical space
    const transcriptSectionDrag = document.querySelector('.block-container:first-child');
    const overhead = isAnswerExpanded ? OVERHEAD_NO_TRANSCRIPT : OVERHEAD_WITH_TRANSCRIPT;
    if (isAnswerExpanded && transcriptSectionDrag) transcriptSectionDrag.style.display = 'none';

    const newAnswerH = Math.max(60, newH - overhead);
    const answerBlock = document.getElementById('answer-block');
    if (answerBlock) {
      answerBlock.style.height = newAnswerH + 'px';
      answerBlock.style.maxHeight = newAnswerH + 'px';
      answerBlock.style.overflowY = 'auto';
    }
    const codeDisplayPre = document.getElementById('code-display');
    if (codeDisplayPre) {
      codeDisplayPre.style.height = newAnswerH + 'px';
      codeDisplayPre.style.maxHeight = newAnswerH + 'px';
      codeDisplayPre.style.overflowY = 'auto';
    }

    // ── Panels container width tracks window width
    const panelsContainerDrag = document.getElementById('panels');
    if (panelsContainerDrag) {
      panelsContainerDrag.style.width = (newW - 20) + 'px';
      panelsContainerDrag.style.maxWidth = 'none';
      panelsContainerDrag.style.height = 'auto';
      panelsContainerDrag.style.maxHeight = 'none';
    }

    window.isCustomResized = true;
    currentHeight = Math.round(newH);
    currentWidth = Math.round(newW);

    // Pass explicit x,y so window repositions correctly (for up/left expansion)
    window.electronAPI.resizeWindow(Math.round(newW), Math.round(newH), toolbarPosition, false, Math.round(newX), Math.round(newY));
    e.preventDefault();
  });

  expandAnswerBtn.addEventListener('pointerup', (e) => {
    if (expandAnswerBtn.hasPointerCapture(e.pointerId)) {
      expandAnswerBtn.releasePointerCapture(e.pointerId);
    }
    if (!isBtnDragging) {
      // ── Tap/click: toggle compact ↔ expanded
      window.isCustomResized = false;
      currentHeight = 0;  // force updateWindowSize to recalculate
      currentWidth = 0;
      isAnswerExpanded = !isAnswerExpanded;
      expandAnswerBtn.classList.toggle('active', isAnswerExpanded);

      // Reset ALL drag-imposed panel styles so CSS defaults fully restore
      const panelsContainerReset = document.getElementById('panels');
      if (panelsContainerReset) {
        panelsContainerReset.style.width = '';
        panelsContainerReset.style.maxWidth = '';
        panelsContainerReset.style.overflow = '';
        panelsContainerReset.style.height = 'auto';
        panelsContainerReset.style.maxHeight = 'none';
      }

      // Restore transcript section visibility
      const transcriptSectionReset = document.querySelector('.block-container:first-child');
      if (transcriptSectionReset) transcriptSectionReset.style.display = '';

      const askBadge = document.getElementById('badge-ask');
      if (askBadge) askBadge.style.display = isAnswerExpanded ? 'inline-block' : 'none';

      const answerBlock = document.getElementById('answer-block');
      if (isAnswerExpanded) {
        expandAnswerBtn.style.color = 'var(--accent-ai)';
        expandAnswerBtn.style.borderColor = 'rgba(20, 184, 166, 0.4)';
        expandAnswerBtn.style.background = 'rgba(20, 184, 166, 0.12)';
        if (answerBlock) { answerBlock.style.height = '170px'; answerBlock.style.maxHeight = '170px'; }
      } else {
        expandAnswerBtn.style.color = 'var(--text-secondary)';
        expandAnswerBtn.style.borderColor = 'rgba(255, 255, 255, 0.08)';
        expandAnswerBtn.style.background = 'rgba(255, 255, 255, 0.04)';
        if (answerBlock) { answerBlock.style.height = '100px'; answerBlock.style.maxHeight = '100px'; }
      }
      updateWindowSize();
    }
    isBtnDragging = false;
    e.preventDefault();
  });
}


const clearAnswerBtn = document.getElementById('clear-answer-btn');
if (clearAnswerBtn) {
  clearAnswerBtn.addEventListener('click', () => {
    answerHistory = [];
    currentAnswerIndex = -1;
    renderActiveAnswer();
  });
}

// Remove titles to prevent OS tooltips in stealth mode
function toggleStealthTooltips(stealthActive) {
  const interactiveElements = document.querySelectorAll('.interactive, button, a, [title]');
  interactiveElements.forEach(el => {
    if (stealthActive) {
      const title = el.getAttribute('title');
      if (title) {
        el.setAttribute('data-title', title);
        el.removeAttribute('title');
      }
    } else {
      const dataTitle = el.getAttribute('data-title');
      if (dataTitle) {
        el.setAttribute('title', dataTitle);
        el.removeAttribute('data-title');
      }
    }
  });
}

// Key listener to temporarily toggle/disable stealth hover-hide behavior
window.addEventListener('keydown', (e) => {
  // Intercept Chromium native zoom and map to custom stealth_font_size
  if (e.ctrlKey) {
    if (e.key === '-' || e.key === '_') {
      e.preventDefault();
      if (window.electronAPI && window.electronAPI.setZoomFactor) window.electronAPI.setZoomFactor(1.0);
      document.body.style.zoom = '1.0'; // reset any remaining zoom
      let currentFont = parseFloat(safeGetItem('stealth_font_size') || '12.5');
      applyFontSize(currentFont - 1.0);
      if (typeof updateWindowSize === 'function') updateWindowSize();
      return;
    } else if (e.key === '=' || e.key === '+') {
      e.preventDefault();
      if (window.electronAPI && window.electronAPI.setZoomFactor) window.electronAPI.setZoomFactor(1.0);
      document.body.style.zoom = '1.0';
      let currentFont = parseFloat(safeGetItem('stealth_font_size') || '12.5');
      applyFontSize(currentFont + 1.0);
      if (typeof updateWindowSize === 'function') updateWindowSize();
      return;
    } else if (e.key === '0') {
      e.preventDefault();
      if (window.electronAPI && window.electronAPI.setZoomFactor) window.electronAPI.setZoomFactor(1.0);
      document.body.style.zoom = '1.0';
      applyFontSize(12.5); // Default font size
      if (typeof updateWindowSize === 'function') updateWindowSize();
      return;
    }
  }

  // Load and process dynamic shortcuts
  const checkShortcut = (action, e) => {
    const config = window.appShortcuts && window.appShortcuts[action];
    if (!config) return false;
    const keyMatch = e.key.toLowerCase() === config.key.toLowerCase() || e.code === config.key || (config.key === 'Space' && e.code === 'Space');
    return keyMatch && (!!config.ctrl === e.ctrlKey) && (!!config.shift === e.shiftKey) && (!!config.alt === e.altKey);
  };

  // Capture Shortcut
  if (checkShortcut('capture', e)) {
    const captureBtn = document.getElementById('capture-btn');
    if (captureBtn) captureBtn.click();
    e.preventDefault();
  }

  // Scroll Answer block
  if (checkShortcut('scrollUp', e)) {
    const answerBlock = document.getElementById('answer-block');
    if (answerBlock) {
      answerBlock.scrollTop -= 80;
      e.preventDefault();
    }
  }

  if (checkShortcut('scrollDown', e)) {
    const answerBlock = document.getElementById('answer-block');
    if (answerBlock) {
      answerBlock.scrollTop += 80;
      e.preventDefault();
    }
  }

  // Prev Question / Answer Shortcut
  if (checkShortcut('prevAnswer', e)) {
    const prevBtn = document.getElementById('prev-answer-btn');
    if (prevBtn) prevBtn.click();
    e.preventDefault();
  }

  // Next Question / Answer Shortcut
  if (checkShortcut('nextAnswer', e)) {
    const nextBtn = document.getElementById('next-answer-btn');
    if (nextBtn) nextBtn.click();
    e.preventDefault();
  }

  // Answer Shortcut
  if (checkShortcut('answer', e)) {
    const aiSendBtn = document.getElementById('ai-send');
    const aiAnswerBtn = document.getElementById('ai-answer-btn');
    const aiInput = document.getElementById('ai-input');
    if (aiInput && document.activeElement === aiInput && aiInput.value.trim() !== '') {
      if (aiSendBtn) aiSendBtn.click();
    } else {
      if (aiAnswerBtn) aiAnswerBtn.click();
    }
    e.preventDefault();
  }

  // Toggle stealth hover check with F8 or Ctrl+Shift+M
  if (e.key === 'F8' || (e.ctrlKey && e.shiftKey && e.key.toUpperCase() === 'M')) {
    const isSessionActive = document.body.classList.contains('stealth-active');

    if (isSessionActive || !isStealthHoverEnabled) {
      isStealthHoverEnabled = !isStealthHoverEnabled;
      console.log('[Stealth Hover Toggle] isStealthHoverEnabled =', isStealthHoverEnabled);

      if (isStealthHoverEnabled) {
        document.body.classList.add('stealth-active');
        // Let normal mouse move handle ignore events
      } else {
        document.body.classList.remove('stealth-active');
        window.electronAPI.setIgnoreMouseEvents(false);
        document.querySelector('.app-container').style.opacity = userOpacity;
      }
    }
  }
});

// Capture override: Ctrl + click on any button
document.addEventListener('click', (e) => {
  if (e.ctrlKey && e.target.closest('button')) {
    const captureBtn = document.getElementById('capture-btn');
    if (captureBtn && e.target.closest('button') !== captureBtn) {
      captureBtn.click();
      e.preventDefault();
      e.stopPropagation();
    }
  }
}, true);


// ── Shortcut Management Logic ───────────────────────────────────────────────
window.appShortcuts = {
  capture: { ctrl: true, shift: false, alt: false, key: 'Space' },
  answer: { ctrl: true, shift: false, alt: false, key: 'Enter' },
  scrollUp: { ctrl: true, shift: false, alt: false, key: 'ArrowUp' },
  scrollDown: { ctrl: true, shift: false, alt: false, key: 'ArrowDown' },
  prevAnswer: { ctrl: true, shift: false, alt: false, key: 'ArrowLeft' },
  nextAnswer: { ctrl: true, shift: false, alt: false, key: 'ArrowRight' }
};

function formatShortcut(config) {
  let parts = [];
  if (config.ctrl) parts.push('Ctrl');
  if (config.shift) parts.push('Shift');
  if (config.alt) parts.push('Alt');
  let keyName = config.key === ' ' ? 'Space' : config.key;
  if (keyName === 'ArrowUp') keyName = '↑';
  if (keyName === 'ArrowDown') keyName = '↓';
  if (keyName === 'ArrowLeft') keyName = '←';
  if (keyName === 'ArrowRight') keyName = '→';
  if (!['↑', '↓', '←', '→'].includes(keyName)) {
    keyName = keyName.charAt(0).toUpperCase() + keyName.slice(1);
  }
  parts.push(keyName);
  return parts.join('+');
}

function updateShortcutUI() {
  // Update Badges
  const captureBadge = document.getElementById('badge-capture');
  const answerBadge = document.getElementById('badge-answer');
  const askBadge = document.getElementById('badge-ask');
  const scrollBadge = document.getElementById('badge-scroll');
  const prevBadge = document.getElementById('badge-prev-answer');
  const nextBadge = document.getElementById('badge-next-answer');

  if (captureBadge) captureBadge.textContent = formatShortcut(window.appShortcuts.capture);
  if (answerBadge) answerBadge.textContent = formatShortcut(window.appShortcuts.answer);
  if (askBadge) {
    askBadge.textContent = formatShortcut(window.appShortcuts.answer);
    askBadge.style.display = (typeof isAnswerExpanded !== 'undefined' && isAnswerExpanded) ? 'inline-block' : 'none';
  }
  if (scrollBadge) scrollBadge.textContent = `${formatShortcut(window.appShortcuts.scrollUp)}/${formatShortcut(window.appShortcuts.scrollDown)}`;
  if (prevBadge && window.appShortcuts.prevAnswer) prevBadge.textContent = formatShortcut(window.appShortcuts.prevAnswer);
  if (nextBadge && window.appShortcuts.nextAnswer) nextBadge.textContent = formatShortcut(window.appShortcuts.nextAnswer);

  // Update Recorders in Settings
  const recCapture = document.getElementById('set-shortcut-capture');
  const recAnswer = document.getElementById('set-shortcut-answer');
  const recScrollUp = document.getElementById('set-shortcut-scrollUp');
  const recScrollDown = document.getElementById('set-shortcut-scrollDown');
  const recPrevAnswer = document.getElementById('set-shortcut-prevAnswer');
  const recNextAnswer = document.getElementById('set-shortcut-nextAnswer');

  if (recCapture && !recCapture.classList.contains('recording')) recCapture.textContent = formatShortcut(window.appShortcuts.capture);
  if (recAnswer && !recAnswer.classList.contains('recording')) recAnswer.textContent = formatShortcut(window.appShortcuts.answer);
  if (recScrollUp && !recScrollUp.classList.contains('recording')) recScrollUp.textContent = formatShortcut(window.appShortcuts.scrollUp);
  if (recScrollDown && !recScrollDown.classList.contains('recording')) recScrollDown.textContent = formatShortcut(window.appShortcuts.scrollDown);
  if (recPrevAnswer && !recPrevAnswer.classList.contains('recording') && window.appShortcuts.prevAnswer) recPrevAnswer.textContent = formatShortcut(window.appShortcuts.prevAnswer);
  if (recNextAnswer && !recNextAnswer.classList.contains('recording') && window.appShortcuts.nextAnswer) recNextAnswer.textContent = formatShortcut(window.appShortcuts.nextAnswer);
}

function loadShortcuts() {
  const saved = safeGetItem('stealth_shortcuts');
  if (saved) {
    try {
      window.appShortcuts = { ...window.appShortcuts, ...JSON.parse(saved) };
    } catch (e) { console.error('Failed to parse shortcuts', e); }
  }
  updateShortcutUI();
}

// Bind Recorder logic
function setupRecorder(btnId, actionKey) {
  const btn = document.getElementById(btnId);
  if (!btn) return;

  btn.addEventListener('click', (e) => {
    btn.classList.add('recording');
    btn.textContent = 'Press Key...';
    e.stopPropagation();

    const handler = (evt) => {
      evt.preventDefault();
      evt.stopPropagation();

      // Ignore bare modifiers
      if (['Control', 'Shift', 'Alt', 'Meta'].includes(evt.key)) return;

      let keyToSave = evt.key === ' ' ? 'Space' : evt.key;

      window.appShortcuts[actionKey] = {
        ctrl: evt.ctrlKey,
        shift: evt.shiftKey,
        alt: evt.altKey,
        key: keyToSave
      };

      safeSetItem('stealth_shortcuts', JSON.stringify(window.appShortcuts));

      btn.classList.remove('recording');
      window.removeEventListener('keydown', handler, true);
      updateShortcutUI();
    };

    window.addEventListener('keydown', handler, true);

    // Cancel on click away
    const cancelHandler = () => {
      btn.classList.remove('recording');
      updateShortcutUI();
      window.removeEventListener('keydown', handler, true);
      window.removeEventListener('click', cancelHandler, true);
    };
    // Delay adding click listener so we don't trigger immediately on the click that opened it
    setTimeout(() => window.addEventListener('click', cancelHandler, true), 10);
  });
}

// Initialize on boot
setTimeout(() => {
  loadShortcuts();
  setupRecorder('set-shortcut-capture', 'capture');
  setupRecorder('set-shortcut-answer', 'answer');
  setupRecorder('set-shortcut-scrollUp', 'scrollUp');
  setupRecorder('set-shortcut-scrollDown', 'scrollDown');
  setupRecorder('set-shortcut-prevAnswer', 'prevAnswer');
  setupRecorder('set-shortcut-nextAnswer', 'nextAnswer');
}, 500);

// ── Inactivity Auto-Kill / Session Safety Manager ─────────────────────────────
let lastUserActivityTime = Date.now();

function registerUserActivity() {
  lastUserActivityTime = Date.now();
}

['mousemove', 'mousedown', 'keydown', 'pointerdown', 'touchstart'].forEach(evt => {
  window.addEventListener(evt, registerUserActivity, { passive: true });
});

// Check inactivity every 30 seconds
setInterval(() => {
  const idleMs = Date.now() - lastUserActivityTime;
  const isSessionActive = document.body.classList.contains('stealth-active');

  if (isSessionActive && idleMs >= 15 * 60 * 1000) {
    console.warn('[Inactivity Manager] Session idle for 15+ minutes — auto-ending active session...');
    if (typeof stopSessionBtn !== 'undefined' && stopSessionBtn) stopSessionBtn.click();
  } else if (!isSessionActive && idleMs >= 30 * 60 * 1000) {
    console.warn('[Inactivity Manager] App idle for 30+ minutes — auto-closing application...');
    if (window.electronAPI && window.electronAPI.quitApp) window.electronAPI.quitApp();
  }
}, 30000);

