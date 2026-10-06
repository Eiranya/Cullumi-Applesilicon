const recentProjectName = (project) =>
  project.root.split(/[\\/]/).pop() || project.root;
function recentProjectTime(value) {
  const opened = new Date(value || 0);
  if (Number.isNaN(opened.getTime())) return "最近使用";
  const now = new Date(),
    today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(
    opened.getFullYear(),
    opened.getMonth(),
    opened.getDate(),
  );
  const days = Math.max(0, Math.floor((today - day) / 86400000));
  if (days === 0)
    return `今天 ${String(opened.getHours()).padStart(2, "0")}:${String(opened.getMinutes()).padStart(2, "0")}`;
  if (days === 1) return "昨天";
  if (days < 7) return `${days} 天前`;
  if (days < 14) return "上周";
  return `${opened.getMonth() + 1} 月 ${opened.getDate()} 日`;
}
function renderRecentProjects() {
  const query = state.recentQuery.trim().toLocaleLowerCase();
  const projects = state.recentProjects.filter((project) =>
    recentProjectName(project).toLocaleLowerCase().includes(query),
  );
  $("#recentList").innerHTML = projects.length
    ? projects
        .map((project) => {
          const meta = project.stats_loaded
            ? `${project.total || 0} 张&nbsp; · &nbsp;已留 ${project.kept || 0}`
            : "正在读取项目信息…";
          const status = !project.available
            ? "目录当前不可用"
            : project.load_error
              ? "项目数据暂时无法读取"
              : recentProjectTime(project.last_opened);
          return `<button class="recent" data-pid="${project.id}" title="${esc(project.load_error || project.root)}"><span class="recent-thumb">${project.thumbnail_url ? `<img src="${esc(project.thumbnail_url)}" alt="">` : `<span class="recent-thumb-empty"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 6a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6Z"/></svg></span>`}</span><span class="recent-info"><b>${esc(recentProjectName(project))}</b><span class="recent-meta">${meta}</span><small>${esc(status)}</small></span><span class="recent-more" aria-label="项目操作" title="项目操作"><svg viewBox="0 0 1024 1024" aria-hidden="true"><use href="#home-more"></use></svg></span></button>`;
        })
        .join("")
    : `<div class="empty recent-empty"><b>${state.recentProjects.length ? "没有匹配的项目" : "暂无最近筛选"}</b><span>${state.recentProjects.length ? "请尝试其他项目名称" : "选择一个照片文件夹即可开始"}</span></div>`;
  $$(".recent").forEach((item) => {
    item.onclick = (event) =>
      event.target.closest(".recent-more")
        ? openRecentMenu(event, item.dataset.pid)
        : openProject(item.dataset.pid);
    item.oncontextmenu = (event) => openRecentMenu(event, item.dataset.pid);
  });
}

async function hydrateRecentProjects(generation) {
  const queue = state.recentProjects
    .filter((project) => project.available && !project.stats_loaded)
    .map((project) => project.id);
  const worker = async () => {
    while (queue.length && generation === state.recentGeneration) {
      const projectId = queue.shift();
      let payload;
      try {
        payload = await json(
          `/api/recent-project?project_id=${encodeURIComponent(projectId)}`,
        );
      } catch (error) {
        payload = {
          ...state.recentProjects.find((project) => project.id === projectId),
          stats_loaded: true,
          load_error: error.message,
        };
      }
      if (generation !== state.recentGeneration) return;
      const index = state.recentProjects.findIndex(
        (project) => project.id === projectId,
      );
      if (index >= 0) {
        state.recentProjects[index] = payload;
        renderRecentProjects();
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(3, queue.length) }, worker));
}

async function boot() {
  const generation = ++state.recentGeneration,
    b = await json("/api/bootstrap");
  if (generation !== state.recentGeneration) return;
  state.profiles = b.profiles;
  state.settings = b.settings;
  state.recentProjects = b.recent_projects;
  applyTheme(b.settings.theme || state.theme);
  $("#appVersion").textContent = `v${b.version}`;
  renderProfiles();
  $("#autoAdvance").checked = !!b.settings.auto_advance;
  $("#removeReviewOnAccept").checked = !!b.settings.remove_review_on_accept;
  $("#confirmAcceptSuggestions").checked =
    b.settings.confirm_accept_suggestions !== false;
  $("#fastAnalysis").checked = !!b.settings.fast_analysis;
  $("#blinkGpu").checked = b.settings.blink_gpu_enabled !== false;
  renderBlinkGpuStatus($("#blinkGpu").checked, b.settings.blink_gpu_active !== false);
  $("#syncVariantDecisions").checked =
    b.settings.sync_variant_decisions !== false;
  $("#autoCheckUpdates").checked = !!b.settings.auto_check_updates;
  $("#motionCoverWriteback").value = b.settings.motion_cover_writeback || "ask";
  $("#defaultCache").value = b.settings.default_cache_root;
  renderRecentProjects();
  hydrateRecentProjects(generation);
  if (b.startup_warning && !$("#startupWarning").dataset.shown) {
    $("#startupWarning").dataset.shown = "1";
    $("#startupWarningBody").textContent = b.startup_warning;
    $("#startupWarning").showModal();
  }
  if (b.settings.auto_check_updates && !state.updateChecked) {
    state.updateChecked = true;
    setTimeout(() => checkForUpdates(false), 450);
  }
}
function renderProfiles() {
  const opts = state.profiles
    .map(
      (p) =>
        `<option value="${p.id}">${esc(p.name)}${p.builtin ? " · 内置" : ""}</option>`,
    )
    .join("");
  $("#profileSelect").innerHTML = opts;
  $("#profileEditorSelect").innerHTML = opts;
  if (state.project) $("#profileSelect").value = state.project.profile_id;
}
async function chooseProject() {
  const r = await json("/api/choose-folder", {});
  if (!r.path) return;
  const p = await json("/api/project/open", { root: r.path });
  await showProject(p);
  await startScan();
}
async function openProject(pid) {
  try {
    await showProject(await json(`/api/project?project_id=${pid}`));
    await startRequiredAnalysis();
  } catch (e) {
    toast(e.message);
  }
}
async function showProject(p) {
  state.project = p;
  state.view = "library";
  state.activeNav = "library";
  state.filters = {
    decisions: new Set(DECISION_VALUES),
    ai: new Set(AI_VALUES),
    formats: new Set((p.format_categories || []).map((item) => item.id)),
  };
  state.librarySort = "suggestion";
  state.librarySortDirection = "asc";
  state.similar = {
    groups: [],
    selectedId: "",
    mode: "closed",
    listSearch: "",
    statusFilter: "all",
    memberSearch: "",
    detail: null,
    formatCategories: [],
    decisions: new Set(DECISION_VALUES),
    ai: new Set(AI_VALUES),
    formats: new Set(),
    sort: "suggestion",
    sortDirection: "desc",
    offset: 0,
    total: 0,
    done: false,
    loading: false,
    generation: 0,
  };
  document.body.classList.add("project-open");
  $("#home").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $("#projectName").textContent = p.root.split(/[\\/]/).pop();
  $("#projectPath").textContent = p.root;
  $("#projectCache").value = p.cache_root;
  $("#profileSelect").value = p.profile_id;
  $("#searchInput").value = "";
  renderFormatFilterOptions();
  syncFilterControls();
  syncSortControls();
  updateCounts(p);
  setActiveNav("library");
  await loadView();
}
function updateCounts(p) {
  const c = p.library_counts || {};
  $("#libraryCount").textContent = c.readable ?? p.total ?? 0;
  $("#aiCount").textContent = c.ai_pending ?? 0;
  $("#undecidedCount").textContent = c.undecided ?? 0;
  $("#keepCount").textContent = c.keep ?? p.decisions?.keep ?? 0;
  $("#removeCount").textContent = c.remove ?? p.decisions?.remove ?? 0;
  $("#unreadableCount").textContent = c.unreadable ?? p.counts?.unreadable ?? 0;
  $("#pairCount").textContent = p.similar_groups ?? p.pairs;
  $("#clearDecisionsBtn").disabled = !Object.values(p.decisions || {}).reduce(
    (sum, count) => sum + count,
    0,
  );
}
function applyProjectCounts(counts) {
  if (!state.project || !counts) return;
  Object.assign(state.project, counts);
  updateCounts(state.project);
}
async function refreshProject() {
  if (!state.project) return;
  const previousFormats = projectFormatValues(),
    selectedAll = setEquals(state.filters.formats, previousFormats);
  state.project = await json(`/api/project?project_id=${state.project.id}`);
  const availableFormats = projectFormatValues();
  state.filters.formats = selectedAll
    ? new Set(availableFormats)
    : new Set(
        [...state.filters.formats].filter((value) =>
          availableFormats.includes(value),
        ),
      );
  renderFormatFilterOptions();
  syncFilterControls();
  updateCounts(state.project);
}
async function startRequiredAnalysis() {
  if (!state.project) return;
  const niqe = state.project.niqe_rescan_required;
  const blink = state.project.blink_rescan_required;
  const preprocessing = state.project.preprocessing_rescan_required;
  if (!niqe && !blink && !preprocessing) return;
  const label = preprocessing ? "照片质量" : niqe && blink ? "画质与眨眼" : niqe ? "画质" : "眨眼";
  toast(`正在补充${label}分析，已有人工决定将保留`);
  await startScan();
}
async function startScan() {
  if (!state.project) return;
  await json("/api/scan", { project_id: state.project.id });
  pollProgress();
}
const wait = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));
async function pollProgress() {
  const generation = ++state.poll,
    projectId = state.project.id;
  $("#progressPanel").classList.remove("hidden");
  try {
    while (generation === state.poll && state.project?.id === projectId) {
      const p = await json(`/api/progress?project_id=${projectId}`);
      if (generation !== state.poll || state.project?.id !== projectId) return;
      $("#progressTitle").textContent = stageName[p.stage] || p.stage;
      if (p.stage === "discovering") {
        const location =
          p.current_directory && p.current_directory !== "."
            ? ` · ${p.current_directory}`
            : "";
        $("#progressDetail").textContent =
          `已检查 ${p.discovered_total || 0} 个文件 · ${p.photo_count || 0} 张照片${location}`;
      } else
        $("#progressDetail").textContent =
          p.file || `${p.current || 0} / ${p.total || 0}`;
      const indeterminate = !p.done && !p.total;
      $("#progressBar").classList.toggle("indeterminate", indeterminate);
      $("#progressBar").style.width = p.total
        ? `${Math.round((100 * (p.current || 0)) / p.total)}%`
        : p.done
          ? "100%"
          : "35%";
      if (p.done) {
        state.lastScan = p;
        if (p.error) toast(p.error);
        else if (p.csv_import_requires_attention)
          toast(
            `扫描完成；自动 CSV 有 ${p.csv_import_conflicting_groups || 0} 个多格式决定冲突，请手动导入处理`,
          );
        else if (!p.total && p.video_count)
          toast(`未发现照片；发现 ${p.video_count} 个视频，当前版本不支持视频`);
        else if (!p.total) toast("未发现支持的照片文件");
        else if (p.unavailable_count || p.inaccessible_count) {
          const skipped = [];
          if (p.unavailable_count)
            skipped.push(`${p.unavailable_count} 张照片在扫描期间不可用`);
          if (p.inaccessible_count)
            skipped.push(`${p.inaccessible_count} 个文件或目录无法访问`);
          toast(`扫描完成，${skipped.join("，")}，已安全跳过`);
        } else toast(stageName[p.stage] || "扫描结束");
        await refreshProject();
        if (generation !== state.poll) return;
        await loadView();
        setTimeout(() => {
          if (generation === state.poll)
            $("#progressPanel").classList.add("hidden");
        }, 2500);
        return;
      }
      await wait(700);
    }
  } catch (error) {
    if (generation === state.poll) toast(`读取扫描进度失败：${error.message}`);
  }
}

function bindSessionEvents() {
  $("#chooseBtn").onclick = chooseProject;
  $("#recentSearch").oninput = (event) => {
    state.recentQuery = event.target.value;
    renderRecentProjects();
  };
  $("#scanBtn").onclick = startScan;
  $("#cancelBtn").onclick = () =>
    json("/api/scan/cancel", { project_id: state.project.id });
  $("#homeBtn").onclick = () => {
    document.body.classList.remove(
      "project-open",
      "similar-view-open",
      "similar-detail-open",
      "similar-side-open",
    );
    $("#workspace").classList.add("hidden");
    $("#home").classList.remove("hidden");
    $("#searchInput").value = "";
    state.poll += 1;
    boot().catch((error) => toast(error.message));
  };
  $("#projectBox").ondblclick = openCurrentProjectFolder;
  $("#projectBox").onkeydown = (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    openCurrentProjectFolder();
  };
  $("#recentOpenFolder").onclick = openRecentFolder;
  $("#recentRemove").onclick = confirmRemoveRecent;
  document
    .querySelector("main")
    .addEventListener("scroll", closeRecentMenu, { passive: true });
}

// ---------------------------------------------------------------------------
// Drag-and-drop folder opening
// ---------------------------------------------------------------------------
// The real filesystem path can only come from Python: a browser exposes just
// the bare file name, and pywebview's macOS backend reads the path off the
// pasteboard only when a drop listener is registered through its DOM API (see
// the drop wiring in app.py). So paths arrive via cullumiAcceptDrop below,
// while the highlight is plain CSS driven from here.
const DROP_ACTIVE_CLASS = "drop-active";
let dragDepth = 0;

function setDropActive(active) {
  const home = $("#home");
  if (!home) return;
  // Only advertise the affordance where it is honoured. With a project open
  // the home view is display:none anyway, so this would never be visible.
  home.classList.toggle(DROP_ACTIVE_CLASS, Boolean(active) && !state.project);
}

function bindDropAffordance() {
  const swallow = (event) => {
    // Without this WKWebView tries to navigate to the dropped file.
    event.preventDefault();
    event.stopPropagation();
  };
  // dragenter/dragleave fire once per element crossed, so a depth counter is
  // needed -- a naive toggle flickers as the pointer moves over children.
  window.addEventListener("dragenter", (event) => {
    if (!event.dataTransfer) return;
    swallow(event);
    dragDepth += 1;
    setDropActive(true);
  });
  window.addEventListener("dragover", (event) => {
    if (!event.dataTransfer) return;
    swallow(event);
  });
  window.addEventListener("dragleave", (event) => {
    swallow(event);
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) setDropActive(false);
  });
  window.addEventListener("drop", (event) => {
    swallow(event);
    dragDepth = 0;
    setDropActive(false);
  });
}

// Called from app.py once it has a real, normalised folder path.
window.cullumiAcceptDrop = async function (root) {
  setDropActive(false);
  if (state.project) {
    toast("请先返回首页，再拖入新的文件夹");
    return;
  }
  try {
    const project = await json("/api/project/open", { root });
    await showProject(project);
    await startScan();
  } catch (error) {
    toast(`打开文件夹失败：${error.message}`);
  }
};

// Called from app.py when the drop was not a usable folder.
window.cullumiRejectDrop = function (reason) {
  setDropActive(false);
  toast(reason === "missing" ? "无法读取拖入文件夹的路径" : "只支持拖入文件夹");
};
