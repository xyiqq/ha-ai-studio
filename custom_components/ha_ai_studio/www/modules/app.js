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

const UI_PREFS_STORAGE_KEY = "ha_ai_studio_ui_prefs";

const state = {
  settings: { ...DEFAULT_SETTINGS },
  sessions: [],
  activeSessionId: null,
  activeSession: null,
  activeSnapshot: null,
  pendingMessages: [],
  sending: false,
  modelOptions: {
    cloud: [],
    local: [],
  },
  promptResolver: null,
  confirmResolver: null,
  editOperations: {},
  ui: {
    sessionsCollapsed: false,
    diagnosticsCollapsed: false,
    skipEditConfirmBySession: {},
  },
};

const els = {};

function $(id) {
  return document.getElementById(id);
}

function cacheDom() {
  Object.assign(els, {
    connectionPill: $("connection-pill"),
    appVersion: $("app-version"),
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
    confirmModal: $("confirm-modal"),
    confirmModalTitle: $("confirm-modal-title"),
    confirmModalSummary: $("confirm-modal-summary"),
    confirmModalSkip: $("confirm-modal-skip"),
    confirmEditButton: $("btn-confirm-edit"),
    composerStatus: $("composer-status"),
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
    collapseSessionsButton: $("btn-collapse-sessions"),
    collapseDiagnosticsButton: $("btn-collapse-diagnostics"),
    toggleSessionsButton: $("btn-toggle-sessions"),
    toggleDiagnosticsButton: $("btn-toggle-diagnostics"),
    toggleEditConfirmButton: $("btn-toggle-edit-confirm"),
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
  } catch {
    return value;
  }
}

function summarizeTitle(text = "", maxLength = 34) {
  const normalized = String(text).replace(/\s+/g, " ").trim();
  if (!normalized) return "New chat";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength).trim()}...`;
}

function isAiConfigured() {
  if (state.settings.aiType === "cloud") return !!state.settings.openaiApiKey;
  if (state.settings.localAiProvider === "ollama") return !!state.settings.ollamaUrl;
  if (state.settings.localAiProvider === "lm-studio") return !!state.settings.lmStudioUrl;
  return !!state.settings.customAiUrl;
}

function updateConnectionPill(message, tone = "neutral") {
  if (!els.connectionPill) return;
  els.connectionPill.textContent = message;
  els.connectionPill.dataset.tone = tone;
}

function showToast(message, tone = "neutral") {
  let layer = document.querySelector(".toast-layer");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "toast-layer";
    document.body.appendChild(layer);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${tone}`;
  toast.textContent = message;
  layer.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 180);
  }, 2600);
}

function autoResizeComposer() {
  if (!els.chatInput) return;
  els.chatInput.style.height = "auto";
  els.chatInput.style.height = `${Math.min(els.chatInput.scrollHeight, 220)}px`;
}

function loadUiPrefs() {
  try {
    const raw = window.localStorage.getItem(UI_PREFS_STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    state.ui.sessionsCollapsed = !!parsed.sessionsCollapsed;
    state.ui.diagnosticsCollapsed = !!parsed.diagnosticsCollapsed;
    state.ui.skipEditConfirmBySession = parsed.skipEditConfirmBySession && typeof parsed.skipEditConfirmBySession === "object"
      ? parsed.skipEditConfirmBySession
      : {};
  } catch {}
}

function saveUiPrefs() {
  try {
    window.localStorage.setItem(UI_PREFS_STORAGE_KEY, JSON.stringify(state.ui));
  } catch {}
}

function openModal(modal) {
  if (modal) modal.classList.remove("hidden");
}

function closeModal(modal) {
  if (modal) modal.classList.add("hidden");
}

function setComposerStatus(message = "", tone = "neutral") {
  if (!els.composerStatus) return;
  if (!message) {
    els.composerStatus.textContent = "";
    els.composerStatus.dataset.tone = "";
    els.composerStatus.classList.add("hidden");
    return;
  }
  els.composerStatus.textContent = message;
  els.composerStatus.dataset.tone = tone;
  els.composerStatus.classList.remove("hidden");
}

function isDirectApplyEnabled(sessionId = state.activeSessionId) {
  if (!sessionId) return false;
  return !!state.ui.skipEditConfirmBySession[sessionId];
}

function updateEditConfirmControl() {
  if (!els.toggleEditConfirmButton) return;
  const enabled = isDirectApplyEnabled();
  els.toggleEditConfirmButton.disabled = !state.activeSessionId;
  els.toggleEditConfirmButton.classList.toggle("active", enabled);
  els.toggleEditConfirmButton.textContent = enabled ? "直接修改已开启" : "修改需确认";
  els.toggleEditConfirmButton.title = enabled
    ? "当前会话后续应用修改将直接执行"
    : "当前会话后续应用修改前会先要求确认";
}

function applyPaneVisibilityState() {
  const appShell = $("app");
  if (!appShell) return;
  appShell.classList.toggle("sessions-collapsed", state.ui.sessionsCollapsed);
  appShell.classList.toggle("diagnostics-collapsed", state.ui.diagnosticsCollapsed);
  if (els.collapseSessionsButton) {
    els.collapseSessionsButton.textContent = state.ui.sessionsCollapsed ? "显示会话" : "隐藏会话";
    els.collapseSessionsButton.title = state.ui.sessionsCollapsed ? "显示 AI 会话历史" : "隐藏 AI 会话历史";
  }
  if (els.collapseDiagnosticsButton) {
    els.collapseDiagnosticsButton.textContent = state.ui.diagnosticsCollapsed ? "显示诊断" : "隐藏诊断";
    els.collapseDiagnosticsButton.title = state.ui.diagnosticsCollapsed ? "显示诊断侧栏" : "隐藏诊断侧栏";
  }
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

function showEditConfirm({ action, edit }) {
  const label = action === "restore" ? "确认恢复备份" : "确认应用修改";
  const filePath = edit.path || edit.file_path || edit.file || "未标注文件";
  const summary = edit.summary || edit.reason || edit.description || (action === "restore" ? "将恢复该修改的备份内容。" : "将应用 assistant 提供的修复草稿。");
  const backupLabel = edit.backup_id || edit.backupId || edit.backup_path || edit.backupPath || "";

  els.confirmModalTitle.textContent = label;
  els.confirmModalSummary.innerHTML = `
    <div class="confirm-row">
      <strong>目标文件</strong>
      <span>${escapeHtml(filePath)}</span>
    </div>
    <div class="confirm-row">
      <strong>操作说明</strong>
      <span>${escapeHtml(summary)}</span>
    </div>
    ${backupLabel ? `
      <div class="confirm-row">
        <strong>备份标识</strong>
        <span>${escapeHtml(backupLabel)}</span>
      </div>
    ` : ""}
  `;
  els.confirmModalSkip.checked = isDirectApplyEnabled();
  els.confirmEditButton.textContent = action === "restore" ? "确认恢复" : "确认应用";
  openModal(els.confirmModal);

  return new Promise((resolve) => {
    state.confirmResolver = resolve;
  });
}

function resolveEditConfirm(result) {
  const resolver = state.confirmResolver;
  state.confirmResolver = null;
  closeModal(els.confirmModal);
  if (resolver) resolver(result);
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

function renderEditPlanSection(message) {
  const proposed = Array.isArray(message.proposed_edits) ? message.proposed_edits : [];
  const applied = Array.isArray(message.applied_edits) ? message.applied_edits : [];
  if (!proposed.length && !applied.length) return "";

  const proposedMarkup = proposed.length ? `
    <section class="extra-card">
      <h4>Proposed Changes</h4>
      <div class="edit-plan-list">
        ${proposed.map((edit) => `
          <div class="edit-plan-item">
            <strong>${escapeHtml(edit.path || "Config file")}</strong>
            <span>${escapeHtml(edit.reason || "")}</span>
          </div>
        `).join("")}
      </div>
      <div class="edit-actions">
        <button class="primary-btn" data-apply-edits="${message.id}">${isDirectApplyEnabled() ? "直接修改" : "确认后修改"}</button>
      </div>
    </section>` : "";

  const appliedMarkup = applied.length ? `
    <section class="extra-card">
      <h4>Applied Changes</h4>
      <div class="edit-plan-list">
        ${applied.map((edit) => `
          <div class="edit-plan-item">
            <strong>${escapeHtml(edit.path || "Config file")}</strong>
            <span>${escapeHtml(edit.reason || edit.status || "")}</span>
            <span>${escapeHtml(edit.status === "restored" ? "已恢复备份" : "已修改并创建备份")}</span>
            <div class="edit-actions">
              ${edit.can_restore ? `<button class="ghost-btn" data-restore-backup="${edit.backup_id}" data-message-id="${message.id}">恢复备份</button>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
    </section>` : "";

  return proposedMarkup + appliedMarkup;
}

function renderMessage(message) {
  const roleLabel = message.role === "user" ? "You" : "HA Copilot";
  const timestamp = message.pending ? (message.role === "user" ? "发送中..." : "分析中...") : formatDateTime(message.created_at);
  const extras = [];

  if (message.citations?.length) {
    extras.push(`<section class="extra-card"><h4>Evidence</h4><div class="citation-list">${message.citations.map((citation) => `
      <div class="citation-item">
        <div class="citation-title">${escapeHtml(citation.title || citation.type || "Context")}</div>
        <div class="citation-snippet">${escapeHtml(citation.path ? `${citation.path}${citation.line ? `:${citation.line}` : ""}` : citation.snippet || "")}</div>
        ${citation.snippet ? `<div class="citation-snippet" style="margin-top:6px;">${escapeHtml(citation.snippet)}</div>` : ""}
      </div>`).join("")}</div></section>`);
  }
  if (message.repair_draft) {
    extras.push(`<section class="extra-card"><h4>Repair Draft</h4><div class="message-body">${renderRichText(message.repair_draft)}</div></section>`);
  }
  if (message.suggested_checks?.length) {
    extras.push(`<section class="extra-card"><h4>How to Verify</h4><div class="checks-list">${message.suggested_checks.map((item) => `<div class="diagnostic-item"><strong>Next Step</strong><span>${escapeHtml(item)}</span></div>`).join("")}</div></section>`);
  }
  const editSection = renderEditPlanSection(message);
  if (editSection) extras.push(editSection);

  return `<article class="message ${message.role}">
    <div class="message-card">
      <div class="message-meta"><span>${roleLabel}</span><span>${escapeHtml(timestamp)}</span></div>
      <div class="message-body">${renderRichText(message.content)}</div>
    </div>
    ${extras.length ? `<div class="message-extras">${extras.join("")}</div>` : ""}
  </article>`;
}

function renderActiveSession() {
  const session = state.activeSession;
  els.chatTitle.textContent = session?.title || "HA AI Studio";
  updateEditConfirmControl();
  setComposerStatus(
    isDirectApplyEnabled()
      ? "此对话已开启直接修改，应用修复草稿时将跳过二次确认。"
      : "默认需要确认后才会真正修改文件，修改前会自动创建备份。",
    isDirectApplyEnabled() ? "success" : "neutral",
  );

  const renderedMessages = [...(session?.messages || []), ...state.pendingMessages];
  if (!session || !renderedMessages.length) {
    els.emptyState.classList.remove("hidden");
    els.chatMessages.classList.add("hidden");
    els.chatMessages.innerHTML = "";
    return;
  }

  els.emptyState.classList.add("hidden");
  els.chatMessages.classList.remove("hidden");
  els.chatMessages.innerHTML = renderedMessages.map(renderMessage).join("");
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function renderDiagnostics() {
  const snapshot = state.activeSnapshot;
  if (!snapshot) {
    els.diagnosticsContent.innerHTML = `<div class="diagnostic-card"><h4>尚无诊断快照</h4><p class="citation-snippet">发送一条消息后，这里会展示最近日志、配置检查摘要、引用文件和相关实体。</p></div>`;
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
        ${(configCheck.errors || []).slice(0, 4).map((error) => `<div class="diagnostic-item"><strong>${escapeHtml(error.file || "Configuration check")}</strong><span>${escapeHtml(error.message || "")}</span></div>`).join("") || `<div class="diagnostic-item"><strong>状态</strong><span>${escapeHtml(configCheck.output || "没有结构化错误，等待更多上下文。")}</span></div>`}
      </div>
    </section>
    <section class="diagnostic-card">
      <h4>Recent Errors</h4>
      <div class="diagnostic-list">
        ${logs.slice(0, 6).map((log) => `<div class="diagnostic-item"><strong>${escapeHtml(log.level || "LOG")}</strong><span>${escapeHtml(log.message || log.raw || "")}</span></div>`).join("") || `<div class="diagnostic-item"><strong>无日志来源</strong><span>当前没有可用日志，或最近没有抓到错误/警告。</span></div>`}
      </div>
    </section>
    <section class="diagnostic-card">
      <h4>Context Sources</h4>
      <div class="diagnostic-list">
        ${files.slice(0, 6).map((item) => `<div class="diagnostic-item"><strong>${escapeHtml(item.path || "Config file")}</strong><span>${escapeHtml(item.summary || "")}</span></div>`).join("") || `<div class="diagnostic-item"><strong>暂无引用文件</strong><span>发送更具体的 Home Assistant 问题会帮助我定位文件。</span></div>`}
      </div>
    </section>
    <section class="diagnostic-card">
      <h4>Suggested Scope</h4>
      <div class="tag-cloud">
        ${entities.slice(0, 8).map((entity) => `<span class="tag">${escapeHtml(entity.entity_id || entity.friendly_name || "")}</span>`).join("")}
        ${services.slice(0, 6).map((service) => `<span class="tag">${escapeHtml(service.service || service.name || "")}</span>`).join("")}
      </div>
    </section>`;
}

function showEditConfirm({ action, messageId = "", edits = [], backupId = "", edit = null }) {
  const summaryEdits = edit ? [edit] : edits;
  els.confirmModalTitle.textContent = action === "restore" ? "确认恢复备份" : "确认应用修改";
  els.confirmModalSummary.innerHTML = summaryEdits.map((item) => `
    <div class="confirm-row">
      <strong>${escapeHtml(item.path || item.file_path || item.file || "目标文件")}</strong>
      <span>${escapeHtml(item.summary || item.reason || item.description || "确认继续执行当前操作。")}</span>
      ${backupId ? `<span>${escapeHtml(`备份 ID: ${backupId}`)}</span>` : ""}
    </div>
  `).join("") || `<div class="confirm-row"><strong>${backupId ? "恢复备份" : "应用修改"}</strong><span>确认继续执行当前操作。</span></div>`;
  els.confirmModalSkip.checked = isDirectApplyEnabled();
  els.confirmEditButton.textContent = action === "restore" ? "确认恢复" : "确认修改";
  openModal(els.confirmModal);
  return new Promise((resolve) => {
    state.confirmResolver = (result) => resolve({ action, messageId, edits: summaryEdits, backupId, ...result });
  });
}

function resolveEditConfirm(result) {
  const resolver = state.confirmResolver;
  state.confirmResolver = null;
  closeModal(els.confirmModal);
  if (!resolver) return null;
  return resolver(result);
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
  if (first) await selectSession(first.id);
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
  const value = await showPrompt({ title: "重命名会话", label: "新的会话标题", value: session.title || "" });
  if (!value || !value.trim()) return;
  const result = await apiPost("chat_update_session", { session_id: sessionId, title: value.trim() });
  state.sessions = state.sessions.map((item) => (item.id === sessionId ? { ...item, ...result.session } : item));
  if (state.activeSessionId === sessionId && state.activeSession) state.activeSession.title = result.session.title;
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
  if (state.sessions.length) await selectSession(state.sessions[0].id);
  else await createSession("New chat");
}

async function sendMessage(explicitMessage = "") {
  const message = (explicitMessage || els.chatInput.value || "").trim();
  if (!message || state.sending) return;
  if (!isAiConfigured()) {
    showToast("请先在 AI 设置里配置模型连接。", "error");
    openModal(els.settingsModal);
    return;
  }
  if (!state.activeSessionId) await ensureActiveSession();

  state.sending = true;
  state.pendingMessages = [
    { id: `pending-user-${Date.now()}`, role: "user", content: message, created_at: new Date().toISOString(), pending: true, citations: [], repair_draft: "", suggested_checks: [], proposed_edits: [], applied_edits: [] },
    { id: `pending-assistant-${Date.now() + 1}`, role: "assistant", content: "正在结合配置、检查结果和最近日志进行分析...", created_at: new Date().toISOString(), pending: true, citations: [], repair_draft: "", suggested_checks: [], proposed_edits: [], applied_edits: [] },
  ];
  els.chatInput.value = "";
  autoResizeComposer();
  renderActiveSession();
  els.sendButton.disabled = true;
  els.sendButton.textContent = "分析中...";

  try {
    const result = await apiPost("chat_send_message", { session_id: state.activeSessionId, message });
    state.pendingMessages = [];
    state.activeSession = result.session;
    state.activeSnapshot = result.diagnostics_snapshot || null;
    await loadSessions();
    renderActiveSession();
    renderDiagnostics();
  } catch (error) {
    state.pendingMessages = [];
    els.chatInput.value = message;
    autoResizeComposer();
    showToast(error.message || "发送失败", "error");
  } finally {
    state.sending = false;
    els.sendButton.disabled = false;
    els.sendButton.textContent = "发送";
    renderActiveSession();
  }
}

async function refreshDiagnostics() {
  if (!state.activeSessionId) return;
  const lastUserMessage = [...(state.activeSession?.messages || [])].reverse().find((item) => item.role === "user");
  const query = lastUserMessage?.content || state.activeSession?.last_summary || "";
  try {
    const result = await apiPost("chat_refresh_diagnostics", { session_id: state.activeSessionId, query });
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
    updateConnectionPill(state.settings.aiType === "cloud" ? "OpenAI-compatible 已配置" : `${state.settings.localAiProvider} 已配置`, "success");
    showToast("AI 设置已保存", "success");
  } catch (error) {
    showToast(error.message || "保存设置失败", "error");
  }
}

async function setDirectApplyEnabled(enabled) {
  if (!state.activeSessionId) return;
  if (enabled) state.ui.skipEditConfirmBySession[state.activeSessionId] = true;
  else delete state.ui.skipEditConfirmBySession[state.activeSessionId];
  saveUiPrefs();
  try {
    const result = await apiPost("chat_update_session", {
      session_id: state.activeSessionId,
      auto_approve_edits: !!enabled,
    });
    if (result.session) {
      state.activeSession = { ...(state.activeSession || {}), ...result.session };
      state.sessions = state.sessions.map((item) =>
        item.id === state.activeSessionId ? { ...item, ...result.session } : item,
      );
      renderSessions();
    }
  } catch (error) {
    showToast(error.message || "更新修改确认策略失败", "error");
  }
  updateEditConfirmControl();
}

async function applyEdits(messageId, confirmed) {
  if (!state.activeSessionId) return;
  try {
    const result = await apiPost("chat_apply_proposed_edits", { session_id: state.activeSessionId, message_id: messageId, confirmed });
    state.activeSession = result.session || state.activeSession;
    if (result.session) {
      state.sessions = state.sessions.map((item) => (item.id === state.activeSessionId ? { ...item, ...result.session } : item));
    }
    renderSessions();
    renderActiveSession();
    showToast("已应用修改，并自动创建备份。", "success");
  } catch (error) {
    showToast(error.message || "应用修改失败", "error");
  }
}

async function restoreBackup(backupId, messageId) {
  if (!state.activeSessionId) return;
  try {
    const result = await apiPost("chat_restore_backup", { session_id: state.activeSessionId, message_id: messageId, backup_id: backupId });
    if (result.session) {
      state.activeSession = result.session;
      state.sessions = state.sessions.map((item) => (item.id === state.activeSessionId ? { ...item, ...result.session } : item));
    }
    renderSessions();
    renderActiveSession();
    showToast("备份已恢复。", "success");
  } catch (error) {
    showToast(error.message || "恢复备份失败", "error");
  }
}

function bindEvents() {
  els.newChatButton.addEventListener("click", () => void createSession("New chat"));
  els.settingsButton.addEventListener("click", () => {
    populateSettingsForm();
    openModal(els.settingsModal);
  });
  els.refreshDiagnosticsButton.addEventListener("click", () => void refreshDiagnostics());
  els.sendButton.addEventListener("click", () => void sendMessage());
  els.chatInput.addEventListener("input", autoResizeComposer);
  els.chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  });
  els.sessionsList.addEventListener("click", async (event) => {
    const actionButton = event.target.closest("[data-session-action]");
    if (actionButton) {
      const sessionId = actionButton.dataset.sessionId;
      if (actionButton.dataset.sessionAction === "rename") await renameSession(sessionId);
      if (actionButton.dataset.sessionAction === "delete") await deleteSession(sessionId);
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
    if (copyButton) {
      try {
        await navigator.clipboard.writeText(copyButton.dataset.copy);
        showToast("已复制到剪贴板", "success");
      } catch {
        showToast("复制失败", "error");
      }
      return;
    }
    const applyButton = event.target.closest("[data-apply-edits]");
    if (applyButton) {
      const messageId = applyButton.getAttribute("data-apply-edits");
      const sourceMessage = state.activeSession?.messages?.find((item) => item.id === messageId);
      const edits = sourceMessage?.proposed_edits || [];
      if (!edits.length) return;
      if (isDirectApplyEnabled()) await applyEdits(messageId, true);
      else showEditConfirm({ action: "apply", messageId, edits });
      return;
    }
    const restoreButton = event.target.closest("[data-restore-backup]");
    if (restoreButton) {
      if (isDirectApplyEnabled()) await restoreBackup(restoreButton.dataset.restoreBackup, restoreButton.dataset.messageId);
      else showEditConfirm({ action: "restore", messageId: restoreButton.dataset.messageId, edits: [], backupId: restoreButton.dataset.restoreBackup });
    }
  });
  els.aiType.addEventListener("change", updateSettingsSections);
  els.localProvider.addEventListener("change", updateSettingsSections);
  els.openaiModelSelect.addEventListener("change", () => { if (els.openaiModelSelect.value) els.openaiModelInput.value = els.openaiModelSelect.value; });
  els.localModelSelect.addEventListener("change", () => { if (els.localModelSelect.value) els.localModelInput.value = els.localModelSelect.value; });
  els.fetchOpenaiModels.addEventListener("click", () => void fetchModels("cloud"));
  els.fetchLocalModels.addEventListener("click", () => void fetchModels("local"));
  els.saveSettingsButton.addEventListener("click", () => void saveSettings());
  els.toggleEditConfirmButton?.addEventListener("click", () => void setDirectApplyEnabled(!isDirectApplyEnabled()));
  els.autoApproveToggle?.addEventListener("change", () => void setDirectApplyEnabled(els.autoApproveToggle.checked));
  els.collapseSessionsButton?.addEventListener("click", () => { state.ui.sessionsCollapsed = !state.ui.sessionsCollapsed; applyPaneVisibilityState(); saveUiPrefs(); });
  els.collapseDiagnosticsButton?.addEventListener("click", () => { state.ui.diagnosticsCollapsed = !state.ui.diagnosticsCollapsed; applyPaneVisibilityState(); saveUiPrefs(); });
  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => {
      const modalId = button.getAttribute("data-close-modal");
      if (modalId === "prompt-modal") resolvePrompt("");
      else closeModal($(modalId));
    });
  });
  els.confirmPromptButton.addEventListener("click", () => resolvePrompt(els.promptModalInput.value));
  els.promptModalInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      resolvePrompt(els.promptModalInput.value);
    }
  });
  els.confirmEditButton?.addEventListener("click", async () => {
    const pending = resolveEditConfirm({ skip: els.confirmModalSkip.checked });
    if (!pending) return;
    if (pending.skip !== isDirectApplyEnabled()) await setDirectApplyEnabled(pending.skip);
    if (pending.action === "restore") await restoreBackup(pending.backupId, pending.messageId);
    else await applyEdits(pending.messageId, true);
  });
  els.toggleSessionsButton?.addEventListener("click", () => els.sessionsPane.classList.toggle("open"));
  els.toggleDiagnosticsButton?.addEventListener("click", () => els.diagnosticsPane.classList.toggle("open"));
}

async function init() {
  cacheDom();
  loadUiPrefs();
  applyPaneVisibilityState();
  bindEvents();
  renderSuggestedPrompts();
  autoResizeComposer();
  try {
    await loadSettings();
    await loadSessions();
    await ensureActiveSession();
    updateConnectionPill(isAiConfigured() ? "AI 已配置" : "尚未配置 AI", isAiConfigured() ? "success" : "neutral");
    if (!isAiConfigured()) openModal(els.settingsModal);
  } catch (error) {
    console.error(error);
    updateConnectionPill("连接失败", "error");
    showToast(error.message || "初始化失败", "error");
  }
}

function getEditOperationKey(action, messageId, editId) {
  return `${action}:${messageId || "message"}:${editId || "edit"}`;
}

function isEditBusy(action, messageId, editId) {
  return !!state.editOperations[getEditOperationKey(action, messageId, editId)];
}

function renderProposedEditCard(edit, message) {
  const editId = edit.id || edit.edit_id || edit.proposed_edit_id || edit.path || `edit-${message.id}`;
  const filePath = edit.path || edit.file_path || edit.file || "";
  const title = edit.title || edit.label || filePath || "修复建议";
  const summary = edit.summary || edit.reason || edit.description || "assistant 建议修改该文件以修复当前问题。";
  const preview = edit.preview || edit.diff || edit.patch || edit.content || edit.after || "";
  const status = edit.status || (edit.applied_at ? "已应用" : "待处理");
  const backupId = edit.backup_id || edit.backupId || edit.backup_path || edit.backupPath || "";
  const canRestore = Boolean(edit.can_restore || backupId || edit.applied_at);
  const applyBusy = isEditBusy("apply", message.id, editId);
  const restoreBusy = isEditBusy("restore", message.id, editId);

  return `
    <article class="proposed-edit-card">
      <div class="proposed-edit-header">
        <div>
          <div class="proposed-edit-title">${escapeHtml(title)}</div>
          ${filePath ? `<div class="proposed-edit-path">${escapeHtml(filePath)}</div>` : ""}
        </div>
        <span class="edit-status-pill">${escapeHtml(status)}</span>
      </div>
      <p class="proposed-edit-summary">${escapeHtml(summary)}</p>
      ${backupId ? `<div class="proposed-edit-backup">备份：${escapeHtml(backupId)}</div>` : ""}
      ${preview ? `<div class="proposed-edit-preview">${renderRichText(preview)}</div>` : ""}
      <div class="proposed-edit-actions">
        <button
          class="primary-btn compact"
          data-edit-action="apply"
          data-message-id="${escapeHtml(message.id || "")}"
          data-edit-id="${escapeHtml(editId)}"
          data-backup-id="${escapeHtml(backupId)}"
          ${applyBusy ? "disabled" : ""}
        >${applyBusy ? "应用中..." : "应用修改"}</button>
        <button
          class="ghost-btn compact"
          data-edit-action="restore"
          data-message-id="${escapeHtml(message.id || "")}"
          data-edit-id="${escapeHtml(editId)}"
          data-backup-id="${escapeHtml(backupId)}"
          ${!canRestore || restoreBusy ? "disabled" : ""}
        >${restoreBusy ? "恢复中..." : "恢复备份"}</button>
      </div>
    </article>
  `;
}

function renderMessage(message) {
  const roleLabel = message.role === "user" ? "You" : "HA Copilot";
  const timestampLabel = message.pending
    ? (message.role === "user" ? "发送中..." : "分析中...")
    : formatDateTime(message.created_at);
  const extraSections = [];
  const bodyContent = message.content || message.answer || "";
  const proposedEdits = Array.isArray(message.proposed_edits) ? message.proposed_edits : [];

  if (message.citations?.length) {
    extraSections.push(`
      <section class="extra-card">
        <h4>Evidence</h4>
        <div class="citation-list">
          ${message.citations.map((citation) => `
            <div class="citation-item">
              <div class="citation-title">${escapeHtml(citation.title || citation.type || "Context")}</div>
              <div class="citation-snippet">${escapeHtml(citation.path ? `${citation.path}${citation.line ? `:${citation.line}` : ""}` : citation.snippet || "")}</div>
              ${citation.snippet ? `<div class="citation-snippet citation-snippet-secondary">${escapeHtml(citation.snippet)}</div>` : ""}
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

  if (proposedEdits.length) {
    extraSections.push(`
      <section class="extra-card">
        <h4>Proposed Edits</h4>
        <div class="proposed-edit-list">
          ${proposedEdits.map((edit) => renderProposedEditCard(edit, message)).join("")}
        </div>
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
      <div class="message-card ${message.pending ? "pending" : ""}">
        <div class="message-meta">
          <span>${roleLabel}</span>
          <span>${escapeHtml(timestampLabel)}</span>
        </div>
        <div class="message-body">${renderRichText(bodyContent)}</div>
      </div>
      ${extraSections.length ? `<div class="message-extras">${extraSections.join("")}</div>` : ""}
    </article>
  `;
}

function renderActiveSession() {
  const session = state.activeSession;
  const renderedMessages = [...(session?.messages || []), ...state.pendingMessages];
  const fallbackTitle = session?.title || summarizeTitle(renderedMessages.find((item) => item.role === "user")?.content || "HA AI Studio", 48);

  els.chatTitle.textContent = fallbackTitle;
  updateEditConfirmControl();

  if (!renderedMessages.length) {
    els.emptyState.classList.remove("hidden");
    els.chatMessages.classList.add("hidden");
    els.chatMessages.innerHTML = "";
  } else {
    els.emptyState.classList.add("hidden");
    els.chatMessages.classList.remove("hidden");
    els.chatMessages.innerHTML = renderedMessages.map(renderMessage).join("");
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
  if (state.activeSessionId && state.activeSessionId !== "__pending__" && state.activeSession) return;
  if (!state.sessions.length) {
    const created = await apiPost("chat_create_session", { title: "New chat" });
    state.sessions = created.sessions || [created.session];
    renderSessions();
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

function beginOptimisticSend(message) {
  const createdAt = new Date().toISOString();
  if (!state.activeSession) {
    state.activeSession = {
      id: "__pending__",
      title: summarizeTitle(message),
      messages: [],
      created_at: createdAt,
      updated_at: createdAt,
      last_summary: message,
    };
  }
  state.pendingMessages = [
    {
      id: `pending-user-${Date.now()}`,
      role: "user",
      content: message,
      created_at: createdAt,
      pending: true,
      citations: [],
      repair_draft: "",
      suggested_checks: [],
    },
    {
      id: `pending-assistant-${Date.now() + 1}`,
      role: "assistant",
      content: "正在结合配置、检查结果和最近日志进行分析...",
      created_at: createdAt,
      pending: true,
      citations: [],
      repair_draft: "",
      suggested_checks: [],
    },
  ];
  renderActiveSession();
  setComposerStatus("已发送，正在分析配置、日志和运行态上下文...", "busy");
  els.sendButton.disabled = true;
  els.sendButton.textContent = "分析中...";
}

function clearSendState() {
  state.pendingMessages = [];
  state.sending = false;
  els.sendButton.disabled = false;
  els.sendButton.textContent = "发送";
  setComposerStatus("");
  renderActiveSession();
}

async function sendMessage(explicitMessage = "") {
  const message = (explicitMessage || els.chatInput.value || "").trim();
  if (!message || state.sending) return;
  if (!isAiConfigured()) {
    showToast("请先在 AI 设置里配置模型连接。", "error");
    openModal(els.settingsModal);
    return;
  }

  state.sending = true;
  beginOptimisticSend(message);
  els.chatInput.value = "";
  autoResizeComposer();

  try {
    if (!state.activeSessionId || state.activeSessionId === "__pending__") {
      await ensureActiveSession();
    }
    if (!state.activeSessionId) {
      throw new Error("无法创建或选中会话");
    }

    const result = await apiPost("chat_send_message", {
      session_id: state.activeSessionId,
      message,
    });
    state.activeSession = result.session;
    state.activeSnapshot = result.diagnostics_snapshot || null;
    await loadSessions();
    renderActiveSession();
    renderDiagnostics();
  } catch (error) {
    els.chatInput.value = message;
    autoResizeComposer();
    if (state.activeSession?.id === "__pending__") {
      state.activeSession = null;
    }
    showToast(error.message || "发送失败", "error");
  } finally {
    clearSendState();
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

async function refreshActiveSessionFromServer() {
  if (!state.activeSessionId) return;
  const result = await apiGet("chat_get_session", { session_id: state.activeSessionId });
  state.activeSession = result.session || state.activeSession;
  state.activeSnapshot = result.snapshot || state.activeSnapshot;
  renderActiveSession();
  renderDiagnostics();
}

async function handleProposedEdit(action, eventTarget) {
  const button = eventTarget.closest("[data-edit-action]");
  if (!button || !state.activeSessionId) return;

  const messageId = button.dataset.messageId || "";
  const editId = button.dataset.editId || "";
  const backupId = button.dataset.backupId || "";
  const message = (state.activeSession?.messages || []).find((item) => String(item.id) === String(messageId));
  const edit = (message?.proposed_edits || []).find((item) => {
    const candidateId = item.id || item.edit_id || item.proposed_edit_id || item.path;
    return String(candidateId) === String(editId);
  }) || {
    id: editId,
    backup_id: backupId,
  };

  if (!isDirectApplyEnabled()) {
    const confirmation = await showEditConfirm({ action, edit });
    if (!confirmation?.confirmed) {
      return;
    }
    if (confirmation.skipFuture) {
      setDirectApplyEnabled(true);
    }
  }

      const actionName = action === "restore" ? "chat_restore_backup" : "chat_apply_proposed_edits";
  const operationKey = getEditOperationKey(action, messageId, editId);
  state.editOperations[operationKey] = true;
  renderActiveSession();
  setComposerStatus(
    action === "restore" ? "正在恢复备份并刷新会话..." : "正在应用修改并创建备份...",
    "busy"
  );

  try {
    const result = await apiPost(actionName, {
      session_id: state.activeSessionId,
      message_id: messageId,
      proposed_edit_id: editId,
      edit_id: editId,
      backup_id: backupId,
    });
    if (result.session) {
      state.activeSession = result.session;
    }
    if (result.diagnostics_snapshot) {
      state.activeSnapshot = result.diagnostics_snapshot;
    }
    await loadSessions();
    if (result.session || result.diagnostics_snapshot) {
      renderActiveSession();
      renderDiagnostics();
    } else {
      await refreshActiveSessionFromServer();
    }
    showToast(action === "restore" ? "备份恢复请求已完成" : "修改应用请求已完成", "success");
  } catch (error) {
    showToast(error.message || (action === "restore" ? "恢复备份失败" : "应用修改失败"), "error");
  } finally {
    delete state.editOperations[operationKey];
    setComposerStatus("");
    renderActiveSession();
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
    if (copyButton) {
      try {
        await navigator.clipboard.writeText(copyButton.dataset.copy);
        showToast("已复制到剪贴板", "success");
      } catch {
        showToast("复制失败", "error");
      }
      return;
    }

    const editButton = event.target.closest("[data-edit-action]");
    if (editButton) {
      await handleProposedEdit(editButton.dataset.editAction, event.target);
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

  if (els.collapseSessionsButton) {
    els.collapseSessionsButton.addEventListener("click", () => {
      state.ui.sessionsCollapsed = !state.ui.sessionsCollapsed;
      applyPaneVisibilityState();
      saveUiPrefs();
    });
  }
  if (els.collapseDiagnosticsButton) {
    els.collapseDiagnosticsButton.addEventListener("click", () => {
      state.ui.diagnosticsCollapsed = !state.ui.diagnosticsCollapsed;
      applyPaneVisibilityState();
      saveUiPrefs();
    });
  }
  if (els.toggleEditConfirmButton) {
    els.toggleEditConfirmButton.addEventListener("click", () => {
      if (!state.activeSessionId) return;
      const next = !isDirectApplyEnabled();
      setDirectApplyEnabled(next);
      showToast(next ? "当前会话已切换为直接修改" : "当前会话已恢复为修改前确认", "success");
    });
  }

  document.querySelectorAll("[data-close-modal]").forEach((button) => {
    button.addEventListener("click", () => {
      const modalId = button.getAttribute("data-close-modal");
      if (modalId === "prompt-modal") {
        resolvePrompt("");
      } else if (modalId === "confirm-modal") {
        resolveEditConfirm({ confirmed: false, skipFuture: els.confirmModalSkip.checked });
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

  els.confirmEditButton.addEventListener("click", () => {
    resolveEditConfirm({
      confirmed: true,
      skipFuture: !!els.confirmModalSkip.checked,
    });
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
  loadUiPrefs();
  applyPaneVisibilityState();
  bindEvents();
  renderSuggestedPrompts();
  autoResizeComposer();
  updateEditConfirmControl();

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
