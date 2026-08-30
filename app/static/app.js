"use strict";

/*
 * RelicScope browser contract. All workflow state displayed by this console is
 * obtained from these same-origin API endpoints; the browser never fabricates
 * scientific measurements, evidence decisions, audit hashes, or reports.
 */
const API = Object.freeze({
  health: "/api/health",
  sessions: "/api/sessions",
  session: (id) => `/api/sessions/${encodeURIComponent(id)}`,
  imageAnalyze: (id) => `/api/sessions/${encodeURIComponent(id)}/images/analyze`,
  imageCompare: (id) => `/api/sessions/${encodeURIComponent(id)}/images/compare`,
  videoRegister: (id) => `/api/sessions/${encodeURIComponent(id)}/videos/register`,
  videoAnalyze: (id, videoId) => `/api/sessions/${encodeURIComponent(id)}/videos/${encodeURIComponent(videoId)}/analyze`,
  knowledgeSearch: (id) => `/api/sessions/${encodeURIComponent(id)}/knowledge/search`,
  plan: (id) => `/api/sessions/${encodeURIComponent(id)}/plan`,
  execute: (id) => `/api/sessions/${encodeURIComponent(id)}/execute`,
  evidence: (id) => `/api/sessions/${encodeURIComponent(id)}/evidence`,
  audit: (id) => `/api/sessions/${encodeURIComponent(id)}/audit`,
  report: (id) => `/api/sessions/${encodeURIComponent(id)}/report`,
  reportJson: (id) => `/api/sessions/${encodeURIComponent(id)}/report.json`,
  reportHtml: (id) => `/api/sessions/${encodeURIComponent(id)}/report.html`,
});

const STATUS_TEXT = Object.freeze({
  created: "已建档",
  ready: "就绪",
  action_reserved: "动作已预留",
  quality_failed: "质量门禁失败",
  evidence_updated: "证据已更新",
  complete: "检测阶段完成",
  abstained: "已弃权",
  online: "ONLINE",
  ready_online: "READY",
  degraded: "DEGRADED",
  disabled: "DISABLED",
  offline: "OFFLINE",
  unhealthy: "UNHEALTHY",
  checking: "CHECKING",
  review_required: "REVIEW REQUIRED",
  evidence_insufficient: "EVIDENCE INSUFFICIENT",
  support: "SUPPORT",
  conflict: "CONFLICT",
  uncertain: "UNCERTAIN",
  escalate: "ESCALATE",
  rejected: "REJECTED",
});

const EVENT_TEXT = Object.freeze({
  SESSION_CREATED: "科学鉴证会话已创建",
  IMAGE_ANALYZED: "图像分析与指纹已登记",
  IMAGE_INGESTED: "原始图像已登记",
  IMAGE_COMPARISON_COMPLETED: "同区域复拍对比已登记",
  IMAGES_COMPARED: "同区域复拍对比已登记",
  VIDEO_REGISTERED: "原始视频与文件哈希已登记",
  VIDEO_FRAMES_ANALYZED: "视频多帧质量与观察已登记",
  VIDEO_ANALYZED: "视频多帧质量与观察已登记",
  KNOWLEDGE_SEARCHED: "本地知识检索完成",
  LOCAL_KNOWLEDGE_RETRIEVED: "本地知识检索完成",
  ACTION_PLANNED: "下一检测已规划并预留",
  ACTION_RESERVED: "风险预算已原子预留",
  ACTION_PLANNED_AND_RESERVED: "下一检测已规划并原子预留",
  ACTION_EXECUTION_CLAIMED: "仪器执行权已原子取得",
  ACTION_EXECUTED: "仪器回放已执行并结算",
  INSTRUMENT_EXECUTED: "仪器回放已执行并结算",
  ACTION_EXECUTED_AND_SETTLED: "仪器回放已执行并结算",
  REPORT_GENERATED: "可审计报告已生成",
});

const GRAPH_COLORS = Object.freeze({
  artifact: "#5d9eff",
  claim: "#f0ba63",
  region: "#67d89c",
  raw: "#8ba2b8",
  observation: "#50d8df",
  action: "#ff7c76",
  evidence: "#67d89c",
  report: "#a997ff",
  reference: "#8ba2b8",
  model_run: "#a997ff",
});

const MODALITY_VIEWS = Object.freeze({
  all: {
    kicker: "FOUR SCIENTIFIC VIEWS",
    title: "从外观到材料，再到内部与年代约束",
    role: "ONE EVIDENCE SYSTEM",
    answer: "不同模态回答不同问题",
    description: "光学、微表面、材料与内部证据回到同一器物坐标；任何单项信号都不被直接解释为真伪结论。",
    productRole: "V1 核心 + 外接接口 + Lab 网络",
    boundary: "结果需要标定、参考样本与专家解释",
  },
  optical: {
    kicker: "RGB · UV · NIR",
    title: "记录形态、纹饰、荧光与区域差异",
    role: "LOW-RISK BASELINE",
    answer: "先建立可重复的视觉基线",
    description: "标准彩色、紫外与近红外观察适合低风险筛查，也为后续材料检测提供位置明确的目标区域。",
    productRole: "Scout / Vault V1 核心",
    boundary: "图像差异依赖受控光源、标定与材料背景",
  },
  surface: {
    kicker: "RTI · PHOTOMETRIC STEREO · 3D",
    title: "看见工具痕、磨损、开片与微地形",
    role: "MICRO-SURFACE",
    answer: "把肉眼难以稳定比较的表面保存下来",
    description: "多方向照明与三维几何帮助比较同一区域的制作痕迹和状态变化，并支持跨时间复检。",
    productRole: "Vault V1 核心 / 三维接口",
    boundary: "表面异常候选不等于真伪；形貌不能替代材料或年代检测",
  },
  material: {
    kicker: "RAMAN · XRF · HSI",
    title: "分子、元素与连续光谱提供材料证据",
    role: "MATERIAL EVIDENCE",
    answer: "当视觉证据不足，再回答材料问题",
    description: "拉曼、XRF 与高光谱分别从分子物相、元素组成和空间光谱差异补充证据，并由主动检测决定是否接入。",
    productRole: "V1 外接接口 / 二期能力",
    boundary: "荧光、矩阵效应、层结构与光谱库都会影响解释",
  },
  internal: {
    kicker: "X-RAY · CT · TL",
    title: "内部结构与年代约束进入升级检测网络",
    role: "LAB ESCALATION",
    answer: "深层问题交给具备条件的专业实验室",
    description: "X射线与 CT 观察内部结构；TL 等年代方法需要专业取样与实验条件，结果再回到同一证据图。",
    productRole: "机构版后续 / Lab 网络",
    boundary: "涉及辐射安全、对象尺寸、取样和法规要求",
  },
});

const state = {
  health: null,
  sessionId: null,
  session: null,
  envelope: null,
  imageFile: null,
  imageObjectUrl: null,
  imageAnalysis: null,
  imageComparison: null,
  mediaMode: "image",
  videoFile: null,
  videoObjectUrl: null,
  videoRecord: null,
  videoAnalysis: null,
  sampledFrames: [],
  videoBusy: false,
  knowledgePayload: null,
  evidencePayload: null,
  auditPayload: null,
  reportPayload: null,
  demoRunning: false,
  demoPhase: "ready",
};

let evidenceGraphCompactMode = null;

const $ = (id) => document.getElementById(id);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function syncBodyStateClasses() {
  if (!document.body) return;
  const session = state.session;
  const hasMediaAnalysis = Boolean(
    state.imageAnalysis
    || state.videoAnalysis
    || (Array.isArray(session?.image_analyses) && session.image_analyses.length)
    || (Array.isArray(session?.video_analyses) && session.video_analyses.length),
  );
  document.body.classList.toggle("has-session", Boolean(state.sessionId && session));
  document.body.classList.toggle("has-media-analysis", hasMediaAnalysis);
  document.body.classList.toggle("has-comparable-images", Boolean(comparableImagePair()));
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  bindModalityExplorer();
  startClock();
  syncBodyStateClasses();
  renderShowcase();
  updateControlAvailability();
  loadHealth();
});

function bindEvents() {
  $("refresh-health").addEventListener("click", loadHealth);
  $("session-form").addEventListener("submit", onCreateSession);
  $("image-file").addEventListener("change", onImageSelected);
  $("image-form").addEventListener("submit", onAnalyzeImage);
  $("compare-images").addEventListener("click", onCompareImages);
  $("video-file").addEventListener("change", onVideoSelected);
  $("video-form").addEventListener("submit", onAnalyzeVideo);
  for (const button of $$('[data-media-tab]')) button.addEventListener("click", () => selectMediaMode(button.dataset.mediaTab));
  bindDropTarget("image-drop-target", "image-file", (file) => selectImageFile(file));
  bindDropTarget("video-drop-target", "video-file", (file) => selectVideoFile(file));
  $("knowledge-form").addEventListener("submit", onKnowledgeSearch);
  $("plan-action").addEventListener("click", () => planNextAction());
  $("execute-action").addEventListener("click", () => executeReservedAction());
  $("run-demo").addEventListener("click", runP01Demo);
  $("refresh-evidence").addEventListener("click", refreshEvidence);
  $("refresh-audit").addEventListener("click", refreshAudit);
  $("generate-report").addEventListener("click", () => generateReport());
  window.addEventListener("resize", () => {
    const compact = window.matchMedia?.("(max-width: 680px)")?.matches === true;
    if (compact === evidenceGraphCompactMode) return;
    renderEvidenceGraph(state.evidencePayload);
  });

  for (const link of [$("download-json"), $("download-html")]) {
    link.addEventListener("click", (event) => {
      if (link.getAttribute("aria-disabled") === "true") event.preventDefault();
    });
  }
}

function bindModalityExplorer() {
  for (const button of $$("[data-modality]")) {
    button.addEventListener("click", () => {
      const key = button.dataset.modality;
      const view = MODALITY_VIEWS[key] || MODALITY_VIEWS.all;
      for (const item of $$("[data-modality]")) {
        const selected = item === button;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-selected", String(selected));
      }
      $("modality-object").dataset.activeModality = key;
      $("modality-stage-kicker").textContent = view.kicker;
      $("modality-stage-title").textContent = view.title;
      $("modality-role").textContent = view.role;
      $("modality-answer").textContent = view.answer;
      $("modality-description").textContent = view.description;
      $("modality-product-role").textContent = view.productRole;
      $("modality-boundary-copy").textContent = view.boundary;
    });
  }
}

function startClock() {
  const tick = () => {
    $("local-clock").textContent = new Intl.DateTimeFormat("zh-CN", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date());
  };
  tick();
  window.setInterval(tick, 1000);
}

async function request(path, options = {}) {
  const requestOptions = {
    method: options.method || "GET",
    headers: { Accept: "application/json", ...(options.headers || {}) },
    signal: options.signal,
  };

  if (options.body !== undefined) {
    requestOptions.headers["Content-Type"] = "application/json";
    requestOptions.body = JSON.stringify(options.body);
  }

  const response = await fetch(path, requestOptions);
  const contentType = response.headers.get("content-type") || "";
  let payload = null;
  if (contentType.includes("application/json")) {
    payload = await response.json().catch(() => null);
  } else {
    payload = await response.text().catch(() => "");
  }

  if (!response.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail ?? payload.message ?? payload.error : payload;
    throw new Error(formatApiError(detail, response.status));
  }
  return payload;
}

async function requestForm(path, formData, options = {}) {
  const response = await fetch(path, {
    method: options.method || "POST",
    headers: { Accept: "application/json", ...(options.headers || {}) },
    body: formData,
    signal: options.signal,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text().catch(() => "");
  if (!response.ok) {
    const detail = payload && typeof payload === "object" ? payload.detail ?? payload.message ?? payload.error : payload;
    throw new Error(formatApiError(detail, response.status));
  }
  return payload;
}

function formatApiError(detail, status) {
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || item.message || JSON.stringify(item)).join("；");
  }
  if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
  return detail ? String(detail) : `请求失败（HTTP ${status}）`;
}

function acceptEnvelope(payload) {
  if (!payload || typeof payload !== "object") return null;
  const session = payload.session && typeof payload.session === "object"
    ? payload.session
    : payload.id && (payload.artifact || payload.claim || payload.risk_budgets)
      ? payload
      : null;

  if (session) {
    state.session = session;
    state.sessionId = String(session.id || session.session_id || state.sessionId || "");
  }
  if (payload.session) state.envelope = payload;
  if (typeof payload.audit_verified === "boolean" || Number.isFinite(payload.audit_event_count)) {
    state.envelope = { ...(state.envelope || {}), ...payload, session: state.session };
  }
  syncBodyStateClasses();
  renderSession();
  renderShowcase();
  return session;
}

function renderShowcase() {
  const session = state.session;
  const graph = extractGraph(state.evidencePayload || state.envelope || session || null);
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
  const report = state.reportPayload || session?.last_report || session?.report || session?.latest_report || null;
  const uncertainty = clampNumber(
    firstDefined(session?.uncertainty, getDeep(session, "proposition.uncertainty"), 0.85),
    0,
    1,
  );

  $("identity-artifact-name").textContent = session?.artifact?.name || session?.artifact_name || "等待建立器物档案";
  const claim = session?.claim || {};
  $("identity-claim").textContent = session
    ? [claim.period, claim.kiln, claim.material].filter(Boolean).join(" · ") || "待验证器物声明"
    : "声明、测量、来源与专家意见将在这里汇合。";
  $("identity-session").textContent = state.sessionId ? shortHash(state.sessionId, 18) : "NOT ISSUED";
  $("identity-evidence-count").textContent = `${nodes.length} NODES`;
  $("identity-uncertainty").textContent = uncertainty.toFixed(2);

  const identityStatus = normalizeStatus(firstDefined(report?.claim_consistency, session?.claim_consistency, session?.status, "waiting"));
  const identityBadge = $("identity-state");
  identityBadge.textContent = state.sessionId ? statusText(identityStatus) : "WAITING";
  identityBadge.className = `badge badge--${state.sessionId ? badgeKind(identityStatus) : "outline"}`;

  const auditVerified = firstDefined(
    state.auditPayload?.audit_verified,
    state.auditPayload?.verified,
    getDeep(state.auditPayload, "verification.valid"),
    state.envelope?.audit_verified,
    null,
  );
  $("identity-integrity").textContent = auditVerified === true ? "CHAIN VERIFIED" : auditVerified === false ? "CHECK FAILED" : "WAITING";

  const evaluations = extractActionEvaluations(session);
  const current = session?.current_action_id
    ? evaluations.find((item) => item.id === session.current_action_id)
    : null;
  const latestExecution = lastItem(session?.executions);
  const sessionStatus = normalizeStatus(session?.status || "");
  const automaticStop = Boolean(report) || sessionStatus === "complete" || uncertainty <= 0.5;
  const actionBadge = $("spotlight-action-state");
  if (automaticStop) {
    $("spotlight-action").textContent = "自动检测已停止，进入专家复核";
    $("spotlight-action-reason").textContent = "当前合格证据已达到演示协议停止条件；系统保留不确定性，不再自动追加检测。";
    actionBadge.textContent = "STOP · EXPERT REVIEW";
    actionBadge.className = "badge badge--success";
  } else if (current) {
    $("spotlight-action").textContent = current.label || current.id || "下一检测已预留";
    $("spotlight-action-reason").textContent = Array.isArray(current.reasons)
      ? current.reasons.join("；")
      : current.reason || `预计降低不确定性 ${formatNumber(current.information_gain, 3)}，并已通过风险硬约束。`;
    actionBadge.textContent = "RESERVED";
    actionBadge.className = "badge badge--info";
  } else if (latestExecution?.quality_gate?.passed === false) {
    $("spotlight-action").textContent = "质量失败：等待受约束重规划";
    $("spotlight-action-reason").textContent = "失败测量不进入科学结论；已经发生的物理暴露仍计入区域风险账本。";
    actionBadge.textContent = "REPLAN";
    actionBadge.className = "badge badge--warning";
  } else {
    $("spotlight-action").textContent = session?.next_step || "等待建立科学命题";
    $("spotlight-action-reason").textContent = session
      ? "系统将在区域风险预算内评估信息价值、成本和执行权限。"
      : "系统将以信息价值、对象风险、成本和权限共同选择下一步。";
    actionBadge.textContent = session ? "READY TO PLAN" : "NOT PLANNED";
    actionBadge.className = `badge badge--${session ? "outline" : "outline"}`;
  }

  const evidenceStates = nodes.map((node) => normalizeStatus(node.status || node.type || ""));
  const hasConflict = evidenceStates.some((value) => value.includes("conflict"));
  $("spotlight-evidence").textContent = nodes.length
    ? `${nodes.length} 个节点组成可追溯证据关系`
    : "证据尚未形成";
  $("spotlight-evidence-detail").textContent = nodes.length
    ? `${hasConflict ? "冲突与支持状态均被保留；" : "测量、来源与声明均保留版本；"}完整性状态 ${auditVerified === true ? "已验证" : "待验证"}。`
    : "每项判断都必须回到区域、原始测量、来源和版本。";

  const reportState = normalizeStatus(firstDefined(report?.claim_consistency, report?.status, session?.claim_consistency, "evidence_insufficient"));
  const reportBadge = $("spotlight-report-state");
  reportBadge.textContent = statusText(reportState);
  reportBadge.className = `badge badge--${badgeKind(reportState)}`;
}

function renderShowcaseNodes(nodeA, nodeB) {
  for (const [prefix, node] of [["a", nodeA], ["b", nodeB]]) {
    const status = normalizeStatus(node?.status || "checking");
    $(`hero-node-${prefix}-status`).textContent = statusText(status);
    const dot = $(`hero-node-${prefix}-dot`);
    dot.className = status === "online" || status === "ready"
      ? "is-online"
      : status === "offline" || status === "unhealthy"
        ? "is-offline"
        : "";
  }
}

async function loadHealth() {
  const button = $("refresh-health");
  setButtonLoading(button, true, "刷新中");
  try {
    state.health = await request(API.health);
    renderHealth(state.health);
  } catch (error) {
    renderHealthError(error);
    toast("无法读取运行状态", error.message, "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function renderHealth(health) {
  const status = normalizeStatus(health?.status || "degraded");
  const components = normalizeHealthComponents(health);
  const nodeA = aggregateNode(components.filter((item) => item.node === "A"), "A");
  const nodeB = aggregateNode(components.filter((item) => item.node === "B"), "B", health);
  renderNode("a", nodeA);
  renderNode("b", nodeB);
  renderShowcaseNodes(nodeA, nodeB);

  const mode = firstDefined(health?.mode, health?.runtime_mode, health?.topology?.mode, "LOCAL / CHECKING");
  const modeToken = String(mode).trim().toLowerCase().replaceAll("_", "-");
  const dualNode = modeToken === "dual-node";
  $("node-a-name").textContent = dualNode ? "Spark A" : "逻辑 A（本机回退）";
  $("node-b-name").textContent = dualNode ? "Spark B" : "逻辑 B（本机回退）";
  $("brand-runtime-subtitle").textContent = dualNode
    ? "古陶瓷多模态科学鉴证 · 双 Spark 本地控制台"
    : "古陶瓷多模态科学鉴证 · 本机降级控制台";
  $("node-link-label").textContent = dualNode ? "PRIVATE API" : "LOCAL LOOPBACK";
  $("node-link-label").closest(".node-link").setAttribute(
    "aria-label",
    dualNode ? "受控私网 API 双向连接" : "本机逻辑服务回环连接",
  );
  const knowledgeVersion = firstDefined(
    health?.knowledge_version,
    health?.knowledge?.version,
    getDeep(state.session, "knowledge.version"),
    getDeep(state.session, "knowledge_version"),
    "—",
  );
  $("runtime-mode").textContent = humanizeMode(mode);
  $("knowledge-version").textContent = String(knowledgeVersion);
  $("knowledge-snapshot").textContent = String(knowledgeVersion);
  $("data-boundary").textContent = formatDataBoundary(health?.data_boundary, health);
  $("health-checked-at").textContent = formatTime(health?.checked_at || new Date().toISOString());

  const globalPill = $("global-system-pill");
  const globalDot = globalPill.querySelector(".status-dot");
  globalDot.className = `status-dot status-dot--${statusClass(status)}`;
  $("global-system-status").textContent = status === "online" || status === "ready"
    ? dualNode ? "双节点服务可用" : "本机逻辑服务可用"
    : status === "degraded"
      ? "本地降级模式"
      : `系统 ${statusText(status)}`;
}

function normalizeHealthComponents(health) {
  const raw = Array.isArray(health?.components)
    ? health.components
    : Array.isArray(health?.nodes)
      ? health.nodes
      : health?.services && typeof health.services === "object"
        ? Object.entries(health.services).map(([name, value]) => ({ name, ...value }))
        : [];

  return raw.map((component, index) => {
    const name = String(component.name || component.id || component.service || `service-${index + 1}`);
    const role = String(component.role || component.detail || "");
    const nodeHint = String(component.node || component.node_id || component.host || "").toUpperCase();
    const search = `${name} ${role} ${nodeHint}`.toLowerCase();
    let node = "B";
    if (/spark[-_ ]?a|vision|visual|perception|embedding|multimodal|compute/.test(search)) node = "A";
    if (/spark[-_ ]?b|gateway|reason|evidence|knowledge|store|orchestrat|application/.test(search)) node = "B";
    return {
      node,
      name,
      role: component.role || component.logical_role || "",
      status: normalizeStatus(component.status || component.ready || "degraded"),
      detail: component.detail || component.message || component.endpoint || "服务状态已返回",
      model: component.model || component.model_id || component.version || "",
      latency: component.latency_ms ?? component.latency ?? null,
    };
  });
}

function aggregateNode(items, node, health = null) {
  if (!items.length) {
    return {
      status: normalizeStatus(health?.status || "degraded"),
      role: node === "A" ? "感知 · 向量化 · 检索" : "编排 · 证据 · 报告",
      model: node === "A" ? "未报告感知模型" : "确定性科学决策",
      detail: node === "A" ? "未返回独立节点组件" : health?.detail || "Gateway 响应可用",
      latency: null,
    };
  }
  const usable = items.filter((item) => item.status === "online" || item.status === "ready");
  const down = items.filter((item) => item.status === "offline" || item.status === "unhealthy");
  const limited = items.filter((item) => ["degraded", "disabled", "checking", "demo"].includes(item.status));
  const status = down.length === items.length
    ? "offline"
    : limited.length || down.length
      ? "degraded"
      : usable.length
        ? "online"
        : "checking";
  const models = unique(items.map((item) => item.model).filter(Boolean));
  const latencies = items.map((item) => Number(item.latency)).filter(Number.isFinite);
  return {
    status,
    role: unique(items.map((item) => item.role || item.name)).join(" · "),
    model: models.join(" / ") || (node === "A" ? "确定性感知回退" : "确定性科学决策"),
    detail: items.map((item) => `${item.name}: ${item.detail}`).join("；"),
    latency: latencies.length ? Math.max(...latencies) : null,
  };
}

function renderNode(prefix, node) {
  const statusElement = $(`node-${prefix}-status`);
  statusElement.textContent = statusText(node.status);
  statusElement.className = `badge badge--${badgeKind(node.status)}`;
  const roleElement = $(`node-${prefix}-role`);
  const modelElement = $(`node-${prefix}-model`);
  const detailElement = $(`node-${prefix}-detail`);
  roleElement.textContent = truncate(node.role, 54);
  roleElement.title = node.role;
  modelElement.textContent = truncate(node.model, 58);
  modelElement.title = node.model;
  detailElement.textContent = summarizeServiceDetail(node.detail);
  detailElement.title = node.detail;
  $(`node-${prefix}-latency`).textContent = Number.isFinite(node.latency) ? `${Math.round(node.latency)} ms` : "—";
}

function summarizeServiceDetail(value) {
  const text = String(value || "");
  const entries = text.split("；").map((item) => item.trim()).filter(Boolean);
  if (entries.length <= 1) return truncate(text, 82);
  const names = entries.map((item) => item.split(":", 1)[0].trim()).filter(Boolean);
  const visible = names.slice(0, 3).join(" · ");
  return `${visible}${names.length > 3 ? ` · +${names.length - 3}` : ""}（详情悬停）`;
}

function renderHealthError(error) {
  for (const prefix of ["a", "b"]) {
    renderNode(prefix, {
      status: "offline",
      role: prefix === "a" ? "感知 · 向量化 · 检索" : "编排 · 证据 · 报告",
      model: "状态不可用",
      detail: error.message,
      latency: null,
    });
  }
  $("global-system-pill").querySelector(".status-dot").className = "status-dot status-dot--offline";
  $("global-system-status").textContent = "服务入口不可达";
  $("node-a-name").textContent = "逻辑 A（状态未知）";
  $("node-b-name").textContent = "逻辑 B（状态未知）";
  $("brand-runtime-subtitle").textContent = "古陶瓷多模态科学鉴证 · 运行拓扑不可用";
  $("node-link-label").textContent = "LINK OFFLINE";
  $("runtime-mode").textContent = "OFFLINE / UNKNOWN";
  $("health-checked-at").textContent = formatTime(new Date().toISOString());
  renderShowcaseNodes({ status: "offline" }, { status: "offline" });
}

async function onCreateSession(event) {
  event.preventDefault();
  await createSession({ button: $("create-session") });
}

function sessionRequestBody() {
  return {
    artifact_name: $("artifact-name").value.trim(),
    operator: $("operator").value.trim(),
    institution: $("institution").value.trim(),
    claim: {
      period: $("claim-period").value.trim(),
      kiln: $("claim-kiln").value.trim(),
      material: $("claim-material").value.trim(),
      provenance_note: $("provenance-note").value.trim(),
    },
  };
}

async function createSession({ button = null, quiet = false } = {}) {
  if (button) setButtonLoading(button, true, "正在建档");
  try {
    const payload = await request(API.sessions, { method: "POST", body: sessionRequestBody() });
    acceptEnvelope(payload);
    if (!state.sessionId) throw new Error("服务响应缺少会话标识");
    resetPerSessionView();
    acceptEnvelope(payload);
    await Promise.allSettled([refreshEvidence({ quiet: true }), refreshAudit({ quiet: true })]);
    if (!quiet) {
      toast("会话已建立", `Session ${shortHash(state.sessionId, 16)}`, "success");
      window.requestAnimationFrame(() => window.requestAnimationFrame(scrollToMediaIngest));
    }
    return state.session;
  } catch (error) {
    if (!quiet) toast("建档失败", error.message, "error");
    throw error;
  } finally {
    if (button) setButtonLoading(button, false);
    updateControlAvailability();
  }
}

function scrollToMediaIngest() {
  const target = $("media-ingest");
  if (!target) return;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  const stickyHeader = document.querySelector(".topbar");
  const offset = (stickyHeader?.getBoundingClientRect().height || 0) + 16;
  const top = Math.max(0, window.scrollY + target.getBoundingClientRect().top - offset);
  try {
    window.scrollTo({ top, behavior: reducedMotion ? "auto" : "smooth" });
  } catch {
    target.scrollIntoView?.({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
  }
}

function resetPerSessionView() {
  state.imageAnalysis = null;
  state.imageComparison = null;
  state.videoRecord = null;
  state.videoAnalysis = null;
  state.sampledFrames = [];
  state.knowledgePayload = null;
  state.evidencePayload = null;
  state.auditPayload = null;
  state.reportPayload = null;
  renderImageAnalysis(null);
  renderImageComparison(null);
  renderVideoFrames([]);
  renderMediaAnalysis(null, state.mediaMode);
  renderKnowledge(null);
  renderEvidenceGraph(null);
  renderAudit(null);
  renderReport(null);
  resetDemoProgress();
  renderShowcase();
}

async function refreshSession({ quiet = false } = {}) {
  if (!state.sessionId) return null;
  try {
    const payload = await request(API.session(state.sessionId));
    acceptEnvelope(payload);
    return payload;
  } catch (error) {
    if (!quiet) toast("会话刷新失败", error.message, "error");
    throw error;
  }
}

function renderSession() {
  const session = state.session;
  syncBodyStateClasses();
  if (!session) {
    updateControlAvailability();
    return;
  }

  const status = normalizeStatus(session.status || "created");
  const statusBadge = $("session-state-badge");
  statusBadge.textContent = statusText(status);
  statusBadge.className = `badge badge--${badgeKind(status)}`;
  $("session-id").textContent = state.sessionId || "—";
  $("session-protocol").textContent = stringifyProtocol(session.protocol || session.protocol_id || "—");
  $("session-version").textContent = String(session.version ?? "—");
  $("session-next-step").textContent = session.next_step || "等待下一操作";

  const claim = session.claim || {};
  $("claim-summary").textContent = [claim.period, claim.kiln, claim.material].filter(Boolean).join(" · ") || "待验证器物声明";

  const uncertainty = clampNumber(
    firstDefined(session.uncertainty, getDeep(session, "proposition.uncertainty"), getDeep(session, "proposition_state.uncertainty"), 0.85),
    0,
    1,
  );
  $("uncertainty-value").textContent = uncertainty.toFixed(2);
  $("uncertainty-gauge").style.setProperty("--uncertainty", String(Math.round(uncertainty * 100)));
  $("threshold-status").textContent = uncertainty <= 0.5 ? "已达到，停止自动检测" : "尚未达到";
  $("uncertainty-explanation").textContent = uncertainty <= 0.5
    ? "关键命题达到演示协议停止阈值，进入专家复核与可审计报告阶段。"
    : "关键命题仍有不确定性；系统仅在安全硬约束内选择有价值的下一项检测。";

  const consistency = normalizeStatus(session.claim_consistency || (status === "abstained" ? "EVIDENCE_INSUFFICIENT" : "EVIDENCE_INSUFFICIENT"));
  const consistencyBadge = $("claim-consistency");
  consistencyBadge.textContent = statusText(consistency);
  consistencyBadge.className = `badge badge--${badgeKind(consistency)}`;

  const knowledgeVersion = firstDefined(
    session.knowledge_version,
    getDeep(session, "knowledge.version"),
    getDeep(session, "last_knowledge_search.knowledge_version"),
  );
  if (knowledgeVersion) {
    $("knowledge-version").textContent = String(knowledgeVersion);
    $("knowledge-snapshot").textContent = String(knowledgeVersion);
  }

  renderRiskBudgets(session.risk_budgets || session.risk_budget || {});
  renderCandidateActions(extractActionEvaluations(session));
  renderExecutionTimeline();
  renderReport(state.reportPayload || session.last_report || session.report || session.latest_report || null);
  updateControlAvailability();
}

function stringifyProtocol(protocol) {
  if (typeof protocol === "string") return protocol;
  if (protocol && typeof protocol === "object") return protocol.id || protocol.name || protocol.version || JSON.stringify(protocol);
  return "—";
}

function selectMediaMode(mode) {
  const selected = mode === "video" ? "video" : "image";
  state.mediaMode = selected;
  for (const button of $$('[data-media-tab]')) {
    const active = button.dataset.mediaTab === selected;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  }
  $("image-input-pane").hidden = selected !== "image";
  $("video-input-pane").hidden = selected !== "video";
  const analysis = selected === "video" ? state.videoAnalysis : state.imageAnalysis;
  renderMediaAnalysis(analysis, selected);
  updateControlAvailability();
}

function bindDropTarget(targetId, inputId, onFile) {
  const target = $(targetId);
  const input = $(inputId);
  const stop = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };
  for (const name of ["dragenter", "dragover"]) {
    target.addEventListener(name, (event) => {
      stop(event);
      target.classList.add("is-dragover");
    });
  }
  for (const name of ["dragleave", "drop"]) {
    target.addEventListener(name, (event) => {
      stop(event);
      target.classList.remove("is-dragover");
    });
  }
  target.addEventListener("drop", (event) => {
    const [file] = event.dataTransfer?.files || [];
    if (!file) return;
    try {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
    } catch {
      // Some browsers intentionally make FileList immutable; state still uses the dropped File.
    }
    onFile(file);
  });
}

function onImageSelected(event) {
  const [file] = event.target.files || [];
  selectImageFile(file || null);
}

function selectImageFile(file) {
  if (file && !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    toast("无法载入图像", "请选择 JPEG、PNG 或 WEBP 文件。", "error");
    return;
  }
  state.imageFile = file || null;
  state.imageAnalysis = null;
  if (state.imageObjectUrl) URL.revokeObjectURL(state.imageObjectUrl);
  if (file) {
    state.imageObjectUrl = URL.createObjectURL(file);
    $("artifact-preview").src = state.imageObjectUrl;
    $("artifact-preview").alt = `已选择的器物图像：${file.name}`;
    $("image-caption").textContent = `${file.name} · ${formatBytes(file.size)} · 等待服务端分析`;
  } else {
    state.imageObjectUrl = null;
    $("artifact-preview").src = "/static/ceramic-placeholder.svg";
    $("artifact-preview").alt = "古陶瓷图像上传占位示意";
    $("image-caption").textContent = "等待本地图像 · 不上传公网";
  }
  renderImageAnalysis(null);
  renderMediaAnalysis(null, "image");
  updateControlAvailability();
}

async function onAnalyzeImage(event) {
  event.preventDefault();
  if (!state.sessionId || !state.imageFile) return;
  const button = $("analyze-image");
  setButtonLoading(button, true, "分析中");
  try {
    const base64 = await fileToBase64(state.imageFile);
    const payload = await request(API.imageAnalyze(state.sessionId), {
      method: "POST",
      body: {
        filename: state.imageFile.name,
        mime_type: state.imageFile.type || "image/jpeg",
        image_base64: base64,
        modality: $("image-modality").value,
        region_id: $("image-region").value,
      },
    });
    acceptEnvelope(payload);
    state.imageAnalysis = extractImageAnalysis(payload, state.session);
    state.imageComparison = null;
    renderImageAnalysis(state.imageAnalysis);
    renderImageComparison(null);
    renderMediaAnalysis(state.imageAnalysis, "image");
    await Promise.allSettled([refreshEvidence({ quiet: true }), refreshAudit({ quiet: true })]);
    toast("图像分析完成", "质量、指纹和原始文件摘要均来自服务端响应。", "success");
  } catch (error) {
    toast("图像分析失败", error.message, "error");
  } finally {
    setButtonLoading(button, false);
    updateControlAvailability();
  }
}

function onVideoSelected(event) {
  const [file] = event.target.files || [];
  selectVideoFile(file || null);
}

function selectVideoFile(file) {
  const allowedTypes = ["video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"];
  const allowedExtension = /\.(mp4|mov|webm|avi)$/i.test(file?.name || "");
  if (file && !allowedTypes.includes(file.type) && !allowedExtension) {
    toast("无法载入视频", "请选择 MP4、MOV、WEBM 或 AVI 视频。", "error");
    return;
  }
  state.videoFile = file || null;
  state.videoRecord = null;
  state.videoAnalysis = null;
  state.sampledFrames = [];
  if (state.videoObjectUrl) URL.revokeObjectURL(state.videoObjectUrl);
  const video = $("artifact-video");
  if (file) {
    state.videoObjectUrl = URL.createObjectURL(file);
    video.src = state.videoObjectUrl;
    video.load();
    $("video-empty").hidden = true;
    $("video-caption").textContent = `${file.name} · ${formatBytes(file.size)} · 正在读取时长`;
    video.onloadedmetadata = () => {
      $("video-caption").textContent = `${file.name} · ${formatBytes(file.size)} · ${formatDuration(video.duration * 1000)} · 等待登记`;
      updateControlAvailability();
    };
  } else {
    state.videoObjectUrl = null;
    video.removeAttribute("src");
    video.load();
    $("video-empty").hidden = false;
    $("video-caption").textContent = "拖入或选择视频 · 浏览器本地抽取代表帧";
  }
  setVideoProgress("等待视频分析", 0, 0);
  renderVideoFrames([]);
  renderMediaAnalysis(null, "video");
  updateControlAvailability();
}

async function onAnalyzeVideo(event) {
  event.preventDefault();
  if (!state.sessionId || !state.videoFile || state.videoBusy) return;
  const button = $("analyze-video");
  const video = $("artifact-video");
  state.videoBusy = true;
  setButtonLoading(button, true, "登记原视频");
  try {
    await ensureVideoReady(video);
    const durationMs = Math.max(1, Math.round(video.duration * 1000));
    const formData = new FormData();
    const inferredMime = videoMimeForFile(state.videoFile);
    const uploadFile = state.videoFile.type === inferredMime
      ? state.videoFile
      : new File([state.videoFile], state.videoFile.name, { type: inferredMime, lastModified: state.videoFile.lastModified });
    formData.append("file", uploadFile, uploadFile.name);
    formData.append("modality", $("video-modality").value);
    formData.append("region_id", $("video-region").value);
    formData.append("duration_ms", String(durationMs));
    formData.append("capture_note", "浏览器端均匀抽帧；用户提供器物环拍视频");
    setVideoProgress("登记原视频与 SHA-256", 1, 12);
    const registration = await requestForm(API.videoRegister(state.sessionId), formData);
    acceptEnvelope(registration);
    state.videoRecord = extractVideoRecord(registration, state.session);
    const videoId = firstDefined(state.videoRecord?.id, state.videoRecord?.video_id, registration?.video_id);
    if (!videoId) throw new Error("服务响应缺少视频标识");

    const requestedCount = clampNumber($("video-frame-count").value, 8, 12);
    setButtonLoading(button, true, "本地抽取代表帧");
    state.sampledFrames = await sampleVideoFrames(video, requestedCount, (done, total) => {
      setVideoProgress("浏览器本地均匀抽帧", 1 + done, 2 + total);
      renderVideoFrames(state.sampledFrames);
    });
    renderVideoFrames(state.sampledFrames);

    setButtonLoading(button, true, "多帧分析中");
    setVideoProgress("服务端质量门控与跨帧融合", requestedCount + 1, requestedCount + 2);
    const payload = await request(API.videoAnalyze(state.sessionId, videoId), {
      method: "POST",
      body: {
        duration_ms: durationMs,
        sampling_strategy: "uniform-browser-v1",
        frames: state.sampledFrames.map((frame) => ({
          timestamp_ms: frame.timestamp_ms,
          mime_type: "image/jpeg",
          image_base64: frame.image_base64,
        })),
      },
    });
    acceptEnvelope(payload);
    state.videoAnalysis = extractVideoAnalysis(payload, state.session);
    renderVideoFrames(state.sampledFrames, state.videoAnalysis);
    renderMediaAnalysis(state.videoAnalysis, "video");
    setVideoProgress("多帧证据已登记", requestedCount + 2, requestedCount + 2);
    await Promise.allSettled([refreshEvidence({ quiet: true }), refreshAudit({ quiet: true })]);
    toast("视频结构化分析完成", "原视频哈希、时间戳帧、质量门控与证据引用已进入同一会话。", "success");
  } catch (error) {
    setVideoProgress("分析中止 · 原因已显示", 0, 1);
    toast("视频分析失败", error.message, "error");
  } finally {
    state.videoBusy = false;
    setButtonLoading(button, false);
    updateControlAvailability();
  }
}

function videoMimeForFile(file) {
  const declared = String(file?.type || "").toLowerCase();
  if (["video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"].includes(declared)) return declared;
  const name = String(file?.name || "").toLowerCase();
  if (name.endsWith(".mov")) return "video/quicktime";
  if (name.endsWith(".webm")) return "video/webm";
  if (name.endsWith(".avi")) return "video/x-msvideo";
  return "video/mp4";
}

function ensureVideoReady(video) {
  if (video.readyState >= 1 && Number.isFinite(video.duration) && video.duration > 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error("无法读取视频元数据，请更换浏览器可解码的格式")), 12000);
    const ready = () => {
      window.clearTimeout(timeout);
      cleanup();
      resolve();
    };
    const failed = () => {
      window.clearTimeout(timeout);
      cleanup();
      reject(new Error("浏览器无法解码该视频"));
    };
    const cleanup = () => {
      video.removeEventListener("loadedmetadata", ready);
      video.removeEventListener("error", failed);
    };
    video.addEventListener("loadedmetadata", ready, { once: true });
    video.addEventListener("error", failed, { once: true });
    video.load();
  });
}

async function sampleVideoFrames(video, count, onProgress = () => {}) {
  await ensureVideoReady(video);
  if (video.readyState < 2) {
    await new Promise((resolve) => video.addEventListener("loadeddata", resolve, { once: true }));
  }
  const duration = video.duration;
  const width = Math.max(1, video.videoWidth);
  const height = Math.max(1, video.videoHeight);
  const scale = Math.min(1, 960 / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(32, Math.round(width * scale));
  canvas.height = Math.max(32, Math.round(height * scale));
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("浏览器无法建立视频抽帧画布");
  const timestamps = Array.from({ length: count }, (_, index) => {
    const ratio = count === 1 ? 0.5 : 0.05 + (index / (count - 1)) * 0.9;
    return Math.min(Math.max(duration * ratio, 0), Math.max(0, duration - 0.03));
  });
  const originalTime = video.currentTime;
  const wasPaused = video.paused;
  video.pause();
  const frames = [];
  for (let index = 0; index < timestamps.length; index += 1) {
    const timestamp = timestamps[index];
    await seekVideo(video, timestamp);
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
    const imageBase64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
    frames.push({
      index,
      timestamp_ms: Math.round(timestamp * 1000),
      data_url: dataUrl,
      image_base64: imageBase64,
      width: canvas.width,
      height: canvas.height,
    });
    state.sampledFrames = frames.slice();
    onProgress(frames.length, timestamps.length);
  }
  video.currentTime = Math.min(originalTime, duration);
  if (!wasPaused) video.play().catch(() => {});
  return frames;
}

function seekVideo(video, seconds) {
  if (Math.abs(video.currentTime - seconds) < 0.01 && video.readyState >= 2) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error(`视频抽帧超时（${formatDuration(seconds * 1000)}）`));
    }, 10000);
    const complete = () => {
      window.clearTimeout(timeout);
      cleanup();
      resolve();
    };
    const failed = () => {
      window.clearTimeout(timeout);
      cleanup();
      reject(new Error("视频跳转失败"));
    };
    const cleanup = () => {
      video.removeEventListener("seeked", complete);
      video.removeEventListener("error", failed);
    };
    video.addEventListener("seeked", complete, { once: true });
    video.addEventListener("error", failed, { once: true });
    video.currentTime = seconds;
  });
}

function extractVideoRecord(payload, session) {
  return firstDefined(
    payload?.video,
    payload?.registration,
    payload?.record,
    lastItem(payload?.videos),
    lastItem(session?.videos),
    lastItem((session?.raw_files || []).filter((item) => String(item.mime_type || "").startsWith("video/"))),
  );
}

function extractVideoAnalysis(payload, session) {
  return firstDefined(
    payload?.video_analysis,
    payload?.analysis,
    payload?.result,
    lastItem(payload?.video_analyses),
    lastItem(session?.video_analyses),
  );
}

function setVideoProgress(label, completed, total) {
  const safeTotal = Math.max(0, Number(total) || 0);
  const safeCompleted = Math.min(safeTotal || 1, Math.max(0, Number(completed) || 0));
  $("video-stage").textContent = label;
  $("video-progress-text").textContent = safeTotal ? `${safeCompleted} / ${safeTotal}` : "0 / 0";
  $("video-progress-bar").style.width = `${safeTotal ? (safeCompleted / safeTotal) * 100 : 0}%`;
}

function extractFrameResults(analysis) {
  const values = [
    analysis?.frame_results,
    analysis?.frames,
    getDeep(analysis, "quality.frames"),
    getDeep(analysis, "analysis.frames"),
  ];
  return values.find(Array.isArray) || [];
}

function renderVideoFrames(frames, analysis = null) {
  const container = $("video-frame-strip");
  container.replaceChildren();
  if (!Array.isArray(frames) || !frames.length) {
    const empty = document.createElement("div");
    empty.className = "frame-strip__empty";
    empty.textContent = "分析后显示采样时点、质量状态与代表帧";
    container.append(empty);
    return;
  }
  const results = extractFrameResults(analysis);
  const representativeIds = new Set(firstDefined(analysis?.representative_frame_ids, analysis?.selected_frame_ids, []));
  const representativeTimes = new Set((firstDefined(analysis?.representative_timestamps_ms, []) || []).map(Number));
  frames.forEach((frame, index) => {
    const result = results[index] || results.find((item) => Number(item.timestamp_ms) === Number(frame.timestamp_ms)) || {};
    const quality = firstDefined(result.quality_gate, result.quality, result.analysis?.quality_gate, result.analysis?.quality, {});
    const duplicate = Boolean(
      result.duplicate || result.is_duplicate || result.duplicate_of || normalizeStatus(result.admission_status) === "duplicate_suppressed" || normalizeStatus(result.status) === "duplicate",
    );
    const representative = Boolean(
      result.representative || result.selected || representativeIds.has(result.id) || representativeIds.has(result.frame_id) || representativeTimes.has(Number(frame.timestamp_ms)),
    );
    const passed = firstDefined(quality.passed, result.quality_passed, null);
    const card = document.createElement("button");
    card.type = "button";
    card.className = `frame-card${representative ? " is-representative" : ""}${duplicate ? " is-duplicate" : ""}`;
    card.title = `跳转到 ${formatDuration(frame.timestamp_ms)}`;
    card.addEventListener("click", () => {
      const video = $("artifact-video");
      video.currentTime = frame.timestamp_ms / 1000;
      video.play().catch(() => {});
    });
    const image = document.createElement("img");
    image.src = frame.data_url;
    image.alt = `视频抽帧 ${index + 1}，时间 ${formatDuration(frame.timestamp_ms)}`;
    const meta = document.createElement("span");
    meta.className = "frame-card__meta";
    const time = document.createElement("strong");
    time.textContent = formatDuration(frame.timestamp_ms);
    const status = document.createElement("small");
    status.textContent = representative ? "代表帧" : duplicate ? "近重复" : passed === true ? "质量通过" : passed === false ? "质量拒绝" : `FRAME ${String(index + 1).padStart(2, "0")}`;
    meta.append(time, status);
    card.append(image, meta);
    container.append(card);
  });
}

function renderMediaAnalysis(analysis, mode = state.mediaMode) {
  syncBodyStateClasses();
  const isVideo = mode === "video";
  const badge = $("media-result-state");
  if (!analysis) {
    badge.textContent = "WAITING";
    badge.className = "badge badge--neutral";
    $("media-input-type").textContent = isVideo ? "VIDEO" : "IMAGE";
    $("media-quality-summary").textContent = "—";
    $("media-coverage").textContent = "—";
    $("media-stability").textContent = "—";
    $("media-representative").textContent = "—";
    renderStringList("media-observations", ["等待服务端返回可见性观察"]);
    renderStringList("media-regions", ["候选区域只用于引导复核与下一步采集"]);
    renderStringList("media-evidence-refs", ["完成分析后显示原始输入、派生观察与模型运行引用"]);
    renderMediaNextObservation(null);
    return;
  }

  const frameResults = isVideo ? extractFrameResults(analysis) : [];
  const quality = firstDefined(analysis.quality_summary, analysis.quality_gate, analysis.quality, {});
  const accepted = Number(firstDefined(
    quality.accepted_count,
    quality.passed_count,
    analysis.accepted_frame_count,
    analysis.summary?.usable_frame_count,
    frameResults.filter((frame) => firstDefined(frame.quality_gate?.passed, frame.quality?.passed, frame.analysis?.quality_gate?.passed, frame.quality_passed) === true && !frame.duplicate_of).length,
  ));
  const total = Number(firstDefined(quality.total_count, analysis.frame_count, analysis.summary?.requested_frame_count, frameResults.length, isVideo ? state.sampledFrames.length : 1));
  const imagePassed = firstDefined(quality.passed, analysis.quality?.passed, null);
  const overallPassed = isVideo
    ? Boolean(firstDefined(quality.passed, accepted > 0 && accepted >= Math.max(1, Math.ceil(total * 0.5))))
    : imagePassed === true;
  badge.textContent = overallPassed ? "OBSERVABLE" : "REVIEW INPUT";
  badge.className = `badge badge--${overallPassed ? "success" : "warning"}`;
  $("media-input-type").textContent = isVideo ? "VIDEO · MULTI-FRAME" : `${analysis.modality || $("image-modality").value} · STILL`;
  $("media-quality-summary").textContent = isVideo
    ? `${Number.isFinite(accepted) ? accepted : "—"} / ${Number.isFinite(total) ? total : "—"} 通过`
    : imagePassed === true ? "PASS" : imagePassed === false ? "REJECTED" : "RECORDED";

  const coverage = firstDefined(analysis.coverage_score, analysis.view_coverage, getDeep(analysis, "coverage.score"), getDeep(analysis, "summary.coverage"), getDeep(analysis, "summary.temporal_span_ratio"));
  $("media-coverage").textContent = isVideo ? formatCoverage(coverage, total) : "SINGLE VIEW";
  const stability = firstDefined(analysis.capture_consistency_score, analysis.stability_score, getDeep(analysis, "summary.capture_consistency_score"));
  $("media-stability").textContent = isVideo ? formatCoverage(stability, null) : "STATIC";
  const representative = firstDefined(
    analysis.representative_frame_count,
    analysis.summary?.representative_frame_count,
    analysis.representative_frames?.length,
    analysis.representative_frame_ids?.length,
    frameResults.filter((frame) => frame.representative || frame.selected).length,
  );
  $("media-representative").textContent = isVideo ? `${Number(representative) || 0} FRAMES` : "1 VIEW";

  const observations = extractVisibleObservations(analysis);
  renderStringList("media-observations", observations.length ? observations : ["服务端未返回可见性观察；系统不从缺失结果推断事实"]);
  const regions = extractCandidateRegions(analysis);
  renderStringList("media-regions", regions.length ? regions : ["当前没有达到显示门槛的候选区域"]);
  const refs = extractMediaEvidenceRefs(analysis, isVideo ? state.videoRecord : null);
  renderStringList("media-evidence-refs", refs.length ? refs : ["证据引用待服务端返回"]);
  renderMediaNextObservation(firstDefined(analysis.next_best_observation, analysis.recommended_next_observation, analysis.next_observation, analysis.next_best_observations?.[0], state.session?.next_best_observations?.[0]));
}

function extractVisibleObservations(analysis) {
  const output = firstDefined(analysis.model_observation?.output, analysis.model_output, analysis.visual_observation, {});
  const candidates = [
    analysis.visible_observations,
    analysis.observations,
    analysis.fused_observations,
    output.visible_observations,
    output.observations,
    output.visible_features,
    output.features,
  ];
  const value = candidates.find(Array.isArray) || [];
  const text = [];
  for (const item of value) {
    if (typeof item === "string" && item.trim()) text.push(item.trim());
    else if (item && typeof item === "object") {
      const label = firstDefined(item.observation, item.text, item.label, item.description, item.finding);
      if (label) text.push(`${label}${item.confidence !== undefined ? ` · 置信 ${formatPercent(item.confidence)}` : ""}`);
    }
  }
  const summary = firstDefined(output.summary, output.description, analysis.visible_summary, analysis.summary?.visible_observation);
  if (!text.length && typeof summary === "string") text.push(summary);
  return unique(text).slice(0, 6);
}

function extractCandidateRegions(analysis) {
  let candidates = firstDefined(analysis.candidate_regions, analysis.salient_regions, analysis.regions, []);
  if ((!Array.isArray(candidates) || !candidates.length) && Array.isArray(analysis.frames)) {
    candidates = analysis.frames
      .filter((frame) => frame.selected || frame.representative)
      .flatMap((frame) => (frame.analysis?.salient_regions || []).map((region) => ({
        ...region,
        label: `${formatDuration(frame.timestamp_ms)} · ${region.id || "ROI"}`,
      })));
  }
  if (!Array.isArray(candidates)) return [];
  return candidates.slice(0, 6).map((region, index) => {
    if (typeof region === "string") return region;
    const label = firstDefined(region.label, region.description, region.id, region.region_id, `ROI-${index + 1}`);
    const score = Number(firstDefined(region.score, region.saliency, region.confidence));
    return Number.isFinite(score) ? `${label} · 候选强度 ${formatPercent(score)}` : String(label);
  });
}

function extractMediaEvidenceRefs(analysis, rawRecord = null) {
  const refs = [];
  const candidates = [
    analysis.evidence_refs,
    analysis.input_refs,
    analysis.output_refs,
    analysis.derived_evidence_ids,
    analysis.frame_refs,
  ];
  for (const values of candidates) {
    if (!Array.isArray(values)) continue;
    for (const value of values) {
      const ref = typeof value === "string" ? value : firstDefined(value.id, value.ref, value.evidence_id, value.file_id);
      if (ref) refs.push(String(ref));
    }
  }
  for (const value of [analysis.file_id, analysis.video_id, analysis.id, rawRecord?.id, analysis.model_run?.run_id, analysis.model_run_id]) {
    if (value) refs.push(String(value));
  }
  for (const frame of (analysis.frames || []).filter((item) => item.selected).slice(0, 3)) {
    for (const value of [frame.id, frame.file_id, frame.analysis_id]) if (value) refs.push(String(value));
  }
  return unique(refs).slice(0, 7);
}

function renderMediaNextObservation(recommendation) {
  const badge = $("media-next-state");
  if (!recommendation) {
    $("media-next-observation").textContent = state.session?.next_step || "等待服务端形成下一步建议";
    $("media-next-reason").textContent = "建议将信息增益、对象风险、成本与设备可用性共同纳入规划。";
    badge.textContent = "NOT PLANNED";
    badge.className = "badge badge--outline";
    return;
  }
  if (typeof recommendation === "string") {
    $("media-next-observation").textContent = recommendation;
    $("media-next-reason").textContent = "该建议来自当前会话的服务端分析，不代表仪器结果。";
  } else {
    $("media-next-observation").textContent = firstDefined(recommendation.label, recommendation.action, recommendation.modality, recommendation.title, "下一项观察已规划");
    $("media-next-reason").textContent = firstDefined(recommendation.reason, recommendation.rationale, recommendation.explanation, "由当前质量、覆盖度与风险约束共同生成。 ");
  }
  badge.textContent = "SUGGESTED";
  badge.className = "badge badge--info";
}

function renderStringList(id, values) {
  const list = $(id);
  list.replaceChildren();
  for (const value of values) {
    const item = document.createElement("li");
    item.textContent = String(value);
    list.append(item);
  }
}

function formatCoverage(value, total) {
  const number = Number(value);
  if (Number.isFinite(number)) return number <= 1 ? formatPercent(number) : `${number.toFixed(0)}%`;
  return Number.isFinite(Number(total)) && Number(total) > 0 ? `${Number(total)} SAMPLED` : "—";
}

function formatDuration(milliseconds) {
  const totalSeconds = Math.max(0, Number(milliseconds) || 0) / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(1).padStart(4, "0")}`;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取本地图像"));
    reader.onload = () => {
      const value = String(reader.result || "");
      const comma = value.indexOf(",");
      resolve(comma >= 0 ? value.slice(comma + 1) : value);
    };
    reader.readAsDataURL(file);
  });
}

function extractImageAnalysis(payload, session) {
  const candidates = [
    payload?.analysis,
    payload?.image_analysis,
    payload?.result,
    payload?.data,
    session?.last_image_analysis,
    lastItem(session?.image_analyses),
    lastItem(session?.images),
    lastItem(session?.raw_files),
  ];
  const candidate = candidates.find((item) => item && typeof item === "object");
  if (!candidate) return null;
  if (candidate.analysis && typeof candidate.analysis === "object") {
    return { ...candidate.analysis, raw_file: candidate.raw_file || candidate.file, model_run: candidate.model_run };
  }
  return candidate;
}

function renderImageAnalysis(analysis) {
  const roiLayer = $("roi-layer");
  roiLayer.replaceChildren();
  if (!analysis) {
    $("image-gate").textContent = "NO DATA";
    $("image-gate").className = "badge badge--neutral";
    $("quality-metrics").className = "quality-metrics empty-copy";
    $("quality-metrics").textContent = "上传后显示服务端质量指标";
    $("fingerprint-algorithm").textContent = "—";
    $("fingerprint-id").textContent = "—";
    $("raw-sha").textContent = "—";
    return;
  }

  const quality = firstDefined(
    analysis.quality,
    analysis.quality_gate,
    getDeep(analysis, "analysis.quality"),
    getDeep(analysis, "analysis.quality_gate"),
    {},
  );
  const metrics = firstDefined(analysis.metrics, getDeep(analysis, "analysis.metrics"), quality.metrics, {});
  const passed = Boolean(quality.passed);
  $("image-gate").textContent = passed ? "PASS" : "REJECTED";
  $("image-gate").className = `badge badge--${passed ? "success" : "danger"}`;

  const metricsContainer = $("quality-metrics");
  metricsContainer.className = "quality-metrics";
  metricsContainer.replaceChildren();
  const checks = quality.checks && typeof quality.checks === "object" ? quality.checks : {};
  const metricEntries = Object.entries(metrics).slice(0, 8);
  if (!metricEntries.length && Object.keys(checks).length) {
    for (const [name, value] of Object.entries(checks)) metricEntries.push([name, value ? "PASS" : "FAIL"]);
  }
  for (const [name, value] of metricEntries) {
    const item = document.createElement("div");
    const checkValue = Object.prototype.hasOwnProperty.call(checks, name) ? Boolean(checks[name]) : null;
    item.className = `quality-item${checkValue === true ? " is-pass" : checkValue === false ? " is-fail" : ""}`;
    const label = document.createElement("span");
    label.textContent = humanizeKey(name);
    label.title = String(name);
    const strong = document.createElement("strong");
    strong.textContent = formatMetricValue(value);
    item.append(label, strong);
    metricsContainer.append(item);
  }
  if (!metricEntries.length) metricsContainer.textContent = "服务端未返回明细指标";

  const fingerprint = firstDefined(analysis.fingerprint, getDeep(analysis, "analysis.fingerprint"), {});
  $("fingerprint-algorithm").textContent = fingerprint.algorithm || fingerprint.version || "—";
  $("fingerprint-id").textContent = fingerprint.id || fingerprint.fingerprint_id || "—";
  $("raw-sha").textContent = firstDefined(
    analysis.sha256,
    analysis.raw_sha256,
    getDeep(analysis, "raw_file.sha256"),
    getDeep(analysis, "file.sha256"),
    lastItem(state.session?.raw_files)?.sha256,
    "—",
  );

  const regions = firstDefined(analysis.salient_regions, getDeep(analysis, "analysis.salient_regions"), []);
  for (const region of Array.isArray(regions) ? regions.slice(0, 4) : []) {
    const box = region.bbox_normalized || region.bbox || [];
    if (!Array.isArray(box) || box.length !== 4) continue;
    const [x0, y0, x1, y1] = box.map(Number);
    if (![x0, y0, x1, y1].every(Number.isFinite)) continue;
    const element = document.createElement("div");
    element.className = "roi-box";
    element.style.left = `${clampNumber(x0, 0, 1) * 100}%`;
    element.style.top = `${clampNumber(y0, 0, 1) * 100}%`;
    element.style.width = `${Math.max(1, clampNumber(x1 - x0, 0, 1) * 100)}%`;
    element.style.height = `${Math.max(1, clampNumber(y1 - y0, 0, 1) * 100)}%`;
    const label = document.createElement("span");
    label.textContent = region.id || "ROI";
    element.append(label);
    roiLayer.append(element);
  }
  $("image-caption").textContent = `${state.imageFile?.name || "器物图像"} · 服务端门禁 ${passed ? "通过" : "失败"}`;
}

function comparableImagePair() {
  const analyses = Array.isArray(state.session?.image_analyses) ? state.session.image_analyses : [];
  if (analyses.length < 2) return null;
  const comparison = analyses[analyses.length - 1];
  const baseline = [...analyses].slice(0, -1).reverse().find((item) => (
    item.region_id === comparison.region_id && item.modality === comparison.modality
  ));
  return baseline && comparison ? { baseline, comparison } : null;
}

async function onCompareImages() {
  if (!state.sessionId) return;
  const pair = comparableImagePair();
  if (!pair) {
    toast("暂不可复拍对比", "请在同一会话登记两张同区域、同通道图像。", "warning");
    return;
  }
  const button = $("compare-images");
  setButtonLoading(button, true, "对比中");
  try {
    const payload = await request(API.imageCompare(state.sessionId), {
      method: "POST",
      body: {
        baseline_analysis_id: pair.baseline.id,
        comparison_analysis_id: pair.comparison.id,
      },
    });
    acceptEnvelope(payload);
    state.imageComparison = firstDefined(
      payload?.image_comparison,
      payload?.comparison,
      payload?.result,
      lastItem(payload?.image_comparisons),
      lastItem(state.session?.image_comparisons),
    );
    renderImageComparison(state.imageComparison);
    if (state.imageComparison?.next_best_observation) renderMediaNextObservation(state.imageComparison.next_best_observation);
    await Promise.allSettled([refreshEvidence({ quiet: true }), refreshAudit({ quiet: true })]);
    toast("复拍对比完成", "比较节点已进入证据图；结果保持采集条件与科学解释边界。", "success");
  } catch (error) {
    toast("复拍对比失败", error.message, "error");
  } finally {
    setButtonLoading(button, false);
    updateControlAvailability();
  }
}

function renderImageComparison(comparison) {
  const badge = $("comparison-status");
  if (!comparison) {
    badge.textContent = "WAITING";
    badge.className = "badge badge--neutral";
    $("comparison-title").textContent = comparableImagePair() ? "已具备同构复拍对比条件" : "至少登记两张同区域、同通道图像";
    $("comparison-reason").textContent = comparableImagePair() ? "点击按钮，由服务端比较最近两次可复核采集。" : "用于科学身份复检与可见变化候选提示。";
    for (const id of ["comparison-dhash", "comparison-feature", "comparison-brightness", "comparison-sharpness"]) $(id).textContent = "—";
    return;
  }
  const status = normalizeStatus(comparison.status);
  const copy = {
    stable_within_capture_tolerance: ["采集容差内稳定", "当前可见差异处于演示阈值内。", "success"],
    visible_change_candidate: ["发现可见变化候选", "建议控制机位、焦距、光照与色卡后复拍确认。", "warning"],
    not_comparable: ["两次采集暂不可比", "先排除质量、构图或视角差异，再比较器物变化。", "danger"],
  }[status] || [statusText(status), "服务端已返回复拍比较状态。", "neutral"];
  badge.textContent = status === "stable_within_capture_tolerance" ? "STABLE" : statusText(status);
  badge.className = `badge badge--${copy[2]}`;
  $("comparison-title").textContent = copy[0];
  $("comparison-reason").textContent = Array.isArray(comparison.reasons) && comparison.reasons.length ? comparison.reasons.join("；") : copy[1];
  const metrics = comparison.metrics || {};
  $("comparison-dhash").textContent = `${firstDefined(metrics.dhash_distance_bits, "—")} bit · ${formatPercent(metrics.dhash_distance_normalized)}`;
  $("comparison-feature").textContent = formatNumber(metrics.feature_distance, 4);
  $("comparison-brightness").textContent = formatSigned(metrics.brightness_delta);
  $("comparison-sharpness").textContent = formatSigned(metrics.sharpness_delta);
}

function formatSigned(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}

async function onKnowledgeSearch(event) {
  event.preventDefault();
  try {
    await searchKnowledge();
  } catch {
    // searchKnowledge 已在非静默模式向操作员显示可执行错误信息。
  }
}

async function searchKnowledge({ quiet = false } = {}) {
  if (!state.sessionId) throw new Error("请先创建会话");
  const button = $("search-knowledge");
  if (!state.demoRunning) setButtonLoading(button, true, "检索中");
  try {
    const payload = await request(API.knowledgeSearch(state.sessionId), {
      method: "POST",
      body: { query: $("knowledge-query").value.trim(), limit: 6, space: "demo" },
    });
    acceptEnvelope(payload);
    state.knowledgePayload = payload;
    renderKnowledge(payload);
    await Promise.allSettled([refreshEvidence({ quiet: true }), refreshAudit({ quiet: true })]);
    if (!quiet) toast("本地检索完成", "引用、版本和来源等级均来自后端。", "success");
    return payload;
  } catch (error) {
    if (!quiet) toast("本地检索失败", error.message, "error");
    throw error;
  } finally {
    if (!state.demoRunning) setButtonLoading(button, false);
    updateControlAvailability();
  }
}

function extractKnowledgeResults(payload) {
  const arrays = [
    payload?.results,
    payload?.matches,
    payload?.citations,
    getDeep(payload, "search.results"),
    getDeep(payload, "knowledge.results"),
    getDeep(payload, "result.results"),
    lastItem(state.session?.knowledge_searches)?.results,
    getDeep(state.session, "last_knowledge_search.results"),
  ];
  return arrays.find(Array.isArray) || [];
}

function renderKnowledge(payload) {
  const container = $("knowledge-results");
  container.replaceChildren();
  const results = payload ? extractKnowledgeResults(payload) : [];
  const version = firstDefined(
    payload?.knowledge_version,
    payload?.version,
    getDeep(payload, "search.knowledge_version"),
    getDeep(payload, "result.knowledge_version"),
    getDeep(state.session, "knowledge_version"),
  );
  if (version) {
    $("knowledge-version").textContent = String(version);
    $("knowledge-snapshot").textContent = String(version);
  }
  if (!results.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = "<span>⌁</span><p>暂无达到门槛的本地引用；系统保持知识不足状态</p>";
    container.append(empty);
    return;
  }

  for (const result of results.slice(0, 6)) {
    const card = document.createElement("article");
    card.className = "knowledge-card";
    const title = firstDefined(result.title, result.label, result.source_title, result.name, result.id, "未命名参考");
    const score = Number(firstDefined(result.score, result.similarity, result.relevance, result.distance));
    const source = firstDefined(result.institution, result.author, result.publisher, getDeep(result, "source.institution"), "演示知识源");
    const citationLocation = result.citation?.location;
    const location = firstDefined(
      result.source_location,
      result.location,
      result.locator,
      result.section,
      result.page,
      typeof citationLocation === "string" ? citationLocation : null,
      citationLocation && typeof citationLocation === "object"
        ? Object.entries(citationLocation).map(([key, value]) => `${humanizeKey(key)} ${value}`).join(" · ")
        : null,
      getDeep(result, "source.location"),
      "位置待返回",
    );
    const snippet = firstDefined(result.snippet, result.excerpt, result.note, result.match_basis, result.applicability, "仅作为可核验的本地检索候选。相似性不等同于真伪或年代结论。");
    const id = firstDefined(result.source_id, result.id, result.reference_id, "—");
    const demo = result.demo_reference !== false && !/expert|verified|formal/i.test(String(result.source_level || result.review_status || ""));
    card.innerHTML = `
      <div class="knowledge-card__head">
        <div><p class="eyebrow">${escapeHtml(String(source))}</p><h3>${escapeHtml(String(title))}</h3></div>
        <span class="knowledge-score">${Number.isFinite(score) ? score.toFixed(3) : "—"}</span>
      </div>
      <p>${escapeHtml(String(snippet))}</p>
      <div><span class="badge ${demo ? "badge--demo" : "badge--info"}">${demo ? "DEMO / SYNTHETIC" : escapeHtml(String(result.review_status || "LOCAL SOURCE"))}</span></div>
      <div class="citation-line">${escapeHtml(String(id))} · ${escapeHtml(String(location))}<br>${escapeHtml(String(version || result.knowledge_version || "VERSION —"))}</div>`;
    container.append(card);
  }
}

async function planNextAction({ quiet = false } = {}) {
  if (!state.sessionId) throw new Error("请先创建会话");
  const button = $("plan-action");
  if (!state.demoRunning) setButtonLoading(button, true, "规划中");
  try {
    const payload = await request(API.plan(state.sessionId), { method: "POST" });
    acceptEnvelope(payload);
    if (!quiet) toast("主动检测规划完成", selectedActionText(), "success");
    await refreshAudit({ quiet: true });
    return payload;
  } catch (error) {
    if (!quiet) toast("规划失败", error.message, "error");
    throw error;
  } finally {
    if (!state.demoRunning) setButtonLoading(button, false);
    updateControlAvailability();
  }
}

async function executeReservedAction(replayProfile = null, { quiet = false } = {}) {
  if (!state.sessionId) throw new Error("请先创建会话");
  if (!state.session?.current_action_id) throw new Error("当前没有已预留动作");
  const button = $("execute-action");
  if (!state.demoRunning) setButtonLoading(button, true, "执行中");
  try {
    const body = replayProfile ? { replay_profile: replayProfile } : {};
    const payload = await request(API.execute(state.sessionId), { method: "POST", body });
    acceptEnvelope(payload);
    if (!quiet) toast("动作已执行", executionOutcomeText(), state.session?.status === "quality_failed" ? "warning" : "success");
    await Promise.allSettled([refreshEvidence({ quiet: true }), refreshAudit({ quiet: true })]);
    return payload;
  } catch (error) {
    if (!quiet) toast("执行失败", error.message, "error");
    throw error;
  } finally {
    if (!state.demoRunning) setButtonLoading(button, false);
    updateControlAvailability();
  }
}

function extractActionEvaluations(session) {
  if (!session) return [];
  if (Array.isArray(session.last_plan)) return session.last_plan;
  if (Array.isArray(session.last_plan?.evaluations)) return session.last_plan.evaluations;
  const history = Array.isArray(session.plan_history) ? lastItem(session.plan_history) : null;
  return Array.isArray(history?.evaluations) ? history.evaluations : [];
}

function renderCandidateActions(actions) {
  const body = $("candidate-actions");
  body.replaceChildren();
  if (!Array.isArray(actions) || !actions.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="7" class="table-empty">等待后端规划结果</td>';
    body.append(row);
  } else {
    for (const action of actions) {
      const decision = normalizeStatus(action.decision || (action.id === state.session?.current_action_id ? "SELECTED" : ""));
      const row = document.createElement("tr");
      row.className = decision === "selected" ? "is-selected" : decision === "blocked" ? "is-blocked" : "";
      const reasons = Array.isArray(action.reasons) ? action.reasons.join("；") : action.reason || "满足当前评估条件";
      row.innerHTML = `
        <td><strong>${escapeHtml(String(action.label || action.id || "—"))}</strong><br><span class="micro-label">${escapeHtml(String(action.modality || ""))} · ${escapeHtml(String(action.region_id || ""))}</span></td>
        <td class="table-number">${formatNumber(action.information_gain, 4)}</td>
        <td class="table-number">${formatNumber(action.cost, 2)}</td>
        <td class="table-number">${formatPercent(action.risk_ratio)}</td>
        <td class="table-number">${formatNumber(action.utility, 4)}</td>
        <td><span class="badge badge--${badgeKind(decision)}">${escapeHtml(statusText(decision || "pending"))}</span></td>
        <td><span class="decision-note">${escapeHtml(String(reasons))}</span></td>`;
      body.append(row);
    }
  }

  const currentId = state.session?.current_action_id;
  const current = Array.isArray(actions) ? actions.find((action) => action.id === currentId) : null;
  const badge = $("current-action");
  if (currentId && state.session?.current_action_run_id) {
    badge.textContent = `RESERVED · ${current?.label || currentId}`;
    badge.className = "badge badge--info";
  } else if (Array.isArray(state.session?.executions) && state.session.executions.length) {
    badge.textContent = "SETTLED · 无预留";
    badge.className = "badge badge--success";
  } else {
    badge.textContent = "NO RESERVATION";
    badge.className = "badge badge--outline";
  }
}

function renderRiskBudgets(budgets) {
  const container = $("risk-budgets");
  container.replaceChildren();
  const rows = [];
  for (const [region, channels] of Object.entries(budgets || {})) {
    if (!channels || typeof channels !== "object") continue;
    for (const [channel, budget] of Object.entries(channels)) rows.push({ region, channel, ...budget });
  }
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty-copy";
    empty.textContent = "创建会话后载入区域风险预算";
    container.append(empty);
    return;
  }

  for (const row of rows) {
    const limit = Number(row.limit ?? row.limit_B ?? 0);
    const used = Number(row.used ?? row.used_E ?? 0);
    const reserved = Number(row.reserved ?? row.reserved_S ?? 0);
    const usedPct = limit > 0 ? clampNumber(used / limit, 0, 1) * 100 : 0;
    const reservedPct = limit > 0 ? clampNumber(reserved / limit, 0, 1) * 100 : 0;
    const item = document.createElement("div");
    item.className = `budget-item${usedPct >= 75 ? " is-critical" : ""}${row.locked ? " is-locked" : ""}`;
    item.innerHTML = `
      <div class="budget-item__head"><strong>${escapeHtml(row.region)} · ${escapeHtml(humanizeChannel(row.channel))}</strong><span class="badge badge--${row.locked ? "danger" : usedPct >= 75 ? "warning" : "info"}">${row.locked ? "LOCKED" : `${usedPct.toFixed(0)}% USED`}</span></div>
      <div class="budget-item__numbers"><span>实耗 ${formatNumber(used, 3)}</span><span>预留 ${formatNumber(reserved, 3)}</span><span>上限 ${formatNumber(limit, 3)} ${escapeHtml(String(row.unit || ""))}</span></div>
      <div class="meter" aria-label="${escapeHtml(row.region)} ${escapeHtml(row.channel)} 风险预算使用 ${usedPct.toFixed(1)}%，预留 ${reservedPct.toFixed(1)}%">
        <span class="meter__used" style="width:${usedPct}%"></span>
        <span class="meter__reserved" style="left:${usedPct}%;width:${Math.min(reservedPct, 100 - usedPct)}%"></span>
      </div>`;
    container.append(item);
  }
}

async function runP01Demo() {
  if (state.demoRunning) return;
  state.demoRunning = true;
  state.demoPhase = "running";
  document.body.classList.remove("is-demo-complete");
  document.body.classList.add("is-demo-running");
  setDemoUiBusy(true);
  resetDemoProgress();
  $("demo-progress-status").textContent = "RUNNING";
  try {
    setDemoStep("session", "active");
    if (!isFreshSession(state.session)) await createSession({ quiet: true });
    await searchKnowledge({ quiet: true });
    setDemoStep("session", "done");
    await pauseForDemo();

    setDemoStep("plan-1", "active");
    await planNextAction({ quiet: true });
    setDemoStep("plan-1", "done");
    await pauseForDemo();

    setDemoStep("raman", "active");
    await executeReservedAction("raman_low_snr", { quiet: true });
    if (normalizeStatus(state.session?.status) !== "quality_failed") {
      throw new Error("固定演示预期 Raman 低信噪质量失败，但服务端返回了不同状态");
    }
    setDemoStep("raman", "failed");
    await pauseForDemo();

    setDemoStep("plan-2", "active");
    await planNextAction({ quiet: true });
    setDemoStep("plan-2", "done");
    await pauseForDemo();

    setDemoStep("hsi", "active");
    await executeReservedAction("hsi_material_anomaly", { quiet: true });
    setDemoStep("hsi", "done");
    await pauseForDemo();

    setDemoStep("report", "active");
    await generateReport({ quiet: true });
    setDemoStep("report", "done");
    $("demo-progress-status").textContent = "COMPLETE";
    state.demoPhase = "complete";
    document.body.classList.add("is-demo-complete");
    renderShowcase();
    toast("P01 演示完成", "Raman 质量失败后实耗已扣账；HSI 重规划通过并生成可审计报告。", "success");
    window.setTimeout(() => $("evidence-story").scrollIntoView({ behavior: "auto", block: "start" }), 520);
  } catch (error) {
    $("demo-progress-status").textContent = "INTERRUPTED";
    state.demoPhase = "interrupted";
    const active = $("demo-progress").querySelector("li.is-active");
    if (active) active.classList.add("is-failed");
    toast("一键演示中断", `${error.message}；现有科学状态已保留，可检查审计链后继续。`, "error");
  } finally {
    state.demoRunning = false;
    document.body.classList.remove("is-demo-running");
    setDemoUiBusy(false);
    updateControlAvailability();
  }
}

function isFreshSession(session) {
  if (!session) return false;
  const executions = Array.isArray(session.executions) ? session.executions.length : 0;
  const uncertainty = Number(session.uncertainty ?? 0.85);
  return executions === 0 && !session.current_action_id && Math.abs(uncertainty - 0.85) < 0.0001;
}

function pauseForDemo() {
  return new Promise((resolve) => window.setTimeout(resolve, 620));
}

function resetDemoProgress() {
  for (const item of $$("#demo-progress li")) item.classList.remove("is-active", "is-done", "is-failed");
  $("demo-progress-status").textContent = "READY";
  state.demoPhase = "ready";
  document.body.classList.remove("is-demo-complete");
}

function setDemoStep(step, status) {
  const item = $(`#demo-progress li[data-step="${step}"]`);
  if (!item) return;
  item.classList.remove("is-active", "is-done", "is-failed");
  item.classList.add(`is-${status}`);
  if (status === "failed" && step === "raman") item.title = "预期质量失败：已发生物理暴露仍须扣账";
  updateShowcasePhase(step, status);
}

function updateShowcasePhase(step, status) {
  const labels = {
    session: "建立对象数字身份",
    "plan-1": "计算下一最佳观察",
    raman: status === "failed" ? "Raman 质量门禁拒绝" : "Raman 回放检测",
    "plan-2": "根据失败证据重新规划",
    hsi: "HSI 回放检测",
    report: status === "done" ? "科学身份包已生成" : "封装证据图与报告",
  };
  const message = labels[step];
  if (message) $("demo-progress-status").textContent = status === "active" ? message : status === "failed" ? "QUALITY REJECTED · REPLANNING" : "RUNNING";
  document.body.dataset.demoStep = step;
}

function setDemoUiBusy(busy) {
  const button = $("run-demo");
  setButtonLoading(button, busy, "正在执行真实 API 流程");
  for (const id of ["create-session", "analyze-image", "search-knowledge", "plan-action", "execute-action", "generate-report", "refresh-evidence", "refresh-audit"]) {
    $(id).disabled = busy || $(id).disabled;
  }
}

async function refreshEvidence({ quiet = false } = {}) {
  if (!state.sessionId) return null;
  const button = $("refresh-evidence");
  if (!quiet && !state.demoRunning) setButtonLoading(button, true, "刷新中");
  try {
    const payload = await request(API.evidence(state.sessionId));
    state.evidencePayload = payload;
    acceptEnvelope(payload);
    renderEvidenceGraph(payload);
    return payload;
  } catch (error) {
    if (!quiet) toast("证据图刷新失败", error.message, "error");
    throw error;
  } finally {
    if (!quiet && !state.demoRunning) setButtonLoading(button, false);
  }
}

function extractGraph(payload) {
  if (payload?.nodes && payload?.edges) return payload;
  return firstDefined(
    payload?.evidence_graph,
    payload?.graph,
    getDeep(payload, "session.evidence_graph"),
    state.session?.evidence_graph,
    null,
  );
}

function renderEvidenceGraph(payload) {
  const svg = $("evidence-graph");
  svg.replaceChildren();
  const graph = extractGraph(payload);
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes.slice(-24) : [];
  const compact = window.matchMedia?.("(max-width: 680px)")?.matches === true;
  evidenceGraphCompactMode = compact;
  const nodeIds = new Set(nodes.map((node) => String(node.id)));
  const edges = Array.isArray(graph?.edges)
    ? graph.edges.filter((edge) => nodeIds.has(String(edge.source)) && nodeIds.has(String(edge.target))).slice(-38)
    : [];

  if (!nodes.length) {
    const emptyWidth = compact ? 320 : 960;
    const emptyHeight = compact ? 260 : 440;
    svg.setAttribute("viewBox", `0 0 ${emptyWidth} ${emptyHeight}`);
    svg.style.height = compact ? `${emptyHeight}px` : "";
    const empty = svgElement("text", { x: emptyWidth / 2, y: emptyHeight / 2, "text-anchor": "middle", class: "graph-empty" });
    empty.textContent = "当前暂无证据节点；所有关系将由后端 API 返回";
    svg.append(empty);
    renderShowcase();
    return;
  }

  const defs = svgElement("defs");
  const marker = svgElement("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 5, markerHeight: 5, orient: "auto-start-reverse" });
  marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "rgba(139,162,184,.6)" }));
  defs.append(marker);
  svg.append(defs);

  const layout = layoutGraphNodes(nodes, { compact });
  const positions = layout.positions;
  svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
  svg.style.height = compact ? `${layout.height}px` : "";
  for (const edge of edges) {
    const source = positions.get(String(edge.source));
    const target = positions.get(String(edge.target));
    if (!source || !target) continue;
    const relation = String(edge.relation || edge.type || "linked");
    const relationStatus = normalizeStatus(edge.status || relation);
    const line = svgElement("line", {
      x1: source.x + source.width / 2,
      y1: source.y + source.height / 2,
      x2: target.x + target.width / 2,
      y2: target.y + target.height / 2,
      class: `graph-edge ${graphEdgeClass(relationStatus)}`,
      "marker-end": "url(#arrow)",
    });
    svg.append(line);
    const label = svgElement("text", {
      x: (source.x + target.x + source.width) / 2,
      y: (source.y + target.y + source.height) / 2 - 4,
      "text-anchor": "middle",
      class: "graph-edge-label",
    });
    label.textContent = truncate(relation, 18);
    svg.append(label);
  }

  for (const node of nodes) {
    const position = positions.get(String(node.id));
    const group = svgElement("g", {
      class: "graph-node",
      tabindex: "0",
      role: "listitem",
      "aria-label": `${node.type || "节点"}：${node.label || node.id}；状态 ${node.status || "neutral"}`,
      style: `--node-color:${node.color || GRAPH_COLORS[node.type] || "#8ba2b8"}`,
    });
    const title = svgElement("title");
    title.textContent = `${node.label || node.id}\n${node.type || "node"} · ${node.status || "neutral"}`;
    const rect = svgElement("rect", { x: position.x, y: position.y, width: position.width, height: position.height });
    const dot = svgElement("circle", { cx: position.x + 11, cy: position.y + 13, r: 3 });
    const type = svgElement("text", { x: position.x + 19, y: position.y + 16, class: "graph-node-type" });
    type.textContent = String(node.type || "NODE").toUpperCase();
    const label = svgElement("text", { x: position.x + 11, y: position.y + 38 });
    label.textContent = truncate(String(node.label || node.id), 17);
    group.append(title, rect, dot, type, label);
    svg.append(group);
  }
  renderShowcase();
}

function layoutGraphNodes(nodes, { compact = false } = {}) {
  const laneForType = { artifact: 0, claim: 1, region: 2, raw: 3, reference: 3, model_run: 4, action: 4, observation: 5, evidence: 6, report: 7 };
  if (compact) {
    const positions = new Map();
    const ordered = nodes.slice().sort((left, right) => {
      const laneDifference = (laneForType[left.type] ?? 4) - (laneForType[right.type] ?? 4);
      return laneDifference || String(left.id).localeCompare(String(right.id));
    });
    const width = 140;
    const height = 51;
    const columnGap = 16;
    const rowGap = 16;
    const canvasWidth = 320;
    ordered.forEach((node, index) => {
      const column = index % 2;
      const row = Math.floor(index / 2);
      positions.set(String(node.id), {
        x: 12 + column * (width + columnGap),
        y: 18 + row * (height + rowGap),
        width,
        height,
      });
    });
    const rows = Math.ceil(ordered.length / 2);
    const canvasHeight = Math.max(260, 36 + rows * height + Math.max(0, rows - 1) * rowGap);
    return { positions, width: canvasWidth, height: canvasHeight };
  }

  const groups = new Map();
  for (const node of nodes) {
    const lane = laneForType[node.type] ?? 4;
    if (!groups.has(lane)) groups.set(lane, []);
    groups.get(lane).push(node);
  }
  const positions = new Map();
  const width = 105;
  const height = 51;
  for (const [lane, items] of groups.entries()) {
    const x = 18 + lane * 117;
    const gap = Math.min(68, 360 / Math.max(1, items.length));
    const total = (items.length - 1) * gap + height;
    const startY = Math.max(22, (440 - total) / 2);
    items.forEach((node, index) => positions.set(String(node.id), { x, y: startY + index * gap, width, height }));
  }
  return { positions, width: 960, height: 440 };
}

function graphEdgeClass(status) {
  if (/conflict/.test(status)) return "is-conflict";
  if (/support/.test(status)) return "is-support";
  if (/uncertain|not_admitted|reject/.test(status)) return "is-uncertain";
  return "";
}

async function refreshAudit({ quiet = false } = {}) {
  if (!state.sessionId) return null;
  const button = $("refresh-audit");
  if (!quiet && !state.demoRunning) setButtonLoading(button, true, "验证中");
  try {
    const payload = await request(API.audit(state.sessionId));
    state.auditPayload = payload;
    acceptEnvelope(payload);
    renderAudit(payload);
    renderExecutionTimeline();
    return payload;
  } catch (error) {
    if (!quiet) toast("审计链验证失败", error.message, "error");
    throw error;
  } finally {
    if (!quiet && !state.demoRunning) setButtonLoading(button, false);
  }
}

function extractAuditEvents(payload) {
  if (Array.isArray(payload)) return payload;
  return firstDefined(payload?.events, payload?.audit_events, getDeep(payload, "audit.events"), []);
}

function renderAudit(payload) {
  const events = payload ? extractAuditEvents(payload) : [];
  const verified = firstDefined(
    payload?.audit_verified,
    payload?.verified,
    getDeep(payload, "verification.valid"),
    getDeep(payload, "audit.verified"),
    state.envelope?.audit_verified,
    null,
  );
  const verdict = $("audit-verdict");
  verdict.className = `audit-verdict${verified === true ? " is-verified" : verified === false ? " is-invalid" : ""}`;
  verdict.querySelector(".audit-verdict__icon").textContent = verified === true ? "◆" : verified === false ? "×" : "◇";
  verdict.querySelector("strong").textContent = verified === true ? "审计链完整" : verified === false ? "审计链验证失败" : "等待验证";
  verdict.querySelector("p").textContent = verified === true
    ? `${events.length} 个事件连续可验证；哈希链证明记录完整性，不证明输入事实真实。`
    : verified === false
      ? "事件载荷、顺序或前序哈希无法通过验证；当前报告不得作为完整记录使用。"
      : "哈希链只能验证记录完整性，不能证明输入事实真实或替代机构签章。";

  const container = $("audit-events");
  container.replaceChildren();
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty-copy";
    empty.textContent = "暂无审计事件";
    container.append(empty);
    renderShowcase();
    return;
  }
  for (const event of events.slice().reverse().slice(0, 20)) {
    const element = document.createElement("article");
    element.className = "audit-event";
    const hash = firstDefined(event.event_hash, event.hash, "—");
    element.innerHTML = `
      <div class="audit-event__head"><strong>#${escapeHtml(String(event.seq ?? "—"))} · ${escapeHtml(String(event.event_type || event.type || "EVENT"))}</strong><time>${escapeHtml(formatTime(event.created_at || event.timestamp))}</time></div>
      <code title="${escapeHtml(String(hash))}">${escapeHtml(String(hash))}</code>`;
    container.append(element);
  }
  renderShowcase();
}

function renderExecutionTimeline() {
  const events = extractAuditEvents(state.auditPayload || {});
  const container = $("execution-timeline");
  container.replaceChildren();
  let timeline = [];

  if (events.length) {
    timeline = events.map((event) => ({
      title: EVENT_TEXT[event.event_type] || humanizeKey(event.event_type || event.type || "EVENT"),
      detail: summarizeAuditPayload(event.payload),
      time: event.created_at || event.timestamp,
      hash: event.event_hash || event.hash,
      tone: eventTone(event),
    }));
  } else if (Array.isArray(state.session?.executions)) {
    timeline = state.session.executions.map((execution) => ({
      title: `${execution.action?.modality || execution.action?.label || "检测"} · ${execution.quality_gate?.passed ? "质量通过" : "质量失败"}`,
      detail: execution.result?.finding || "服务端执行记录",
      time: execution.created_at,
      hash: null,
      tone: execution.quality_gate?.passed ? "success" : "failure",
    }));
  }

  $("execution-count").textContent = `${timeline.length} EVENTS`;
  if (!timeline.length) {
    const empty = document.createElement("li");
    empty.className = "timeline__empty";
    empty.textContent = "尚无检测事件";
    container.append(empty);
    return;
  }

  for (const item of timeline.slice(-18).reverse()) {
    const li = document.createElement("li");
    li.className = `timeline__item is-${item.tone}`;
    li.innerHTML = `
      <span class="timeline__marker" aria-hidden="true"></span>
      <div class="timeline__content"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span><small>${escapeHtml(formatTime(item.time))}${item.hash ? ` · ${escapeHtml(shortHash(item.hash, 14))}` : ""}</small></div>`;
    container.append(li);
  }
}

function summarizeAuditPayload(payload) {
  if (!payload || typeof payload !== "object") return "状态变更已记录";
  if (payload.action_id) {
    const gate = payload.quality_gate?.passed;
    return `${payload.action_id}${gate === false ? " · 回放质量失败，实际暴露仍结算" : gate === true ? " · 回放质量通过" : " · 风险已预留"}`;
  }
  if (payload.filename) return `${payload.filename}${payload.sha256 ? ` · ${shortHash(payload.sha256, 12)}` : ""}`;
  if (payload.video_id) return `${payload.video_id}${payload.frame_count ? ` · ${payload.frame_count} 帧` : " · 多帧分析"}`;
  if (payload.query) return `本地查询：${truncate(payload.query, 30)}`;
  if (payload.artifact_name) return `器物：${payload.artifact_name}`;
  if (payload.report_id) return `报告：${shortHash(payload.report_id, 16)}`;
  return Object.keys(payload).slice(0, 3).map(humanizeKey).join(" · ") || "状态变更已记录";
}

function eventTone(event) {
  const type = String(event.event_type || event.type || "").toLowerCase();
  if (/fail|reject|invalid|error/.test(type) || event.payload?.quality_gate?.passed === false) return "failure";
  if (/report|complete|verified/.test(type) || event.payload?.quality_gate?.passed === true) return "success";
  return "info";
}

async function generateReport({ quiet = false } = {}) {
  if (!state.sessionId) throw new Error("请先创建会话");
  const button = $("generate-report");
  if (!state.demoRunning) setButtonLoading(button, true, "生成中");
  try {
    const payload = await request(API.report(state.sessionId), { method: "POST" });
    acceptEnvelope(payload);
    state.reportPayload = extractReport(payload, state.session);
    renderReport(state.reportPayload);
    enableReportDownloads();
    await Promise.allSettled([refreshEvidence({ quiet: true }), refreshAudit({ quiet: true })]);
    if (!quiet) toast("报告已生成", "报告保留 DEMO 边界、会话版本和完整性摘要。", "success");
    return payload;
  } catch (error) {
    if (!quiet) toast("报告生成失败", error.message, "error");
    throw error;
  } finally {
    if (!state.demoRunning) setButtonLoading(button, false);
    updateControlAvailability();
  }
}

function extractReport(payload, session) {
  return firstDefined(
    payload?.report,
    payload?.result,
    payload?.data,
    session?.last_report,
    session?.report,
    session?.latest_report,
    payload?.report_id ? payload : null,
  );
}

function renderReport(report) {
  const session = state.session;
  const status = normalizeStatus(firstDefined(
    report?.claim_consistency,
    report?.status,
    session?.claim_consistency,
    session?.status === "complete" ? "REVIEW_REQUIRED" : "EVIDENCE_INSUFFICIENT",
  ));
  $("report-status").textContent = statusText(status);
  $("report-status").style.color = status.includes("conflict") || status.includes("review") ? "var(--amber)" : status.includes("support") ? "var(--green)" : "var(--amber)";
  $("report-summary").textContent = firstDefined(
    typeof report?.summary === "string" ? report.summary : null,
    getDeep(report, "summary.text"),
    getDeep(report, "assistant_summary.summary"),
    report?.conclusion,
    session?.next_step ? `当前科学状态：${session.next_step}。系统不会输出真伪、确定年代、价格、文物定级或法律鉴定结论。` : null,
    "当前尚无足够证据。系统不会输出真伪、确定年代、价格、文物定级或法律鉴定结论。",
  );
  $("report-id").textContent = firstDefined(report?.report_id, report?.id, getDeep(report, "metadata.report_id"), "—");
  $("report-session-version").textContent = String(firstDefined(
    report?.covered_session_version,
    report?.session_version,
    getDeep(report, "metadata.session_version"),
    session?.version,
    "—",
  ));
  $("report-hash").textContent = firstDefined(
    report?.package_sha256,
    report?.report_hash,
    report?.sha256,
    getDeep(report, "integrity.report_sha256"),
    getDeep(report, "integrity.package_sha256"),
    getDeep(report, "integrity.hash"),
    "—",
  );

  const limitations = firstDefined(report?.limitations, getDeep(report, "summary.limitations"), []);
  const list = $("report-limitations");
  list.replaceChildren();
  const values = Array.isArray(limitations) && limitations.length
    ? limitations
    : [
        "演示检测值和内置参考资料均为合成或回放数据。",
        "相似性检索不能等同于身份、年代或真伪判断。",
        "结论需要真实仪器数据、专家复核和适用机构程序。",
      ];
  for (const value of values) {
    const li = document.createElement("li");
    li.textContent = typeof value === "string" ? value : value.text || JSON.stringify(value);
    list.append(li);
  }
  renderReportMediaStructure(report);
  renderShowcase();
}

function reportMediaCandidates() {
  const session = state.session || {};
  const rawFiles = Array.isArray(session.raw_files) ? session.raw_files : [];
  const videos = Array.isArray(session.videos) ? session.videos : [];
  const candidates = [];
  const seen = new Set();

  const add = (kind, analysis, fallbackOrder) => {
    if (!analysis || typeof analysis !== "object") return;
    const key = `${kind}:${analysis.id || analysis.analysis_id || analysis.file_id || fallbackOrder}`;
    if (seen.has(key)) return;
    seen.add(key);
    const rawFile = rawFiles.find((item) => item.id === analysis.file_id)
      || rawFiles.find((item) => analysis.video_id && item.video_id === analysis.video_id)
      || null;
    const videoRecord = kind === "video"
      ? videos.find((item) => item.id === analysis.video_id || item.file_id === analysis.file_id) || null
      : null;
    const rawOrder = rawFile ? rawFiles.indexOf(rawFile) : -1;
    const timestampText = firstDefined(
      analysis.created_at,
      analysis.completed_at,
      videoRecord?.registered_at,
      rawFile?.received_at,
    );
    const timestamp = timestampText ? Date.parse(timestampText) : Number.NaN;
    candidates.push({
      kind,
      analysis,
      rawRecord: videoRecord || rawFile,
      timestamp: Number.isFinite(timestamp) ? timestamp : 0,
      order: rawOrder >= 0 ? rawOrder : fallbackOrder,
    });
  };

  (session.image_analyses || []).forEach((analysis, index) => add("image", analysis, index));
  (session.video_analyses || []).forEach((analysis, index) => add("video", analysis, index));
  add("image", state.imageAnalysis, (session.image_analyses || []).length);
  add("video", state.videoAnalysis, (session.video_analyses || []).length);
  return candidates;
}

function latestMediaCandidate(candidates) {
  const ordered = [...candidates].sort((first, second) => (
    first.timestamp - second.timestamp || first.order - second.order
  ));
  return ordered.length ? ordered[ordered.length - 1] : null;
}

function selectReportMediaContext(report) {
  const candidates = reportMediaCandidates();
  const activeKind = state.mediaMode === "video" ? "video" : "image";
  const activeCandidates = candidates.filter((item) => item.kind === activeKind);
  const selected = latestMediaCandidate(activeCandidates.length ? activeCandidates : candidates);
  if (selected) return selected;

  const summaryValue = firstDefined(report?.media_summary, report?.media_analysis, report?.inputs?.media, null);
  const mediaSummary = Array.isArray(summaryValue) ? lastItem(summaryValue) : summaryValue;
  if (!mediaSummary || typeof mediaSummary !== "object" || !Object.keys(mediaSummary).length) return null;
  const kind = String(mediaSummary.media_type || "").toLowerCase() === "video" || mediaSummary.video_id
    ? "video"
    : "image";
  return {
    kind,
    analysis: mediaSummary,
    rawRecord: kind === "video" ? state.videoRecord : null,
    timestamp: 0,
    order: 0,
  };
}

function renderReportMediaStructure(report) {
  const context = selectReportMediaContext(report);
  const analysis = context?.analysis || null;
  const isVideo = context?.kind === "video";
  const rawRecord = context?.rawRecord || null;
  const hash = firstDefined(rawRecord?.sha256, rawRecord?.raw_sha256, analysis?.raw_sha256, analysis?.sha256);
  $("report-media-source").textContent = analysis
    ? `${isVideo ? "视频 / 多帧" : "图像 / 单视图"}${hash ? ` · SHA ${shortHash(hash, 12)}` : " · 原始引用已登记"}`
    : "等待图像或视频登记";

  const frameResults = isVideo ? extractFrameResults(analysis) : [];
  const quality = firstDefined(analysis?.quality_summary, analysis?.quality_gate, analysis?.quality, {});
  const accepted = Number(firstDefined(quality.accepted_count, quality.passed_count, analysis?.accepted_frame_count, analysis?.sampling_summary?.usable_frame_count, analysis?.summary?.usable_frame_count, frameResults.filter((frame) => firstDefined(frame.quality_gate?.passed, frame.quality?.passed, frame.analysis?.quality_gate?.passed) === true && !frame.duplicate_of).length));
  const total = Number(firstDefined(quality.total_count, analysis?.frame_count, analysis?.sampling_summary?.requested_frame_count, analysis?.summary?.requested_frame_count, frameResults.length));
  $("report-media-quality").textContent = analysis
    ? isVideo
      ? `${Number.isFinite(accepted) ? accepted : "—"}/${Number.isFinite(total) && total > 0 ? total : state.sampledFrames.length} 帧通过 · 近重复帧独立标记`
      : firstDefined(quality.passed, false) ? "单视图质量门控通过" : "单视图需补拍或专家复核"
    : "等待质量门控";

  const observations = analysis ? extractVisibleObservations(analysis) : [];
  $("report-media-observation").textContent = observations.length
    ? truncate(observations[0], 74)
    : "当前无可引用的可见性观察";

  const refs = analysis ? extractMediaEvidenceRefs(analysis, rawRecord) : [];
  $("report-media-evidence").textContent = refs.length
    ? `${refs.length} 项媒体引用 · 可回到原始输入与派生观察`
    : "等待证据节点";

  const next = firstDefined(analysis?.next_best_observation, analysis?.recommended_next_observation, analysis?.next_observation, analysis?.next_best_observations?.[0], state.session?.next_best_observations?.[0], state.session?.next_step);
  $("report-media-next").textContent = typeof next === "string"
    ? truncate(next, 76)
    : firstDefined(next?.label, next?.action, next?.title, "保持不确定性，等待规划");
}

function enableReportDownloads() {
  if (!state.sessionId) return;
  for (const [id, href] of [["download-json", API.reportJson(state.sessionId)], ["download-html", API.reportHtml(state.sessionId)]]) {
    const link = $(id);
    link.href = href;
    link.download = "";
    link.classList.remove("is-disabled");
    link.setAttribute("aria-disabled", "false");
  }
}

function selectedActionText() {
  const actions = extractActionEvaluations(state.session);
  const current = actions.find((item) => item.id === state.session?.current_action_id || normalizeStatus(item.decision) === "selected");
  return current ? `${current.label || current.id} 已通过硬约束并完成预算预留。` : state.session?.next_step || "规划状态已更新";
}

function executionOutcomeText() {
  const execution = lastItem(state.session?.executions);
  if (!execution) return state.session?.next_step || "执行状态已更新";
  return execution.quality_gate?.passed
    ? `${execution.action?.modality || "检测"} 通过质量门禁，命题状态已更新。`
    : `${execution.action?.modality || "检测"} 未通过质量门禁；实际暴露已结算，命题保持不变。`;
}

function updateControlAvailability() {
  const hasSession = Boolean(state.sessionId);
  $("analyze-image").disabled = state.demoRunning || !hasSession || !state.imageFile;
  $("compare-images").disabled = state.demoRunning || !hasSession || !comparableImagePair();
  $("analyze-video").disabled = state.demoRunning || state.videoBusy || !hasSession || !state.videoFile;
  $("search-knowledge").disabled = state.demoRunning || !hasSession || !$("knowledge-query").value.trim();
  $("plan-action").disabled = state.demoRunning || !hasSession || Boolean(state.session?.current_action_id) || normalizeStatus(state.session?.status) === "complete";
  $("execute-action").disabled = state.demoRunning || !hasSession || !state.session?.current_action_id;
  $("refresh-evidence").disabled = state.demoRunning || !hasSession;
  $("refresh-audit").disabled = state.demoRunning || !hasSession;
  $("generate-report").disabled = state.demoRunning || !hasSession;
  $("run-demo").disabled = state.demoRunning;
}

function setButtonLoading(button, loading, label = "处理中") {
  if (!button) return;
  if (loading) {
    if (!button.dataset.originalHtml) button.dataset.originalHtml = button.innerHTML;
    button.classList.add("is-loading");
    button.textContent = label;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    button.disabled = false;
  }
}

function toast(title, message, tone = "info") {
  const element = document.createElement("div");
  element.className = `toast${tone === "error" ? " is-error" : tone === "warning" ? " is-warning" : ""}`;
  const strong = document.createElement("strong");
  strong.textContent = title;
  const span = document.createElement("span");
  span.textContent = message;
  element.append(strong, span);
  $("toast-region").append(element);
  window.setTimeout(() => element.remove(), 5200);
}

function normalizeStatus(value) {
  if (value === true) return "online";
  if (value === false) return "offline";
  return String(value ?? "").trim().toLowerCase().replace(/[\s/]+/g, "_");
}

function statusText(value) {
  const normalized = normalizeStatus(value);
  return STATUS_TEXT[normalized] || normalized.replaceAll("_", " ").toUpperCase() || "UNKNOWN";
}

function statusClass(value) {
  const status = normalizeStatus(value);
  if (["online", "ready", "complete", "success"].includes(status)) return "online";
  if (["offline", "unhealthy", "failed", "error"].includes(status)) return "offline";
  return "degraded";
}

function badgeKind(value) {
  const status = normalizeStatus(value);
  if (/online|ready|complete|eligible|selected|pass|support|verified|success/.test(status)) return "success";
  if (/blocked|fail|reject|offline|unhealthy|invalid|conflict/.test(status)) return "danger";
  if (/warning|uncertain|insufficient|review|abstain|degraded|below|disabled/.test(status)) return "warning";
  if (/reserve|active|info|created/.test(status)) return "info";
  return "neutral";
}

function humanizeMode(value) {
  const text = String(value ?? "").replaceAll("_", " ").toUpperCase();
  if (/dual|two|双/.test(text)) return "DUAL SPARK · SERVICE COLLAB";
  if (/single|单/.test(text)) return "SINGLE SPARK · DEGRADED";
  return text;
}

function formatDataBoundary(value, health = null) {
  if (typeof value === "string" && value.trim()) return value.replaceAll("_", " ").toUpperCase();
  if (value && typeof value === "object") {
    const mode = String(value.mode || "").toUpperCase();
    const localOnly = /LOCAL|OFFLINE/.test(mode) || value.raw_artifact_data_egress === "BLOCKED_BY_DEFAULT";
    const offline = Boolean(health?.offline) || /OFFLINE/.test(mode);
    const privateOnly = value.private_endpoint_enforcement === true;
    const labels = [];
    if (localOnly) labels.push("LOCAL ONLY");
    if (offline) labels.push("OFFLINE");
    else if (privateOnly) labels.push("PRIVATE");
    return labels.join(" · ") || "LOCAL / PRIVATE";
  }
  return health?.offline ? "LOCAL ONLY · OFFLINE" : "LOCAL / PRIVATE";
}

function humanizeKey(value) {
  return String(value ?? "").replaceAll("_", " ").replaceAll("-", " ");
}

function humanizeChannel(value) {
  const labels = { photochemical: "光化学", ionizing: "电离辐射", thermal: "热", contact: "接触", sampling: "取样" };
  return labels[value] || humanizeKey(value);
}

function formatNumber(value, digits = 3) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "—";
}

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "—";
}

function formatMetricValue(value) {
  if (typeof value === "boolean") return value ? "PASS" : "FAIL";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return String(value ?? "—");
}

function formatBytes(bytes) {
  const number = Number(bytes);
  if (!Number.isFinite(number)) return "—";
  if (number < 1024) return `${number} B`;
  if (number < 1024 ** 2) return `${(number / 1024).toFixed(1)} KB`;
  return `${(number / 1024 ** 2).toFixed(1)} MB`;
}

function formatTime(value) {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

function getDeep(object, path) {
  return String(path).split(".").reduce((value, key) => (value && typeof value === "object" ? value[key] : undefined), object);
}

function lastItem(value) {
  return Array.isArray(value) && value.length ? value[value.length - 1] : null;
}

function unique(values) {
  return [...new Set(values)];
}

function clampNumber(value, min, max) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : min;
}

function shortHash(value, length = 12) {
  const text = String(value ?? "");
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function truncate(value, length = 40) {
  const text = String(value ?? "");
  return text.length > length ? `${text.slice(0, Math.max(1, length - 1))}…` : text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}
