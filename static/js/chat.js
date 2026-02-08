const state = {
    currentProjectId: null,
    currentProjectName: null,
    currentMode: null,
    qualityTier: 'good',
    videoDuration: 30,
    messages: [],
    projects: [],
    exportCount: parseInt(document.body.dataset.exportCount || '0'),
    isProcessing: false,
    isGenerating: false
};

const QUALITY_TIERS = {
    good: {
        name: 'Good',
        model: 'Gen-3 Turbo',
        pricePerThirty: 4.00,
        costPerSecond: 0.05,
        description: 'Fast generation, solid quality for most content'
    },
    better: {
        name: 'Better',
        model: 'Gen-4 Turbo',
        pricePerThirty: 6.00,
        costPerSecond: 0.10,
        description: 'Enhanced detail and motion consistency'
    },
    best: {
        name: 'Best',
        model: 'Gen-4 Aleph',
        pricePerThirty: 8.00,
        costPerSecond: 0.15,
        description: 'Cinema-grade output with maximum fidelity'
    }
};

const QUALITY_DISCLAIMER = "Quality tier affects visual generation only. It won't change your video's direction, pacing, or message — just how sharp and polished the final output looks.";

function startNewProject() {
    state.currentProjectId = null;
    state.currentProjectName = null;
    state.currentMode = null;
    state.messages = [];
    document.getElementById('current-project-title').textContent = '';
    document.getElementById('project-name-text').textContent = 'Untitled Project';
    document.getElementById('chat-welcome').style.display = 'block';
    document.getElementById('chat-messages').classList.remove('active');
    document.getElementById('chat-messages').innerHTML = '';
    document.querySelectorAll('.project-card').forEach(c => c.classList.remove('active'));
}

function selectMode(mode) {
    state.currentMode = mode;
    document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('selected'));
    event.currentTarget.classList.add('selected');
    
    document.getElementById('chat-welcome').style.display = 'none';
    document.getElementById('chat-messages').classList.add('active');
    
    if (mode === 'remix') {
        addAIMessageWithQualitySelector();
    } else {
        const modeDescriptions = {
            clipper: "You selected Clipper mode. Share a long video or transcript and I'll extract the best moments to create professional clips.",
            simple: "You selected Simple Stock mode. Tell me your topic or script and I'll create a video using stock footage and AI-generated visuals."
        };
        addAIMessage(modeDescriptions[mode] + "\n\nWhat would you like to create?");
    }
}

function addAIMessageWithQualitySelector() {
    const container = document.getElementById('chat-messages');
    const msgEl = document.createElement('div');
    msgEl.className = 'message message-ai';
    
    const qualityCardsHtml = Object.entries(QUALITY_TIERS).map(([key, tier]) => `
        <div class="quality-card ${key === state.qualityTier ? 'selected' : ''}" onclick="selectQualityTier('${key}')">
            <div class="quality-tier-name">${tier.name}</div>
            <div class="quality-tier-model">${tier.model}</div>
            <div class="quality-tier-price">$${tier.pricePerThirty.toFixed(2)}<span class="quality-tier-unit">/30s</span></div>
            <div class="quality-tier-desc">${tier.description}</div>
        </div>
    `).join('');
    
    msgEl.innerHTML = `
        <div class="message-ai-avatar">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
            </svg>
        </div>
        <div class="message-ai-content">
            <p>You selected <strong>Remix mode</strong>. First, choose your quality tier:</p>
            
            <div class="quality-selector">
                <div class="quality-selector-title">Select Video Quality</div>
                <div class="quality-cards" id="quality-cards">
                    ${qualityCardsHtml}
                </div>
                <div class="quality-disclaimer">${QUALITY_DISCLAIMER}</div>
                <div class="quality-cost-estimate">
                    <span class="cost-label">Estimated cost for 30s video:</span>
                    <span class="cost-value" id="quality-cost-estimate">$${calculateCostEstimate(30).toFixed(2)}</span>
                </div>
            </div>
            
            <p style="margin-top: 16px;">Upload a reference video for the vibe you want, then tell me what content to create. I'll preserve the motion and structure while transforming the visuals.</p>
        </div>
    `;
    
    container.appendChild(msgEl);
    container.scrollTop = container.scrollHeight;
}

function selectQualityTier(tier) {
    state.qualityTier = tier;
    document.querySelectorAll('.quality-card').forEach(c => c.classList.remove('selected'));
    event.currentTarget.classList.add('selected');
    updateCostEstimate();
}

function calculateCostEstimate(durationSeconds) {
    const tier = QUALITY_TIERS[state.qualityTier];
    const runwayCost = tier.costPerSecond * durationSeconds;
    const shotstackCost = 0.20 * (durationSeconds / 30);
    const claudeCost = 0.50;
    const elevenLabsCost = 0.30;
    const stockCost = 0.10;
    return runwayCost + shotstackCost + claudeCost + elevenLabsCost + stockCost;
}

function updateCostEstimate() {
    const costEl = document.getElementById('quality-cost-estimate');
    if (costEl) {
        costEl.textContent = '$' + calculateCostEstimate(state.videoDuration).toFixed(2);
    }
}

function showToast(title, message, action = null, actionLabel = 'View', autoClose = true) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast';
    
    const actionHtml = action ? `<button class="toast-action" onclick="(${action})(); this.closest('.toast').remove();">${actionLabel}</button>` : '';
    
    toast.innerHTML = `
        <div class="toast-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
        </div>
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            <div class="toast-message">${message}</div>
        </div>
        ${actionHtml}
        <button class="toast-close" onclick="this.closest('.toast').remove()">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
        </button>
    `;
    
    container.appendChild(toast);
    
    if (autoClose) {
        setTimeout(() => {
            if (toast.parentNode) {
                toast.style.animation = 'slideIn 0.3s ease reverse';
                setTimeout(() => toast.remove(), 300);
            }
        }, 8000);
    }
    
    return toast;
}

function showGeneratingSpinner(projectId) {
    const card = document.querySelector(`[data-project-id="${projectId}"]`);
    if (card && !card.querySelector('.project-generating-spinner')) {
        const spinner = document.createElement('div');
        spinner.className = 'project-generating-spinner';
        card.appendChild(spinner);
    }
}

function hideGeneratingSpinner(projectId) {
    const card = document.querySelector(`[data-project-id="${projectId}"]`);
    if (card) {
        const spinner = card.querySelector('.project-generating-spinner');
        if (spinner) spinner.remove();
    }
}

function setGeneratingState(isGenerating, projectId = null) {
    state.isGenerating = isGenerating;
    if (projectId) {
        if (isGenerating) {
            showGeneratingSpinner(projectId);
        } else {
            hideGeneratingSpinner(projectId);
        }
    }
}

function onVideoGenerationComplete(projectId, videoUrl) {
    setGeneratingState(false, projectId);
    showToast(
        'Video Ready!',
        'Your video has been generated successfully.',
        `function() { window.location.href = '/project/${projectId}'; }`,
        'View',
        false
    );
}

function showProgressMessage(sceneNum, totalScenes, estimatedMinutes) {
    const progressText = `Generating scene ${sceneNum} of ${totalScenes}... ${estimatedMinutes > 0 ? `~${estimatedMinutes} min remaining` : 'Almost done'}`;
    return progressText;
}

const activeJobPolls = new Map();

async function createVideoJob(projectId, qualityTier, jobData) {
    try {
        const response = await fetch('/api/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                project_id: projectId,
                quality_tier: qualityTier,
                job_data: jobData
            })
        });
        
        const data = await response.json();
        
        if (data.ok) {
            setGeneratingState(true, projectId);
            startJobPolling(data.job.id, projectId);
            addAIMessage(`Video generation started! You can continue working while it processes. Quality: ${QUALITY_TIERS[qualityTier].name}`);
            return data.job;
        } else {
            addAIMessage(`Failed to start generation: ${data.error}`);
            return null;
        }
    } catch (err) {
        console.error('Failed to create job:', err);
        addAIMessage('Something went wrong. Please try again.');
        return null;
    }
}

function startJobPolling(jobId, projectId) {
    if (activeJobPolls.has(jobId)) return;
    
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/jobs/${jobId}`);
            const data = await response.json();
            
            if (!data.ok) {
                clearInterval(pollInterval);
                activeJobPolls.delete(jobId);
                return;
            }
            
            const job = data.job;
            
            if (job.status === 'completed') {
                clearInterval(pollInterval);
                activeJobPolls.delete(jobId);
                onVideoGenerationComplete(projectId, job.result_url);
            } else if (job.status === 'failed') {
                clearInterval(pollInterval);
                activeJobPolls.delete(jobId);
                setGeneratingState(false, projectId);
                showToast('Generation Failed', job.error_message || 'Please try again.', null, null, true);
            } else if (job.status === 'processing') {
                const progress = job.progress;
                if (progress && progress.message) {
                    updateProgressDisplay(projectId, progress.current, progress.total, progress.message);
                }
            }
        } catch (err) {
            console.error('Job polling error:', err);
        }
    }, 3000);
    
    activeJobPolls.set(jobId, pollInterval);
}

function updateProgressDisplay(projectId, current, total, message) {
    const card = document.querySelector(`[data-project-id="${projectId}"]`);
    if (card) {
        let progressEl = card.querySelector('.project-progress-text');
        if (!progressEl) {
            progressEl = document.createElement('div');
            progressEl.className = 'project-progress-text';
            progressEl.style.cssText = 'font-size: 10px; color: var(--text-dim); margin-top: 2px;';
            card.querySelector('.project-info')?.appendChild(progressEl);
        }
        progressEl.textContent = message || `${current}/${total}`;
    }
}

async function checkActiveJobs() {
    try {
        const response = await fetch('/api/jobs?active=true');
        const data = await response.json();
        
        if (data.ok && data.jobs.length > 0) {
            data.jobs.forEach(job => {
                if (!activeJobPolls.has(job.id)) {
                    setGeneratingState(true, job.project_id);
                    startJobPolling(job.id, job.project_id);
                }
            });
        }
    } catch (err) {
        console.error('Failed to check active jobs:', err);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(checkActiveJobs, 1000);
});

function selectProject(projectId) {
    state.currentProjectId = projectId;
    document.querySelectorAll('.project-card').forEach(c => c.classList.remove('active'));
    document.querySelector(`[data-project-id="${projectId}"]`)?.classList.add('active');
    
    loadProjectChat(projectId);
}

async function loadProjectChat(projectId) {
    try {
        const response = await fetch(`/api/project/${projectId}/chat`);
        const data = await response.json();
        
        if (data.ok) {
            state.messages = data.messages || [];
            state.currentMode = data.mode;
            state.currentProjectName = data.name || 'Untitled Project';
            document.getElementById('current-project-title').textContent = data.name;
            document.getElementById('project-name-text').textContent = state.currentProjectName;
            document.getElementById('chat-welcome').style.display = 'none';
            document.getElementById('chat-messages').classList.add('active');
            renderMessages();
        }
    } catch (err) {
        console.error('Failed to load project chat:', err);
    }
}

function startProjectRename() {
    if (!state.currentProjectId) return;
    
    const container = document.getElementById('project-name-edit');
    const currentName = state.currentProjectName || 'Untitled Project';
    
    container.innerHTML = `<input type="text" class="project-name-input" id="project-name-input" value="${escapeHtml(currentName)}" onblur="finishProjectRename()" onkeydown="if(event.key==='Enter')finishProjectRename();if(event.key==='Escape')cancelProjectRename();">`;
    
    const input = document.getElementById('project-name-input');
    input.focus();
    input.select();
}

async function finishProjectRename() {
    const input = document.getElementById('project-name-input');
    if (!input) return;
    
    const newName = input.value.trim() || 'Untitled Project';
    
    if (state.currentProjectId && newName !== state.currentProjectName) {
        try {
            const response = await fetch(`/api/project/${state.currentProjectId}/rename`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: newName })
            });
            
            const data = await response.json();
            if (data.ok) {
                state.currentProjectName = newName;
                loadProjects();
            }
        } catch (err) {
            console.error('Failed to rename project:', err);
        }
    }
    
    restoreProjectNameDisplay(newName);
}

function cancelProjectRename() {
    restoreProjectNameDisplay(state.currentProjectName || 'Untitled Project');
}

function restoreProjectNameDisplay(name) {
    const container = document.getElementById('project-name-edit');
    container.innerHTML = `
        <span class="project-name-text" id="project-name-text">${escapeHtml(name)}</span>
        <svg class="project-name-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
        </svg>
    `;
}

function renderMessages() {
    const container = document.getElementById('chat-messages');
    container.innerHTML = state.messages.map(msg => {
        if (msg.role === 'user') {
            return `<div class="message message-user">
                <div class="message-content">
                    <div class="message-text">${escapeHtml(msg.content)}</div>
                </div>
            </div>`;
        } else {
            return `<div class="message message-ai">
                <div class="message-ai-avatar">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0d0d0d" stroke-width="2">
                        <circle cx="12" cy="12" r="10"></circle>
                    </svg>
                </div>
                <div class="message-content">
                    <div class="message-text">${formatAIMessage(msg.content)}</div>
                </div>
            </div>`;
        }
    }).join('');
    
    container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatAIMessage(text) {
    return escapeHtml(text).replace(/\n/g, '<br>');
}

function addUserMessage(text) {
    state.messages.push({ role: 'user', content: text });
    renderMessages();
}

function addAIMessage(text) {
    state.messages.push({ role: 'assistant', content: text });
    renderMessages();
}

async function sendMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    
    input.value = '';
    autoResize(input);
    
    addUserMessage(text);
    
    if (!state.currentMode && !state.currentProjectId) {
        document.getElementById('chat-welcome').style.display = 'none';
        document.getElementById('chat-messages').classList.add('active');
        state.currentMode = 'auto';
    }
    
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: text,
                project_id: state.currentProjectId,
                mode: state.currentMode
            })
        });
        
        const data = await response.json();
        
        if (data.ok) {
            if (data.project_id && !state.currentProjectId) {
                state.currentProjectId = data.project_id;
                state.currentProjectName = data.project_name || 'New Project';
                document.getElementById('current-project-title').textContent = state.currentProjectName;
                document.getElementById('project-name-text').textContent = state.currentProjectName;
                loadProjects();
            }
            
            if (data.response) {
                addAIMessage(data.response);
            }
            
            if (data.needs_clarification) {
                addAIMessage(data.clarification_question);
            }
            
            if (data.trigger_generation && data.job_data) {
                const selectedTier = state.selectedQualityTier || 'good';
                createVideoJob(state.currentProjectId, selectedTier, data.job_data);
            }
            
            if (data.processing) {
                showProcessingBanner(data.job_id);
            }
        } else {
            addAIMessage("I encountered an issue. " + (data.error || "Please try again."));
        }
    } catch (err) {
        addAIMessage("Connection error. Please check your network and try again.");
    }
}

function handleInputKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

function autoResize(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px';
}

async function loadProjects() {
    try {
        const response = await fetch('/api/projects');
        const data = await response.json();
        
        if (data.ok) {
            state.projects = data.projects || [];
            renderProjects();
            updateAutoGenStatus();
        }
    } catch (err) {
        console.error('Failed to load projects:', err);
    }
}

function renderProjects() {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const weekAgo = new Date(today);
    weekAgo.setDate(weekAgo.getDate() - 7);
    
    const todayProjects = [];
    const yesterdayProjects = [];
    const olderProjects = [];
    
    state.projects.forEach(project => {
        const projectDate = new Date(project.created_at);
        projectDate.setHours(0, 0, 0, 0);
        
        if (projectDate >= today) {
            todayProjects.push(project);
        } else if (projectDate >= yesterday) {
            yesterdayProjects.push(project);
        } else if (projectDate >= weekAgo) {
            olderProjects.push(project);
        }
    });
    
    document.getElementById('today-list').innerHTML = renderProjectList(todayProjects);
    document.getElementById('yesterday-list').innerHTML = renderProjectList(yesterdayProjects);
    document.getElementById('older-list').innerHTML = renderProjectList(olderProjects);
    
    document.getElementById('today-projects').style.display = todayProjects.length ? 'block' : 'none';
    document.getElementById('yesterday-projects').style.display = yesterdayProjects.length ? 'block' : 'none';
    document.getElementById('older-projects').style.display = olderProjects.length ? 'block' : 'none';
}

function renderProjectList(projects) {
    return projects.map(p => `
        <div class="project-card ${state.currentProjectId === p.id ? 'active' : ''}" 
             data-project-id="${p.id}"
             onclick="selectProject(${p.id})">
            <div class="project-thumb">
                ${p.thumbnail ? `<img src="${p.thumbnail}" alt="">` : p.name.charAt(0).toUpperCase()}
            </div>
            <div class="project-info">
                <div class="project-name">${escapeHtml(p.name)}</div>
                <div class="project-meta">${p.duration || '0'}s • ${p.mode || 'Draft'}</div>
            </div>
        </div>
    `).join('');
}

function updateAutoGenStatus() {
    const count = state.exportCount;
    const unlocked = count >= 5;
    
    document.getElementById('auto-gen-count').textContent = `${Math.min(count, 5)}/5`;
    document.getElementById('auto-gen-bar').style.width = `${Math.min(count, 5) * 20}%`;
    
    const btn = document.getElementById('auto-gen-btn');
    const btnText = document.getElementById('auto-gen-btn-text');
    
    if (unlocked) {
        btn.classList.add('unlocked');
        btnText.textContent = 'CHECK PREVIEWS';
    } else {
        btn.classList.remove('unlocked');
        btnText.textContent = 'LOCKED';
    }
}

function openAutoGenerator() {
    if (state.exportCount >= 5) {
        window.location.href = '/auto-generator';
    }
}

function showProcessingBanner(jobId) {
    state.isProcessing = true;
    document.getElementById('processing-banner').classList.add('active');
    pollJobStatus(jobId);
}

async function pollJobStatus(jobId) {
    try {
        const response = await fetch(`/api/job/${jobId}/status`);
        const data = await response.json();
        
        if (data.status === 'complete') {
            document.getElementById('processing-banner').classList.remove('active');
            state.isProcessing = false;
            addAIMessage("Your video is ready! You can preview it now or export the final version.");
        } else if (data.status === 'error') {
            document.getElementById('processing-banner').classList.remove('active');
            state.isProcessing = false;
            addAIMessage("There was an issue creating your video. Let me try a different approach.");
        } else {
            document.getElementById('processing-fill').style.width = `${data.progress || 0}%`;
            document.getElementById('processing-text').textContent = data.message || 'Processing...';
            setTimeout(() => pollJobStatus(jobId), 2000);
        }
    } catch (err) {
        console.error('Failed to poll job status:', err);
    }
}

function closeAndNotify() {
    addAIMessage("I'll email you when your video is ready. You can close this tab now.");
    document.getElementById('processing-banner').classList.remove('active');
}

function keepWatching() {
    document.getElementById('processing-banner').classList.remove('active');
}

function toggleUserMenu() {
    // User profile menu - show logout option
    if (confirm('Log out of Framd?')) {
        window.location.href = '/logout';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadProjects();
    updateAutoGenStatus();
    setupDragAndDrop();
});

// File drag and drop anywhere on page
function setupDragAndDrop() {
    const overlay = document.getElementById('dropzone-overlay');
    let dragCounter = 0;
    
    document.addEventListener('dragenter', (e) => {
        e.preventDefault();
        dragCounter++;
        if (e.dataTransfer.types.includes('Files')) {
            overlay.classList.add('active');
        }
    });
    
    document.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dragCounter--;
        if (dragCounter === 0) {
            overlay.classList.remove('active');
        }
    });
    
    document.addEventListener('dragover', (e) => {
        e.preventDefault();
    });
    
    document.addEventListener('drop', (e) => {
        e.preventDefault();
        dragCounter = 0;
        overlay.classList.remove('active');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleDroppedFile(files[0]);
        }
    });
}

async function handleDroppedFile(file) {
    const validTypes = ['video/', 'audio/', 'image/'];
    const isValid = validTypes.some(type => file.type.startsWith(type));
    
    if (!isValid) {
        addAIMessage("I can only accept video, audio, or image files. Please try a different file.");
        return;
    }
    
    // Show the chat area if not visible
    document.getElementById('chat-welcome').style.display = 'none';
    document.getElementById('chat-messages').classList.add('active');
    
    // Add user message about the file
    const fileType = file.type.split('/')[0];
    addUserMessage(`[Uploaded ${fileType}: ${file.name}]`);
    
    // Upload the file
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        addAIMessage("Uploading your file...");
        
        const response = await fetch('/upload-media', {
            method: 'POST',
            body: formData
        });
        
        if (response.ok) {
            const data = await response.json();
            // Remove the "uploading" message
            const messages = document.getElementById('chat-messages');
            if (messages.lastChild) {
                messages.removeChild(messages.lastChild);
            }
            
            if (data.transcript) {
                addAIMessage(`Got it! I've analyzed your ${fileType}. What would you like me to create from this?`);
            } else {
                addAIMessage(`I've received your ${fileType}. What would you like me to do with it?`);
            }
            
            // Store the file info in state
            state.uploadedFile = data;
        } else {
            addAIMessage("There was an issue uploading your file. Please try again.");
        }
    } catch (err) {
        console.error('Upload error:', err);
        addAIMessage("Upload failed. Please check your connection and try again.");
    }
}
