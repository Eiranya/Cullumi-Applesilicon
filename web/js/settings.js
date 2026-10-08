function getPath(o, path) {
  return path.split(".").reduce((a, k) => a?.[k], o);
}
function setPath(o, path, v) {
  const ks = path.split(".");
  let a = o;
  ks.slice(0, -1).forEach((k) => (a = a[k]));
  a[ks.at(-1)] = v;
}
function editorLoad(id) {
  const p = structuredClone(state.profiles.find((x) => x.id === id));
  state.editor = p;
  $("#profileName").value = p.name;
  $("#profileName").disabled = p.builtin;
  $("#saveProfile").disabled = p.builtin;
  $("#deleteProfile").disabled = p.builtin;
  $$("[data-p]").forEach((el) => {
    const v = getPath(p, el.dataset.p);
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v;
  });
  clearProfileValidation();
}
function editorRead() {
  const p = state.editor;
  p.name = $("#profileName").value.trim();
  $$("[data-p]").forEach((el) =>
    setPath(
      p,
      el.dataset.p,
      el.type === "checkbox"
        ? el.checked
        : el.type === "number"
          ? Number(el.value)
          : el.value,
    ),
  );
  return p;
}
function showProfileStatus(message, tone = "", stateName = "status") {
  const el = $("#estimate");
  el.textContent = message;
  el.dataset.state = stateName;
  el.className = `estimate${tone ? ` ${tone}` : ""}`;
}
function profileSaveStatus(message, failed = false) {
  showProfileStatus(message, failed ? "failed" : "success", "save");
}
function clearProfileValidation() {
  [$("#profileName"), ...$$('.form-grid input[type="number"][data-p]')].forEach(
    (el) => {
      el.classList.remove("input-invalid");
      el.closest("label")?.classList.remove("field-invalid");
    },
  );
  const status = $("#estimate");
  status.textContent = "调整后可先预估影响。";
  status.dataset.state = "default";
  status.className = "estimate";
  syncProfileEstimateVisibility();
}
function validateProfileInputs(showBottom = true) {
  const fields = [
      $("#profileName"),
      ...$$('.form-grid input[type="number"][data-p]'),
    ],
    missing = fields.filter((el) => !String(el.value).trim());
  fields.forEach((el) => {
    const invalid = missing.includes(el);
    el.classList.toggle("input-invalid", invalid);
    el.closest("label")?.classList.toggle("field-invalid", invalid);
  });
  if (missing.length) {
    if (showBottom)
      profileSaveStatus("保存失败：还有项目没有输入完整，请填写标红的项目。", true);
    missing[0].focus();
    return false;
  }
  return true;
}
async function saveProfile() {
  if (!validateProfileInputs(true)) return;
  const button = $("#saveProfile");
  button.disabled = true;
  profileSaveStatus("正在保存…");
  try {
    const p = await json("/api/profile/save", {
      profile: editorRead(),
      project_id: state.project?.id || "",
    });
    const i = state.profiles.findIndex((x) => x.id === p.id);
    if (i >= 0) state.profiles[i] = p;
    else state.profiles.push(p);
    renderProfiles();
    $("#profileEditorSelect").value = p.id;
    editorLoad(p.id);
    if (state.project?.profile_id === p.id) {
      await refreshProject();
      await startRequiredAnalysis();
    }
    profileSaveStatus("✓ 自定义模式已保存");
    toast("自定义模式已保存");
  } catch (e) {
    profileSaveStatus(`保存失败：${e.message}`, true);
    toast(`保存失败：${e.message}`);
  } finally {
    button.disabled = !!state.editor?.builtin;
  }
}
async function cloneProfile() {
  const source = state.profiles.find(
    (x) => x.id === $("#profileEditorSelect").value,
  );
  const p = structuredClone(source);
  p.base_mode = source.builtin ? source.id : source.base_mode || "balanced";
  p.id = "";
  p.name = p.name + " 副本";
  p.builtin = false;
  p.quality.blur_review_percentile ??= 5;
  p.quality.blur_remove_percentile ??= 1;
  delete p.created_at;
  delete p.updated_at;
  state.editor = p;
  clearProfileValidation();
  $("#profileName").disabled = false;
  $("#saveProfile").disabled = false;
  $("#deleteProfile").disabled = true;
  $("#profileName").value = p.name;
  $$("[data-p]").forEach((el) => {
    const v = getPath(p, el.dataset.p);
    el.type === "checkbox" ? (el.checked = !!v) : (el.value = v);
  });
  toast("请命名并保存新模式");
}
async function applyProfile(id) {
  try {
    state.project = await json("/api/profile/apply", {
      project_id: state.project.id,
      profile_id: id,
    });
    updateCounts(state.project);
    toast("已切换分析模式");
    await loadView();
    await startRequiredAnalysis();
  } catch (e) {
    toast(e.message);
  }
}
async function estimate() {
  if (!validateProfileInputs(false)) {
    showProfileStatus(
      "预估失败：还有项目没有输入完整，请填写标红的项目。",
      "failed",
      "estimate",
    );
    return;
  }
  const button = $("#estimateBtn");
  button.disabled = true;
  showProfileStatus("正在按当前参数完整预估…", "", "estimate");
  const status = $("#estimate");
  try {
    const d = await json("/api/profile/estimate", {
      project_id: state.project.id,
      profile: editorRead(),
    });
    status.textContent =
      `预计：建议移除 ${d.counts.remove || 0} 张，人工复看 ${d.counts.review || 0} 张，相似组 ${d.estimated_groups || 0} 组（${d.estimated_pairs || 0} 条关系）。`;
  } catch (e) {
    showProfileStatus(`预估失败：${e.message}`, "failed", "estimate");
    toast(`预估失败：${e.message}`);
  } finally {
    button.disabled = false;
  }
}
function closeFilterMenus() {
  $$(".multi-filter-panel").forEach((panel) => panel.classList.add("hidden"));
  $$(".multi-filter-trigger").forEach((button) =>
    button.setAttribute("aria-expanded", "false"),
  );
}
function closeDialogs(e) {
  const d = e.target.closest("dialog");
  if (d) d.close();
}
function closeNoticeOnBackdrop(event) {
  const dialog = event.currentTarget,
    box = dialog.getBoundingClientRect();
  const outside =
    event.clientX < box.left ||
    event.clientX > box.right ||
    event.clientY < box.top ||
    event.clientY > box.bottom;
  if (outside) dialog.close();
}
function closeRecentMenu() {
  state.recentMenuId = "";
  $("#recentMenu").classList.add("hidden");
}
function openRecentMenu(event, projectId) {
  event.preventDefault();
  event.stopPropagation();
  state.recentMenuId = projectId;
  const project = state.recentProjects.find((x) => x.id === projectId);
  const menu = $("#recentMenu");
  $("#recentOpenFolder").disabled = !project?.available;
  menu.classList.remove("hidden");
  const left = Math.min(
    event.clientX,
    window.innerWidth - menu.offsetWidth - 8,
  );
  const top = Math.min(
    event.clientY,
    window.innerHeight - menu.offsetHeight - 8,
  );
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
}
async function openRecentFolder() {
  const id = state.recentMenuId;
  closeRecentMenu();
  if (!id) return;
  try {
    await json("/api/project/open-folder", { project_id: id });
  } catch (e) {
    toast(e.message);
  }
}
async function openCurrentProjectFolder() {
  if (!state.project) return;
  try {
    await json("/api/project/open-folder", { project_id: state.project.id });
  } catch (e) {
    toast(e.message);
  }
}
async function checkForUpdates(manual = true) {
  if (state.updateChecking) return;
  state.updateChecking = true;
  const button = $("#checkUpdateBtn"),
    status = $("#updateStatus");
  button.disabled = true;
  if (manual) status.textContent = "正在连接 GitHub…";
  try {
    const update = await json("/api/update/check", {});
    if (update.update_available) {
      status.textContent = `发现 v${update.latest_version}`;
      showUpdatePrompt(update);
    } else if (update.no_release) {
      if (manual) {
        status.textContent = "发布页暂时没有可用版本";
        toast("暂时没有可用的发布版本");
      }
    } else if (manual) {
      status.textContent = `当前 v${update.current_version} 已是最新版本`;
      toast("当前已是最新版本");
    }
  } catch (e) {
    if (manual) {
      status.textContent = `检查失败：${e.message}`;
      toast(`检查更新失败：${e.message}`);
    } else console.warn("自动检查更新失败", e);
  } finally {
    state.updateChecking = false;
    button.disabled = false;
  }
}
function appendReleaseNotesInline(parent, source) {
  const pattern =
    /(`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\)|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/gi;
  let offset = 0;
  for (const match of source.matchAll(pattern)) {
    parent.append(document.createTextNode(source.slice(offset, match.index)));
    const token = match[0],
      link = token.match(/^\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)$/i);
    if (link) {
      const anchor = document.createElement("a");
      anchor.textContent = link[1];
      anchor.href = link[2];
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.referrerPolicy = "no-referrer";
      parent.append(anchor);
    } else if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else {
      const emphasis = document.createElement("em");
      emphasis.textContent = token.slice(1, -1);
      parent.append(emphasis);
    }
    offset = match.index + token.length;
  }
  parent.append(document.createTextNode(source.slice(offset)));
}
function renderReleaseNotesMarkdown(target, markdown) {
  const source = typeof markdown === "string" ? markdown.trim() : "";
  target.replaceChildren();
  target.classList.toggle("empty", !source);
  if (!source) {
    target.textContent = "本次发布暂未提供更新说明。";
    return;
  }

  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [],
    list = null,
    fencedCode = null;
  const flushParagraph = () => {
      if (!paragraph.length) return;
      const element = document.createElement("p");
      appendReleaseNotesInline(element, paragraph.join(" "));
      target.append(element);
      paragraph = [];
    },
    flushCode = () => {
      if (!fencedCode) return;
      const pre = document.createElement("pre"),
        code = document.createElement("code");
      code.textContent = fencedCode.lines.join("\n");
      if (fencedCode.language) code.dataset.language = fencedCode.language;
      pre.append(code);
      target.append(pre);
      fencedCode = null;
    };

  for (const line of lines) {
    if (fencedCode) {
      if (/^\s*```\s*$/.test(line)) flushCode();
      else fencedCode.lines.push(line);
      continue;
    }
    const fence = line.match(/^\s*```([\w.+-]*)\s*$/);
    if (fence) {
      flushParagraph();
      list = null;
      fencedCode = { language: fence[1], lines: [] };
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      list = null;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      flushParagraph();
      list = null;
      const level = Math.min(6, heading[1].length + 3),
        element = document.createElement(`h${level}`);
      appendReleaseNotesInline(element, heading[2]);
      target.append(element);
      continue;
    }
    if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      list = null;
      target.append(document.createElement("hr"));
      continue;
    }
    const unordered = line.match(/^\s{0,3}[-*+]\s+(.+)$/),
      ordered = line.match(/^\s{0,3}\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const tag = unordered ? "UL" : "OL";
      if (!list || list.tagName !== tag) {
        list = document.createElement(tag.toLowerCase());
        target.append(list);
      }
      const item = document.createElement("li");
      appendReleaseNotesInline(item, (unordered || ordered)[1]);
      list.append(item);
      continue;
    }
    const quote = line.match(/^\s{0,3}>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      list = null;
      const element = document.createElement("blockquote");
      appendReleaseNotesInline(element, quote[1]);
      target.append(element);
      continue;
    }
    list = null;
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushCode();
}
function hostPlatformName(update) {
  // Prefer the platform reported by the backend: it reflects the packaged
  // build, whereas the user agent only reflects the embedded WebView.
  if (update && update.platform) return update.platform;
  const ua = navigator.userAgent || "";
  if (/Macintosh|Mac OS X/i.test(ua)) return "macOS";
  return "Windows";
}
function showUpdatePrompt(update) {
  const platform = hostPlatformName(update);
  $("#updateTitle").textContent = `发现新版本 v${update.latest_version}`;
  $("#updateBody").innerHTML = update.download_available
    ? `<p>当前版本为 v${esc(update.current_version)}，是否将 <b>${esc(update.asset_name)}</b> 下载到系统 Downloads 文件夹？</p><p id="updateDownloadStatus" class="update-download-status">照片和项目数据不会受到影响。</p>`
    : `<p>当前版本为 v${esc(update.current_version)}，新版本已经发布，但发布页没有可直接下载的 ${esc(platform)} 附件。</p><p id="updateDownloadStatus" class="update-download-status">可以前往 Releases 页面查看详情。</p>`;
  renderReleaseNotesMarkdown(
    $("#updateReleaseNotesBody"),
    update.release_notes,
  );
  const button = $("#updateDownload");
  button.disabled = false;
  button.textContent = update.download_available ? "下载更新" : "查看发布页";
  button.onclick = update.download_available
    ? downloadUpdate
    : async () => {
        await json("/api/update/open", {});
        $("#updateDialog").close();
      };
  $("#updateDialog").showModal();
}
async function downloadUpdate() {
  const button = $("#updateDownload"),
    status = $("#updateDownloadStatus");
  button.disabled = true;
  button.textContent = "正在下载…";
  status.textContent = "正在下载更新，请不要关闭软件。";
  try {
    const result = await json("/api/update/download", {});
    $("#updateTitle").textContent = `v${result.version} 已下载`;
    $("#updateBody").innerHTML =
      `<p>更新包已保存到：</p><p class="download-path">${esc(result.path)}</p><p>关闭当前软件后，解压并运行新版本即可。</p>`;
    button.disabled = false;
    button.textContent = "完成";
    button.onclick = () => $("#updateDialog").close();
    toast("更新包下载完成");
  } catch (e) {
    status.textContent = `下载失败：${e.message}`;
    button.disabled = false;
    button.textContent = "重试下载";
    toast(`下载失败：${e.message}`);
  }
}
function confirmRemoveRecent() {
  const id = state.recentMenuId;
  const project = state.recentProjects.find((x) => x.id === id);
  closeRecentMenu();
  if (!project) return;
  $("#confirmTitle").textContent = "从最近项目中移除？";
  $("#confirmBody").innerHTML =
    `<p>“${esc(project.root.split(/[\\/]/).pop())}”将从首页列表移除。真实照片不会被删除或移动。</p><label class="toggle confirm-option"><input id="deleteProjectCache" type="checkbox"><span>同时删除项目缓存（包含原图备份）</span></label><p class="confirm-note">不勾选时，数据库、缩略图和原图备份会保留，今后重新打开该照片目录可继续使用。</p>`;
  const button = prepareConfirmAction();
  button.textContent = "确认移除";
  button.onclick = async () => {
    const deleteCache = $("#deleteProjectCache").checked;
    try {
      await json("/api/project/remove-recent", {
        project_id: id,
        delete_cache: deleteCache,
      });
      $("#confirm").close();
      toast(deleteCache ? "项目缓存和原图备份已删除" : "已从最近项目中移除");
      await boot();
    } catch (e) {
      toast(e.message);
    }
  };
  $("#confirm").showModal();
}
function projectsUsingProfile(profileId) {
  const projects = [],
    seen = new Set();
  [state.project, ...state.recentProjects]
    .filter(Boolean)
    .forEach((project) => {
      if (project.id === state.project?.id && project !== state.project) return;
      if (project.profile_id === profileId && !seen.has(project.id)) {
        seen.add(project.id);
        projects.push(project);
      }
    });
  return projects;
}
function showProfileInUseWarning(profile, projects = []) {
  const current = projects.some((project) => project.id === state.project?.id);
  let message = `“${profile.name}”仍被某个项目使用，暂时不能删除。请先将使用该模式的项目切换到其他分析模式，再删除此模式。`;
  if (current)
    message = `“${profile.name}”正在被当前项目使用，暂时不能删除。请先在窗口顶部切换到其他分析模式，再删除此模式。`;
  else if (projects.length) {
    const names = projects.map(recentProjectName).join("”、“");
    message = `“${profile.name}”仍被项目“${names}”使用，暂时不能删除。请先打开该项目并切换到其他分析模式，再删除此模式。`;
  }
  $("#profileInUseWarningBody").textContent = message;
  $("#profileInUseWarning").showModal();
}
function confirmDeleteProfile() {
  const profile = state.editor;
  if (!profile || profile.builtin) return;
  const projects = projectsUsingProfile(profile.id);
  if (projects.length) {
    showProfileInUseWarning(profile, projects);
    return;
  }
  $("#confirmTitle").textContent = "删除自定义模式？";
  $("#confirmBody").textContent =
    `“${profile.name}”将从本机配置中删除，此操作无法撤销。`;
  const button = prepareConfirmAction();
  button.textContent = "确认删除";
  button.onclick = async () => {
    try {
      await json("/api/profile/delete", { profile_id: profile.id });
      $("#confirm").close();
      state.profiles = state.profiles.filter((x) => x.id !== profile.id);
      renderProfiles();
      editorLoad(state.profiles[0].id);
      toast("自定义模式已删除");
    } catch (e) {
      if (
        e.message.includes("被项目使用") ||
        e.message.includes("切换项目模式")
      ) {
        $("#confirm").close();
        showProfileInUseWarning(profile);
      } else toast(e.message);
    }
  };
  $("#confirm").showModal();
}
function confirmClearDecisions() {
  const count = Object.values(state.project?.decisions || {}).reduce(
    (a, b) => a + b,
    0,
  );
  if (!count) {
    toast("没有需要清空的选择");
    return;
  }
  $("#confirmTitle").textContent = "清空所有选择？";
  $("#confirmBody").textContent =
    `将清除当前项目中 ${count} 张照片的“保留/移除”选择，照片文件不会被移动或删除。`;
  const button = prepareConfirmAction();
  button.textContent = "确认清空";
  button.onclick = async () => {
    try {
      const r = await json("/api/decision/clear", {
        project_id: state.project.id,
      });
      $("#confirm").close();
      toast(`已清空 ${r.cleared} 张照片的选择`);
      applyProjectCounts(r.project_counts);
      loadView();
    } catch (e) {
      toast(e.message);
    }
  };
  $("#confirm").showModal();
}
async function executeAcceptSuggestions(context, preferenceWarning = "") {
  const r = await json("/api/decision/accept", {
    project_id: context.projectId,
    scope: context.scope,
    ...(context.currentGroup ? { group_id: context.currentGroup } : {}),
  });
  applyProjectCounts(r.project_counts);
  if (context.currentGroup) {
    await loadSimilarGroupMembers();
    if (
      state.settings.auto_advance &&
      state.similar.mode !== "expanded" &&
      similarGroupComplete()
    ) await advanceSimilarGroup(false);
  } else await loadView();
  toast(
    `已采纳 ${r.marked} 张照片：保留 ${r.kept} 张，移除 ${r.removed} 张${r.skipped_conflicting_groups ? `，跳过 ${r.skipped_conflicting_groups} 个已有冲突决定的关联组` : ""}${preferenceWarning}`,
  );
}

async function runAcceptSuggestions(context, dontAsk = false) {
  const button = $("#acceptSuggestionsBtn");
  let preferenceWarning = "";
  button.disabled = true;
  try {
    if (dontAsk) {
      try {
        const saved = await json("/api/settings", {
          confirm_accept_suggestions: false,
        });
        state.settings.confirm_accept_suggestions =
          saved.settings.confirm_accept_suggestions;
        $("#confirmAcceptSuggestions").checked =
          saved.settings.confirm_accept_suggestions !== false;
      } catch (error) {
        preferenceWarning = `；“不再提醒”未保存：${error.message}`;
      }
    }
    await executeAcceptSuggestions(context, preferenceWarning);
  } catch (error) {
    toast(`${error.message}${preferenceWarning}`);
  } finally {
    button.disabled = false;
  }
}

function confirmAcceptSuggestions() {
  const similar = state.view === "similar",
    currentGroup = similar && state.similar.selectedId,
    context = {
      projectId: state.project.id,
      scope: similar ? "similar" : state.activeNav,
      currentGroup,
  };
  if (state.settings.confirm_accept_suggestions === false) {
    runAcceptSuggestions(context);
    return;
  }
  const dialog = $("#confirm");
  $("#confirmTitle").textContent = "采纳推荐决定？";
  const message = similar
    ? currentGroup
      ? "当前组中推荐保留的照片会标记为“保留”，其余照片会标记为“移除”。不会修改已决定照片。"
      : "所有相似组中推荐保留的照片会标记为“保留”，其余照片会标记为“移除”。不会修改已决定照片。"
    : state.settings.remove_review_on_accept
      ? "建议移除和人工复查照片会标记为“移除”，无建议照片保持未决定。不会修改已决定照片。"
      : "建议移除照片会标记为“移除”，人工复查和无建议照片保持未决定。不会修改已决定照片。";
  $("#confirmBody").innerHTML =
    `<p>${esc(message)}</p><label class="toggle confirm-option"><input id="acceptSuggestionsDontAsk" type="checkbox"><span>不再提醒</span></label><p class="confirm-note">可以随时在设置中重新开启确认。</p>`;
  const button = prepareConfirmAction("confirm-accept-action");
  button.textContent = "确认采纳";
  dialog.addEventListener(
    "close",
    () => prepareConfirmAction(),
    { once: true },
  );
  button.onclick = async () => {
    const dontAsk = $("#acceptSuggestionsDontAsk").checked;
    dialog.close();
    await runAcceptSuggestions(context, dontAsk);
  };
  dialog.showModal();
}

function selectSettingsPage(button) {
  $$("[data-setting]").forEach((candidate) => {
    const active = candidate === button;
    candidate.classList.toggle("active", active);
    active
      ? candidate.setAttribute("aria-current", "page")
      : candidate.removeAttribute("aria-current");
  });
  $$("[data-setting-page]").forEach((page) =>
    page.classList.toggle(
      "hidden",
      page.dataset.settingPage !== button.dataset.setting,
    ),
  );
  $("#settingsPageTitle").textContent =
    button.dataset.settingsTitle || button.textContent.trim();
  syncProfileEstimateVisibility();
}

function syncProfileEstimateVisibility() {
  const projectOpen =
    !!state.project && document.body.classList.contains("project-open");
  const status = $("#estimate");
  const defaultMessage = status.dataset.state === "default";
  status.classList.toggle("hidden", !projectOpen && defaultMessage);
  $("#estimateBtn").classList.toggle("hidden", !projectOpen);
}

async function chooseDefaultCache() {
  const button = $("#defaultCacheBtn");
  try {
    const selected = await json("/api/choose-cache", {});
    if (!selected.path) return;
    button.disabled = true;
    const saved = await json("/api/settings", {
      default_cache_root: selected.path,
    });
    const cacheRoot = saved.settings.default_cache_root;
    state.settings.default_cache_root = cacheRoot;
    $("#defaultCache").value = cacheRoot;
    toast("默认位置已保存");
  } catch (error) {
    toast(`保存失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function migrateProjectCache() {
  if (!state.project) return;
  const button = $("#projectCacheBtn");
  try {
    const selected = await json("/api/choose-cache", {});
    if (!selected.path) return;
    button.disabled = true;
    const migration = await json("/api/project/cache", {
      project_id: state.project.id,
      cache_root: selected.path,
    });
    state.project.cache_root = migration.cache_root;
    $("#projectCache").value = migration.cache_root;
    if (migration.old_cache) {
      $("#oldCaches").innerHTML =
        `<div class="old-cache-row"><p class="old-cache-path">旧缓存已保留：${esc(migration.old_cache)}</p><button id="cleanOld">清理</button></div>`;
      $("#cleanOld").onclick = async () => {
        try {
          await json("/api/project/cache/cleanup", {
            project_id: state.project.id,
            path: migration.old_cache,
          });
          $("#oldCaches").innerHTML =
            '<span class="empty-cache">暂无待清理的旧缓存</span>';
          toast("旧缓存已清理");
        } catch (error) {
          toast(`清理失败：${error.message}`);
        }
      };
    }
    toast(
      migration.changed ? "迁移完成，旧缓存仍保留" : "当前项目已使用此存储位置",
    );
  } catch (error) {
    toast(`迁移失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

function addProfileResetButtons() {
  $$(".form-grid [data-p]").forEach((element) => {
    const button = document.createElement("button");
    const label = element.closest("label");
    const field = element.closest(".select-field") || element;
    button.type = "button";
    button.className = "field-reset";
    button.innerHTML =
      `<svg viewBox="0 0 1024 1024" aria-hidden="true"><use transform="translate(1024 0) scale(-1 1)" href="#motion-reset"></use></svg>`;
    button.title = "恢复基础模式默认值";
    button.setAttribute("aria-label", button.title);
    label.classList.add("field-reset-label");
    label.insertBefore(button, field);
    button.onclick = () => {
      const base =
        state.profiles.find(
          (profile) => profile.id === (state.editor?.base_mode || "balanced"),
        ) || state.profiles.find((profile) => profile.id === "balanced");
      const value = getPath(base, element.dataset.p);
      if (value === undefined) return;
      setPath(state.editor, element.dataset.p, value);
      element.value = value;
    };
  });
}

function configureProfileInputs() {
  const numberRanges = {
    "quality.min_megapixels_review": [0, 500],
    "quality.min_megapixels_remove": [0, 500],
    "quality.min_size_kb_review": [0, 10000000],
    "quality.min_size_kb_remove": [0, 10000000],
    "similarity.time_window_minutes": [0, 10080],
    "similarity.sequence_gap": [0, 10000],
    "similarity.min_group_size": [2, 1000],
    "similarity.blink.face_confidence_min": [0.5, 0.99],
    "similarity.blink.open_confidence_min": [0.5, 0.99],
    "similarity.blink.closed_confidence_min": [0.5, 0.99],
    "similarity.blink.min_eye_distance_px": [4, 64],
    "similarity.blink.reliable_coverage_min": [0.5, 1],
  };
  $$('.form-grid input[type="number"][data-p]').forEach((element) => {
    const range = numberRanges[element.dataset.p] || [element.min, element.max];
    if (range[0] !== "" && range[0] !== undefined) element.min = range[0];
    if (range[1] !== "" && range[1] !== undefined) element.max = range[1];
    element.placeholder = `请输入 ${element.min}–${element.max}`;
    element.addEventListener("input", () => {
      if (!String(element.value).trim()) return;
      element.classList.remove("input-invalid");
      element.closest("label")?.classList.remove("field-invalid");
    });
  });
  $("#profileName").addEventListener("input", () => {
    if (!$("#profileName").value.trim()) return;
    $("#profileName").classList.remove("input-invalid");
    $("#profileName").closest("label")?.classList.remove("field-invalid");
  });
}

// The switch records the user's intent; `active` is what the backend reports
// it will really use. Keeping the two separate means a Mac that cannot offer
// the accelerator says so instead of silently showing "on".
function renderBlinkGpuStatus(enabled, active) {
  const node = $("#blinkGpuStatus");
  if (!node) return;
  if (!enabled) node.textContent = "已关闭，使用 CPU 检测。";
  else if (active) node.textContent = "已启用，正在使用硬件加速。";
  else node.textContent = "已启用，但此设备不可用，已回退到 CPU。";
}

function bindSettingsEvents() {
  $("#settingsBtn").onclick = () => {
    $("#settings").showModal();
    const projectOpen = document.body.classList.contains("project-open");
    syncProfileEstimateVisibility();
    $("#projectCacheSettingsRow").classList.toggle("hidden", !projectOpen);
    if (projectOpen && state.project)
      $("#projectCache").value = state.project.cache_root;
    $("#profileEditorSelect").value =
      state.project?.profile_id || state.profiles[0]?.id;
    editorLoad($("#profileEditorSelect").value);
    // 开机时 bootstrap 已把设置装进 state（session.js 的 boot），这里只负责把
    // 持久化值回放到开关上。语义是「显示」：只有 true 才勾选，缺省/非法一律
    // 视为关（隐藏）。
    $("#viewerBottomStatusLines").checked =
      state.settings.viewer_bottom_status_lines === true;
  };
  $$("[data-setting]").forEach(
    (button) => (button.onclick = () => selectSettingsPage(button)),
  );
  $("#autoAdvance").onchange = async (event) => {
    state.settings.auto_advance = event.target.checked;
    await json("/api/settings", { auto_advance: event.target.checked });
  };
  $("#removeReviewOnAccept").onchange = async (event) => {
    const previous = !!state.settings.remove_review_on_accept;
    try {
      const saved = await json("/api/settings", {
        remove_review_on_accept: event.target.checked,
      });
      state.settings.remove_review_on_accept =
        saved.settings.remove_review_on_accept;
    } catch (error) {
      event.target.checked = previous;
      toast(`保存一键采纳设置失败：${error.message}`);
    }
  };
  $("#confirmAcceptSuggestions").onchange = async (event) => {
    const input = event.target,
      previous = state.settings.confirm_accept_suggestions !== false;
    input.disabled = true;
    try {
      const saved = await json("/api/settings", {
        confirm_accept_suggestions: input.checked,
      });
      state.settings.confirm_accept_suggestions =
        saved.settings.confirm_accept_suggestions;
      input.checked = saved.settings.confirm_accept_suggestions !== false;
    } catch (error) {
      input.checked = previous;
      toast(`保存一键采纳确认设置失败：${error.message}`);
    } finally {
      input.disabled = false;
    }
  };
  $("#fastAnalysis").onchange = async (event) => {
    const input = event.target, previous = !!state.settings.fast_analysis;
    input.disabled = true;
    try {
      await json("/api/settings", { fast_analysis: input.checked });
      state.settings.fast_analysis = input.checked;
    } catch (error) {
      input.checked = previous;
      toast(`保存失败：${error.message}`);
    } finally {
      input.disabled = false;
    }
  };
  $("#blinkGpu").onchange = async (event) => {
    const input = event.target,
      previous = state.settings.blink_gpu_enabled !== false;
    input.disabled = true;
    try {
      const saved = await json("/api/settings", {
        blink_gpu_enabled: input.checked,
      });
      state.settings.blink_gpu_enabled = input.checked;
      // The backend reports what inference will ACTUALLY use, which can differ
      // from the switch when this Mac cannot offer the accelerator.
      state.settings.blink_gpu_active = saved.blink_gpu_active !== false;
      renderBlinkGpuStatus(input.checked, state.settings.blink_gpu_active);
    } catch (error) {
      input.checked = previous;
      toast(`保存硬件加速设置失败：${error.message}`);
    } finally {
      input.disabled = false;
    }
  };
  $("#syncVariantDecisions").onchange = async (event) => {
    const previous = state.settings.sync_variant_decisions !== false;
    try {
      const saved = await json("/api/settings", {
        sync_variant_decisions: event.target.checked,
      });
      state.settings.sync_variant_decisions =
        saved.settings.sync_variant_decisions;
    } catch (error) {
      event.target.checked = previous;
      toast(`保存同步决定设置失败：${error.message}`);
    }
  };
  $("#autoCheckUpdates").onchange = async (event) => {
    state.settings.auto_check_updates = event.target.checked;
    await json("/api/settings", { auto_check_updates: event.target.checked });
  };
  $("#motionCoverWriteback").onchange = async (event) => {
    const previous = state.settings.motion_cover_writeback || "ask";
    try {
      const saved = await json("/api/settings", {
        motion_cover_writeback: event.target.value,
      });
      state.settings.motion_cover_writeback =
        saved.settings.motion_cover_writeback || event.target.value;
    } catch (error) {
      event.target.value = previous;
      toast(`保存原图修改设置失败：${error.message}`);
    }
  };
  // 滚轮灵敏度：拖动时只更新读数（oninput），松手（onchange）才落盘一次，避免每一
  // 格都打一次 /api/settings。保存失败要把滑杆退回旧值，否则界面会显示一个没存上的数。
  [
    ["viewerWheelTrackpad", "viewer_wheel_trackpad_sensitivity"],
    ["viewerWheelMouse", "viewer_wheel_mouse_sensitivity"],
  ].forEach(([id, key]) => {
    const input = $(`#${id}`),
      output = $(`#${id}Value`),
      render = () => {
        output.textContent = `${Number(input.value).toFixed(1)}×`;
      };
    input.oninput = render;
    input.onchange = async () => {
      const previous = Number(state.settings[key]) || 1;
      try {
        const saved = await json("/api/settings", { [key]: Number(input.value) });
        state.settings[key] = Number(saved.settings[key]);
        input.value = String(state.settings[key]);
      } catch (error) {
        input.value = String(previous);
        toast(`保存滚轮灵敏度失败：${error.message}`);
      }
      render();
    };
    render();
  });
  // 输入设备：自动识别是启发式、会判错，所以必须让用户能强制指定。
  $("#viewerWheelDevice").onchange = async (event) => {
    const previous = state.settings.viewer_wheel_device || "auto";
    try {
      const saved = await json("/api/settings", {
        viewer_wheel_device: event.target.value,
      });
      state.settings.viewer_wheel_device =
        saved.settings.viewer_wheel_device || event.target.value;
      event.target.value = state.settings.viewer_wheel_device;
    } catch (error) {
      event.target.value = previous;
      toast(`保存输入设备设置失败：${error.message}`);
    }
  };
  // 底部状态行：默认隐藏（键语义是「显示」），关闭可增大图片显示面积。
  // 保存失败把勾选退回旧值；无论成败都重套一次类——切换要立即生效，包括当前
  // 已打开的查看器（figcaption 变矮后媒体区变高，applyViewerStatusLinesSetting
  // 内部会按 resize 语义重跑 fit）。
  $("#viewerBottomStatusLines").onchange = async (event) => {
    const previous = state.settings.viewer_bottom_status_lines === true;
    try {
      const saved = await json("/api/settings", {
        viewer_bottom_status_lines: event.target.checked,
      });
      state.settings.viewer_bottom_status_lines =
        saved.settings.viewer_bottom_status_lines === true;
    } catch (error) {
      event.target.checked = previous;
      toast(`保存状态行设置失败：${error.message}`);
    }
    applyViewerStatusLinesSetting();
  };
  $("#checkUpdateBtn").onclick = () => checkForUpdates(true);
  $("#defaultCacheBtn").onclick = chooseDefaultCache;
  $("#projectCacheBtn").onclick = migrateProjectCache;
  $("#profileEditorSelect").onchange = (event) =>
    editorLoad(event.target.value);
  $("#cloneProfile").onclick = cloneProfile;
  $("#saveProfile").onclick = saveProfile;
  $("#estimateBtn").onclick = estimate;
  $("#deleteProfile").onclick = confirmDeleteProfile;
  addProfileResetButtons();
  configureProfileInputs();
}
