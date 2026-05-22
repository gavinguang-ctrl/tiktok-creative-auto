let uploadedImagePaths = [];
let uploadedVideoPaths = [];
let currentTaskId = null;
let subcategoryData = {};

document.addEventListener("DOMContentLoaded", () => {
  renderHistory();
  loadSubcategories();

  document.getElementById("mainForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    await previewPrompt();
  });

  document.getElementById("category").addEventListener("change", () => {
    updateSubCategoryOptions();
  });

  document.getElementById("subCategory").addEventListener("change", () => {
    const sel = document.getElementById("subCategory");
    const hint = document.getElementById("subCategoryEnHint");
    const opt = sel.options[sel.selectedIndex];
    hint.textContent = opt && opt.getAttribute("data-en") ? opt.getAttribute("data-en") : "";
  });

  document.getElementById("imageFiles").addEventListener("change", (e) => {
    showFileList("imageList", e.target.files);
  });
  document.getElementById("videoFiles").addEventListener("change", (e) => {
    showFileList("videoList", e.target.files);
  });
});

async function loadSubcategories() {
  try {
    const resp = await fetch("/api/subcategories");
    subcategoryData = await resp.json();
    updateSubCategoryOptions();
  } catch (e) {
    subcategoryData = {};
  }
}

function updateSubCategoryOptions() {
  const category = document.getElementById("category").value;
  const group = document.getElementById("subCategoryGroup");
  const select = document.getElementById("subCategory");

  // Show sub-categories for selected category, or default ("") if none selected
  const subs = subcategoryData[category] || subcategoryData[""] || [];
  if (subs.length === 0) {
    group.style.display = "none";
    select.innerHTML = '<option value="">-- 所有趋势 --</option>';
    return;
  }

  group.style.display = "";
  select.innerHTML = '<option value="">-- 所有趋势 --</option>';
  for (const sub of subs) {
    const opt = document.createElement("option");
    if (typeof sub === "object" && sub.en) {
      opt.value = sub.en;
      opt.textContent = sub.zh;
      opt.setAttribute("data-en", sub.en);
    } else {
      opt.value = sub;
      opt.textContent = sub;
    }
    select.appendChild(opt);
  }
}

async function refreshSubcategories() {
  const hint = document.getElementById("subCategoryHint");
  hint.textContent = "正在抓取子分类...";
  try {
    const resp = await fetch("/api/scrape-subcategories", { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json();
      hint.textContent = "抓取失败: " + (err.error || resp.statusText);
      return;
    }
    subcategoryData = await resp.json();
    hint.textContent = "更新完成";
    updateSubCategoryOptions();
  } catch (e) {
    hint.textContent = "抓取失败: " + e.message;
  }
}

// --- History Management (localStorage) ---

function getHistory() {
  return JSON.parse(localStorage.getItem("taskHistory") || "[]");
}

function saveHistory(history) {
  localStorage.setItem("taskHistory", JSON.stringify(history));
}

function saveCurrentToHistory() {
  const task = {
    id: Date.now().toString(36),
    time: new Date().toLocaleString(),
    productName: document.getElementById("productName").value,
    productPrice: document.getElementById("productPrice").value,
    productDetails: document.getElementById("productDetails").value,
    sellingPoints: document.getElementById("sellingPoints").value,
    productLink: document.getElementById("productLink").value,
    country: document.getElementById("country").value,
    language: document.getElementById("language").value,
    subtitleEnabled: document.getElementById("subtitleEnabled").checked,
    category: document.getElementById("category").value,
    subCategory: document.getElementById("subCategory").value,
    videoCount: document.getElementById("videoCount").value,
    startTrendIndex: document.getElementById("startTrendIndex").value,
    imagePaths: uploadedImagePaths,
    videoPaths: uploadedVideoPaths,
  };
  const history = getHistory();
  history.unshift(task);
  if (history.length > 20) history.pop();
  saveHistory(history);
  renderHistory();
}

function loadFromHistory(id) {
  const history = getHistory();
  const task = history.find((t) => t.id === id);
  if (!task) return;
  document.getElementById("productName").value = task.productName || "";
  document.getElementById("productPrice").value = task.productPrice || "";
  document.getElementById("productDetails").value = task.productDetails || "";
  document.getElementById("sellingPoints").value = task.sellingPoints || "";
  document.getElementById("productLink").value = task.productLink || "";
  document.getElementById("country").value = task.country || "越南";
  document.getElementById("language").value = task.language || "越南语";
  document.getElementById("subtitleEnabled").checked = task.subtitleEnabled !== false;
  document.getElementById("category").value = task.category || "";
  updateSubCategoryOptions();
  document.getElementById("subCategory").value = task.subCategory || "";
  document.getElementById("videoCount").value = task.videoCount || "1";
  document.getElementById("startTrendIndex").value = task.startTrendIndex || "0";

  // Restore saved file paths
  uploadedImagePaths = task.imagePaths || [];
  uploadedVideoPaths = task.videoPaths || [];
  document.getElementById("imageList").textContent = uploadedImagePaths.map(p => p.split(/[\\\/]/).pop()).join(", ");
  document.getElementById("videoList").textContent = uploadedVideoPaths.map(p => p.split(/[\\\/]/).pop()).join(", ");
}

function deleteFromHistory(id) {
  const history = getHistory().filter((t) => t.id !== id);
  saveHistory(history);
  renderHistory();
}

function renderHistory() {
  const history = getHistory();
  const container = document.getElementById("historyList");
  if (history.length === 0) {
    container.innerHTML = '<p class="empty-hint">暂无历史任务</p>';
    return;
  }
  container.innerHTML = history
    .map(
      (t) => `
    <div class="history-item">
      <div class="history-info" onclick="loadFromHistory('${t.id}')">
        <strong>${t.productName || "未命名"}</strong>
        <span>${t.country} · ${t.category || "默认趋势"} · ${t.videoCount}个视频</span>
        <small>${t.time}</small>
      </div>
      <button class="btn-small btn-danger" onclick="deleteFromHistory('${t.id}')">删除</button>
    </div>`
    )
    .join("");
}

// --- Core Functions ---

function showFileList(containerId, files) {
  const el = document.getElementById(containerId);
  el.textContent = Array.from(files).map((f) => f.name).join(", ");
}

async function uploadFiles(inputId) {
  const input = document.getElementById(inputId);
  if (!input.files.length) return [];
  const form = new FormData();
  for (const f of input.files) {
    form.append("files", f);
  }
  const resp = await fetch("/api/upload", { method: "POST", body: form });
  const data = await resp.json();
  return data.paths || [];
}

async function previewPrompt() {
  const form = new FormData();
  form.append("product_name", document.getElementById("productName").value);
  form.append("product_price", document.getElementById("productPrice").value);
  form.append("product_details", document.getElementById("productDetails").value);
  form.append("selling_points", document.getElementById("sellingPoints").value);
  form.append("product_link", document.getElementById("productLink").value);
  form.append("country", document.getElementById("country").value);
  form.append("language", document.getElementById("language").value);
  form.append("subtitle_enabled", document.getElementById("subtitleEnabled").checked);

  const resp = await fetch("/api/preview-prompt", { method: "POST", body: form });
  const data = await resp.json();

  document.getElementById("generatedPrompt").value = data.prompt;
  document.getElementById("promptPreview").classList.remove("hidden");
  document.getElementById("mainForm").classList.add("hidden");
}

function hidePreview() {
  document.getElementById("promptPreview").classList.add("hidden");
  document.getElementById("mainForm").classList.remove("hidden");
}

async function confirmAndRun() {
  document.getElementById("promptPreview").classList.add("hidden");

  const statusPanel = document.getElementById("statusPanel");
  const statusMsg = document.getElementById("statusMessage");
  const progressFill = document.getElementById("progressFill");
  const resultsList = document.getElementById("resultsList");

  statusPanel.classList.remove("hidden");
  resultsList.innerHTML = "";
  statusMsg.textContent = "上传文件中...";
  progressFill.style.width = "0%";
  progressFill.style.background = "#0066cc";

  // Only upload if new files were selected (not loaded from history)
  const imageInput = document.getElementById("imageFiles");
  const videoInput = document.getElementById("videoFiles");
  try {
    if (imageInput.files.length > 0) {
      uploadedImagePaths = await uploadFiles("imageFiles");
    }
    if (videoInput.files.length > 0) {
      uploadedVideoPaths = await uploadFiles("videoFiles");
    }
  } catch (e) {
    console.error("Upload failed:", e);
  }

  // Save to history (with uploaded paths)
  saveCurrentToHistory();

  const form = new FormData();
  form.append("product_name", document.getElementById("productName").value);
  form.append("product_price", document.getElementById("productPrice").value);
  form.append("product_details", document.getElementById("productDetails").value);
  form.append("selling_points", document.getElementById("sellingPoints").value);
  form.append("product_link", document.getElementById("productLink").value);
  form.append("country", document.getElementById("country").value);
  form.append("language", document.getElementById("language").value);
  form.append("subtitle_enabled", document.getElementById("subtitleEnabled").checked);
  form.append("category", document.getElementById("category").value);
  form.append("sub_category", document.getElementById("subCategory").value);
  form.append("video_count", document.getElementById("videoCount").value);
  form.append("start_trend_index", document.getElementById("startTrendIndex").value);
  form.append("image_paths", uploadedImagePaths.join(","));
  form.append("video_paths", uploadedVideoPaths.join(","));
  form.append("custom_prompt", document.getElementById("generatedPrompt").value);

  statusMsg.textContent = "启动任务...";
  const resp = await fetch("/api/run", { method: "POST", body: form });
  const data = await resp.json();

  if (!resp.ok) {
    statusMsg.textContent = `错误: ${data.error}`;
    return;
  }

  currentTaskId = data.task_id;
  document.getElementById("stopTaskBtn").style.display = "";
  connectWebSocket(data.task_id);
}

function connectWebSocket(taskId) {
  const ws = new WebSocket(`ws://localhost:8000/ws/${taskId}`);
  const statusMsg = document.getElementById("statusMessage");
  const progressFill = document.getElementById("progressFill");
  const loopStatus = document.getElementById("loopStatus");
  const resultsList = document.getElementById("resultsList");

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    statusMsg.textContent = data.message || data.status;

    if (data.total_videos > 1) {
      loopStatus.textContent = `第 ${data.current_video}/${data.total_videos} 个视频`;
      const pct = Math.round(((data.current_video - 1) / data.total_videos) * 100);
      progressFill.style.width = pct + "%";
    }

    if (data.result_paths && data.result_paths.length > 0) {
      resultsList.innerHTML = data.result_paths
        .map((p, i) => `<a class="btn-download" href="/api/run/${taskId}/result/${i + 1}" download>下载视频 ${i + 1}</a>`)
        .join(" ");
    }

    if (data.status === "completed") {
      progressFill.style.width = "100%";
      loopStatus.textContent = `全部完成 (${data.result_paths.length} 个视频)`;
      document.getElementById("stopTaskBtn").style.display = "none";
      ws.close();
    } else if (data.status === "failed") {
      progressFill.style.width = "100%";
      progressFill.style.background = "#cc3333";
      document.getElementById("stopTaskBtn").style.display = "none";
      ws.close();
    }
  };

  ws.onerror = () => {
    statusMsg.textContent = "WebSocket 连接失败，尝试轮询...";
    pollStatus(taskId);
  };
}

async function pollStatus(taskId) {
  const statusMsg = document.getElementById("statusMessage");
  const progressFill = document.getElementById("progressFill");
  const loopStatus = document.getElementById("loopStatus");
  const resultsList = document.getElementById("resultsList");

  const interval = setInterval(async () => {
    const resp = await fetch(`/api/run/${taskId}/status`);
    const data = await resp.json();
    statusMsg.textContent = data.message || data.status;

    if (data.total_videos > 1) {
      loopStatus.textContent = `第 ${data.current_video}/${data.total_videos} 个视频`;
    }

    if (data.result_paths && data.result_paths.length > 0) {
      resultsList.innerHTML = data.result_paths
        .map((p, i) => `<a class="btn-download" href="/api/run/${taskId}/result/${i + 1}" download>下载视频 ${i + 1}</a>`)
        .join(" ");
    }

    if (data.status === "completed" || data.status === "failed") {
      clearInterval(interval);
      progressFill.style.width = "100%";
    }
  }, 3000);
}

async function stopTask() {
  if (!currentTaskId) return;
  try {
    await fetch(`/api/run/${currentTaskId}/stop`, { method: "POST" });
  } catch (e) {
    console.error("Stop request failed:", e);
  }
  // Reset UI back to form
  document.getElementById("statusPanel").classList.add("hidden");
  document.getElementById("mainForm").classList.remove("hidden");
  document.getElementById("stopTaskBtn").style.display = "";
  currentTaskId = null;
}
