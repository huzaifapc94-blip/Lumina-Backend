// State Registry & Storage Keys
const STORAGE_KEY = 'lumina_chat_sessions';
let modelsRegistry = [];
let sessions = {};
let activeSessionId = null;
let activeAbortController = null;
let currentModelId = 'nvidia/nemotron-3-ultra-550b-a55b';
let isNearBottom = true;
let pendingAttachments = [];


// DOM Elements cache
const sidebar = document.getElementById('sidebar');
const sidebarToggle = document.getElementById('sidebar-toggle');
const threadList = document.getElementById('thread-list');
const newChatBtn = document.getElementById('new-chat-btn');
const statusIndicator = document.getElementById('status-indicator');
const statusText = document.getElementById('status-text');
const engineSelect = document.getElementById('engine-select');
const engineDetails = document.getElementById('engine-details');
const chatCanvas = document.getElementById('chat-canvas');
const emptyState = document.getElementById('empty-state');
const stopStreamPanel = document.getElementById('stop-stream-panel');
const stopStreamBtn = document.getElementById('stop-stream-btn');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const sidebarOverlay = document.getElementById('sidebar-overlay');
const fileInput = document.getElementById('file-input');
const attachBtn = document.getElementById('attach-btn');
const attachmentPreview = document.getElementById('attachment-preview');

// Initial Setup
document.addEventListener('DOMContentLoaded', async () => {
    initApp();
});

async function initApp() {
    setupEventListeners();
    loadSessionsFromStorage();
    await checkServerHealth();
    await loadModelsRegistry();
    renderSidebar();

    // Auto-focus input
    chatInput.focus();
}

// Check backend status
async function checkServerHealth() {
    statusIndicator.className = 'status-indicator checking';
    statusText.textContent = 'Connecting...';
    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        if (data.status === 'healthy' && data.api_key_configured) {
            statusIndicator.className = 'status-indicator online';
            statusText.textContent = 'Lumina Server: Ready';
        } else if (data.status === 'healthy' && !data.api_key_configured) {
            statusIndicator.className = 'status-indicator offline';
            statusText.textContent = 'Missing API Key';
            console.error('AGENTROUTER_API_KEY is not configured on the server.');
        } else {
            statusIndicator.className = 'status-indicator offline';
            statusText.textContent = 'Server Error';
        }
    } catch (error) {
        statusIndicator.className = 'status-indicator offline';
        statusText.textContent = 'Offline';
        console.error('Failed to contact Lumina backend:', error);
    }
}

// Retrieve models from backend and populate selector dropdown
async function loadModelsRegistry() {
    try {
        const response = await fetch('/api/models');
        modelsRegistry = await response.json();

        // Populating dropdown
        engineSelect.innerHTML = '';
        modelsRegistry.forEach((model) => {
            const option = document.createElement('option');
            option.value = model.model_id;
            option.textContent = model.display_name;
            engineSelect.appendChild(option);
        });

        // Set default selection
        if (modelsRegistry.length > 0) {
            currentModelId = modelsRegistry[0].model_id;
            engineSelect.value = currentModelId;
            updateModelDetails(currentModelId);
        }
    } catch (error) {
        console.error('Failed to load models list:', error);
        // Fallback static option
        engineSelect.innerHTML = '<option value="nvidia/nemotron-3-ultra-550b-a55b">Nemotron 3 Ultra (550B)</option>';
        currentModelId = 'nvidia/nemotron-3-ultra-550b-a55b';
        updateModelDetails('nvidia/nemotron-3-ultra-550b-a55b');
    }
}

// Event Listeners setup
function setupEventListeners() {
    // Model Select
    engineSelect.addEventListener('change', (e) => {
        currentModelId = e.target.value;
        updateModelDetails(currentModelId);
    });

    // Sidebar Slide Drawer for mobile
    sidebarToggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        sidebarOverlay.classList.toggle('active');
    });

    // Close sidebar if user clicks overlay
    sidebarOverlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        sidebarOverlay.classList.remove('active');
    });

    // Passive scroll listener for performance and smart auto-scrolling
    chatCanvas.addEventListener('scroll', () => {
        const threshold = 120; // px
        isNearBottom = (chatCanvas.scrollHeight - chatCanvas.clientHeight - chatCanvas.scrollTop) < threshold;
    }, { passive: true });

    // New Chat
    newChatBtn.addEventListener('click', () => {
        startNewChat();
    });

    // Stop Stream
    stopStreamBtn.addEventListener('click', () => {
        if (activeAbortController) {
            activeAbortController.abort();
        }
    });

    // Textarea input auto-grow & send bindings
    chatInput.addEventListener('input', () => {
        autoGrowTextarea(chatInput);
        updateSendBtnState();
    });

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submitMessage();
        }
    });

    sendBtn.addEventListener('click', () => {
        submitMessage();
    });

    // File Attachment
    attachBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        fileInput.click();
    });

    fileInput.addEventListener('change', () => {
        handleFileSelection(fileInput.files);
        fileInput.value = '';
    });

    // Drag and drop support on input container
    const inputContainer = document.querySelector('.input-container');
    inputContainer.addEventListener('dragover', (e) => {
        e.preventDefault();
        inputContainer.style.borderColor = 'var(--accent-indigo)';
    });
    inputContainer.addEventListener('dragleave', () => {
        inputContainer.style.borderColor = '';
    });
    inputContainer.addEventListener('drop', (e) => {
        e.preventDefault();
        inputContainer.style.borderColor = '';
        if (e.dataTransfer.files.length > 0) {
            handleFileSelection(e.dataTransfer.files);
        }
    });

    // Quick Starts Prompts
    document.querySelectorAll('.quick-start-card').forEach(card => {
        card.addEventListener('click', () => {
            const prompt = card.getAttribute('data-prompt');
            chatInput.value = prompt;
            autoGrowTextarea(chatInput);
            sendBtn.disabled = false;
            submitMessage();
        });
    });

    // Auto-close sidebar on resize/orientation change
    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            sidebar.classList.remove('open');
            sidebarOverlay.classList.remove('active');
        }
    });
}

// Update UI detailing for active engine
function updateModelDetails(modelId) {
    const model = modelsRegistry.find(m => m.model_id === modelId);
    if (model) {
        engineDetails.textContent = model.optimizations;
    } else {
        engineDetails.textContent = 'Multi-model routing gateway';
    }
}

// Handles input area resizing
function autoGrowTextarea(el) {
    el.style.height = 'auto';
    el.style.height = (el.scrollHeight) + 'px';
}

function updateSendBtnState() {
    sendBtn.disabled = !chatInput.value.trim() && pendingAttachments.length === 0;
}

function handleFileSelection(files) {
    for (const file of files) {
        if (!file.type.startsWith('image/')) continue;
        if (file.size > 20 * 1024 * 1024) continue;

        const reader = new FileReader();
        reader.onload = (e) => {
            pendingAttachments.push({
                name: file.name,
                dataUrl: e.target.result
            });
            renderAttachmentPreviews();
            updateSendBtnState();
        };
        reader.readAsDataURL(file);
    }
}

function renderAttachmentPreviews() {
    attachmentPreview.innerHTML = '';
    pendingAttachments.forEach((att, idx) => {
        const thumb = document.createElement('div');
        thumb.className = 'attachment-thumb';
        thumb.innerHTML = `
            <img src="${att.dataUrl}" alt="${escapeHtml(att.name)}">
            <button class="remove-attachment" data-idx="${idx}">&times;</button>
        `;
        thumb.querySelector('.remove-attachment').addEventListener('click', () => {
            pendingAttachments.splice(idx, 1);
            renderAttachmentPreviews();
            updateSendBtnState();
        });
        attachmentPreview.appendChild(thumb);
    });
}

function clearAttachments() {
    pendingAttachments = [];
    attachmentPreview.innerHTML = '';
}

// Create new clean state
function startNewChat() {
    activeSessionId = null;
    chatCanvas.innerHTML = '';
    chatCanvas.appendChild(emptyState);
    emptyState.style.display = 'flex';
    document.querySelectorAll('.thread-item').forEach(item => item.classList.remove('active'));
    chatInput.value = '';
    autoGrowTextarea(chatInput);
    sendBtn.disabled = true;
    clearAttachments();
    chatInput.focus();
    closeSidebarOnMobile();
}

// Load threads from local storage
function loadSessionsFromStorage() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            sessions = JSON.parse(stored);
        } else {
            sessions = {};
        }
    } catch (e) {
        console.error('Error loading history from storage:', e);
        sessions = {};
    }
}

// Save threads to local storage
function saveSessionsToStorage() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
    } catch (e) {
        console.error('Error saving history to storage:', e);
    }
}

// Render Sidebar List
function renderSidebar() {
    threadList.innerHTML = '';
    const sortedSessionIds = Object.keys(sessions).sort((a, b) => b - a); // reverse chronological

    if (sortedSessionIds.length === 0) {
        const emptyHistory = document.createElement('div');
        emptyHistory.style.padding = '20px';
        emptyHistory.style.color = 'var(--text-muted)';
        emptyHistory.style.fontSize = '0.8rem';
        emptyHistory.style.textAlign = 'center';
        emptyHistory.textContent = 'No past conversations';
        threadList.appendChild(emptyHistory);
        return;
    }

    sortedSessionIds.forEach(id => {
        const session = sessions[id];
        const item = document.createElement('div');
        item.className = `thread-item ${activeSessionId === id ? 'active' : ''}`;
        item.setAttribute('data-id', id);

        item.innerHTML = `
            <div class="thread-title-container">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="thread-icon"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                <span class="thread-title">${escapeHtml(session.title)}</span>
            </div>
            <button class="delete-thread-btn" title="Delete conversation">
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
        `;

        // Load thread on click
        item.addEventListener('click', (e) => {
            if (e.target.closest('.delete-thread-btn')) {
                e.stopPropagation();
                deleteSession(id);
            } else {
                selectSession(id);
                closeSidebarOnMobile();
            }
        });

        threadList.appendChild(item);
    });
}

// Delete selected session
function deleteSession(id) {
    delete sessions[id];
    saveSessionsToStorage();
    renderSidebar();

    if (activeSessionId === id) {
        startNewChat();
    }
}

// Select/Load selected session
function selectSession(id) {
    activeSessionId = id;
    renderSidebar();

    const session = sessions[id];
    if (!session) return;

    // Hide empty landing state
    emptyState.style.display = 'none';
    chatCanvas.innerHTML = '';

    // Set engine selection value to match thread's first configuration (optional convenience)
    if (session.modelId) {
        currentModelId = session.modelId;
        engineSelect.value = currentModelId;
        updateModelDetails(currentModelId);
    }

    // Render bubbles
    session.messages.forEach(msg => {
        appendMessageBubble(msg.role, msg.content, msg.modelId || session.modelId, msg.images, msg.reasoning);
    });

    // Scroll to bottom
    scrollToBottom(true);
}

// Creates message bubble DOM node
function appendMessageBubble(role, content, modelId, images, reasoning) {
    const row = document.createElement('div');
    row.className = `message-row ${role}`;

    const wrapper = document.createElement('div');
    wrapper.className = 'message-wrapper';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';

    if (role === 'user') {
        if (images && images.length > 0) {
            const imgContainer = document.createElement('div');
            imgContainer.className = 'message-images';
            images.forEach(src => {
                const img = document.createElement('img');
                img.src = src;
                img.className = 'message-image';
                img.addEventListener('click', () => openLightbox(src));
                imgContainer.appendChild(img);
            });
            bubble.appendChild(imgContainer);
        }
        const textNode = document.createTextNode(typeof content === 'string' ? content : '');
        if (content) bubble.appendChild(textNode);
    } else if (role === 'assistant') {
        bubble.innerHTML = renderMarkdown(typeof content === 'string' ? content : '');
    }

    wrapper.appendChild(bubble);

    // Render a saved reasoning panel (from a prior reasoning-model turn) above the bubble.
    if (role === 'assistant' && reasoning) {
        const panel = createReasoningPanel(bubble);
        panel.body.innerHTML = renderMarkdown(reasoning);
    }

    if (role === 'assistant' && modelId) {
        const meta = document.createElement('div');
        meta.className = 'message-meta';
        const modelName = getModelDisplayName(modelId);
        meta.innerHTML = `<span class="engine-badge">[Engine: ${escapeHtml(modelName)}]</span>`;
        wrapper.appendChild(meta);
    }

    row.appendChild(wrapper);
    chatCanvas.appendChild(row);
    return bubble;
}

// Builds a collapsible "Thinking" panel for reasoning-model output and
// inserts it directly above the given answer bubble. Returns { panel, body }.
function createReasoningPanel(bubble) {
    const panel = document.createElement('details');
    panel.className = 'reasoning-panel';
    panel.open = true;

    const summary = document.createElement('summary');
    summary.className = 'reasoning-summary';
    summary.innerHTML = `
        <svg class="reasoning-icon" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.663 17h4.673M12 3v1M3.34 7l.867.5M20.66 7l-.867.5M12 21a7 7 0 0 1-4-12.75A6.97 6.97 0 0 1 12 5a7 7 0 0 1 4 12.75V19a2 2 0 0 1-2 2h-2z"></path></svg>
        <span>Thinking</span>`;
    panel.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'reasoning-body';
    panel.appendChild(body);

    // Insert the panel just before the answer bubble within the message wrapper.
    bubble.parentNode.insertBefore(panel, bubble);

    return { panel, body };
}

// Utility to get display name from ID
function getModelDisplayName(modelId) {
    const model = modelsRegistry.find(m => m.model_id === modelId);
    return model ? model.display_name : modelId.split('/').pop().toUpperCase();
}

function openLightbox(src) {
    const overlay = document.createElement('div');
    overlay.className = 'image-lightbox';
    overlay.innerHTML = `<img src="${src}" alt="Full size">`;
    overlay.addEventListener('click', () => overlay.remove());
    document.body.appendChild(overlay);
}

function scrollToBottom(force = false) {
    if (force || isNearBottom) {
        requestAnimationFrame(() => {
            chatCanvas.scrollTop = chatCanvas.scrollHeight;
        });
    }
}

function closeSidebarOnMobile() {
    if (window.innerWidth <= 768) {
        sidebar.classList.remove('open');
        sidebarOverlay.classList.remove('active');
    }
}

function normalizeStreamText(value) {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(normalizeStreamText).join('');
    if (typeof value === 'object') {
        return normalizeStreamText(value.text ?? value.content ?? value.output_text ?? value.value);
    }
    return String(value);
}

// Main Submit Message Loop
async function submitMessage() {
    const messageContent = chatInput.value.trim();
    const currentImages = pendingAttachments.map(a => a.dataUrl);
    if (!messageContent && currentImages.length === 0) return;

    // Remove empty landing view
    if (emptyState.parentNode === chatCanvas || emptyState.style.display !== 'none') {
        emptyState.style.display = 'none';
        chatCanvas.innerHTML = '';
    }

    // Initialize session if empty
    if (!activeSessionId) {
        activeSessionId = Date.now().toString();
        const titleSource = messageContent || 'Image analysis';
        const words = titleSource.split(/\s+/);
        let title = words.slice(0, 4).join(' ');
        if (title.length > 28) title = title.slice(0, 25) + '...';

        sessions[activeSessionId] = {
            id: activeSessionId,
            title: title || 'New Chat',
            modelId: currentModelId,
            messages: []
        };
    }

    const session = sessions[activeSessionId];

    // Store images under 2MB per image for localStorage persistence
    const storedImages = currentImages.filter(d => d.length < 2 * 1024 * 1024);

    session.messages.push({
        role: 'user',
        content: messageContent,
        images: storedImages.length > 0 ? storedImages : undefined
    });
    appendMessageBubble('user', messageContent, null, currentImages);
    saveSessionsToStorage();
    renderSidebar();

    // Reset Input Box
    chatInput.value = '';
    autoGrowTextarea(chatInput);
    clearAttachments();
    sendBtn.disabled = true;

    // Lock UI input controls
    chatInput.disabled = true;

    // Show Stop Stream indicator
    stopStreamPanel.classList.add('active');

    // Prep Assistant Placeholder bubble
    const assistantBubble = appendMessageBubble('assistant', '', currentModelId);

    // Add pulsing typing dot indicator
    const streamIndicator = document.createElement('span');
    streamIndicator.className = 'streaming-indicator';
    streamIndicator.innerHTML = `
        <span class="streaming-dot"></span>
        <span class="streaming-dot"></span>
        <span class="streaming-dot"></span>
    `;
    assistantBubble.appendChild(streamIndicator);

    scrollToBottom(true);

    // Initialize abort token
    activeAbortController = new AbortController();
    let assistantResponseText = '';
    let reasoningText = '';
    let reasoningPanel = null;

    const renderAssistantToken = (token) => {
        const text = normalizeStreamText(token);
        if (!text) return;

        // Clear initial typing indicator if first token
        if (assistantResponseText === '') {
            assistantBubble.innerHTML = '';
            // Collapse the thinking panel once the answer begins.
            if (reasoningPanel) reasoningPanel.panel.open = false;
        }

        assistantResponseText += text;

        // Re-render markdown live with typing cursor appended
        assistantBubble.innerHTML = renderMarkdown(assistantResponseText);

        // Append dynamic dot indicators during stream activity
        const tempIndicator = streamIndicator.cloneNode(true);
        assistantBubble.appendChild(tempIndicator);

        scrollToBottom();
    };

    const renderReasoningToken = (token) => {
        const text = normalizeStreamText(token);
        if (!text) return;

        // Reasoning models may stream their thinking separately. Render it
        // above the answer and collapse it once answer text arrives.
        if (reasoningText === '') {
            reasoningPanel = createReasoningPanel(assistantBubble);
        }
        reasoningText += text;
        reasoningPanel.body.innerHTML = renderMarkdown(reasoningText);
        scrollToBottom();
    };

    const processSseLine = (line) => {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(':') || !trimmed.startsWith('data:')) return;

        const dataJson = trimmed.slice(5).trim();
        if (!dataJson || dataJson === '[DONE]') return;

        let parsed;
        try {
            parsed = JSON.parse(dataJson);
        } catch (err) {
            console.warn('Skipping malformed SSE frame:', dataJson, err);
            return;
        }

        if (parsed.error) {
            throw new Error(normalizeStreamText(parsed.error));
        }
        if (parsed.reasoning) {
            renderReasoningToken(parsed.reasoning);
            return;
        }
        renderAssistantToken(parsed.token ?? parsed.content ?? parsed.text ?? parsed.output_text);
    };

    let isErrorOccurred = false;

    try {
        // Collect history message payload, filtering out previous error messages
        const historyPayload = session.messages.slice(0, -1)
            .filter(m => !(m.role === 'assistant' && typeof m.content === 'string' && m.content.includes('[Connection Error:')))
            .map(m => {
                if (m.images && m.images.length > 0) {
                    const parts = m.images.map(url => ({
                        type: 'image_url',
                        image_url: { url }
                    }));
                    if (m.content) parts.push({ type: 'text', text: m.content });
                    return { role: m.role, content: parts };
                }
                return { role: m.role, content: m.content };
            });

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: messageContent,
                model_id: currentModelId,
                history: historyPayload,
                images: currentImages.length > 0 ? currentImages : undefined
            }),
            signal: activeAbortController.signal
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Failed connection to backend proxy.' }));
            throw new Error(errorData.detail || `Server error (${response.status})`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // save incomplete line back to buffer

            for (const line of lines) {
                processSseLine(line);
            }
        }

        buffer += decoder.decode();
        if (buffer.trim()) {
            buffer.split('\n').forEach(processSseLine);
        }

        if (!assistantResponseText && !reasoningText) {
            throw new Error('The model completed, but no text was returned by the stream.');
        }

    } catch (error) {
        console.error('Streaming connection error:', error);

        // Remove trailing typing dots if any
        const indicators = assistantBubble.querySelectorAll('.streaming-indicator');
        indicators.forEach(i => i.remove());

        if (error.name === 'AbortError') {
            assistantResponseText += '\n\n*(Generation stopped by user)*';
        } else {
            isErrorOccurred = true;
            assistantResponseText += `\n\n**[Connection Error: ${error.message}]**`;
        }

        assistantBubble.innerHTML = renderMarkdown(assistantResponseText);
    } finally {
        // Remove typing indicators
        const indicators = assistantBubble.querySelectorAll('.streaming-indicator');
        indicators.forEach(i => i.remove());

        if (!assistantResponseText && reasoningText) {
            assistantResponseText = reasoningText;
            reasoningText = '';
            if (reasoningPanel) reasoningPanel.panel.remove();
            assistantBubble.innerHTML = renderMarkdown(assistantResponseText);
        }

        // Save assistant completion to sessions state only if successful or stopped by user
        if ((assistantResponseText || reasoningText) && !isErrorOccurred) {
            session.messages.push({
                role: 'assistant',
                content: assistantResponseText,
                reasoning: reasoningText || undefined,
                modelId: currentModelId
            });
            saveSessionsToStorage();
        }

        // Release UI locks
        chatInput.disabled = false;
        stopStreamPanel.classList.remove('active');
        activeAbortController = null;

        // Refocus textarea
        chatInput.focus();
    }
}
