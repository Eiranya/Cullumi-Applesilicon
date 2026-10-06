const MOTION_PLAY_ICON = `<svg viewBox="0 0 1024 1024" aria-hidden="true"><use href="#motion-play"></use></svg>`;
const MOTION_PAUSE_ICON = `<svg viewBox="0 0 1024 1024" aria-hidden="true"><use href="#motion-pause"></use></svg>`;
const MOTION_MUTED_ICON = `<svg viewBox="0 0 1024 1024" aria-hidden="true"><use href="#motion-muted"></use></svg>`;
const MOTION_SOUND_ICON = `<svg viewBox="0 0 1024 1024" aria-hidden="true"><use href="#motion-sound"></use></svg>`;

function viewerSuggestion(p) {
  if (p._viewerBadge) return { text: p._viewerBadge, kind: p._viewerKind };
  if (p.suggestion === "remove") return { text: "建议移除", kind: "remove" };
  if (p.suggestion === "review") return { text: "人工复查", kind: "review" };
  return { text: "", kind: "" };
}
function syncCardAnalysis(p) {
  const suggestion = viewerSuggestion({
    ...p,
    _viewerBadge: "",
    _viewerKind: "",
  });
  $$(`[data-photo-id="${p.id}"]`).forEach((card) => {
    const thumb = card.querySelector(".thumb"),
      contextBadge = thumb?.querySelector("[data-context-badge]");
    let badge = thumb?.querySelector("[data-analysis-badge]");
    if (suggestion.text && !contextBadge) {
      if (!badge) {
        badge = document.createElement("span");
        badge.dataset.analysisBadge = "";
        thumb.appendChild(badge);
      }
      badge.className = `badge badge-${suggestion.kind}`;
      badge.textContent = suggestion.text;
    } else badge?.remove();
    const image = card.querySelector("img"),
      detail = card.querySelector("small");
    if (image) image.src = p.thumb_url;
    if (detail) detail.textContent = cardDetailText(p);
  });
}
function updateViewerDecision(p) {
  $("#viewerKeep").classList.toggle("active", p.decision === "keep");
  $("#viewerRemove").classList.toggle("active", p.decision === "remove");
}
function viewerTransformTarget() {
  return state.viewerMotion.active ? $("#viewerVideo") : $("#viewerImage");
}
function applyViewerTransform() {
  const t = state.viewerTransform,
    target = viewerTransformTarget();
  [$("#viewerImage"), $("#viewerVideo")].forEach((media) => {
    if (media !== target) {
      media.style.transform = "";
      media.classList.remove("zoomed", "dragging");
    }
  });
  target.style.transform = `translate3d(${t.x}px,${t.y}px,0) scale(${t.scale})`;
  target.classList.toggle("zoomed", t.scale > 1);
  target.classList.toggle("dragging", t.dragging);
}
function clampViewerPan() {
  const t = state.viewerTransform,
    target = viewerTransformTarget(),
    maxX = Math.max(0, (target.offsetWidth * (t.scale - 1)) / 2),
    maxY = Math.max(0, (target.offsetHeight * (t.scale - 1)) / 2);
  t.x = Math.max(-maxX, Math.min(maxX, t.x));
  t.y = Math.max(-maxY, Math.min(maxY, t.y));
}
function resetViewerTransform() {
  Object.assign(state.viewerTransform, {
    scale: 1,
    x: 0,
    y: 0,
    dragging: false,
    moved: false,
    suppressClick: false,
  });
  clearTimeout(state.viewerClickTimer);
  applyViewerTransform();
}
function zoomViewer(factor, clientX, clientY) {
  const t = state.viewerTransform,
    figure = viewerTransformTarget().parentElement,
    rect = figure.getBoundingClientRect(),
    old = t.scale,
    next = Math.max(1, Math.min(8, old * factor));
  if (next === old) return;
  const pointX =
      (clientX ?? rect.left + rect.width / 2) - (rect.left + rect.width / 2),
    pointY =
      (clientY ?? rect.top + rect.height / 2) - (rect.top + rect.height / 2),
    ratio = next / old;
  t.x = pointX - (pointX - t.x) * ratio;
  t.y = pointY - (pointY - t.y) * ratio;
  t.scale = next;
  if (next === 1) {
    t.x = 0;
    t.y = 0;
  }
  clampViewerPan();
  applyViewerTransform();
}
function motionClock(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
function lastMotionFrameTime(p) {
  const duration = p?.motion?.duration_ms || 0,
    fps = p?.motion?.fps || 0,
    frameDuration = fps > 0 ? Math.max(1, Math.ceil(1000 / fps)) : 1;
  return Math.max(0, duration - frameDuration);
}
function motionMarkerPosition(time, lastFrame) {
  const percent = lastFrame ? (time / lastFrame) * 100 : 0,
    edgeOffset = 6 - percent * 0.12;
  return { percent, position: `calc(${percent}% + ${edgeOffset}px)` };
}
function syncMotionCoverMarkers(p) {
  const wrap = $("#motionTimelineWrap"),
    lastFrame = lastMotionFrameTime(p),
    motionCover = p?.motion?.cover_source === "motion",
    stillTime = Math.min(lastFrame, Math.max(0, p?.motion?.still_time_ms || 0)),
    coverTime = motionCover
      ? Math.min(lastFrame, Math.max(0, p.motion.cover_time_ms || 0))
      : stillTime,
    current = motionMarkerPosition(coverTime, lastFrame),
    original = motionMarkerPosition(stillTime, lastFrame);
  wrap.style.setProperty("--motion-cover-percent", `${current.percent}%`);
  wrap.style.setProperty("--motion-cover-position", current.position);
  wrap.style.setProperty("--motion-original-position", original.position);
  $("#motionCoverMarker").title = `当前封面 · ${motionClock(coverTime)}`;
  $("#motionOriginalMarker").title = `原始封面 · ${motionClock(stillTime)}`;
  $("#motionOriginalMarker").classList.toggle(
    "hidden",
    !motionCover || Math.abs(coverTime - stillTime) < 1,
  );
}
function stopViewerMotion() {
  const video = $("#viewerVideo");
  video.pause();
  video.removeAttribute("src");
  video.load();
  video.classList.add("hidden");
  $("#motionControls").classList.add("hidden");
  $("#viewer").classList.remove("viewer-motion-open");
  state.viewerMotion = { active: false, scrubbing: false };
}
function syncMotionTime() {
  const p = state.items[state.viewerIndex],
    duration = p?.motion?.duration_ms || 0,
    current = Math.round(($("#viewerVideo").currentTime || 0) * 1000),
    timeline = $("#motionTimeline"),
    play = $("#motionPlay"),
    paused = $("#viewerVideo").paused;
  timeline.value = String(Math.min(lastMotionFrameTime(p), current));
  timeline.style.setProperty(
    "--motion-progress",
    `${duration ? Math.min(100, (current / duration) * 100) : 0}%`,
  );
  $("#motionTime").textContent =
    `${motionClock(current)} / ${motionClock(duration)}`;
  play.innerHTML = paused ? MOTION_PLAY_ICON : MOTION_PAUSE_ICON;
  play.setAttribute("aria-label", paused ? "播放" : "暂停");
  play.title = paused ? "播放" : "暂停";
}
function syncMotionVolume() {
  const video = $("#viewerVideo"),
    button = $("#motionMute"),
    muted = video.muted;
  button.innerHTML = muted ? MOTION_MUTED_ICON : MOTION_SOUND_ICON;
  button.setAttribute("aria-label", muted ? "播放声音" : "静音");
  button.title = muted ? "播放声音" : "静音";
}
function toggleMotionPlayback() {
  const video = $("#viewerVideo");
  if (video.paused) {
    if (video.ended) video.currentTime = 0;
    video.play();
  } else video.pause();
}
function toggleMotionMute() {
  const video = $("#viewerVideo");
  video.muted = !video.muted;
  syncMotionVolume();
}
function motionCoverTime(p) {
  return Math.min(
    lastMotionFrameTime(p),
    Math.max(
      0,
      p.motion.cover_source === "motion"
        ? p.motion.cover_time_ms
        : p.motion.still_time_ms || 0,
    ),
  );
}
function freezeMotionAtCover(p) {
  const video = $("#viewerVideo"),
    apply = () => {
      if (
        state.items[state.viewerIndex]?.id !== p.id ||
        !state.viewerMotion.active
      )
        return;
      video.pause();
      video.currentTime = motionCoverTime(p) / 1000;
      syncMotionTime();
    };
  if (video.readyState >= 1) apply();
  else video.addEventListener("loadedmetadata", apply, { once: true });
}
async function locateMotionStillTime(p) {
  if ((p?.motion?.still_time_ms ?? -1) >= 0) return;
  try {
    const result = await json("/api/motion/locate", {
      project_id: state.project.id,
      photo_id: p.id,
    });
    p.motion.still_time_ms = result.still_time_ms;
    if (state.items[state.viewerIndex]?.id === p.id) {
      $("#motionTimeline").value = String(motionCoverTime(p));
      syncMotionCoverMarkers(p);
      freezeMotionAtCover(p);
    }
  } catch {
    p.motion.still_time_ms = 0;
  }
}
function setupMotionViewer(p) {
  const video = $("#viewerVideo"),
    lastFrame = lastMotionFrameTime(p),
    coverTime = motionCoverTime(p);
  state.viewerMotion = { active: true, scrubbing: false };
  $("#viewer").classList.add("viewer-motion-open");
  $("#motionControls").classList.remove("hidden");
  $("#motionTimeline").max = String(lastFrame);
  $("#motionTimeline").value = String(coverTime);
  syncMotionCoverMarkers(p);
  video.poster = p.photo_url;
  video.src = p.motion.video_url;
  video.classList.remove("hidden");
  $("#viewerImage").classList.add("hidden");
  resetViewerTransform();
  syncMotionTime();
  syncMotionVolume();
  freezeMotionAtCover(p);
  locateMotionStillTime(p);
}
function chooseMotionSourceWriteback() {
  const mode = state.settings.motion_cover_writeback || "ask";
  if (mode === "never") return Promise.resolve(false);
  if (mode === "always") return Promise.resolve(true);
  return new Promise((resolve) => {
    const dialog = $("#motionWritebackConfirm"),
      checkbox = $("#motionWritebackDontAsk"),
      yes = $("#motionWritebackYes"),
      no = $("#motionWritebackNo");
    let settled = false;
    checkbox.checked = false;
    yes.disabled = false;
    no.disabled = false;
    const finish = async (writeSource) => {
      if (settled) return;
      settled = true;
      yes.disabled = true;
      no.disabled = true;
      if (checkbox.checked) {
        const next = writeSource ? "always" : "never";
        try {
          const saved = await json("/api/settings", {
            motion_cover_writeback: next,
          });
          state.settings.motion_cover_writeback =
            saved.settings.motion_cover_writeback || next;
          $("#motionCoverWriteback").value =
            state.settings.motion_cover_writeback;
        } catch (error) {
          toast(`保存原图修改设置失败：${error.message}`);
        }
      }
      dialog.close();
      resolve(writeSource);
    };
    yes.onclick = () => finish(true);
    no.onclick = () => finish(false);
    dialog.oncancel = (event) => {
      event.preventDefault();
      finish(false);
    };
    dialog.showModal();
  });
}
async function saveMotionCover(source = "motion", timeMs = null) {
  const p = state.items[state.viewerIndex];
  if (!p?.motion) return;
  const button =
    source === "still" ? $("#motionResetCover") : $("#motionSetCover");
  button.disabled = true;
  try {
    const writeSource =
        source === "motion" ? await chooseMotionSourceWriteback() : false,
      requested = timeMs ?? Math.round($("#viewerVideo").currentTime * 1000),
      selectedTime =
        source === "motion"
          ? Math.min(lastMotionFrameTime(p), Math.max(0, requested))
          : 0,
      result = await json("/api/motion/cover", {
        project_id: state.project.id,
        photo_id: p.id,
        source,
        time_ms: selectedTime,
        write_source: writeSource,
      });
    state.items[state.viewerIndex] = result.photo;
    state.viewerNeedsRefresh = true;
    state.viewerDirtyIds.add(p.id);
    applyProjectCounts(result.project_counts);
    syncCardAnalysis(result.photo);
    openViewer(state.viewerIndex);
    toast(
      source === "still"
        ? "已恢复原始封面"
        : result.source_written
          ? "封面分析已更新，原图已备份并修改"
          : "封面和照片分析已更新",
    );
  } catch (error) {
    toast(`保存封面失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}
// 变体按钮上的格式标签。优先取文件名的扩展名（payload 里的 extension 是
// 小写带点的原值）；路径中没有扩展名时退回 extension 字段。注意判断必须是
// dot > slash 而不是 dot > max(dot, slash) —— 后者恒为假，标签会永远是空的。
function viewerVariantLabel(photo) {
  const path = String(photo.relative_path || ""),
    dot = path.lastIndexOf("."),
    slash = path.lastIndexOf("/");
  return (dot > slash ? path.slice(dot + 1) : photo.extension || "")
    .replace(/^\./, "")
    .toUpperCase();
}
// 同一张曝光的 RAW + JPEG 在照片库中折叠成一张卡片，所以 state.items 里只有
// 代表文件，被折叠的那一份连 id 都没有。查看器要切换格式就必须另外取回整组数据
// （每种格式各自的完整 payload），否则切过去就没有文件名、尺寸和评分可显示。
// RAW 可能还没跑出分析结果（quality_score 为 null、reason 为空），此时不能显示
// "undefined 分" 或 "0 B"，而是整段略去。p._blinkLabel 只在相似视图里赋值，
// 照片库中没有 blinks 段，因此这里的 undefined 是预期行为。
function viewerMetaText(p) {
  const parts = [],
    width = Number(p.width) || 0,
    height = Number(p.height) || 0;
  if (width && height) parts.push(`${width} × ${height}`);
  if (Number(p.size) > 0) parts.push(formatSize(p.size));
  if (Number.isFinite(p.quality_score)) parts.push(`${p.quality_score} 分`);
  if (p.reason) parts.push(String(p.reason));
  if (p._blinkLabel) parts.push("眨眼");
  if (p.motion?.error) parts.push("动态部分不可用");
  return parts.join(" · ");
}
// 变体切换控件。徽章原本是一段「CR3 + JPG」的纯文本，现在每个格式各成一个
// 按钮，中间仍以「 + 」相连：外观与文案一字不变，而每个格式都可点击切换。
// 整组只有一份照片时退化成纯文本徽章（即从前的行为），不做无用的往返。
function renderViewerVariantSwitch(p) {
  const host = $("#viewerVariantBadge"),
    items = state.viewerVariants;
  const text = variantFormatText(p, true);
  host.classList.toggle("hidden", !text && items.length < 2);
  if (items.length < 2) {
    host.textContent = text;
    host.title = text ? `关联格式：${variantFormatText(p)}` : "";
    host.removeAttribute("role");
    return;
  }
  host.setAttribute("role", "group");
  host.setAttribute("aria-label", "切换显示格式");
  host.title = `点击切换显示格式（当前共 ${items.length} 种），或按 F 键`;
  const nodes = [];
  items.forEach((item, index) => {
    if (index) nodes.push(` + `);
    const active = state.viewerVariantIndex === index || item.id === p.id;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `viewer-variant-option${active ? " active" : ""}`;
    button.textContent = viewerVariantLabel(item);
    button.dataset.variantIndex = String(index);
    button.setAttribute("aria-pressed", String(active));
    nodes.push(button);
  });
  host.replaceChildren(...nodes);
}
// 渲染「当前这一份」照片的全部查看器状态。切换格式时必须走这里：用的是被选中
// 格式自己的完整 payload，文件名/尺寸/评分/决定状态都会跟着换，而不只是换图。
function renderViewerPhoto(p) {
  const suggestion = viewerSuggestion(p),
    badge = $("#viewerBadge"),
    analysisBadge = $("#viewerAnalysisBadge"),
    img = $("#viewerImage");
  stopViewerMotion();
  resetViewerTransform();
  img.classList.remove("hidden");
  img.src = p.photo_url;
  $("#viewerName").textContent = p.relative_path.split("/").pop();
  $("#viewerMeta").textContent = viewerMetaText(p);
  if (p.media_type === "motion_photo") {
    badge.innerHTML = LIVE_PHOTO_ICON;
    badge.setAttribute("aria-label", "动态照片");
    analysisBadge.textContent = suggestion.text;
    analysisBadge.className = `viewer-badge ${suggestion.kind ? `badge-${suggestion.kind}` : "hidden"}`;
  } else {
    badge.textContent = suggestion.text;
    badge.removeAttribute("aria-label");
    analysisBadge.className = "viewer-badge hidden";
  }
  badge.className = `viewer-badge ${p.media_type === "motion_photo" ? "viewer-live-mark" : suggestion.kind ? `badge-${suggestion.kind}` : "hidden"}`;
  renderViewerVariantSwitch(p);
  updateViewerDecision(p);
  if (p.motion && !p.motion.error) setupMotionViewer(p);
}
function openViewer(i) {
  if (!state.items.length) return;
  state.viewerIndex = (i + state.items.length) % state.items.length;
  // 换一张照片就回到该组的代表文件：格式选择是「这张照片」的临时状态。
  state.viewerVariants = [];
  state.viewerVariantIndex = -1;
  const p = state.items[state.viewerIndex];
  renderViewerPhoto(p);
  $("#viewerIndex").textContent =
    `${state.viewerIndex + 1} / ${state.items.length}`;
  if (!$("#viewer").open) $("#viewer").showModal();
  loadViewerVariants(p);
}
// 按需取回整组格式。只有 variant_extensions 超过一种时才发请求，独立照片
// （绝大多数）不会为这个功能多付一次往返。失败时保持纯文本徽章，不打扰用户。
async function loadViewerVariants(p) {
  if ((p.variant_extensions || []).length < 2) return;
  const photoId = p.id;
  try {
    const result = await json(
      `/api/photo/variants?project_id=${state.project.id}&id=${photoId}`,
    );
    // 用户可能已经翻到下一张：过期响应直接丢弃，否则会把上一张的格式列表
    // 装到当前照片上。
    if (state.items[state.viewerIndex]?.id !== photoId) return;
    state.viewerVariants = result.items || [];
    // 落在被预览的那一份上，而不是默认第一项：代表文件未必是组内第一行。
    const current = state.viewerVariants.findIndex(
      (item) => item.id === photoId,
    );
    state.viewerVariantIndex = current >= 0 ? current : 0;
    renderViewerPhoto(viewerCurrentPhoto());
  } catch {
    // 失败时保持纯文本徽章。切换格式是增强功能，取不到整组数据不该打断预览。
    if (state.items[state.viewerIndex]?.id !== photoId) return;
    state.viewerVariants = [];
    state.viewerVariantIndex = -1;
    renderViewerVariantSwitch(state.items[state.viewerIndex]);
  }
}
function viewerCurrentPhoto() {
  return (
    state.viewerVariants[state.viewerVariantIndex] ||
    state.items[state.viewerIndex]
  );
}
// 在同一张曝光的各格式之间循环。组内只有一份时不做任何事，调用方据此决定
// 是否吞掉这次按键。
function cycleViewerVariant(step = 1) {
  const total = state.viewerVariants.length;
  if (total < 2) return false;
  const base = state.viewerVariantIndex < 0 ? 0 : state.viewerVariantIndex;
  const next = (base + step + total) % total;
  if (next === state.viewerVariantIndex) return false;
  state.viewerVariantIndex = next;
  renderViewerPhoto(state.viewerVariants[next]);
  return true;
}
const moveViewer = (d) => openViewer(state.viewerIndex + d);

function bindViewerEvents() {
  $("#viewerPrev").onclick = () => moveViewer(-1);
  $("#viewerNext").onclick = () => moveViewer(1);
  // 切换格式的点击入口。用事件委托，因为按钮是随整组数据一起重建的。
  $("#viewerVariantBadge").addEventListener("click", (event) => {
    const option = event.target.closest("[data-variant-index]");
    if (!option) return;
    event.preventDefault();
    const index = Number(option.dataset.variantIndex);
    if (index === state.viewerVariantIndex) return;
    state.viewerVariantIndex = index;
    renderViewerPhoto(state.viewerVariants[index]);
  });
  $("#viewerKeep").onclick = () => {
    const p = viewerCurrentPhoto();
    if (p) setDecision(p.id, "keep");
  };
  $("#viewerRemove").onclick = () => {
    const p = viewerCurrentPhoto();
    if (p) setDecision(p.id, "remove");
  };
  $("#viewer").addEventListener("close", () => {
    stopViewerMotion();
    syncViewerDecisions().catch((error) => toast(error.message));
  });
  ["timeupdate", "play", "pause", "ended"].forEach((name) =>
    $("#viewerVideo").addEventListener(name, syncMotionTime),
  );
  $("#viewerVideo").addEventListener("volumechange", syncMotionVolume);
  $("#motionMute").onclick = toggleMotionMute;
  $("#motionPlay").onclick = toggleMotionPlayback;
  $("#motionTimeline").oninput = (event) => {
    $("#viewerVideo").pause();
    $("#viewerVideo").currentTime = Number(event.target.value) / 1000;
    syncMotionTime();
  };
  $("#motionSetCover").onclick = () => saveMotionCover("motion");
  $("#motionResetCover").onclick = () => saveMotionCover("still", 0);
  $("#viewerImage").addEventListener("click", (event) => {
    if (state.viewerTransform.suppressClick) return;
    clearTimeout(state.viewerClickTimer);
    state.viewerClickTimer = setTimeout(
      () => zoomViewer(1.5, event.clientX, event.clientY),
      220,
    );
  });
  $("#viewerImage").addEventListener("dblclick", (event) => {
    event.preventDefault();
    clearTimeout(state.viewerClickTimer);
    resetViewerTransform();
  });
  $("#viewerVideo").addEventListener("click", () => {
    if (!state.viewerTransform.suppressClick) toggleMotionPlayback();
  });
  [$("#viewerImage"), $("#viewerVideo")].forEach((media) => {
    media.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        zoomViewer(
          event.deltaY < 0 ? 1.18 : 1 / 1.18,
          event.clientX,
          event.clientY,
        );
      },
      { passive: false },
    );
    media.addEventListener("mousedown", (event) => {
      if (event.button !== 0 || state.viewerTransform.scale <= 1) return;
      event.preventDefault();
      const transform = state.viewerTransform;
      Object.assign(transform, {
        dragging: true,
        moved: false,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startX: transform.x,
        startY: transform.y,
      });
      applyViewerTransform();
    });
  });
  window.addEventListener("mousemove", (event) => {
    const transform = state.viewerTransform;
    if (!transform.dragging) return;
    const dx = event.clientX - transform.startClientX,
      dy = event.clientY - transform.startClientY;
    if (Math.abs(dx) + Math.abs(dy) > 3) transform.moved = true;
    transform.x = transform.startX + dx;
    transform.y = transform.startY + dy;
    clampViewerPan();
    applyViewerTransform();
  });
  window.addEventListener("mouseup", () => {
    const transform = state.viewerTransform;
    if (!transform.dragging) return;
    transform.dragging = false;
    if (transform.moved) {
      transform.suppressClick = true;
      setTimeout(() => (transform.suppressClick = false), 0);
    }
    applyViewerTransform();
  });
}
