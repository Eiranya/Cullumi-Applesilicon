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
// 「1:1」在此处的确切含义：让一个**源像素**正好占一个 **CSS 像素**，浏览器因此
// 不做任何插值。CSS `transform: scale()` 是在已解码位图之上重采样的，所以只有把
// 位图铺到与源像素一一对应时才谈得上原始像素。
//
// 位图本身的像素尺寸由 naturalWidth 决定，而 CSS 的 max-width/max-height 会先把它
// 缩小到视口内。于是 naturalWidth(=源像素) 与 offsetWidth(=缩放前的布局像素) 的
// 比值就是「1:1 所需的放大倍数」：
//
//   offsetWidth * scale == naturalWidth   =>   scale = naturalWidth / offsetWidth
//
// 这也解释了为什么 1:1 通常**大于** 1：一张 4000px 的照片在 800px 的舞台上，1:1
// 需要放大到 5 倍——那个倍率下每个屏幕像素正好对应一个源像素，浏览器无需插值。
//
// 若位图比视口小（offsetWidth >= naturalWidth），1:1 就是 scale=1。DPR>1 的屏幕上
// CSS 像素本身就小于设备像素，这是显示器的物理约束，与「是否插值」无关，故不参与
// 该比值。
function viewerOneToOneScale() {
  const target = viewerTransformTarget();
  const natural = target.naturalWidth || 0,
    laid = target.offsetWidth || 0;
  if (!natural || !laid) return 1;
  return Math.max(1, natural / laid);
}
// 当前是否**正好**处在 1:1。
//
// 这里必须用「等于」而不是「小于等于」。小于 1:1 点意味着位图被缩着放（每个 CSS
// 像素塞了多于一个源像素），那是降采样而非 1:1；把它一并报成「已 1:1」会让状态
// 提示说谎——一张4000px 的图缩到 800px 宽、放到 3 倍，听起来像 1:1，其实每个屏幕
// 像素里挤了 1.67 个源像素。只有恰好落在该点（±0.1% 容忍浮点误差）才叫 1:1。
//
// 「有没有插值」是另一个问题，由 viewerIsInterpolating 回答；两者必须分开。
function viewerIsOneToOne() {
  const oneToOne = viewerOneToOneScale(),
    scale = state.viewerTransform.scale;
  if (!viewerHasPixels()) return false;
  return Math.abs(scale - oneToOne) <= oneToOne * 0.001;
}
// 位图是否已经解码出尺寸。没有它时不能对「是否 1:1」下结论——那会让提示在图片
// 尚未加载时先闪一次错误的「已插值 100%」。
function viewerHasPixels() {
  const target = viewerTransformTarget();
  return Boolean(target.naturalWidth && target.offsetWidth);
}
// 是否正在放大超过 1:1，也就是浏览器在插值。
function viewerIsInterpolating() {
  if (!viewerHasPixels()) return false;
  return state.viewerTransform.scale > viewerOneToOneScale() * 1.001;
}
// 状态提示。必须如实告诉用户「看到的不是原始像素」，因为这是用户投诉的原点：
// 放大后发糊不是原图不清晰，而是浏览器在插值一张已经缩小的位图。
function renderViewerScaleHint() {
  const hint = $("#viewerScaleHint"),
    p = state.viewerMotion.active ? null : state.items[state.viewerIndex];
  if (!p || state.viewerMotion.active || !viewerHasPixels()) {
    hint.textContent = "";
    hint.classList.remove("viewer-scale-hint-exact");
    return;
  }
  const width = Number(p.width) || 0,
    height = Number(p.height) || 0;
  if (!width || !height) {
    hint.textContent = "";
    hint.classList.remove("viewer-scale-hint-exact");
    return;
  }
  if (viewerIsOneToOne()) {
    hint.textContent = `已 1:1（原始分辨率 ${width} × ${height}）`;
    hint.classList.add("viewer-scale-hint-exact");
    return;
  }
  hint.classList.remove("viewer-scale-hint-exact");
  if (viewerIsInterpolating()) {
    hint.textContent = `已插值 ${Math.round(
      state.viewerTransform.scale * 100,
    )}%，细节非原始像素`;
    return;
  }
  // 未达 1:1：整幅图缩在屏幕里，每个屏幕像素含多于一个源像素。没有插值，但也没有
  // 放大到能逐像素判读的程度——说清楚比只报一个倍率有用。
  hint.textContent = `整幅显示 · 原始分辨率 ${width} × ${height}`;
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
  renderViewerScaleHint();
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
// 缩放上限必须够到「原始像素」这一档，否则 1:1 按钮在常规照片上会撞上限而停在
// 一个仍然插值的倍率上——那正是这个功能要解决的问题，不能自己复现。
const VIEWER_MAX_SCALE = 32;
function zoomViewer(factor, clientX, clientY) {
  const t = state.viewerTransform,
    figure = viewerTransformTarget().parentElement,
    rect = figure.getBoundingClientRect(),
    old = t.scale,
    next = Math.max(1, Math.min(VIEWER_MAX_SCALE, old * factor));
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
// 一键跳到真实 1:1。位图比视口小的时候 scale 就是 1（本来就没有插值）；
// 比视口大时按 naturalWidth/offsetWidth 放大到位图与源像素一一对应。
function toggleViewerOneToOne() {
  const t = state.viewerTransform;
  if (viewerIsOneToOne()) {
    resetViewerTransform();
    return;
  }
  Object.assign(t, {
    scale: viewerOneToOneScale(),
    x: 0,
    y: 0,
    dragging: false,
    moved: false,
    suppressClick: false,
  });
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
// 变体徽章：纯文字、不可点击。
//
// 照片库把同一张曝光的 RAW + JPEG 折叠成一张卡片，state.items 里只有代表文件
// （`_representative_sort_key` 永远挑可解码、分辨率更高的那一份）。RAW 可能还没
// 跑出分析结果（quality_score 为 null、reason 为空），此时不能显示 "undefined 分"
// 或 "0 B"，而是整段略去。p._blinkLabel 只在相似视图里赋值，照片库中没有 blinks
// 段，因此这里的 undefined 是预期行为。
//
// 用户试用后撤回了「点开预览可在 RAW/JPEG 间切换」：RAW 无法在浏览器里解码，
// 切过去只会得到更差或空白的画面。因此徽章退化为纯提示，标题如实说明本组含 RAW
// 而只预览可解码格式；可点击按钮、.viewer-variant-option 样式与 F 键循环均已移除。
function renderViewerVariantBadge(p) {
  const host = $("#viewerVariantBadge"),
    text = variantFormatText(p, true);
  host.classList.toggle("hidden", !text);
  host.textContent = text;
  host.title = text
    ? `本组包含 ${variantFormatText(p)}；Cullumi 只预览可解码的格式，不提供 RAW 预览`
    : "";
}
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
// 渲染当前照片的全部查看器状态。
function renderViewerPhoto(p) {
  const suggestion = viewerSuggestion(p),
    badge = $("#viewerBadge"),
    analysisBadge = $("#viewerAnalysisBadge"),
    img = $("#viewerImage");
  stopViewerMotion();
  resetViewerTransform();
  img.classList.remove("hidden");
  // 首屏先要预览图（服务端按 w= 缓存的 JPEG），原图留给「查看原图」按需拉取。
  // 动态照片的封面本来就是一个提取出来的 JPEG，用原图 URL 即可。
  img.src =
    p.media_type === "motion_photo" || !p.preview_url
      ? p.photo_url
      : p.preview_url;
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
  renderViewerVariantBadge(p);
  updateViewerDecision(p);
  syncViewerOriginalState();
  if (p.motion && !p.motion.error) setupMotionViewer(p);
}
function openViewer(i) {
  if (!state.items.length) return;
  state.viewerIndex = (i + state.items.length) % state.items.length;
  const p = state.items[state.viewerIndex];
  renderViewerPhoto(p);
  $("#viewerIndex").textContent =
    `${state.viewerIndex + 1} / ${state.items.length}`;
  if (!$("#viewer").open) $("#viewer").showModal();
  // 位图解码完成后才知道 naturalWidth / offsetWidth，1:1 按钮的可用状态要等这一刻。
  const img = $("#viewerImage");
  if (img.complete && img.naturalWidth) syncViewerOriginalState();
  else img.addEventListener("load", syncViewerOriginalState, { once: true });
}
// 「已载入原图」= <img> 当前挂的就是 photo_url（而非 preview_url）。
//
// 判定刻意不看尺寸：尺寸法在「原图恰好被缩到预览图大小」时会误判为已载入，
// 于是 1:1 按钮声称看的是原始像素，实际却在放大一张预览图——正是要修的毛病。
function viewerShowingOriginal() {
  const p = state.items[state.viewerIndex],
    img = $("#viewerImage");
  if (!p) return false;
  const current = (img.getAttribute("src") || "").split("&w=")[0];
  return Boolean(p.photo_url) && current === p.photo_url;
}
// 原图按需加载的状态机。
//
// 为什么默认不载：原图实测中位 19.5MB、最大 24MB，逐张下发给「看一眼构图」毫无
// 必要（preview_url 的 JPEG 约 1MB）。但 1:1 判细节必须要有真像素，否则「放大后
// 发糊」会被误当成照片本身不清晰。
//
// 失败处理：原图取不到时**不**清空画面——保留已经显示的预览图，只把按钮标成不可用
// 并说明原因。降级必须是无声的视觉损失，而不是一个坏掉的预览器。
function syncViewerOriginalState() {
  const p = state.items[state.viewerIndex],
    button = $("#viewerOriginal"),
    hint = $("#viewerOriginalHint");
  if (!p || state.viewerMotion.active) {
    button.disabled = true;
    hint.textContent = "";
    return;
  }
  button.disabled = false;
  if (viewerShowingOriginal()) {
    button.classList.add("viewer-original-loaded");
    button.textContent = "原图";
    hint.textContent = state.viewerOriginalFailed.has(p.id)
      ? "原图不可用，显示的是预览图"
      : "";
  } else {
    button.classList.remove("viewer-original-loaded");
    button.textContent = "查看原图";
    hint.textContent = state.viewerOriginalFailed.has(p.id)
      ? "原图载入失败，已退回预览图"
      : "";
  }
  renderViewerScaleHint();
}
// 取原图。用一个独立的 Image 预加载，成功后才替换 <img> 的 src：直接改 src 会先
// 把当前画面清空，加载失败就只剩一个破图。预览图撑到原图到达为止，视觉上没有空洞。
function loadViewerOriginal() {
  const p = state.items[state.viewerIndex];
  if (!p || state.viewerMotion.active || viewerShowingOriginal()) return;
  const photoId = p.id,
    indicator = $("#viewerLoading"),
    probe = new Image();
  indicator.classList.remove("hidden");
  probe.onload = () => {
    // 用户可能已经翻到下一张：过期响应直接丢弃，否则会把上一张的原图装到当前照片上。
    if (state.items[state.viewerIndex]?.id !== photoId) {
      indicator.classList.add("hidden");
      return;
    }
    const img = $("#viewerImage");
    img.src = p.photo_url;
    indicator.classList.add("hidden");
    syncViewerOriginalState();
    img.addEventListener(
      "load",
      () => {
        resetViewerTransform();
        // 原图更大，之前那个「适应窗口」的倍率现在对应真实的 1:1，直接跳过去，
        // 省掉用户再点一次 1:1。
        toggleViewerOneToOne();
        syncViewerOriginalState();
      },
      { once: true },
    );
    img.addEventListener(
      "error",
      () => {
        state.viewerOriginalFailed.add(photoId);
        // 退回已经显示着的预览图，而不是留下一张坏图。
        img.src = p.preview_url || p.photo_url;
        resetViewerTransform();
        indicator.classList.add("hidden");
        toast("原图载入失败，已退回预览图");
        syncViewerOriginalState();
      },
      { once: true },
    );
  };
  probe.onerror = () => {
    indicator.classList.add("hidden");
    if (state.items[state.viewerIndex]?.id !== photoId) return;
    state.viewerOriginalFailed.add(photoId);
    syncViewerOriginalState();
    toast("原图载入失败，已保留预览图");
  };
  probe.src = p.photo_url;
}
const moveViewer = (d) => openViewer(state.viewerIndex + d);

function bindViewerEvents() {
  $("#viewerPrev").onclick = () => moveViewer(-1);
  $("#viewerNext").onclick = () => moveViewer(1);
  $("#viewerKeep").onclick = () => {
    const p = state.items[state.viewerIndex];
    if (p) setDecision(p.id, "keep");
  };
  $("#viewerRemove").onclick = () => {
    const p = state.items[state.viewerIndex];
    if (p) setDecision(p.id, "remove");
  };
  $("#viewerOneToOne").onclick = toggleViewerOneToOne;
  $("#viewerFit").onclick = resetViewerTransform;
  $("#viewerOriginal").onclick = loadViewerOriginal;
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
