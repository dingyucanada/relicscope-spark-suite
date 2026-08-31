const overall = document.querySelector("#overall");

function text(id, value) {
  const node = document.querySelector(`#${id}`);
  if (node) node.textContent = value;
}

async function refresh() {
  try {
    const response = await fetch("/api/v2/scout/health", { cache: "no-store" });
    const payload = await response.json();
    const model = payload.model || {};
    const storage = payload.storage || {};
    const fullyReady = response.ok && payload.operational_status === "READY";
    overall.className = fullyReady ? "status ready" : "status degraded";
    overall.querySelector("span").textContent = fullyReady
      ? "本地分析服务就绪"
      : response.ok
        ? storage.ready === false
          ? "网关就绪 · 数据卷余量不足"
          : "网关就绪 · 模型待恢复"
        : "服务需要检查";
    text("gateway-state", String(payload.status || "UNKNOWN").toUpperCase());
    text("gateway-detail", `${payload.node_id || "unknown node"} · ${payload.runtime_mode || "unknown mode"}`);
    text("model-state", String(model.status || "UNKNOWN").toUpperCase());
    text("model-detail", model.model || model.detail || "模型未报告身份");
    text("worker-state", String(payload.queue_worker || "UNKNOWN").toUpperCase());
  } catch (error) {
    overall.className = "status failed";
    overall.querySelector("span").textContent = "无法连接本地服务";
    text("gateway-state", "OFFLINE");
    text("model-state", "UNKNOWN");
    text("worker-state", "UNKNOWN");
  }
}

refresh();
setInterval(refresh, 5000);
