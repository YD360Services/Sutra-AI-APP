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
const WIDTH = 850;
const COLLAPSED_HEIGHT = 56;
const EXPANDED_HEIGHT = 664; // Toolbar 48px + margin 8px + panels 600px + padding/buffer = 664px
const MAX_HEIGHT = 1000; // Hard cap — never exceeded


let currentWidth = WIDTH;
let currentHeight = EXPANDED_HEIGHT;
// Use a counter instead of a boolean so nested/overlapping programmatic
// resizes don't accidentally clear each other's guards.
let pendingProgrammaticResizes = 0;
let isDraggingWindow = false;

let resizeDebounceTimeout;
window.addEventListener('resize', () => {
  // Ignore any resize event that WE triggered programmatically or that
  // arrives while the user is physically dragging the window.
  if (pendingProgrammaticResizes > 0 || isDraggingWindow) {
    if (pendingProgrammaticResizes > 0) pendingProgrammaticResizes--;
    return;
  }
  
  // Track native window expansion/resizing initiated by the user
  clearTimeout(resizeDebounceTimeout);
  resizeDebounceTimeout = setTimeout(() => {
    currentWidth = window.innerWidth;
  }, 100);
});

// Active state tracking
let activeTab = null; // 'ai', 'code', or null
let toolbarPosition = 'top'; // 'top' or 'bottom'
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
let sessionToken = '';     // JWT from backend
let lastAnswerOffset = 0;  // transcript cursor: char offset of last answered position

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
        console.log('[Stealth Settings] User synchronized successfully. Mapped USER_ID:', USER_ID);
        // Reload recent sessions if page is currently visible
        if (typeof recentSessionsView !== 'undefined' && recentSessionsView && recentSessionsView.style.display !== 'none') {
          loadRecentSessions();
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
    banner.style.background = 'rgba(139,92,246,0.06)';
    banner.style.borderColor = 'rgba(139,92,246,0.2)';
    banner.style.color = '#a78bfa';
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

    // Clear existing options (except the first placeholder)
    while (setupResumeSelect.options.length > 1) setupResumeSelect.remove(1);
    // Clear all optgroups and options from doc select (keep only first placeholder option)
    while (setupDocSelect.options.length > 1) setupDocSelect.remove(1);
    const existingGroups = setupDocSelect.querySelectorAll('optgroup');
    existingGroups.forEach(g => g.remove());

    let resumeError = false;
    let docError = false;

    // Fetch resumes list
    try {
      const resResumes = await fetch(`${base}/api/resumes?user_id=${USER_ID}`);
      if (resResumes.ok) {
        backendResumes = await resResumes.json();
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
      const resDocs = await fetch(`${base}/api/knowledge?user_id=${USER_ID}`);
      if (resDocs.ok) {
        backendDocs = await resDocs.json();
        console.log(`[Stealth] Loaded ${backendDocs.length} docs/prompts`);

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

    // Add Upload options at bottom of each dropdown
    const resumeUploadOpt = document.createElement('option');
    resumeUploadOpt.value = '__upload__';
    resumeUploadOpt.textContent = '⬆ Upload new resume...';
    setupResumeSelect.appendChild(resumeUploadOpt);

    const docUploadOpt = document.createElement('option');
    docUploadOpt.value = '__upload__';
    docUploadOpt.textContent = '⬆ Upload new document...';
    setupDocSelect.appendChild(docUploadOpt);

    if (resumeError || docError) {
      const parts = [];
      if (resumeError) parts.push('resumes');
      if (docError) parts.push('documents');
      setDropdownStatus('error', `⚠️ Could not load ${parts.join(' & ')} — backend may be offline. Click ↺ Retry.`);
    } else {
      const total = backendResumes.length + backendDocs.length;
      setDropdownStatus('success', `✓ Loaded ${backendResumes.length} resume(s) and ${backendDocs.length} document(s)`);
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
    if (offlineUserContext.job_description) setupJd.value = offlineUserContext.job_description;
    console.log('[Stealth] Populated setup form from local context successfully');
  } catch (e) {
    console.error('[Stealth] Failed to load local L4 context:', e.message);
  }

  // Load dropdown options
  await loadDropdowns();

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
  return { bg: 'rgba(139,92,246,0.12)', border: 'rgba(139,92,246,0.3)', color: '#a78bfa', label: 'Interview+Coding' };
}

// Show/hide recent sessions page
async function showRecentSessionsPage() {
  setupView.style.display = 'none';
  recentSessionsView.style.display = 'flex';
  // Resize window to give sessions table room
  pendingProgrammaticResizes++;
  window.electronAPI.resizeWindow(WIDTH, 530, 'top', false);
  window.electronAPI.setIgnoreMouseEvents(false, {});
  await loadRecentSessions();
}

function hideRecentSessionsPage() {
  recentSessionsView.style.display = 'none';
  setupView.style.display = 'flex';
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
      row.style.cssText = 'display:grid;grid-template-columns:2fr 2.3fr 1.5fr 1fr 0.8fr 1.2fr 2.2fr;gap:4px;align-items:center;padding:6px 6px;border-radius:7px;border:1px solid rgba(255,255,255,0.03);transition:background 0.15s;cursor:pointer;';
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
            <button class="session-transcript-btn interactive" data-id="${s.id}" title="View Transcript" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(139,92,246,0.12);border:1px solid rgba(139,92,246,0.25);color:#a78bfa;cursor:pointer;outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
            </button>
            <!-- Summary Button -->
            <button class="session-summary-btn interactive" data-id="${s.id}" title="View Summary" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.25);color:#4ade80;cursor:pointer;outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
            </button>
            <!-- Edit Button -->
            <button class="session-edit-btn interactive" data-id="${s.id}" title="Edit Config" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);color:rgba(255,255,255,0.7);cursor:pointer;outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
            </button>
            <!-- Delete Button -->
            <button class="session-delete-btn interactive" data-id="${s.id}" title="Delete Session" style="display:flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);color:#ef4444;cursor:pointer;outline:none;transition:all 0.15s;">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
        </div>
      `;

      row.addEventListener('mouseenter', () => row.style.background = 'rgba(255,255,255,0.04)');
      row.addEventListener('mouseleave', () => row.style.background = '');

      // Helper to pre-fill wizard
      const prefillWizard = () => {
        if (s.company_name && setupCompany) setupCompany.value = s.company_name;
        if (s.role_name && setupRole) setupRole.value = s.role_name;
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
          const res = await fetch(`${base}/api/sessions/${s.id}/transcripts`);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const blocks = await res.json();

          if (!blocks || blocks.length === 0) {
            showModalOverlay(`Transcript — ${title}`, `<div style="text-align:center;padding:40px 0;color:var(--text-muted);font-size:11px;">No transcript records found for this session.</div>`);
            return;
          }

          const html = blocks.map(b => {
            const speakerLabel = b.speaker === 'interviewer' ? 'Interviewer' : 'You';
            const speakerColor = b.speaker === 'interviewer' ? '#a78bfa' : '#4ade80';
            const timeStr = b.created_at ? new Date(b.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
            return `
              <div style="margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.03);">
                <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 3px;">
                  <span style="font-size: 9px; font-weight: 700; color: ${speakerColor}; text-transform: uppercase; letter-spacing: 0.3px;">${speakerLabel}</span>
                  <span style="font-size: 8px; color: var(--text-muted); font-family: monospace;">${timeStr}</span>
                </div>
                <div style="white-space: pre-wrap; font-size: 10.5px; line-height: 1.5; color: rgba(255,255,255,0.9);">${b.content}</div>
              </div>
            `;
          }).join('');

          showModalOverlay(`Transcript — ${title}`, html);
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
              <button id="confirm-delete-yes" style="padding:8px 22px;background:rgba(239,68,68,0.2);border:1px solid rgba(239,68,68,0.5);color:#fca5a5;border-radius:7px;cursor:pointer;font-size:11px;">Delete</button>
              <button id="confirm-delete-no" style="padding:8px 22px;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);color:rgba(255,255,255,0.7);border-radius:7px;cursor:pointer;font-size:11px;">Cancel</button>
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
    + `<button onclick="this.parentElement.remove()" style="background:none;border:none;color:#fca5a5;cursor:pointer;font-size:14px;padding:0 0 0 6px;line-height:1;">✕</button>`;
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
      <button id="modal-close-btn" class="interactive" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); color: #fff; border-radius: 6px; padding: 4px 10px; font-size: 10px; font-weight: 600; cursor: pointer; outline: none; transition: background 0.2s;">
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
  quitAppBtn.addEventListener('click', () => window.electronAPI.quitApp());
}

// Recent-view quit button
const recentViewQuitBtn = document.getElementById('recent-view-quit-btn');
if (recentViewQuitBtn) {
  recentViewQuitBtn.addEventListener('click', () => window.electronAPI.quitApp());
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
  }
});

setupDocSelect.addEventListener('change', () => {
  if (setupDocSelect.value === '__upload__') {
    setupDocFile.click();
    setupDocSelect.value = '';
  }
});

// File upload event handlers
setupResumeFile.addEventListener('change', async () => {
  if (!setupResumeFile.files.length) return;
  const file = setupResumeFile.files[0];
  const formData = new FormData();
  formData.append('file', file);
  formData.append('user_id', USER_ID);

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
  formData.append('user_id', USER_ID);

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
      ind.num.style.background = 'var(--accent-chat)';
      ind.num.style.borderColor = 'var(--accent-chat)';
      ind.num.textContent = stepIdx;
      ind.num.style.color = '#fff';
      ind.text.style.color = 'var(--text-primary)';
    } else {
      ind.num.style.background = 'rgba(255,255,255,0.04)';
      ind.num.style.borderColor = 'rgba(255,255,255,0.08)';
      ind.num.textContent = stepIdx;
      ind.num.style.color = 'var(--text-muted)';
      ind.text.style.color = 'var(--text-muted)';
    }
  });

  stepLine1.style.background = currentStep > 1 ? 'var(--accent-chat)' : 'rgba(255,255,255,0.06)';
  stepLine2.style.background = currentStep > 2 ? 'var(--accent-chat)' : 'rgba(255,255,255,0.06)';

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

// Session timer variables
let sessionTimerInterval = null;
let sessionSecondsElapsed = 0;
const sessionTimerElement = document.getElementById('session-timer');

function startSessionTimer() {
  if (sessionTimerInterval) clearInterval(sessionTimerInterval);
  sessionSecondsElapsed = 0;
  if (sessionTimerElement) {
    sessionTimerElement.textContent = '00:00';
    sessionTimerElement.style.display = 'inline-block';
  }
  sessionTimerInterval = setInterval(() => {
    sessionSecondsElapsed++;
    const m = Math.floor(sessionSecondsElapsed / 60).toString().padStart(2, '0');
    const s = (sessionSecondsElapsed % 60).toString().padStart(2, '0');
    if (sessionTimerElement) {
      sessionTimerElement.textContent = `${m}:${s}`;
    }
  }, 1000);
}

function stopSessionTimer() {
  if (sessionTimerInterval) {
    clearInterval(sessionTimerInterval);
    sessionTimerInterval = null;
  }
  sessionSecondsElapsed = 0;
  if (sessionTimerElement) {
    sessionTimerElement.style.display = 'none';
  }
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
        console.log(`[Backend] Session created: ${session.session_id}`);
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

  // Hide cursor from screen-share capture while in stealth overlay mode
  document.body.classList.add('stealth-active');
  isStealthHoverEnabled = true;
  toggleStealthTooltips(true);
  document.querySelector('.app-container').style.opacity = userOpacity;

  // Load saved bounds if any
  const savedState = await window.electronAPI.getSavedBounds();
  if (savedState) {
    currentWidth = savedState.width || WIDTH;
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
    // Collapse window to default 680x56 collapsed state
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
  // Expand window back to 500x530 setup state
  pendingProgrammaticResizes++;
  window.electronAPI.resizeWindow(500, 530, 'top', true);

  // Close any active panel/tabs
  if (activeTab) {
    const activePanel = aiPanel;
    activePanel.classList.remove('active');
    panelsContainer.classList.remove('active');
    activeTab = null;
  }

  // Reset session & save ending duration/status to backend
  if (sessionToken) {
    try {
      await window.electronAPI.updateBackendSession(sessionToken, {
        status: 'completed',
        duration_seconds: sessionSecondsElapsed
      });
      await window.electronAPI.resetSessionMemory(sessionToken);
    } catch (e) {
      console.error('[Stealth] Failed to complete session on backend:', e.message);
    }
    sessionToken = '';
  }

  // Reset step wizard back to Step 1
  currentStep = 1;
  updateWizardView();

  // Clear answer history
  answerHistory = [];
  currentAnswerIndex = -1;
  renderActiveAnswer();

  // Restore cursor visibility when exiting stealth mode
  document.body.classList.remove('stealth-active');
  toggleStealthTooltips(false);
  document.body.classList.remove('hover-active');
  document.querySelector('.app-container').style.opacity = 1.0;

  // Stop live session ticking timer
  stopSessionTimer();

  // Switch views
  toolbarView.style.display = 'none';
  setupView.style.display = 'flex';

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
let startWidth, startHeight;
let startMouseX, startMouseY;

window.addEventListener('mouseleave', () => {
  if (isShrunk || isResizingPanel) return;
  if (document.body.classList.contains('stealth-active') && isStealthHoverEnabled) {
    document.querySelector('.app-container').style.opacity = userOpacity;
    document.body.classList.remove('hover-active');
  }
});

window.addEventListener('pointerup', (e) => {
  if (isResizingPanel) {
    isResizingPanel = false;
    safeSetItem('stealth_panelWidth', currentWidth);
    safeSetItem('stealth_panelHeight', currentHeight);
    
    // Release pointer capture
    const expandAnswerBtn = document.getElementById('expand-answer-btn');
    if (expandAnswerBtn) {
      try {
        expandAnswerBtn.releasePointerCapture(e.pointerId);
      } catch (err) {}
    }
  }
  isDraggingWindow = false;
});

window.addEventListener('pointermove', (e) => {
  // Handle panel resizing
  if (isResizingPanel) {
    const dx = e.screenX - startMouseX;
    const dy = e.screenY - startMouseY;
    
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      isDragClick = true;
    }
    
    // Scale delta direction depending on where the toolbar is docked
    let dxSigned = dx;
    let dySigned = dy;
    if (toolbarPosition === 'bottom') {
      dySigned = -dy; // dragging upwards (negative dy) expands panels height
    }
    if (toolbarPosition === 'right') {
      dxSigned = -dx; // dragging leftwards (negative dx) expands panels width
    }
    
    const newWidth = Math.max(600, Math.min(1400, startWidth + dxSigned));
    const newHeight = Math.max(200, Math.min(MAX_HEIGHT, startHeight + dySigned));
    
    currentWidth = newWidth;
    currentHeight = newHeight;

    const panelsContainer = document.getElementById('panels-container');
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

    pendingProgrammaticResizes++;
    window.electronAPI.resizeWindow(newWidth, newHeight, toolbarPosition, false);
    return;
  }

  if (isShrunk) {
    window.electronAPI.setIgnoreMouseEvents(false);
    return;
  }
  
  const inStealth = document.body.classList.contains('stealth-active') && isStealthHoverEnabled;
  if (!inStealth) {
    window.electronAPI.setIgnoreMouseEvents(false);
    return;
  }

  const element = document.elementFromPoint(e.clientX, e.clientY);
  if (element && (element.closest('.interactive') || element.closest('.panels-container'))) {
    window.electronAPI.setIgnoreMouseEvents(false);
    document.querySelector('.app-container').style.opacity = userOpacity;
    document.body.classList.add('hover-active');
  } else {
    window.electronAPI.setIgnoreMouseEvents(true, { forward: true });
    document.querySelector('.app-container').style.opacity = 0.05;
    document.body.classList.remove('hover-active');
  }
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
  // NEVER resize while user is dragging — it causes the window to expand
  if (isDraggingWindow) return;

  // If in setup wizard view, do not resize the window (keep 500x530 bounds)
  if (setupView && setupView.style.display !== 'none') {
    return;
  }

  if (!activeTab) {
    const settingsPopupEl = document.getElementById('settings-popup');
    const settingsOpen = settingsPopupEl && settingsPopupEl.style.display === 'flex';
    const targetHeight = settingsOpen ? 280 : COLLAPSED_HEIGHT;
    pendingProgrammaticResizes++;
    window.electronAPI.resizeWindow(currentWidth, targetHeight, toolbarPosition, reposition);
  } else {
    const answerBlock = document.getElementById('answer-block');
    const codeDisplayPre = document.getElementById('code-display');

    if (isAnswerExpanded) {
      if (answerBlock) {
        answerBlock.style.height = '480px';
        answerBlock.style.maxHeight = '550px';
        answerBlock.style.overflowY = 'auto';
      }
      if (codeDisplayPre) {
        codeDisplayPre.style.height = 'auto';
        codeDisplayPre.style.maxHeight = '350px';
        codeDisplayPre.style.overflowY = 'auto';
      }
      if (copyAllAnswerBtn) copyAllAnswerBtn.style.display = 'inline-block';
    } else if (hasActiveAnswer) {
      if (answerBlock) {
        answerBlock.style.height = 'auto';
        answerBlock.style.maxHeight = '250px';
        answerBlock.style.overflowY = 'auto';
      }
      if (codeDisplayPre) {
        codeDisplayPre.style.height = 'auto';
        codeDisplayPre.style.maxHeight = '350px';
        codeDisplayPre.style.overflowY = 'auto';
      }
      // Show the sticky copy-all button
      if (copyAllAnswerBtn) copyAllAnswerBtn.style.display = 'inline-block';
    } else {
      if (answerBlock) {
        answerBlock.style.height = '110px';
        answerBlock.style.maxHeight = '250px';
        answerBlock.style.overflowY = 'auto';
      }
      if (codeDisplayPre) {
        codeDisplayPre.style.height = '310px';
        codeDisplayPre.style.maxHeight = '350px';
        codeDisplayPre.style.overflowY = 'auto';
      }
    }

    setTimeout(() => {
      const appContainer = document.querySelector('.app-container');
      const rect = appContainer.getBoundingClientRect();
      const settingsPopupEl = document.getElementById('settings-popup');
      const settingsOpen = settingsPopupEl && settingsPopupEl.style.display === 'flex';
      const settingsBuffer = settingsOpen ? 180 : 0;
      // Clamp target height to MAX_HEIGHT maximum
      const targetHeight = Math.min(MAX_HEIGHT, Math.round(rect.height) + 12 + settingsBuffer);

      pendingProgrammaticResizes++;
      window.electronAPI.resizeWindow(currentWidth, targetHeight, toolbarPosition, reposition);
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
// Now using native OS-level dragging via CSS `-webkit-app-region: drag` to prevent
// DPI-scaling related sub-pixel rounding errors that caused the window to slowly expand.
// The custom javascript `setPosition` loop was removed.

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

  // Case 2: First source starts → Initialize Deepgram Socket and Audio Context
  if (!isCurrentlyRecordingAny) {
    try {
      // 1. Get Deepgram key
      const dgKey = await window.electronAPI.getDeepgramKey();
      if (!dgKey || dgKey.startsWith('your_')) {
        showInlineError('Deepgram API key is missing — define DEEPGRAM_API_KEY in your .env file.', answerBlock);
        resetRecordButton();
        isRecordingSystem = false;
        isRecordingMic = false;
        isRecording = false;
        return;
      }

      // Initialize Web Audio API nodes
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      audioDestNode = audioCtx.createMediaStreamDestination();

      // Connect to Deepgram live transcription WebSocket
      dgSocket = new WebSocket('wss://api.deepgram.com/v1/listen?model=nova-3&interim_results=true&smart_format=true&endpointing=300', ['token', dgKey]);

      dgSocket.onopen = () => {
        console.log('[Deepgram Socket] Connected successfully.');
        
        recordBtn.style.pointerEvents = 'auto';
        if (micBtnAi) micBtnAi.style.pointerEvents = 'auto';

        if (transcriptBlock.textContent.startsWith('Transcription will appear')) {
          transcriptBlock.textContent = '';
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
          if (transcript) {
            if (data.is_final) {
              accumulatedTranscript += (accumulatedTranscript ? ' ' : '') + transcript;
              transcriptBlock.textContent = accumulatedTranscript;

              // Save transcript block to backend history database
              if (sessionToken && shouldSaveTranscript) {
                window.electronAPI.saveTranscriptBlock(sessionToken, {
                  speaker: 'dialogue',
                  content: transcript,
                  source: 'mixed_audio'
                }).catch(e => console.error('[Save Transcript] Failed:', e.message));
              }

              // --- AUTO ANSWER LOGIC ---
              const autoAnswerCheckbox = document.getElementById('setup-auto-answer');
              const autoAnswerActive = autoAnswerCheckbox ? autoAnswerCheckbox.checked : false;

              if (autoAnswerActive && !answerBlock.classList.contains('loading')) {
                const score = questionScore(transcript.trim());
                if (score > 0) {
                  console.log(`[Auto-Answer] Question end detected inside transcript chunk: "${transcript}". Querying AI...`);
                  if (autoAnswerTimeoutId) {
                    clearTimeout(autoAnswerTimeoutId);
                    autoAnswerTimeoutId = null;
                  }
                  queryAssistant(null, false);
                } else {
                  if (autoAnswerTimeoutId) clearTimeout(autoAnswerTimeoutId);
                  autoAnswerTimeoutId = setTimeout(() => {
                    if (!answerBlock.classList.contains('loading')) {
                      console.log('[Auto-Answer] 1-second transcript gap detected. Querying AI...');
                      queryAssistant(null, false);
                    }
                    autoAnswerTimeoutId = null;
                  }, 1000);
                }
              }
            } else {
              transcriptBlock.innerHTML = accumulatedTranscript + (accumulatedTranscript ? ' ' : '') + `<span style="color: var(--text-secondary); font-style: italic;">${transcript}</span>`;
            }
            transcriptBlock.scrollLeft = transcriptBlock.scrollWidth;
          }
        } catch (e) {
          console.error('[Deepgram Socket] Error parsing message:', e);
        }
      };

      dgSocket.onerror = (err) => {
        console.error('[Deepgram Socket] Connection error:', err);
        stopRecording();
      };

      dgSocket.onclose = () => {
        console.log('[Deepgram Socket] Closed connection.');
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
          activeSystemStream.getVideoTracks().forEach(track => track.stop());
          
          systemSourceNode = audioCtx.createMediaStreamSource(activeSystemStream);
          systemSourceNode.connect(audioDestNode);
          
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
        try { systemSourceNode.disconnect(); } catch (e) {}
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
        
        if (micBtnAi) {
          micBtnAi.style.pointerEvents = 'auto';
          micBtnAi.classList.add('recording');
          micText.textContent = 'Mic Active';
          micBtnAi.style.background = 'rgba(239, 68, 68, 0.12)';
          micBtnAi.style.borderColor = 'rgba(239, 68, 68, 0.4)';
          micBtnAi.style.color = '#f87171';
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
        try { micSourceNode.disconnect(); } catch (e) {}
        micSourceNode = null;
      }
      if (activeMicStream) {
        activeMicStream.getTracks().forEach(track => track.stop());
        activeMicStream = null;
      }
      if (micBtnAi) {
        micBtnAi.classList.remove('recording');
        micText.textContent = 'Mic';
        micBtnAi.style.background = 'rgba(6, 182, 212, 0.12)';
        micBtnAi.style.borderColor = 'rgba(6, 182, 212, 0.4)';
        micBtnAi.style.color = '#22d3ee';
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
    try { systemSourceNode.disconnect(); } catch (e) {}
    systemSourceNode = null;
  }
  if (activeSystemStream) {
    activeSystemStream.getTracks().forEach(track => track.stop());
    activeSystemStream = null;
  }

  if (micSourceNode) {
    try { micSourceNode.disconnect(); } catch (e) {}
    micSourceNode = null;
  }
  if (activeMicStream) {
    activeMicStream.getTracks().forEach(track => track.stop());
    activeMicStream = null;
  }

  if (audioCtx) {
    audioCtx.close().catch(e => {});
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
    micBtnAi.style.background = 'rgba(6, 182, 212, 0.12)';
    micBtnAi.style.borderColor = 'rgba(6, 182, 212, 0.4)';
    micBtnAi.style.color = '#22d3ee';
  }
}

// Clear Transcript
clearTranscriptBtn.addEventListener('click', () => {
  accumulatedTranscript = '';
  transcriptBlock.textContent = 'Transcription will appear here in real-time as system audio is captured...';
});

// Copy Transcript
copyTranscriptBtn.addEventListener('click', () => {
  const text = transcriptBlock.textContent.trim();
  if (text && !text.startsWith('Transcription will appear')) {
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
            answerBlock.scrollTop = answerBlock.scrollHeight;
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
                answerBlock.scrollTop = answerBlock.scrollHeight;
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
        answerBlock.scrollTop = answerBlock.scrollHeight;
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
  const currentText = accumulatedTranscript.trim();
  if (!currentText || currentText.startsWith('Transcription will appear')) {
    // Stealth mode only — show inline red banner in the answer block
    showInlineError('No transcript yet — start recording to capture audio first.', answerBlock);
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
      html += `<span style="color: #a78bfa; font-weight: bold;">${escapeHTML(keyword)}</span>`;
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
    qDiv.style.borderLeft = '3px solid #a78bfa';
    qDiv.style.display = 'flex';
    qDiv.style.alignItems = 'flex-start';
    qDiv.style.gap = '6px';

    const qBadge = document.createElement('span');
    qBadge.textContent = 'Q:';
    qBadge.style.color = '#a78bfa';
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
      codeWrapper.style.border = '1px solid rgba(139, 92, 246, 0.3)';
      codeWrapper.style.background = '#05060f';
      codeWrapper.style.fontFamily = 'monospace';
      codeWrapper.style.fontSize = '0.9em';
      codeWrapper.style.overflow = 'hidden';

      const header = document.createElement('div');
      header.style.display = 'flex';
      header.style.justifyContent = 'space-between';
      header.style.alignItems = 'center';
      header.style.background = 'rgba(124, 58, 237, 0.1)';
      header.style.padding = '4px 12px';
      header.style.borderBottom = '1px solid rgba(139, 92, 246, 0.2)';

      const langLabel = document.createElement('span');
      langLabel.textContent = (segment.lang || 'code').toUpperCase();
      langLabel.style.color = '#c084fc';
      langLabel.style.fontSize = '0.75em';
      langLabel.style.fontWeight = 'bold';
      langLabel.style.letterSpacing = '0.5px';

      const copyBtn = document.createElement('button');
      copyBtn.textContent = 'Copy';
      copyBtn.style.background = 'rgba(124, 58, 237, 0.2)';
      copyBtn.style.border = '1px solid rgba(139, 92, 246, 0.2)';
      copyBtn.style.color = '#c084fc';
      copyBtn.style.fontSize = '0.75em';
      copyBtn.style.padding = '2px 8px';
      copyBtn.style.borderRadius = '4px';
      copyBtn.style.cursor = 'pointer';
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
      textDiv.style.lineHeight = '1.6';
      textDiv.style.color = '#e2e8f0';
      textDiv.style.whiteSpace = 'pre-wrap';
      textDiv.style.fontSize = '1em';

      let formattedContent = escapeHTML(segment.content);
      formattedContent = formattedContent.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      textDiv.innerHTML = formattedContent;
      container.appendChild(textDiv);
    }
  });
}

const settingsBtn = document.getElementById('settings-btn');
const opacitySlider = document.getElementById('opacity-slider');
const opacityDisplay = document.getElementById('opacity-display');
const fontSizeInput = document.getElementById('font-size-input');
const userEmailInput = document.getElementById('user-email-input');
const mainAnswerBlock = document.getElementById('answer-block');
const mainTranscriptBlock = document.getElementById('transcript-block');
const zoomSlider = document.getElementById('zoom-slider');
const zoomDisplay = document.getElementById('zoom-display');

// Load settings from localStorage
function loadAllSettings() {
  const savedEmail = safeGetItem('stealth_user_email') || 'premium@stealth.ai';
  if (userEmailInput) {
    userEmailInput.value = savedEmail;
  }
  syncUserEmail(savedEmail); // Dynamically sync user ID

  const savedOpacity = safeGetItem('stealth_opacity');
  if (savedOpacity !== null) {
    userOpacity = parseFloat(savedOpacity);
    if (opacitySlider) opacitySlider.value = savedOpacity;
    if (opacityDisplay) opacityDisplay.textContent = Math.round(userOpacity * 100) + '%';
    // Apply opacity
    if (!document.body.classList.contains('stealth-active')) {
      const appCont = document.querySelector('.app-container');
      if (appCont) appCont.style.opacity = userOpacity;
    }
  } else {
    if (opacityDisplay && opacitySlider) {
      opacityDisplay.textContent = Math.round(parseFloat(opacitySlider.value) * 100) + '%';
    }
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
    document.body.style.zoom = val;
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
    const syncUrl = `${base}/auth/sync?port=48999`;
    window.electronAPI.openExternalUrl(syncUrl);
  } catch (err) {
    console.error('[Stealth Sync] Failed to launch external browser sync page:', err);
  }
}

function logoutLocalUser() {
  safeSetItem('stealth_user_email', '');
  safeSetItem('stealth_login_token', '');
  if (userEmailInput) userEmailInput.value = '';
  showSyncPage();
}

// Call settings initialization and startup verification
loadAllSettings();
verifySessionOnStartup();

// Listen for credentials sent from system browser via Electron local HTTP server
if (window.electronAPI && window.electronAPI.onSyncCredentials) {
  window.electronAPI.onSyncCredentials((data) => {
    console.log('[Stealth Sync] Credentials received from browser:', data);
    if (data.email && data.token) {
      safeSetItem('stealth_user_email', data.email);
      safeSetItem('stealth_login_token', data.token);
      if (userEmailInput) {
        userEmailInput.value = data.email;
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
  const popup = document.getElementById('settings-popup');
  if (popup) {
    const isShowing = popup.style.display === 'flex';
    popup.style.display = isShowing ? 'none' : 'flex';
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

if (userEmailInput) {
  userEmailInput.addEventListener('input', (e) => {
    const email = e.target.value;
    safeSetItem('stealth_user_email', email);
    syncUserEmail(email);
  });
}

if (opacitySlider) {
  opacitySlider.addEventListener('input', (e) => {
    userOpacity = parseFloat(e.target.value);
    safeSetItem('stealth_opacity', e.target.value);
    if (opacityDisplay) opacityDisplay.textContent = Math.round(userOpacity * 100) + '%';
    if (!document.body.classList.contains('stealth-active')) {
      const appCont = document.querySelector('.app-container');
      if (appCont) appCont.style.opacity = userOpacity;
    } else {
      if (document.body.classList.contains('hover-active')) {
        const appCont = document.querySelector('.app-container');
        if (appCont) appCont.style.opacity = userOpacity;
      }
    }
  });
}

if (fontSizeInput) {
  fontSizeInput.addEventListener('input', (e) => {
    const val = e.target.value;
    safeSetItem('stealth_font_size', val);
    const valPx = val + 'px';
    if (mainAnswerBlock) mainAnswerBlock.style.fontSize = valPx;
    if (mainTranscriptBlock) mainTranscriptBlock.style.fontSize = valPx;
  });
}

if (zoomSlider) {
  zoomSlider.addEventListener('input', (e) => {
    const val = parseFloat(e.target.value);
    safeSetItem('stealth_zoom', e.target.value);
    if (zoomDisplay) zoomDisplay.textContent = Math.round(val * 100) + '%';
    document.body.style.zoom = val;
  });
}

const shrinkBtn = document.getElementById('shrink-btn');
const diamondBtn = document.getElementById('diamond-btn');
const appContainer = document.querySelector('.app-container');

async function toggleShrunk(shrunk) {
  isShrunk = shrunk;
  if (isShrunk) {
    appContainer.style.display = 'none';
    if (diamondBtn) {
      diamondBtn.style.display = 'flex';
      diamondBtn.style.position = 'absolute';
      diamondBtn.style.top = '4px';
      diamondBtn.style.left = '4px';
    }
    pendingProgrammaticResizes++;
    window.electronAPI.resizeWindow(44, 44, toolbarPosition, false);
    window.electronAPI.setIgnoreMouseEvents(false);
  } else {
    if (diamondBtn) {
      diamondBtn.style.display = 'none';
    }
    appContainer.style.display = 'flex';
    
    // When expanding, make sure it fades in smoothly if stealth is active
    if (document.body.classList.contains('stealth-active')) {
      appContainer.style.opacity = userOpacity;
      document.body.classList.add('hover-active');
    } else {
      appContainer.style.opacity = 1.0;
    }
    
    updateWindowSize();
  }
}

if (shrinkBtn) {
  shrinkBtn.addEventListener('click', () => {
    toggleShrunk(true);
  });
}

if (diamondBtn) {
  diamondBtn.addEventListener('click', () => {
    toggleShrunk(false);
  });
}

const expandAnswerBtn = document.getElementById('expand-answer-btn');
let isDragClick = false;

if (expandAnswerBtn) {
  expandAnswerBtn.addEventListener('pointerdown', (e) => {
    isResizingPanel = true;
    startWidth = currentWidth;
    startHeight = currentHeight;
    startMouseX = e.screenX;
    startMouseY = e.screenY;
    isDragClick = false;
    e.preventDefault();
    
    // Capture pointer events globally to allow dragging outside window boundaries
    expandAnswerBtn.setPointerCapture(e.pointerId);
    
    window.electronAPI.setIgnoreMouseEvents(false);
  });

  expandAnswerBtn.addEventListener('click', (e) => {
    if (isDragClick) {
      isDragClick = false;
      return;
    }
    isAnswerExpanded = !isAnswerExpanded;
    expandAnswerBtn.classList.toggle('active', isAnswerExpanded);
    if (isAnswerExpanded) {
      expandAnswerBtn.style.color = 'var(--accent-ai)';
      expandAnswerBtn.style.borderColor = 'rgba(139, 92, 246, 0.4)';
      expandAnswerBtn.style.background = 'rgba(139, 92, 246, 0.08)';
      
      // Default large dimensions
      currentWidth = Math.max(currentWidth, 850);
      currentHeight = Math.max(currentHeight, 664);
    } else {
      expandAnswerBtn.style.color = 'var(--text-secondary)';
      expandAnswerBtn.style.borderColor = 'rgba(255, 255, 255, 0.08)';
      expandAnswerBtn.style.background = 'rgba(255, 255, 255, 0.04)';
      
      // Default collapsed dimensions
      currentWidth = 850;
      currentHeight = 664;
      
      const panelsContainer = document.getElementById('panels-container');
      if (panelsContainer) {
        panelsContainer.style.height = 'auto';
        panelsContainer.style.maxHeight = '1200px';
      }
      const answerBlock = document.getElementById('answer-block');
      if (answerBlock) {
        answerBlock.style.height = 'auto';
        answerBlock.style.maxHeight = '250px';
      }
    }
    updateWindowSize();
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
