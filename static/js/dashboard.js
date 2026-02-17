// ── Guard ──────────────────────────────────────────
if (!API.isAuthenticated()) {
  window.location.href = '/';
}

// ── State ──────────────────────────────────────────
let agents = [];
let selectedAgent = null;
let editingAgent = null; // null = create, id = edit
let currentView = 'detail';
let conversationId = null;

// ── DOM refs ───────────────────────────────────────
const $agentList = document.getElementById('agent-list');
const $mainTitle = document.getElementById('main-title');
const $mainBody = document.getElementById('main-body');
const $viewEmpty = document.getElementById('view-empty');
const $viewDetail = document.getElementById('view-detail');
const $viewChat = document.getElementById('view-chat');
const $btnEdit = document.getElementById('btn-edit');
const $btnDelete = document.getElementById('btn-delete');
const $modal = document.getElementById('modal');
const $modalTitle = document.getElementById('modal-title');
const $agentForm = document.getElementById('agent-form');
const $chatMessages = document.getElementById('chat-messages');
const $chatInput = document.getElementById('chat-input');
const $userName = document.getElementById('user-name');

// ── Init ───────────────────────────────────────────
$userName.textContent = localStorage.getItem('user_name') || 'User';
loadAgents();

// ── Load Agents ────────────────────────────────────
async function loadAgents() {
  try {
    agents = await API.listAgents();
    renderAgentList();
  } catch (err) {
    console.error('Failed to load agents:', err);
  }
}

function renderAgentList() {
  $agentList.innerHTML = agents.map(a => `
    <div class="agent-item ${selectedAgent && selectedAgent.id === a.id ? 'active' : ''}"
         data-id="${a.id}">
      <div class="agent-item-name">${esc(a.name)}</div>
      <div class="agent-item-desc">${esc(a.description)}</div>
    </div>
  `).join('');

  $agentList.querySelectorAll('.agent-item').forEach(el => {
    el.addEventListener('click', () => selectAgent(el.dataset.id));
  });
}

// ── Select Agent ───────────────────────────────────
function selectAgent(id) {
  selectedAgent = agents.find(a => a.id === id) || null;
  conversationId = null;
  $chatMessages.innerHTML = '';
  renderAgentList();
  renderView();
}

function renderView() {
  if (!selectedAgent) {
    $viewEmpty.style.display = '';
    $viewDetail.style.display = 'none';
    $viewChat.style.display = 'none';
    $btnEdit.style.display = 'none';
    $btnDelete.style.display = 'none';
    $mainTitle.textContent = 'Dashboard';
    return;
  }

  $mainTitle.textContent = selectedAgent.name;
  $btnEdit.style.display = '';
  $btnDelete.style.display = '';
  $viewEmpty.style.display = 'none';

  if (currentView === 'detail') {
    $viewDetail.style.display = '';
    $viewChat.style.display = 'none';
    renderDetail();
  } else {
    $viewDetail.style.display = 'none';
    $viewChat.style.display = '';
  }
}

function renderDetail() {
  const a = selectedAgent;
  $viewDetail.innerHTML = `
    <div class="detail-section">
      <h3>Model</h3>
      <span class="badge badge-accent">${esc(a.model)}</span>
    </div>
    <div class="detail-section">
      <h3>Description</h3>
      <div class="detail-value">${esc(a.description)}</div>
    </div>
    <div class="detail-section">
      <h3>Instruction</h3>
      <div class="detail-value">${esc(a.instruction)}</div>
    </div>
    <div class="detail-section">
      <h3>Tools</h3>
      <div class="detail-value">${a.tools && a.tools.length
        ? a.tools.map(t => `<span class="badge badge-accent">${esc(t.name || 'unknown')}</span> <span style="color:var(--text-muted);font-size:0.8125rem">${esc(t.description || '')}</span><br>`).join('')
        : '<span class="badge">None configured</span>'}</div>
    </div>
    <div class="detail-section">
      <h3>Created</h3>
      <div class="detail-value">${new Date(a.created_at).toLocaleString()}</div>
    </div>
  `;
}

// ── View Toggle ────────────────────────────────────
document.querySelectorAll('.view-toggle button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.view-toggle button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentView = btn.dataset.view;
    renderView();
  });
});

// ── Modal (Create / Edit) ──────────────────────────
document.getElementById('btn-new-agent').addEventListener('click', () => openModal(null));
$btnEdit.addEventListener('click', () => {
  if (selectedAgent) openModal(selectedAgent);
});

document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-cancel').addEventListener('click', closeModal);
$modal.addEventListener('click', (e) => {
  if (e.target === $modal) closeModal();
});

function openModal(agent) {
  editingAgent = agent ? agent.id : null;
  $modalTitle.textContent = agent ? 'Edit Agent' : 'New Agent';
  document.getElementById('f-name').value = agent ? agent.name : '';
  document.getElementById('f-model').value = agent ? agent.model : 'gemini-2.0-flash';
  document.getElementById('f-description').value = agent ? agent.description : '';
  document.getElementById('f-instruction').value = agent ? agent.instruction : '';
  document.getElementById('f-tools').value = agent && agent.tools && agent.tools.length
    ? JSON.stringify(agent.tools, null, 2) : '[]';
  $modal.classList.add('visible');
}

function closeModal() {
  $modal.classList.remove('visible');
  editingAgent = null;
}

$agentForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('modal-save');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';

  let tools = [];
  const toolsRaw = document.getElementById('f-tools').value.trim();
  if (toolsRaw) {
    try {
      tools = JSON.parse(toolsRaw);
      if (!Array.isArray(tools)) { alert('Tools must be a JSON array'); return; }
    } catch (e) {
      alert('Invalid JSON in Tools field: ' + e.message);
      btn.disabled = false;
      btn.textContent = 'Save Agent';
      return;
    }
  }

  const payload = {
    name: document.getElementById('f-name').value,
    model: document.getElementById('f-model').value,
    description: document.getElementById('f-description').value,
    instruction: document.getElementById('f-instruction').value,
    tools,
  };

  try {
    if (editingAgent) {
      await API.updateAgent(editingAgent, payload);
    } else {
      await API.createAgent(payload);
    }
    closeModal();
    await loadAgents();
    if (editingAgent) {
      selectAgent(editingAgent);
    }
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Agent';
  }
});

// ── Delete ─────────────────────────────────────────
$btnDelete.addEventListener('click', async () => {
  if (!selectedAgent) return;
  if (!confirm(`Delete "${selectedAgent.name}"? This cannot be undone.`)) return;

  try {
    await API.deleteAgent(selectedAgent.id);
    selectedAgent = null;
    await loadAgents();
    renderView();
  } catch (err) {
    alert(err.message);
  }
});

// ── Chat ───────────────────────────────────────────
document.getElementById('btn-send').addEventListener('click', sendChat);
$chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') sendChat();
});

async function sendChat() {
  const msg = $chatInput.value.trim();
  if (!msg) return;

  appendChat('user', msg);
  $chatInput.value = '';
  $chatInput.disabled = true;
  document.getElementById('btn-send').disabled = true;

  // Show typing indicator
  const typingEl = document.createElement('div');
  typingEl.className = 'chat-message';
  typingEl.id = 'typing';
  typingEl.innerHTML = `
    <div class="chat-avatar assistant">AI</div>
    <div class="chat-bubble"><span class="spinner" style="border-top-color:var(--accent)"></span></div>
  `;
  $chatMessages.appendChild(typingEl);
  $chatMessages.scrollTop = $chatMessages.scrollHeight;

  try {
    const res = await API.sendMessage(msg, conversationId);
    conversationId = res.conversation_id;
    document.getElementById('typing')?.remove();
    appendChat('assistant', res.response, res.agent_name);
  } catch (err) {
    document.getElementById('typing')?.remove();
    appendChat('assistant', 'Error: ' + err.message);
  } finally {
    $chatInput.disabled = false;
    document.getElementById('btn-send').disabled = false;
    $chatInput.focus();
  }
}

function appendChat(role, text, agentName) {
  const div = document.createElement('div');
  div.className = 'chat-message';
  const label = role === 'user' ? 'You' : 'AI';
  const tagHtml = agentName ? `<div class="agent-tag">${esc(agentName)}</div>` : '';
  div.innerHTML = `
    <div class="chat-avatar ${role}">${label === 'You' ? 'U' : 'AI'}</div>
    <div class="chat-bubble">${tagHtml}${esc(text)}</div>
  `;
  $chatMessages.appendChild(div);
  $chatMessages.scrollTop = $chatMessages.scrollHeight;
}

// ── Logout ─────────────────────────────────────────
document.getElementById('btn-logout').addEventListener('click', () => {
  API.clearAuth();
  window.location.href = '/';
});

// ── Util ───────────────────────────────────────────
function esc(str) {
  const d = document.createElement('div');
  d.textContent = str || '';
  return d.innerHTML;
}
