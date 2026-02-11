/* global marked */

const $ = (id) => document.getElementById(id);

function loadSetting(key, fallback = "") {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function saveSetting(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore
  }
}

function apiBase() {
  const base = ($("apiBase").value || "").trim();
  if (!base) return "";
  // Common mistake: set base to http://host/web/ . API base should be http://host
  return base.replace(/\/?web\/?$/, "").replace(/\/$/, "");
}

function headers() {
  const h = { "Content-Type": "application/json" };
  const apiKey = ($("apiKey").value || "").trim();
  if (apiKey) h["X-API-Key"] = apiKey;
  return h;
}

async function apiGet(path) {
  const url = apiBase() + path;
  const r = await fetch(url, { headers: headers() });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`GET ${path} ${r.status}: ${txt}`);
  }
  return r.json();
}

async function apiPost(path, body) {
  const url = apiBase() + path;
  const r = await fetch(url, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  const txt = await r.text();
  if (!r.ok) throw new Error(`POST ${path} ${r.status}: ${txt}`);
  return JSON.parse(txt);
}

let allCourses = [];
let selected = null; // {repo_name, course_code, course_name, repo_type}

let pollTimer = null;
let currentTomlSource = "";

let locateCandidates = [];

// Markdown rendering (GitHub-ish)
try {
  const renderer = new marked.Renderer();
  // Prevent raw HTML in Markdown from breaking the preview DOM.
  // If content contains an unclosed tag/comment, the browser may swallow the rest of the page.
  renderer.html = (html) => escapeHtml(html);
  marked.setOptions({
    gfm: true,
    breaks: true,
    headerIds: true,
    mangle: false,
    renderer,
    highlight: (code, lang) => {
      try {
        if (window.hljs) {
          if (lang && window.hljs.getLanguage(lang)) {
            return window.hljs.highlight(code, { language: lang }).value;
          }
          return window.hljs.highlightAuto(code).value;
        }
      } catch {
        // ignore
      }
      return code;
    },
  });
} catch {
  // ignore
}

function filterCourses() {
  const q = ($("search").value || "").trim().toLowerCase();
  if (!q) return allCourses;
  return allCourses.filter((it) => {
    return (
      (it.repo_name || "").toLowerCase().includes(q) ||
      (it.course_code || "").toLowerCase().includes(q) ||
      (it.course_name || "").toLowerCase().includes(q)
    );
  });
}

function setTab(tab) {
  const isPreview = tab === "preview";
  $("panelPreview").classList.toggle("hidden", !isPreview);
  $("panelEdit").classList.toggle("hidden", isPreview);
  $("tabPreview").classList.toggle("bg-slate-900", isPreview);
  $("tabPreview").classList.toggle("text-white", isPreview);
  $("tabEdit").classList.toggle("bg-slate-900", !isPreview);
  $("tabEdit").classList.toggle("text-white", !isPreview);
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setToast({ type, title, message, linkUrl, linkText } = {}) {
  const box = $("toast");
  if (!box) return;

  const palette =
    type === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-900" :
    type === "warn" ? "border-amber-200 bg-amber-50 text-amber-900" :
    type === "error" ? "border-red-200 bg-red-50 text-red-900" :
    "border-slate-200 bg-white text-slate-900";

  box.className = `fixed bottom-4 right-4 z-50 max-w-[520px] border rounded shadow p-3 ${palette}`;
  box.innerHTML = `
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="font-semibold text-sm">${escapeHtml(title || "")}</div>
        ${message ? `<div class="text-sm mt-1 break-words">${escapeHtml(message)}</div>` : ""}
        ${linkUrl ? `<a class="text-sm underline mt-2 inline-block" target="_blank" rel="noreferrer" href="${escapeHtml(linkUrl)}">${escapeHtml(linkText || linkUrl)}</a>` : ""}
      </div>
      <button id="toastClose" class="px-2 py-1 text-xs border rounded bg-white/60">关闭</button>
    </div>
  `;
  box.classList.remove("hidden");
  $("toastClose")?.addEventListener("click", () => box.classList.add("hidden"));
  // auto hide after 8s for non-error
  if (type !== "error") {
    setTimeout(() => box.classList.add("hidden"), 8000);
  }
}

function setSubmitStatus(html, { kind = "info" } = {}) {
  const el = $("submitStatus");
  if (!el) return;
  const palette =
    kind === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-900" :
    kind === "warn" ? "border-amber-200 bg-amber-50 text-amber-900" :
    kind === "error" ? "border-red-200 bg-red-50 text-red-900" :
    "border-slate-200 bg-slate-50 text-slate-900";
  el.className = `mt-3 border rounded p-3 ${palette}`;
  el.innerHTML = html;
  el.classList.remove("hidden");
}

function setBusy(isBusy, label = "") {
  const submitBtn = $("submit");
  if (submitBtn) submitBtn.disabled = isBusy;
  if (submitBtn) submitBtn.textContent = isBusy ? (label || "提交中…") : "提交（确保 PR）";
}

function setLocateStatus(text) {
  const el = $("locateStatus");
  if (!el) return;
  el.textContent = text || "";
}

function resetLocatePick() {
  locateCandidates = [];
  const sel = $("locatePick");
  const applyBtn = $("applyPatchBtn");
  if (sel) {
    sel.innerHTML = '<option value="">（先定位）</option>';
    sel.disabled = true;
  }
  if (applyBtn) applyBtn.disabled = true;
}

function labelCandidate(c) {
  const t = c?.type || "";
  const pv = c?.preview || "";
  if (t === "description") return `[description] ${pv}`;
  if (t === "section_item") {
    const sec = c?.target?.section || "";
    const idx = Number(c?.target?.index ?? 0) + 1;
    return `[sections] ${sec} #${idx} ${pv}`;
  }
  if (t === "lecturer_review") {
    const lec = c?.target?.lecturer || "";
    const idx = Number(c?.target?.review_index ?? 0) + 1;
    return `[lecturers] ${lec} review#${idx} ${pv}`;
  }
  if (t === "course_section_item") {
    const cn = c?.target?.course_name || "";
    const sec = c?.target?.section || "";
    const idx = Number(c?.target?.index ?? 0) + 1;
    return `[courses.sections] ${cn} / ${sec} #${idx} ${pv}`;
  }
  if (t === "course_teacher_review") {
    const cn = c?.target?.course_name || "";
    const tn = c?.target?.teacher || "";
    const idx = Number(c?.target?.review_index ?? 0) + 1;
    return `[courses.teachers] ${cn} / ${tn} review#${idx} ${pv}`;
  }
  return `[${t}] ${pv}`;
}

async function locateParagraphCandidates() {
  const toml = ($("tomlText").value || "").trim();
  const snippet = ($("locateOld").value || "").trim();
  if (!toml) throw new Error("TOML 为空");
  if (!snippet || snippet.length < 5) throw new Error("原段落太短");

  setLocateStatus("定位中...");
  resetLocatePick();
  const data = await apiPost("/v1/toml/locate", { toml, snippet });
  const candidates = data?.candidates || [];
  if (!Array.isArray(candidates) || candidates.length === 0) {
    setLocateStatus("未找到匹配");
    return;
  }

  locateCandidates = candidates;
  const sel = $("locatePick");
  if (sel) {
    sel.disabled = false;
    sel.innerHTML = "";
    for (let i = 0; i < Math.min(20, candidates.length); i++) {
      const c = candidates[i];
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = labelCandidate(c);
      sel.appendChild(opt);
    }
  }
  const applyBtn = $("applyPatchBtn");
  if (applyBtn) applyBtn.disabled = false;
  setLocateStatus(`找到 ${candidates.length} 个候选（最多列出 20）`);
}

async function applyParagraphPatch() {
  const toml = ($("tomlText").value || "").trim();
  const old_paragraph = ($("locateOld").value || "").trim();
  const new_paragraph = ($("locateNew").value || "").trim();
  const sel = $("locatePick");
  if (!toml) throw new Error("TOML 为空");
  if (!old_paragraph) throw new Error("原段落不能为空");
  if (!new_paragraph) throw new Error("新段落不能为空");
  if (!sel || sel.value === "") throw new Error("请先选择候选");
  const idx = Number(sel.value);
  const c = locateCandidates[idx];
  if (!c || !c.target) throw new Error("候选无效，请重新定位");

  setLocateStatus("生成 patched TOML...");
  const data = await apiPost("/v1/toml/patch_paragraph", {
    toml,
    target: c.target,
    old_paragraph,
    new_paragraph,
  });
  if (!data?.toml) throw new Error("patch 未返回 TOML");
  $("tomlText").value = data.toml;
  currentTomlSource = "patched";
  setLocateStatus("已应用修改（TOML 已回填）");
  setToast({ type: "success", title: "已应用修改", message: "patched TOML 已回填到编辑框" });
}

function renderRepoList(items) {
  $("count").textContent = String(items.length);
  const list = $("repoList");
  list.innerHTML = "";

  if (!items.length) {
    list.innerHTML = '<div class="p-3 text-sm text-slate-600">没有匹配结果。可以点右上角“申请新建”。</div>';
    return;
  }

  for (const it of items) {
    const row = document.createElement("button");
    row.className =
      "w-full text-left px-3 py-2 hover:bg-slate-50 flex items-start justify-between gap-2";

    const left = document.createElement("div");
    left.innerHTML = `
      <div class="font-semibold mono">${escapeHtml(it.repo_name)}</div>
      <div class="text-xs text-slate-600">${escapeHtml(it.course_name || it.course_code || "")}</div>
    `;

    const right = document.createElement("div");
    right.className = "text-xs text-slate-500";
    right.textContent = it.repo_type || "";

    row.appendChild(left);
    row.appendChild(right);

    row.addEventListener("click", () => selectCourse(it));
    list.appendChild(row);
  }
}

async function refreshIndex({ force = false } = {}) {
  $("repoList").innerHTML =
    '<div class="p-3 text-sm text-slate-600">加载中…</div>';
  try {
    const items = await apiGet(`/v1/courses/index?refresh=${force ? "true" : "false"}`);
    allCourses = items;
    renderRepoList(filterCourses());
  } catch (e) {
    allCourses = [];
    $("count").textContent = "0";
    $("repoList").innerHTML = `
      <div class="p-3 text-sm text-slate-600">
        加载失败：<span class="mono">${escapeHtml(String(e.message || e))}</span><br/>
        提示：如果看到 429/403 rate limit，请在服务端配置 <span class="mono">GITHUB_TOKEN</span>。
      </div>
    `;
    // Do not throw; keep the page usable for manual actions.
  }
}

async function selectCourse(it) {
  selected = it;
  $("selectedTitle").textContent = `${it.repo_name} - ${it.course_name || it.course_code || ""}`;
  $("selectedMeta").textContent = `course_code=${it.course_code} repo_type=${it.repo_type}`;
  // default edit fields
  $("targetRepo").value = it.repo_name || "";
  $("courseCode").value = it.course_code || it.repo_name || "";
  $("courseName").value = it.course_name || it.course_code || it.repo_name || "";
  $("repoType").value = it.repo_type || "normal";
  await loadTomlFromRepoOrTemplate();
  await renderPreviewFromToml();
  setTab("preview");
}

function getEnsurePayload() {
  const repo_name = ($("targetRepo").value || "").trim();
  const course_code = ($("courseCode").value || "").trim();
  const course_name = ($("courseName").value || "").trim();
  const repo_type = ($("repoType").value || "normal").trim();
  const toml = ($("tomlText").value || "").trim();
  const payload = {
    repo_name: repo_name || null,
    course_code,
    course_name,
    repo_type,
  };
  if (toml) payload.toml = toml;
  return payload;
}

async function loadTomlFromRepoOrTemplate() {
  const repo_name = ($("targetRepo").value || "").trim() || (selected ? selected.repo_name : "");
  const course_code = ($("courseCode").value || "").trim() || (selected ? selected.course_code : "");
  const course_name = ($("courseName").value || "").trim() || (selected ? selected.course_name : "");
  const repo_type = ($("repoType").value || (selected ? selected.repo_type : "normal")).trim() || "normal";
  if (!repo_name && !course_code) throw new Error("需要 repo_name 或 course_code");

  const qs = new URLSearchParams();
  if (repo_name) qs.set("repo_name", repo_name);
  if (course_code) qs.set("course_code", course_code);
  if (course_name) qs.set("course_name", course_name);
  if (repo_type) qs.set("repo_type", repo_type);

  const data = await apiGet(`/v1/courses/lookup?${qs.toString()}`);
  $("tomlText").value = data.toml || "";
  resetLocatePick();
  setLocateStatus("");
  currentTomlSource = data.exists ? "repo_toml_or_template" : "template";
  setToast({ type: "success", title: "已加载 TOML", message: data.exists ? "来自仓库/模板" : "仓库不存在，使用模板" });
}

async function loadTomlFromFinalDir() {
  const course_code = ($("courseCode").value || "").trim();
  if (!course_code) throw new Error("course_code 不能为空");
  const data = await apiGet(`/v1/final/toml?course_code=${encodeURIComponent(course_code)}`);
  $("tomlText").value = data.toml || "";
  resetLocatePick();
  setLocateStatus("");
  currentTomlSource = data.source || "final_dir";
  setToast({ type: "success", title: "已加载 final TOML", message: `source=${currentTomlSource}` });
}

async function renderPreviewFromToml() {
  const toml = ($("tomlText").value || "").trim();
  if (!toml) {
    $("readme").innerHTML = '<div class="text-sm text-slate-600">TOML 为空，无法渲染。</div>';
    $("readmeSource").textContent = "";
    return;
  }
  $("readme").innerHTML = '<div class="text-sm text-slate-600">渲染中…</div>';
  const data = await apiPost("/v1/readme/render", { toml });
  $("readmeSource").textContent = currentTomlSource || "toml";
  $("readme").innerHTML = marked.parse(data.readme_md || "");
  try {
    if (window.hljs) window.hljs.highlightAll();
  } catch {
    // ignore
  }
}

async function submitEnsure() {
  const payload = getEnsurePayload();
  if (!payload.course_code) throw new Error("course_code 不能为空");
  setBusy(true);
  setSubmitStatus("提交中…", { kind: "info" });
  $("result").textContent = "";
  try {
    const data = await apiPost("/v1/pr/ensure", payload);
    $("result").textContent = JSON.stringify(data, null, 2);

    if (data.status === "waiting_repo") {
      setSubmitStatus(`仓库不存在，已进入 pending：request_id=${escapeHtml(String(data.request_id))}`, { kind: "warn" });
      startPolling(data.request_id);
      return;
    }

    if (data.pr_url) {
      setSubmitStatus(`完成：status=${escapeHtml(data.status)}。<a class="underline" target="_blank" rel="noreferrer" href="${escapeHtml(data.pr_url)}">打开 PR</a>`, { kind: "success" });
      setToast({ type: "success", title: "提交成功", message: data.status, linkUrl: data.pr_url, linkText: "打开 PR" });
    } else {
      setSubmitStatus(`完成：status=${escapeHtml(data.status)}`, { kind: "success" });
      setToast({ type: "success", title: "提交完成", message: data.status });
    }
  } catch (e) {
    setSubmitStatus(`失败：${escapeHtml(String(e.message || e))}`, { kind: "error" });
    setToast({ type: "error", title: "提交失败", message: String(e.message || e) });
  } finally {
    setBusy(false);
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling(requestId) {
  stopPolling();
  if (!requestId) return;
  pollTimer = setInterval(async () => {
    try {
      const r = await apiGet(`/v1/requests/${requestId}`);
      if (r.status === "pr_created" && r.pr_url) {
        stopPolling();
        setSubmitStatus(`pending 已完成：<a class="underline" target="_blank" rel="noreferrer" href="${escapeHtml(r.pr_url)}">打开 PR</a>`, { kind: "success" });
        setToast({ type: "success", title: "PR 已创建", linkUrl: r.pr_url, linkText: "打开 PR" });
      } else if (r.status === "failed") {
        stopPolling();
        setSubmitStatus(`pending 失败：${escapeHtml(String(r.last_error || ""))}`, { kind: "error" });
      }
    } catch {
      // ignore
    }
  }, 5000);
}

function wireEvents() {
  $("search")?.addEventListener("input", () => renderRepoList(filterCourses()));
  $("refresh")?.addEventListener("click", () => refreshIndex({ force: true }).catch((e) => setToast({ type: "error", title: "刷新失败", message: String(e.message || e) })));

  $("tabPreview")?.addEventListener("click", () => setTab("preview"));
  $("tabEdit")?.addEventListener("click", () => setTab("edit"));

  $("renderPreview")?.addEventListener("click", () => renderPreviewFromToml().catch((e) => setToast({ type: "error", title: "渲染失败", message: String(e.message || e) })));
  $("renderFromToml")?.addEventListener("click", () => renderPreviewFromToml().then(() => setTab("preview")).catch((e) => setToast({ type: "error", title: "渲染失败", message: String(e.message || e) })));

  $("loadFromRepo")?.addEventListener("click", () => loadTomlFromRepoOrTemplate().catch((e) => setToast({ type: "error", title: "加载失败", message: String(e.message || e) })));
  $("loadFromFinal")?.addEventListener("click", () => loadTomlFromFinalDir().catch((e) => setToast({ type: "error", title: "加载失败", message: String(e.message || e) })));

  $("submit")?.addEventListener("click", () => submitEnsure());

  $("locateBtn")?.addEventListener("click", () => locateParagraphCandidates().catch((e) => setToast({ type: "error", title: "定位失败", message: String(e.message || e) })));
  $("applyPatchBtn")?.addEventListener("click", () => applyParagraphPatch().catch((e) => setToast({ type: "error", title: "应用失败", message: String(e.message || e) })));
  $("tomlText")?.addEventListener("input", () => {
    // TOML changed; invalidate existing locate selection to avoid patching stale targets.
    resetLocatePick();
    setLocateStatus("");
  });

  $("toggleNew")?.addEventListener("click", () => $("newRepoPanel").classList.toggle("hidden"));
  $("cancelNew")?.addEventListener("click", () => $("newRepoPanel").classList.add("hidden"));
  $("startNew")?.addEventListener("click", () => {
    const course_code = ($("newCourseCode").value || "").trim();
    const course_name = ($("newCourseName").value || "").trim();
    const repo_type = ($("newRepoType").value || "normal").trim();
    const repo_name = ($("newRepoName").value || "").trim();
    if (!course_code) {
      setToast({ type: "error", title: "缺少 course_code" });
      return;
    }
    selected = { repo_name: repo_name || course_code, course_code, course_name, repo_type, __isNew: true };
    $("selectedTitle").textContent = `${selected.repo_name} - ${selected.course_name || selected.course_code}`;
    $("selectedMeta").textContent = `course_code=${selected.course_code} repo_type=${selected.repo_type}`;
    $("targetRepo").value = selected.repo_name;
    $("courseCode").value = selected.course_code;
    $("courseName").value = selected.course_name;
    $("repoType").value = selected.repo_type;
    $("tomlText").value = "";
    resetLocatePick();
    setLocateStatus("");
    currentTomlSource = "";
    setTab("edit");
    $("newRepoPanel").classList.add("hidden");
  });
}

function initSettings() {
  $("apiBase").value = loadSetting("apiBase", "");
  $("apiKey").value = loadSetting("apiKey", "");
  $("apiBase")?.addEventListener("change", () => saveSetting("apiBase", $("apiBase").value || ""));
  $("apiKey")?.addEventListener("change", () => saveSetting("apiKey", $("apiKey").value || ""));
}

async function main() {
  initSettings();
  wireEvents();
  await refreshIndex({ force: false });
}

// boot
main().catch((e) => {
  setToast({ type: "error", title: "初始化失败", message: String(e.message || e) });
});

// ---- legacy code below kept intentionally removed ----

