// State
let conversationHistory = [];
let currentScript = null;
let currentVisualPlan = null;
let selectedVoice = 'the_anchor';
let selectedFormat = '9:16';
let uploadedFile = null;
let tokenBalance = 120;
let chatPanelOpen = false;
let unreadMessages = 0;
let isPro = false;

// Image placement state
let pendingImageFile = null;
let pendingImagePath = null;
let pendingImageAnalysis = null;
let selectedPlacement = 'background';

// Personalization mode state
let personalizeVideoData = null; // Stores template data for personalized video rendering
let pendingPersonalizeRender = false; // Flag to trigger render after script confirmation

// Unified Engine State
let contentMode = 'auto'; // auto, create, clip
let currentThesis = null;
let currentAnchors = [];
let currentThoughtChanges = [];
let clipSuggestions = [];
let learnedPatternsApplied = false;
let sceneDirections = {}; // Store scene directions by index

// Document Editor State
let docEditTimeout = null;
let lastDocContent = '';
let docHasScript = false;
let docEditAbortController = null;

// Global abort controller for render operations
let renderAbortController = null;
let currentRenderSessionId = null;

// Robust fetch with timeout and external abort signal support
async function fetchWithTimeout(url, options = {}, timeoutMs = 30000, externalSignal = null) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    
    // If external signal provided, listen for its abort and chain it
    let externalAbortHandler = null;
    if (externalSignal) {
        externalAbortHandler = () => controller.abort();
        externalSignal.addEventListener('abort', externalAbortHandler);
    }
    
    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        return response;
    } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === 'AbortError') {
            // Check if it was a user-initiated cancel vs timeout
            if (externalSignal && externalSignal.aborted) {
                throw new Error('Request cancelled');
            }
            throw new Error(`Request timed out after ${timeoutMs/1000}s`);
        }
        throw err;
    } finally {
        if (externalSignal && externalAbortHandler) {
            externalSignal.removeEventListener('abort', externalAbortHandler);
        }
    }
}

// Fetch with retry for transient failures (not user cancels)
async function fetchWithRetry(url, options = {}, { timeoutMs = 30000, maxRetries = 2, retryDelay = 1000, signal = null } = {}) {
    let lastError;
    
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        // Check if cancelled before each attempt
        if (signal && signal.aborted) {
            throw new Error('Request cancelled');
        }
        
        try {
            const response = await fetchWithTimeout(url, options, timeoutMs, signal);
            
            // Retry on 5xx server errors
            if (response.status >= 500 && attempt < maxRetries) {
                console.warn(`Server error ${response.status}, retrying (${attempt + 1}/${maxRetries})...`);
                await new Promise(r => setTimeout(r, retryDelay * (attempt + 1)));
                continue;
            }
            
            return response;
        } catch (err) {
            lastError = err;
            
            // Don't retry if user cancelled
            if (err.message === 'Request cancelled') {
                throw err;
            }
            
            // Retry on timeout or network errors (TypeError = network down)
            const isRetryable = err.message.includes('timed out') || 
                                err.name === 'TypeError' || 
                                err.message.includes('Failed to fetch');
            
            if (isRetryable && attempt < maxRetries) {
                console.warn(`Request failed (${err.message}), retrying (${attempt + 1}/${maxRetries})...`);
                await new Promise(r => setTimeout(r, retryDelay * (attempt + 1)));
                continue;
            }
            
            throw err;
        }
    }
    
    throw lastError || new Error('Request failed after retries');
}

// Cancel any in-flight render operations
function cancelRenderOperations() {
    if (renderAbortController) {
        renderAbortController.abort();
        renderAbortController = null;
    }
    currentRenderSessionId = null;
}

// Generate unique render session ID
function startRenderSession() {
    cancelRenderOperations();
    renderAbortController = new AbortController();
    currentRenderSessionId = 'render-' + Date.now();
    return currentRenderSessionId;
}

// Check if this render session is still active
function isRenderSessionActive(sessionId) {
    return currentRenderSessionId === sessionId;
}

// Document Editor Functions - simplified, no auto AI calls
function updateDocEditor() {
    const editor = document.getElementById('doc-script-editor');
    if (!editor) return;
    const content = editor.innerText.trim();
    updateTimelineControls(content.length > 100);
}

function handleAIAction(actionType) {
    
    switch(actionType) {
        case 'generate_script':
            generateScriptFromDoc();
            break;
        case 'find_visuals':
            showVisuals();
            break;
        case 'cast_voices':
            showVoices();
            break;
        case 'refine':
            refineScript();
            break;
        default:
            sendMessage();
    }
}

async function generateScriptFromDoc() {
    const editor = document.getElementById('doc-script-editor');
    if (!editor) return;
    const content = editor.innerText.trim();
    
    if (!content) {
        showToast('Add some content first');
        return;
    }
    
    showStatus('Generating script...');
    
    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: content,
                project_id: currentProjectId,
                conversation: conversationHistory.slice(-5)
            })
        });
        
        const data = await response.json();
        
        if (data.script) {
            currentScript = data.script;
            docHasScript = true;
            displayScriptInEditor(data.script);
            updateTimelineControls(true);
            showAIFeedback('Script generated. You can edit it directly, then assign voices or find visuals.', [
                { type: 'cast_voices', label: 'Assign Voices' },
                { type: 'find_visuals', label: 'Find Visuals' }
            ]);
        } else if (data.reply) {
            showAIFeedback(data.reply, []);
        }
        
        hideStatus();
    } catch (error) {
        console.error('Error generating script:', error);
        showError('Script Generation Failed', 'Something went wrong. Please try again.');
        hideStatus();
    }
}

function displayScriptInEditor(script) {
    const editor = document.getElementById('doc-script-editor');
    if (!editor) return;
    
    // Parse script format and display nicely
    if (typeof script === 'string') {
        // Format the script with visual styling
        let formatted = script
            .replace(/^SCENE (\d+)(.*)$/gm, '<div class="scene-header">SCENE $1$2</div>')
            .replace(/^\[([A-Z_]+)\]:(.*)$/gm, '<span class="character-name">[$1]:</span><span class="dialogue">$2</span>')
            .replace(/^VISUAL:(.*)$/gm, '<div class="visual-cue">VISUAL:$1</div>')
            .replace(/^CUT:(.*)$/gm, '<div class="visual-cue">CUT:$1</div>');
        
        editor.innerHTML = formatted;
    } else {
        editor.textContent = JSON.stringify(script, null, 2);
    }
    
    lastDocContent = editor.innerText;
}

function updateTimelineControls(show) {
    const controls = document.getElementById('timeline-controls');
    if (!controls) return;
    if (show && docHasScript) {
        controls.classList.add('visible');
    } else {
        controls.classList.remove('visible');
    }
}

function showStatus(text) {
    // Status indicator removed - no-op
}

function hideStatus() {
    // Status indicator removed - no-op
}

function showAIFeedback(message, actions) {
    // Display feedback in the chat if available
    if (message) {
        addMessage(message, false, true);
    }
}

// Timeline Button Handlers
function showVoices() {
    showStage('review');
    setTimeout(() => {
        document.getElementById('casting-panel')?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
}

function showVisuals() {
    if (!currentScript) {
        showToast('Generate a script first');
        return;
    }
    startLoading();
    curateVisuals(currentScript);
}

function showCaptions() {
    showStage('review');
    setTimeout(() => {
        document.querySelector('.caption-section')?.scrollIntoView({ behavior: 'smooth' });
    }, 100);
}

function previewVideo() {
    if (!currentScript) {
        showToast('Generate a script first');
        return;
    }
    showStage('export');
}

// Mode Selection
function setContentMode(mode) {
    contentMode = mode;
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
    });
    
    // Unified placeholder - AI auto-detects mode
    const placeholder = document.getElementById('composer-input');
    placeholder.placeholder = "Prompt, paste, or drop. We'll make the clip.";
}

// Thesis Display
function displayThesis(thesis) {
    currentThesis = thesis;
    const panel = document.getElementById('thesis-panel');
    const content = document.getElementById('thesis-content');
    const meta = document.getElementById('thesis-meta');
    const typeEl = document.getElementById('thesis-type');
    const confEl = document.getElementById('thesis-confidence');
    
    if (!panel) return;
    
    if (thesis && thesis.thesis_statement) {
        panel.style.display = 'block';
        if (content) content.innerHTML = thesis.thesis_statement;
        
        if ((thesis.thesis_type || thesis.confidence) && meta) {
            meta.style.display = 'flex';
            if (typeEl) typeEl.textContent = thesis.thesis_type || 'general';
            if (confEl) confEl.textContent = thesis.confidence ? `${Math.round(thesis.confidence * 100)}% confident` : '';
        }
    } else {
        panel.style.display = 'none';
    }
}

// Loop Score Validation
async function validateLoopScore(thesis, script) {
    try {
        const response = await fetch('/validate-loop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ thesis, script })
        });
        
        const data = await response.json();
        if (data.success) {
            displayLoopScore(data);
        }
        return data;
    } catch (err) {
        console.error('Loop validation error:', err);
        return null;
    }
}

// Display Loop Score in chat
function displayLoopScore(loopData) {
    const score = loopData.loop_score || 0;
    const strength = loopData.loop_strength || 'unknown';
    const analysis = loopData.analysis || '';
    const issues = loopData.issues || [];
    const suggestedFix = loopData.suggested_fix;
    
    let scoreColor = '#4ade80';
    if (score < 0.4) scoreColor = '#f87171';
    else if (score < 0.7) scoreColor = '#fbbf24';
    
    let html = `
        <div class="loop-score-card">
            <div class="loop-score-header">
                <span class="loop-label">Loop Score</span>
                <span class="loop-value" style="color: ${scoreColor}">${Math.round(score * 100)}%</span>
                <span class="loop-strength">${strength}</span>
            </div>
            <div class="loop-analysis">${analysis}</div>
    `;
    
    if (issues.length > 0) {
        html += `<div class="loop-issues"><strong>Issues:</strong> ${issues.join(', ')}</div>`;
    }
    
    if (suggestedFix && score < 0.7) {
        html += `
            <div class="loop-fix">
                <strong>Suggested fix:</strong> ${suggestedFix}
                <button class="loop-apply-fix" onclick="applyLoopFix('${suggestedFix.replace(/'/g, "\\'")}')">Apply Fix</button>
            </div>
        `;
    }
    
    html += '</div>';
    
    addMessage(html, false, true);
}

function applyLoopFix(newClosing) {
    if (currentScript) {
        currentScript.closing = newClosing;
        
        // Update the full script with new closing if possible
        if (currentScript.full_script) {
            const lines = currentScript.full_script.split('\n');
            if (lines.length > 0) {
                lines[lines.length - 1] = newClosing;
                currentScript.full_script = lines.join('\n');
                updateScriptCard(currentScript.full_script);
                displayScriptInEditor(currentScript.full_script);
            }
        }
        
        addMessage('Applied the suggested fix. Your closing now better connects to the thesis.');
    }
}

// Scene visuals state
let sceneVisuals = {};
let activeSceneIndex = null;

// Workflow State Persistence
function saveWorkflowState() {
    if (!currentProjectId) return;
    const state = {
        projectId: currentProjectId,
        workflowStep: currentWorkflowStep,
        script: currentScript,
        anchors: currentAnchors,
        sceneVisuals: sceneVisuals,
        thesis: currentThesis,
        selectedVoice: selectedVoice,
        selectedFormat: selectedFormat,
        sceneDirections: sceneDirections,
        detectedCharacters: detectedCharacters,
        timestamp: Date.now()
    };
    try {
        localStorage.setItem('framd_workflow_state', JSON.stringify(state));
    } catch (e) {
        console.warn('Could not save workflow state:', e);
    }
}

function restoreWorkflowState() {
    try {
        const saved = localStorage.getItem('framd_workflow_state');
        if (!saved) return false;
        
        const state = JSON.parse(saved);
        
        // Only restore if less than 24 hours old
        if (Date.now() - state.timestamp > 24 * 60 * 60 * 1000) {
            localStorage.removeItem('framd_workflow_state');
            return false;
        }
        
        if (state.projectId) currentProjectId = state.projectId;
        if (state.workflowStep) currentWorkflowStep = state.workflowStep;
        if (state.script) currentScript = state.script;
        if (state.anchors) currentAnchors = state.anchors;
        if (state.sceneVisuals) sceneVisuals = state.sceneVisuals;
        if (state.thesis) currentThesis = state.thesis;
        if (state.selectedVoice) selectedVoice = state.selectedVoice;
        if (state.selectedFormat) selectedFormat = state.selectedFormat;
        if (state.sceneDirections) sceneDirections = state.sceneDirections;
        if (state.detectedCharacters) detectedCharacters = state.detectedCharacters;
        
        console.log('[Workflow] Restored state at step', currentWorkflowStep);
        return true;
    } catch (e) {
        console.warn('Could not restore workflow state:', e);
        return false;
    }
}

function clearWorkflowState() {
    localStorage.removeItem('framd_workflow_state');
}

function updateProgressIndicator() {
    const stepLabels = ['Input', 'Script', 'Visuals', 'Voice', 'Render'];
    const maxStep = stepLabels.length;
    const progress = Math.min(currentWorkflowStep, maxStep) / maxStep * 100;
    
    const indicator = document.querySelector('.workflow-progress');
    if (indicator) {
        indicator.style.width = `${progress}%`;
    }
    
    const stepDisplay = document.querySelector('.workflow-step-label');
    if (stepDisplay && currentWorkflowStep <= maxStep) {
        stepDisplay.textContent = stepLabels[currentWorkflowStep - 1] || 'Ready';
    }
    
    console.log('[Progress] Step', currentWorkflowStep, '/', maxStep, '(' + progress.toFixed(0) + '%)');
}

// Anchors Display - Scenes List Format (Uses new single-scene picker)
async function displayAnchors(anchors) {
    currentAnchors = anchors || [];
    
    if (currentAnchors.length > 0) {
        // Show brief scene summary
        addMessage(`Your script has ${currentAnchors.length} scenes. Let's pick visuals for each one.`, false);
        
        // Detect characters from full script first
        const scriptText = currentScript?.full_script || (typeof currentScript === 'string' ? currentScript : null);
        if (scriptText && detectedCharacters.length === 0) {
            try {
                const resp = await fetch('/detect-characters', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ script: scriptText })
                });
                const data = await resp.json();
                if (data.success && data.characters) {
                    detectedCharacters = data.characters;
                    console.log('[displayAnchors] Detected characters:', detectedCharacters);
                }
            } catch (err) {
                console.warn('Could not detect characters:', err);
            }
        }
        
        // Create container and open new single-scene visual picker
        const messagesDiv = document.getElementById('messages');
        const containerDiv = document.createElement('div');
        containerDiv.className = 'message ai visual-picker-message';
        containerDiv.id = 'visual-picker-container-' + Date.now();
        messagesDiv.appendChild(containerDiv);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        
        // Initialize the single-scene visual picker
        currentVisualSceneIndex = 0;
        visualPickerContainer = containerDiv;
        setTimeout(() => {
            renderSingleSceneVisualPicker(containerDiv, currentAnchors, 0);
        }, 300);
    }
}

// Open visual picker - shows loading immediately
function openVisualPicker() {
    const container = document.getElementById('anchor-cards-container');
    const btn = document.getElementById('visual-picker-toggle');
    if (!container) {
        console.error('[openVisualPicker] Container not found!');
        return;
    }
    
    console.log('[openVisualPicker] Starting...');
    
    // Force container to be visible with explicit styles
    container.style.display = 'block';
    container.style.visibility = 'visible';
    container.style.opacity = '1';
    container.style.minHeight = '300px';
    
    // Show skeleton loading state immediately
    container.innerHTML = `
        <div style="text-align: center; padding: 20px; color: var(--gold);">
            <div style="font-size: 18px; margin-bottom: 16px;">Preparing visual picker...</div>
        </div>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; padding: 0 16px;">
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
        </div>
    `;
    
    // Hide button after showing loading
    if (btn) btn.style.display = 'none';
    
    renderedSceneIndex = -1; // Reset so we render fresh
    showAnchorCards();
}

// Show anchor cards in the visuals workflow card
async function showAnchorCards() {
    const container = document.getElementById('anchor-cards-container');
    if (!container) return;
    
    // Use currentAnchors from script generation, or try to get from currentScript
    let anchors = currentAnchors;
    if ((!anchors || anchors.length === 0) && currentScript?.anchor_points) {
        anchors = currentScript.anchor_points;
        currentAnchors = anchors;
    }
    
    if (!anchors || anchors.length === 0) {
        container.innerHTML = '<p style="color: var(--text-dim); text-align: center; padding: 20px;">No scenes found. Generate a script first.</p>';
        // Show button again so user can retry
        const btn = document.getElementById('visual-picker-toggle');
        if (btn) btn.style.display = 'block';
        return;
    }
    
    // Detect characters first if not already done
    const scriptText = currentScript?.full_script || (typeof currentScript === 'string' ? currentScript : null);
    if (scriptText && detectedCharacters.length === 0) {
        try {
            const resp = await fetch('/detect-characters', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ script: scriptText })
            });
            const data = await resp.json();
            if (data.success && data.characters) {
                detectedCharacters = data.characters;
            }
        } catch (err) {
            console.warn('Could not detect characters:', err);
        }
    }
    
    // Render single-scene visual picker (one at a time)
    currentVisualSceneIndex = 0;
    renderedSceneIndex = -1; // Reset so we can render fresh
    visualPickerContainer = container; // Store reference for navigation
    renderSingleSceneVisualPicker(container, anchors, 0);
}

// Track current scene in visual picker
let currentVisualSceneIndex = 0;
let renderedSceneIndex = -1; // Track which scene is currently rendered
let visualPickerRenderedAt = 0; // Timestamp of last render

// Render a single scene visual picker with navigation
function renderSingleSceneVisualPicker(container, anchors, sceneIndex, forceRender = false) {
    // Check if this scene's grid already exists and has loaded content
    const existingGrid = document.getElementById(`scene-visual-grid-${sceneIndex}`);
    if (!forceRender && existingGrid && existingGrid.dataset.loaded === 'true') {
        console.log('[renderSingleSceneVisualPicker] Scene', sceneIndex, 'already has loaded content - skipping');
        return;
    }
    
    // Check if visuals are currently loading for this scene
    if (!forceRender && loadingSceneVisuals[sceneIndex]) {
        console.log('[renderSingleSceneVisualPicker] Scene', sceneIndex, 'is loading - skipping re-render');
        return;
    }
    
    // Also skip if same scene is being rendered (backup guard) - with time check
    const now = Date.now();
    if (!forceRender && renderedSceneIndex === sceneIndex && existingGrid && (now - visualPickerRenderedAt < 2000)) {
        console.log('[renderSingleSceneVisualPicker] Scene', sceneIndex, 'already rendered recently - skipping');
        return;
    }
    renderedSceneIndex = sceneIndex;
    visualPickerRenderedAt = now;
    const anchor = anchors[sceneIndex];
    if (!anchor) return;
    
    const hasVisual = sceneVisuals[sceneIndex] && sceneVisuals[sceneIndex].url;
    const totalScenes = anchors.length;
    
    container.innerHTML = `
        <div class="single-scene-picker" style="background: rgba(255,214,10,0.03); border: 1px solid var(--border); border-radius: 12px; overflow: hidden;">
            <!-- Progress indicator -->
            <div style="display: flex; gap: 4px; padding: 12px 16px; background: rgba(0,0,0,0.2);">
                ${anchors.map((_, i) => `
                    <div style="flex: 1; height: 4px; border-radius: 2px; background: ${i < sceneIndex ? 'var(--gold)' : i === sceneIndex ? 'rgba(255,214,10,0.6)' : 'rgba(255,255,255,0.1)'}; transition: all 0.3s;"></div>
                `).join('')}
            </div>
            
            <!-- Scene header -->
            <div style="padding: 16px; border-bottom: 1px solid var(--border);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span style="font-weight: 700; color: var(--gold); font-size: 16px;">Scene ${sceneIndex + 1} of ${totalScenes}: ${anchor.anchor_type || 'SCENE'}</span>
                    ${hasVisual ? '<span style="color: #4ade80; font-size: 14px;">✓ Visual Selected</span>' : ''}
                </div>
                <div style="font-size: 14px; color: var(--text); line-height: 1.5;">${anchor.anchor_text || ''}</div>
            </div>
            
            <!-- Visual grid (auto-loads) -->
            <div id="single-scene-visuals" style="padding: 16px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <span style="font-size: 13px; color: var(--text-dim);">Select a visual for this scene:</span>
                    <button class="btn btn-secondary" onclick="refreshSceneVisuals(${sceneIndex})" style="font-size: 11px; padding: 6px 12px;">
                        ↻ Refresh
                    </button>
                </div>
                <div id="scene-visual-grid-${sceneIndex}" class="visual-grid-container" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; min-height: 200px; visibility: visible !important;">
                    <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
                </div>
            </div>
            
            <!-- Pop-up visuals (optional overlay graphics) -->
            <div style="padding: 0 16px 16px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span style="font-size: 13px; color: var(--text-dim);">Pop-up visual (optional):</span>
                    <button class="btn btn-secondary" onclick="loadPopupVisuals(${sceneIndex})" style="font-size: 11px; padding: 4px 8px; min-width: auto;">
                        ↻
                    </button>
                </div>
                <div id="popup-grid-${sceneIndex}" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px;">
                    <div class="skeleton-card" style="aspect-ratio: 1; border-radius: 6px; height: 50px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 1; border-radius: 6px; height: 50px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 1; border-radius: 6px; height: 50px;"></div>
                    <div class="skeleton-card" style="aspect-ratio: 1; border-radius: 6px; height: 50px;"></div>
                </div>
            </div>
            
            <!-- Scene Directions (editable) -->
            <div style="padding: 0 16px 16px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span style="font-size: 13px; color: var(--text-dim);">Scene Direction:</span>
                    <button class="btn btn-secondary" onclick="generateSceneDirection(${sceneIndex})" style="font-size: 11px; padding: 4px 8px; min-width: auto;">
                        AI Suggest
                    </button>
                </div>
                <input type="text" 
                    id="scene-direction-${sceneIndex}" 
                    class="scene-direction-input"
                    placeholder="e.g. slow zoom, pan left, hold static..."
                    value="${sceneDirections[sceneIndex] || ''}"
                    onchange="updateSceneDirection(${sceneIndex}, this.value)"
                    style="width: 100%; padding: 10px 12px; background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 13px;">
                <div style="font-size: 11px; color: var(--text-dim); margin-top: 6px;">
                    Directions: <code style="background: rgba(255,214,10,0.1); padding: 2px 4px; border-radius: 3px;">zoom in</code>, 
                    <code style="background: rgba(255,214,10,0.1); padding: 2px 4px; border-radius: 3px;">pan left</code>, 
                    <code style="background: rgba(255,214,10,0.1); padding: 2px 4px; border-radius: 3px;">pan right</code>, 
                    <code style="background: rgba(255,214,10,0.1); padding: 2px 4px; border-radius: 3px;">zoom out</code>, 
                    <code style="background: rgba(255,214,10,0.1); padding: 2px 4px; border-radius: 3px;">static</code>
                </div>
            </div>
            
            <!-- Navigation buttons -->
            <div style="display: flex; gap: 12px; padding: 16px; border-top: 1px solid var(--border); background: rgba(0,0,0,0.2);">
                <button class="btn btn-secondary" onclick="navigateVisualScene(${sceneIndex - 1})" style="flex: 1; ${sceneIndex === 0 ? 'opacity: 0.5; pointer-events: none;' : ''}">
                    ← Previous
                </button>
                <button class="btn btn-primary" onclick="navigateVisualScene(${sceneIndex + 1})" style="flex: 2;">
                    ${sceneIndex === totalScenes - 1 ? 'Finish & Continue →' : 'Next Scene →'}
                </button>
            </div>
        </div>
    `;
    
    // Auto-load visuals and popups immediately
    loadSceneVisualsAuto(sceneIndex);
    loadPopupVisuals(sceneIndex);
}

// Update scene direction from user input
function updateSceneDirection(sceneIndex, value) {
    sceneDirections[sceneIndex] = value;
    console.log(`Scene ${sceneIndex} direction updated:`, value);
}

// Generate AI suggestion for scene direction
async function generateSceneDirection(sceneIndex) {
    const anchor = currentAnchors[sceneIndex];
    if (!anchor) return;
    
    const input = document.getElementById(`scene-direction-${sceneIndex}`);
    if (input) input.value = 'Analyzing...';
    
    // Get visual description if available
    const visual = sceneVisuals[sceneIndex];
    const visualDescription = visual?.alt || visual?.description || '';
    
    try {
        const response = await fetch('/generate-scene-direction', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_text: anchor.anchor_text || '',
                scene_type: anchor.anchor_type || 'SCENE',
                visual_description: visualDescription
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            const direction = data.direction || 'static';
            sceneDirections[sceneIndex] = direction;
            if (input) input.value = direction;
            showToast(`Direction: ${direction}`);
        } else {
            if (input) input.value = 'static';
            sceneDirections[sceneIndex] = 'static';
        }
    } catch (err) {
        console.error('Error generating scene direction:', err);
        if (input) input.value = 'static';
        sceneDirections[sceneIndex] = 'static';
    }
}

// Load popup visuals for a scene
async function loadPopupVisuals(sceneIndex) {
    const popupGrid = document.getElementById(`popup-grid-${sceneIndex}`);
    if (!popupGrid) return;
    
    const anchor = currentAnchors[sceneIndex];
    if (!anchor) return;
    
    try {
        const response = await fetch('/scene-visuals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_text: anchor.anchor_text || '',
                scene_type: 'POPUP',
                keywords: ['icon', 'graphic', 'symbol', 'illustration']
            })
        });
        
        if (!response.ok) throw new Error('Server error');
        const data = await response.json();
        
        const allVisuals = [...(data.curated || []), ...(data.backgrounds || [])];
        
        // Re-fetch grid in case DOM changed
        const currentPopupGrid = document.getElementById(`popup-grid-${sceneIndex}`);
        if (!currentPopupGrid) return;
        
        if (allVisuals.length > 0) {
            currentPopupGrid.innerHTML = allVisuals.slice(0, 4).map((img) => `
                <div class="chat-visual-option popup-option ${sceneVisuals[sceneIndex]?.popupUrl === img.url ? 'selected' : ''}" 
                     style="cursor: pointer; aspect-ratio: 1;"
                     data-url="${img.url}">
                    <img src="${img.url}" alt="Pop-up" loading="lazy" style="pointer-events: none; width: 100%; height: 100%; object-fit: cover; border-radius: 6px;">
                </div>
            `).join('');
            
            currentPopupGrid.querySelectorAll('.popup-option').forEach(el => {
                el.addEventListener('click', function() {
                    const url = this.dataset.url;
                    // Toggle selection
                    if (sceneVisuals[sceneIndex]?.popupUrl === url) {
                        sceneVisuals[sceneIndex] = { ...sceneVisuals[sceneIndex], popupUrl: null, hasPopup: false };
                        this.classList.remove('selected');
                        showToast('Pop-up removed');
                    } else {
                        sceneVisuals[sceneIndex] = { ...sceneVisuals[sceneIndex], popupUrl: url, hasPopup: true };
                        currentPopupGrid.querySelectorAll('.popup-option').forEach(opt => opt.classList.remove('selected'));
                        this.classList.add('selected');
                        showToast('Pop-up selected');
                    }
                });
                
                // Error handler
                const img = el.querySelector('img');
                if (img) {
                    img.onerror = function() {
                        el.style.display = 'none';
                    };
                }
            });
        } else {
            currentPopupGrid.innerHTML = '<div style="grid-column: span 4; text-align: center; padding: 12px; color: var(--text-dim); font-size: 12px;">No pop-ups available</div>';
        }
    } catch (err) {
        console.error('[loadPopupVisuals] Error:', err);
        const errorGrid = document.getElementById(`popup-grid-${sceneIndex}`);
        if (errorGrid) {
            errorGrid.innerHTML = '<div style="grid-column: span 4; text-align: center; padding: 12px; color: red; font-size: 12px;">Error loading pop-ups</div>';
        }
    }
}

// Store reference to the visual picker container
let visualPickerContainer = null;

// Navigate between scenes
function navigateVisualScene(newIndex) {
    if (newIndex < 0) return;
    
    // Collapse the previous scene visual picker (not the current one being navigated to)
    const previousIndex = newIndex - 1;
    if (previousIndex >= 0) {
        collapsePreviousVisualPicker(previousIndex);
    }
    
    if (newIndex >= currentAnchors.length) {
        // Collapse the last scene before finishing
        collapsePreviousVisualPicker(currentAnchors.length - 1);
        
        // Finished all scenes - auto-advance to voice casting
        currentWorkflowStep = 4;
        addMessage(`Visuals set for all ${currentAnchors.length} scenes. Moving to voice casting...`, false);
        
        // Short delay then show voice options
        setTimeout(() => {
            showVoiceCastingOptions();
        }, 500);
        return;
    }
    
    currentVisualSceneIndex = newIndex;
    if (visualPickerContainer) {
        renderSingleSceneVisualPicker(visualPickerContainer, currentAnchors, newIndex);
    }
}

// Collapse previous visual picker when advancing to next scene
function collapsePreviousVisualPicker(previousSceneIndex) {
    // Find all visual pickers that haven't been collapsed yet
    const pickers = document.querySelectorAll('.chat-visual-picker:not(.picker-collapsed)');
    
    pickers.forEach((picker) => {
        // Check if this picker is for the previous scene
        const titleEl = picker.querySelector('.chat-visual-picker-title');
        const titleText = titleEl ? titleEl.textContent : '';
        const sceneMatch = titleText.match(/Scene (\d+)/i);
        const pickerSceneIndex = sceneMatch ? parseInt(sceneMatch[1]) - 1 : -1;
        
        // Only collapse if it's the previous scene (not current or future)
        if (pickerSceneIndex !== previousSceneIndex) return;
        
        // Mark as collapsed
        picker.classList.add('picker-collapsed');
        
        // Get selected visual thumbnail
        const selectedVisual = sceneVisuals[pickerSceneIndex];
        const thumbUrl = selectedVisual?.thumbnail || selectedVisual?.url || '';
        const thumbHtml = thumbUrl ? `<img class="scene-thumb" src="${thumbUrl}" alt="">` : '';
        
        // Create and insert collapsed header before the picker
        const collapsedHeader = document.createElement('div');
        collapsedHeader.className = 'collapsed-section';
        collapsedHeader.dataset.sceneIndex = pickerSceneIndex;
        collapsedHeader.onclick = function() { toggleVisualExpand(this); };
        collapsedHeader.innerHTML = `
            <span class="check-icon">✓</span>
            ${thumbHtml}
            <span class="summary-text">${titleText}${selectedVisual ? '' : ' (no visual)'}</span>
            <span class="expand-arrow">▼</span>
        `;
        
        picker.parentNode.insertBefore(collapsedHeader, picker);
        
        // Hide the picker content
        picker.style.display = 'none';
    });
}

// Toggle visual picker expand/collapse
function toggleVisualExpand(header) {
    header.classList.toggle('expanded');
    const picker = header.nextElementSibling;
    if (picker && picker.classList.contains('chat-visual-picker')) {
        picker.style.display = header.classList.contains('expanded') ? 'block' : 'none';
    }
}

// Track if voice casting options have been shown
let voiceCastingShown = false;

// Show voice casting options inline
function showVoiceCastingOptions() {
    // Prevent duplicate prompts
    if (voiceCastingShown) {
        console.log('[showVoiceCastingOptions] Already shown, skipping');
        return;
    }
    voiceCastingShown = true;
    
    const options = ['Auto-assign voices', 'Let me pick voices', 'Skip to render'];
    addMessageWithOptions('How would you like to handle voice casting?', options);
}

// Track loading state to prevent duplicate calls
let loadingSceneVisuals = {};

// Load visuals automatically for a scene
async function loadSceneVisualsAuto(sceneIndex) {
    // Prevent duplicate calls
    if (loadingSceneVisuals[sceneIndex]) {
        console.log('[loadSceneVisualsAuto] Already loading scene', sceneIndex, '- skipping');
        return;
    }
    loadingSceneVisuals[sceneIndex] = true;
    
    const anchor = currentAnchors[sceneIndex];
    if (!anchor) {
        console.warn('[loadSceneVisualsAuto] No anchor at index', sceneIndex);
        loadingSceneVisuals[sceneIndex] = false;
        return;
    }
    
    const grid = document.getElementById(`scene-visual-grid-${sceneIndex}`);
    if (!grid) {
        console.warn('[loadSceneVisualsAuto] No grid found for scene', sceneIndex);
        loadingSceneVisuals[sceneIndex] = false;
        return;
    }
    
    // Show loading bar at top
    startLoading();
    console.log('[loadSceneVisualsAuto] Loading visuals for scene', sceneIndex, 'Grid found:', !!grid);
    
    try {
        const response = await fetch('/scene-visuals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_text: anchor.anchor_text || '',
                scene_type: anchor.anchor_type || 'CLAIM',
                keywords: anchor.keywords || []
            })
        });
        
        if (!response.ok) {
            console.error('[loadSceneVisualsAuto] Server error:', response.status);
            throw new Error('Server error');
        }
        const data = await response.json();
        console.log('[loadSceneVisualsAuto] Received data:', data);
        
        const allVisuals = [...(data.curated || []), ...(data.backgrounds || [])];
        console.log('[loadSceneVisualsAuto] Total visuals:', allVisuals.length);
        
        // Re-fetch the grid element in case the DOM was updated during fetch
        const currentGrid = document.getElementById(`scene-visual-grid-${sceneIndex}`);
        if (!currentGrid) {
            console.warn('[loadSceneVisualsAuto] Grid disappeared during fetch for scene', sceneIndex);
            return;
        }
        
        // Store extra visuals as backups for failed loads
        window.backupVisuals = window.backupVisuals || {};
        window.backupVisuals[sceneIndex] = allVisuals.slice(9, 20);
        
        if (allVisuals.length > 0) {
            const htmlContent = allVisuals.slice(0, 9).map((img, imgIdx) => `
                <div class="chat-visual-option ${sceneVisuals[sceneIndex]?.url === img.url ? 'selected' : ''}" 
                     style="cursor: pointer; min-height: 80px; background: rgba(255,255,255,0.05);"
                     data-url="${img.url}"
                     data-img-idx="${imgIdx}">
                    <img src="${img.thumbnail || img.url}" alt="Visual option" style="pointer-events: none; width: 100%; height: 100%; object-fit: cover;"
                         onerror="this.parentElement.innerHTML='<div style=\\'display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font-size:11px;\\'>Image unavailable</div>'">
                </div>
            `).join('');
            
            console.log('[loadSceneVisualsAuto] Grid ID:', currentGrid.id, 'Parent:', currentGrid.parentElement?.id || currentGrid.parentElement?.className);
            
            currentGrid.innerHTML = htmlContent;
            currentGrid.dataset.loaded = 'true';
            currentGrid.dataset.loadTime = Date.now();
            
            console.log('[loadSceneVisualsAuto] SUCCESS! Set', currentGrid.children.length, 'images. Grid marked as loaded.');
            
            // Add click handlers and error handlers
            currentGrid.querySelectorAll('.chat-visual-option').forEach((el) => {
                el.addEventListener('click', function() {
                    const url = this.dataset.url;
                    selectSingleSceneVisual(sceneIndex, url, currentGrid);
                });
                
                // Add error handler for failed images
                const img = el.querySelector('img');
                if (img) {
                    img.onerror = function() {
                        replaceFailedImage(el, sceneIndex, currentGrid);
                    };
                }
            });
            console.log('[loadSceneVisualsAuto] Rendered', allVisuals.slice(0, 9).length, 'visuals to grid');
        } else {
            currentGrid.innerHTML = '<div style="grid-column: span 3; text-align: center; padding: 20px; color: var(--text-dim);">No visuals found. Try refreshing.</div>';
        }
    } catch (err) {
        console.error('[loadSceneVisualsAuto] Error:', err);
        showError('Visual Loading Failed', 'Could not load visuals for this scene. Try refreshing.');
        const errorGrid = document.getElementById(`scene-visual-grid-${sceneIndex}`);
        if (errorGrid) {
            errorGrid.innerHTML = '<div style="grid-column: span 3; text-align: center; padding: 20px; color: red;">Error loading visuals. Try refreshing.</div>';
        }
    } finally {
        // Always stop loading bar and reset loading state
        stopLoading();
        loadingSceneVisuals[sceneIndex] = false;
    }
}

// Replace a failed image with a backup or remove it
function replaceFailedImage(element, sceneIndex, grid) {
    const backups = window.backupVisuals?.[sceneIndex] || [];
    
    if (backups.length > 0) {
        // Get a backup image
        const backup = backups.shift();
        const newUrl = backup.url;
        
        // Update the element
        element.dataset.url = newUrl;
        const img = element.querySelector('img');
        if (img) {
            img.src = newUrl;
            // Add error handler for the new image too
            img.onerror = function() {
                replaceFailedImage(element, sceneIndex, grid);
            };
        }
    } else {
        // No more backups - hide the broken image card
        element.style.display = 'none';
        element.classList.add('failed-image');
    }
}

// Refresh visuals for current scene
function refreshSceneVisuals(sceneIndex) {
    const grid = document.getElementById(`scene-visual-grid-${sceneIndex}`);
    if (grid) {
        grid.innerHTML = `
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
            <div class="skeleton-card" style="aspect-ratio: 16/9; border-radius: 8px;"></div>
        `;
        grid.dataset.loaded = 'false'; // Clear loaded flag to force refresh
    }
    // Clear backups and loading state for this scene
    if (window.backupVisuals) {
        window.backupVisuals[sceneIndex] = [];
    }
    loadingSceneVisuals[sceneIndex] = false; // Reset to allow refresh
    showToast('Refreshing visuals...');
    loadSceneVisualsAuto(sceneIndex);
}

// Select a visual in the single-scene picker
function selectSingleSceneVisual(sceneIndex, imageUrl, grid) {
    // Store the visual
    sceneVisuals[sceneIndex] = {
        ...sceneVisuals[sceneIndex],
        url: imageUrl,
        category: 'curated'
    };
    
    // Update selection UI
    grid.querySelectorAll('.chat-visual-option').forEach(opt => {
        opt.classList.remove('selected');
    });
    grid.querySelectorAll('.chat-visual-option').forEach(opt => {
        if (opt.dataset.url === imageUrl) {
            opt.classList.add('selected');
        }
    });
    
    showToast(`Visual selected for Scene ${sceneIndex + 1}`);
}

// Toggle pop-up option for a scene
function toggleScenePopup(sceneIndex, hasPopup) {
    sceneVisuals[sceneIndex] = {
        ...sceneVisuals[sceneIndex],
        hasPopup: hasPopup
    };
    
    const btn = document.getElementById(`popup-pick-btn-${sceneIndex}`);
    if (btn) {
        btn.style.display = hasPopup ? '' : 'none';
    }
}

// Open pop-up visual picker
async function openPopupPicker(sceneIndex) {
    const preview = document.getElementById(`popup-preview-${sceneIndex}`);
    if (!preview) return;
    
    preview.innerHTML = '<div style="color: var(--text-dim); font-size: 12px; padding: 8px;">Loading pop-up options...</div>';
    startLoading();
    
    const anchor = currentAnchors[sceneIndex];
    try {
        const response = await fetch('/scene-visuals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_text: anchor.anchor_text || '',
                scene_type: 'POPUP',
                keywords: ['icon', 'graphic', 'illustration', 'symbol']
            })
        });
        
        if (!response.ok) throw new Error('Server error');
        const data = await response.json();
        
        const allVisuals = [...(data.curated || []), ...(data.backgrounds || [])];
        if (allVisuals.length > 0) {
            preview.innerHTML = `
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px;">
                    ${allVisuals.slice(0, 8).map((img) => `
                        <div class="chat-visual-option popup-option" 
                             style="cursor: pointer; aspect-ratio: 1;"
                             data-url="${img.url}">
                            <img src="${img.url}" alt="Pop-up option" loading="lazy" style="pointer-events: none; width: 100%; height: 100%; object-fit: cover;">
                        </div>
                    `).join('')}
                </div>
            `;
            
            preview.querySelectorAll('.popup-option').forEach(el => {
                el.addEventListener('click', function() {
                    const url = this.dataset.url;
                    sceneVisuals[sceneIndex] = {
                        ...sceneVisuals[sceneIndex],
                        popupUrl: url
                    };
                    preview.querySelectorAll('.popup-option').forEach(opt => opt.classList.remove('selected'));
                    this.classList.add('selected');
                    showToast('Pop-up visual selected');
                });
            });
        } else {
            preview.innerHTML = '<div style="color: var(--text-dim); font-size: 12px; padding: 8px;">No pop-up visuals found.</div>';
        }
    } catch (err) {
        preview.innerHTML = '<div style="color: red; font-size: 12px; padding: 8px;">Error loading pop-up options.</div>';
    } finally {
        stopLoading();
    }
}

// Open visual picker for a specific scene from the workflow card
async function openSceneVisualPicker(sceneIndex) {
    const anchor = currentAnchors[sceneIndex];
    if (!anchor) return;
    
    // Show loading in the preview area
    const preview = document.getElementById(`scene-visual-preview-${sceneIndex}`);
    if (preview) {
        preview.innerHTML = '<div style="color: var(--text-dim); font-size: 12px;">Loading visuals...</div>';
    }
    
    try {
        const response = await fetch('/scene-visuals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_text: anchor.anchor_text || '',
                scene_type: anchor.anchor_type || 'CLAIM',
                keywords: anchor.keywords || []
            })
        });
        
        if (!response.ok) throw new Error('Server error');
        const data = await response.json();
        
        // Show visual options
        const allVisuals = [...(data.curated || []), ...(data.backgrounds || [])];
        if (preview && allVisuals.length > 0) {
            preview.innerHTML = `
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 0.5rem;">
                    ${allVisuals.slice(0, 6).map((img, imgIdx) => `
                        <div onclick="selectSceneVisual(${sceneIndex}, '${img.url.replace(/'/g, "\\'")}', this)" 
                             style="aspect-ratio: 16/9; border-radius: 6px; overflow: hidden; cursor: pointer; border: 2px solid transparent; transition: all 0.15s;"
                             class="scene-visual-option">
                            <img src="${img.url}" style="width: 100%; height: 100%; object-fit: cover;" loading="lazy">
                        </div>
                    `).join('')}
                </div>
            `;
        } else if (preview) {
            preview.innerHTML = '<div style="color: var(--text-dim); font-size: 12px;">No visuals found</div>';
        }
    } catch (err) {
        console.error('Error loading visuals:', err);
        if (preview) {
            preview.innerHTML = '<div style="color: red; font-size: 12px;">Error loading visuals</div>';
        }
    }
}

// Select a visual for a scene
function selectSceneVisual(sceneIndex, imageUrl, element) {
    sceneVisuals[sceneIndex] = { url: imageUrl, category: 'curated' };
    
    // Update selection UI
    const parent = element.parentElement;
    parent.querySelectorAll('.scene-visual-option').forEach(opt => {
        opt.style.borderColor = 'transparent';
    });
    element.style.borderColor = 'var(--gold)';
    
    showToast(`Visual selected for Scene ${sceneIndex + 1}`);
}

// Open inline visual picker for a scene with 3 categories
async function openInlineVisuals(sceneIndex) {
    console.log('[openInlineVisuals] Opening scene', sceneIndex, 'anchors:', currentAnchors.length);
    activeSceneIndex = sceneIndex;
    const anchor = currentAnchors[sceneIndex];
    if (!anchor) {
        console.warn('[openInlineVisuals] No anchor found at index', sceneIndex);
        return;
    }
    console.log('[openInlineVisuals] Anchor:', anchor);
    
    // Find characters that speak in this scene
    const sceneText = (anchor.anchor_text || '').toUpperCase();
    const sceneCharacters = detectedCharacters.filter(char => {
        const charName = (char.name || '').toUpperCase();
        return sceneText.includes(charName) || charName === 'NARRATOR';
    });
    
    // Create inline visual picker in chat with 3 sections
    const pickerId = `visual-picker-${sceneIndex}-${Date.now()}`;
    const pickerHtml = `
        <div class="chat-visual-picker" id="${pickerId}">
            <div class="chat-visual-picker-header">
                <span class="chat-visual-picker-title">Scene ${sceneIndex + 1}: ${anchor.anchor_type || 'SCENE'}</span>
            </div>
            <div class="chat-visual-picker-concept" id="${pickerId}-concept"></div>
            <div class="chat-visual-picker-script">${anchor.anchor_text || ''}</div>
            
            <div class="visual-category" id="${pickerId}-characters-section">
                <div class="visual-category-label">Characters in Scene</div>
                <div class="chat-visual-picker-grid character-models-grid" id="${pickerId}-characters">
                    ${sceneCharacters.length > 0 ? sceneCharacters.map(char => {
                        const safeName = (char.name || '').replace(/'/g, "\\'");
                        const safePersonality = (char.personality || '').replace(/'/g, "\\'");
                        const existingModel = extractedModels.find(m => m.name === char.name);
                        if (existingModel) {
                            return `<div class="character-model-card has-model" data-char="${safeName}">
                                <img src="${existingModel.image}" alt="${safeName}">
                                <span class="visual-label">${char.name}</span>
                            </div>`;
                        } else {
                            return `<div class="character-model-card no-model" data-char="${safeName}" onclick="generateCharacterImage('${safeName}', '${safePersonality}')">
                                <div class="char-placeholder">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                        <circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 4-6 8-6s8 2 8 6"/>
                                    </svg>
                                </div>
                                <span class="visual-label">${char.name}</span>
                                <span class="generate-hint">Click to generate</span>
                            </div>`;
                        }
                    }).join('') : '<div class="chat-visual-empty">No specific characters in this scene</div>'}
                </div>
            </div>
            
            <div class="visual-category">
                <div class="visual-category-label">Curated Visuals</div>
                <div class="chat-visual-picker-grid" id="${pickerId}-curated">
                    <div class="chat-visual-loading">Finding visuals...</div>
                </div>
            </div>
            
            <div class="visual-category">
                <div class="visual-category-label">Backgrounds</div>
                <div class="chat-visual-picker-grid" id="${pickerId}-backgrounds">
                    <div class="chat-visual-loading">Loading backgrounds...</div>
                </div>
            </div>
        </div>
    `;
    
    const messagesDiv = document.getElementById('messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message ai';
    msgDiv.innerHTML = pickerHtml;
    messagesDiv.appendChild(msgDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    
    // Fetch visuals
    try {
        const response = await fetch('/scene-visuals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_text: anchor.anchor_text || '',
                scene_type: anchor.anchor_type || 'CLAIM',
                keywords: anchor.keywords || []
            })
        });
        
        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }
        
        const text = await response.text();
        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error('Invalid JSON response:', text.substring(0, 200));
            throw new Error('Invalid response from server');
        }
        
        const concept = document.getElementById(`${pickerId}-concept`);
        const curatedGrid = document.getElementById(`${pickerId}-curated`);
        const bgGrid = document.getElementById(`${pickerId}-backgrounds`);
        
        if (concept) concept.textContent = data.suggestions?.visual_concept || '';
        
        // Store all images for selection (curated and backgrounds only, characters are handled separately)
        window.tempVisualImages = window.tempVisualImages || {};
        window.tempVisualImages[pickerId] = [...(data.curated || []), ...(data.backgrounds || [])];
        
        // Render Curated visuals
        if (data.curated && data.curated.length > 0) {
            curatedGrid.innerHTML = data.curated.map((img, imgIdx) => `
                <div class="chat-visual-option ${sceneVisuals[sceneIndex]?.url === img.url ? 'selected' : ''}" 
                     data-scene="${sceneIndex}" 
                     data-img-idx="${imgIdx}" 
                     data-picker="${pickerId}" 
                     data-category="curated"
                     style="cursor: pointer;">
                    <img src="${img.url}" alt="${img.alt || 'Visual'}" loading="lazy" style="pointer-events: none;">
                </div>
            `).join('');
            
            // Add click handlers with proper data
            curatedGrid.querySelectorAll('.chat-visual-option').forEach((el, idx) => {
                el.addEventListener('click', function() {
                    const img = data.curated[idx];
                    selectInlineVisual(sceneIndex, idx, pickerId, img.url, img.photographer || '', 'curated');
                });
            });
        } else {
            curatedGrid.innerHTML = '<div class="chat-visual-empty">No curated visuals found</div>';
        }
        
        // Render Backgrounds
        if (data.backgrounds && data.backgrounds.length > 0) {
            const curLen = (data.curated || []).length;
            bgGrid.innerHTML = data.backgrounds.map((img, imgIdx) => `
                <div class="chat-visual-option ${sceneVisuals[sceneIndex]?.url === img.url ? 'selected' : ''}" 
                     data-scene="${sceneIndex}" 
                     data-img-idx="${curLen + imgIdx}" 
                     data-picker="${pickerId}" 
                     data-category="background"
                     style="cursor: pointer;">
                    <img src="${img.url}" alt="${img.alt || 'Background'}" loading="lazy" style="pointer-events: none;">
                </div>
            `).join('');
            
            // Add click handlers with proper data
            bgGrid.querySelectorAll('.chat-visual-option').forEach((el, idx) => {
                el.addEventListener('click', function() {
                    const img = data.backgrounds[idx];
                    selectInlineVisual(sceneIndex, curLen + idx, pickerId, img.url, img.photographer || '', 'background');
                });
            });
        } else {
            bgGrid.innerHTML = '<div class="chat-visual-empty">No backgrounds found</div>';
        }
        
    } catch (err) {
        console.error('Error loading visuals:', err);
        const picker = document.getElementById(pickerId);
        if (picker) picker.innerHTML = '<div class="chat-visual-loading">Error loading visuals. Try again.</div>';
    }
}

// Select a visual from inline picker
function selectInlineVisual(sceneIndex, imageIndex, pickerId, imageUrl, photographer, category = 'curated') {
    console.log('[selectInlineVisual] Scene:', sceneIndex, 'Image:', imageIndex, 'URL:', imageUrl.substring(0, 50));
    
    // Store the visual with category
    sceneVisuals[sceneIndex] = {
        url: imageUrl,
        photographer: photographer,
        category: category
    };
    
    // Update the scene card thumbnail
    const sceneVisualEl = document.getElementById(`scene-visual-${sceneIndex}`);
    if (sceneVisualEl) {
        sceneVisualEl.innerHTML = `<img src="${imageUrl}" alt="Scene visual">`;
    }
    
    // Clear all selections first, then mark the clicked one
    const picker = document.getElementById(pickerId);
    if (picker) {
        // Remove selected from all options
        picker.querySelectorAll('.chat-visual-option').forEach(opt => {
            opt.classList.remove('selected');
        });
        
        // Find and select the one with matching URL
        picker.querySelectorAll('.chat-visual-option img').forEach(img => {
            if (img.src === imageUrl) {
                img.parentElement.classList.add('selected');
            }
        });
    }
    
    // Show confirmation message
    addMessage(`Visual selected for Scene ${sceneIndex + 1}. Click another image to change, or click "Next Scene" to continue.`, false);
}

// Open visual picker for a scene
async function openSceneVisuals(sceneIndex) {
    activeSceneIndex = sceneIndex;
    const anchor = currentAnchors[sceneIndex];
    if (!anchor) return;
    
    const popup = document.getElementById('scene-visual-popup');
    const title = document.getElementById('scene-visual-title');
    const grid = document.getElementById('scene-visual-grid');
    const concept = document.getElementById('scene-visual-concept');
    
    title.textContent = `Scene ${sceneIndex + 1}: ${anchor.anchor_type || 'SCENE'}`;
    grid.innerHTML = '<div class="loading-visuals">Finding visuals...</div>';
    concept.textContent = '';
    popup.classList.add('show');
    
    try {
        const response = await fetch('/scene-visuals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scene_text: anchor.anchor_text || '',
                scene_type: anchor.anchor_type || 'CLAIM',
                keywords: anchor.keywords || []
            })
        });
        
        const data = await response.json();
        if (data.success) {
            concept.textContent = data.suggestions?.visual_concept || '';
            
            if (data.images && data.images.length > 0) {
                grid.innerHTML = data.images.map((img, i) => `
                    <div class="visual-option" onclick="selectSceneVisual(${sceneIndex}, '${img.url || img.src?.medium || ''}', '${img.photographer || 'Unknown'}')">
                        <img src="${img.url || img.src?.medium || ''}" alt="Visual option ${i+1}">
                        <div class="visual-option-overlay">Select</div>
                    </div>
                `).join('');
            } else {
                grid.innerHTML = '<div class="no-visuals">No visuals found. Try different keywords.</div>';
            }
        } else {
            grid.innerHTML = '<div class="no-visuals">Could not load visuals.</div>';
        }
    } catch (err) {
        console.error('Scene visuals error:', err);
        grid.innerHTML = '<div class="no-visuals">Error loading visuals.</div>';
    }
}

// Select a visual for a scene
function selectSceneVisual(sceneIndex, url, photographer) {
    sceneVisuals[sceneIndex] = { url, photographer };
    
    const preview = document.getElementById(`scene-visual-${sceneIndex}`);
    if (preview) {
        preview.innerHTML = `<img src="${url}" alt="Scene visual">`;
    }
    
    closeSceneVisualPopup();
}

// Close visual popup
function closeSceneVisualPopup() {
    document.getElementById('scene-visual-popup').classList.remove('show');
    activeSceneIndex = null;
}

// Display Clip Suggestions
function displayClipSuggestions(clips) {
    clipSuggestions = clips || [];
    
    // Remove existing clips panel if present
    const existingPanel = document.getElementById('clips-panel');
    if (existingPanel) existingPanel.remove();
    
    if (clipSuggestions.length > 0) {
        const clipsHtml = `
            <div class="clips-panel" id="clips-panel">
                <div class="clips-header">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="2" y="4" width="20" height="16" rx="2"/>
                        <path d="M10 10l4 4m0-4l-4 4"/>
                    </svg>
                    Suggested Clips (${clipSuggestions.length})
                </div>
                ${clipSuggestions.slice(0, 5).map((clip, i) => `
                    <div class="clip-item">
                        <div class="clip-text">${clip.clip_text ? clip.clip_text.substring(0, 200) + '...' : 'Clip ' + (i+1)}</div>
                        <div class="clip-meta">
                            <span class="clip-score">
                                Thesis: 
                                <span class="clip-score-bar"><span class="clip-score-fill" style="width:${(clip.thesis_alignment || 0.5) * 100}%"></span></span>
                            </span>
                            <span class="clip-score">
                                Hook: 
                                <span class="clip-score-bar"><span class="clip-score-fill" style="width:${(clip.hook_potential || 0.5) * 100}%"></span></span>
                            </span>
                            <button class="use-clip-btn" onclick="useClip(${i})">Use This</button>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
        
        const thesisPanel = document.getElementById('thesis-panel');
        thesisPanel.insertAdjacentHTML('afterend', clipsHtml);
    }
}

// Use a suggested clip
function useClip(index) {
    const clip = clipSuggestions[index];
    if (clip && clip.clip_text) {
        currentScript = clip.clip_text;
        updateScriptCard(clip.clip_text);
        
        // Move to review stage
        addMessage(`Using clip ${index + 1}. Let's refine it for your video.`);
        showReviewConfirmation();
    }
}

// Edit Thesis
function editThesis() {
    const current = currentThesis?.thesis_statement || '';
    const newThesis = prompt('Edit the core thesis:', current);
    if (newThesis && newThesis !== current) {
        currentThesis = { ...currentThesis, thesis_statement: newThesis, is_user_confirmed: true };
        displayThesis(currentThesis);
        addMessage(`Thesis updated: "${newThesis}". Regenerating with this focus.`);
    }
}

// Handle Unified Engine Response
function handleUnifiedResponse(result) {
    if (result.status === 'needs_clarification') {
        // AI needs clarification - show with clickable options
        const question = result.question || 'What is the main point you want to make?';
        // Use options from backend if available, otherwise extract from question
        const options = (result.options && result.options.length > 0) 
            ? result.options 
            : extractOptionsFromQuestion(question);
        addMessageWithOptions(question, options);
        if (result.thesis) displayThesis(result.thesis);
        
        // Set flag to indicate we're awaiting clarification
        awaitingClarification = true;
        
        // Track clarification number
        if (result.clarification_number) {
            console.log(`[Clarification ${result.clarification_number}/3]`);
        }
    } else if (result.status === 'ready') {
        if (result.mode === 'clip') {
            // Clipping mode - show suggested clips
            displayThesis(result.result?.thesis);
            displayClipSuggestions(result.result?.recommended_clips);
            
            const clipCount = result.result?.recommended_clips?.length || 0;
            const learnNote = result.result?.learnings ? ' Learning from this content...' : '';
            addMessage(`Found ${clipCount} potential clips.${learnNote} Select one to use as your script.`);
        } else {
            // Create mode - show thesis first, then script
            // IMPORTANT: Do NOT show visuals yet - wait for script confirmation
            displayThesis(result.thesis);
            
            // Store anchors for later (after script confirmation)
            currentAnchors = result.anchors || [];
            
            // Display content type badge
            if (result.content_type) {
                displayContentType(result.content_type);
            }
            
            // Store visual plan for later
            if (result.visual_plan) {
                currentVisualPlan = result.visual_plan;
            }
            
            if (result.script?.full_script) {
                currentScript = result.script;
                updateScriptCard(result.script.full_script);
                
                // Also update the document editor
                docHasScript = true;
                displayScriptInEditor(result.script.full_script);
                updateTimelineControls(true);
                // Script card inline now handles the flow - no showAIFeedback actions needed
                
                const contentTypeLabel = result.content_type ? `<span class="content-type-badge ${result.content_type}">${result.content_type}</span> ` : '';
                const learnNote = result.learned_patterns_applied ? 
                    ' <span class="learning-badge"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg> Applied learned patterns</span>' : '';
                
                addMessage(`${contentTypeLabel}Script ready with ${result.anchors?.length || 0} anchor points.${learnNote}`, false, true);
                showReviewConfirmation();
                
                // Validate loop score against thesis
                if (result.thesis?.thesis_statement) {
                    validateLoopScore(result.thesis.thesis_statement, result.script);
                }
            }
        }
    }
}

// Current project tracking
let currentProjectId = null;
let generatingProjectId = null;
let aiLearningData = { learning_progress: 0, can_auto_generate: false };
let autoGenerateConfidence = { is_unlocked: false, liked_count: 0, unlock_threshold: 5, progress_message: '0/5 videos liked to unlock' };

// Auto-save state
let saveTimeout = null;
let isSaving = false;
let lastSavedContent = null;

// Centralized token balance update function
function updateAllTokenDisplays(balance, monthlyTokens = null) {
    const balanceNum = parseInt(balance) || 0;
    const formattedBalance = balanceNum.toLocaleString();
    
    // Header token count
    const headerTokenCount = document.getElementById('header-token-count');
    if (headerTokenCount) headerTokenCount.textContent = formattedBalance;
    
    // Header token pill low state
    const headerTokenPill = document.getElementById('header-token-pill');
    if (headerTokenPill) headerTokenPill.classList.toggle('low', balanceNum < 20);
    
    // Dashboard token balance
    const dashboardTokenEl = document.getElementById('dashboard-token-balance');
    if (dashboardTokenEl) dashboardTokenEl.textContent = formattedBalance;
    
    // Billing token balance
    const billingTokenEl = document.getElementById('billing-token-balance');
    if (billingTokenEl) billingTokenEl.textContent = formattedBalance;
    
    // Billing progress bar
    if (monthlyTokens) {
        const monthlyEl = document.getElementById('billing-monthly-tokens');
        const barEl = document.getElementById('billing-token-bar');
        if (monthlyEl) monthlyEl.textContent = monthlyTokens + ' tokens';
        if (barEl) barEl.style.width = Math.min(100, (balanceNum / monthlyTokens) * 100) + '%';
    }
}

// Fetch and update token balance from server
async function refreshTokenBalance() {
    try {
        const response = await fetch('/get-tokens');
        const data = await response.json();
        if (data.balance !== undefined) {
            updateAllTokenDisplays(data.balance, data.monthly_tokens || null);
        }
    } catch (err) {
        console.error('Failed to refresh token balance:', err);
    }
}

// Save indicator functions
function showSaveIndicator(state) {
    const indicator = document.getElementById('save-indicator');
    const saveText = indicator?.querySelector('.save-text');
    if (!indicator) return;
    
    indicator.style.display = 'flex';
    
    if (state === 'saving') {
        indicator.classList.add('saving');
        if (saveText) saveText.textContent = 'Saving...';
    } else if (state === 'saved') {
        indicator.classList.remove('saving');
        if (saveText) saveText.textContent = 'Saved';
        // Hide after 3 seconds
        setTimeout(() => {
            if (!isSaving) {
                indicator.style.display = 'none';
            }
        }, 3000);
    }
}

function hideSaveIndicator() {
    const indicator = document.getElementById('save-indicator');
    if (indicator) indicator.style.display = 'none';
}

// Debounced auto-save function
function triggerAutoSave(data) {
    if (saveTimeout) {
        clearTimeout(saveTimeout);
    }
    
    saveTimeout = setTimeout(async () => {
        if (!currentProjectId || isSaving) return;
        
        isSaving = true;
        showSaveIndicator('saving');
        
        try {
            const response = await fetch(`/projects/${currentProjectId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            
            if (response.ok) {
                showSaveIndicator('saved');
                lastSavedContent = JSON.stringify(data);
            } else {
                console.error('Auto-save failed');
            }
        } catch (error) {
            console.error('Auto-save error:', error);
        } finally {
            isSaving = false;
        }
    }, 1500); // 1.5 second debounce
}

// Fetch auto-generate confidence status
async function fetchAutoGenerateConfidence() {
    try {
        const response = await fetch('/generator-confidence');
        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                autoGenerateConfidence = data;
            }
        }
    } catch (e) {
        console.log('Could not fetch auto-generate confidence');
    }
}

// Generator settings state
let generatorSettings = {
    tone: 'neutral',
    format_type: 'explainer',
    target_length: 45,
    voice_style: 'news_anchor',
    enabled_topics: []
};

// Toggle generator settings panel
function toggleGeneratorSettings() {
    const body = document.getElementById('generator-settings-body');
    const icon = document.getElementById('settings-toggle-icon');
    body.classList.toggle('open');
    icon.textContent = body.classList.contains('open') ? '▼' : '▶';
}

// Update length display
function updateLengthDisplay(value) {
    document.getElementById('length-display').textContent = value + 's';
}

// Toggle topic selection
function toggleTopic(btn) {
    btn.classList.toggle('active');
    const topic = btn.dataset.topic;
    const index = generatorSettings.enabled_topics.indexOf(topic);
    if (index > -1) {
        generatorSettings.enabled_topics.splice(index, 1);
    } else {
        generatorSettings.enabled_topics.push(topic);
    }
    saveGeneratorSettings();
}

// Update a generator setting
async function updateGeneratorSetting(key, value) {
    generatorSettings[key] = value;
    await saveGeneratorSettings();
}

// Save generator settings to server
async function saveGeneratorSettings() {
    try {
        await fetch('/generator-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(generatorSettings)
        });
    } catch (e) {
        console.log('Could not save settings');
    }
}

// Load generator settings from server
async function loadGeneratorSettings() {
    try {
        const response = await fetch('/generator-settings');
        if (response.ok) {
            const data = await response.json();
            if (data.success && data.settings) {
                generatorSettings = data.settings;
                applySettingsToUI();
            }
        }
    } catch (e) {
        console.log('Could not load settings');
    }
}

// Apply settings to UI elements
function applySettingsToUI() {
    const toneEl = document.getElementById('setting-tone');
    const formatEl = document.getElementById('setting-format');
    const lengthEl = document.getElementById('setting-length');
    const voiceEl = document.getElementById('setting-voice');
    
    if (toneEl) toneEl.value = generatorSettings.tone;
    if (formatEl) formatEl.value = generatorSettings.format_type;
    if (lengthEl) {
        lengthEl.value = generatorSettings.target_length;
        updateLengthDisplay(generatorSettings.target_length);
    }
    if (voiceEl) voiceEl.value = generatorSettings.voice_style;
    
    // Apply topic toggles
    document.querySelectorAll('.topic-toggle').forEach(btn => {
        const topic = btn.dataset.topic;
        if (generatorSettings.enabled_topics && generatorSettings.enabled_topics.includes(topic)) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}

// Update auto-gen status display
function updateAutoGenStatus() {
    const statusEl = document.getElementById('auto-gen-status');
    const iconEl = document.getElementById('auto-gen-icon');
    const textEl = document.getElementById('auto-gen-text');
    
    if (autoGenerateConfidence.is_unlocked) {
        statusEl.classList.add('unlocked');
        iconEl.textContent = '✦';
        textEl.textContent = 'Auto-generation unlocked! AI has learned your style.';
    } else {
        statusEl.classList.remove('unlocked');
        iconEl.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>';
        textEl.textContent = autoGenerateConfidence.progress_message;
    }
    
    // Update the dashboard banner
    const banner = document.getElementById('autogen-unlock-banner');
    const bannerProgress = document.getElementById('banner-progress');
    
    if (banner && bannerProgress) {
        if (autoGenerateConfidence.is_unlocked) {
            banner.classList.add('unlocked');
            banner.querySelector('.banner-text').textContent = 'Auto Generate Content';
            banner.querySelector('.banner-icon').textContent = '✦';
            bannerProgress.textContent = 'Unlocked!';
            bannerProgress.style.color = 'var(--gold)';
        } else {
            banner.classList.remove('unlocked');
            banner.querySelector('.banner-text').textContent = 'Auto Generate Content';
            banner.querySelector('.banner-icon').textContent = '✦';
            bannerProgress.textContent = `${autoGenerateConfidence.liked_count || 0}/5`;
            bannerProgress.style.color = 'var(--text-dim)';
        }
    }
}

// Show delete confirmation modal
function showDeleteConfirmation(projectId, event) {
    event.stopPropagation();
    
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.id = 'delete-confirm-modal';
    modal.innerHTML = `
        <div class="modal-content delete-modal">
            <h2>Are you sure?</h2>
            <p>This project will be permanently deleted. This action cannot be undone.</p>
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="document.getElementById('delete-confirm-modal').remove();">Cancel</button>
                <button class="btn btn-danger" onclick="confirmDeleteProject(${projectId})">Delete</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

// Confirm and delete project
async function confirmDeleteProject(projectId) {
    const modal = document.getElementById('delete-confirm-modal');
    if (modal) modal.remove();
    
    try {
        const response = await fetch(`/project/${projectId}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            // Reload the page to ensure clean state
            window.location.reload();
        } else {
            alert(data.error || 'Could not delete project');
        }
    } catch (error) {
        alert('Could not delete project');
    }
}

// Handle auto-generate button click
async function handleAutoGenerate(projectId, event) {
    event.stopPropagation();
    
    if (!autoGenerateConfidence.is_unlocked) {
        showNotification('Keep creating! ' + autoGenerateConfidence.progress_message, 'info');
        return;
    }
    
    const btn = event.currentTarget;
    btn.disabled = true;
    btn.innerHTML = '<span class="sparkle-icon">⟳</span> Generating...';
    
    try {
        const response = await fetch('/auto-generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_id: projectId })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showNotification('Content auto-generated!', 'success');
            // Refresh the project list
            loadProjects();
            // Open the new project if created
            if (data.project_id) {
                openProject(data.project_id);
            }
        } else {
            showNotification(data.error || 'Auto-generation failed', 'error');
        }
    } catch (error) {
        showNotification('Auto-generation failed', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = autoGenerateConfidence.is_unlocked ? 
            '<span class="sparkle-icon">✦</span> Auto-Generate' : 
            '<span class="sparkle-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span> ' + autoGenerateConfidence.progress_message;
    }
}

// Update progress on project card
function updateProjectCardProgress(projectId, percent, label) {
    const fill = document.getElementById(`project-progress-fill-${projectId}`);
    const labelEl = document.getElementById(`project-progress-label-${projectId}`);
    const progressContainer = document.getElementById(`project-progress-${projectId}`);
    const card = document.getElementById(`project-card-${projectId}`);
    
    if (fill) fill.style.width = percent + '%';
    if (labelEl) labelEl.textContent = label;
    if (progressContainer) progressContainer.classList.add('active');
    if (card) card.classList.add('generating');
}

function clearProjectCardProgress(projectId) {
    const progressContainer = document.getElementById(`project-progress-${projectId}`);
    const card = document.getElementById(`project-card-${projectId}`);
    
    if (progressContainer) progressContainer.classList.remove('active');
    if (card) card.classList.remove('generating');
    generatingProjectId = null;
}

function getStepName(step) {
    const names = { 1: 'Script', 2: 'Voice', 3: 'Visuals', 4: 'Ready' };
    return names[step] || '';
}

async function updateProjectWorkflowStep(step) {
    if (!currentProjectId) return;
    try {
        await fetch(`/projects/${currentProjectId}/workflow-step`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ step })
        });
    } catch (err) {
        console.error('Error updating workflow step:', err);
    }
}

// Device detection - adds classes to body for device-specific styling
(function detectDevice() {
    const ua = navigator.userAgent;
    const body = document.body;
    
    // Device type detection
    const isIPhone = /iPhone/i.test(ua);
    const isIPad = /iPad/i.test(ua);
    const isAndroid = /Android/i.test(ua);
    const isIOS = isIPhone || isIPad || /Mac/i.test(ua) && 'ontouchend' in document;
    const isMobile = isIPhone || isAndroid || /Mobile/i.test(ua);
    const isTablet = isIPad || (isAndroid && !/Mobile/i.test(ua));
    const isDesktop = !isMobile && !isTablet;
    
    // Add classes to body
    if (isIPhone) body.classList.add('is-iphone');
    if (isIPad) body.classList.add('is-ipad');
    if (isIOS) body.classList.add('is-ios');
    if (isAndroid) body.classList.add('is-android');
    if (isMobile) body.classList.add('is-mobile');
    if (isTablet) body.classList.add('is-tablet');
    if (isDesktop) body.classList.add('is-desktop');
    
    // Touch device detection
    if ('ontouchstart' in window || navigator.maxTouchPoints > 0) {
        body.classList.add('is-touch');
    } else {
        body.classList.add('is-pointer');
    }
    
    // Expose for JavaScript use
    window.deviceInfo = { isIPhone, isIPad, isIOS, isAndroid, isMobile, isTablet, isDesktop };
})();

// Restore conversation from localStorage on page load
document.addEventListener('DOMContentLoaded', () => {
    restoreConversation();
    loadDashboardProjects();
    checkSubscriptionStatus();
    
    // Restore workflow state if available
    const hasState = restoreWorkflowState();
    if (hasState && currentProjectId) {
        // Re-render the UI based on restored state
        updateProgressIndicator();
        if (currentAnchors.length > 0) {
            rebuildSceneUI();
        }
        console.log('[Init] Restored workflow at step', currentWorkflowStep);
    }
    
    // Show chat panel on non-create stages
    const currentStage = document.querySelector('.stage.active')?.id;
    if (currentStage !== 'stage-create' && currentStage !== 'stage-projects') {
        document.getElementById('chat-toggle').classList.remove('hidden');
    }
});

function rebuildSceneUI() {
    if (!currentAnchors || currentAnchors.length === 0) return;
    
    // Rebuild visual pickers for each scene that has visuals selected
    currentAnchors.forEach((anchor, idx) => {
        if (sceneVisuals[idx]) {
            const visual = sceneVisuals[idx];
            console.log(`[Rebuild] Scene ${idx + 1} has visual:`, visual.type);
        }
    });
    
    // Update progress indicator
    updateProgressIndicator();
}

async function checkSubscriptionStatus() {
    try {
        const resp = await fetch('/subscription-status');
        const data = await resp.json();
        isPro = data.is_pro === true;
        updateSubscriptionUI();
    } catch (err) {
        console.log('Could not check subscription status');
    }
}

function updateSubscriptionUI() {
    const badge = document.getElementById('pro-badge');
    if (badge) {
        badge.style.display = isPro ? 'inline-flex' : 'none';
    }
}

async function startSubscription(tier = 'pro') {
    try {
        showNotification('Redirecting to checkout...', 'info');
        const resp = await fetch('/create-subscription', { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tier: tier })
        });
        const data = await resp.json();
        if (data.url) {
            window.location.href = data.url;
        } else {
            showToast(data.error || 'Could not start checkout');
        }
    } catch (err) {
        showToast('Error starting subscription');
    }
}

function showUpgradePrompt() {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-content upgrade-modal" style="max-width: 800px;">
            <h2 style="margin-bottom: 8px;">Choose Your Plan</h2>
            <p style="color: var(--text-muted); margin-bottom: 24px;">Unlock video generation and more features</p>
            
            <div class="pricing-tiers" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 24px;">
                <!-- Free Tier -->
                <div class="pricing-tier" style="background: rgba(0,0,0,0.3); border-radius: 12px; padding: 20px; border: 1px solid rgba(255,255,255,0.1);">
                    <h3 style="font-size: 18px; margin-bottom: 4px; color: var(--text);">Free</h3>
                    <div style="font-size: 28px; font-weight: 700; color: var(--text); margin-bottom: 4px;">$0<span style="font-size: 14px; font-weight: 400; color: var(--text-muted);">/month</span></div>
                    <div style="font-size: 13px; color: var(--gold); margin-bottom: 16px;">50 tokens/month</div>
                    <ul style="list-style: none; padding: 0; margin: 0; font-size: 13px; color: var(--text-muted);">
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Script generation</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> AI voices</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #ef4444;">✗</span> Video export</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #ef4444;">✗</span> Premium voices</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #ef4444;">✗</span> Unlimited revisions</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #ef4444;">✗</span> Auto-generator</li>
                    </ul>
                    <button class="btn btn-secondary" style="width: 100%; margin-top: 16px;" disabled>Current Plan</button>
                </div>
                
                <!-- Creator Tier -->
                <div class="pricing-tier" style="background: rgba(0,0,0,0.3); border-radius: 12px; padding: 20px; border: 1px solid var(--gold);">
                    <h3 style="font-size: 18px; margin-bottom: 4px; color: var(--text);">Creator</h3>
                    <div style="font-size: 28px; font-weight: 700; color: var(--text); margin-bottom: 4px;">$10<span style="font-size: 14px; font-weight: 400; color: var(--text-muted);">/month</span></div>
                    <div style="font-size: 13px; color: var(--gold); margin-bottom: 16px;">300 tokens/month</div>
                    <ul style="list-style: none; padding: 0; margin: 0; font-size: 13px; color: var(--text-muted);">
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Script generation</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> AI voices</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Video export</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Premium voices</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #ef4444;">✗</span> Unlimited revisions</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #ef4444;">✗</span> Auto-generator</li>
                    </ul>
                    <button class="btn btn-primary" style="width: 100%; margin-top: 16px;" onclick="startSubscription('creator'); this.closest('.modal-overlay').remove();">Get Creator</button>
                </div>
                
                <!-- Pro Tier -->
                <div class="pricing-tier" style="background: linear-gradient(135deg, rgba(255,214,10,0.1) 0%, rgba(0,0,0,0.3) 100%); border-radius: 12px; padding: 20px; border: 2px solid var(--gold); position: relative;">
                    <div style="position: absolute; top: -10px; left: 50%; transform: translateX(-50%); background: var(--gold); color: #000; font-size: 11px; font-weight: 600; padding: 2px 12px; border-radius: 10px;">BEST VALUE</div>
                    <h3 style="font-size: 18px; margin-bottom: 4px; color: var(--text);">Pro</h3>
                    <div style="font-size: 28px; font-weight: 700; color: var(--gold); margin-bottom: 4px;">$25<span style="font-size: 14px; font-weight: 400; color: var(--text-muted);">/month</span></div>
                    <div style="font-size: 13px; color: var(--gold); margin-bottom: 16px;">1000 tokens/month</div>
                    <ul style="list-style: none; padding: 0; margin: 0; font-size: 13px; color: var(--text-muted);">
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Script generation</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> AI voices</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Video export</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Premium voices</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Unlimited revisions</li>
                        <li style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;"><span style="color: #4ade80;">✓</span> Auto-generator</li>
                    </ul>
                    <button class="btn btn-primary" style="width: 100%; margin-top: 16px; background: var(--gold); color: #000;" onclick="startSubscription('pro'); this.closest('.modal-overlay').remove();">Get Pro</button>
                </div>
            </div>
            
            <!-- Token Purchase Section -->
            <div style="border-top: 1px solid rgba(255,255,255,0.1); padding-top: 20px; margin-top: 8px;">
                <h3 style="font-size: 16px; margin-bottom: 12px; color: var(--text);">Or Buy Tokens Directly</h3>
                <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 16px; background: rgba(255,214,10,0.1); padding: 10px; border-radius: 8px; border-left: 3px solid var(--gold);">
                    Tokens are used for: Video rendering, AI voiceovers, and Auto-generator. Script generation is always free. Tokens never expire.
                </p>
                
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px;">
                    <button class="btn btn-secondary" onclick="purchaseTokens(50); this.closest('.modal-overlay').remove();" style="padding: 12px 8px; flex-direction: column; display: flex; align-items: center;">
                        <span style="font-size: 18px; font-weight: 700;">50</span>
                        <span style="font-size: 11px; color: var(--text-muted);">tokens</span>
                        <span style="font-size: 14px; color: var(--gold); margin-top: 4px;">$5</span>
                    </button>
                    <button class="btn btn-secondary" onclick="purchaseTokens(150); this.closest('.modal-overlay').remove();" style="padding: 12px 8px; flex-direction: column; display: flex; align-items: center;">
                        <span style="font-size: 18px; font-weight: 700;">150</span>
                        <span style="font-size: 11px; color: var(--text-muted);">tokens</span>
                        <span style="font-size: 14px; color: var(--gold); margin-top: 4px;">$12</span>
                    </button>
                    <button class="btn btn-secondary" onclick="purchaseTokens(400); this.closest('.modal-overlay').remove();" style="padding: 12px 8px; flex-direction: column; display: flex; align-items: center; border-color: var(--gold);">
                        <span style="font-size: 18px; font-weight: 700;">400</span>
                        <span style="font-size: 11px; color: var(--text-muted);">tokens</span>
                        <span style="font-size: 14px; color: var(--gold); margin-top: 4px;">$25</span>
                    </button>
                    <button class="btn btn-secondary" onclick="purchaseTokens(1000); this.closest('.modal-overlay').remove();" style="padding: 12px 8px; flex-direction: column; display: flex; align-items: center;">
                        <span style="font-size: 18px; font-weight: 700;">1000</span>
                        <span style="font-size: 11px; color: var(--text-muted);">tokens</span>
                        <span style="font-size: 14px; color: var(--gold); margin-top: 4px;">$50</span>
                    </button>
                </div>
                
                <div style="font-size: 11px; color: var(--text-muted); background: rgba(0,0,0,0.3); padding: 12px; border-radius: 8px;">
                    <strong style="color: var(--text); display: block; margin-bottom: 6px;">Token Costs:</strong>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 4px;">
                        <span>Video render (base): 25 tokens</span>
                        <span>Extra character: +3 tokens</span>
                        <span>Sound effect: +1 token each</span>
                        <span>Script generation: Free</span>
                    </div>
                </div>
            </div>
            
            <button class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove();" style="margin-top: 16px;">Maybe Later</button>
        </div>
    `;
    document.body.appendChild(modal);
}

// Token purchase function
async function purchaseTokens(amount) {
    const prices = { 50: 500, 150: 1200, 400: 2500, 1000: 5000 }; // cents
    const priceInCents = prices[amount];
    if (!priceInCents) {
        showToast('Invalid token amount');
        return;
    }
    
    showToast('Redirecting to payment...');
    
    try {
        const response = await fetch('/create-token-checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tokens: amount, price: priceInCents })
        });
        const data = await response.json();
        if (data.url) {
            window.location.href = data.url;
        } else {
            showError('Payment Failed', data.error || 'Unable to process payment. Please try again.');
        }
    } catch (err) {
        showError('Payment Error', err.message || 'Connection error. Please try again.');
    }
}

// === PROJECT DASHBOARD FUNCTIONS ===

// Load projects for the main dashboard (initial view)
async function loadDashboardProjects() {
    const grid = document.getElementById('dashboard-projects-grid');
    if (!grid) return;
    
    try {
        // Fetch confidence for banner
        await fetchAutoGenerateConfidence();
        updateAutoGenStatus();
        
        const response = await fetch('/projects');
        let projects = [];
        let learning = { learning_progress: 0, total_projects: 0, successful_projects: 0, can_auto_generate: false };
        
        if (response.ok) {
            const data = await response.json();
            projects = data.projects || [];
            learning = data.ai_learning || learning;
        }
        
        // Update learning card
        updateDashboardLearning(learning);
        
        // Render project cards
        renderDashboardProjects(projects);
    } catch (err) {
        console.warn('Could not load projects:', err);
        // Still show empty state
        renderDashboardProjects([]);
        updateDashboardLearning({ learning_progress: 0, total_projects: 0, successful_projects: 0 });
    }
}

function renderDashboardProjects(projects) {
    const grid = document.getElementById('dashboard-projects-grid');
    if (!grid) return;
    
    let html = '';
    
    // Show empty state if no projects
    if (!projects || projects.length === 0) {
        const likedCount = autoGenerateConfidence.liked_count || 0;
        const remaining = 5 - likedCount;
        html = `
            <div class="empty-state-container" style="text-align: center; padding: 40px 20px; max-height: calc(100vh - 120px); overflow-y: auto;">
                <div style="margin-bottom: 16px; opacity: 0.8;"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/></svg></div>
                <h3 style="font-size: 20px; font-weight: 600; margin-bottom: 8px; color: var(--text);">Start Your First Project</h3>
                <p style="color: var(--text-dim); margin-bottom: 16px; max-width: 340px; margin-left: auto; margin-right: auto;">All templates include integrated video clipping and AI visual curation. Pick one to get started.</p>
                
                <div style="display: flex; justify-content: center; gap: 24px; margin-bottom: 24px; flex-wrap: wrap;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div style="width: 24px; height: 24px; border-radius: 50%; background: var(--gold); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: var(--dark-green);">1</div>
                        <span style="font-size: 12px; color: var(--text-dim);">Write Script</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div style="width: 24px; height: 24px; border-radius: 50%; background: rgba(255,214,10,0.3); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: var(--gold);">2</div>
                        <span style="font-size: 12px; color: var(--text-dim);">Add Voice</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div style="width: 24px; height: 24px; border-radius: 50%; background: rgba(255,214,10,0.3); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: var(--gold);">3</div>
                        <span style="font-size: 12px; color: var(--text-dim);">Pick Visuals</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <div style="width: 24px; height: 24px; border-radius: 50%; background: rgba(255,214,10,0.3); display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: var(--gold);">4</div>
                        <span style="font-size: 12px; color: var(--text-dim);">Export</span>
                    </div>
                </div>
                
                <div class="template-grid" style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; max-width: 400px; margin: 0 auto 24px;">
                    <div class="template-card" onclick="createProjectFromTemplate('hot_take')" style="cursor: pointer; background: rgba(255,214,10,0.08); border: 1px solid rgba(255,214,10,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #ffd60a;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Hot Take</div>
                        <div style="font-size: 11px; color: var(--text-dim);">30-45s opinion piece</div>
                    </div>
                    <div class="template-card" onclick="createProjectFromTemplate('explainer')" style="cursor: pointer; background: rgba(78,205,196,0.08); border: 1px solid rgba(78,205,196,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #4ecdc4;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Explainer</div>
                        <div style="font-size: 11px; color: var(--text-dim);">45-60s breakdown</div>
                    </div>
                    <div class="template-card" onclick="createProjectFromTemplate('story')" style="cursor: pointer; background: rgba(155,89,182,0.08); border: 1px solid rgba(155,89,182,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #9b59b6;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Story Time</div>
                        <div style="font-size: 11px; color: var(--text-dim);">60-75s narrative</div>
                    </div>
                    <div class="template-card" onclick="createProjectFromTemplate('commentary')" style="cursor: pointer; background: rgba(231,76,60,0.08); border: 1px solid rgba(231,76,60,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #e74c3c;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 20H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v1"/><path d="M18 14h3"/><path d="M18 18h3"/><path d="M18 10h3"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Commentary</div>
                        <div style="font-size: 11px; color: var(--text-dim);">35-50s reaction</div>
                    </div>
                    <div class="template-card" onclick="createProjectFromTemplate('letter')" style="cursor: pointer; background: rgba(46,204,113,0.08); border: 1px solid rgba(46,204,113,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #2ecc71;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Open Letter</div>
                        <div style="font-size: 11px; color: var(--text-dim);">45-60s direct address</div>
                    </div>
                    <div class="template-card" onclick="createProjectFromTemplate('meme')" style="cursor: pointer; background: rgba(255,165,0,0.08); border: 1px solid rgba(255,165,0,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #ffa500;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Meme / Funny</div>
                        <div style="font-size: 11px; color: var(--text-dim);">Trend-driven comedy</div>
                    </div>
                    <div class="template-card" onclick="createProjectFromTemplate('ad')" style="cursor: pointer; background: rgba(52,152,219,0.08); border: 1px solid rgba(52,152,219,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #3498db;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Make an Ad</div>
                        <div style="font-size: 11px; color: var(--text-dim);">Product promo video</div>
                    </div>
                    <div class="template-card" onclick="createProjectFromTemplate('tiktok_edit')" style="cursor: pointer; background: rgba(238,88,166,0.08); border: 1px solid rgba(238,88,166,0.2); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s;">
                        <div style="margin-bottom: 8px; color: #ee58a6;"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">TikTok Edit</div>
                        <div style="font-size: 11px; color: var(--text-dim);">Beat-synced visual edit</div>
                    </div>
                    <div class="template-card" onclick="openCustomTemplateCreator()" style="cursor: pointer; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 16px; text-align: left; transition: all 0.2s; grid-column: span 2;">
                        <div style="margin-bottom: 8px; color: var(--text-dim);"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></div>
                        <div style="font-weight: 600; font-size: 14px; margin-bottom: 4px;">Custom Template</div>
                        <div style="font-size: 11px; color: var(--text-dim);">Upload a video to learn its style</div>
                    </div>
                </div>
                
                <div style="background: rgba(255,214,10,0.1); border: 1px solid rgba(255,214,10,0.2); border-radius: 12px; padding: 16px; max-width: 350px; margin: 0 auto;">
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                        <span style="color: var(--gold);">✦</span>
                        <span style="font-weight: 600; font-size: 13px;">Unlock Auto-Generate</span>
                    </div>
                    <p style="font-size: 12px; color: var(--text-dim); margin-bottom: 8px;">
                        Like ${remaining > 0 ? remaining + ' more videos' : 'videos'} to unlock AI auto-generation. Echo Engine learns from what you approve.
                    </p>
                    <div style="display: flex; gap: 4px;">
                        ${[1,2,3,4,5].map(i => `<div style="width: 20%; height: 4px; border-radius: 2px; background: ${i <= likedCount ? 'var(--gold)' : 'rgba(255,255,255,0.1)'};"></div>`).join('')}
                    </div>
                </div>
            </div>
        `;
        grid.innerHTML = html;
        return;
    }
    
    // Add existing projects
    projects.forEach(p => {
        const isGenerating = generatingProjectId === p.id;
        const step = p.workflow_step || 1;
        const isReady = step >= 4; // Ready for generation at step 4+
        
        // Generate workflow step dots with labels
        const stepNames = ['Script', 'Voice', 'Visuals', 'Export'];
        let stepDots = '';
        for (let i = 1; i <= 4; i++) {
            const stepClass = i < step ? 'complete' : (i === step ? 'active' : '');
            const dotClass = i < step ? 'complete' : (i === step ? 'active' : '');
            stepDots += `
                <div class="workflow-step ${stepClass}">
                    <div class="workflow-step-dot ${dotClass}"></div>
                    <span class="workflow-step-label">${stepNames[i-1]}</span>
                    ${i < 4 ? '<div class="workflow-step-line"></div>' : ''}
                </div>`;
        }
        
        // Get template icon (matching template picker exactly)
        const templateIcons = {
            'hot_take': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ffd60a" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
            'explainer': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#4ecdc4" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
            'story': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#9b59b6" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
            'story_time': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#9b59b6" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
            'commentary': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#e74c3c" stroke-width="2"><path d="M19 20H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v1"/><path d="M18 14h3"/><path d="M18 18h3"/><path d="M18 10h3"/></svg>',
            'letter': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#2ecc71" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>',
            'open_letter': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#2ecc71" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>',
            'meme': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f39c12" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>',
            'meme_funny': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#f39c12" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>',
            'ad': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3498db" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>',
            'make_an_ad': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#3498db" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>',
            'tiktok_edit': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ee58a6" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>',
            'start_from_scratch': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ffd60a" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>'
        };
        const defaultIcon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ffd60a" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
        const templateIcon = templateIcons[p.template_type] || defaultIcon;
        
        // Get template display name
        const templateNames = {
            'hot_take': 'Hot Take',
            'explainer': 'Explainer',
            'story': 'Story Time',
            'story_time': 'Story Time',
            'commentary': 'Commentary',
            'letter': 'Open Letter',
            'open_letter': 'Open Letter',
            'meme': 'Meme',
            'meme_funny': 'Meme',
            'ad': 'Ad',
            'make_an_ad': 'Ad',
            'tiktok_edit': 'TikTok Edit',
            'start_from_scratch': 'Draft'
        };
        const templateName = templateNames[p.template_type] || 'Draft';
        
        // Get hook preview (first meaningful line of script)
        let hookPreview = '';
        if (p.script) {
            const lines = p.script.split('\n').filter(l => l.trim() && !l.trim().startsWith('[') && !l.trim().match(/^(INT\.|EXT\.)/));
            if (lines.length > 0) {
                let firstLine = lines[0].replace(/^[A-Z]+:\s*/, '').trim();
                if (firstLine.length > 80) firstLine = firstLine.substring(0, 77) + '...';
                hookPreview = firstLine;
            }
        }
        
        const projectName = p.name || 'Untitled Project';
        const projectDesc = p.description || 'No description';
        const statusText = isGenerating ? 'Generating...' : (templateName + ' Draft');
        const likedCount = autoGenerateConfidence.liked_count || 0;
        const isUnlocked = autoGenerateConfidence.is_unlocked;
        const progressText = isUnlocked ? 'Unlocked!' : `${likedCount}/5`;
        const progressColor = isUnlocked ? 'var(--gold)' : 'var(--text-dim)';
        
        html += `
            <div class="project-card-item ${p.is_successful ? 'successful' : ''} ${isGenerating ? 'generating' : ''}" 
                 id="project-card-${p.id}" data-project-id="${p.id}" onclick="${isGenerating ? '' : `openProject(${p.id})`}">
                <button class="project-delete-btn" onclick="showDeleteConfirmation(${p.id}, event)" title="Delete project">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
                    </svg>
                </button>
                <div class="project-card-header-row">
                    <div class="project-template-icon" title="${(p.template_type || 'start_from_scratch').replace(/_/g, ' ')}">${templateIcon}</div>
                    <div>
                        <span class="project-card-status" style="font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px;">${statusText}</span>
                    </div>
                </div>
                <div class="project-card-title">
                    ${escapeHtml(projectName)}
                    ${p.is_successful ? '<span class="success-badge">Posted</span>' : ''}
                    ${isReady && !p.is_successful ? '<span class="project-ready-badge">Ready</span>' : ''}
                </div>
                ${hookPreview ? `<div class="project-hook-preview">"${escapeHtml(hookPreview)}"</div>` : `<div class="project-card-desc">${escapeHtml(projectDesc)}</div>`}
                <div class="project-workflow-steps">${stepDots}</div>
                <div class="project-card-autogen" style="display: flex; align-items: center; justify-content: space-between; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border);">
                    <div class="autogen-tooltip">Like 5 of your creations to unlock Auto Generate. The AI learns your style and can create content that matches your tone and preferences.</div>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="color: var(--gold); font-size: 12px;">✦</span>
                        <span style="font-size: 11px; color: var(--text-dim);">Auto Generate</span>
                    </div>
                    <span style="font-size: 11px; color: ${progressColor};">${progressText}</span>
                </div>
            </div>
        `;
    });
    
    grid.innerHTML = html;
}

function updateDashboardLearning(learning) {
    const status = document.getElementById('dashboard-learning-status');
    const percent = document.getElementById('dashboard-learning-percent');
    const bar = document.getElementById('dashboard-learning-bar');
    const total = document.getElementById('dashboard-total-projects');
    const successful = document.getElementById('dashboard-successful-projects');
    
    if (percent) percent.textContent = (learning.learning_progress || 0) + '%';
    if (bar) bar.style.width = (learning.learning_progress || 0) + '%';
    if (total) total.textContent = learning.total_projects || 0;
    if (successful) successful.textContent = learning.successful_projects || 0;
    
    if (status) {
        if (learning.can_auto_generate) {
            status.textContent = 'Ready to auto-generate!';
        } else if ((learning.learning_progress || 0) >= 50) {
            status.textContent = 'Learning fast...';
        } else {
            status.textContent = 'Learning your style...';
        }
    }
    
    // Also update header badge
    updateAILearningUI(learning);
}

function startNewProject() {
    currentProjectId = null;
    conversationHistory = [];
    
    // Reset workflow state
    voiceCastingShown = false;
    clarificationCount = 0;
    originalIdea = '';
    awaitingClarification = false;
    currentWorkflowStep = 1;
    currentScript = null;
    currentAnchors = [];
    sceneVisuals = {};
    currentThesis = null;
    sceneDirections = {};
    detectedCharacters = [];
    
    // Clear saved state
    clearWorkflowState();
    saveConversation();
    
    // Clear last project input for rebuild functionality
    localStorage.removeItem('lastProjectInput');
    
    // Switch to create stage
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
    document.getElementById('stage-create').classList.add('active');
    
    // Show the chat bar
    const chatBar = document.getElementById('bottom-chat-bar');
    if (chatBar) chatBar.style.display = 'block';
    
    // Clear and reset messages
    const messagesEl = document.getElementById('messages');
    if (messagesEl) {
        messagesEl.innerHTML = '';
    }
    
    // Focus the input
    setTimeout(() => {
        const input = document.getElementById('composer-input');
        if (input) input.focus();
    }, 100);
}

async function openProject(projectId) {
    try {
        const response = await fetch('/projects/' + projectId);
        if (response.ok) {
            const project = await response.json();
            currentProjectId = projectId;
            
            // Update header project name
            updateProjectNameDisplay(project.name || 'Untitled Project');
            
            // Restore project state
            if (project.script) currentScript = project.script;
            if (project.visual_plan) {
                currentVisualPlan = project.visual_plan;
                // Populate scenes from visual plan if it has sections
                if (project.visual_plan.sections && project.visual_plan.sections.length > 0) {
                    populateScenesFromVisualBoard(project.visual_plan);
                }
            }
            
            // Switch to create stage
            document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
            document.getElementById('stage-create').classList.add('active');
            
            // Show the chat bar
            const chatBar = document.getElementById('bottom-chat-bar');
            if (chatBar) chatBar.style.display = 'block';
            
            // Show back link
            document.getElementById('back-to-projects').style.display = 'block';
            
            // Show welcome back message
            addMessage(`Continuing project: ${project.name}`, false);
        }
    } catch (err) {
        console.error('Error opening project:', err);
    }
}

function goToProjects() {
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
    document.getElementById('stage-projects').classList.add('active');
    const chatBar = document.getElementById('bottom-chat-bar');
    if (chatBar) chatBar.style.display = 'none';
    currentProjectId = null;
    updateProjectNameDisplay(null);
    document.getElementById('back-to-projects').style.display = 'none';
    loadDashboardProjects();
}

function toggleDashboard() {
    const overlay = document.getElementById('dashboard-overlay');
    overlay.classList.toggle('open');
    if (overlay.classList.contains('open')) {
        loadProjects();
        loadGeneratorSettings();
        updateAutoGenStatus();
    }
}

function closeDashboard(event) {
    if (event.target.id === 'dashboard-overlay') {
        document.getElementById('dashboard-overlay').classList.remove('open');
    }
}

function switchDashboardTab(tab) {
    const buildNewTab = document.getElementById('tab-build-new');
    const generatorTab = document.getElementById('tab-generator');
    const buildNewContent = document.getElementById('tab-content-build-new');
    const generatorContent = document.getElementById('tab-content-generator');
    
    if (tab === 'build-new') {
        buildNewTab.style.background = 'var(--gold)';
        buildNewTab.style.color = 'var(--bg)';
        generatorTab.style.background = 'rgba(255,255,255,0.03)';
        generatorTab.style.color = 'var(--text-dim)';
        buildNewContent.style.display = 'block';
        generatorContent.style.display = 'none';
    } else {
        buildNewTab.style.background = 'rgba(255,255,255,0.03)';
        buildNewTab.style.color = 'var(--text-dim)';
        generatorTab.style.background = 'var(--gold)';
        generatorTab.style.color = 'var(--bg)';
        buildNewContent.style.display = 'none';
        generatorContent.style.display = 'block';
        checkGeneratorStatus();
    }
}

async function checkGeneratorStatus() {
    try {
        const response = await fetch('/auto-generate-status');
        const data = await response.json();
        
        const lockedState = document.getElementById('generator-locked-state');
        const unlockedState = document.getElementById('generator-unlocked-state');
        const proBadge = document.getElementById('generator-lock-badge');
        const checkPro = document.getElementById('generator-check-pro');
        const checkLikes = document.getElementById('generator-check-likes');
        const likesCount = document.getElementById('generator-likes-count');
        
        if (likesCount) likesCount.textContent = data.liked_count || 0;
        
        if (data.has_pro) {
            checkPro.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#2ecc71" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>';
            checkPro.style.background = 'rgba(46,204,113,0.2)';
        } else {
            checkPro.innerHTML = '-';
            checkPro.style.background = 'rgba(255,255,255,0.1)';
        }
        
        if (data.liked_count >= 5) {
            checkLikes.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#2ecc71" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>';
            checkLikes.style.background = 'rgba(46,204,113,0.2)';
        } else {
            checkLikes.innerHTML = '-';
            checkLikes.style.background = 'rgba(255,255,255,0.1)';
        }
        
        if (data.eligible) {
            lockedState.style.display = 'none';
            unlockedState.style.display = 'block';
            if (proBadge) proBadge.style.display = 'none';
            loadGeneratorDrafts();
            loadDraftSettings();
        } else {
            lockedState.style.display = 'block';
            unlockedState.style.display = 'none';
            if (proBadge) proBadge.style.display = 'block';
        }
    } catch (err) {
        console.error('Error checking generator status:', err);
    }
}

let currentGeneratorProjectId = null;

async function loadGeneratorDrafts() {
    if (!currentGeneratorProjectId) {
        const projectsResponse = await fetch('/projects');
        const projectsData = await projectsResponse.json();
        if (projectsData.projects && projectsData.projects.length > 0) {
            currentGeneratorProjectId = projectsData.projects[0].id;
        } else {
            document.getElementById('generator-drafts-list').innerHTML = `
                <div class="generator-empty-state" style="text-align: center; padding: 40px 20px; background: rgba(255,255,255,0.02); border: 1px dashed var(--border); border-radius: 12px;">
                    <p style="color: var(--text-dim); font-size: 13px;">Create a project first to generate AI drafts.</p>
                </div>
            `;
            return;
        }
    }
    
    try {
        const response = await fetch(`/projects/${currentGeneratorProjectId}/generated-drafts`);
        const data = await response.json();
        renderGeneratorDrafts(data.drafts, data.can_generate_more);
    } catch (err) {
        console.error('Error loading generator drafts:', err);
    }
}

function renderGeneratorDrafts(drafts, canGenerateMore) {
    const container = document.getElementById('generator-drafts-list');
    const generateBtn = document.getElementById('generate-more-btn');
    
    if (generateBtn) {
        generateBtn.style.display = canGenerateMore ? 'flex' : 'none';
    }
    
    if (!drafts || drafts.length === 0) {
        container.innerHTML = `
            <div class="generator-empty-state" style="text-align: center; padding: 40px 20px; background: rgba(255,255,255,0.02); border: 1px dashed var(--border); border-radius: 12px;">
                <p style="color: var(--text-dim); font-size: 13px; margin-bottom: 12px;">No drafts yet. Click "Generate Drafts" to create up to 3 AI-powered content drafts.</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = drafts.map((draft, index) => `
        <div class="generator-draft-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 12px; padding: 16px; cursor: pointer; transition: all 0.2s;" onclick="openDraftEditor(${draft.id})" onmouseover="this.style.borderColor='var(--gold)'" onmouseout="this.style.borderColor='var(--border)'">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                <div>
                    <div style="display: flex; gap: 6px; margin-bottom: 8px;">
                        <span style="font-size: 10px; padding: 2px 8px; background: rgba(255,214,10,0.15); border-radius: 10px; color: var(--gold);">${draft.angle_used || 'standard'}</span>
                        <span style="font-size: 10px; padding: 2px 8px; background: rgba(78,205,196,0.15); border-radius: 10px; color: #4ecdc4;">${draft.vibe_used || 'neutral'}</span>
                        <span style="font-size: 10px; padding: 2px 8px; background: rgba(155,89,182,0.15); border-radius: 10px; color: #9b59b6;">${draft.hook_type || 'direct'}</span>
                    </div>
                    <h4 style="font-size: 13px; font-weight: 600; margin-bottom: 4px;">Draft ${index + 1}</h4>
                </div>
                <span style="font-size: 11px; color: var(--text-dim);">${new Date(draft.created_at).toLocaleDateString()}</span>
            </div>
            <p style="font-size: 12px; color: var(--text-dim); line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;">${(draft.script || '').substring(0, 150)}...</p>
            <div style="display: flex; gap: 8px; margin-top: 12px;">
                <button onclick="event.stopPropagation(); approveDraft(${draft.id})" style="flex: 1; padding: 8px; background: var(--gold); color: var(--bg); border: none; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer;">Use This</button>
                <button onclick="event.stopPropagation(); skipDraft(${draft.id})" style="padding: 8px 12px; background: rgba(255,255,255,0.05); color: var(--text-dim); border: 1px solid var(--border); border-radius: 6px; font-size: 12px; cursor: pointer;">Skip</button>
            </div>
        </div>
    `).join('');
}

async function generateMoreDrafts() {
    if (!currentGeneratorProjectId) {
        showToast('Select a project first', 'error');
        return;
    }
    
    const btn = document.getElementById('generate-more-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Generating...';
    }
    
    try {
        const response = await fetch(`/projects/${currentGeneratorProjectId}/generate-drafts`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        const data = await response.json();
        
        if (data.success) {
            showToast(`Generated ${data.drafts_generated} new drafts!`, 'success');
            await loadGeneratorDrafts();
        } else {
            showToast(data.error || 'Failed to generate drafts', 'error');
        }
    } catch (err) {
        console.error('Error generating drafts:', err);
        showToast('Failed to generate drafts', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg> Generate Drafts';
        }
    }
}

async function approveDraft(draftId) {
    try {
        const response = await fetch(`/generated-drafts/${draftId}/action`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'approve'})
        });
        const data = await response.json();
        
        if (data.success) {
            showToast('Draft approved! Loading into editor...', 'success');
            document.getElementById('dashboard-overlay').classList.remove('open');
            loadProject(data.project_id);
        } else {
            showToast(data.error || 'Failed to approve draft', 'error');
        }
    } catch (err) {
        console.error('Error approving draft:', err);
        showToast('Failed to approve draft', 'error');
    }
}

async function skipDraft(draftId) {
    try {
        const response = await fetch(`/generated-drafts/${draftId}/action`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({action: 'skip'})
        });
        const data = await response.json();
        
        if (data.success) {
            showToast('Draft skipped', 'info');
            await loadGeneratorDrafts();
        } else {
            showToast(data.error || 'Failed to skip draft', 'error');
        }
    } catch (err) {
        console.error('Error skipping draft:', err);
        showToast('Failed to skip draft', 'error');
    }
}

function openDraftEditor(draftId) {
    showToast('Opening draft editor...', 'info');
}

function updateDailyLimitDisplay(value) {
    document.getElementById('daily-limit-value').textContent = value;
}

async function saveDailyLimit(value) {
    try {
        const response = await fetch('/draft-settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({daily_limit: parseInt(value)})
        });
        const data = await response.json();
        if (data.success) {
            showToast(`Daily limit set to ${value}`, 'success');
            loadDraftSettings();
        }
    } catch (err) {
        console.error('Error saving draft limit:', err);
    }
}

async function loadDraftSettings() {
    try {
        const response = await fetch('/draft-settings');
        const data = await response.json();
        
        const slider = document.getElementById('daily-limit-slider');
        const limitValue = document.getElementById('daily-limit-value');
        const generatedToday = document.getElementById('drafts-generated-today');
        const todayLimit = document.getElementById('drafts-today-limit');
        const remaining = document.getElementById('drafts-remaining');
        
        if (slider) slider.value = data.daily_limit || 3;
        if (limitValue) limitValue.textContent = data.daily_limit || 3;
        if (generatedToday) generatedToday.textContent = data.generated_today || 0;
        if (todayLimit) todayLimit.textContent = data.daily_limit || 3;
        if (remaining) remaining.textContent = data.remaining || 0;
    } catch (err) {
        console.error('Error loading draft settings:', err);
    }
}

async function createVideoUnlimited() {
    document.getElementById('dashboard-overlay').classList.remove('open');
    showTemplatePickerModal();
}

async function loadProjects() {
    const container = document.getElementById('projects-list');
    
    // Show skeleton loaders immediately
    container.innerHTML = `
        <div class="skeleton-card">
            <div class="skeleton-line short"></div>
            <div class="skeleton-line medium"></div>
            <div class="skeleton-line long"></div>
        </div>
        <div class="skeleton-card" style="animation-delay: 0.1s;">
            <div class="skeleton-line short"></div>
            <div class="skeleton-line medium"></div>
            <div class="skeleton-line long"></div>
        </div>
    `;
    
    try {
        // Fetch auto-generate confidence in parallel with projects
        await fetchAutoGenerateConfidence();
        updateAutoGenStatus();
        
        const response = await fetch('/projects');
        if (!response.ok) {
            if (response.status === 401) {
                container.innerHTML = '<div class="empty-projects">Sign in to save projects</div>';
                return;
            }
            throw new Error('Failed to load projects');
        }
        
        const data = await response.json();
        renderProjects(data.projects);
        updateAILearningUI(data.ai_learning);
    } catch (err) {
        console.error('Error loading projects:', err);
        container.innerHTML = '<div class="empty-projects">Could not load projects</div>';
    }
}

function renderProjects(projects) {
    const container = document.getElementById('projects-list');
    
    if (!projects || projects.length === 0) {
        container.innerHTML = `
            <div class="empty-projects">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <rect x="3" y="3" width="18" height="18" rx="2"/>
                    <line x1="12" y1="8" x2="12" y2="16"/>
                    <line x1="8" y1="12" x2="16" y2="12"/>
                </svg>
                <p>No projects yet. Start creating!</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = projects.map(p => `
        <div class="project-card ${p.is_successful ? 'successful' : ''}" data-project-id="${p.id}" onclick="loadProject(${p.id})">
            <div class="project-name">
                ${escapeHtml(p.name)}
                ${p.is_successful ? '<span class="project-success-badge">Posted</span>' : ''}
                ${p.liked === true ? '<span class="project-liked-badge" style="background: rgba(46,204,113,0.2); color: #2ecc71; font-size: 10px; padding: 2px 6px; border-radius: 8px; margin-left: 6px;">Liked</span>' : ''}
            </div>
            <div class="project-meta">
                <span class="project-status">${p.status}</span>
                <span class="project-date">${formatDate(p.updated_at)}</span>
                <span class="project-autogen-toggle" onclick="event.stopPropagation(); toggleProjectAutoGen(${p.id}, ${!p.auto_generate_enabled})" title="Toggle AI Auto-Generation" style="margin-left: auto; cursor: pointer; display: flex; align-items: center; gap: 4px; padding: 4px 8px; border-radius: 12px; font-size: 10px; ${p.auto_generate_enabled ? 'background: rgba(255,214,10,0.15); color: var(--gold);' : 'background: rgba(255,255,255,0.05); color: var(--text-dim);'}">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
                    ${p.auto_generate_enabled ? 'Auto' : 'Manual'}
                </span>
            </div>
        </div>
    `).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function toggleProjectAutoGen(projectId, enable) {
    try {
        const response = await fetch(`/projects/${projectId}/toggle-auto-generate`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({enable: enable})
        });
        const data = await response.json();
        
        if (data.success) {
            showToast(`Auto-generation ${data.auto_generate_enabled ? 'enabled' : 'disabled'}`, 'success');
            await loadProjects();
        } else {
            showToast(data.error || 'Could not toggle auto-generation', 'error');
        }
    } catch (err) {
        console.error('Error toggling auto-gen:', err);
        showToast('Could not toggle auto-generation', 'error');
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now - date;
    
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
    if (diff < 86400000) return Math.floor(diff / 3600000) + 'h ago';
    if (diff < 604800000) return Math.floor(diff / 86400000) + 'd ago';
    return date.toLocaleDateString();
}

function updateAILearningUI(learning) {
    if (!learning) return;
    
    aiLearningData = learning;
    
    // Update mini badge in header
    const progressBar = document.getElementById('learning-progress-bar');
    const progressPercent = document.getElementById('learning-percent');
    if (progressBar) progressBar.style.width = learning.learning_progress + '%';
    if (progressPercent) progressPercent.textContent = learning.learning_progress + '%';
    
    // Update full dashboard panel
    const barFull = document.getElementById('learning-bar-full');
    const totalProjects = document.getElementById('total-projects');
    const successfulProjects = document.getElementById('successful-projects');
    const progressStat = document.getElementById('learning-progress-stat');
    const status = document.getElementById('learning-status');
    const tip = document.getElementById('learning-tip');
    
    if (barFull) barFull.style.width = learning.learning_progress + '%';
    if (totalProjects) totalProjects.textContent = learning.total_projects || 0;
    if (successfulProjects) successfulProjects.textContent = learning.successful_projects || 0;
    if (progressStat) progressStat.textContent = learning.learning_progress + '%';
    
    if (status) {
        if (learning.can_auto_generate) {
            status.textContent = 'Ready to Auto-Generate!';
            status.style.color = '#4caf50';
        } else if (learning.learning_progress >= 50) {
            status.textContent = 'Learning Fast!';
        } else {
            status.textContent = 'Learning...';
        }
    }
    
    if (tip) {
        if (learning.can_auto_generate) {
            tip.textContent = 'Echo Engine can now auto-generate content in your style!';
        } else if (learning.learning_progress >= 50) {
            tip.textContent = `${5 - (learning.successful_projects || 0)} more successful posts to unlock auto-generation.`;
        } else {
            tip.textContent = 'Like more videos to help Echo Engine learn your style.';
        }
    }
}

async function loadAILearningProgress() {
    try {
        const response = await fetch('/ai-learning');
        if (response.ok) {
            const data = await response.json();
            updateAILearningUI(data);
        }
    } catch (err) {
        console.warn('Could not load AI learning progress:', err);
    }
}

async function createNewProject() {
    try {
        const response = await fetch('/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'New Project' })
        });
        
        if (response.ok) {
            const data = await response.json();
            currentProjectId = data.project.id;
            document.getElementById('dashboard-overlay').classList.remove('open');
            // Reset the conversation for new project
            conversationHistory = [];
            saveConversation();
            renderMainMessages();
            addMessage("What message should the world know?", false);
        }
    } catch (err) {
        console.error('Error creating project:', err);
    }
}

const templatePrompts = {
    hot_take: "I want to create a hot take video (30-45 seconds). Help me craft a bold, opinion-driven piece that challenges conventional thinking. What controversial or thought-provoking topic should we explore?",
    explainer: "I want to create an explainer video (45-60 seconds). Help me break down a complex topic in a clear, educational way. What concept or process should we simplify for the audience?",
    story: "I want to create a story time video (60-75 seconds). Help me craft a compelling narrative with a beginning, middle, and end. What personal story or anecdote should we tell?",
    commentary: "I want to create a news commentary video (35-50 seconds). Help me react to a current event or trending topic with a unique perspective. What recent news should we discuss?",
    letter: "I want to create an open letter video (45-60 seconds). Help me write a direct, personal address to a specific group or individual. Who should we speak to and what message do they need to hear?",
    meme: "Let's create something funny! I'll research what meme formats and comedy styles are trending right now, then help you create viral-worthy content. What topic, person, or situation do you want to make a meme about?",
    ad: "Let's create a professional ad for your product or service! Do you have product photos or brand assets to include? If not, I can generate a logo for you (this will use extra tokens). Tell me about what you're promoting.",
    tiktok_edit: "Let's create a beat-synced TikTok edit! What song do you want to sync your edit to? I'll analyze the beat and create an audioless video with exact timestamps for when to start and end the music."
};

function showTemplatePickerModal() {
    const modal = document.getElementById('template-picker-modal');
    if (modal) {
        modal.style.display = 'flex';
    }
}

function closeTemplatePickerModal() {
    const modal = document.getElementById('template-picker-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

// Video Editor State
let editorState = {
    videoUrl: '',
    captionPosition: 'bottom',
    captionOffset: 10,
    captionSize: 22,
    captionOpacity: 80,
    captionColor: '#ffffff'
};

function openVideoEditor(videoUrl, captions) {
    editorState.videoUrl = videoUrl;
    const modal = document.getElementById('video-editor-modal');
    const video = document.getElementById('editor-video');
    if (modal && video) {
        video.src = videoUrl;
        modal.style.display = 'block';
        // Load saved preferences if any
        loadEditorPreferences();
        // Load captions into editor
        loadCaptionsInEditor(captions || lastGeneratedCaptions || []);
    }
}

// Store captions from last render
let lastGeneratedCaptions = [];

function closeVideoEditor() {
    const modal = document.getElementById('video-editor-modal');
    const video = document.getElementById('editor-video');
    if (modal) {
        modal.style.display = 'none';
    }
    if (video) {
        video.pause();
        video.src = '';
    }
}

function setCaptionPosition(position) {
    editorState.captionPosition = position;
    const overlay = document.getElementById('caption-preview-overlay');
    const buttons = document.querySelectorAll('.position-btn');
    
    buttons.forEach(btn => {
        if (btn.dataset.pos === position) {
            btn.style.background = 'var(--gold)';
            btn.style.borderColor = 'var(--gold)';
            btn.style.color = 'var(--bg)';
        } else {
            btn.style.background = 'rgba(255,255,255,0.05)';
            btn.style.borderColor = 'var(--border)';
            btn.style.color = 'var(--text)';
        }
    });
    
    if (overlay) {
        overlay.style.bottom = position === 'bottom' ? '0' : 'auto';
        overlay.style.top = position === 'top' ? '0' : (position === 'middle' ? '50%' : 'auto');
        overlay.style.transform = position === 'middle' ? 'translateY(-50%)' : 'none';
    }
}

function updateCaptionOffset(value) {
    editorState.captionOffset = parseInt(value);
    const overlay = document.getElementById('caption-preview-overlay');
    if (overlay) {
        overlay.style.padding = `${value}px 20px`;
    }
}

function updateCaptionSize(value) {
    editorState.captionSize = parseInt(value);
    const caption = document.getElementById('preview-caption');
    if (caption) {
        caption.style.fontSize = `${value}px`;
    }
}

function updateCaptionOpacity(value) {
    editorState.captionOpacity = parseInt(value);
    const caption = document.getElementById('preview-caption');
    if (caption) {
        caption.style.background = `rgba(0,0,0,${value/100})`;
    }
}

function setCaptionColor(color) {
    editorState.captionColor = color;
    const caption = document.getElementById('preview-caption');
    if (caption) {
        caption.style.color = color;
    }
}

async function loadEditorPreferences() {
    try {
        // Try to load from server first
        const response = await fetch('/get-caption-preferences');
        if (response.ok) {
            const prefs = await response.json();
            if (Object.keys(prefs).length > 0) {
                applyPreferences(prefs);
                return;
            }
        }
    } catch (e) {
        console.warn('Could not load preferences from server');
    }
    
    // Fallback to localStorage
    try {
        const saved = localStorage.getItem('framd_caption_prefs');
        if (saved) {
            applyPreferences(JSON.parse(saved));
        }
    } catch (e) {
        console.warn('Could not load editor preferences from localStorage');
    }
}

function applyPreferences(prefs) {
    if (prefs.caption_position || prefs.captionPosition) {
        setCaptionPosition(prefs.caption_position || prefs.captionPosition);
    }
    if (prefs.caption_offset !== undefined || prefs.captionOffset !== undefined) {
        const offset = prefs.caption_offset ?? prefs.captionOffset;
        document.getElementById('caption-offset').value = offset;
        updateCaptionOffset(offset);
    }
    if (prefs.caption_size !== undefined || prefs.captionSize !== undefined) {
        const size = prefs.caption_size ?? prefs.captionSize;
        document.getElementById('caption-size').value = size;
        updateCaptionSize(size);
    }
    if (prefs.caption_opacity !== undefined || prefs.captionOpacity !== undefined) {
        const opacity = prefs.caption_opacity ?? prefs.captionOpacity;
        document.getElementById('caption-opacity').value = opacity;
        updateCaptionOpacity(opacity);
    }
    if (prefs.caption_color || prefs.captionColor) {
        setCaptionColor(prefs.caption_color || prefs.captionColor);
    }
    editorState = { 
        ...editorState, 
        captionPosition: prefs.caption_position || prefs.captionPosition || editorState.captionPosition,
        captionOffset: prefs.caption_offset ?? prefs.captionOffset ?? editorState.captionOffset,
        captionSize: prefs.caption_size ?? prefs.captionSize ?? editorState.captionSize,
        captionOpacity: prefs.caption_opacity ?? prefs.captionOpacity ?? editorState.captionOpacity,
        captionColor: prefs.caption_color || prefs.captionColor || editorState.captionColor
    };
}

async function applyEditorChanges() {
    const savePreset = document.getElementById('save-as-preset').checked;
    
    // Save edited captions for next render
    lastGeneratedCaptions = [...editorCaptions];
    
    if (savePreset) {
        // Save preferences locally
        localStorage.setItem('framd_caption_prefs', JSON.stringify(editorState));
        
        // Save to AI learning system
        try {
            await fetch('/save-caption-preferences', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    caption_position: editorState.captionPosition,
                    caption_offset: editorState.captionOffset,
                    caption_size: editorState.captionSize,
                    caption_opacity: editorState.captionOpacity,
                    caption_color: editorState.captionColor
                })
            });
        } catch (e) {
            console.warn('Could not save caption preferences to server');
        }
    }
    
    // Check if captions were edited
    if (editorCaptions.length > 0) {
        showToast('Caption changes saved! Click "Re-render" in chat to apply.');
    } else {
        showToast('Caption preferences saved! Next render will use these settings.');
    }
    closeVideoEditor();
}

// Caption Editor Functions
let editorCaptions = [];

function loadCaptionsInEditor(captions) {
    editorCaptions = captions || [];
    renderCaptionList();
}

function renderCaptionList() {
    const container = document.getElementById('caption-list');
    if (!container) return;
    
    if (editorCaptions.length === 0) {
        container.innerHTML = '<div style="color: var(--text-dim); font-size: 13px; text-align: center; padding: 20px;">No captions available. Add some!</div>';
        return;
    }
    
    container.innerHTML = editorCaptions.map((cap, idx) => `
        <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: rgba(0,0,0,0.3); border-radius: 8px; margin-bottom: 8px;">
            <div style="flex: 1;">
                <input type="text" value="${cap.text || ''}" onchange="updateCaptionText(${idx}, this.value)" 
                    style="width: 100%; padding: 8px; background: rgba(255,255,255,0.1); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px;">
                <div style="display: flex; gap: 8px; margin-top: 6px;">
                    <input type="number" value="${(cap.start || 0).toFixed(1)}" step="0.1" min="0" 
                        onchange="updateCaptionTiming(${idx}, 'start', this.value)"
                        style="width: 70px; padding: 4px 8px; background: rgba(255,255,255,0.1); border: 1px solid var(--border); border-radius: 4px; color: var(--text); font-size: 12px;" placeholder="Start">
                    <span style="color: var(--text-dim); font-size: 12px;">to</span>
                    <input type="number" value="${(cap.end || 0).toFixed(1)}" step="0.1" min="0"
                        onchange="updateCaptionTiming(${idx}, 'end', this.value)"
                        style="width: 70px; padding: 4px 8px; background: rgba(255,255,255,0.1); border: 1px solid var(--border); border-radius: 4px; color: var(--text); font-size: 12px;" placeholder="End">
                    <span style="color: var(--text-dim); font-size: 12px;">sec</span>
                </div>
            </div>
            <button onclick="deleteCaption(${idx})" style="padding: 6px; background: rgba(231,76,60,0.2); border: none; border-radius: 6px; color: #E74C3C; cursor: pointer;">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
            </button>
        </div>
    `).join('');
}

function updateCaptionText(idx, text) {
    if (editorCaptions[idx]) {
        editorCaptions[idx].text = text;
    }
}

function updateCaptionTiming(idx, field, value) {
    if (editorCaptions[idx]) {
        editorCaptions[idx][field] = parseFloat(value) || 0;
    }
}

function addNewCaption() {
    const lastEnd = editorCaptions.length > 0 ? editorCaptions[editorCaptions.length - 1].end || 0 : 0;
    editorCaptions.push({ text: 'New caption', start: lastEnd, end: lastEnd + 2 });
    renderCaptionList();
}

function deleteCaption(idx) {
    editorCaptions.splice(idx, 1);
    renderCaptionList();
}

// Video History Functions
async function showVideoHistory() {
    const modal = document.getElementById('video-history-modal');
    if (modal) modal.style.display = 'flex';
    
    try {
        const response = await fetch('/video-history');
        const data = await response.json();
        renderVideoHistory(data.videos || []);
    } catch (e) {
        document.getElementById('video-history-list').innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-dim);">Could not load video history</div>';
    }
}

function renderVideoHistory(videos) {
    const container = document.getElementById('video-history-list');
    if (!container) return;
    
    if (videos.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-dim);">No videos generated yet. Create your first video!</div>';
        return;
    }
    
    container.innerHTML = videos.map(v => `
        <div style="display: flex; align-items: center; gap: 16px; padding: 16px; background: rgba(255,255,255,0.05); border: 1px solid var(--border); border-radius: 12px;">
            <div style="width: 80px; height: 80px; background: rgba(0,0,0,0.3); border-radius: 8px; display: flex; align-items: center; justify-content: center;">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            </div>
            <div style="flex: 1;">
                <div style="font-weight: 600; font-size: 15px; margin-bottom: 4px;">${v.project_name}</div>
                <div style="font-size: 12px; color: var(--text-dim);">${v.format || '9:16'} • ${formatTimeAgo(v.created_at)}</div>
            </div>
            <a href="${v.video_path}" download style="padding: 10px 16px; background: var(--gold); border-radius: 8px; color: var(--bg); font-weight: 600; font-size: 13px; text-decoration: none;">Download</a>
        </div>
    `).join('');
}

function formatTimeAgo(dateStr) {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return diffDays + ' days ago';
    return date.toLocaleDateString();
}

function closeVideoHistory() {
    const modal = document.getElementById('video-history-modal');
    if (modal) modal.style.display = 'none';
}

// Email Settings Functions
async function showEmailSettings() {
    const modal = document.getElementById('email-settings-modal');
    if (modal) modal.style.display = 'flex';
    
    try {
        const response = await fetch('/email-preferences');
        const prefs = await response.json();
        document.getElementById('email-video-ready').checked = prefs.video_ready !== false;
        document.getElementById('email-low-tokens').checked = prefs.low_tokens !== false;
        document.getElementById('email-weekly-digest').checked = prefs.weekly_digest === true;
    } catch (e) {
        console.warn('Could not load email preferences');
    }
}

function closeEmailSettings() {
    const modal = document.getElementById('email-settings-modal');
    if (modal) modal.style.display = 'none';
}

async function saveEmailSettings() {
    try {
        await fetch('/email-preferences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_ready: document.getElementById('email-video-ready').checked,
                low_tokens: document.getElementById('email-low-tokens').checked,
                weekly_digest: document.getElementById('email-weekly-digest').checked
            })
        });
        showToast('Email preferences saved!');
        closeEmailSettings();
    } catch (e) {
        showToast('Could not save preferences');
    }
}

// Platform Export Functions
let currentExportVideoUrl = '';

function showPlatformExport(videoUrl) {
    currentExportVideoUrl = videoUrl;
    document.getElementById('platform-export-modal').style.display = 'block';
    document.getElementById('export-progress').style.display = 'none';
    document.getElementById('export-btn').disabled = false;
}

function closePlatformExport() {
    document.getElementById('platform-export-modal').style.display = 'none';
}

async function startPlatformExport() {
    const checkboxes = document.querySelectorAll('input[name="platform"]:checked');
    const platforms = Array.from(checkboxes).map(cb => cb.value);
    
    if (platforms.length === 0) {
        showToast('Please select at least one platform');
        return;
    }
    
    document.getElementById('export-progress').style.display = 'block';
    document.getElementById('export-btn').disabled = true;
    
    const progressBar = document.getElementById('export-progress-bar');
    const progressText = document.getElementById('export-progress').querySelector('div');
    let completed = 0;
    let successful = 0;
    let failed = [];
    
    for (const platform of platforms) {
        progressBar.style.width = ((completed / platforms.length) * 100) + '%';
        progressText.textContent = `Exporting ${platform}... (${completed + 1}/${platforms.length})`;
        
        try {
            const response = await fetch('/export-platform-format', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    video_url: currentExportVideoUrl,
                    platform: platform
                })
            });
            
            const data = await response.json();
            if (data.success && data.video_path) {
                successful++;
                // Auto-download with delay to prevent browser blocking
                setTimeout(() => {
                    const a = document.createElement('a');
                    a.href = data.video_path;
                    a.download = `framd-${platform}.mp4`;
                    a.click();
                }, completed * 500);
            } else {
                failed.push(platform);
                console.error(`Export to ${platform} failed:`, data.error);
            }
        } catch (e) {
            failed.push(platform);
            console.error(`Export to ${platform} failed:`, e);
        }
        
        completed++;
    }
    
    progressBar.style.width = '100%';
    
    if (failed.length > 0) {
        showToast(`Exported ${successful} of ${platforms.length}. Failed: ${failed.join(', ')}`);
    } else {
        showToast(`All ${platforms.length} formats exported successfully!`);
    }
    
    progressText.textContent = 'Export complete!';
    
    setTimeout(() => {
        closePlatformExport();
    }, 1500);
}

// Promo Pack Functions
let promoPackData = { quoteCards: [], memes: [], infographics: [] };
let approvedPromoItems = new Set();

async function generatePromoPack(videoUrl) {
    document.getElementById('promo-pack-modal').style.display = 'block';
    document.getElementById('promo-loading').style.display = 'block';
    document.getElementById('promo-content').style.display = 'none';
    document.getElementById('promo-actions').style.display = 'none';
    
    try {
        const response = await fetch('/generate-promo-pack', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_url: videoUrl,
                script: currentScript,
                project_id: currentProjectId
            })
        });
        
        const data = await response.json();
        if (data.success) {
            promoPackData = data;
            renderPromoPack(data);
        } else {
            showToast(data.error || 'Failed to generate promo pack');
            closePromoPack();
        }
    } catch (e) {
        console.error('Promo pack error:', e);
        showToast('Failed to generate promo content');
        closePromoPack();
    }
}

function renderPromoPack(data) {
    document.getElementById('promo-loading').style.display = 'none';
    document.getElementById('promo-content').style.display = 'block';
    document.getElementById('promo-actions').style.display = 'flex';
    
    // Render quote cards
    const quoteGrid = document.getElementById('quote-cards-grid');
    quoteGrid.innerHTML = (data.quote_cards || []).map((card, idx) => `
        <div class="promo-item ${approvedPromoItems.has('quote-'+idx) ? 'approved' : ''}" onclick="togglePromoApproval('quote', ${idx})" style="cursor: pointer; border: 2px solid ${approvedPromoItems.has('quote-'+idx) ? 'var(--gold)' : 'var(--border)'}; border-radius: 12px; overflow: hidden; transition: all 0.2s;">
            <div style="aspect-ratio: 1; background: linear-gradient(135deg, ${card.bg_color || '#1a1a2e'}, ${card.accent_color || '#16213e'}); display: flex; align-items: center; justify-content: center; padding: 20px; text-align: center;">
                <div style="color: white; font-size: 14px; font-weight: 600; line-height: 1.4;">"${card.quote}"</div>
            </div>
            <div style="padding: 10px; background: rgba(0,0,0,0.3); display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 11px; color: var(--text-dim);">Quote Card</span>
                <span style="font-size: 11px; color: ${approvedPromoItems.has('quote-'+idx) ? 'var(--gold)' : 'var(--text-dim)'};">${approvedPromoItems.has('quote-'+idx) ? '✓ Approved' : 'Tap to approve'}</span>
            </div>
        </div>
    `).join('') || '<div style="color: var(--text-dim); font-size: 13px;">No quote cards generated</div>';
    
    // Render memes if available
    const memesSection = document.getElementById('memes-section');
    const memesGrid = document.getElementById('memes-grid');
    if (data.memes && data.memes.length > 0) {
        memesSection.style.display = 'block';
        memesGrid.innerHTML = data.memes.map((meme, idx) => `
            <div class="promo-item ${approvedPromoItems.has('meme-'+idx) ? 'approved' : ''}" onclick="togglePromoApproval('meme', ${idx})" style="cursor: pointer; border: 2px solid ${approvedPromoItems.has('meme-'+idx) ? 'var(--gold)' : 'var(--border)'}; border-radius: 12px; overflow: hidden; transition: all 0.2s;">
                <div style="aspect-ratio: 1; background: #000; display: flex; flex-direction: column; justify-content: space-between; padding: 12px;">
                    <div style="color: white; font-size: 13px; font-weight: 700; text-align: center; text-shadow: 2px 2px 0 #000;">${meme.top_text}</div>
                    <div style="color: white; font-size: 13px; font-weight: 700; text-align: center; text-shadow: 2px 2px 0 #000;">${meme.bottom_text}</div>
                </div>
                <div style="padding: 10px; background: rgba(0,0,0,0.3); display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 11px; color: var(--text-dim);">${meme.format || 'Meme'}</span>
                    <span style="font-size: 11px; color: ${approvedPromoItems.has('meme-'+idx) ? 'var(--gold)' : 'var(--text-dim)'};">${approvedPromoItems.has('meme-'+idx) ? '✓ Approved' : 'Tap to approve'}</span>
                </div>
            </div>
        `).join('');
    } else {
        memesSection.style.display = 'none';
    }
    
    // Render infographics
    const infoGrid = document.getElementById('infographics-grid');
    infoGrid.innerHTML = (data.infographics || []).map((info, idx) => `
        <div class="promo-item ${approvedPromoItems.has('info-'+idx) ? 'approved' : ''}" onclick="togglePromoApproval('info', ${idx})" style="cursor: pointer; border: 2px solid ${approvedPromoItems.has('info-'+idx) ? 'var(--gold)' : 'var(--border)'}; border-radius: 12px; overflow: hidden; transition: all 0.2s;">
            <div style="aspect-ratio: 1; background: linear-gradient(135deg, #0a1f14, #1a3d2a); padding: 16px; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center;">
                <div style="font-size: 28px; font-weight: 700; color: var(--gold); margin-bottom: 8px;">${info.stat}</div>
                <div style="font-size: 12px; color: white; line-height: 1.3;">${info.label}</div>
            </div>
            <div style="padding: 10px; background: rgba(0,0,0,0.3); display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 11px; color: var(--text-dim);">Stat Card</span>
                <span style="font-size: 11px; color: ${approvedPromoItems.has('info-'+idx) ? 'var(--gold)' : 'var(--text-dim)'};">${approvedPromoItems.has('info-'+idx) ? '✓ Approved' : 'Tap to approve'}</span>
            </div>
        </div>
    `).join('') || '<div style="color: var(--text-dim); font-size: 13px;">No infographics generated</div>';
}

function togglePromoApproval(type, idx) {
    const key = type + '-' + idx;
    if (approvedPromoItems.has(key)) {
        approvedPromoItems.delete(key);
    } else {
        approvedPromoItems.add(key);
    }
    renderPromoPack(promoPackData);
}

function closePromoPack() {
    document.getElementById('promo-pack-modal').style.display = 'none';
    approvedPromoItems.clear();
}

async function downloadAllPromo() {
    if (approvedPromoItems.size === 0) {
        showToast('Please approve at least one item to download');
        return;
    }
    
    showToast(`Downloading ${approvedPromoItems.size} promo items...`);
    
    // Generate and download approved items
    try {
        const response = await fetch('/download-promo-pack', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                approved_items: Array.from(approvedPromoItems),
                promo_data: promoPackData
            })
        });
        
        const data = await response.json();
        if (data.success && data.download_url) {
            const a = document.createElement('a');
            a.href = data.download_url;
            a.download = 'framd-promo-pack.zip';
            a.click();
        }
    } catch (e) {
        console.error('Download error:', e);
        showToast('Download failed - items saved to your library');
    }
    
    closePromoPack();
}

// Save video to history after successful render
async function saveToVideoHistory(projectName, videoPath, format) {
    try {
        await fetch('/save-video-history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: currentProjectId,
                project_name: projectName || 'Untitled Video',
                video_path: videoPath,
                format: format
            })
        });
    } catch (e) {
        console.warn('Could not save to video history');
    }
}

async function createProjectFromTemplate(templateType) {
    try {
        const response = await fetch('/projects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'New Project', description: '', template_type: templateType })
        });
        
        if (response.ok) {
            const data = await response.json();
            currentProjectId = data.project.id;
            document.getElementById('dashboard-overlay').classList.remove('open');
            
            // Reset conversation and switch to create stage
            conversationHistory = [];
            saveConversation();
            
            document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
            document.getElementById('stage-create').classList.add('active');
            
            const chatBar = document.getElementById('bottom-chat-bar');
            if (chatBar) chatBar.style.display = 'block';
            
            const messagesEl = document.getElementById('messages');
            if (messagesEl) messagesEl.innerHTML = '';
            
            // Add template-specific prompt
            const prompt = templatePrompts[templateType] || "What message should the world know?";
            addMessage(prompt, false);
        }
    } catch (err) {
        console.error('Error creating project from template:', err);
    }
}

async function loadProject(projectId) {
    try {
        const response = await fetch('/projects/' + projectId);
        if (response.ok) {
            const project = await response.json();
            currentProjectId = projectId;
            
            // Update header project name
            updateProjectNameDisplay(project.name || 'Untitled Project');
            
            // Restore project state
            if (project.script) {
                currentScript = project.script;
            }
            if (project.visual_plan) {
                currentVisualPlan = project.visual_plan;
                // Populate scenes from visual plan if it has sections
                if (project.visual_plan.sections && project.visual_plan.sections.length > 0) {
                    populateScenesFromVisualBoard(project.visual_plan);
                }
            }
            
            document.getElementById('dashboard-overlay').classList.remove('open');
            addMessage(`Loaded project: ${project.name}. Ready to continue!`, false);
        }
    } catch (err) {
        console.error('Error loading project:', err);
    }
}

async function saveCurrentProject(updates = {}) {
    if (!currentProjectId) return;
    
    showSaveIndicator('saving');
    try {
        const response = await fetch('/projects/' + currentProjectId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates)
        });
        if (response.ok) {
            showSaveIndicator('saved');
        } else {
            hideSaveIndicator();
        }
    } catch (err) {
        console.warn('Could not save project:', err);
        hideSaveIndicator();
    }
}

async function markProjectSuccessful(score = 1) {
    if (!currentProjectId) return;
    
    try {
        const response = await fetch('/projects/' + currentProjectId + '/mark-successful', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ score })
        });
        
        if (response.ok) {
            const data = await response.json();
            updateAILearningUI({
                learning_progress: data.learning_progress,
                can_auto_generate: data.can_auto_generate
            });
            addMessage("Your success has been recorded! Echo Engine is learning from your wins.", false);
        }
    } catch (err) {
        console.error('Error marking project successful:', err);
    }
}

// Listen for storage events to sync across tabs
window.addEventListener('storage', (e) => {
    if (e.key === 'krakd_conversation') {
        try {
            const newHistory = JSON.parse(e.newValue || '[]');
            // Check if new AI messages were added (not user messages)
            const lastNewMsg = newHistory[newHistory.length - 1];
            if (newHistory.length > conversationHistory.length && 
                lastNewMsg && lastNewMsg.role === 'assistant') {
                showUnreadBadge();
            }
            conversationHistory = newHistory;
            renderChatPanelMessages();
            renderMainMessages();
        } catch (err) {
            console.warn('Could not sync conversation:', err);
        }
    }
    // Sync unread clear across tabs
    if (e.key === 'krakd_last_seen') {
        unreadMessages = 0;
        const badge = document.getElementById('chat-badge');
        badge.classList.remove('show');
        badge.textContent = '0';
    }
});

// Save conversation to localStorage (syncs across tabs)
function saveConversation() {
    try {
        localStorage.setItem('krakd_conversation', JSON.stringify(conversationHistory));
        saveWorkflowState();
    } catch (e) {
        console.warn('Could not save conversation:', e);
    }
}

// Restore conversation from localStorage
function restoreConversation() {
    try {
        const saved = localStorage.getItem('krakd_conversation');
        if (saved) {
            conversationHistory = JSON.parse(saved);
            renderChatPanelMessages();
            renderMainMessages();
        }
    } catch (e) {
        console.warn('Could not restore conversation:', e);
    }
}

// Render messages in the floating chat panel
function renderChatPanelMessages() {
    const container = document.getElementById('chat-panel-messages');
    if (!container) return;
    
    container.innerHTML = conversationHistory.length === 0 
        ? '<div class="message ai"><div class="message-content">Prompt, paste, or drop. We\'ll make the clip.</div></div>'
        : conversationHistory.map(msg => `
            <div class="message ${msg.role === 'user' ? 'user' : 'ai'}">
                <div class="message-content">${escapeHtml(msg.content)}</div>
            </div>
        `).join('');
    
    container.scrollTop = container.scrollHeight;
}

// Render messages in main create stage
function renderMainMessages() {
    const container = document.getElementById('messages');
    if (!container) return;
    
    if (conversationHistory.length === 0) {
        container.innerHTML = `
            <div class="message ai">
                <div class="message-content">Prompt, paste, or drop. We'll make the clip.</div>
            </div>
            <div class="suggested-prompts">
                <button class="suggested-prompt" onclick="useSuggestedPrompt('Turn this article into a 60 second clip')">Turn this article into a 60 second clip</button>
                <button class="suggested-prompt" onclick="useSuggestedPrompt('The perfect clipper')">The perfect clipper</button>
                <button class="suggested-prompt" onclick="useSuggestedPrompt('Build a script around your thesis for a post')">Build a script around your thesis for a post</button>
            </div>
        `;
    } else {
        container.innerHTML = conversationHistory.map(msg => `
            <div class="message ${msg.role === 'user' ? 'user' : 'ai'}">
                <div class="message-content">${escapeHtml(msg.content)}</div>
            </div>
        `).join('');
    }
    
    container.scrollTop = container.scrollHeight;
}

// Use suggested prompt
function useSuggestedPrompt(prompt) {
    const input = document.getElementById('composer-input');
    if (input) {
        input.value = prompt;
        input.focus();
    }
}

// Toggle floating chat panel
function toggleChatPanel() {
    chatPanelOpen = !chatPanelOpen;
    const panel = document.getElementById('chat-panel');
    const toggle = document.getElementById('chat-toggle');
    const badge = document.getElementById('chat-badge');
    
    panel.classList.toggle('open', chatPanelOpen);
    toggle.classList.toggle('hidden', chatPanelOpen);
    
    if (chatPanelOpen) {
        unreadMessages = 0;
        badge.classList.remove('show');
        badge.textContent = '0';
        // Broadcast to other tabs that unread was cleared
        localStorage.setItem('krakd_last_seen', Date.now().toString());
        document.getElementById('chat-panel-input').focus();
        renderChatPanelMessages();
    }
}

// Show unread badge
function showUnreadBadge() {
    if (!chatPanelOpen) {
        unreadMessages++;
        const badge = document.getElementById('chat-badge');
        badge.textContent = unreadMessages > 9 ? '9+' : unreadMessages;
        badge.classList.add('show');
    }
}

// Send message from floating chat panel
async function sendChatPanelMessage() {
    const input = document.getElementById('chat-panel-input');
    const message = input.value.trim();
    if (!message) return;
    
    input.value = '';
    
    // Add user message
    conversationHistory.push({ role: 'user', content: message });
    saveConversation();
    renderChatPanelMessages();
    renderMainMessages();
    
    // Show typing indicator
    const container = document.getElementById('chat-panel-messages');
    const typingDiv = document.createElement('div');
    typingDiv.className = 'message ai';
    typingDiv.innerHTML = `
        <div class="message-content" style="color: var(--text-dim); font-style: italic;">
            <span class="typing-dots">Thinking</span>
        </div>
    `;
    container.appendChild(typingDiv);
    container.scrollTop = container.scrollHeight;
    
    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                conversation: conversationHistory.slice(-10)
            })
        });
        
        const data = await response.json();
        typingDiv.remove();
        
        if (data.reply) {
            conversationHistory.push({ role: 'assistant', content: data.reply });
            saveConversation();
            renderChatPanelMessages();
            renderMainMessages();
            
            if (data.script) {
                currentScript = data.script;
                updateScriptCard(data.display_script || data.script);
            }
        }
    } catch (error) {
        typingDiv.remove();
        console.error('Chat error:', error);
    }
}

// Auto-resize textarea
function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// Handle keyboard
function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
}

// File handling
function handleFile(e) {
    const file = e.target.files[0];
    if (file) {
        uploadedFile = file;
        document.getElementById('file-name').textContent = file.name;
        document.getElementById('file-preview').classList.add('show');
    }
}

function clearFile() {
    uploadedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-preview').classList.remove('show');
}

// Image/Video placement functions
let selectedUploadMode = 'curate';

function isImageFile(filename) {
    return filename.match(/\.(jpg|jpeg|png|gif|webp)$/i);
}

function isVideoFile(filename) {
    return filename.match(/\.(mp4|mov|avi|mkv|webm)$/i);
}

// Reskin options state
let reskinCaptionPosition = 'bottom';
let reskinVoiceMode = 'ai';
let reskinCustomVoiceover = null;
let reskinCustomImages = [];

function selectUploadMode(mode) {
    selectedUploadMode = mode;
    document.querySelectorAll('.mode-option').forEach(opt => opt.classList.remove('selected'));
    const selectedOpt = document.querySelector(`.mode-option[data-mode="${mode}"]`);
    if (selectedOpt) selectedOpt.classList.add('selected');
    
    const placementSection = document.getElementById('placement-options-section');
    const reskinSection = document.getElementById('reskin-options-section');
    
    if (mode === 'reskin' || mode === 'clipper') {
        placementSection.style.display = 'none';
        reskinSection.style.display = 'block';
    } else {
        placementSection.style.display = 'flex';
        reskinSection.style.display = 'none';
    }
}

function openCustomTemplateCreator() {
    addMessage("Upload a video to create your custom template. AI will learn its editing style, pacing, and structure for future use.", false);
    document.getElementById('file-input').click();
    window.customTemplateMode = true;
}

function selectCaptionPosition(pos) {
    reskinCaptionPosition = pos;
    document.querySelectorAll('.caption-pos-btn').forEach(btn => {
        if (btn.dataset.pos === pos) {
            btn.classList.add('selected');
            btn.style.background = 'var(--gold-muted)';
            btn.style.borderColor = 'var(--gold)';
            btn.style.color = 'var(--gold)';
        } else {
            btn.classList.remove('selected');
            btn.style.background = 'var(--card-bg)';
            btn.style.borderColor = 'var(--border)';
            btn.style.color = 'var(--text-secondary)';
        }
    });
}

function selectVoiceMode(mode) {
    reskinVoiceMode = mode;
    document.querySelectorAll('.voice-mode-btn').forEach(btn => {
        if (btn.dataset.mode === mode) {
            btn.classList.add('selected');
            btn.style.background = 'var(--gold-muted)';
            btn.style.borderColor = 'var(--gold)';
            btn.style.color = 'var(--gold)';
        } else {
            btn.classList.remove('selected');
            btn.style.background = 'var(--card-bg)';
            btn.style.borderColor = 'var(--border)';
            btn.style.color = 'var(--text-secondary)';
        }
    });
    
    const customUpload = document.getElementById('custom-voice-upload');
    customUpload.style.display = mode === 'custom' ? 'block' : 'none';
}

async function handleCustomVoiceover(input) {
    const file = input.files[0];
    if (!file) return;
    
    const label = document.getElementById('custom-voice-label');
    label.textContent = 'Uploading...';
    
    try {
        const formData = new FormData();
        formData.append('file', file);
        
        const response = await fetch('/upload-file', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        if (data.success) {
            reskinCustomVoiceover = data.path;
            label.textContent = `✓ ${file.name}`;
            label.style.color = 'var(--gold)';
        } else {
            throw new Error(data.error);
        }
    } catch (err) {
        label.textContent = 'Upload failed. Try again.';
        label.style.color = 'var(--error)';
    }
}

async function handleCustomImages(input) {
    const files = input.files;
    if (!files.length) return;
    
    const preview = document.getElementById('custom-images-preview');
    const label = document.getElementById('custom-images-label');
    
    for (const file of files) {
        try {
            const formData = new FormData();
            formData.append('file', file);
            
            const response = await fetch('/upload-file', {
                method: 'POST',
                body: formData
            });
            
            const data = await response.json();
            if (data.success) {
                reskinCustomImages.push(data.path);
                
                // Add preview thumbnail
                const thumb = document.createElement('div');
                thumb.style.cssText = 'width: 50px; height: 50px; border-radius: 6px; background-size: cover; background-position: center; border: 1px solid var(--border);';
                thumb.style.backgroundImage = `url(${data.path})`;
                preview.appendChild(thumb);
            }
        } catch (err) {
            console.error('Image upload error:', err);
        }
    }
    
    label.textContent = `${reskinCustomImages.length} image(s) added`;
}

function updateConfirmButton() {
    const checkbox = document.getElementById('content-rights-check');
    const confirmBtn = document.getElementById('confirm-upload-btn');
    confirmBtn.disabled = !checkbox.checked;
}

async function analyzeAndShowImagePlacement(file, filePath) {
    pendingImageFile = file;
    pendingImagePath = filePath;
    selectedPlacement = 'background';
    selectedUploadMode = 'curate';
    
    const isVideo = isVideoFile(file.name);
    
    // Use inline card for video files instead of popup
    if (isVideo) {
        renderInlineUploadCard(file, filePath, true);
        return;
    }
    
    // For images, still use the popup (or could also use inline card)
    const popup = document.getElementById('image-placement-popup');
    const imgPreview = document.getElementById('image-placement-preview');
    const vidPreview = document.getElementById('video-placement-preview');
    const analysis = document.getElementById('image-placement-analysis');
    const placementSection = document.getElementById('placement-options-section');
    
    imgPreview.style.display = 'block';
    vidPreview.style.display = 'none';
    imgPreview.src = URL.createObjectURL(file);
    analysis.innerHTML = '<em>Analyzing image with AI...</em>';
    
    popup.classList.add('show');
    document.body.style.overflow = 'hidden';
    
    document.getElementById('content-rights-check').checked = false;
    document.getElementById('confirm-upload-btn').disabled = true;
    
    document.querySelectorAll('.mode-option').forEach(opt => opt.classList.remove('selected'));
    document.querySelector('.mode-option[data-mode="curate"]').classList.add('selected');
    placementSection.style.display = 'flex';
    
    document.querySelectorAll('.placement-option').forEach(opt => opt.classList.remove('selected'));
    document.querySelector('.placement-option[data-placement="background"]').classList.add('selected');
    
    // Call image analysis endpoint
    const analyzeEndpoint = '/analyze-image';
    
    try {
        analysis.innerHTML = '<em>Analyzing image with AI...</em>';
        
        const response = await fetch(analyzeEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path: filePath })
        });
        
        const data = await response.json();
        if (data.success && data.analysis) {
            pendingImageAnalysis = data.analysis;
            analysis.innerHTML = `
                <strong>${data.analysis.description}</strong><br>
                <span style="color: var(--gold)">Mood:</span> ${data.analysis.mood}<br>
                <span style="color: var(--gold)">AI suggests:</span> ${data.analysis.suggested_use === 'background' ? 'Full Background' : 'Pop-up Visual'}
            `;
            
            // Auto-select AI suggestion
            selectedPlacement = data.analysis.suggested_use || 'background';
            document.querySelectorAll('.placement-option').forEach(opt => opt.classList.remove('selected'));
            document.querySelector(`.placement-option[data-placement="${selectedPlacement}"]`).classList.add('selected');
        } else {
            analysis.innerHTML = '<em>Image ready for use</em>';
        }
    } catch (err) {
        console.error('Image analysis error:', err);
        analysis.innerHTML = '<em>Image ready for use</em>';
    }
}

function selectPlacement(placement) {
    selectedPlacement = placement;
    document.querySelectorAll('.placement-option').forEach(opt => opt.classList.remove('selected'));
    document.querySelector(`.placement-option[data-placement="${placement}"]`).classList.add('selected');
}

function cancelImagePlacement() {
    pendingImageFile = null;
    pendingImagePath = null;
    pendingImageAnalysis = null;
    // Remove inline card if exists
    const inlineCard = document.getElementById('inline-upload-card');
    if (inlineCard) inlineCard.remove();
    // Legacy popup cleanup
    const popup = document.getElementById('image-placement-popup');
    if (popup) {
        popup.classList.remove('show');
        document.body.style.overflow = '';
    }
}

// Inline Upload Card - Replaces the popup modal
let inlineUploadCardMode = 'reskin';
let inlineUploadRightsChecked = false;

function renderInlineUploadCard(file, filePath, isVideo) {
    pendingImageFile = file;
    pendingImagePath = filePath;
    inlineUploadCardMode = 'reskin'; // Default to reskin for videos
    inlineUploadRightsChecked = false;
    
    const messagesDiv = document.getElementById('messages');
    
    // Remove any existing inline card
    const existingCard = document.getElementById('inline-upload-card');
    if (existingCard) existingCard.remove();
    
    const cardId = 'inline-upload-card';
    const previewUrl = URL.createObjectURL(file);
    
    const cardHtml = `
        <div class="message ai">
            <div class="inline-upload-card" id="${cardId}">
                ${isVideo 
                    ? `<video class="upload-preview" src="${previewUrl}" muted loop autoplay playsinline></video>`
                    : `<img class="upload-preview" src="${previewUrl}" alt="Preview">`
                }
                
                <div class="upload-analysis" id="inline-upload-analysis">
                    <em>${isVideo ? 'Video ready' : 'Image ready'}</em>
                </div>
                
                <div class="mode-buttons" id="inline-mode-buttons">
                    <div class="mode-btn selected" data-mode="reskin" onclick="selectInlineMode('reskin')">
                        <div class="mode-btn-label">AI Remix</div>
                        <div class="mode-btn-desc">AI regenerates visuals for your topic</div>
                    </div>
                    <div class="mode-btn" data-mode="clipper" onclick="selectInlineMode('clipper')">
                        <div class="mode-btn-label">Next-gen clipper</div>
                        <div class="mode-btn-desc">Script leads, template guides edits</div>
                    </div>
                </div>
                
                <div class="rights-check">
                    <input type="checkbox" id="inline-rights-check" onchange="updateInlineConfirmBtn()">
                    <label for="inline-rights-check">I have rights to use this content</label>
                </div>
                
                <div class="upload-actions">
                    <button class="btn-cancel" onclick="cancelInlineUpload()">Cancel</button>
                    <button class="btn-confirm" id="inline-confirm-btn" onclick="confirmInlineUpload()" disabled>Use This Clip</button>
                </div>
            </div>
        </div>
    `;
    
    messagesDiv.insertAdjacentHTML('beforeend', cardHtml);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function selectInlineMode(mode) {
    inlineUploadCardMode = mode;
    document.querySelectorAll('#inline-upload-card .mode-btn').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.mode === mode);
    });
}

function updateInlineConfirmBtn() {
    const checkbox = document.getElementById('inline-rights-check');
    const confirmBtn = document.getElementById('inline-confirm-btn');
    inlineUploadRightsChecked = checkbox?.checked || false;
    if (confirmBtn) confirmBtn.disabled = !inlineUploadRightsChecked;
}

function cancelInlineUpload() {
    pendingImageFile = null;
    pendingImagePath = null;
    const card = document.getElementById('inline-upload-card');
    if (card) {
        const messageDiv = card.closest('.message');
        if (messageDiv) messageDiv.remove();
    }
}

async function confirmInlineUpload() {
    if (!pendingImageFile || !pendingImagePath) return;
    
    const card = document.getElementById('inline-upload-card');
    const isVideo = isVideoFile(pendingImageFile.name);
    const mode = inlineUploadCardMode;
    
    // Store file info for later processing (after topic is provided)
    uploadedFile = pendingImageFile;
    uploadedFile.placement = 'background';
    uploadedFile.filePath = pendingImagePath;
    uploadedFile.uploadMode = mode;
    
    // Set pending state - DNA extraction happens AFTER topic is provided
    pendingPersonalizeRender = true;
    personalizeVideoData = {
        mode: mode,
        filePath: pendingImagePath,
        needsExtraction: true  // Flag that we need to extract DNA when topic is provided
    };
    
    // Remove card and ask for topic (no expensive AI calls yet)
    const messageDiv = card?.closest('.message');
    if (messageDiv) messageDiv.remove();
    
    if (mode === 'reskin') {
        addMessage("What's your topic? Tell me what this video should be about and I'll regenerate the visuals to match.", false);
    } else if (mode === 'clipper') {
        addMessage("What's your script or topic? I'll create a video using this clip's editing style.", false);
    } else {
        // Non-video file - simple file attachment
        document.getElementById('file-name').textContent = pendingImageFile.name;
        document.getElementById('file-preview').classList.add('show');
    }
}

async function confirmImagePlacement() {
    uploadedFile = pendingImageFile;
    uploadedFile.placement = selectedPlacement;
    uploadedFile.analysis = pendingImageAnalysis;
    uploadedFile.filePath = pendingImagePath;
    uploadedFile.uploadMode = selectedUploadMode;
    
    let modeLabel = '';
    if (selectedUploadMode === 'reskin') {
        modeLabel = 'AI Remix';
        
        if (isVideoFile(pendingImageFile.name)) {
            showToast('Extracting creative DNA...');
            try {
                const response = await fetch('/extract-creative-dna', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: pendingImagePath })
                });
                const data = await response.json();
                if (data.creative_dna) {
                    uploadedFile.creativeDna = data.creative_dna;
                    uploadedFile.creativeDecisions = data.creative_dna.adjustable_elements || {};
                    showToast(`Learned style: ${data.creative_dna.scenes?.length || 0} scenes, ${data.creative_dna.total_duration?.toFixed(1)}s`);
                }
            } catch (err) {
                console.error('Creative DNA extraction error:', err);
                showToast('Style extraction failed');
            }
        }
    } else if (selectedUploadMode === 'clipper') {
        modeLabel = 'Clipper';
        
        if (isVideoFile(pendingImageFile.name)) {
            showToast('Analyzing clip structure...');
            try {
                const response = await fetch('/extract-video-template', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: pendingImagePath })
                });
                const data = await response.json();
                if (data.success) {
                    uploadedFile.templateData = data;
                    uploadedFile.templateId = data.template_id;
                    showToast(`Clip analyzed: ${data.scene_count} scenes`);
                }
            } catch (err) {
                console.error('Clip analysis error:', err);
                showToast('Clip analysis failed');
            }
        }
    } else {
        modeLabel = selectedPlacement === 'background' ? 'Background' : 'Pop-up';
    }
    
    document.getElementById('file-name').textContent = `${pendingImageFile.name} (${modeLabel})`;
    document.getElementById('file-preview').classList.add('show');
    document.getElementById('image-placement-popup').classList.remove('show');
    document.body.style.overflow = '';
    
    const vidPreview = document.getElementById('video-placement-preview');
    if (vidPreview) {
        vidPreview.pause();
        vidPreview.src = '';
    }
    
    document.getElementById('composer-input').focus();
}

// Stage navigation with workflow guides (Echo Engine controls transitions)
function showStage(stage) {
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
    const stageEl = document.getElementById('stage-' + stage);
    if (stageEl) stageEl.classList.add('active');
    
    // Show/hide bottom chat bar based on stage
    const bottomChatBar = document.getElementById('bottom-chat-bar');
    
    if (bottomChatBar) {
        if (stage === 'create') {
            // On create stage, show the bottom chat bar
            bottomChatBar.style.display = 'block';
        } else {
            // On other stages, hide the bottom chat bar
            bottomChatBar.style.display = 'none';
        }
    }
    
    // Add workflow guide for review stage (caption styling - step 5)
    if (stage === 'review' && currentWorkflowStep >= 4 && currentWorkflowStep < 5) {
        setTimeout(() => {
            currentWorkflowStep = 5;
            storeWorkflowPreference('caption_styling_started', true);
            renderWorkflowGuide(5);
        }, 2000);
    }
    
    // Auto-trigger character detection when entering review stage
    if (stage === 'review') {
        const script = document.getElementById('script-text').textContent;
        const charGrid = document.querySelector('.character-layers-inline');
        // Only auto-detect if we have a script and characters haven't been detected yet
        if (script && script !== 'Script will appear here after AI generates it...' && charGrid && !charGrid.querySelector('.character-layer')) {
            setTimeout(() => detectCharacters(), 500);
        }
    }
    
    // Add workflow guide for export stage
    if (stage === 'export' && currentWorkflowStep < 8) {
        currentWorkflowStep = 8;
        storeWorkflowPreference('export_started', true);
        renderWorkflowGuide(8);
    }
}

// Voice selection
function selectVoice(el) {
    document.querySelectorAll('.voice-option').forEach(v => v.classList.remove('selected'));
    el.classList.add('selected');
    selectedVoice = el.dataset.voice;
}

// Format selection
function selectFormat(el) {
    document.querySelectorAll('.format-option').forEach(f => f.classList.remove('selected'));
    el.classList.add('selected');
    selectedFormat = el.dataset.format;
    updatePhoneFrameFormat(selectedFormat);
}

function updatePhoneFrameFormat(format) {
    const phoneFrame = document.querySelector('.phone-frame');
    const formatIndicator = document.getElementById('format-indicator');
    
    const formatLabels = {
        '9:16': '9:16 Portrait',
        '16:9': '16:9 Landscape',
        '1:1': '1:1 Square',
        '4:5': '4:5 Portrait'
    };
    
    if (formatIndicator) {
        formatIndicator.textContent = formatLabels[format] || format;
    }
    
    if (phoneFrame) {
        const aspectRatios = {
            '9:16': '9/16',
            '16:9': '16/9',
            '1:1': '1/1',
            '4:5': '4/5'
        };
        phoneFrame.style.aspectRatio = aspectRatios[format] || '9/16';
    }
}

// Caption settings
let selectedCaptionColor = '#FFFFFF';
let selectedCaptionFont = "'Bebas Neue', sans-serif";
let captionAnimationInterval = null;
let selectedCaptionPosition = 'center';
let selectedCaptionAnimation = 'highlight';
let selectedHighlightColor = '#FFD60A';

function selectCaptionColor(el) {
    document.querySelectorAll('.color-preset').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
    selectedCaptionColor = el.dataset.color;
    updateCaptionPreview();
}

function selectCaptionFont(el) {
    document.querySelectorAll('.font-option').forEach(f => f.classList.remove('selected'));
    el.classList.add('selected');
    selectedCaptionFont = el.dataset.font;
    updateCaptionPreview();
}

function toggleCaptionPill(el) {
    const checkbox = el.querySelector('input');
    if (checkbox) {
        checkbox.checked = !checkbox.checked;
        el.classList.toggle('active', checkbox.checked);
    }
    updateCaptionPreview();
}

function selectCaptionPosition(el) {
    document.querySelectorAll('.position-btn').forEach(b => b.classList.remove('selected'));
    el.classList.add('selected');
    selectedCaptionPosition = el.dataset.position;
    
    // Update preview position
    const preview = document.getElementById('caption-preview');
    if (preview) {
        preview.classList.remove('position-top', 'position-center', 'position-bottom');
        preview.classList.add(`position-${selectedCaptionPosition}`);
    }
}

// Caption Style Template System
let selectedCaptionTemplate = 'bold_pop';
let captionStyleHistory = [];
let captionHistoryIndex = -1;

// Load caption history on init
async function loadCaptionHistory() {
    try {
        const response = await fetch('/get-caption-history');
        const data = await response.json();
        
        if (data.success && data.history && data.history.length > 0) {
            captionStyleHistory = data.history.map(h => h.template_key);
            captionHistoryIndex = captionStyleHistory.length - 1;
            
            // Select the most recent style
            const latestStyle = captionStyleHistory[captionHistoryIndex];
            if (latestStyle) {
                selectCaptionStyle(latestStyle, null);
            }
            
            updateCaptionHistoryButtons();
            console.log('[Caption] Loaded history:', captionStyleHistory);
        }
    } catch (error) {
        console.error('[Caption] Failed to load history:', error);
    }
}

// Initialize caption history when page loads
document.addEventListener('DOMContentLoaded', function() {
    loadCaptionHistory();
});

function selectCaptionStyle(styleKey, el) {
    selectedCaptionTemplate = styleKey;
    
    // Update UI selection
    document.querySelectorAll('.caption-style-card').forEach(card => {
        card.classList.remove('selected');
    });
    if (el) el.classList.add('selected');
    
    // Find card by data-style if el not provided
    if (!el) {
        const card = document.querySelector(`.caption-style-card[data-style="${styleKey}"]`);
        if (card) card.classList.add('selected');
    }
    
    console.log('[Caption] Selected style:', styleKey);
}

async function refreshCaptionStyle() {
    const refreshBtn = document.getElementById('style-refresh-btn');
    if (refreshBtn) {
        refreshBtn.style.opacity = '0.5';
        refreshBtn.disabled = true;
    }
    
    try {
        const response = await fetch('/refresh-caption-style', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                current_style: selectedCaptionTemplate,
                content_type: currentContentType || 'general'
            })
        });
        
        const data = await response.json();
        
        if (data.success && data.new_style) {
            // Add current to history before changing
            if (captionHistoryIndex === captionStyleHistory.length - 1 || captionStyleHistory.length === 0) {
                captionStyleHistory.push(selectedCaptionTemplate);
            }
            
            // Select new style
            selectCaptionStyle(data.new_style.key, null);
            
            // Add new to history
            captionStyleHistory.push(data.new_style.key);
            captionHistoryIndex = captionStyleHistory.length - 1;
            
            updateCaptionHistoryButtons();
        }
    } catch (error) {
        console.error('[Caption] Refresh error:', error);
    } finally {
        if (refreshBtn) {
            refreshBtn.style.opacity = '1';
            refreshBtn.disabled = false;
        }
    }
}

function navigateCaptionHistory(direction) {
    const newIndex = captionHistoryIndex + direction;
    
    if (newIndex >= 0 && newIndex < captionStyleHistory.length) {
        captionHistoryIndex = newIndex;
        const styleKey = captionStyleHistory[captionHistoryIndex];
        selectCaptionStyle(styleKey, null);
        updateCaptionHistoryButtons();
    }
}

function updateCaptionHistoryButtons() {
    const backBtn = document.getElementById('style-back-btn');
    const forwardBtn = document.getElementById('style-forward-btn');
    
    if (backBtn) {
        backBtn.disabled = captionHistoryIndex <= 0;
    }
    if (forwardBtn) {
        forwardBtn.disabled = captionHistoryIndex >= captionStyleHistory.length - 1;
    }
}

function updateCaptionAnimation(animationType) {
    selectedCaptionAnimation = animationType;
    
    // Show/hide highlight color based on animation type
    const highlightGroup = document.getElementById('highlight-color-group');
    if (highlightGroup) {
        highlightGroup.style.display = ['highlight', 'bounce', 'karaoke'].includes(animationType) ? 'flex' : 'none';
    }
    
    updateCaptionPreview();
}

function selectHighlightColor(el) {
    document.querySelectorAll('#highlight-colors .color-preset').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
    selectedHighlightColor = el.dataset.color;
    
    // Update CSS variable for highlight color
    const preview = document.getElementById('caption-text-preview');
    if (preview) {
        preview.style.setProperty('--highlight-color', selectedHighlightColor);
    }
    
    updateCaptionPreview();
}

function updateCaptionPreview() {
    const preview = document.getElementById('caption-text-preview');
    if (!preview) return;
    
    const hasShadow = document.getElementById('caption-shadow')?.checked ?? true;
    const hasOutline = document.getElementById('caption-outline')?.checked ?? true;
    const isUppercase = document.getElementById('caption-uppercase')?.checked ?? true;
    
    preview.style.color = selectedCaptionColor;
    preview.style.fontFamily = selectedCaptionFont;
    preview.classList.toggle('shadow', hasShadow);
    preview.classList.toggle('outline', hasOutline);
    
    const words = isUppercase ? ['THE', 'QUICK', 'BROWN', 'FOX'] : ['The', 'quick', 'brown', 'fox'];
    updateCaptionWords(words);
}

function updateCaptionWords(words) {
    const preview = document.getElementById('caption-text-preview');
    if (!preview) return;
    
    // Clear existing animation
    if (captionAnimationInterval) {
        clearInterval(captionAnimationInterval);
    }
    
    // Remove old animation classes
    preview.classList.remove('animation-highlight', 'animation-bounce', 'animation-karaoke', 'animation-typewriter');
    
    // Add current animation class
    if (selectedCaptionAnimation !== 'none') {
        preview.classList.add(`animation-${selectedCaptionAnimation}`);
    }
    
    // Set highlight color CSS variable
    preview.style.setProperty('--highlight-color', selectedHighlightColor);
    
    // Create word spans based on animation type
    const initialClass = selectedCaptionAnimation === 'typewriter' ? '' : (selectedCaptionAnimation !== 'none' ? ' active' : '');
    preview.innerHTML = words.map((word, i) => 
        `<span class="caption-word${i === 0 ? initialClass : ''}">${word}</span>`
    ).join(' ');
    
    // Skip animation if set to none
    if (selectedCaptionAnimation === 'none') return;
    
    // Animate based on type
    let currentWord = 0;
    const animationSpeed = selectedCaptionAnimation === 'typewriter' ? 300 : 500;
    
    captionAnimationInterval = setInterval(() => {
        const wordEls = preview.querySelectorAll('.caption-word');
        
        if (selectedCaptionAnimation === 'typewriter') {
            // Typewriter: reveal words one by one
            wordEls.forEach((w, i) => {
                w.classList.toggle('visible', i <= currentWord);
            });
        } else {
            // Highlight/Bounce/Karaoke: highlight current word
            wordEls.forEach((w, i) => {
                const isActive = i === currentWord;
                w.classList.toggle('active', isActive);
                w.classList.toggle('highlight', isActive);
                if (isActive) {
                    w.style.color = selectedHighlightColor;
                } else {
                    w.style.color = '';
                }
            });
        }
        
        currentWord = (currentWord + 1) % words.length;
        
        // Reset typewriter after showing all words
        if (selectedCaptionAnimation === 'typewriter' && currentWord === 0) {
            wordEls.forEach(w => w.classList.remove('visible'));
        }
    }, animationSpeed);
}

function getCaptionSettings() {
    return {
        enabled: true,
        position: selectedCaptionPosition,
        animation: selectedCaptionAnimation,
        highlightColor: selectedHighlightColor,
        uppercase: document.getElementById('caption-uppercase')?.checked ?? true,
        outline: document.getElementById('caption-outline')?.checked ?? true,
        shadow: document.getElementById('caption-shadow')?.checked ?? true,
        textColor: selectedCaptionColor,
        font: selectedCaptionFont
    };
}

// Start caption animation when page loads
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => updateCaptionPreview(), 500);
});

// Character Casting
let characterVoices = { 'NARRATOR': 'the_anchor' };
let detectedCharacters = [];

async function previewVoiceChars() {
    const script = document.getElementById('script-text').textContent;
    if (!script || script === 'Script will appear here after AI generates it...') {
        return;
    }
    
    try {
        const resp = await fetch('/preview-voice-chars', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script })
        });
        
        const data = await resp.json();
        document.getElementById('voice-char-count').textContent = data.chars;
        document.getElementById('voice-char-text').textContent = data.dialogue || '(No dialogue extracted)';
        document.getElementById('voice-char-preview').style.display = 'block';
    } catch (err) {
        console.error('Voice char preview error:', err);
    }
}

async function detectCharacters() {
    const script = document.getElementById('script-text').textContent;
    if (!script || script === 'Script will appear here after AI generates it...') {
        showToast('Generate a script first');
        return;
    }
    
    startLoading();
    try {
        const resp = await fetch('/detect-characters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script })
        });
        
        const data = await resp.json();
        if (data.success && data.characters) {
            detectedCharacters = data.characters;
            renderCastingPanel(data.characters);
            renderCharacterLayers(data.characters);
            document.getElementById('voice-count').textContent = `(${data.characters.length} characters)`;
            // Update cost when characters change
            updateTotalTokenCost();
            // Show voice character count preview
            previewVoiceChars();
        } else {
            showToast('Could not detect characters');
        }
    } catch (err) {
        console.error(err);
        showToast('Error detecting characters');
    } finally {
        stopLoading();
    }
}

async function rebuildScript() {
    // Get the original input that created this script
    const originalInput = localStorage.getItem('lastProjectInput') || '';
    if (!originalInput) {
        showToast('No original input found. Start a new project instead.');
        return;
    }
    
    showNotification('Rebuilding script and visuals...', 'info');
    startLoading();
    
    try {
        // Step 1: Regenerate script via chat
        const chatResp = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                message: `Rewrite this as a fresh video script with different angles and hooks:\n\n${originalInput}`,
                conversation: []
            })
        });
        
        const chatData = await chatResp.json();
        if (chatData.reply) {
            currentScript = chatData.reply;
            updateScriptCard(chatData.reply);
            
            // Step 2: Re-curate visuals for new script
            showNotification('Curating new visuals...', 'info');
            const curateResp = await fetch('/curate-visuals', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ script: currentScript })
            });
            
            const curateData = await curateResp.json();
            if (curateData.success && curateData.visual_board) {
                populateScenesFromVisualBoard(curateData.visual_board);
                renderSceneComposer();
                showToast('Script and visuals rebuilt!');
            }
        } else {
            showToast('Failed to rebuild script');
        }
    } catch (err) {
        console.error('Rebuild error:', err);
        showToast('Error rebuilding script');
    } finally {
        stopLoading();
    }
}

// Workflow card navigation
const workflowCardOrder = ['script', 'visuals', 'voices', 'soundfx', 'format', 'captions'];
let currentWorkflowCard = 'script';

function advanceToCard(cardName) {
    // Hide all workflow cards
    workflowCardOrder.forEach(name => {
        const card = document.getElementById(`workflow-card-${name}`);
        if (card) card.style.display = 'none';
    });
    
    // Show the target card
    const targetCard = document.getElementById(`workflow-card-${cardName}`);
    if (targetCard) {
        targetCard.style.display = 'block';
        currentWorkflowCard = cardName;
        
        // Reset visuals card button visibility when entering
        if (cardName === 'visuals') {
            const btn = document.getElementById('visual-picker-toggle');
            const container = document.getElementById('anchor-cards-container');
            if (btn && container) {
                // Show button, hide container (fresh start)
                btn.style.display = 'block';
                container.style.display = 'none';
                container.innerHTML = '';
            }
        }
        
        // Scroll to the card
        targetCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

function showWorkflowCard(cardName) {
    advanceToCard(cardName);
}

// Script card functions
let scriptEditMode = false;

function updateScriptCard(script, metadata = {}) {
    const scriptText = document.getElementById('script-text');
    const hookText = document.getElementById('script-hook-text');
    const durationEl = document.getElementById('script-duration');
    const scenesEl = document.getElementById('script-scenes');
    const qualityEl = document.getElementById('script-quality');
    const editBtn = document.getElementById('script-edit-btn');
    const editArea = document.getElementById('script-edit-area');
    
    if (!scriptText) return;
    
    // Handle empty/invalid script
    const cleanScript = (script || '').trim();
    if (!cleanScript) {
        scriptText.textContent = 'Script will appear here after AI generates it...';
        if (hookText) hookText.textContent = 'No hook yet';
        if (durationEl) durationEl.textContent = '0s';
        if (scenesEl) scenesEl.textContent = '0 scenes';
        if (qualityEl) qualityEl.style.display = 'none';
        if (editBtn) editBtn.style.display = 'none';
        return;
    }
    
    // Update script content
    scriptText.textContent = cleanScript;
    
    // Sync edit area if in edit mode
    if (scriptEditMode && editArea) {
        editArea.value = cleanScript;
    }
    
    // Extract hook (first meaningful dialogue line)
    const lines = cleanScript.split('\n').filter(l => l.trim());
    const skipPatterns = /^(SCENE|VISUAL:|CUT:|---|\[|\#)/i;
    const firstContent = lines.find(l => !skipPatterns.test(l.trim()) && l.trim().length > 10);
    if (hookText) {
        if (firstContent) {
            hookText.textContent = firstContent.substring(0, 120) + (firstContent.length > 120 ? '...' : '');
        } else {
            hookText.textContent = lines[0]?.substring(0, 120) || 'No hook detected';
        }
    }
    
    // Calculate duration estimate (roughly 2.5 words per second)
    const words = cleanScript.split(/\s+/).filter(w => w.length > 0);
    const wordCount = words.length;
    const duration = wordCount > 0 ? Math.round(wordCount / 2.5) : 0;
    if (durationEl) durationEl.textContent = duration + 's';
    
    // Count scenes (case insensitive, multiple formats)
    const sceneMatches = cleanScript.match(/\b(scene\s*\d+|scene\s*#?\d+)/gi) || [];
    const sceneCount = sceneMatches.length || (cleanScript.length > 50 ? 1 : 0);
    if (scenesEl) scenesEl.textContent = sceneCount + ' scene' + (sceneCount !== 1 ? 's' : '');
    
    // Handle quality score
    if (qualityEl) {
        if (metadata.quality !== undefined && metadata.quality !== null) {
            qualityEl.textContent = metadata.quality;
            qualityEl.style.display = 'inline';
            qualityEl.className = 'script-quality-badge';
            if (metadata.quality < 60) qualityEl.classList.add('low');
            else if (metadata.quality < 80) qualityEl.classList.add('medium');
        } else {
            qualityEl.style.display = 'none';
        }
    }
    
    // Show edit button
    if (editBtn) editBtn.style.display = 'inline-block';
}

function toggleScriptEdit() {
    const scriptText = document.getElementById('script-text');
    const editArea = document.getElementById('script-edit-area');
    const editBtn = document.getElementById('script-edit-btn');
    const confirmBtn = document.getElementById('script-confirm-btn');
    
    if (!scriptText || !editArea) return;
    
    scriptEditMode = !scriptEditMode;
    
    if (scriptEditMode) {
        // Enter edit mode
        editArea.value = scriptText.textContent;
        scriptText.style.display = 'none';
        editArea.style.display = 'block';
        editBtn.style.display = 'none';
        confirmBtn.style.display = 'inline-block';
        editArea.focus();
    } else {
        // Exit edit mode
        scriptText.style.display = 'block';
        editArea.style.display = 'none';
        editBtn.style.display = 'inline-block';
        confirmBtn.style.display = 'none';
    }
}

function confirmScriptEdit() {
    const scriptText = document.getElementById('script-text');
    const editArea = document.getElementById('script-edit-area');
    
    if (!scriptText || !editArea) return;
    
    const newScript = editArea.value.trim();
    if (!newScript) {
        showToast('Script cannot be empty');
        return;
    }
    
    currentScript = newScript;
    updateScriptCard(newScript);
    saveCurrentProject({ script: newScript });
    showToast('Script updated');
    
    toggleScriptEdit();
}

function renderCastingPanel(characters) {
    const container = document.getElementById('casting-characters');
    
    const voiceOptions = `
        <option value="the_anchor">The Anchor</option>
        <option value="british_authority">British Authority</option>
        <option value="the_storyteller">The Storyteller</option>
        <option value="aussie_casual">Aussie Casual</option>
        <option value="power_exec">Power Exec</option>
        <option value="documentary_pro">Documentary Pro</option>
        <option value="hype_machine">Hype Machine</option>
        <option value="cinema_epic">Cinema Epic</option>
        <option value="whisper_intimate">Whisper Intimate</option>
        <option value="zen_guide">Zen Guide</option>
        <option value="warm_narrator">Warm Narrator</option>
        <option value="countdown_king">Countdown King</option>
        <option value="custom">Custom Voice</option>
    `;
    
    container.innerHTML = characters.map(char => {
        const currentVoice = characterVoices[char.name] || 'the_anchor';
        return `
            <div class="character-cast">
                <div class="cast-info">
                    <div class="cast-name">${char.name}</div>
                    <div class="cast-desc">${char.personality || ''}</div>
                </div>
                <div class="cast-voice-row">
                    <select class="cast-voice" id="voice-${char.name}" onchange="handleVoiceChange('${char.name}', this.value)">
                        ${voiceOptions.replace(`value="${currentVoice}"`, `value="${currentVoice}" selected`)}
                    </select>
                    <button class="voice-preview-btn" onclick="previewVoice(document.getElementById('voice-${char.name}').value)" title="Preview voice">&#9654;</button>
                </div>
            </div>
        `;
    }).join('');
}

function updateCharacterVoice(characterName, voice) {
    characterVoices[characterName] = voice;
}

// Custom voice prompts storage
let customVoicePrompts = {};
let customVoiceModels = {};
let previousVoiceSelection = {};

function handleVoiceChange(characterName, voice) {
    if (voice === 'custom') {
        // Store current value before opening popup
        const select = document.getElementById('voice-' + characterName);
        previousVoiceSelection[characterName] = select ? select.dataset.previousValue || 'the_anchor' : 'the_anchor';
        showCustomVoicePopup(characterName);
    } else {
        updateCharacterVoice(characterName, voice);
        // Track this selection for cancel recovery
        const select = document.getElementById('voice-' + characterName);
        if (select) select.dataset.previousValue = voice;
    }
}

function showCustomVoicePopup(characterName) {
    const models = extractedModels || [];
    const modelOptions = models.length > 0 
        ? models.map(m => `<option value="${m.name}">${m.name}</option>`).join('')
        : '<option value="">No models available</option>';
    
    const modal = document.createElement('div');
    modal.className = 'custom-voice-modal';
    modal.id = 'custom-voice-modal';
    modal.innerHTML = `
        <div class="custom-voice-content">
            <h3>Custom Voice for ${characterName}</h3>
            <div class="custom-voice-field">
                <label>What kind of voiceover do you want?</label>
                <textarea id="custom-voice-prompt" placeholder="e.g., Calm, soothing narrator with a slight British accent. Speaks slowly and thoughtfully..."></textarea>
            </div>
            <div class="custom-voice-field">
                <label>Character model (optional)</label>
                <select id="custom-voice-model">
                    <option value="">None - voice only</option>
                    ${modelOptions}
                </select>
            </div>
            <div class="custom-voice-actions">
                <button class="btn-cancel" onclick="cancelCustomVoice('${characterName}')">Cancel</button>
                <button class="btn-confirm" onclick="confirmCustomVoice('${characterName}')">Confirm</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

function cancelCustomVoice(characterName) {
    const modal = document.getElementById('custom-voice-modal');
    if (modal) modal.remove();
    
    // Reset dropdown to the actual previous value
    const select = document.getElementById('voice-' + characterName);
    if (select) {
        const prevValue = previousVoiceSelection[characterName] || 'the_anchor';
        select.value = prevValue;
    }
}

function confirmCustomVoice(characterName) {
    const prompt = document.getElementById('custom-voice-prompt').value.trim();
    const model = document.getElementById('custom-voice-model').value;
    
    if (!prompt) {
        showToast('Please describe the voiceover style');
        return;
    }
    
    customVoicePrompts[characterName] = prompt;
    customVoiceModels[characterName] = model || null;
    characterVoices[characterName] = 'custom';
    
    const modal = document.getElementById('custom-voice-modal');
    if (modal) modal.remove();
    
    showToast(`Custom voice set for ${characterName}`);
}

// Framd AI Panel
let framdAIMessages = [];

function openFramdAI() {
    // Create overlay
    const overlay = document.createElement('div');
    overlay.className = 'framd-ai-overlay';
    overlay.id = 'framd-ai-overlay';
    overlay.onclick = closeFramdAI;
    document.body.appendChild(overlay);
    
    // Create modal
    const modal = document.createElement('div');
    modal.className = 'framd-ai-modal';
    modal.id = 'framd-ai-modal';
    modal.innerHTML = `
        <div class="framd-ai-header">
            <h3>Framd AI - Edit Script</h3>
            <button class="framd-ai-close" onclick="closeFramdAI()">&times;</button>
        </div>
        <div class="framd-ai-messages" id="framd-ai-messages">
            <div class="framd-ai-msg ai">Hi! I can help you edit your script. What changes would you like to make?</div>
        </div>
        <div class="framd-ai-input-area">
            <input type="text" id="framd-ai-input" placeholder="Describe changes to your script..." onkeypress="if(event.key==='Enter')sendFramdAIMessage()">
            <button class="framd-ai-send" onclick="sendFramdAIMessage()">Send</button>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Focus input
    document.getElementById('framd-ai-input').focus();
}

function closeFramdAI() {
    const modal = document.getElementById('framd-ai-modal');
    const overlay = document.getElementById('framd-ai-overlay');
    if (modal) modal.remove();
    if (overlay) overlay.remove();
}

async function sendFramdAIMessage() {
    const input = document.getElementById('framd-ai-input');
    const message = input.value.trim();
    if (!message) return;
    
    const messagesContainer = document.getElementById('framd-ai-messages');
    
    // Add user message
    messagesContainer.innerHTML += `<div class="framd-ai-msg user">${message}</div>`;
    input.value = '';
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
    
    // Show loading
    document.body.classList.add('loading');
    
    try {
        const currentScript = document.getElementById('script-text').textContent;
        
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: `Current script:\n\n${currentScript}\n\nUser request: ${message}\n\nPlease provide the edited script based on the user's request. Return ONLY the edited script text, no explanations.`
            })
        });
        
        const data = await response.json();
        
        if (data.response) {
            // Update the script
            updateScriptCard(data.response);
            messagesContainer.innerHTML += `<div class="framd-ai-msg ai">Done! I've updated the script based on your request.</div>`;
        } else {
            messagesContainer.innerHTML += `<div class="framd-ai-msg ai">Sorry, I couldn't process that request.</div>`;
        }
    } catch (error) {
        messagesContainer.innerHTML += `<div class="framd-ai-msg ai">Error: ${error.message}</div>`;
    } finally {
        document.body.classList.remove('loading');
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

// Scene Composer
let scenes = [];
let extractedModels = [];
let sceneIdCounter = 0;

// Populate scenes from visual board sections (called after curation)
function populateScenesFromVisualBoard(visualBoard) {
    scenes = [];
    sceneIdCounter = 0;
    
    if (!visualBoard || !visualBoard.sections || !Array.isArray(visualBoard.sections)) {
        console.log('No sections in visual board to populate scenes');
        return;
    }
    
    // Store the visual board so getSceneVisuals can access suggested_videos
    currentVisualPlan = visualBoard;
    console.log('[populateScenesFromVisualBoard] Set currentVisualPlan with', visualBoard.sections.length, 'sections');
    // Debug: check if sections have suggested_videos
    visualBoard.sections.forEach((s, i) => {
        console.log('[populateScenesFromVisualBoard] Section', i, 'suggested_videos:', s.suggested_videos?.length || 0);
    });
    
    visualBoard.sections.forEach((section, idx) => {
        const sceneId = sceneIdCounter++;
        // IDEA-DRIVEN: Use the core idea being discussed, not scene setting
        let label = section.idea || section.scene_label || `Section ${idx + 1}`;
        // If no idea, try to extract from script segment
        if (!section.idea && section.script_segment) {
            label = section.script_segment.substring(0, 60) + (section.script_segment.length > 60 ? '...' : '');
        }
        
        scenes.push({
            id: sceneId,
            sectionIndex: idx,
            label: label,
            idea: section.idea || label,  // Store the full idea
            visual_concept: section.visual_concept || '',
            duration: section.duration_seconds || 5,
            background: null,
            layers: [],
            minimized: idx > 0  // First scene expanded, rest minimized
        });
    });
    
    console.log('Populated', scenes.length, 'scenes from visual board');
    // Note: Scene composer UI removed - visuals are selected in anchor cards
    
    // Advance to step 4 - Scene Review
    if (currentWorkflowStep < 4 && scenes.length > 0) {
        currentWorkflowStep = 4;
        updateProjectWorkflowStep(4);
        storeWorkflowPreference('scenes_populated', true);
        
        // Add guide message with action button
        setTimeout(() => {
            renderWorkflowGuide(4);
        }, 1000);
    }
}

// Fetch visuals for scenes that don't have them
async function fetchMissingSceneVisuals() {
    console.log('[fetchMissingSceneVisuals] Starting for', scenes.length, 'scenes');
    
    for (const scene of scenes) {
        console.log('[fetchMissingSceneVisuals] Processing scene', scene.id, 'idea:', scene.idea?.substring(0, 30));
        
        // Check existing visuals
        const existingVisuals = getSceneVisuals(scene);
        console.log('[fetchMissingSceneVisuals] Scene', scene.id, 'has', existingVisuals.length, 'existing visuals');
        
        if (existingVisuals.length > 0) {
            console.log('[fetchMissingSceneVisuals] Skipping scene', scene.id, '- already has visuals');
            continue;
        }
        
        // Fetch visuals using the scene's idea/label
        const sceneText = scene.idea || scene.label || '';
        if (!sceneText) {
            console.log('[fetchMissingSceneVisuals] Skipping scene', scene.id, '- no text');
            continue;
        }
        
        try {
            console.log('[fetchMissingSceneVisuals] Fetching visuals for:', sceneText.substring(0, 50));
            const response = await fetch('/scene-visuals', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scene_text: sceneText,
                    scene_type: 'CLAIM',
                    keywords: []
                })
            });
            
            const data = await response.json();
            console.log('[fetchMissingSceneVisuals] Response:', data.success, 'images:', data.images?.length, 'curated:', data.curated?.length, 'backgrounds:', data.backgrounds?.length);
            
            // Combine all visual categories from response
            const allVisuals = [
                ...(data.images || []),
                ...(data.curated || []),
                ...(data.backgrounds || []),
                ...(data.characters || [])
            ];
            
            console.log('[fetchMissingSceneVisuals] Total visuals:', allVisuals.length);
            
            if (data.success && allVisuals.length > 0) {
                // Cache visuals in the scene object - handle Wikimedia/Pexels formats
                scene.cachedVisuals = allVisuals.slice(0, 6).map(img => ({
                    thumbnail: img.thumbnail || img.url || img.src?.medium,
                    preview_url: img.thumbnail || img.url || img.src?.medium,
                    url: img.url || img.src?.large2x || img.src?.original,
                    title: img.alt || img.title || img.character_name || 'Visual',
                    attribution: img.attribution || (img.photographer ? `${img.photographer} / Pexels` : '')
                }));
                console.log('[fetchMissingSceneVisuals] Cached', scene.cachedVisuals.length, 'visuals for scene', scene.id);
                renderScenes(); // Re-render to show fetched visuals
            }
        } catch (err) {
            console.warn('[fetchMissingSceneVisuals] Failed for scene:', scene.id, err);
        }
    }
    console.log('[fetchMissingSceneVisuals] Complete');
}

// Get visuals for a specific scene (checks visual plan first, then cached visuals)
function getSceneVisuals(scene) {
    // First try currentVisualPlan sections
    if (currentVisualPlan && currentVisualPlan.sections && scene.sectionIndex !== undefined) {
        const section = currentVisualPlan.sections[scene.sectionIndex];
        if (section && section.suggested_videos && section.suggested_videos.length > 0) {
            console.log('[getSceneVisuals] Scene', scene.id, 'using', section.suggested_videos.length, 'suggested_videos from visual plan');
            return section.suggested_videos.map(video => ({
                thumbnail: video.thumbnail,
                preview_url: video.thumbnail,
                url: video.download_url || video.url,
                video_url: video.download_url || video.url,
                title: video.title || 'Scene visual',
                attribution: video.attribution || ''
            }));
        }
    }
    
    // Fallback to scene's cached visuals (from fetchMissingSceneVisuals)
    if (scene.cachedVisuals && scene.cachedVisuals.length > 0) {
        console.log('[getSceneVisuals] Scene', scene.id, 'using', scene.cachedVisuals.length, 'cached visuals');
        return scene.cachedVisuals;
    }
    
    console.log('[getSceneVisuals] Scene', scene.id, 'has no visuals');
    return [];
}

// Helper to extract all visuals (for fallback)
function getVisualsArray() {
    if (!currentVisualPlan) return [];
    if (Array.isArray(currentVisualPlan)) return currentVisualPlan;
    
    // Try various properties that might contain visuals
    if (currentVisualPlan.curated_visuals) return currentVisualPlan.curated_visuals;
    if (currentVisualPlan.visuals) return currentVisualPlan.visuals;
    if (currentVisualPlan.images) return currentVisualPlan.images;
    
    // Extract from sections if visual_board format
    if (currentVisualPlan.sections && Array.isArray(currentVisualPlan.sections)) {
        const allVisuals = [];
        currentVisualPlan.sections.forEach(section => {
            if (section.suggested_videos && section.suggested_videos.length > 0) {
                section.suggested_videos.forEach(video => {
                    allVisuals.push({
                        thumbnail: video.thumbnail,
                        preview_url: video.thumbnail,
                        url: video.download_url,
                        video_url: video.download_url,
                        title: video.title || `Scene visual`,
                        license: video.license,
                        duration: video.duration
                    });
                });
            }
        });
        return allVisuals;
    }
    
    return [];
}

function addNewScene() {
    const sceneId = sceneIdCounter++;
    const scene = {
        id: sceneId,
        background: null,
        layers: [],
        minimized: false
    };
    scenes.push(scene);
    renderScenes();
}

function renderScenes() {
    const container = document.getElementById('scenes-container');
    if (!container) {
        console.log('[renderScenes] No scenes-container found');
        return;
    }
    if (scenes.length === 0) {
        container.innerHTML = '<div class="no-models-msg">No sections yet. Generate a script to see idea-driven visuals.</div>';
        return;
    }
    
    console.log('[renderScenes] Rendering', scenes.length, 'scenes');
    
    container.innerHTML = scenes.map((scene, idx) => {
        // Get visuals using unified function (checks visual plan + cached visuals)
        let sceneVisuals = getSceneVisuals(scene);
        console.log('[renderScenes] Scene', scene.id, 'has', sceneVisuals.length, 'visuals, cachedVisuals:', scene.cachedVisuals?.length || 0);
        
        // Last fallback to global visuals array
        if (sceneVisuals.length === 0) {
            sceneVisuals = getVisualsArray().slice(idx * 3, (idx + 1) * 3);
        }
        
        // IDEA-DRIVEN: Show the core idea being discussed
        const ideaText = scene.idea || scene.label || `Section ${idx + 1}`;
        const hasSelection = scene.background !== null;
        
        return `
        <div class="scene-card ${scene.minimized ? 'minimized' : ''}" data-scene-id="${scene.id}">
            <div class="scene-header" onclick="toggleScene(${scene.id})">
                <span class="scene-title">
                    <strong>${idx + 1}.</strong> ${escapeHtml(ideaText)}
                    ${hasSelection ? ' <span style="color:#4ade80;">✓</span>' : ''}
                </span>
                <span class="scene-toggle">${scene.minimized ? '▼' : '▲'}</span>
            </div>
            <div class="scene-body" style="display: ${scene.minimized ? 'none' : 'block'}">
                ${scene.visual_concept ? `<div class="scene-setting" style="font-size:0.75rem;color:var(--accent);margin-bottom:0.75rem;font-style:italic;">Visual: ${escapeHtml(scene.visual_concept)}</div>` : ''}
                <div class="scene-layer">
                    <span class="layer-label">Choose visual (${sceneVisuals.length} options)</span>
                    <div class="layer-content">
                        ${sceneVisuals.length > 0 ? sceneVisuals.map((v, i) => `
                            <div class="layer-item ${scene.background && scene.background.visualIdx === i ? 'selected' : ''}" 
                                 onclick="selectSceneBackground(${scene.id}, ${i})"
                                 title="${escapeHtml(v.title || '')}">
                                <img src="${v.thumbnail || v.preview_url || v.url}" 
                                     alt="${v.title || 'Visual'}"
                                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22100%22 height=%22100%22><rect fill=%22%23222%22 width=%22100%22 height=%22100%22/><text x=%2250%25%22 y=%2250%25%22 fill=%22%23666%22 text-anchor=%22middle%22 dy=%22.3em%22>?</text></svg>'">
                            </div>
                        `).join('') : '<div class="add-layer-btn" style="opacity:0.5">No visuals found for this idea</div>'}
                    </div>
                </div>
            </div>
        </div>
    `}).join('');
}

function toggleScene(sceneId) {
    const scene = scenes.find(s => s.id === sceneId);
    if (scene) {
        scene.minimized = !scene.minimized;
        renderScenes();
    }
}

function selectSceneBackground(sceneId, visualIdx) {
    const scene = scenes.find(s => s.id === sceneId);
    if (!scene) return;
    
    // Get visuals specific to this scene
    let sceneVisuals = getSceneVisuals(scene);
    if (sceneVisuals.length === 0) {
        sceneVisuals = getVisualsArray();
    }
    
    if (sceneVisuals[visualIdx]) {
        scene.background = { ...sceneVisuals[visualIdx], visualIdx: visualIdx };
        scene.minimized = true;
        renderScenes();
    }
}

function addLayerToScene(sceneId) {
    if (extractedModels.length === 0) {
        showToast('No character models available. Extract a subject first.');
        return;
    }
    const scene = scenes.find(s => s.id === sceneId);
    if (scene) {
        scene.layers.push(extractedModels[0]);
        renderScenes();
    }
}

function removeSceneLayer(sceneId, layerIdx) {
    const scene = scenes.find(s => s.id === sceneId);
    if (scene) {
        scene.layers.splice(layerIdx, 1);
        renderScenes();
    }
}

function renderCharacterLayers(characters) {
    const grid = document.getElementById('models-grid');
    if (!characters || characters.length === 0) {
        grid.innerHTML = '<div class="no-models-msg">No characters extracted yet. Detect characters above.</div>';
        return;
    }
    
    // Check if any character already has an image generated
    grid.innerHTML = characters.map((char, idx) => {
        const modelIdx = extractedModels.findIndex(m => m.name === char.name);
        const existingModel = modelIdx >= 0 ? extractedModels[modelIdx] : null;
        const safeName = (char.name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const safePersonality = (char.personality || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        
        if (existingModel) {
            return `
                <div class="model-card character-layer" draggable="true" data-model-idx="${modelIdx}">
                    <div class="model-tab">${char.name}</div>
                    <div class="model-preview">
                        <img src="${existingModel.image}" alt="${safeName}">
                    </div>
                </div>
            `;
        } else {
            return `
                <div class="model-card character-layer character-placeholder" data-char-name="${safeName}">
                    <div class="model-tab">${char.name}</div>
                    <div class="model-preview placeholder-preview">
                        <div class="placeholder-icon">
                            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                <circle cx="12" cy="8" r="4"/>
                                <path d="M4 20c0-4 4-6 8-6s8 2 8 6"/>
                            </svg>
                        </div>
                        <button class="generate-char-btn" onclick="generateCharacterImage('${safeName}', '${safePersonality}')">
                            Generate Image
                        </button>
                    </div>
                </div>
            `;
        }
    }).join('');
}

async function generateCharacterImage(name, personality) {
    showToast('Generating character image...');
    startLoading();
    try {
        const resp = await fetch('/generate-character-image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, personality })
        });
        const data = await resp.json();
        if (data.success && data.image) {
            addExtractedModel(name, data.image);
            renderCharacterLayers(detectedCharacters);
            showToast('Character image generated!');
        } else {
            showToast(data.error || 'Failed to generate image');
        }
    } catch (err) {
        console.error(err);
        showToast('Error generating character image');
    } finally {
        stopLoading();
    }
}

function renderExtractedModels() {
    const grid = document.getElementById('models-grid');
    if (extractedModels.length === 0) {
        grid.innerHTML = '<div class="no-models-msg">No characters extracted. AI will detect subjects automatically.</div>';
        return;
    }
    
    grid.innerHTML = extractedModels.map((model, idx) => `
        <div class="model-card" draggable="true" data-model-idx="${idx}">
            <div class="model-tab">${model.name}</div>
            <div class="model-preview">
                <img src="${model.image}" alt="${model.name}">
            </div>
        </div>
    `).join('');
}

function addExtractedModel(name, imageBase64) {
    extractedModels.push({
        name: name,
        image: 'data:image/png;base64,' + imageBase64
    });
    renderExtractedModels();
}

// Legacy showToast - redirects to new toast system
// This is a compatibility wrapper for old calls that use (message, duration) format
function showToastLegacy(message, duration = 3000) {
    // Use the new toast system with info type
    if (typeof showInfo === 'function') {
        showInfo('Notice', message, duration);
    } else {
        console.log('[Toast]', message);
    }
}

// Confetti burst effect for completion
function triggerConfetti(element) {
    const rect = element.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    
    const colors = ['#ffd60a', '#ffb700', '#4ade80', '#60a5fa'];
    
    for (let i = 0; i < 12; i++) {
        const particle = document.createElement('div');
        particle.className = 'confetti-particle';
        particle.style.left = centerX + 'px';
        particle.style.top = centerY + 'px';
        particle.style.background = colors[i % colors.length];
        particle.style.opacity = '1';
        particle.style.transform = 'scale(0)';
        
        const angle = (i / 12) * 360;
        const distance = 40 + Math.random() * 30;
        const tx = Math.cos(angle * Math.PI / 180) * distance;
        const ty = Math.sin(angle * Math.PI / 180) * distance;
        const rotation = Math.random() * 360;
        
        document.body.appendChild(particle);
        
        // Animate using requestAnimationFrame for smooth travel
        requestAnimationFrame(() => {
            particle.style.transition = 'transform 500ms ease-out, opacity 500ms ease-out';
            particle.style.transform = `translate(${tx}px, ${ty}px) scale(1) rotate(${rotation}deg)`;
            
            setTimeout(() => {
                particle.style.opacity = '0';
                particle.style.transform = `translate(${tx * 1.5}px, ${ty * 1.5}px) scale(0.5) rotate(${rotation + 180}deg)`;
            }, 300);
        });
        
        setTimeout(() => particle.remove(), 600);
    }
}

// Golden shimmer effect
function triggerShimmer(element) {
    element.classList.add('completion-shimmer');
    setTimeout(() => element.classList.remove('completion-shimmer'), 1000);
}

// Pulse attention on element
function pulseAttention(element) {
    element.classList.add('pulse-attention');
}

function stopPulseAttention(element) {
    element.classList.remove('pulse-attention');
}

// Streaming text effect for AI responses
function streamText(element, text, speed = 15) {
    return new Promise(resolve => {
        element.textContent = '';
        const cursor = document.createElement('span');
        cursor.className = 'streaming-cursor';
        element.appendChild(cursor);
        
        let i = 0;
        const interval = setInterval(() => {
            if (i < text.length) {
                const char = document.createTextNode(text[i]);
                element.insertBefore(char, cursor);
                i++;
                
                // Auto-scroll if in scrollable container
                const container = element.closest('.chat-messages, #messages');
                if (container) container.scrollTop = container.scrollHeight;
            } else {
                clearInterval(interval);
                cursor.remove();
                resolve();
            }
        }, speed);
    });
}

function formatUserError(errorMsg) {
    if (!errorMsg) return 'Something went wrong. Please try again.';
    const msg = errorMsg.toLowerCase();
    if (msg.includes('api key') || msg.includes('authentication')) {
        return "We're having trouble connecting to our AI service. Please try again in a moment.";
    } else if (msg.includes('rate limit')) {
        return "Our AI is handling a lot of requests right now. Please wait a minute and try again.";
    } else if (msg.includes('timeout') || msg.includes('timed out')) {
        return "This is taking longer than expected. Please try again with a shorter script.";
    } else if (msg.includes('no visual content') || msg.includes('no scenes')) {
        return "Please add some visual content before generating your video.";
    } else if (msg.includes('no audio') || msg.includes('voiceover')) {
        return "Please generate a voiceover first before creating the video.";
    } else if (msg.includes('insufficient tokens') || msg.includes('not enough tokens')) {
        return "You don't have enough tokens for this video. Please add more tokens or upgrade your plan.";
    } else if (msg.includes('file not found') || msg.includes('no such file')) {
        return "Some files are missing. Please try regenerating your content.";
    } else if (msg.includes('ffmpeg')) {
        return "There was an issue assembling your video. Please try again.";
    } else if (msg.includes('connection') || msg.includes('network') || msg.includes('fetch')) {
        return "Connection issue. Please check your internet and try again.";
    } else if (errorMsg.length > 100) {
        return "Something went wrong. Please try again or contact support.";
    }
    return errorMsg;
}

// Copy video description to clipboard
function copyDescription() {
    const textarea = document.getElementById('video-description');
    if (textarea) {
        const citations = document.getElementById('citations-text');
        const includeCitations = document.getElementById('include-citations')?.checked;
        let text = textarea.value;
        if (includeCitations && citations) {
            text += '\n\n' + citations.textContent;
        }
        navigator.clipboard.writeText(text).then(() => {
            showToast('Description copied!');
        }).catch(() => {
            showToast('Failed to copy');
        });
    }
}

// Copy video URL to clipboard
function copyVideoUrl(videoPath) {
    const fullUrl = window.location.origin + videoPath;
    navigator.clipboard.writeText(fullUrl).then(() => {
        showToast('Video link copied!');
    }).catch(() => {
        showToast('Failed to copy link');
    });
}

// Toggle citations visibility
function toggleCitations() {
    const citationsDiv = document.getElementById('citations-text');
    const checkbox = document.getElementById('include-citations');
    if (citationsDiv && checkbox) {
        citationsDiv.style.display = checkbox.checked ? 'block' : 'none';
    }
}

// Like video feedback
function likeVideo() {
    showToast('Thanks for the feedback! This helps improve future results.');
    // Store positive feedback for AI learning
    fetch('/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'like', project_id: currentProjectId })
    }).catch(() => {});
}

// Show dislike options
function showDislikeOptions() {
    addMessage('What would you like to improve? (e.g., pacing, visuals, voice, script)', true);
}

// Loading bar helpers
let loadingProgress = 0;
let loadingInterval = null;

function startLoading() {
    document.body.classList.add('loading');
    
    const bar = document.getElementById('global-loading-bar');
    const fill = document.getElementById('global-loading-fill');
    if (!bar || !fill) return;
    
    // Reset and show
    loadingProgress = 0;
    fill.style.width = '0%';
    fill.classList.remove('pulsing');
    bar.classList.add('active');
    
    // Clear any existing interval
    if (loadingInterval) clearInterval(loadingInterval);
    
    // Animate progress from 0 to 90%
    loadingInterval = setInterval(() => {
        if (loadingProgress < 30) {
            loadingProgress += 8; // Fast start
        } else if (loadingProgress < 60) {
            loadingProgress += 4; // Medium pace
        } else if (loadingProgress < 85) {
            loadingProgress += 1; // Slow down
        } else if (loadingProgress >= 85) {
            // Start pulsing when we hit 85%
            fill.classList.add('pulsing');
            loadingProgress = Math.min(loadingProgress + 0.3, 90);
        }
        fill.style.width = loadingProgress + '%';
    }, 150);
}

function stopLoading() {
    document.body.classList.remove('loading');
    
    const bar = document.getElementById('global-loading-bar');
    const fill = document.getElementById('global-loading-fill');
    if (!bar || !fill) return;
    
    // Clear interval
    if (loadingInterval) {
        clearInterval(loadingInterval);
        loadingInterval = null;
    }
    
    // Complete to 100%
    fill.classList.remove('pulsing');
    fill.style.width = '100%';
    
    // Hide after completion animation
    setTimeout(() => {
        bar.classList.remove('active');
        fill.style.width = '0%';
        loadingProgress = 0;
    }, 300);
}

// Unified Loader System
const unifiedLoader = {
    currentStep: null,
    steps: ['script', 'visuals', 'voice', 'render'],
    
    show(options = {}) {
        const loader = document.getElementById('unified-loader');
        const title = document.getElementById('unified-loader-title');
        const status = document.getElementById('unified-loader-status');
        const ring = document.getElementById('unified-loader-ring');
        const emailNote = document.getElementById('unified-loader-email');
        const stepsContainer = document.getElementById('unified-loader-steps');
        
        if (!loader) return;
        
        title.textContent = options.title || 'Processing';
        status.textContent = options.status || 'Please wait...';
        
        // Show/hide steps based on options
        if (options.showSteps === false) {
            stepsContainer.style.display = 'none';
        } else {
            stepsContainer.style.display = 'flex';
        }
        
        // Show email note for background rendering
        if (options.backgroundRender) {
            emailNote.style.display = 'block';
        } else {
            emailNote.style.display = 'none';
        }
        
        // Set determinate or indeterminate mode
        if (options.progress !== undefined) {
            ring.classList.remove('indeterminate');
            this.setProgress(options.progress);
        } else {
            ring.classList.add('indeterminate');
        }
        
        loader.classList.add('show');
        document.body.style.overflow = 'hidden';
    },
    
    hide() {
        const loader = document.getElementById('unified-loader');
        if (loader) {
            loader.classList.remove('show');
            document.body.style.overflow = '';
            this.resetSteps();
        }
    },
    
    setTitle(text) {
        const title = document.getElementById('unified-loader-title');
        if (title) title.textContent = text;
    },
    
    setStatus(text) {
        const status = document.getElementById('unified-loader-status');
        if (status) status.textContent = text;
    },
    
    setProgress(percent) {
        const fill = document.getElementById('unified-loader-fill');
        const ring = document.getElementById('ring-progress');
        
        if (fill) fill.style.width = percent + '%';
        
        // Update ring progress (circumference = 2 * PI * 30 = 188.5)
        if (ring) {
            const offset = 188.5 - (188.5 * percent / 100);
            ring.style.strokeDashoffset = offset;
        }
    },
    
    setStep(stepName) {
        this.currentStep = stepName;
        const steps = document.querySelectorAll('.unified-loader-step');
        let foundCurrent = false;
        
        steps.forEach(step => {
            const name = step.dataset.step;
            if (name === stepName) {
                step.classList.add('active');
                step.classList.remove('completed');
                foundCurrent = true;
            } else if (!foundCurrent) {
                step.classList.remove('active');
                step.classList.add('completed');
            } else {
                step.classList.remove('active', 'completed');
            }
        });
    },
    
    resetSteps() {
        const steps = document.querySelectorAll('.unified-loader-step');
        steps.forEach(step => {
            step.classList.remove('active', 'completed');
        });
        this.currentStep = null;
        this.setProgress(0);
    }
};

// Preview narrator voice
async function previewVoice(voiceId) {
    startLoading();
    showToast('Generating voice preview...');
    try {
        const sampleText = "This is how your character will sound in the final video.";
        const response = await fetch('/generate-voiceover', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                text: sampleText, 
                voice: voiceId
            })
        });
        
        const data = await response.json();
        if (data.success && data.audio_url) {
            const audio = new Audio(data.audio_url);
            audio.play();
            showToast('Playing voice preview');
        } else {
            showToast(data.error || 'Failed to preview voice');
        }
    } catch (error) {
        console.error('Preview error:', error);
        showToast('Failed to preview voice');
    } finally {
        stopLoading();
    }
}

// Add message to chat (allowHtml for workflow buttons)
function addMessage(content, isUser = false, allowHtml = false, useStreaming = false) {
    const messages = document.getElementById('messages');
    if (!messages) return; // No chat panel in simplified UI
    const msg = document.createElement('div');
    msg.className = 'message ' + (isUser ? 'user' : 'ai');
    
    if (!isUser && useStreaming && !allowHtml) {
        // Stream AI messages for perceived speed
        msg.innerHTML = `<div class="message-content"></div>`;
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
        
        const contentEl = msg.querySelector('.message-content');
        streamText(contentEl, content, 10); // 10ms per character
    } else {
        msg.innerHTML = `<div class="message-content">${allowHtml ? content : escapeHtml(content)}</div>`;
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
    }
}

// Add AI message with streaming effect
function addStreamingMessage(content) {
    addMessage(content, false, false, true);
}

function addTypingIndicator() {
    const messages = document.getElementById('messages');
    if (!messages) return;
    const msg = document.createElement('div');
    msg.className = 'message ai';
    msg.id = 'typing';
    msg.innerHTML = `
        <div style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; background: rgba(255, 214, 10, 0.08); border-radius: 20px; border: 1px solid rgba(255, 214, 10, 0.2);">
            <div style="display: flex; gap: 4px;">
                <span style="width: 6px; height: 6px; background: var(--gold); border-radius: 50%; animation: dotPulse 1.4s ease-in-out infinite;"></span>
                <span style="width: 6px; height: 6px; background: var(--gold); border-radius: 50%; animation: dotPulse 1.4s ease-in-out 0.2s infinite;"></span>
                <span style="width: 6px; height: 6px; background: var(--gold); border-radius: 50%; animation: dotPulse 1.4s ease-in-out 0.4s infinite;"></span>
            </div>
            <span style="color: var(--gold); font-size: 13px; font-weight: 500; opacity: 0.9;">Thinking</span>
        </div>
    `;
    messages.appendChild(msg);
    messages.scrollTop = messages.scrollHeight;
}

function removeTypingIndicator() {
    const typing = document.getElementById('typing');
    if (typing) {
        typing.remove();
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Extract options from a question like "Is the goal X, or Y?"
function extractOptionsFromQuestion(question) {
    if (!question) return [];
    
    // Try to find options separated by ", or " or " or "
    const orPattern = /(.+?)(?:,\s*or\s+|\s+or\s+)(.+?)(?:\?|$)/i;
    const match = question.match(orPattern);
    
    if (match) {
        const options = [];
        // Split by commas and "or"
        const parts = question.replace(/\?$/, '').split(/,\s*or\s+|\s+or\s+|,\s+/i);
        
        for (let part of parts) {
            // Clean up the option text
            let cleaned = part.trim()
                .replace(/^(is the goal|is it|do you want|would you like|should it be)\s+/i, '')
                .replace(/\?$/, '')
                .trim();
            
            if (cleaned && cleaned.length > 2 && cleaned.length < 100) {
                // Capitalize first letter
                cleaned = cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
                options.push(cleaned);
            }
        }
        
        // Add "Something else" option if we have valid options
        if (options.length >= 2) {
            options.push('Something else...');
            return options;
        }
    }
    
    return [];
}

// Add message with clickable option buttons
function addMessageWithOptions(content, options) {
    const messages = document.getElementById('messages');
    if (!messages) return;
    
    const msg = document.createElement('div');
    msg.className = 'message ai';
    
    // Clean the question text (remove the options part for cleaner display)
    let questionText = content;
    
    // Use flex column layout to stack question and options vertically
    let html = `<div style="display: flex; flex-direction: column; gap: 16px;">`;
    html += `<div class="message-content" style="margin-bottom: 0;">${escapeHtml(questionText)}</div>`;
    
    // Add option buttons if we have any - stacked vertically for cleaner look
    if (options && options.length > 0) {
        html += `<div class="option-buttons" style="display: flex; flex-direction: column; gap: 10px; padding-left: 8px;">`;
        
        for (const option of options) {
            const isOther = option.toLowerCase().includes('something else');
            const btnStyle = isOther 
                ? 'background: transparent; border: 1px solid var(--border); color: var(--text-dim);' 
                : 'background: var(--gold); color: #000;';
            html += `<button class="btn option-btn" onclick="selectOption('${escapeHtml(option.replace(/'/g, "\\'"))}')" style="font-size: 13px; padding: 10px 18px; border-radius: 8px; text-align: left; ${btnStyle} transition: all 0.2s; cursor: pointer;">${escapeHtml(option)}</button>`;
        }
        
        html += `</div>`;
    }
    
    html += `</div>`;
    
    msg.innerHTML = html;
    messages.appendChild(msg);
    messages.scrollTop = messages.scrollHeight;
}

// Handle option button click
function selectOption(option) {
    // Remove option buttons from the message (already selected)
    const optionButtons = document.querySelectorAll('.option-buttons');
    optionButtons.forEach(btns => {
        btns.innerHTML = `<div style="display: block; margin-top: 16px; padding: 10px 14px; background: rgba(255, 214, 10, 0.15); border-left: 3px solid var(--gold); border-radius: 4px; color: var(--gold); font-size: 13px;"><span style="opacity: 0.7;">You selected:</span> <strong>${escapeHtml(option)}</strong></div>`;
    });
    
    // Handle voice casting options specially
    const optionLower = option.toLowerCase();
    if (optionLower.includes('auto-assign voices') || optionLower.includes('auto assign voices')) {
        addMessage(option, true);
        autoAssignVoices();
        return;
    } else if (optionLower.includes('let me pick') || optionLower.includes('pick voices')) {
        addMessage(option, true);
        showVoicePickerUI();
        return;
    } else if (optionLower.includes('skip to render') || optionLower.includes('skip render')) {
        addMessage(option, true);
        showRenderConfirmation();
        return;
    }
    
    // Note: clarificationCount is incremented in sendMessageWithContext, not here
    // This prevents double-counting
    
    if (option.toLowerCase().includes('something else')) {
        // Focus the input for custom answer - don't increment yet, wait for typed response
        const input = document.getElementById('composer-input');
        if (input) {
            input.focus();
            input.placeholder = 'Type your answer...';
        }
    } else {
        // Combine selected option with original context
        const combinedMessage = originalIdea 
            ? `${originalIdea}\n\nUser clarification: ${option}`
            : option;
        
        const input = document.getElementById('composer-input');
        if (input) {
            // Show just the option in chat for cleaner look
            addMessage(option, true);
            input.value = '';
            
            // Increment and reset flag, then send combined context to AI
            clarificationCount++;
            awaitingClarification = false;  // Reset after handling option selection
            sendMessageWithContext(combinedMessage, clarificationCount);
        }
    }
}

// Send message with context (for clarification flow)
async function sendMessageWithContext(fullMessage, clarifyCount) {
    const sendBtn = document.getElementById('send-btn');
    sendBtn.disabled = true;
    startLoading();
    addTypingIndicator();
    
    try {
        const response = await fetch('/unified-engine', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                input: fullMessage,
                mode: 'auto',
                clarification_count: clarifyCount,
                force_generate: clarifyCount >= MAX_CLARIFICATIONS
            })
        });
        
        const data = await response.json();
        removeTypingIndicator();
        
        if (data.mode === 'greeting') {
            addMessage(data.reply || "What's on your mind the world should get to know?", false, false, true);
        } else if (data.success && data.result) {
            handleUnifiedResponse(data.result);
            
            // Reset clarification tracking on successful script generation
            if (data.result.status === 'ready' && data.result.script?.full_script) {
                clarificationCount = 0;
                originalIdea = '';
                awaitingClarification = false;
            }
        } else {
            addMessage('Could not process. Try rephrasing your idea.', false, false, true);
        }
    } catch (error) {
        console.error('Error:', error);
        removeTypingIndicator();
        addMessage('Connection error. Please try again.', false, false, true);
    } finally {
        stopLoading();
        sendBtn.disabled = false;
    }
}

// Send message
async function sendMessage() {
    const input = document.getElementById('composer-input');
    const sendBtn = document.getElementById('send-btn');
    let message = input.value.trim();
    
    if (!message && !uploadedFile) return;
    
    // Store original idea if this is a new conversation (not a clarification response)
    if (message && !originalIdea && clarificationCount === 0) {
        originalIdea = message;
    }
    
    // Save original input for rebuild functionality
    if (message && !localStorage.getItem('lastProjectInput')) {
        localStorage.setItem('lastProjectInput', message);
    }
    
    // If no project exists yet, create one with AI-generated name/description
    if (!currentProjectId && message) {
        try {
            console.log('[Project] Generating metadata for:', message.substring(0, 50));
            
            // Generate AI name and description
            const metaRes = await fetch('/generate-project-metadata', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ idea: message })
            });
            const metaData = await metaRes.json();
            console.log('[Project] Metadata response:', metaData);
            
            const projectName = metaData.success ? metaData.name : 'Untitled';
            const projectDesc = metaData.success ? metaData.description : message.substring(0, 100);
            
            console.log('[Project] Creating project with name:', projectName);
            showSaveIndicator('saving');
            
            // Create project with AI metadata
            const createRes = await fetch('/projects', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: projectName, description: projectDesc })
            });
            const createData = await createRes.json();
            console.log('[Project] Create response:', createData);
            
            if (createData.success) {
                currentProjectId = createData.project.id;
                updateProjectNameDisplay(projectName);
                document.getElementById('back-to-projects').style.display = 'block';
                console.log('[Project] Successfully created:', projectName);
                showSaveIndicator('saved');
            }
        } catch (err) {
            console.error('[Project] Error creating project:', err);
        }
    }
    
    sendBtn.disabled = true;
    startLoading();
    
    // Handle file upload
    let fileContext = '';
    if (uploadedFile) {
        addMessage(message + (message ? '\n\n' : '') + '📎 ' + uploadedFile.name, true);
        input.value = '';
        input.style.height = 'auto';
        addTypingIndicator();
        
        try {
            const formData = new FormData();
            formData.append('file', uploadedFile);
            
            const uploadRes = await fetch('/upload', { method: 'POST', body: formData });
            const uploadData = await uploadRes.json();
            
            if (uploadData.success) {
                fileContext = `[Uploaded: ${uploadedFile.name}, path: ${uploadData.filename}]`;
                
                // Include upload mode for reskin or clipper flow
                if (uploadedFile.uploadMode === 'reskin') {
                    fileContext += `\n[MODE: RE-SKIN - Clip guides visuals, AI regenerates]`;
                    if (uploadedFile.creativeDna) {
                        const dna = uploadedFile.creativeDna;
                        fileContext += `\n[Creative DNA: ${dna.scenes?.length || 0} scenes, ${dna.total_duration?.toFixed(1) || '?'}s]`;
                        if (dna.adjustable_elements) {
                            fileContext += `\n[Adjustable: colors, angles, composition]`;
                            fileContext += `\n[Fixed: rhythm, structure, pacing]`;
                        }
                    }
                    // Get reskin options from UI
                    const topicInput = document.getElementById('reskin-topic-input');
                    
                    personalizeVideoData = {
                        creativeDna: uploadedFile.creativeDna,
                        creativeDecisions: uploadedFile.creativeDecisions,
                        filePath: uploadedFile.filePath || uploadData.filename,
                        analysis: uploadedFile.analysis,
                        mode: 'reskin',
                        useReskin: true,
                        topic: topicInput ? topicInput.value : '',
                        captionPosition: reskinCaptionPosition || 'bottom',
                        customVoiceover: reskinVoiceMode === 'custom' ? reskinCustomVoiceover : null,
                        customImages: reskinCustomImages || [],
                        brandColors: {}
                    };
                    pendingPersonalizeRender = true;
                    
                    // Reset state for next upload
                    reskinCaptionPosition = 'bottom';
                    reskinVoiceMode = 'ai';
                    reskinCustomVoiceover = null;
                    reskinCustomImages = [];
                } else if (uploadedFile.uploadMode === 'clipper') {
                    fileContext += `\n[MODE: NEXT GEN CLIPPER - Script leads, template influences]`;
                    if (uploadedFile.templateId) {
                        fileContext += `\n[Template ID: ${uploadedFile.templateId}]`;
                    }
                    if (uploadedFile.templateData) {
                        fileContext += `\n[Template: ${uploadedFile.templateData.scene_count || 0} scenes, ${uploadedFile.templateData.duration?.toFixed(1) || '?'}s]`;
                    }
                    // Get clipper options from UI
                    const topicInput = document.getElementById('reskin-topic-input');
                    
                    personalizeVideoData = {
                        templateId: uploadedFile.templateId,
                        templateData: uploadedFile.templateData,
                        filePath: uploadedFile.filePath || uploadData.filename,
                        analysis: uploadedFile.analysis,
                        mode: 'clipper',
                        useReskin: false,
                        topic: topicInput ? topicInput.value : '',
                        captionPosition: reskinCaptionPosition || 'bottom',
                        customVoiceover: reskinVoiceMode === 'custom' ? reskinCustomVoiceover : null,
                        customImages: reskinCustomImages || [],
                        brandColors: {}
                    };
                    pendingPersonalizeRender = true;
                    
                    // Reset state for next upload
                    reskinCaptionPosition = 'bottom';
                    reskinVoiceMode = 'ai';
                    reskinCustomVoiceover = null;
                    reskinCustomImages = [];
                }
                
                // Include video/image analysis that was captured during placement popup
                if (uploadedFile.analysis) {
                    const analysis = uploadedFile.analysis;
                    if (analysis.content_type === 'video') {
                        // Video analysis with transcript and frame description
                        let videoContext = `\n\n[VIDEO ANALYSIS]`;
                        if (analysis.duration) {
                            videoContext += `\nDuration: ${analysis.duration.toFixed(1)} seconds`;
                        }
                        if (analysis.frame_analysis) {
                            videoContext += `\nVisual content: ${analysis.frame_analysis}`;
                        }
                        if (analysis.transcript) {
                            videoContext += `\nAudio transcript: "${analysis.transcript.substring(0, 2000)}"`;
                        }
                        fileContext += videoContext;
                    } else if (analysis.description) {
                        // Image analysis
                        fileContext += `\n[IMAGE ANALYSIS]\nDescription: ${analysis.description}\nMood: ${analysis.mood || 'neutral'}`;
                    }
                } else if (uploadedFile.name.match(/\.(mp4|mov|webm|avi|mkv|m4v)$/i)) {
                    // Fallback: analyze video now if not already done
                    try {
                        const analyzeRes = await fetch('/analyze-video', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ file_path: uploadData.filename })
                        });
                        const analyzeData = await analyzeRes.json();
                        if (analyzeData.success && analyzeData.analysis) {
                            let videoContext = `\n\n[VIDEO ANALYSIS]`;
                            if (analyzeData.analysis.duration) {
                                videoContext += `\nDuration: ${analyzeData.analysis.duration.toFixed(1)} seconds`;
                            }
                            if (analyzeData.analysis.frame_analysis) {
                                videoContext += `\nVisual content: ${analyzeData.analysis.frame_analysis}`;
                            }
                            if (analyzeData.analysis.transcript) {
                                videoContext += `\nAudio transcript: "${analyzeData.analysis.transcript.substring(0, 2000)}"`;
                            }
                            fileContext += videoContext;
                        }
                    } catch (err) {
                        console.error('Video analysis fallback error:', err);
                    }
                }
            }
        } catch (err) {
            console.error('Upload error:', err);
        }
        
        clearFile();
    } else {
        addMessage(message, true);
        input.value = '';
        input.style.height = 'auto';
        addTypingIndicator();
    }
    
    const fullMessage = fileContext ? (message + '\n\n' + fileContext) : message;
    conversationHistory.push({ role: 'user', content: fullMessage });
    saveConversation();
    renderChatPanelMessages();
    
    // Handle flow commands - pass state context to AI for dynamic response
    const msgLower = message.toLowerCase().trim();
    const flowCommands = ['continue', 'yes', 'proceed', 'next', 'skip', 'skip visuals'];
    
    if (flowCommands.includes(msgLower)) {
        // Build context about current project state
        const projectState = {
            hasScript: !!currentScript,
            hasAnchors: currentAnchors.length > 0,
            anchorCount: currentAnchors.length,
            visualsSelected: Object.keys(sceneVisuals).length,
            currentStep: currentWorkflowStep
        };
        
        removeTypingIndicator();
        
        // Determine next action based on state
        if (!currentScript) {
            addMessage("I don't have anything to continue with yet. Share your idea or paste content to get started.", false);
        } else if (currentAnchors.length === 0 && currentScript.anchor_points) {
            addMessage("Let me break this into scenes for you...", false);
            setTimeout(() => displayAnchors(currentScript.anchor_points), 300);
        } else if (projectState.visualsSelected < projectState.anchorCount && msgLower !== 'skip' && msgLower !== 'skip visuals') {
            const missing = projectState.anchorCount - projectState.visualsSelected;
            addMessage(`You have ${missing} scene(s) without visuals. Click any scene to pick visuals, or say 'skip' to continue without them.`, false);
        } else {
            // Ready for next step - voice assignment
            currentWorkflowStep = 4;
            addMessage("Ready for voice assignment. I can suggest voices that match each scene's tone, or you can pick them yourself. What would you like?", false);
        }
        
        stopLoading();
        sendBtn.disabled = false;
        return;
    }
    
    // Handle reskin mode - user sending topic (DNA extraction happens now)
    if (pendingPersonalizeRender && personalizeVideoData && personalizeVideoData.mode === 'reskin' && currentWorkflowStep === 2 && message) {
        personalizeVideoData.topic = message;
        removeTypingIndicator();
        
        try {
            // Step 1: Extract creative DNA now (deferred until topic is provided, 180s timeout)
            if (personalizeVideoData.needsExtraction && personalizeVideoData.filePath) {
                addMessage('Analyzing your clip...', false);
                
                const dnaResponse = await fetchWithRetry('/extract-creative-dna', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: personalizeVideoData.filePath })
                }, { timeoutMs: 180000, maxRetries: 1 });
                
                const dnaData = await dnaResponse.json();
                
                if (dnaData.creative_dna) {
                    personalizeVideoData.creativeDna = dnaData.creative_dna;
                    personalizeVideoData.creativeDecisions = dnaData.creative_dna.adjustable_elements || {};
                    personalizeVideoData.needsExtraction = false;
                } else {
                    throw new Error(dnaData.error || 'Failed to analyze clip');
                }
            }
            
            // Step 2: Generate script with the topic
            const scriptResponse = await fetchWithRetry('/unified-engine', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    input: message,
                    mode: 'create',
                    context: {
                        reskin_mode: true,
                        creative_dna: personalizeVideoData.creativeDna,
                        original_transcript: personalizeVideoData.creativeDna?.transcript || ''
                    }
                })
            }, { timeoutMs: 120000, maxRetries: 1 });
            
            const scriptData = await scriptResponse.json();
            
            if (scriptData.success && scriptData.result?.script?.full_script) {
                currentScript = scriptData.result.script.full_script;
                currentProjectThesis = scriptData.result.script.thesis || message;
                
                renderScriptCardInChat(currentScript);
                addMessage('Here\'s your script. Review it and click "Confirm & Continue" to proceed to rendering.', false);
            } else {
                addMessage('Script generated. Click "Confirm & Continue" when ready.', false);
                currentScript = message;
                renderScriptCardInChat(message);
            }
        } catch (err) {
            console.error('Reskin flow error:', err);
            addMessage('Something went wrong: ' + (err.message || 'Please try again'), false);
        }
        
        stopLoading();
        sendBtn.disabled = false;
        return;
    }
    
    // Handle scene change requests dynamically
    const sceneChangeMatch = msgLower.match(/(?:change|redo|update|fix)\s+(?:scene\s+)?(\d+)/);
    if (sceneChangeMatch && currentAnchors.length > 0) {
        const sceneNum = parseInt(sceneChangeMatch[1]) - 1;
        if (sceneNum >= 0 && sceneNum < currentAnchors.length) {
            removeTypingIndicator();
            addMessage(`Opening visual picker for Scene ${sceneNum + 1}...`, false);
            // Navigate to specific scene in the single-scene picker
            currentVisualSceneIndex = sceneNum;
            const messagesDiv = document.getElementById('messages');
            const containerDiv = document.createElement('div');
            containerDiv.className = 'message ai visual-picker-message';
            containerDiv.id = 'visual-picker-container-' + Date.now();
            messagesDiv.appendChild(containerDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
            visualPickerContainer = containerDiv;
            setTimeout(() => renderSingleSceneVisualPicker(containerDiv, currentAnchors, sceneNum), 300);
            stopLoading();
            sendBtn.disabled = false;
            return;
        }
    }
    
    try {
        // Always use unified engine with auto-detection - AI determines create vs clip
        const useUnified = true;
        
        // If this is a typed clarification response (user typed after "Something else...")
        // combine with original context
        let messageToSend = fullMessage;
        if (awaitingClarification && originalIdea) {
            messageToSend = `${originalIdea}\n\nUser clarification: ${fullMessage}`;
            clarificationCount++;  // Increment only once for typed responses
            awaitingClarification = false;  // Reset flag after handling
        }
        
        if (useUnified) {
            // Use unified engine for thesis extraction and clipping - auto-detect mode
            const unifiedResponse = await fetch('/unified-engine', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    input: messageToSend,
                    mode: 'auto',
                    clarification_count: clarificationCount,
                    force_generate: clarificationCount >= MAX_CLARIFICATIONS
                })
            });
            
            const unifiedData = await unifiedResponse.json();
            removeTypingIndicator();
            
            // Handle greeting mode - conversational response
            if (unifiedData.mode === 'greeting') {
                addMessage(unifiedData.reply || "What message should the world know?");
                conversationHistory.push({ role: 'assistant', content: unifiedData.reply });
                saveConversation();
                renderChatPanelMessages();
                showUnreadBadge();
                stopLoading();
                return;
            }
            
            if (unifiedData.success && unifiedData.result) {
                handleUnifiedResponse(unifiedData.result);
                
                // Reset clarification tracking on successful script generation
                if (unifiedData.result.status === 'ready' && unifiedData.result.script?.full_script) {
                    clarificationCount = 0;
                    originalIdea = '';
                    awaitingClarification = false;
                }
                
                // Create a meaningful message based on mode
                let responseMsg = 'Processing complete.';
                if (unifiedData.result.mode === 'clip') {
                    const clipCount = unifiedData.result.result?.recommended_clips?.length || 0;
                    responseMsg = clipCount > 0 ? `Found ${clipCount} clip-worthy moments.` : 'Analyzed content for clipping.';
                } else if (unifiedData.result.mode === 'create') {
                    responseMsg = unifiedData.result.script?.full_script ? 'Script ready.' : 'Content analyzed.';
                }
                
                conversationHistory.push({ role: 'assistant', content: responseMsg });
                saveConversation();
                renderChatPanelMessages();
                showUnreadBadge();
            } else {
                addMessage('Could not process content. Try rephrasing or switching to Create mode.');
            }
        } else {
            // Standard script refinement flow
            const response = await fetch('/refine-script', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: fullMessage,
                    conversation: conversationHistory
                })
            });
            
            const data = await response.json();
            removeTypingIndicator();
            
            if (data.success) {
                const reply = data.reply || data.refined_script || '';
                if (!reply) {
                    addMessage('No response received. Please try again.');
                    return;
                }
                addMessage(reply);
                conversationHistory.push({ role: 'assistant', content: reply });
                saveConversation();
                renderChatPanelMessages();
                showUnreadBadge();
                
                // Check if script is ready
                const isScript = reply && (reply.includes('SCENE') || reply.includes('===') || reply.includes('VISUAL:'));
                if (data.script_ready || (isScript && reply.length > 300)) {
                    // Store full script for processing, but show voice actor version to user
                    currentScript = data.refined_script || reply;
                    
                    // Extract thesis from the script
                    try {
                        const thesisRes = await fetch('/extract-thesis', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ content: reply, content_type: 'script' })
                        });
                        const thesisData = await thesisRes.json();
                        if (thesisData.success) {
                            displayThesis(thesisData.thesis);
                        }
                    } catch(e) { console.log('Thesis extraction skipped'); }
                    
                    // If we have a voice actor script, display that instead
                    if (data.voice_actor_script) {
                        // Update the displayed message with clean script
                        const messages = document.querySelectorAll('#messages .message:not(.user)');
                        if (messages.length > 0) {
                            const lastMsg = messages[messages.length - 1];
                            lastMsg.innerHTML = `<div class="script-display">${escapeHtml(data.voice_actor_script).replace(/\n/g, '<br>')}</div>`;
                        }
                    }
                    
                    // Also update the document editor with the script
                    docHasScript = true;
                    displayScriptInEditor(data.refined_script || reply);
                    updateTimelineControls(true);
                    // Script card inline now handles the flow - no showAIFeedback actions needed
                    
                    // Show confirmation via chat (not modal)
                    showReviewConfirmation();
                }
            } else {
                addMessage('Something went wrong. Please try again.');
            }
        }
    } catch (error) {
        console.error('sendMessage Error:', error, error?.message, error?.stack);
        removeTypingIndicator();
        addMessage('Connection error. Please try again.');
    } finally {
        stopLoading();
    }
    
    sendBtn.disabled = false;
}

// Workflow step tracking - All transitions happen via chat
let currentWorkflowStep = 1;

// Clarification tracking - max 3 clarifications then force script generation
let clarificationCount = 0;
let originalIdea = '';  // Store the original idea to maintain context
let awaitingClarification = false;  // Flag to track if we're in clarification mode
const MAX_CLARIFICATIONS = 3;

const WORKFLOW_STEPS = {
    1: { name: 'Script Writing', guide: null, action: null },
    2: { name: 'Scene Review', guide: null, action: null },
    3: { name: 'Visual Selection', guide: null, action: null },
    4: { name: 'Voice Assignment', guide: null, action: null },
    5: { name: 'Generate Video', guide: null, action: null },
    6: { name: 'Final Preview', guide: null, action: null }
};

// Render a guide message with optional action button
function renderWorkflowGuide(step) {
    const stepData = WORKFLOW_STEPS[step];
    if (!stepData || !stepData.guide) return;
    
    let messageHtml = stepData.guide;
    
    // Add action button if defined
    if (stepData.action) {
        messageHtml += `<div style="margin-top: 12px;"><button class="workflow-action-btn" onclick="${stepData.action.fn}()">${stepData.action.label}</button></div>`;
    }
    
    addMessage(messageHtml, false, true); // false = AI message, true = allow HTML
    conversationHistory.push({ role: 'assistant', content: stepData.guide });
    saveConversation();
    renderChatPanelMessages();
}

// Show review confirmation via chat (not modal) - now proceeds to scenes
function showReviewConfirmation() {
    // Advance to step 2 - Scene Review (track state but stay in chat)
    currentWorkflowStep = 2;
    updateProjectWorkflowStep(2);
    storeWorkflowPreference('script_completed', true);
    
    // DON'T navigate away - stay in chat and render script card inline
    // Show script card inline in chat
    if (currentScript) {
        const scriptText = typeof currentScript === 'string' ? currentScript : currentScript.full_script;
        renderScriptCardInChat(scriptText);
    }
}

// Render script card inline in chat messages (edit-first flow)
function renderScriptCardInChat(scriptText) {
    if (!scriptText) return;
    
    const messagesDiv = document.getElementById('messages');
    if (!messagesDiv) return;
    
    // Create inline script card container
    const cardDiv = document.createElement('div');
    cardDiv.className = 'message ai script-card-message';
    cardDiv.id = 'inline-script-card-' + Date.now();
    
    // Calculate metadata
    const wordCount = scriptText.split(/\s+/).length;
    const duration = Math.round(wordCount / 2.5); // ~2.5 words per second
    const sceneMatches = scriptText.match(/\[SCENE|\[HOOK|\[CLAIM|\[EVIDENCE|\[PIVOT|\[COUNTER|\[CLOSER/gi);
    const sceneCount = sceneMatches ? sceneMatches.length : 1;
    
    // Extract hook (first meaningful line)
    const lines = scriptText.split('\n').filter(l => l.trim() && !l.trim().startsWith('['));
    const hook = lines[0]?.substring(0, 120) || 'Your script';
    
    // Generate unique ID for this card instance
    const cardUniqueId = 'sc-' + Date.now();
    
    cardDiv.innerHTML = `
        <div class="script-loop-container" style="display: flex; flex-direction: column; gap: 2px;">
            <!-- Script Card -->
            <div class="inline-script-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 12px 12px 4px 4px; padding: 16px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div style="font-size: 14px; font-weight: 600; color: var(--gold);">Your Script</div>
                    <div style="display: flex; gap: 12px; font-size: 12px; color: var(--text-dim);">
                        <span>${duration}s</span>
                        <span>${sceneCount} scenes</span>
                    </div>
                </div>
                <div style="font-size: 13px; color: var(--text-dim); margin-bottom: 12px; font-style: italic;">"${escapeHtml(hook)}${hook.length >= 120 ? '...' : ''}"</div>
                <div class="script-preview" id="script-preview-text-${cardUniqueId}" style="max-height: 200px; overflow-y: auto; font-size: 13px; line-height: 1.6; color: var(--text); white-space: pre-wrap; padding: 12px; background: rgba(0,0,0,0.2); border-radius: 8px;">${escapeHtml(scriptText)}</div>
            </div>
            
            <!-- Loop Score Card (separate but connected) -->
            <div class="inline-loop-score-card" id="inline-loop-score-${cardUniqueId}" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-top: none; border-radius: 0 0 4px 4px; padding: 12px 16px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <span style="font-size: 13px; color: var(--text-dim);">Loop Score</span>
                    <span id="loop-score-value-${cardUniqueId}" style="font-size: 14px; font-weight: 600; color: var(--gold);">Analyzing...</span>
                </div>
                <div id="loop-score-analysis-${cardUniqueId}" style="font-size: 12px; color: var(--text-dim); margin-top: 6px; display: none;"></div>
            </div>
            
            <!-- Action Buttons Card (separate but connected) -->
            <div class="inline-action-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-top: none; border-radius: 0 0 12px 12px; padding: 12px 16px;">
                <div class="script-card-buttons" style="display: flex; gap: 8px; flex-wrap: wrap;">
                    <button class="btn btn-secondary" onclick="editScriptInline(this)" style="font-size: 12px; padding: 8px 16px;">Edit Script</button>
                    <button class="btn btn-primary" onclick="confirmScriptAndProceed()" style="font-size: 12px; padding: 8px 16px;">Confirm & Continue</button>
                </div>
            </div>
        </div>
    `;
    
    messagesDiv.appendChild(cardDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    
    // Fetch and display Loop Score asynchronously with scoped ID
    fetchInlineLoopScore(scriptText, cardUniqueId);
}

// Fetch Loop Score and update inline card
async function fetchInlineLoopScore(scriptText, cardId) {
    const scoreEl = document.getElementById('loop-score-value-' + cardId);
    const analysisEl = document.getElementById('loop-score-analysis-' + cardId);
    
    try {
        const thesis = currentProjectThesis || scriptText.split('\n')[0];
        const response = await fetch('/validate-loop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ thesis, script: scriptText })
        });
        
        const data = await response.json();
        if (data.success && scoreEl) {
            updateInlineLoopScore(data, scoreEl, analysisEl);
        } else if (scoreEl) {
            scoreEl.textContent = 'N/A';
        }
    } catch (err) {
        console.error('Loop score error:', err);
        if (scoreEl) scoreEl.textContent = 'N/A';
    }
}

// Update the inline Loop Score display
function updateInlineLoopScore(loopData, scoreEl, analysisEl) {
    if (!scoreEl) return;
    
    const score = loopData.loop_score || 0;
    const strength = loopData.loop_strength || '';
    const analysis = loopData.analysis || '';
    
    let scoreColor = '#4ade80'; // green
    if (score < 0.4) scoreColor = '#f87171'; // red
    else if (score < 0.7) scoreColor = '#fbbf24'; // yellow
    
    scoreEl.style.color = scoreColor;
    scoreEl.textContent = `${Math.round(score * 100)}% ${strength}`;
    
    if (analysis && analysisEl) {
        analysisEl.textContent = analysis;
        analysisEl.style.display = 'block';
    }
}

// Confirm script and proceed to visuals or render (for personalized content)
function confirmScriptAndProceed() {
    currentWorkflowStep = 3;
    
    // Collapse the entire script card to a summary line
    collapseScriptSection();
    
    // Check if we're in personalize mode - show pre-render preview
    if (pendingPersonalizeRender && personalizeVideoData) {
        setTimeout(() => {
            showPreRenderPreview();
        }, 200);
    } else {
        // Standard flow - proceed to visuals
        setTimeout(() => {
            addMessage('Script confirmed! Now let\'s find visuals for each scene.', false);
            setTimeout(() => proceedToVisuals(), 300);
        }, 200);
    }
}

// Show pre-render preview with breakdown before actual render
function showPreRenderPreview() {
    const messagesDiv = document.getElementById('messages');
    if (!messagesDiv) return;
    
    // Calculate metadata from script
    const wordCount = currentScript ? currentScript.split(/\s+/).length : 0;
    const duration = Math.round(wordCount / 2.5);
    
    // Get creative DNA info if available
    const creativeDna = personalizeVideoData?.creativeDna || {};
    const sceneCount = creativeDna.scene_count || creativeDna.scenes?.length || Math.max(1, Math.round(duration / 5));
    const originalDuration = creativeDna.total_duration || creativeDna.duration || duration;
    
    // Fixed and adjustable elements
    const fixedElements = creativeDna.fixed_elements || ['rhythm', 'structure', 'pacing', 'transitions'];
    const adjustableElements = creativeDna.adjustable_elements || ['colors', 'angles', 'composition', 'visual content'];
    
    const useReskin = personalizeVideoData.useReskin !== false;
    const topic = personalizeVideoData.topic || currentProjectThesis || 'Your topic';
    
    const previewDiv = document.createElement('div');
    previewDiv.className = 'message ai pre-render-preview';
    previewDiv.id = 'pre-render-preview';
    
    previewDiv.innerHTML = `
        <div style="background: linear-gradient(135deg, rgba(10,31,20,0.9), rgba(20,40,30,0.9)); border: 1px solid var(--gold); border-radius: 16px; padding: 24px; margin: 8px 0;">
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
                <div style="width: 48px; height: 48px; background: var(--gold); border-radius: 12px; display: flex; align-items: center; justify-content: center;">
                    <svg width="24" height="24" fill="none" stroke="var(--bg)" stroke-width="2" viewBox="0 0 24 24">
                        <polygon points="5 3 19 12 5 21 5 3"></polygon>
                    </svg>
                </div>
                <div>
                    <div style="font-size: 18px; font-weight: 700; color: var(--gold);">Ready to Render</div>
                    <div style="font-size: 13px; color: var(--text-dim);">Here's what will happen</div>
                </div>
            </div>
            
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 20px;">
                <div style="background: rgba(0,0,0,0.3); border-radius: 12px; padding: 16px; text-align: center;">
                    <div style="font-size: 28px; font-weight: 700; color: var(--gold);">${sceneCount}</div>
                    <div style="font-size: 12px; color: var(--text-dim);">Scenes</div>
                </div>
                <div style="background: rgba(0,0,0,0.3); border-radius: 12px; padding: 16px; text-align: center;">
                    <div style="font-size: 28px; font-weight: 700; color: var(--gold);">${Math.round(originalDuration)}s</div>
                    <div style="font-size: 12px; color: var(--text-dim);">Duration</div>
                </div>
                <div style="background: rgba(0,0,0,0.3); border-radius: 12px; padding: 16px; text-align: center;">
                    <div style="font-size: 28px; font-weight: 700; color: var(--gold);">AI</div>
                    <div style="font-size: 12px; color: var(--text-dim);">Generated</div>
                </div>
            </div>
            
            <div style="margin-bottom: 20px;">
                <div style="font-size: 13px; font-weight: 600; color: var(--text); margin-bottom: 8px;">Topic</div>
                <div style="font-size: 14px; color: var(--text-dim); padding: 12px; background: rgba(0,0,0,0.2); border-radius: 8px;">${escapeHtml(topic.substring(0, 150))}${topic.length > 150 ? '...' : ''}</div>
            </div>
            
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px;">
                <div style="background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); border-radius: 12px; padding: 16px;">
                    <div style="font-size: 12px; font-weight: 600; color: #22c55e; margin-bottom: 8px; display: flex; align-items: center; gap: 6px;">
                        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                        Preserved from Original
                    </div>
                    <div style="font-size: 12px; color: var(--text-dim); line-height: 1.6;">
                        ${Array.isArray(fixedElements) ? fixedElements.map(e => `<div style="padding: 2px 0;">• ${e.charAt(0).toUpperCase() + e.slice(1).replace(/_/g, ' ')}</div>`).join('') : '• Rhythm<br>• Structure<br>• Pacing'}
                    </div>
                </div>
                <div style="background: rgba(255,214,10,0.1); border: 1px solid rgba(255,214,10,0.3); border-radius: 12px; padding: 16px;">
                    <div style="font-size: 12px; font-weight: 600; color: var(--gold); margin-bottom: 8px; display: flex; align-items: center; gap: 6px;">
                        <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                        AI Will Generate
                    </div>
                    <div style="font-size: 12px; color: var(--text-dim); line-height: 1.6;">
                        ${Array.isArray(adjustableElements) ? adjustableElements.map(e => `<div style="padding: 2px 0;">• ${e.charAt(0).toUpperCase() + e.slice(1).replace(/_/g, ' ')}</div>`).join('') : '• Colors<br>• Angles<br>• Visuals'}
                    </div>
                </div>
            </div>
            
            <div style="background: rgba(0,0,0,0.2); border-radius: 8px; padding: 12px; margin-bottom: 20px; font-size: 12px; color: var(--text-dim);">
                <strong style="color: var(--text);">How it works:</strong> AI generates ${sceneCount} unique visuals for your topic while preserving the original video's rhythm and editing style. No stock footage, just original AI art.
            </div>
            
            <button class="btn btn-primary" onclick="startPersonalizedRender()" style="width: 100%; padding: 16px; font-size: 16px; font-weight: 600; display: flex; align-items: center; justify-content: center; gap: 8px;">
                <svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                Render Video
            </button>
        </div>
    `;
    
    messagesDiv.appendChild(previewDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Re-skin video: extract creative DNA, generate new visuals, render
async function startPersonalizedRender() {
    if (!personalizeVideoData || !currentScript) {
        addMessage('Missing template data or script. Please try again.');
        return;
    }
    
    // Check for reskin mode vs simple overlay mode
    const useReskin = personalizeVideoData.useReskin !== false;
    
    // Show full-page render overlay
    const overlayTitle = useReskin ? 'AI Remix in Progress' : 'Video Rendering';
    showRenderOverlay(overlayTitle);
    
    // Start a new render session (cancels any previous)
    const sessionId = startRenderSession();
    const signal = renderAbortController?.signal;
    
    // Progress stages for the overlay
    const stages = [
        { stage: 'Extracting creative DNA...', substage: 'Analyzing video structure and rhythm' },
        { stage: 'Generating voiceover...', substage: 'Converting script to speech' },
        { stage: 'Creating visuals...', substage: 'AI is generating custom images' },
        { stage: 'Assembling video...', substage: 'Compositing frames and audio' },
        { stage: 'Finalizing...', substage: 'Adding finishing touches' }
    ];
    
    // Helper for progress updates (only if session still active)
    const updateProgress = (step, total, message) => {
        if (!isRenderSessionActive(sessionId)) return;
        if (isRenderCancelled()) return;
        const stageInfo = stages[step - 1] || { stage: message, substage: '' };
        updateRenderStage(stageInfo.stage, stageInfo.substage);
    };
    
    updateProgress(0, 5, 'Starting render...');
    
    try {
        // Use already-extracted creative DNA if available
        let creativeDna = personalizeVideoData.creativeDna || null;
        let audioPath = null;
        
        // Step 1: Extract DNA if needed (180s timeout - GPT-4 Vision + Whisper can take time)
        if (useReskin && !creativeDna && personalizeVideoData.filePath) {
            updateProgress(1, 5, 'Extracting creative DNA...');
            
            const dnaResponse = await fetchWithRetry('/extract-creative-dna', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: personalizeVideoData.filePath
                })
            }, { timeoutMs: 180000, maxRetries: 1, signal });
            
            const dnaData = await dnaResponse.json();
            if (!dnaData.success) {
                console.warn('DNA extraction failed, falling back to simple render');
            } else {
                creativeDna = dnaData.creative_dna;
            }
        } else {
            updateProgress(1, 5, 'Using cached creative DNA...');
        }
        
        // Step 2: Generate voiceover (120s timeout - ElevenLabs can be slow)
        updateProgress(2, 5, 'Generating voiceover...');
        
        if (personalizeVideoData.customVoiceover) {
            audioPath = personalizeVideoData.customVoiceover;
        } else {
            const voiceResponse = await fetchWithRetry('/generate-voiceover-multi', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    script: currentScript,
                    character_voices: characterVoices || {},
                    custom_prompts: customVoicePrompts || {}
                })
            }, { timeoutMs: 120000, maxRetries: 1, signal });
            
            const voiceData = await voiceResponse.json();
            if (!voiceData.success) {
                throw new Error(voiceData.error || 'Voiceover generation failed');
            }
            audioPath = voiceData.audio_path;
        }
        
        let renderData;
        
        if (useReskin && creativeDna) {
            // Step 3: AI Remix - transform source video with new style (300s timeout)
            updateProgress(3, 5, 'Transforming video with AI Remix...');
            
            // Get caption settings with position
            const captionSettings = getCaptionSettings();
            const captionPosition = personalizeVideoData.captionPosition || 'bottom';
            
            const colorGradeSelect = document.getElementById('reskin-color-grade');
            const colorGrade = colorGradeSelect ? colorGradeSelect.value : 'cinematic';
            
            const captionStyleSelect = document.getElementById('reskin-caption-style');
            const dynamicCaptionStyle = captionStyleSelect ? captionStyleSelect.value : 'bold_pop';
            
            const reskinResponse = await fetchWithRetry('/reskin-video', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    creative_dna: creativeDna,
                    topic: personalizeVideoData.topic || currentProjectThesis || '',
                    script: currentScript,
                    brand_colors: personalizeVideoData.brandColors || {},
                    custom_images: personalizeVideoData.customImages || [],
                    voiceover_path: audioPath,
                    caption_position: captionPosition,
                    caption_style: dynamicCaptionStyle,
                    captions_enabled: captionSettings.enabled !== false,
                    format: selectedFormat || '9:16',
                    color_grade: colorGrade
                })
            }, { timeoutMs: 300000, maxRetries: 1, signal });
            
            renderData = await reskinResponse.json();
            
            if (renderData.success) {
                // Step 4: AI Quality Review (30s timeout - optional, don't fail if it times out)
                updateProgress(4, 5, 'AI reviewing quality...');
                
                try {
                    const reviewResponse = await fetchWithRetry('/ai-quality-review', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            video_path: renderData.video_path,
                            topic: personalizeVideoData.topic || '',
                            script: currentScript,
                            creative_dna: creativeDna
                        })
                    }, { timeoutMs: 30000, maxRetries: 0, signal });
                    
                    const reviewData = await reviewResponse.json();
                    renderData.quality_review = reviewData;
                    renderData.quality_score = reviewData.quality_score;
                } catch (reviewErr) {
                    console.warn('Quality review failed, skipping:', reviewErr);
                    renderData.quality_score = null;
                }
                
                // Store for feedback
                renderData.creative_dna = creativeDna;
                renderData.sources_used = renderData.sources_used || [];
            }
        } else {
            // Fallback: Simple overlay render (120s timeout)
            updateProgress(3, 5, 'Assembling video...');
            
            const captionSettings = getCaptionSettings();
            
            const renderResponse = await fetchWithRetry('/render-personalized-video', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    template_path: personalizeVideoData.filePath,
                    template_id: personalizeVideoData.templateId,
                    audio_path: audioPath,
                    script: currentScript,
                    captions: captionSettings,
                    format: selectedFormat || '9:16'
                })
            }, { timeoutMs: 120000, maxRetries: 1, signal });
            
            renderData = await renderResponse.json();
        }
        
        // Step 5: Complete
        updateProgress(5, 5, 'Finalizing...');
        
        // Verify this session is still active before updating UI
        if (!isRenderSessionActive(sessionId)) {
            console.log('Render session was superseded, ignoring results');
            return;
        }
        
        if (renderData.success) {
            // Show video with quality info if available
            renderVideoResultInChat(renderData);
            
            const qualityMsg = renderData.quality_score 
                ? ` (Quality: ${Math.round(renderData.quality_score * 100)}%)`
                : '';
            showSuccess('Video Ready', `Your AI Remix is ready!${qualityMsg}`);
            
            // Show feedback buttons for learning
            if (useReskin) {
                showReskinFeedbackUI(renderData);
            }
            
            // Clear personalize state
            personalizeVideoData = null;
            pendingPersonalizeRender = false;
            
            refreshTokenBalance();
        } else {
            throw new Error(renderData.error || 'Render failed');
        }
        
    } catch (err) {
        console.error('Personalized render error:', err);
        // Only show error if this session is still active (not superseded)
        if (isRenderSessionActive(sessionId)) {
            // Don't show error for user cancellations
            if (err.message !== 'Request cancelled' && !isRenderCancelled()) {
                addMessage(`Render failed: ${err.message}`);
                showError('Render Failed', err.message);
            }
        }
    } finally {
        // Always hide overlay
        hideRenderOverlay();
        // Only clean up if this is still the active session
        if (isRenderSessionActive(sessionId)) {
            renderAbortController = null;
            currentRenderSessionId = null;
        }
    }
}

// Show feedback UI for re-skinned videos to power global learning
function showReskinFeedbackUI(renderData) {
    const feedbackHtml = `
        <div class="reskin-feedback-card" style="background: var(--card-bg); border: 1px solid var(--gold-muted); border-radius: 12px; padding: 16px; margin-top: 12px;">
            <div style="font-size: 14px; color: var(--text-secondary); margin-bottom: 12px;">
                Help the AI learn: Did this match what you wanted?
            </div>
            <div style="display: flex; gap: 12px;">
                <button onclick="submitReskinFeedback(true, this)" style="flex: 1; padding: 10px; background: var(--success-bg, #1a4d1a); border: 1px solid var(--success, #4ade80); border-radius: 8px; color: var(--success, #4ade80); cursor: pointer; font-weight: 600;">
                    👍 Yes, looks great
                </button>
                <button onclick="submitReskinFeedback(false, this)" style="flex: 1; padding: 10px; background: var(--error-bg, #4d1a1a); border: 1px solid var(--error, #f87171); border-radius: 8px; color: var(--error, #f87171); cursor: pointer; font-weight: 600;">
                    👎 Needs work
                </button>
            </div>
        </div>
    `;
    
    // Store render data for feedback submission
    window.lastReskinData = renderData;
    addMessage(feedbackHtml, false, true);
}

// Submit feedback for global learning
async function submitReskinFeedback(liked, buttonEl) {
    const data = window.lastReskinData;
    if (!data) return;
    
    // Disable buttons
    const card = buttonEl.closest('.reskin-feedback-card');
    card.querySelectorAll('button').forEach(b => b.disabled = true);
    
    try {
        await fetch('/reskin-feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                liked: liked,
                video_path: data.video_path,
                topic: data.topic || currentProjectThesis || '',
                visual_sources: data.sources_used || [],
                creative_dna: data.creative_dna || {},
                quality_scores: data.quality_review || {}
            })
        });
        
        card.innerHTML = `
            <div style="text-align: center; color: var(--gold); padding: 8px;">
                ✓ Thanks! Your feedback helps the AI improve for everyone.
            </div>
        `;
    } catch (err) {
        console.error('Feedback error:', err);
    }
}

// Collapse script card to single summary line
function collapseScriptSection() {
    const scriptCard = document.querySelector('.inline-script-card');
    if (!scriptCard) return;
    
    const parentMsg = scriptCard.closest('.message');
    if (!parentMsg) return;
    
    // Get script metadata
    const preview = scriptCard.querySelector('.script-preview, .script-edit-textarea');
    const scriptText = preview ? preview.textContent || preview.value : '';
    const wordCount = scriptText.split(/\s+/).length;
    const duration = Math.round(wordCount / 2.5);
    const sceneMatches = scriptText.match(/\[SCENE|\[HOOK|\[CLAIM|\[EVIDENCE|\[PIVOT|\[COUNTER|\[CLOSER/gi);
    const sceneCount = sceneMatches ? sceneMatches.length : 1;
    
    // Store original content for expansion
    const originalContent = scriptCard.outerHTML;
    parentMsg.dataset.originalContent = originalContent;
    parentMsg.dataset.scriptText = scriptText;
    
    // Replace with collapsed summary
    parentMsg.innerHTML = `
        <div class="collapsed-section" onclick="toggleScriptExpand(this)">
            <span class="check-icon">✓</span>
            <span class="summary-text">Script confirmed (${duration}s, ${sceneCount} scenes)</span>
            <span class="expand-arrow">▼</span>
        </div>
        <div class="collapsible-content">
            ${originalContent}
        </div>
    `;
    
    // Remove buttons from collapsed content
    const collapsedButtons = parentMsg.querySelector('.collapsible-content .script-card-buttons');
    if (collapsedButtons) {
        collapsedButtons.innerHTML = '<span style="color: var(--gold); font-size: 12px;">✓ Confirmed</span>';
    }
}

// Toggle script section expand/collapse
function toggleScriptExpand(header) {
    header.classList.toggle('expanded');
    const content = header.nextElementSibling;
    if (content) {
        content.classList.toggle('expanded');
    }
}

// Edit script inline
function editScriptInline(btn) {
    const card = btn.closest('.inline-script-card');
    const preview = card.querySelector('.script-preview');
    if (!preview) return;
    
    const currentText = preview.textContent;
    
    // Replace preview with textarea
    const textarea = document.createElement('textarea');
    textarea.className = 'script-edit-textarea';
    textarea.style.cssText = 'width: 100%; min-height: 200px; font-size: 13px; line-height: 1.6; color: var(--text); background: rgba(0,0,0,0.3); border: 1px solid var(--gold); border-radius: 8px; padding: 12px; resize: vertical; font-family: inherit;';
    textarea.value = currentText;
    
    preview.replaceWith(textarea);
    textarea.focus();
    
    // Change button to Save
    btn.textContent = 'Save Changes';
    btn.onclick = function() {
        const newText = textarea.value.trim();
        if (newText) {
            // Update current script
            if (typeof currentScript === 'object') {
                currentScript.full_script = newText;
            } else {
                currentScript = newText;
            }
            
            // Replace textarea with preview
            const newPreview = document.createElement('div');
            newPreview.className = 'script-preview';
            newPreview.style.cssText = 'max-height: 200px; overflow-y: auto; font-size: 13px; line-height: 1.6; color: var(--text); white-space: pre-wrap; padding: 12px; background: rgba(0,0,0,0.2); border-radius: 8px; margin-bottom: 12px;';
            newPreview.textContent = newText;
            textarea.replaceWith(newPreview);
            
            // Reset button
            btn.textContent = 'Edit Script';
            btn.onclick = function() { editScriptInline(btn); };
            
            addMessage('Script updated! Ready to continue.', false);
        }
    };
}

// Proceed to visuals from inline card
function proceedToVisuals() {
    // Display visual layers if we have them stored
    if (currentVisualPlan) {
        displayVisualLayers(currentVisualPlan);
    }
    
    if (!currentAnchors || currentAnchors.length === 0) {
        // Need to extract anchors first
        if (currentScript) {
            const scriptText = typeof currentScript === 'string' ? currentScript : currentScript.full_script;
            addMessage('Breaking your script into scenes...', false);
            
            // Call backend to extract anchors
            fetch('/extract-anchors', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ script: scriptText })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success && data.anchors) {
                    displayAnchors(data.anchors);
                } else {
                    addMessage('Could not break script into scenes. Try editing your script first.', false);
                }
            })
            .catch(err => {
                console.error('Anchor extraction error:', err);
                addMessage('Error extracting scenes. Please try again.', false);
            });
        } else {
            addMessage('No script available. Share your idea first.', false);
        }
    } else {
        // Already have anchors, show visual picker inline
        displayAnchors(currentAnchors);
    }
}

// Proceed to voices from inline card
function proceedToVoices() {
    currentWorkflowStep = 4;
    showVoiceCastingOptions();
}

// Auto-assign voices to characters
async function autoAssignVoices() {
    currentWorkflowStep = 5;
    addTypingIndicator();
    
    try {
        const response = await fetch('/auto-assign-voices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: currentProjectId,
                script: currentScript
            })
        });
        
        removeTypingIndicator();
        const data = await response.json();
        
        if (data.success) {
            currentVoiceAssignments = data.voice_assignments || {};
            addMessage(`Voices auto-assigned! ${Object.keys(currentVoiceAssignments).length} character(s) ready.`);
            
            // Show voice assignments summary
            let summary = 'Voice casting:\n';
            for (const [char, voice] of Object.entries(currentVoiceAssignments)) {
                summary += `• ${char}: ${voice}\n`;
            }
            addMessage(summary.trim());
            
            // Show render confirmation with cost estimate
            setTimeout(() => showRenderConfirmation(), 500);
        } else {
            addMessage(data.error || 'Failed to auto-assign voices. Try picking manually.');
            showVoicePickerUI();
        }
    } catch (err) {
        removeTypingIndicator();
        console.error('Auto-assign voices error:', err);
        addMessage('Error assigning voices. Try picking manually.');
        showVoicePickerUI();
    }
}

// Voice preview audio element
let voicePreviewAudio = null;
let currentlyPreviewingVoice = null;

// ElevenLabs voice IDs (common voices)
const elevenLabsVoices = {
    'Adam': { id: 'pNInz6obpgDQGcFmaJgB', gender: 'male', description: 'Deep, authoritative' },
    'Antoni': { id: 'ErXwobaYiN019PkySvjV', gender: 'male', description: 'Warm, conversational' },
    'Arnold': { id: 'VR6AewLTigWG4xSOukaG', gender: 'male', description: 'Strong, confident' },
    'Bella': { id: 'EXAVITQu4vr4xnSDxMaL', gender: 'female', description: 'Soft, expressive' },
    'Domi': { id: 'AZnzlk1XvdvUeBnXmlld', gender: 'female', description: 'Strong, clear' },
    'Elli': { id: 'MF3mGyEYCl7XYWbV9V6O', gender: 'female', description: 'Emotional, young' },
    'Josh': { id: 'TxGEqnHWrfWFTfGW9XjX', gender: 'male', description: 'Deep, dramatic' },
    'Rachel': { id: '21m00Tcm4TlvDq8ikWAM', gender: 'female', description: 'Calm, professional' },
    'Sam': { id: 'yoZ06aMxZJJ28mfd3POQ', gender: 'male', description: 'Energetic, youthful' }
};

// Preview voice sample
async function previewVoiceSample(voiceName, btn) {
    // Stop any currently playing preview
    if (voicePreviewAudio) {
        voicePreviewAudio.pause();
        voicePreviewAudio = null;
    }
    
    // Reset all preview buttons
    document.querySelectorAll('.voice-preview-btn').forEach(b => {
        b.innerHTML = '▶';
        b.classList.remove('playing');
    });
    
    // If clicking same voice, just stop
    if (currentlyPreviewingVoice === voiceName) {
        currentlyPreviewingVoice = null;
        return;
    }
    
    currentlyPreviewingVoice = voiceName;
    btn.innerHTML = '⏹';
    btn.classList.add('playing');
    
    // Unique sample text for each voice to showcase its character
    const voiceSamples = {
        'Adam': "The facts speak for themselves. Let me walk you through what really happened.",
        'Antoni': "Here's what you need to understand about this situation.",
        'Arnold': "Listen closely. This is important.",
        'Bella': "I want to share something with you that changed my perspective.",
        'Domi': "You won't believe what I discovered. It's absolutely fascinating!",
        'Elli': "Let me tell you a story that might surprise you.",
        'Josh': "So here's the thing nobody's talking about.",
        'Rachel': "The evidence points to one clear conclusion.",
        'Sam': "Breaking this down step by step, the answer becomes obvious.",
        'The Analyst': "Looking at the data, we can draw several conclusions.",
        'The Narrator': "In a world of noise, clarity cuts through.",
        'The Storyteller': "Once upon a time, there was an idea that changed everything.",
        'The Teacher': "Let me explain this in a way that makes sense.",
        'The Critic': "Here's where most people get it wrong.",
        'The Advocate': "We need to talk about what really matters here.",
        'The Philosopher': "Consider for a moment what this truly means.",
        'The Journalist': "The story behind this is more complex than you think."
    };
    
    const sampleText = voiceSamples[voiceName] || `This is ${voiceName}. Clear, confident, ready to tell your story.`;
    
    try {
        const response = await fetch('/preview-voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                voice_name: voiceName,
                text: sampleText
            })
        });
        
        if (response.ok) {
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            voicePreviewAudio = new Audio(url);
            voicePreviewAudio.onended = () => {
                btn.innerHTML = '▶';
                btn.classList.remove('playing');
                currentlyPreviewingVoice = null;
            };
            voicePreviewAudio.play();
        } else {
            btn.innerHTML = '▶';
            currentlyPreviewingVoice = null;
        }
    } catch (err) {
        console.error('Voice preview error:', err);
        btn.innerHTML = '▶';
        currentlyPreviewingVoice = null;
    }
}

// Show voice picker UI inline in chat
function showVoicePickerUI() {
    currentWorkflowStep = 5;
    
    // Get characters from current script
    const characters = [];
    if (currentScript && currentScript.anchors) {
        for (const anchor of currentScript.anchors) {
            const char = anchor.character || 'Narrator';
            if (!characters.includes(char)) {
                characters.push(char);
            }
        }
    }
    
    if (characters.length === 0) {
        characters.push('Narrator');
    }
    
    // Build voice picker card with previews
    const voiceNames = Object.keys(elevenLabsVoices);
    
    let html = `<div class="voice-picker-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin: 8px 0;">`;
    html += `<div style="font-size: 14px; font-weight: 600; color: var(--gold); margin-bottom: 16px;">Cast Your Voices</div>`;
    
    // Voice preview section
    html += `<div style="margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--border);">`;
    html += `<div style="font-size: 12px; color: var(--text-dim); margin-bottom: 10px;">Preview voices:</div>`;
    html += `<div style="display: flex; flex-wrap: wrap; gap: 8px;">`;
    for (const voice of voiceNames) {
        const info = elevenLabsVoices[voice];
        const genderIcon = info.gender === 'male' ? '♂' : '♀';
        html += `<div style="display: flex; align-items: center; gap: 4px; background: rgba(255,255,255,0.05); padding: 6px 10px; border-radius: 6px; font-size: 12px;">`;
        html += `<button class="voice-preview-btn" onclick="previewVoiceSample('${voice}', this)" style="background: none; border: none; color: var(--gold); cursor: pointer; padding: 0; font-size: 14px; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center;">▶</button>`;
        html += `<span style="color: var(--text);">${voice}</span>`;
        html += `<span style="color: var(--text-dim); font-size: 10px;">${genderIcon}</span>`;
        html += `</div>`;
    }
    html += `</div></div>`;
    
    // Character assignment section
    for (const char of characters) {
        html += `<div style="margin-bottom: 12px;">`;
        html += `<label style="display: block; font-size: 13px; color: var(--text-dim); margin-bottom: 4px;">${char}</label>`;
        html += `<select onchange="updateCharacterVoice('${char}', this.value)" style="width: 100%; padding: 8px 12px; background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px;">`;
        for (const voice of voiceNames) {
            const info = elevenLabsVoices[voice];
            html += `<option value="${voice}">${voice} - ${info.description}</option>`;
        }
        html += `</select>`;
        html += `</div>`;
    }
    
    html += `<button onclick="confirmVoiceCasting()" class="btn btn-primary" style="width: 100%; margin-top: 8px; font-size: 13px; padding: 10px;">Confirm & Render</button>`;
    html += `</div>`;
    
    const messagesDiv = document.getElementById('messages');
    if (messagesDiv) {
        const cardDiv = document.createElement('div');
        cardDiv.className = 'message ai';
        cardDiv.innerHTML = html;
        messagesDiv.appendChild(cardDiv);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
    
    // Initialize voice assignments
    currentVoiceAssignments = {};
    for (const char of characters) {
        currentVoiceAssignments[char] = voiceNames[0];
    }
}

// Confirm voice casting and show caption options
function confirmVoiceCasting() {
    addMessage('Voices confirmed! Now customize your captions:', true);
    showCaptionOptionsCard();
}

// Show caption options inline before rendering
function showCaptionOptionsCard() {
    const messagesDiv = document.getElementById('messages');
    if (!messagesDiv) return;
    
    const cardDiv = document.createElement('div');
    cardDiv.className = 'message ai';
    cardDiv.innerHTML = `
        <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 12px; padding: 16px;">
            <div style="font-size: 14px; font-weight: 600; color: var(--gold); margin-bottom: 12px;">Caption Settings</div>
            
            <div style="margin-bottom: 16px;">
                <label style="display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 6px;">Caption Position</label>
                <div style="display: flex; gap: 8px;">
                    <button onclick="setCaptionPosition('top', this)" class="btn btn-secondary caption-pos-btn" style="flex: 1; font-size: 12px; padding: 8px;">Top</button>
                    <button onclick="setCaptionPosition('center', this)" class="btn btn-secondary caption-pos-btn active" style="flex: 1; font-size: 12px; padding: 8px; background: var(--gold); color: #000;">Center</button>
                    <button onclick="setCaptionPosition('bottom', this)" class="btn btn-secondary caption-pos-btn" style="flex: 1; font-size: 12px; padding: 8px;">Bottom</button>
                </div>
            </div>
            
            <div style="margin-bottom: 16px;">
                <label style="display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 6px;">Animation Style</label>
                <select id="inline-caption-animation" onchange="setCaptionAnimation(this.value)" style="width: 100%; padding: 10px 12px; background: rgba(0,0,0,0.3); border: 1px solid var(--border); border-radius: 8px; color: var(--text); font-size: 13px;">
                    <option value="highlight" selected>Word Highlight</option>
                    <option value="fade">Fade In</option>
                    <option value="bounce">Bounce</option>
                    <option value="typewriter">Typewriter</option>
                    <option value="none">No Animation</option>
                </select>
            </div>
            
            <div style="margin-bottom: 16px;">
                <label style="display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 6px;">Font Style</label>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;">
                    <button onclick="setCaptionFontInline('Bebas Neue', this)" class="btn btn-secondary font-btn active" style="font-family: 'Bebas Neue'; font-size: 14px; padding: 8px; background: var(--gold); color: #000;">BEBAS</button>
                    <button onclick="setCaptionFontInline('Montserrat', this)" class="btn btn-secondary font-btn" style="font-family: 'Montserrat'; font-size: 11px; font-weight: 900; padding: 8px;">MONTSERRAT</button>
                    <button onclick="setCaptionFontInline('Anton', this)" class="btn btn-secondary font-btn" style="font-family: 'Anton'; font-size: 14px; padding: 8px;">ANTON</button>
                    <button onclick="setCaptionFontInline('Oswald', this)" class="btn btn-secondary font-btn" style="font-family: 'Oswald'; font-size: 12px; font-weight: 700; padding: 8px;">OSWALD</button>
                    <button onclick="setCaptionFontInline('Poppins', this)" class="btn btn-secondary font-btn" style="font-family: 'Poppins'; font-size: 11px; font-weight: 800; padding: 8px;">POPPINS</button>
                    <button onclick="setCaptionFontInline('Bangers', this)" class="btn btn-secondary font-btn" style="font-family: 'Bangers'; font-size: 14px; padding: 8px;">BANGERS</button>
                </div>
            </div>
            
            <div style="margin-bottom: 16px;">
                <label style="display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 6px;">Highlight Color</label>
                <div style="display: flex; gap: 8px;">
                    <button onclick="setHighlightColor('#FFD60A', this)" class="btn color-btn active" style="width: 32px; height: 32px; border-radius: 50%; background: #FFD60A; border: 2px solid #FFD60A;"></button>
                    <button onclick="setHighlightColor('#FF6B6B', this)" class="btn color-btn" style="width: 32px; height: 32px; border-radius: 50%; background: #FF6B6B; border: 2px solid transparent;"></button>
                    <button onclick="setHighlightColor('#4ECDC4', this)" class="btn color-btn" style="width: 32px; height: 32px; border-radius: 50%; background: #4ECDC4; border: 2px solid transparent;"></button>
                    <button onclick="setHighlightColor('#A855F7', this)" class="btn color-btn" style="width: 32px; height: 32px; border-radius: 50%; background: #A855F7; border: 2px solid transparent;"></button>
                    <button onclick="setHighlightColor('#FFFFFF', this)" class="btn color-btn" style="width: 32px; height: 32px; border-radius: 50%; background: #FFFFFF; border: 2px solid transparent;"></button>
                </div>
            </div>
            
            <button onclick="confirmCaptionsAndRender()" class="btn btn-primary" style="width: 100%; margin-top: 8px; font-size: 14px; padding: 12px;">Confirm & Render Video</button>
        </div>
    `;
    messagesDiv.appendChild(cardDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Caption option setters for inline card
function setCaptionPosition(position, btn) {
    selectedCaptionPosition = position;
    document.querySelectorAll('.caption-pos-btn').forEach(b => {
        b.style.background = '';
        b.style.color = '';
    });
    btn.style.background = 'var(--gold)';
    btn.style.color = '#000';
}

function setCaptionAnimation(animation) {
    selectedCaptionAnimation = animation;
}

function setCaptionFontInline(font, btn) {
    selectedCaptionFont = `'${font}', sans-serif`;
    document.querySelectorAll('.font-btn').forEach(b => {
        b.style.background = '';
        b.style.color = '';
        b.classList.remove('active');
    });
    btn.style.background = 'var(--gold)';
    btn.style.color = '#000';
    btn.classList.add('active');
}

function setHighlightColor(color, btn) {
    selectedHighlightColor = color;
    document.querySelectorAll('.color-btn').forEach(b => {
        b.style.borderColor = 'transparent';
    });
    btn.style.borderColor = color;
}

function confirmCaptionsAndRender() {
    addMessage('Captions configured!', true);
    showRenderConfirmation();
}

// Show token cost estimate before rendering
function showRenderConfirmation() {
    // Calculate token cost
    const anchors = currentAnchors || (currentScript?.anchors || []);
    const characterCount = new Set(anchors.map(a => a.character || 'Narrator')).size;
    const sfxCount = anchors.filter(a => (a.text || '').includes('[SOUND:')).length;
    const baseCost = 25;
    const charCost = characterCount * 3;
    const sfxCostVal = sfxCount * 1;
    const totalCost = baseCost + charCost + sfxCostVal;
    const dollarCost = (totalCost * 0.04).toFixed(2);
    
    const confirmHtml = `
        <div class="render-confirm-card" style="background: rgba(255,214,10,0.05); border: 1px solid rgba(255,214,10,0.3); border-radius: 12px; padding: 16px; margin: 8px 0;">
            <div style="font-size: 14px; font-weight: 600; color: var(--gold); margin-bottom: 12px;">Ready to render your video</div>
            <div style="display: flex; gap: 16px; margin-bottom: 12px; font-size: 13px; color: var(--text-dim);">
                <div>✦ <strong style="color: var(--gold);">${totalCost}</strong> tokens</div>
                <div>≈ <strong>$${dollarCost}</strong></div>
            </div>
            <div style="font-size: 11px; color: var(--text-dim); margin-bottom: 14px;">
                Base: ${baseCost} + Characters: ${charCost} + SFX: ${sfxCostVal}
            </div>
            <div style="display: flex; gap: 10px;">
                <button onclick="proceedToRender(); this.closest('.render-confirm-card').remove();" style="flex: 1; padding: 10px 16px; background: var(--gold); color: var(--bg); border: none; border-radius: 8px; font-weight: 600; cursor: pointer; transition: transform 0.15s;">
                    Render Video
                </button>
                <button onclick="this.closest('.message').remove();" style="padding: 10px 16px; background: transparent; color: var(--text-dim); border: 1px solid var(--border); border-radius: 8px; cursor: pointer;">
                    Cancel
                </button>
            </div>
        </div>
    `;
    addMessage(confirmHtml, false, true);
}

// Build scenes array from sceneVisuals and anchors
function buildScenesForRender() {
    const scenes = [];
    const anchors = currentAnchors || (currentScript?.anchors || []);
    
    for (let i = 0; i < anchors.length; i++) {
        const anchor = anchors[i];
        const visual = sceneVisuals[i] || {};
        const direction = sceneDirections[i] || 'static';
        
        scenes.push({
            index: i,
            text: anchor.text || anchor.content || '',
            type: anchor.type || 'CLAIM',
            character: anchor.character || 'Narrator',
            visual_url: visual.url || visual.popupUrl || null,
            image_url: visual.url || visual.thumbnail || visual.popupUrl || null,
            thumbnail: visual.thumbnail || visual.url || null,
            direction: direction,
            motion: visual.motion || 'static',
            duration: anchor.duration || 5
        });
    }
    
    return scenes;
}

// Proceed to render
async function proceedToRender() {
    currentWorkflowStep = 6;
    
    // Add rendering progress card
    const renderingHtml = `
        <div class="rendering-progress-card" id="rendering-progress" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin: 8px 0;">
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px;">
                <div class="render-spinner" style="width: 24px; height: 24px; border: 3px solid rgba(255,214,10,0.2); border-top-color: var(--gold); border-radius: 50%; animation: spin 1s linear infinite;"></div>
                <span style="font-size: 14px; font-weight: 600; color: var(--gold);">Rendering your video...</span>
            </div>
            <div style="background: rgba(255,255,255,0.1); border-radius: 8px; height: 8px; overflow: hidden; margin-bottom: 8px;">
                <div id="render-progress-bar" style="height: 100%; width: 0%; background: linear-gradient(90deg, var(--gold), #ffed4a); border-radius: 8px; transition: width 0.3s ease;"></div>
            </div>
            <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-dim);">
                <span id="render-status-text">Preparing clips...</span>
                <span id="render-progress-pct">0%</span>
            </div>
        </div>
    `;
    addMessage(renderingHtml, false, true);
    
    // Start progress animation
    let progress = 0;
    const progressSteps = [
        { pct: 10, text: 'Downloading visuals...' },
        { pct: 25, text: 'Processing clips...' },
        { pct: 45, text: 'Adding transitions...' },
        { pct: 60, text: 'Mixing audio...' },
        { pct: 75, text: 'Generating captions...' },
        { pct: 90, text: 'Finalizing video...' }
    ];
    let stepIndex = 0;
    
    const progressInterval = setInterval(() => {
        if (stepIndex < progressSteps.length) {
            const step = progressSteps[stepIndex];
            const bar = document.getElementById('render-progress-bar');
            const text = document.getElementById('render-status-text');
            const pct = document.getElementById('render-progress-pct');
            if (bar) bar.style.width = step.pct + '%';
            if (text) text.textContent = step.text;
            if (pct) pct.textContent = step.pct + '%';
            stepIndex++;
        }
    }, 2500);
    
    // Build scenes from visuals and anchors
    const scenes = buildScenesForRender();
    
    if (scenes.length === 0) {
        removeTypingIndicator();
        addMessage('No scenes to render. Please go back and configure visuals first.');
        return;
    }
    
    // Build script text for captions
    const scriptText = scenes.map(s => s.text).join('\n');
    
    try {
        const response = await fetch('/render-video', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: currentProjectId,
                scenes: scenes,
                script: scriptText,
                voice_assignments: currentVoiceAssignments || {},
                format: '9:16',
                captions: { enabled: true }
            })
        });
        
        clearInterval(progressInterval);
        const data = await response.json();
        
        // Update progress to 100%
        const bar = document.getElementById('render-progress-bar');
        const text = document.getElementById('render-status-text');
        const pct = document.getElementById('render-progress-pct');
        if (bar) bar.style.width = '100%';
        if (text) text.textContent = data.success ? 'Complete!' : 'Failed';
        if (pct) pct.textContent = '100%';
        
        // Remove progress card after brief delay
        setTimeout(() => {
            const progressCard = document.getElementById('rendering-progress');
            if (progressCard) progressCard.parentElement?.remove();
        }, 500);
        
        if (data.success) {
            renderVideoResultInChat(data);
        } else {
            addMessage(data.error || 'Failed to render video. Please try again.');
        }
    } catch (err) {
        clearInterval(progressInterval);
        console.error('Render error:', err);
        
        // Update progress to show error
        const text = document.getElementById('render-status-text');
        if (text) text.textContent = 'Error occurred';
        
        showError('Render Failed', 'Error rendering video. Please try again.');
        addMessage('Error rendering video. Please try again.');
    }
}

// Track current preview data for revision flow
let currentPreviewData = null;
// Note: selectedCaptionPosition is already declared in caption settings section

// Render video result inline in chat - now shows PREVIEW with watermark
function renderVideoResultInChat(renderData, isPreview = true) {
    const messagesDiv = document.getElementById('messages');
    if (!messagesDiv) return;
    
    const videoUrl = renderData.video_url || renderData.url;
    if (!videoUrl) {
        addMessage('Video rendered but no URL available.', false);
        return;
    }
    
    // Store preview data for potential revisions
    currentPreviewData = { ...renderData, videoUrl };
    
    // Store template ID if available for element editing
    if (renderData.template_id) {
        currentTemplateId = renderData.template_id;
    }
    
    const cardDiv = document.createElement('div');
    cardDiv.className = 'message ai video-result-message';
    cardDiv.id = 'video-result-' + Date.now();
    
    const trendSources = renderData.trend_sources || [];
    const hasSources = trendSources.length > 0;
    
    // Different UI for preview vs final
    const headerText = isPreview ? 'Preview Ready' : 'Your Video is Ready!';
    const headerSubtext = isPreview ? 'Review your video below. Happy with it? Download or get the final version.' : '';
    
    cardDiv.innerHTML = `
        <div class="inline-video-card" id="video-completion-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin: 8px 0;">
            <div class="video-ready-header" style="font-size: 14px; font-weight: 600; color: var(--gold); margin-bottom: 4px;">${headerText}</div>
            ${isPreview ? `<div style="font-size: 12px; color: var(--text-dim); margin-bottom: 12px;">${headerSubtext}</div>` : ''}
            
            <!-- Video with bouncing watermark -->
            <div class="video-preview-container" id="preview-container">
                <video controls style="width: 100%; max-height: 400px; border-radius: 8px; background: #000;">
                    <source src="${videoUrl}" type="video/mp4">
                    Your browser does not support video playback.
                </video>
                ${isPreview ? `<div class="bouncing-watermark">PREVIEW - Framd</div>` : ''}
            </div>
            
            <!-- Caption Position Selector -->
            <div class="caption-position-selector" id="caption-position-ui">
                <label>Captions:</label>
                <button class="caption-pos-btn ${selectedCaptionPosition === 'top' ? 'active' : ''}" onclick="setCaptionPosition('top', this)">Top</button>
                <button class="caption-pos-btn ${selectedCaptionPosition === 'middle' ? 'active' : ''}" onclick="setCaptionPosition('middle', this)">Middle</button>
                <button class="caption-pos-btn ${selectedCaptionPosition === 'bottom' ? 'active' : ''}" onclick="setCaptionPosition('bottom', this)">Bottom</button>
                <button class="caption-pos-btn ${selectedCaptionPosition === 'none' ? 'active' : ''}" onclick="setCaptionPosition('none', this)">None</button>
            </div>
            
            <!-- Preview Actions -->
            <div class="preview-actions">
                <button class="btn btn-accept" onclick="acceptVideo('${videoUrl}')" style="font-size: 12px; padding: 10px 20px;">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right: 6px; vertical-align: middle;"><polyline points="20 6 9 17 4 12"/></svg>
                    Download
                </button>
                <button class="btn btn-final" onclick="getFinalVideo('${videoUrl}')" style="font-size: 12px; padding: 10px 20px;">
                    Get Final Video
                </button>
                <button class="btn-edit-elements" id="edit-elements-btn" onclick="toggleElementEditMode()">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                    Edit Elements
                </button>
                <button class="btn btn-revision" onclick="showRevisionPanel()" style="font-size: 12px; padding: 10px 20px;">
                    Needs Changes
                </button>
            </div>
            
            <!-- Revision Panel (hidden by default) -->
            <div class="revision-panel" id="revision-panel" style="display: none;">
                <h4>What would you like to change?</h4>
                <div class="revision-options">
                    <div class="revision-option" onclick="selectRevisionType('minor', this)" data-type="minor">
                        <div class="option-title">Minor Tweaks</div>
                        <div class="option-desc">Adjust pacing, colors, or small details</div>
                    </div>
                    <div class="revision-option" onclick="selectRevisionType('major', this)" data-type="major">
                        <div class="option-title">Start Over</div>
                        <div class="option-desc">Regenerate with different approach</div>
                    </div>
                </div>
                <textarea class="revision-feedback" id="revision-feedback" placeholder="Tell the AI what's wrong or what you'd like changed..."></textarea>
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-primary" onclick="submitRevision()" style="flex: 1;">Submit Feedback</button>
                    <button class="btn btn-secondary" onclick="hideRevisionPanel()">Cancel</button>
                </div>
            </div>
            
            <!-- Video Description Section -->
            <div style="margin-top: 16px; padding: 12px; background: rgba(0,0,0,0.2); border-radius: 8px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span style="font-size: 12px; font-weight: 600; color: var(--gold);">Video Description</span>
                    <button onclick="copyDescription()" class="btn btn-secondary" style="font-size: 11px; padding: 4px 12px;">Copy</button>
                </div>
                <textarea id="video-description" readonly style="width: 100%; height: 60px; background: transparent; border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 12px; padding: 8px; resize: none;">${renderData.description || 'Generating description...'}</textarea>
                ${hasSources ? `
                <div style="margin-top: 8px;">
                    <label style="display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-dim); cursor: pointer;">
                        <input type="checkbox" id="include-citations" onchange="toggleCitations()" style="accent-color: var(--gold);">
                        Include citations
                    </label>
                    <div id="citations-text" style="display: none; margin-top: 6px; font-size: 11px; color: var(--text-dim); padding: 8px; background: rgba(0,0,0,0.15); border-radius: 4px;">
                        Sources: ${trendSources.map(s => s.title || s.url).join(', ')}
                    </div>
                </div>
                ` : ''}
            </div>
            
            <!-- Multi-Platform Export & Promo Pack -->
            <div style="margin-top: 16px; padding: 12px; background: linear-gradient(135deg, rgba(255,214,10,0.1), rgba(255,214,10,0.02)); border: 1px solid rgba(255,214,10,0.2); border-radius: 8px;">
                <div style="font-size: 12px; font-weight: 600; color: var(--gold); margin-bottom: 10px;">Boost Your Reach</div>
                <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                    <button onclick="showPlatformExport('${videoUrl}')" class="btn btn-secondary" style="font-size: 11px; padding: 6px 12px; display: flex; align-items: center; gap: 4px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18"/></svg>
                        Export All Formats
                    </button>
                    <button onclick="generatePromoPack('${videoUrl}')" class="btn btn-secondary" style="font-size: 11px; padding: 6px 12px; display: flex; align-items: center; gap: 4px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                        Generate Promo Pack
                    </button>
                </div>
            </div>
        </div>
    `;
    
    messagesDiv.appendChild(cardDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    
    // Trigger completion effects after a brief delay
    setTimeout(() => {
        const completionCard = document.getElementById('video-completion-card');
        if (completionCard) {
            triggerConfetti(completionCard);
            triggerShimmer(completionCard);
        }
    }, 100);
}

// Caption position selector
function setCaptionPosition(position, btn) {
    selectedCaptionPosition = position;
    document.querySelectorAll('.caption-pos-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}

// Accept video (download as preview) - counts as "like"
function acceptVideo(videoUrl) {
    // Record positive feedback
    recordVideoFeedback('positive', 'User downloaded preview');
    
    // Trigger download
    const link = document.createElement('a');
    link.href = videoUrl;
    link.download = 'framd-video.mp4';
    link.click();
    
    showSuccess('Downloaded', 'Your video has been downloaded.');
}

// Get final video (removes watermark, uses tokens)
async function getFinalVideo(previewUrl) {
    showRenderOverlay('Generating Final Video');
    updateRenderStage('Removing watermark...', 'Creating your final video');
    
    try {
        const response = await fetch('/finalize-video', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                preview_url: previewUrl,
                caption_position: selectedCaptionPosition
            })
        });
        
        const data = await response.json();
        hideRenderOverlay();
        
        if (data.success) {
            // Replace preview with final video
            const container = document.getElementById('preview-container');
            if (container) {
                const watermark = container.querySelector('.bouncing-watermark');
                if (watermark) watermark.remove();
                
                const video = container.querySelector('video source');
                if (video) {
                    video.src = data.video_url;
                    container.querySelector('video').load();
                }
            }
            
            showSuccess('Final Video Ready', 'Your video is ready to download without watermark!');
            
            // Update download button
            const acceptBtn = document.querySelector('.btn-accept');
            if (acceptBtn) {
                acceptBtn.onclick = () => acceptVideo(data.video_url);
            }
            
            refreshTokenBalance();
        } else {
            showError('Error', data.error || 'Failed to generate final video');
        }
    } catch (err) {
        hideRenderOverlay();
        showError('Error', 'Failed to generate final video');
    }
}

// Revision flow
let selectedRevisionType = 'minor';

// Element editing state
let elementEditMode = false;
let currentElements = [];
let selectedElement = null;
let currentTemplateId = null;

function toggleElementEditMode() {
    elementEditMode = !elementEditMode;
    const btn = document.getElementById('edit-elements-btn');
    const container = document.getElementById('preview-container');
    
    if (elementEditMode) {
        btn.classList.add('active');
        btn.textContent = 'Exit Edit Mode';
        container.classList.add('element-edit-mode');
        loadTemplateElements();
    } else {
        btn.classList.remove('active');
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg> Edit Elements';
        container.classList.remove('element-edit-mode');
        clearElementOverlays();
        hideElementEditPanel();
    }
}

async function loadTemplateElements() {
    if (!currentTemplateId) {
        // If no template loaded yet, show a message
        showToast('No template loaded. Generate a video first, then edit elements.');
        return;
    }
    
    try {
        const response = await fetch(`/get-template-elements/${currentTemplateId}`);
        const data = await response.json();
        
        if (data.elements) {
            currentElements = data.elements;
            renderElementOverlays(data.elements);
        }
    } catch (error) {
        console.error('Failed to load elements:', error);
    }
}

function renderElementOverlays(elements) {
    const container = document.getElementById('preview-container');
    
    // Remove existing overlay
    const existingOverlay = container.querySelector('.element-overlay-container');
    if (existingOverlay) existingOverlay.remove();
    
    // Create overlay container
    const overlayContainer = document.createElement('div');
    overlayContainer.className = 'element-overlay-container';
    
    elements.forEach(elem => {
        const hotspot = document.createElement('div');
        hotspot.className = 'element-hotspot';
        hotspot.dataset.elementId = elem.id;
        hotspot.dataset.elementName = elem.display_name || elem.name;
        
        // Position based on element data
        const x = (elem.position?.x || 0.5) * 100;
        const y = (elem.position?.y || 0.5) * 100;
        const w = (elem.position?.width || 0.2) * 100;
        const h = (elem.position?.height || 0.1) * 100;
        
        hotspot.style.left = `${x - w/2}%`;
        hotspot.style.top = `${y - h/2}%`;
        hotspot.style.width = `${w}%`;
        hotspot.style.height = `${h}%`;
        
        // Add label
        const label = document.createElement('div');
        label.className = 'element-label';
        label.textContent = elem.display_name || elem.name;
        hotspot.appendChild(label);
        
        // Click handler
        hotspot.onclick = () => selectElement(elem);
        
        overlayContainer.appendChild(hotspot);
    });
    
    container.appendChild(overlayContainer);
}

function clearElementOverlays() {
    const container = document.getElementById('preview-container');
    const overlay = container?.querySelector('.element-overlay-container');
    if (overlay) overlay.remove();
}

function selectElement(element) {
    selectedElement = element;
    
    // Update UI
    document.querySelectorAll('.element-hotspot').forEach(h => h.classList.remove('selected'));
    const hotspot = document.querySelector(`[data-element-id="${element.id}"]`);
    if (hotspot) hotspot.classList.add('selected');
    
    showElementEditPanel(element);
}

function showElementEditPanel(element) {
    // Remove existing panel
    let panel = document.getElementById('element-edit-panel');
    if (panel) panel.remove();
    
    // Create panel
    panel = document.createElement('div');
    panel.id = 'element-edit-panel';
    panel.className = 'element-edit-panel visible';
    panel.innerHTML = `
        <h4>Editing: ${element.display_name || element.name}</h4>
        <input type="text" class="element-edit-input" id="element-edit-input" 
               placeholder="Describe what you want..." 
               value="">
        <div class="element-edit-actions">
            <button class="btn btn-primary" onclick="submitElementChange()" style="flex: 1;">Apply Change</button>
            <button class="btn btn-secondary" onclick="hideElementEditPanel()">Cancel</button>
        </div>
    `;
    
    document.body.appendChild(panel);
    document.getElementById('element-edit-input').focus();
    
    // Handle enter key
    document.getElementById('element-edit-input').onkeydown = (e) => {
        if (e.key === 'Enter') submitElementChange();
    };
}

function hideElementEditPanel() {
    const panel = document.getElementById('element-edit-panel');
    if (panel) panel.remove();
    selectedElement = null;
    document.querySelectorAll('.element-hotspot').forEach(h => h.classList.remove('selected'));
}

async function submitElementChange() {
    if (!selectedElement) return;
    
    const input = document.getElementById('element-edit-input');
    const instruction = input.value.trim();
    
    if (!instruction) {
        showWarning('Instruction Required', 'Please describe what you want to change');
        return;
    }
    
    // Send to chat with element context
    const message = `Change the "${selectedElement.display_name || selectedElement.name}" element: ${instruction}`;
    addMessage(message, true);
    
    hideElementEditPanel();
    toggleElementEditMode(); // Exit edit mode
    
    // Trigger regeneration
    addTypingIndicator();
    
    try {
        const response = await fetch('/regenerate-element', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                element_id: selectedElement.id,
                instruction: instruction
            })
        });
        
        removeTypingIndicator();
        const data = await response.json();
        
        if (data.success) {
            addMessage(`Updated "${data.element_name}". The change has been applied.`, false);
            showToast('Element updated successfully');
        } else {
            addMessage(`I couldn't update that element: ${data.error}`, false);
        }
    } catch (error) {
        removeTypingIndicator();
        addMessage('Sorry, there was an error updating the element.', false);
    }
}

function showRevisionPanel() {
    document.getElementById('revision-panel').style.display = 'block';
}

function hideRevisionPanel() {
    document.getElementById('revision-panel').style.display = 'none';
}

function selectRevisionType(type, element) {
    selectedRevisionType = type;
    document.querySelectorAll('.revision-option').forEach(opt => opt.classList.remove('selected'));
    element.classList.add('selected');
}

async function submitRevision() {
    const feedback = document.getElementById('revision-feedback').value.trim();
    if (!feedback) {
        showWarning('Feedback Required', 'Please describe what you want changed');
        return;
    }
    
    hideRevisionPanel();
    
    // Send feedback to AI
    addMessage(feedback, true);
    
    // Record feedback for learning
    recordVideoFeedback('revision', feedback);
    
    // Let AI handle the revision request
    const revisionPrompt = selectedRevisionType === 'minor' 
        ? `The user wants minor tweaks to the video: "${feedback}". Adjust the current video accordingly.`
        : `The user wants to start over with a different approach: "${feedback}". Regenerate the video with this feedback in mind.`;
    
    // Trigger AI response
    handleAIRevision(revisionPrompt, selectedRevisionType);
}

async function handleAIRevision(prompt, revisionType) {
    addTypingIndicator();
    
    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: prompt,
                revision_type: revisionType,
                previous_video: currentPreviewData
            })
        });
        
        removeTypingIndicator();
        const data = await response.json();
        
        if (data.response) {
            addMessage(data.response, false);
        }
    } catch (err) {
        removeTypingIndicator();
        showError('Error', 'Failed to process revision request');
    }
}

function recordVideoFeedback(type, details) {
    fetch('/record-video-feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            feedback_type: type,
            details: details,
            video_data: currentPreviewData
        })
    }).catch(err => console.warn('Failed to record feedback:', err));
}

// Display Visual Plan before generating
async function createAndShowVisualPlan(script, intent, template) {
    try {
        const response = await fetch('/create-visual-plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                script: script,
                intent: intent,
                template: template
            })
        });
        
        const data = await response.json();
        if (data.success && data.plan) {
            displayVisualPlan(data.plan);
            return data.plan;
        }
        return null;
    } catch (err) {
        console.error('Failed to create visual plan:', err);
        return null;
    }
}

function displayVisualPlan(plan) {
    const messagesDiv = document.getElementById('messages');
    if (!messagesDiv) return;
    
    const sourceLabels = {
        'stock': 'Stock Photo',
        'dalle': 'AI Generated',
        'user_content': 'Your Content'
    };
    
    const scenesHtml = plan.scenes.map((scene, i) => `
        <div class="scene-plan-item">
            <div class="scene-plan-index">${i + 1}</div>
            <div class="scene-plan-details">
                <div class="scene-plan-text">${scene.text.substring(0, 60)}${scene.text.length > 60 ? '...' : ''}</div>
                <div class="scene-plan-source">
                    <span class="source-tag">${sourceLabels[scene.source] || scene.source}</span>
                    ${scene.source_reason}
                </div>
            </div>
        </div>
    `).join('');
    
    const planCard = document.createElement('div');
    planCard.className = 'message ai';
    planCard.innerHTML = `
        <div class="message-content">
            <div class="visual-plan-card">
                <div class="visual-plan-header">
                    <h4>Visual Plan</h4>
                    <span style="font-size: 11px; color: var(--text-dim);">Content Type: ${plan.content_type}</span>
                </div>
                <div style="display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap;">
                    ${plan.color_palette.slice(0, 5).map(color => `
                        <div style="width: 24px; height: 24px; border-radius: 4px; background: ${color}; border: 1px solid rgba(255,255,255,0.1);"></div>
                    `).join('')}
                    <span style="font-size: 11px; color: var(--text-dim); align-self: center; margin-left: 8px;">${plan.color_mood}</span>
                </div>
                <div class="visual-plan-scenes">
                    ${scenesHtml}
                </div>
                <div style="margin-top: 12px; display: flex; gap: 10px;">
                    <button class="btn btn-primary" onclick="proceedWithPlan('${plan.plan_id}')" style="flex: 1;">Generate Video</button>
                    <button class="btn btn-secondary" onclick="adjustPlan('${plan.plan_id}')">Adjust Plan</button>
                </div>
            </div>
        </div>
    `;
    
    messagesDiv.appendChild(planCard);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

async function proceedWithPlan(planId) {
    addMessage('Generating video based on the visual plan...', false);
    // This would trigger the render with the plan
    // For now, proceed with normal render
    if (typeof startPersonalizedRender === 'function') {
        startPersonalizedRender();
    }
}

function adjustPlan(planId) {
    addMessage('Tell me what you would like to change about the visual plan.', false);
}

// Store workflow preference for AI learning
function storeWorkflowPreference(key, value) {
    try {
        const prefs = JSON.parse(localStorage.getItem('krakd_workflow_prefs') || '{}');
        prefs[key] = value;
        prefs.lastUpdated = Date.now();
        localStorage.setItem('krakd_workflow_prefs', JSON.stringify(prefs));
    } catch (e) {
        console.warn('Could not store workflow preference:', e);
    }
}

// Proceed to review after confirmation
async function proceedToReview() {
    const modal = document.querySelector('.review-confirm-modal');
    if (modal) modal.remove();
    
    // Advance to step 3 - Visual Curation
    currentWorkflowStep = 3;
    updateProjectWorkflowStep(3);
    storeWorkflowPreference('visual_curation_started', true);
    
    // Show visual curation guide
    renderWorkflowGuide(3);
    
    // Estimate clip duration before curating visuals
    try {
        const durationResp = await fetch('/estimate-clip-duration', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script: currentScript })
        });
        const durationData = await durationResp.json();
        
        // Show duration estimate with status
        let durationMsg = `Estimated clip: ${durationData.duration_display} (${durationData.word_count} words)`;
        if (durationData.status === 'short') {
            durationMsg += ' - Script may be too short';
        } else if (durationData.status === 'long') {
            durationMsg += ' - Script may be too long';
        }
        showToast(durationMsg);
        
        // Store for later use
        window.estimatedDuration = durationData;
    } catch (e) {
        console.warn('Could not estimate duration:', e);
    }
    
    showToast('Curating visuals...');
    startLoading();
    await curateVisuals(currentScript);
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Extract simplified script for display (spoken lines + scene headers in bold)
function simplifyScriptForDisplay(script) {
    const lines = script.split('\n');
    let simplified = [];
    let currentScene = '';
    let pendingScene = false;
    
    for (let line of lines) {
        const trimmed = line.trim();
        
        // Scene header (e.g., "SCENE 1 [5s]")
        if (/^SCENE\s+\d+/i.test(trimmed)) {
            // If there was a pending scene without location, output it now
            if (pendingScene && currentScene) {
                simplified.push({ type: 'header', text: currentScene });
            }
            currentScene = trimmed.replace(/\[.*?\]/g, '').trim();
            pendingScene = true;
            continue;
        }
        
        // Location line (EXT. or INT.) - combine with scene number
        if (/^(EXT\.|INT\.)/i.test(trimmed)) {
            const location = trimmed.replace(/^(EXT\.|INT\.)\s*/i, '').replace(/-.*$/, '').trim();
            if (currentScene) {
                simplified.push({ type: 'header', text: currentScene + ' - ' + location });
            } else {
                simplified.push({ type: 'header', text: location });
            }
            currentScene = '';
            pendingScene = false;
            continue;
        }
        
        // Skip visual directions, underscores, character names as headers, and technical notes
        if (trimmed.startsWith('VISUAL:') || 
            trimmed.startsWith('CUT:') || 
            trimmed.startsWith('===') ||
            trimmed.startsWith('___') ||
            trimmed.startsWith('CHARACTERS:') ||
            trimmed.startsWith('VOICES?') ||
            /^[A-Z\s]+$/.test(trimmed) && trimmed.length < 30) {
            continue;
        }
        
        // Dialogue lines (indented or regular text that's not a direction)
        if (trimmed.length > 0 && !trimmed.match(/^\[.*\]$/)) {
            // If there's a pending scene header, output it before dialogue
            if (pendingScene && currentScene) {
                simplified.push({ type: 'header', text: currentScene });
                currentScene = '';
                pendingScene = false;
            }
            simplified.push({ type: 'dialogue', text: trimmed });
        }
    }
    
    // Output any remaining pending scene
    if (pendingScene && currentScene) {
        simplified.push({ type: 'header', text: currentScene });
    }
    
    // Build safe HTML with escaped content
    return simplified.map(item => {
        const escaped = escapeHtml(item.text);
        return item.type === 'header' ? `<strong>${escaped}</strong>` : escaped;
    }).join('<br><br>');
}

// Curate visuals
async function curateVisuals(script) {
    try {
        console.log('Starting visual curation...');
        const response = await fetch('/curate-visuals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script })
        });
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('Server response:', errorText);
            throw new Error(`Server error: ${response.status}`);
        }
        
        const data = await response.json();
        console.log('Curate response:', data);
        
        if (data.success) {
            currentVisualPlan = data.visual_board;
            
            // Save visual_plan to project
            saveCurrentProject({ visual_plan: data.visual_board });
            
            // Show simplified script for user (spoken lines only)
            const scriptEl = document.getElementById('script-text');
            scriptEl.innerHTML = simplifyScriptForDisplay(script);
            
            // Populate scene composer with sections from visual board
            populateScenesFromVisualBoard(data.visual_board);
            
            // Stay in chat - auto-advance to voice casting
            const sceneCount = data.visual_board.sections?.length || 0;
            currentWorkflowStep = 4;
            addMessage(`Visuals curated for ${sceneCount} scenes. Moving to voice casting...`, false);
            showToast('Visuals curated!');
            
            // Auto-advance to voice options
            setTimeout(() => {
                showVoiceCastingOptions();
            }, 500);
        } else {
            console.error('Curate failed:', data.error);
            showToast(data.error || 'Failed to curate visuals');
        }
    } catch (error) {
        console.error('Curate error:', error);
        showToast('Failed to curate visuals: ' + (error.message || String(error) || 'Unknown error'));
    } finally {
        stopLoading();
    }
}

// Populate visual grid
function populateVisuals(visualBoard) {
    const grid = document.getElementById('visual-grid');
    if (!grid) {
        console.error('Visual grid element not found');
        return;
    }
    grid.innerHTML = '';
    
    if (!visualBoard) {
        console.log('No visual board provided');
        return;
    }
    
    if (!visualBoard.sections || !Array.isArray(visualBoard.sections)) {
        console.log('No sections in visual board:', visualBoard);
        return;
    }
    
    console.log('Populating visuals with', visualBoard.sections.length, 'sections');
    
    let visualsAdded = 0;
    visualBoard.sections.forEach((section, i) => {
        if (section.suggested_videos && section.suggested_videos.length > 0) {
            const video = section.suggested_videos[0];
            // Get thumbnail with fallbacks
            const thumbUrl = video.thumbnail || video.preview_url || video.url || '';
            if (!thumbUrl) {
                console.log('Section', i, 'has no valid thumbnail');
                return;
            }
            
            const div = document.createElement('div');
            div.className = 'visual-option' + (visualsAdded === 0 ? ' selected' : '');
            div.innerHTML = `
                <img src="${thumbUrl}" alt="Scene ${i + 1}" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22100%22 height=%22100%22><rect fill=%22%23333%22 width=%22100%22 height=%22100%22/><text x=%2250%25%22 y=%2250%25%22 fill=%22%23888%22 text-anchor=%22middle%22 dy=%22.3em%22>No Preview</text></svg>'">
            `;
            div.onclick = () => {
                document.querySelectorAll('.visual-option').forEach(v => v.classList.remove('selected'));
                div.classList.add('selected');
            };
            grid.appendChild(div);
            visualsAdded++;
        }
    });
    
    console.log('Added', visualsAdded, 'visual options to grid');
    
    if (visualsAdded === 0) {
        grid.innerHTML = '<div class="no-visuals-msg">No visuals found. Try a different script.</div>';
    }
}

// Generate video
let renderTimer = null;
let renderStartTime = null;

function showLoading(show = true) {
    const overlay = document.getElementById('loading-overlay');
    if (show) {
        overlay.classList.add('show');
        renderStartTime = Date.now();
        updateTimer();
        renderTimer = setInterval(updateTimer, 1000);
        // Reset progress bar
        updateRenderProgress(0, '');
    } else {
        overlay.classList.remove('show');
        if (renderTimer) {
            clearInterval(renderTimer);
            renderTimer = null;
        }
        // Reset progress bar after delay
        setTimeout(() => updateRenderProgress(0, ''), 500);
    }
}

function updateTimer() {
    if (!renderStartTime) return;
    const elapsed = Math.floor((Date.now() - renderStartTime) / 1000);
    const mins = Math.floor(elapsed / 60);
    const secs = elapsed % 60;
    document.getElementById('loading-timer').textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
}

function updateLoadingStatus(status) {
    document.getElementById('loading-status').textContent = status;
}

function updateRenderProgress(percent, step) {
    const bar = document.getElementById('render-progress-bar');
    if (bar) bar.style.width = percent + '%';
    
    // Update step indicators
    const steps = ['voice', 'scenes', 'captions', 'render'];
    const stepIndex = steps.indexOf(step);
    
    steps.forEach((s, i) => {
        const stepEl = document.getElementById('step-' + s);
        if (stepEl) {
            stepEl.style.opacity = i <= stepIndex ? '1' : '0.4';
        }
    });
}

// Session spending tracker
let sessionTokensSpent = 0;
let stageDirections = '';

function updateSpendingTracker(amount) {
    sessionTokensSpent += amount;
    const el = document.getElementById('session-spent');
    if (el) el.textContent = sessionTokensSpent;
}

// Token costs - unified pricing for simplicity
// All-inclusive cost per video: includes AI character, premium voice, captions, SFX, everything
const TOKEN_COSTS = {
    baseVideo: 25,  // Base cost including script, voice, basic rendering
    perCharacter: 3,  // Additional characters beyond first
    perSfx: 1  // Per sound effect
};

// Count SFX in stage directions and update badge/cost
function countSoundFx() {
    const input = document.getElementById('stage-directions-input');
    if (!input) return 0;
    const matches = input.value.match(/\[SOUND:/gi);
    return matches ? matches.length : 0;
}

function updateSfxTokenBadge() {
    // SFX badge removed - cost is now unified
    updateTotalTokenCost();
}

function updateTotalTokenCost() {
    // Calculate unified all-inclusive cost
    const sfxCount = countSoundFx();
    const sfxCost = sfxCount * TOKEN_COSTS.perSfx;
    
    // Count additional characters beyond the first
    const charLayers = document.querySelectorAll('.character-layer');
    const extraChars = Math.max(0, charLayers.length - 1);
    const charCost = extraChars * TOKEN_COSTS.perCharacter;
    
    const totalCost = TOKEN_COSTS.baseVideo + sfxCost + charCost;
    const costEl = document.getElementById('token-cost');
    if (costEl) {
        costEl.textContent = totalCost;
    }
    
    // Update dollar cost estimate (based on 400 token pack: $25/400 = ~$0.0625/token)
    const dollarCost = (totalCost * 0.0625).toFixed(2);
    const dollarEl = document.getElementById('dollar-cost');
    if (dollarEl) {
        dollarEl.textContent = '~$' + dollarCost;
    }
}

// Stage Directions Functions
async function generateStageDirections() {
    // currentScript is an object with full_script property, or fallback to DOM text
    const scriptText = (typeof currentScript === 'object' && currentScript?.full_script) 
        ? currentScript.full_script 
        : (typeof currentScript === 'string' ? currentScript : document.getElementById('script-text').textContent);
    if (!scriptText || scriptText.includes('Script will appear')) {
        showToast('Generate a script first');
        return;
    }
    
    showToast('Generating stage directions...');
    
    try {
        const response = await fetch('/generate-stage-directions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script: scriptText })
        });
        
        const data = await response.json();
        if (data.success) {
            document.getElementById('stage-directions-input').value = data.directions;
            stageDirections = data.directions;
            updateSpendingTracker(3);
            updateSfxTokenBadge();  // Update SFX count and total cost
            showToast('Stage directions generated!');
        } else {
            showToast(data.error || 'Failed to generate directions');
        }
    } catch (err) {
        showToast('Error generating directions');
    }
}

function checkStageDirections() {
    const input = document.getElementById('stage-directions-input');
    const directions = input ? input.value.trim() : '';
    // Has directions if not empty and has meaningful content
    return directions.length > 0;
}

function openStageDirectionsConfirm() {
    document.getElementById('stage-directions-confirm').classList.add('open');
}

function closeStageDirectionsConfirm() {
    document.getElementById('stage-directions-confirm').classList.remove('open');
}

function confirmGenerateNoDirections() {
    closeStageDirectionsConfirm();
    doGenerateVideo();
}

async function generateVideo() {
    // Check subscription status first
    if (!isPro) {
        showUpgradePrompt();
        return;
    }
    // Check if stage directions are empty
    if (!checkStageDirections()) {
        openStageDirectionsConfirm();
        return;
    }
    doGenerateVideo();
}

async function doGenerateVideo() {
    showLoading(true);
    document.body.classList.add('loading');
    
    // Track which project is generating
    generatingProjectId = currentProjectId;
    
    // Track token spending - unified all-inclusive cost
    const sfxCount = countSoundFx();
    const sfxCost = sfxCount * TOKEN_COSTS.perSfx;
    const charLayers = document.querySelectorAll('.character-layer');
    const extraChars = Math.max(0, charLayers.length - 1);
    const charCost = extraChars * TOKEN_COSTS.perCharacter;
    const totalCost = TOKEN_COSTS.baseVideo + sfxCost + charCost;
    updateSpendingTracker(totalCost);
    
    try {
        // Step 1: Generate voiceover from script with character voices
        updateLoadingStatus('Generating voiceover...');
        updateRenderProgress(20, 'voice');
        if (generatingProjectId) updateProjectCardProgress(generatingProjectId, 20, 'Generating voiceover...');
        
        // Get stage directions from input
        const stageDirectionsInput = document.getElementById('stage-directions-input');
        const stageDirectionsText = stageDirectionsInput ? stageDirectionsInput.value : '';
        
        const voiceResponse = await fetch('/generate-voiceover-multi', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                script: currentScript,
                character_voices: characterVoices,
                custom_prompts: customVoicePrompts,
                stage_directions: stageDirectionsText
            })
        });
        
        const voiceData = await voiceResponse.json();
        if (!voiceData.success) {
            throw new Error(voiceData.error || 'Voiceover generation failed');
        }
        
        const audioPath = voiceData.audio_path;
        
        // Advance to step 6 - Audio Preview and show guide
        if (currentWorkflowStep < 6) {
            currentWorkflowStep = 6;
            storeWorkflowPreference('audio_generated', true);
            renderWorkflowGuide(6);
        }
        
        // Step 2: Build scenes array from visual plan or scene composer
        updateLoadingStatus('Preparing scenes...');
        updateRenderProgress(50, 'scenes');
        if (generatingProjectId) updateProjectCardProgress(generatingProjectId, 50, 'Preparing scenes...');
        
        let scenesForRender = [];
        
        // Use scenes from scene composer if available
        if (scenes.length > 0) {
            scenesForRender = scenes.map(scene => {
                // Extract URL from background object
                let videoUrl = null;
                if (scene.background) {
                    videoUrl = scene.background.download_url || 
                               scene.background.url || 
                               scene.background.video_url ||
                               (typeof scene.background === 'string' ? scene.background : null);
                }
                return {
                    video_url: videoUrl,
                    duration: scene.duration || 5
                };
            }).filter(s => s.video_url);
        }
        
        // Fallback to visual plan images if no scenes
        if (scenesForRender.length === 0 && currentVisualPlan && currentVisualPlan.curated_visuals) {
            scenesForRender = currentVisualPlan.curated_visuals.map(v => ({
                video_url: v.video_url || v.url,
                duration: 5
            })).filter(s => s.video_url);
        }
        
        // If still no scenes, use any images from visual board
        if (scenesForRender.length === 0 && currentVisualPlan) {
            const allVisuals = currentVisualPlan.images || currentVisualPlan.visuals || [];
            scenesForRender = allVisuals.slice(0, 6).map(v => ({
                video_url: v.video_url || v.url || v.image_url,
                duration: 5
            })).filter(s => s.video_url);
        }
        
        if (scenesForRender.length === 0) {
            throw new Error('No visual content found. Please add scenes or curate visuals first.');
        }
        
        // Step 3: Render final video with captions
        updateLoadingStatus('Adding captions...');
        updateRenderProgress(65, 'captions');
        setTimeout(() => {
            updateLoadingStatus('Assembling final video...');
            updateRenderProgress(80, 'render');
        }, 2000);
        if (generatingProjectId) updateProjectCardProgress(generatingProjectId, 75, 'Assembling video...');
        
        const renderResponse = await fetch('/render-video', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scenes: scenesForRender,
                audio_path: audioPath,
                format: selectedFormat,
                script: currentScript,
                captions: getCaptionSettings()
            })
        });
        
        const renderData = await renderResponse.json();
        
        showLoading(false);
        document.body.classList.remove('loading');
        
        if (renderData.success) {
            updateRenderProgress(100, 'render');
            if (generatingProjectId) updateProjectCardProgress(generatingProjectId, 100, 'Complete!');
            document.getElementById('export-video').src = renderData.video_path;
            
            // Save to video history for re-downloading
            const projectName = currentProjectName || 'Untitled Video';
            saveToVideoHistory(projectName, renderData.video_path, currentVideoFormat || '9:16');
            
            // Advance to step 7 - Final Preview
            currentWorkflowStep = 7;
            storeWorkflowPreference('video_rendered', true);
            renderWorkflowGuide(7);
            
            // Stay in chat - show video result inline
            renderVideoResultInChat(renderData);
            showSuccess('Video Ready', 'Your video has been rendered successfully!');
            
            // Refresh token balance after render
            refreshTokenBalance();
            
            // Clear progress after a short delay
            setTimeout(() => {
                if (generatingProjectId) clearProjectCardProgress(generatingProjectId);
            }, 2000);
        } else if (renderData.requires_subscription) {
            if (generatingProjectId) clearProjectCardProgress(generatingProjectId);
            showUpgradePrompt();
        } else {
            if (generatingProjectId) clearProjectCardProgress(generatingProjectId);
            showError('Video Generation Failed', formatUserError(renderData.error || 'Something went wrong with your video'));
        }
    } catch (error) {
        console.error('Generate error:', error);
        showLoading(false);
        document.body.classList.remove('loading');
        if (generatingProjectId) clearProjectCardProgress(generatingProjectId);
        showToast(formatUserError(error.message));
    }
}

// Download video
function downloadVideo() {
    const video = document.getElementById('export-video');
    if (video.src) {
        const a = document.createElement('a');
        a.href = video.src;
        a.download = 'framd-video.mp4';
        a.click();
    }
}

// Video Like/Dislike System
let currentRevisionCount = 0;
const MAX_FREE_REVISIONS = 3;

function handleVideoLike(liked) {
    const likeBtn = document.getElementById('like-btn');
    const dislikeBtn = document.getElementById('dislike-btn');
    const commentFlow = document.getElementById('dislike-comment-flow');
    const actionButtons = document.getElementById('export-action-buttons');
    const revisionCounter = document.getElementById('revision-counter');
    
    if (liked) {
        // User liked the video
        likeBtn.classList.add('selected');
        dislikeBtn.classList.remove('selected');
        commentFlow.style.display = 'none';
        actionButtons.style.display = 'block';
        
        // Save like to backend
        saveVideoFeedback(true, null);
        showToast('Video saved. Download or share it.');
    } else {
        // User disliked - show comment flow
        dislikeBtn.classList.add('selected');
        likeBtn.classList.remove('selected');
        
        // Check revision limit for free users
        const userIsPro = isPro;
        const revisionsLeft = MAX_FREE_REVISIONS - currentRevisionCount;
        
        if (!isPro && revisionsLeft <= 0) {
            showToast('No revisions left. Upgrade to Pro for unlimited revisions.');
            commentFlow.style.display = 'none';
            return;
        }
        
        // Show revision counter for free users
        if (!isPro) {
            revisionCounter.style.display = 'block';
            document.getElementById('revisions-left').textContent = revisionsLeft;
        }
        
        commentFlow.style.display = 'block';
        actionButtons.style.display = 'none';
    }
}

function cancelDislike() {
    const commentFlow = document.getElementById('dislike-comment-flow');
    const actionButtons = document.getElementById('export-action-buttons');
    const dislikeBtn = document.getElementById('dislike-btn');
    const revisionCounter = document.getElementById('revision-counter');
    
    commentFlow.style.display = 'none';
    actionButtons.style.display = 'block';
    dislikeBtn.classList.remove('selected');
    revisionCounter.style.display = 'none';
    document.getElementById('dislike-comment').value = '';
}

async function sendBackToAI() {
    const comment = document.getElementById('dislike-comment').value.trim();
    
    if (!comment) {
        showToast('Please tell me what to fix');
        return;
    }
    
    const sendBtn = document.getElementById('send-to-ai-btn');
    sendBtn.disabled = true;
    sendBtn.textContent = 'Sending to AI...';
    
    try {
        // Save dislike feedback first
        await saveVideoFeedback(false, comment);
        
        // Increment revision counter
        currentRevisionCount++;
        
        // Send to AI for refinement
        const response = await fetch('/refine-from-feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: currentProject,
                script: currentScript,
                feedback: comment,
                revision_number: currentRevisionCount
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // Update script with refined version
            currentScript = data.refined_script;
            displayScriptInEditor(data.refined_script);
            
            // Sync revision count from server
            if (data.revision_number) {
                currentRevisionCount = data.revision_number;
            }
            
            // Stay in chat - hide dislike modal and show revised script inline
            cancelDislike();
            
            // Add AI message about what was changed and show new script card
            addMessage(data.ai_message || 'Script revised based on your feedback. Review and regenerate when ready.', false, true);
            if (currentScript) {
                const scriptText = typeof currentScript === 'string' ? currentScript : currentScript.full_script;
                renderScriptCardInChat(scriptText);
            }
            showToast('Script refined. Ready to regenerate.');
        } else if (data.requires_subscription) {
            // Handle revision limit reached
            showToast(`Revision limit reached (${data.revisions_used}/${data.max_revisions}). Upgrade to Pro for unlimited revisions.`);
            cancelDislike();
        } else {
            throw new Error(data.error || 'Failed to refine script');
        }
    } catch (error) {
        console.error('Refinement error:', error);
        showToast('Error: ' + error.message);
    } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send Back to AI';
    }
}

async function saveVideoFeedback(liked, comment) {
    try {
        await fetch('/video-feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: currentProject,
                liked: liked,
                comment: comment,
                script: currentScript,
                revision_number: currentRevisionCount
            })
        });
    } catch (error) {
        console.error('Failed to save feedback:', error);
    }
}

function resetVideoFeedback() {
    currentRevisionCount = 0;
    const likeBtn = document.getElementById('like-btn');
    const dislikeBtn = document.getElementById('dislike-btn');
    const commentFlow = document.getElementById('dislike-comment-flow');
    const actionButtons = document.getElementById('export-action-buttons');
    const revisionCounter = document.getElementById('revision-counter');
    
    if (likeBtn) likeBtn.classList.remove('selected');
    if (dislikeBtn) dislikeBtn.classList.remove('selected');
    if (commentFlow) commentFlow.style.display = 'none';
    if (actionButtons) actionButtons.style.display = 'block';
    if (revisionCounter) revisionCounter.style.display = 'none';
    
    const commentInput = document.getElementById('dislike-comment');
    if (commentInput) commentInput.value = '';
}

// Feedback System
const feedbackData = {
    script: null,
    voice: null,
    visuals: null,
    soundfx: null,
    overall: null
};

function setFeedback(category, value, btn) {
    feedbackData[category] = value;
    
    // Update button states
    const container = btn.closest('.feedback-category');
    container.querySelectorAll('.feedback-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
}

function resetFeedback() {
    Object.keys(feedbackData).forEach(key => feedbackData[key] = null);
    document.querySelectorAll('.feedback-btn').forEach(btn => btn.classList.remove('selected'));
    document.getElementById('feedback-text').value = '';
}

async function submitFeedbackAndReflect() {
    const feedbackText = document.getElementById('feedback-text').value;
    
    // Check if any feedback was given
    const hasFeedback = Object.values(feedbackData).some(v => v !== null) || feedbackText.trim();
    if (!hasFeedback) {
        showToast('Please provide at least some feedback');
        return;
    }
    
    // Calculate severity based on ratings
    let severity = 'minor';
    const weakCount = Object.values(feedbackData).filter(v => v === 'weak').length;
    if (weakCount >= 3 || feedbackData.overall === 'weak') {
        severity = 'critical';
    } else if (weakCount >= 1) {
        severity = 'moderate';
    }
    
    try {
        // Submit feedback and get AI self-assessment
        const response = await fetch('/submit-feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: currentProjectId,
                script_rating: feedbackData.script,
                voice_rating: feedbackData.voice,
                visuals_rating: feedbackData.visuals,
                soundfx_rating: feedbackData.soundfx,
                overall_rating: feedbackData.overall,
                feedback_text: feedbackText,
                severity: severity,
                script: currentScript,
                voice_assignments: characterVoices,
                scenes: currentScenes
            })
        });
        
        const data = await response.json();
        if (data.success) {
            // Show reflection modal with AI's honest assessment
            showReflectionModal(data);
            resetFeedback();
        } else {
            showToast(data.error || 'Failed to submit feedback');
        }
    } catch (err) {
        console.error('Feedback submission error:', err);
        showToast('Error submitting feedback');
    }
}

function showReflectionModal(data) {
    const modal = document.getElementById('reflection-modal');
    
    // Update content
    document.getElementById('reflection-learned').textContent = data.ai_learned || 'Processing...';
    document.getElementById('reflection-improve').textContent = data.ai_to_improve || 'Still analyzing...';
    
    // Update progress bar
    const progressValue = document.getElementById('learning-progress-value');
    const progressFill = document.getElementById('learning-progress-fill');
    const learningGain = document.getElementById('learning-gain');
    
    const oldProgress = data.old_progress || 0;
    const newProgress = data.new_progress || 0;
    const gain = data.learning_points_gained || 0;
    
    progressValue.textContent = `${newProgress}%`;
    
    // Animate progress bar
    setTimeout(() => {
        progressFill.style.width = `${newProgress}%`;
    }, 100);
    
    // Show gain if any
    if (gain > 0) {
        learningGain.textContent = `+${gain}% from this project`;
        learningGain.style.display = 'block';
    } else {
        learningGain.style.display = 'none';
    }
    
    // Check for auto-generation unlock
    const autoGenUnlock = document.getElementById('auto-gen-unlock');
    if (data.can_auto_generate && !data.was_already_unlocked) {
        autoGenUnlock.style.display = 'block';
    } else {
        autoGenUnlock.style.display = 'none';
    }
    
    // Show modal
    modal.classList.add('show');
}

function closeReflectionModal() {
    document.getElementById('reflection-modal').classList.remove('show');
}

async function hostAndShare() {
    const video = document.getElementById('export-video');
    if (!video.src) {
        showToast('No video to host');
        return;
    }
    
    if (!isPro) {
        showUpgradePrompt();
        return;
    }
    
    try {
        showNotification('Hosting your video...', 'info');
        const resp = await fetch('/host-video', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_path: video.src,
                title: currentScript?.full_script ? currentScript.full_script.substring(0, 50) + '...' : 'My Framd Video',
                project_id: currentProjectId
            })
        });
        
        const data = await resp.json();
        if (data.success) {
            document.getElementById('share-url-input').value = data.share_url;
            document.getElementById('share-url-container').style.display = 'block';
            showToast('Video hosted! Share link ready.');
        } else if (data.requires_subscription) {
            showUpgradePrompt();
        } else {
            showToast(data.error || 'Failed to host video');
        }
    } catch (err) {
        console.error('Host error:', err);
        showToast('Error hosting video');
    }
}

function copyShareUrl() {
    const input = document.getElementById('share-url-input');
    input.select();
    document.execCommand('copy');
    showToast('Link copied!');
}

// Start over
function startOver() {
    conversationHistory = [];
    currentScript = null;
    currentVisualPlan = null;
    saveConversation();
    renderChatPanelMessages();
    
    document.getElementById('messages').innerHTML = `
        <div class="message ai">
            <div class="message-content">What would you like to create?</div>
        </div>
    `;
    
    document.querySelectorAll('.step').forEach(s => s.classList.remove('completed'));
    showStage('create');
}

// Buy tokens
function buyTokens() {
    window.open('/create-checkout?tokens=100', '_blank');
}

// Drag and drop
document.addEventListener('DOMContentLoaded', () => {
    const composer = document.querySelector('.bottom-chat-input-wrapper') || document.querySelector('.composer-inner');
    
    if (composer) {
        composer.addEventListener('dragenter', (e) => {
            e.preventDefault();
            composer.classList.add('drag-over');
        });
        
        composer.addEventListener('dragover', (e) => {
            e.preventDefault();
            composer.classList.add('drag-over');
        });
        
        composer.addEventListener('dragleave', (e) => {
            if (!composer.contains(e.relatedTarget)) {
                composer.classList.remove('drag-over');
            }
        });
        
        composer.addEventListener('drop', async (e) => {
            e.preventDefault();
            composer.classList.remove('drag-over');
            
            if (e.dataTransfer.files.length > 0) {
                const file = e.dataTransfer.files[0];
                
                if (isImageFile(file.name) || isVideoFile(file.name)) {
                    const formData = new FormData();
                    formData.append('file', file);
                    
                    try {
                        const uploadRes = await fetch('/upload', { method: 'POST', body: formData });
                        const uploadData = await uploadRes.json();
                        
                        if (uploadData.success) {
                            analyzeAndShowImagePlacement(file, uploadData.file_path);
                        }
                    } catch (err) {
                        console.error('Upload error:', err);
                        uploadedFile = file;
                        document.getElementById('file-name').textContent = file.name;
                        document.getElementById('file-preview').classList.add('show');
                    }
                } else {
                    uploadedFile = file;
                    document.getElementById('file-name').textContent = file.name;
                    document.getElementById('file-preview').classList.add('show');
                }
                
                const input = document.getElementById('composer-input');
                if (input) input.focus();
            }
        });
    }
    
    // Paste handling
    document.getElementById('composer-input').addEventListener('paste', (e) => {
        const items = e.clipboardData.items;
        for (let item of items) {
            if (item.kind === 'file') {
                const file = item.getAsFile();
                uploadedFile = file;
                document.getElementById('file-name').textContent = file.name;
                document.getElementById('file-preview').classList.add('show');
            }
        }
    });
    
    // Load token balance using centralized function
    refreshTokenBalance();
});

// ===== DISCOVER FEED FUNCTIONALITY =====
let feedItems = [];
let currentCardIndex = 0;
let isDragging = false;
let startX = 0;
let currentX = 0;

async function loadFeedItems() {
    try {
        const response = await fetch('/feed/items');
        const data = await response.json();
        feedItems = data.items || [];
        currentCardIndex = 0;
        renderCards();
    } catch (error) {
        console.error('Error loading feed:', error);
        document.getElementById('swipe-cards').innerHTML = `
            <div class="swipe-card empty-card">
                <p>Could not load content. Try again later.</p>
            </div>
        `;
    }
}

function renderCards() {
    const container = document.getElementById('swipe-cards');
    if (!feedItems.length || currentCardIndex >= feedItems.length) {
        container.innerHTML = `
            <div class="swipe-card empty-card">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 1rem; opacity: 0.5;">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="M8 15h8M9 9h.01M15 9h.01"/>
                </svg>
                <p>No more content to discover!</p>
                <p style="font-size: 0.85rem; margin-top: 0.5rem;">Generate more or check back later.</p>
            </div>
        `;
        return;
    }
    
    const visibleCards = feedItems.slice(currentCardIndex, currentCardIndex + 3);
    container.innerHTML = visibleCards.map((item, i) => `
        <div class="swipe-card" data-index="${currentCardIndex + i}" data-id="${item.id}" style="z-index: ${10 - i}; transform: scale(${1 - i * 0.05}) translateY(${i * 10}px);">
            <div class="swipe-overlay like">LIKE</div>
            <div class="swipe-overlay skip">SKIP</div>
            <div class="swipe-card-content">
                <div class="swipe-card-title">${escapeHtml(item.title)}</div>
                <div class="swipe-card-script">${escapeHtml(item.script || '')}</div>
            </div>
            <div class="swipe-card-meta">
                ${item.topic ? `<span class="swipe-card-tag">${escapeHtml(item.topic)}</span>` : ''}
                ${item.hook_style ? `<span class="swipe-card-tag">${escapeHtml(item.hook_style)}</span>` : ''}
            </div>
        </div>
    `).join('');
    
    const topCard = container.querySelector('.swipe-card');
    if (topCard) {
        setupCardDrag(topCard);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function setupCardDrag(card) {
    card.addEventListener('mousedown', startDrag);
    card.addEventListener('touchstart', startDrag, { passive: true });
    
    document.addEventListener('mousemove', onDrag);
    document.addEventListener('touchmove', onDrag, { passive: false });
    
    document.addEventListener('mouseup', endDrag);
    document.addEventListener('touchend', endDrag);
}

function startDrag(e) {
    isDragging = true;
    startX = e.type === 'mousedown' ? e.clientX : e.touches[0].clientX;
    currentX = startX;
}

function onDrag(e) {
    if (!isDragging) return;
    
    currentX = e.type === 'mousemove' ? e.clientX : e.touches[0].clientX;
    const diff = currentX - startX;
    const card = document.querySelector('.swipe-card[data-index="' + currentCardIndex + '"]');
    
    if (card) {
        const rotation = diff * 0.05;
        card.style.transform = `translateX(${diff}px) rotate(${rotation}deg)`;
        card.style.transition = 'none';
        
        const likeOverlay = card.querySelector('.swipe-overlay.like');
        const skipOverlay = card.querySelector('.swipe-overlay.skip');
        
        if (diff > 50) {
            likeOverlay.style.opacity = Math.min((diff - 50) / 100, 1);
            skipOverlay.style.opacity = 0;
        } else if (diff < -50) {
            skipOverlay.style.opacity = Math.min((-diff - 50) / 100, 1);
            likeOverlay.style.opacity = 0;
        } else {
            likeOverlay.style.opacity = 0;
            skipOverlay.style.opacity = 0;
        }
    }
}

function endDrag(e) {
    if (!isDragging) return;
    isDragging = false;
    
    const diff = currentX - startX;
    const card = document.querySelector('.swipe-card[data-index="' + currentCardIndex + '"]');
    
    if (card) {
        card.style.transition = 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
        
        if (diff > 100) {
            swipeCard('like');
        } else if (diff < -100) {
            swipeCard('skip');
        } else {
            card.style.transform = '';
            const likeOverlay = card.querySelector('.swipe-overlay.like');
            const skipOverlay = card.querySelector('.swipe-overlay.skip');
            if (likeOverlay) likeOverlay.style.opacity = 0;
            if (skipOverlay) skipOverlay.style.opacity = 0;
        }
    }
}

async function swipeCard(action, feedback = '') {
    const card = document.querySelector('.swipe-card[data-index="' + currentCardIndex + '"]');
    if (!card) return;
    
    const itemId = card.dataset.id;
    
    card.style.transition = 'transform 0.4s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s';
    card.style.transform = action === 'like' ? 'translateX(150%) rotate(30deg)' : 'translateX(-150%) rotate(-30deg)';
    card.style.opacity = '0';
    
    try {
        await fetch('/feed/swipe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_id: itemId, action: action, feedback: feedback })
        });
    } catch (error) {
        console.error('Error recording swipe:', error);
    }
    
    setTimeout(() => {
        currentCardIndex++;
        renderCards();
    }, 300);
}

function openFeedbackModal() {
    const modal = document.getElementById('feedback-modal');
    modal.classList.add('active');
    document.getElementById('feedback-text').value = '';
    document.getElementById('feedback-text').focus();
}

function closeFeedbackModal() {
    document.getElementById('feedback-modal').classList.remove('active');
}

async function submitFeedback() {
    const feedbackText = document.getElementById('feedback-text').value.trim();
    closeFeedbackModal();
    if (feedbackText) {
        await swipeCard('like', feedbackText);
    }
}

async function generateMoreContent() {
    const btn = document.querySelector('.generate-more-btn');
    btn.disabled = true;
    btn.textContent = 'Generating...';
    
    try {
        const topics = ['technology', 'business', 'science', 'culture', 'health', 'politics'];
        const randomTopic = topics[Math.floor(Math.random() * topics.length)];
        
        await fetch('/feed/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic: randomTopic })
        });
        
        await loadFeedItems();
    } catch (error) {
        console.error('Error generating content:', error);
    }
    
    btn.disabled = false;
    btn.textContent = 'Generate More Content';
}

function resetProject() {
    if (confirm('Start a new project? This will clear the current conversation.')) {
        startNewProject();
    }
}

function toggleAvatarMenu() {
    const menu = document.getElementById('avatar-menu');
    menu.classList.toggle('active');
}

function updateProjectNameDisplay(name) {
    const display = document.getElementById('project-name-display');
    const textSpan = document.getElementById('project-name-text');
    if (display && textSpan) {
        if (name) {
            textSpan.textContent = name;
            display.classList.remove('empty');
        } else {
            textSpan.textContent = 'Select a project';
            display.classList.add('empty');
        }
    }
}

// Edit project name inline
function editProjectName() {
    const display = document.getElementById('project-name-display');
    const textSpan = document.getElementById('project-name-text');
    
    if (!display || !textSpan || display.classList.contains('empty') || display.classList.contains('editing')) {
        return;
    }
    
    const currentName = textSpan.textContent;
    display.classList.add('editing');
    
    // Create input field
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'project-name-input';
    input.value = currentName;
    input.maxLength = 100;
    
    // Hide text span and edit icon, show input
    textSpan.style.display = 'none';
    display.querySelector('.edit-icon').style.display = 'none';
    display.insertBefore(input, textSpan);
    input.focus();
    input.select();
    
    // Save on blur or enter
    const saveEdit = async () => {
        const newName = input.value.trim() || currentName;
        
        // Remove input, restore text
        input.remove();
        textSpan.style.display = '';
        display.querySelector('.edit-icon').style.display = '';
        display.classList.remove('editing');
        textSpan.textContent = newName;
        
        // Save to backend if changed
        if (newName !== currentName && currentProjectId) {
            showSaveIndicator('saving');
            try {
                const response = await fetch(`/projects/${currentProjectId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: newName })
                });
                
                if (response.ok) {
                    showSaveIndicator('saved');
                } else {
                    console.error('Failed to update project name');
                    textSpan.textContent = currentName; // Revert on error
                    hideSaveIndicator();
                }
            } catch (error) {
                console.error('Error updating project name:', error);
                textSpan.textContent = currentName; // Revert on error
                hideSaveIndicator();
            }
        }
    };
    
    input.addEventListener('blur', saveEdit);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
        } else if (e.key === 'Escape') {
            input.value = currentName;
            input.blur();
        }
    });
    
    // Prevent click from bubbling and re-triggering edit
    input.addEventListener('click', (e) => e.stopPropagation());
}

function openSettings(section) {
    // Hide chat bar
    const chatBar = document.getElementById('bottom-chat-bar');
    if (chatBar) chatBar.style.display = 'none';
    
    // Hide back to projects link
    document.getElementById('back-to-projects').style.display = 'none';
    
    // Clear project name display
    updateProjectNameDisplay(null);
    
    // Switch to settings stage
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
    const stageId = 'stage-' + section;
    const stage = document.getElementById(stageId);
    if (stage) {
        stage.classList.add('active');
        
        // Load data for the page
        if (section === 'profile') loadProfileData();
        if (section === 'billing') loadBillingData();
    }
}

async function loadProfileData() {
    try {
        // Get user info
        const userEmail = document.body.dataset.userEmail || 'user@example.com';
        const userName = document.body.dataset.userName || 'User';
        const initial = userName.charAt(0).toUpperCase();
        
        document.getElementById('profile-initial-large').textContent = initial;
        document.getElementById('profile-display-name').textContent = userName;
        document.getElementById('profile-email-display').textContent = userEmail;
        document.getElementById('profile-email').textContent = userEmail;
        
        // Get project count
        const response = await fetch('/projects');
        if (response.ok) {
            const projects = await response.json();
            document.getElementById('profile-projects-count').textContent = projects.length;
        }
    } catch (err) {
        console.error('Error loading profile:', err);
    }
}

async function loadBillingData() {
    try {
        const response = await fetch('/subscription-status');
        if (response.ok) {
            const data = await response.json();
            const tier = data.tier || 'free';
            const tokenBalance = data.token_balance || 50;
            const monthlyTokens = data.monthly_tokens || 50;
            
            // Update all token displays using centralized function
            updateAllTokenDisplays(tokenBalance, monthlyTokens);
            
            const checkSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
            const xSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
            
            if (tier === 'pro') {
                document.getElementById('billing-plan-name').textContent = 'Pro Plan';
                document.getElementById('billing-plan-price').innerHTML = '$25<span>/month</span>';
                document.getElementById('billing-features').innerHTML = `
                    <li>${checkSvg} Script generation</li>
                    <li>${checkSvg} Video export</li>
                    <li>${checkSvg} Premium voices</li>
                    <li>${checkSvg} Unlimited revisions</li>
                    <li>${checkSvg} Auto-generator</li>
                `;
                document.getElementById('billing-upgrade-section').style.display = 'none';
                document.getElementById('billing-manage-section').style.display = 'block';
            } else if (tier === 'creator') {
                document.getElementById('billing-plan-name').textContent = 'Creator Plan';
                document.getElementById('billing-plan-price').innerHTML = '$10<span>/month</span>';
                document.getElementById('billing-features').innerHTML = `
                    <li>${checkSvg} Script generation</li>
                    <li>${checkSvg} Video export</li>
                    <li>${checkSvg} Premium voices</li>
                    <li>${xSvg} Unlimited revisions</li>
                    <li>${xSvg} Auto-generator</li>
                `;
                document.getElementById('billing-upgrade-section').innerHTML = `
                    <button class="settings-btn settings-btn-primary" onclick="startSubscription('pro')" style="width: 100%; padding: 1rem; font-size: 1rem;">
                        Upgrade to Pro - $25/month
                    </button>
                    <p style="text-align: center; color: rgba(255,214,10,0.5); font-size: 0.85rem; margin-top: 0.75rem;">
                        Get unlimited revisions and auto-generator
                    </p>
                `;
                document.getElementById('billing-manage-section').style.display = 'block';
            } else {
                // Free tier
                document.getElementById('billing-plan-name').textContent = 'Free Plan';
                document.getElementById('billing-plan-price').innerHTML = '$0<span>/month</span>';
                document.getElementById('billing-features').innerHTML = `
                    <li>${checkSvg} Script generation</li>
                    <li>${xSvg} Video export</li>
                    <li>${xSvg} Premium voices</li>
                    <li>${xSvg} Unlimited revisions</li>
                    <li>${xSvg} Auto-generator</li>
                `;
            }
        }
    } catch (err) {
        console.error('Error loading billing:', err);
    }
}

function togglePrivacySetting(setting) {
    const toggle = document.getElementById('toggle-' + setting);
    if (toggle) {
        toggle.classList.toggle('active');
    }
}

function exportUserData() {
    alert('Your data export will be emailed to you shortly.');
}

function confirmDeleteAccount() {
    if (confirm('Are you sure you want to delete your account? This action cannot be undone.')) {
        if (confirm('This will permanently delete all your projects and data. Type DELETE to confirm.')) {
            alert('Please contact support to complete account deletion.');
        }
    }
}

async function manageSubscription() {
    try {
        const response = await fetch('/create-customer-portal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await response.json();
        if (data.url) {
            window.location.href = data.url;
        }
    } catch (err) {
        console.error('Error opening customer portal:', err);
    }
}

document.addEventListener('click', (e) => {
    const menu = document.getElementById('avatar-menu');
    const btn = document.getElementById('avatar-btn');
    if (menu && btn && !menu.contains(e.target) && !btn.contains(e.target)) {
        menu.classList.remove('active');
    }
});

function goToDiscover() {
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
    document.getElementById('stage-discover').classList.add('active');
    loadFeedItems();
}

async function showLikedItems() {
    const sidebar = document.getElementById('liked-sidebar');
    sidebar.classList.add('active');
    
    const list = document.getElementById('liked-items-list');
    list.innerHTML = '<div class="loading-spinner"></div>';
    
    try {
        const response = await fetch('/feed/liked');
        const data = await response.json();
        
        if (!data.items || data.items.length === 0) {
            list.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">No liked items yet. Swipe right on content you love!</p>';
            return;
        }
        
        list.innerHTML = data.items.map(item => `
            <div class="liked-item" onclick="useAsDraft(${item.id}, '${escapeHtml(item.title)}', '${escapeHtml(item.script || '').replace(/'/g, "\\'")}')">
                <div class="liked-item-title">${escapeHtml(item.title)}</div>
                <div class="liked-item-preview">${escapeHtml(item.script || '')}</div>
            </div>
        `).join('');
    } catch (error) {
        list.innerHTML = '<p style="text-align: center; color: var(--text-secondary);">Could not load liked items.</p>';
    }
}

function closeLikedSidebar() {
    document.getElementById('liked-sidebar').classList.remove('active');
}

function useAsDraft(itemId, title, script) {
    closeLikedSidebar();
    goToProjects();
    startNewProject();
    setTimeout(() => {
        const input = document.getElementById('composer-input');
        if (input) {
            input.value = `Create a video with this script:\n\n${script}`;
            autoResize(input);
        }
    }, 500);
}
// Display content type badge
function displayContentType(contentType) {
    const thesisMeta = document.getElementById('thesis-meta');
    const thesisType = document.getElementById('thesis-type');
    if (thesisMeta && thesisType && contentType) {
        thesisType.className = 'content-type-badge ' + contentType;
        thesisType.textContent = contentType.charAt(0).toUpperCase() + contentType.slice(1);
        thesisMeta.style.display = 'flex';
    }
}

// Display visual layers - HIDDEN for now (adds complexity, doesn't show actual images)
function displayVisualLayers(visualPlan) {
    // Visual layers panel removed to simplify UI
    // The actual curated images are shown in the scene-composer after curate-visuals is called
    return;
}

const toastIcons = {
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="9 12 12 15 16 10"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
};

function showToast(typeOrMessage, titleOrType, message, duration = 5000) {
    const container = document.getElementById('toast-container');
    
    // Detect legacy signature: showToast('message') or showToast('message', 'type')
    const validTypes = ['success', 'error', 'warning', 'info'];
    const typeDefaults = { success: 'Success', error: 'Error', warning: 'Warning', info: 'Notice' };
    let type, title, msg, dur;
    
    if (validTypes.includes(typeOrMessage)) {
        // New signature: showToast(type, title, message?, duration?)
        type = typeOrMessage;
        title = titleOrType;
        msg = message || '';
        dur = typeof duration === 'number' ? duration : 5000;
    } else if (titleOrType && validTypes.includes(titleOrType)) {
        // Legacy signature: showToast('message', 'success'|'error')
        // Map: type from second arg, title as default, message from first arg
        type = titleOrType;
        title = typeDefaults[titleOrType] || 'Notice';
        msg = typeOrMessage;
        dur = 3000;
    } else {
        // Legacy signature: showToast('message') or showToast('message', duration)
        type = 'info';
        title = 'Notice';
        msg = typeOrMessage;
        dur = typeof titleOrType === 'number' ? titleOrType : 3000;
    }
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <div class="toast-icon">${toastIcons[type] || toastIcons.info}</div>
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            ${msg ? `<div class="toast-message">${msg}</div>` : ''}
        </div>
        <button class="toast-close" onclick="closeToast(this.parentElement)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    `;
    container.appendChild(toast);
    
    if (dur > 0) {
        setTimeout(() => closeToast(toast), dur);
    }
    
    return toast;
}

function closeToast(toast) {
    if (!toast || toast.classList.contains('toast-hide')) return;
    toast.classList.add('toast-hide');
    setTimeout(() => toast.remove(), 200);
}

// Convenience functions
function showSuccess(title, message, duration) { return showToast('success', title, message, duration); }
function showError(title, message, duration) { return showToast('error', title, message, duration); }
function showWarning(title, message, duration) { return showToast('warning', title, message, duration); }
function showInfo(title, message, duration) { return showToast('info', title, message, duration); }

let renderCancelled = false;

function showRenderOverlay(title = 'AI Remix in Progress') {
    renderCancelled = false;
    const overlay = document.getElementById('render-overlay');
    const titleEl = overlay.querySelector('.render-title');
    titleEl.textContent = title;
    updateRenderStage('Initializing...', 'Please wait while we create your video');
    overlay.classList.add('active');
}

function hideRenderOverlay() {
    const overlay = document.getElementById('render-overlay');
    overlay.classList.remove('active');
}

function updateRenderStage(stage, substage = '') {
    const stageEl = document.getElementById('render-stage');
    const substageEl = document.getElementById('render-substage');
    if (stageEl) stageEl.textContent = stage;
    if (substageEl) substageEl.textContent = substage;
}

function cancelRender() {
    renderCancelled = true;
    hideRenderOverlay();
    showWarning('Cancelled', 'Video rendering was cancelled');
}

function isRenderCancelled() {
    return renderCancelled;
}
