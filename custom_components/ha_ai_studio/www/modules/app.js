import { apiGet, apiPost } from "./api.js";

const DEFAULT_SETTINGS = {
  aiType: "cloud",
  cloudProvider: "openai",
  aiModel: "",
  openaiApiKey: "",
  openaiBaseUrl: "",
  localAiProvider: "ollama",
  ollamaUrl: "http://localhost:11434",
  ollamaModel: "",
  lmStudioUrl: "http://localhost:1234",
  lmStudioModel: "",
  customAiUrl: "",
  customAiModel: "",
  customAiApiKey: "",
  uiLanguage: "auto",
};

const SUGGESTED_PROMPTS = [
  "分析我最近的 Home Assistant 错误，并给出最可能原因。",
  "检查我的 automations.yaml 常见问题，并给一个修复草稿。",
  "解释最近日志里最重要的报错，告诉我先验证什么。",
  "帮我看模板/Jinja 报错应该怎么改。",
];

const state = {
  settings: { ...DEFAULT_SETTINGS },
  sessions: [],
  activeSessionId: null,
  activeSession: null,
  activeSnapshot: null,
  sending: false,
  modelOptions: {
    cloud: [],
    local: [],
  },
  promptResolver: null,
};

const els = {};

function $(id) {
  return document.getElementById(id);
}

function cacheDom() {
  Object.assign(els, {
    connectionPill: $("connection-pill"),
    sessionsPane: $("sessions-pane"),
    diagnosticsPane: $("diagnostics-pane"),
    sessionsList: $("sessions-list"),
    chatTitle: $("chat-title"),
    emptyState: $("empty-state"),
    suggestedPrompts: $("suggested-prompts"),
    chatMessages: $("chat-messages"),
    chatInput: $("chat-input"),
    sendButton: $("btn-send"),
    refreshDiagnosticsButton: $("btn-refresh-diagnostics"),
    newChatButton: $("btn-new-chat"),
    settingsButton: $("btn-open-settings"),
    diagnosticsContent: $("diagnostics-content"),
    settingsModal: $("settings-modal"),
    promptModal: $("prompt-modal"),
    promptModalTitle: $("prompt-modal-title"),
    promptModalLabel: $("prompt-modal-label"),
    promptModalInput: $("prompt-modal-input"),
    confirmPromptButton: $("btn-confirm-prompt"),
    aiType: $("settings-ai-type"),
    cloudProvider: $("settings-cloud-provider"),
    cloudSettings: $("cloud-settings"),
    localSettings: $("local-settings"),
    openaiBaseUrl: $("settings-openai-base-url"),
    openaiApiKey: $("settings-openai-api-key"),
    openaiModelSelect: $("settings-openai-model-select"),
    openaiModelInput: $("settings-ai-model"),
    fetchOpenaiModels: $("btn-fetch-openai-models"),
    localProvider: $("settings-local-provider"),
    ollamaUrl: $("settings-ollama-url"),
    lmStudioUrl: $("settings-lmstudio-url"),
    customUrl: $("settings-custom-url"),
    customApiKey: $("settings-custom-api-key"),
    localModelSelect: $("settings-local-model-select"),
    localModelInput: $("settings-local-model"),
    fetchLocalModels: $("btn-fetch-local-models"),
    uiLanguage: $("settings-ui-language"),
    saveSettingsButton: $("btn-save-settings"),
    fieldOllamaUrl: $("field-ollama-url"),
    fieldLmStudioUrl: $("field-lmstudio-url"),
    fieldCustomUrl: $("field-custom-url"),
    fieldCustomKey: $("field-custom-key"),
    toggleSessionsButton: $("btn-toggle-sessions"),
    toggleDiagnosticsButton: $("btn-toggle-diagnostics"),
  });
}

function escapeHtml(value = "") {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDateTime(value) {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch (error) {
    return value;
  }
}

function isAiConfigured() {
  if (state.settings.aiType === "cloud") {
    return !!state.settings.openaiApiKey;
  }
  if (state.settings.localAiProvider === "ollama") {
    return !!state.settings.ollamaUrl;
  }
  if (state.settings.localAiProvider === "lm-studio") {
    return !!state.settings.lmStudioUrl;
  }
  return !!state.settings.customAiUrl;
}

function updateConnectionPill(message, type = "neutral") {
  if (!els.connectionPill) return;
  els.connectionPill.textContent = message;
  els.connectionPill.style.color =
    type === "error" ? "var(--danger)" :
    type === "success" ? "#bdf8ea" :
    "var(--text-muted)";
}

function showToast(message, type = "neutral") {
  let layer = document.querySelector(".toast-layer");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "toast-layer";
    document.body.appendChild(layer);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  layer.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 180);
  }, 2600);
}

function autoResizeComposer() {
  const input = els.chatInput;
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
}

function collectSettingsFromForm() {
  return {
    aiType: els.aiType.value,
    cloudProvider: els.cloudProvider.value,
    openaiBaseUrl: els.openaiBaseUrl.value.trim(),
    openaiApiKey: els.openaiApiKey.value.trim(),
    aiModel: els.openaiModelInput.value.trim(),
    localAiProvider: els.localProvider.value,
    ollamaUrl: els.ollamaUrl.value.trim(),
    ollamaModel: els.localProvider.value === "ollama" ? els.localModelInput.value.trim() : state.settings.ollamaModel,
    lmStudioUrl: els.lmStudioUrl.value.trim(),
    lmStudioModel: els.localProvider.value === "lm-studio" ? els.localModelInput.value.trim() : state.settings.lmStudioModel,
    customAiUrl: els.customUrl.value.trim(),
    customAiModel: els.localProvider.value === "custom" ? els.localModelInput.value.trim() : state.settings.customAiModel,
    customAiApiKey: els.customApiKey.value.trim(),
    uiLanguage: els.uiLanguage.value,
  };
}

function populateSettingsForm() {
  const settings = state.settings;
  els.aiType.value = settings.aiType || "cloud";
  els.cloudProvider.value = settings.cloudProvider || "openai";
  els.openaiBaseUrl.value = settings.openaiBaseUrl || "";
  els.openaiApiKey.value = settings.openaiApiKey || "";
  els.openaiModelInput.value = settings.aiModel || "";
  els.localProvider.value = settings.localAiProvider || "ollama";
  els.ollamaUrl.value = settings.ollamaUrl || "";
  els.lmStudioUrl.value = settings.lmStudioUrl || "";
  els.customUrl.value = settings.customAiUrl || "";
  els.customApiKey.value = settings.customAiApiKey || "";
  if (els.localProvider.value === "ollama") {
    els.localModelInput.value = settings.ollamaModel || "";
  } else if (els.localProvider.value === "lm-studio") {
    els.localModelInput.value = settings.lmStudioModel || "";
  } else {
    els.localModelInput.value = settings.customAiModel || "";
  }
  els.uiLanguage.value = settings.uiLanguage || "auto";
  updateSettingsSections();
}

function updateSettingsSections() {
  const aiType = els.aiType.value;
  const localProvider = els.localProvider.value;
  els.cloudSettings.classList.toggle("hidden", aiType !== "cloud");
  els.localSettings.classList.toggle("hidden", aiType !== "local-ai");

  els.fieldOllamaUrl.classList.toggle("hidden", localProvider !== "ollama");
  els.fieldLmStudioUrl.classList.toggle("hidden", localProvider !== "lm-studio");
  els.fieldCustomUrl.classList.toggle("hidden", localProvider !== "custom");
  els.fieldCustomKey.classList.toggle("hidden", localProvider !== "custom");

  if (localProvider === "ollama") {
    els.localModelInput.placeholder = "例如 qwen2.5-coder:7b";
    els.localModelInput.value = state.settings.ollamaModel || els.localModelInput.value || "";
  } else if (localProvider === "lm-studio") {
    els.localModelInput.placeholder = "例如 qwen2.5-coder";
    els.localModelInput.value = state.settings.lmStudioModel || els.localModelInput.value || "";
  } else {
    els.localModelInput.placeholder = "例如 deepseek-chat";
    els.localModelInput.value = state.settings.customAiModel || els.localModelInput.value || "";
  }
}

function renderModelOptions(target, models) {
  const select = target === "cloud" ? els.openaiModelSelect : els.localModelSelect;
  const activeInput = target === "cloud" ? els.openaiModelInput : els.localModelInput;
  select.innerHTML = `<option value="">手动输入模型名</option>`;
  models.forEach((model) => {
    const option = document.createElement("option");
    option.value = model.id || model.label || "";
    option.textContent = model.label || model.id || "";
    select.appendChild(option);
  });
  if (activeInput.value) {
    select.value = activeInput.value;
  }
}

function setFetchModelsLoading(target, loading) {
  const button = target === "cloud" ? els.fetchOpenaiModels : els.fetchLocalModels;
  button.disabled = loading;
  button.textContent = loading ? "获取中..." : "获取模型";
}

async function fetchModels(target) {
  const settings = collectSettingsFromForm();
  const payload = {
    ai_type: target === "cloud" ? "cloud" : "local-ai",
    cloud_provider: "openai",
    ai_model: target === "cloud" ? settings.aiModel : (
      settings.localAiProvider === "ollama" ? settings.ollamaModel :
      settings.localAiProvider === "lm-studio" ? settings.lmStudioModel :
      settings.customAiModel
    ),
    settings,
  };

  try {
    setFetchModelsLoading(target, true);
    const result = await apiPost("ai_get_models", payload);
    const models = Array.isArray(result.models) ? result.models : [];
    state.modelOptions[target] = models;
    renderModelOptions(target, models);
    showToast(`已获取 ${models.length} 个模型`, "success");
  } catch (error) {
    showToast(error.message || "获取模型失败", "error");
  } finally {
    setFetchModelsLoading(target, false);
  }
}

function openModal(modal) {
  modal.classList.remove("hidden");
}

function closeModal(modal) {
  modal.classList.add("hidden");
}

function showPrompt({ title, label, value = "" }) {
  els.promptModalTitle.textContent = title;
  els.promptModalLabel.textContent = label;
  els.promptModalInput.value = value;
  openModal(els.promptModal);
  els.promptModalInput.focus();
  els.promptModalInput.select();
  return new Promise((resolve) => {
    state.promptResolver = resolve;
  });
}

function resolvePrompt(value) {
  const resolver = state.promptResolver;
  state.promptResolver = null;
  closeModal(els.promptModal);
  if (resolver) resolver(value);
}

function renderSuggestedPrompts() {
  els.suggestedPrompts.innerHTML = "";
  SUGGESTED_PROMPTS.forEach((prompt) => {
    const button = document.createElement("button");
    button.textContent = prompt;
    button.addEventListener("click", () => {
      els.chatInput.value = prompt;
      autoResizeComposer();
      sendMessage();
    });
    els.suggestedPrompts.appendChild(button);
  });
}

function renderSessions() {
  if (!state.sessions.length) {
    els.sessionsList.innerHTML = `<div class="loading-state">还没有会话，先创建一个对话。</div>`;
    return;
  }

  els.sessionsList.innerHTML = state.sessions.map((session) => {
    const active = session.id === state.activeSessionId ? "active" : "";
    return `
      <article class="session-card ${active}" data-session-id="${session.id}">
        <div class="session-card-header">
          <div class="session-card-title">${escapeHtml(session.title || "New chat")}</div>
          <div class="session-actions">
            <button data-session-action="rename" data-session-id="${session.id}" title="重命名">✎</button>
            <button data-session-action="delete" data-session-id="${session.id}" title="删除">×</button>
          </div>
        </div>
        <div class="session-card-summary">${escapeHtml(session.last_summary || "等待新的诊断问题")}</div>
        <div class="session-card-meta">${escapeHtml(formatDateTime(session.updated_at || session.created_at))}</div>
      </article>
    `;
  }).join("");
}

function renderRichText(text) {
  const chunks = String(text || "").split(/```/g);
  return chunks.map((chunk, index) => {
    if (index % 2 === 1) {
      return `
        <div class="code-block">
          <div class="code-toolbar">
            <button class="copy-btn" data-copy="${escapeHtml(chunk)}">复制代码</button>
          </div>
          <pre><code>${escapeHtml(chunk.trim())}</code></pre>
        </div>
      `;
    }

    const lines = chunk.split("\n");
    const html = [];
    let listItems = [];
    const flushList = () => {
      if (listItems.length) {
        html.push(`<ul>${listItems.join("")}</ul>`);
        listItems = [];
      }
    };

    lines.forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) {
        flushList();
        return;
      }
      if (trimmed.startsWith("## ")) {
        flushList();
        html.push(`<h2>${escapeHtml(trimmed.slice(3))}</h2>`);
        return;
      }
      if (trimmed.startsWith("### ")) {
        flushList();
        html.push(`<h3>${escapeHtml(trimmed.slice(4))}</h3>`);
        return;
      }
      if (trimmed.startsWith("- ")) {
        listItems.push(`<li>${escapeHtml(trimmed.slice(2))}</li>`);
        return;
      }
      flushList();
      html.push(`<p>${escapeHtml(trimmed)}</p>`);
    });
    flushList();
    return html.join("");
  }).join("");
}

function renderMessage(message) {
  const roleLabel = message.role === "user" ? "You" : "HA Copilot";
  const extraSections = [];

  if (message.citations?.length) {
    extraSections.push(`
      <section class="extra-card">
        <h4>Evidence</h4>
        <div class="citation-list">
          ${message.citations.map((citation) => `
            <div class="citation-item">
              <div class="citation-title">${escapeHtml(citation.title || citation.type || "Context")}</div>
              <div class="citation-snippet">${escapeHtml(citation.path ? `${citation.path}${citation.line ? `:${citation.line}` : ""}` : citation.snippet || "")}</div>
              ${citation.snippet ? `<div class="citation-snippet" style="margin-top:6px;">${escapeHtml(citation.snippet)}</div>` : ""}
            </div>
          `).join("")}
        </div>
      </section>
    `);
  }

  if (message.repair_draft) {
    extraSections.push(`
      <section class="extra-card">
        <h4>Repair Draft</h4>
        <div class="message-body">${renderRichText(message.repair_draft)}</div>
      </section>
    `);
  }

  if (message.suggested_checks?.length) {
    extraSections.push(`
      <section class="extra-card">
        <h4>How to Verify</h4>
        <div class="checks-list">
          ${message.suggested_checks.map((item) => `<div class="diagnostic-item"><strong>Next Step</strong><span>${escapeHtml(item)}</span></div>`).join("")}
        </div>
      </section>
    `);
  }

  return `
    <article class="message ${message.role}">
      <div class="message-card">
        <div class="message-meta">
          <span>${roleLabel}</span>
          <span>${escapeHtml(formatDateTime(message.created_at))}</span>
        </div>
        <div class="message-body">${renderRichText(message.content)}</div>
      </div>
      ${extraSections.length ? `<div class="message-extras">${extraSections.join("")}</div>` : ""}
    </article>
  `;
}

function renderActiveSession() {
  const session = state.activeSession;
  els.chatTitle.textContent = session?.title || "HA AI Studio";

  if (!session || !session.messages?.length) {
    els.emptyState.classList.remove("hidden");
    els.chatMessages.classList.add("hidden");
    els.chatMessages.innerHTML = "";
  } else {
    els.emptyState.classList.add("hidden");
    els.chatMessages.classList.remove("hidden");
    els.chatMessages.innerHTML = session.messages.map(renderMessage).join("");
    if (state.sending) {
      els.chatMessages.insertAdjacentHTML("beforeend", `
        <article class="message assistant">
          <div class="message-card">
            <div class="message-meta"><span>HA Copilot</span><span>生成中</span></div>
            <div class="message-body"><p>正在结合配置、检查结果和最近日志进行分析...</p></div>
          </div>
        </article>
      `);
    }
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
  }
}

function renderDiagnostics() {
  const snapshot = state.activeSnapshot;
  if (!snapshot) {
    els.diagnosticsContent.innerHTML = `
      <div class="diagnostic-card">
        <h4>尚无诊断快照</h4>
        <p class="citation-snippet">发送一条消息后，这里会展示最近日志、配置检查摘要、引用文件和相关实体。</p>
      </div>
    `;
    return;
  }

  const configCheck = snapshot.config_check || { errors: [] };
  const logs = snapshot.recent_logs || [];
  const files = snapshot.config_files || [];
  const entities = snapshot.related_entities || [];
  const services = snapshot.related_services || [];

  els.diagnosticsContent.innerHTML = `
    <section class="diagnostic-card">
      <h4>Home Assistant Health</h4>
      <div class="diagnostic-summary">
        <div class="summary-pill">${configCheck.success ? "Config Check 通过" : "Config Check 有错误"}</div>
        <div class="summary-pill">错误数 ${configCheck.errors?.length || 0}</div>
        <div class="summary-pill">日志条目 ${logs.length}</div>
      </div>
      <div class="diagnostic-list">
        ${(configCheck.errors || []).slice(0, 4).map((error) => `
          <div class="diagnostic-item">
            <strong>${escapeHtml(error.file || "Configuration check")}</strong>
            <span>${escapeHtml(error.message || "")}</span>
          </div>
        `).join("") || `<div class="diagnostic-item"><strong>状态</strong><span>${escapeHtml(configCheck.output || "没有结构化错误，等待更多上下文。")}</span></div>`}
      </div>
    </section>

    <section class="diagnostic-card">
      <h4>Recent Errors</h4>
      <div class="diagnostic-list">
        ${logs.slice(0, 6).map((log) => `
          <div class="diagnostic-item">
            <strong>${escapeHtml(log.level || "LOG")}</strong>
            <span>${escapeHtml(log.message || log.raw || "")}</span>
          </div>
        `).join("") || `<div class="diagnostic-item"><strong>无日志来源</strong><span>当前没有可用日志，或最近没有抓到错误/警告。</span></div>`}
      </div>
    </section>

    <section class="diagnostic-card">
      <h4>Context Sources</h4>
      <div class="diagnostic-list">
        ${files.slice(0, 6).map((item) => `
          <div class="diagnostic-item">
            <strong>${escapeHtml(item.path || "Config file")}</strong>
            <span>${escapeHtml(item.summary || "")}</span>
          </div>
        `).join("") || `<div class="diagnostic-item"><strong>暂无引用文件</strong><span>发送更具体的 Home Assistant 问题会帮助我定位文件。</span></div>`}
      </div>
    </section>

    <section class="diagnostic-card">
      <h4>Suggested Scope</h4>
      <div class="tag-cloud">
        ${entities.slice(0, 8).map((entity) => `<span class="tag">${escapeHtml(entity.entity_id || entity.friendly_name || "")}</span>`).join("")}
        ${services.slice(0, 6).map((service) => `<span class="tag">${escapeHtml(service.service || service.name || "")}</span>`).join("")}
      </div>
    </section>
  `;
}

async function loadSettings() {
  const result = await apiGet("get_settings");
  state.settings = { ...DEFAULT_SETTINGS, ...(result.settings || {}) };
  populateSettingsForm();
}

async function loadSessions() {
  const result = await apiGet("chat_list_sessions");
  state.sessions = result.sessions || [];
  renderSessions();
}

async function ensureActiveSession() {
  if (state.activeSessionId && state.activeSession) return;
  if (!state.sessions.length) {
    const created = await apiPost("chat_create_session", { title: "New chat" });
    state.sessions = created.sessions || [created.session];
  }
  const first = state.sessions[0];
  if (first) {
    await selectSession(first.id);
  }
}

async function selectSession(sessionId) {
  const result = await apiGet("chat_get_session", { session_id: sessionId });
  state.activeSessionId = sessionId;
  state.activeSession = result.session;
  state.activeSnapshot = result.snapshot || null;
  renderSessions();
  renderActiveSession();
  renderDiagnostics();
}

async function createSession(title = "New chat") {
  const result = await apiPost("chat_create_session", { title });
  state.sessions = result.sessions || [];
  await selectSession(result.session.id);
}

async function renameSession(sessionId) {
  const session = state.sessions.find((item) => item.id === sessionId);
  if (!session) return;
  const value = await showPrompt({
    title: "重命名会话",
    label: "新的会话标题",
    value: session.title || "",
  });
  if (!value || !value.trim()) return;
  const result = await apiPost("chat_update_session", { session_id: sessionId, title: value.trim() });
  state.sessions = state.sessions.map((item) => item.id === sessionId ? { ...item, ...result.session } : item);
  if (state.activeSessionId === sessionId && state.activeSession) {
    state.activeSession.title = result.session.title;
  }
  renderSessions();
  renderActiveSession();
}

async function deleteSession(sessionId) {
  if (!window.confirm("确定删除这个会话吗？")) return;
  const result = await apiPost("chat_delete_session", { session_id: sessionId });
  state.sessions = result.sessions || [];
  if (state.activeSessionId === sessionId) {
    state.activeSessionId = null;
    state.activeSession = null;
    state.activeSnapshot = null;
  }
  renderSessions();
  if (state.sessions.length) {
    await selectSession(state.sessions[0].id);
  } else {
    await createSession("New chat");
  }
}

async function sendMessage(explicitMessage = "") {
  const message = (explicitMessage || els.chatInput.value || "").trim();
  if (!message || state.sending) return;
  if (!isAiConfigured()) {
    showToast("请先在 AI 设置里配置模型连接。", "error");
    openModal(els.settingsModal);
    return;
  }
  if (!state.activeSessionId) {
    await ensureActiveSession();
  }

  state.sending = true;
  renderActiveSession();
  els.sendButton.disabled = true;

  try {
    const result = await apiPost("chat_send_message", {
      session_id: state.activeSessionId,
      message,
    });
    state.activeSession = result.session;
    state.activeSnapshot = result.diagnostics_snapshot || null;
    await loadSessions();
    renderActiveSession();
    renderDiagnostics();
    els.chatInput.value = "";
    autoResizeComposer();
  } catch (error) {
    showToast(error.message || "发送失败", "error");
  } finally {
    state.sending = false;
    els.sendButton.disabled = false;
    renderActiveSession();
  }
}

async function refreshDiagnostics() {
  if (!state.activeSessionId) return;
  const lastUserMessage = [...(state.activeSession?.messages || [])].reverse().find((message) => message.role === "user");
  const query = lastUserMessage?.content || state.activeSession?.last_summary || "";
  try {
    const result = await apiPost("chat_refresh_diagnostics", {
      session_id: state.activeSessionId,
      query,
    });
    state.activeSnapshot = result.snapshot || null;
    renderDiagnostics();
    showToast("诊断快照已刷新", "success");
  } catch (error) {
    showToast(error.message || "刷新诊断失败", "error");
  }
}

async function saveSettings() {
  const settings = collectSettingsFromForm();
  try {
    const result = await apiPost("save_settings", { settings });
    state.settings = { ...DEFAULT_SETTINGS, ...(result.settings || settings) };
    populateSettingsForm();
    closeModal(els.settingsModal);
    updateConnectionPill(
      state.settings.aiType === "cloud"
        ? "OpenAI-compatible 已配置"
        : `${state.settings.localAiProvider} 已配置`,
      "success"
    );
    showToast("AI 设置已保存", "success");
  } catch (error) {
    showToast(error.message || "保存设置失败", "error");
  }
}

function bindEvents() {
  els.newChatButton.addEventListener("click", () => createSession("New chat"));
  els.settingsButton.addEventListener("click", () => {
    populateSettingsForm();
    openModal(els.settingsModal);
  });
  els.refreshDiagnosticsButton.addEventListener("click", refreshDiagnostics);
  els.sendButton.addEventListener("click", () => sendMessage());
  els.chatInput.addEventListener("input", autoResizeComposer);
  els.chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  els.sessionsList.addEventListener("click", async (event) => {
    const actionButton = event.target.closest("[data-session-action]");
    if (actionButton) {
      const sessionId = actionButton.dataset.sessionId;
      if (actionButton.dataset.sessionAction === "rename") {
        await renameSession(sessionId);
      } else if (actionButton.dataset.sessionAction === "delete") {
        await deleteSession(sessionId);
      }
      return;
    }

    const card = event.target.closest("[data-session-id]");
    if (card?.dataset.sessionId) {
      await selectSession(card.dataset.sessionId);
      els.sessionsPane.classList.remove("open");
    }
  });

  els.chatMessages.addEventListener("click", async (event) => {
    const copyButton = event.target.closest("[data-copy]");
    if (!copyButton) return;
    try {
      await navigator.clipboard.writeText(copyButton.dataset.copy);
      showToast("已复制到剪贴板", "success");
    } catch (error) {
      showToast("复制失败", "error");
    }
  });

  els.aiType.addEventListener("change", updateSettingsSections);
  els.localProvider.addEventListener("change", updateSettingsSections);
  els.openaiModelSelect.addEventListener("change", () => {
    if (els.openaiModelSelect.value) {
      els.openaiModelInput.value = els.openaiModelSelect.value;
    }
  });
  els.localModelSelect.addEventListener("change", () => {
    if (els.localModelSelect.value) {
      els.localModelInput.value = els.localModelSelect.value;
    }
  });
  els.fetchOpenaiModels.addEventListener("click", () => fetchModels("cloud"));
  els.fetchLocalModels.addEventListener("click", () => fetchModels("local"));
  els.saveSettingsButton.addEventListener("click", saveSettings);

  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => {
      const modalId = button.getAttribute("data-close-modal");
      if (modalId === "prompt-modal") {
        resolvePrompt("");
      } else {
        closeModal($(modalId));
      }
    });
  });

  els.confirmPromptButton.addEventListener("click", () => {
    resolvePrompt(els.promptModalInput.value);
  });
  els.promptModalInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      resolvePrompt(els.promptModalInput.value);
    }
  });

  els.toggleSessionsButton.addEventListener("click", () => {
    els.sessionsPane.classList.toggle("open");
  });
  els.toggleDiagnosticsButton.addEventListener("click", () => {
    els.diagnosticsPane.classList.toggle("open");
  });
}

async function init() {
  cacheDom();
  bindEvents();
  renderSuggestedPrompts();
  autoResizeComposer();

  try {
    await loadSettings();
    await loadSessions();
    await ensureActiveSession();
    updateConnectionPill(isAiConfigured() ? "AI 已配置" : "尚未配置 AI", isAiConfigured() ? "success" : "neutral");
    if (!isAiConfigured()) {
      openModal(els.settingsModal);
    }
  } catch (error) {
    console.error(error);
    updateConnectionPill("连接失败", "error");
    showToast(error.message || "初始化失败", "error");
  }
}

init();
