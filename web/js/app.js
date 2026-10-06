function handleGlobalKeydown(event) {
  if (event.key === "Escape") {
    closeRecentMenu();
    if ($$(".multi-filter-panel:not(.hidden)").length) {
      closeFilterMenus();
      event.preventDefault();
      return;
    }
    if ($$("dialog[open]").length) return;
    if (state.view === "similar" && state.similar.selectedId) {
      event.preventDefault();
      closeSimilarDetail();
    }
    return;
  }
  if (!$("#viewer").open) return;
  if (event.code === "Space" && state.viewerMotion.active) {
    event.preventDefault();
    if (!event.repeat) toggleMotionPlayback();
    return;
  }
  if (event.target.matches("input[type=range]")) return;
  const key = event.key.toLowerCase();
  const photo = () => state.items[state.viewerIndex];
  // 缩放快捷键。不占用 W/S/A/D 与方向键：那些键在查看器里已有含义
  // （保留/移除/上一张/下一张），缩放是另一个维度。
  //
  // F 此前是「切换显示格式」的循环键，该功能已撤回，这里改为「载入原图」——
  // 同一个物理键现在服务于「看清细节」这个仍然存在的需求。
  //
  // 1 / 0 沿用看图软件的惯例（1 = 原始像素，0 = 适应窗口），且与既有绑定零冲突：
  // 查看器此前没有占用任何数字键。
  if (key === "f") {
    loadViewerOriginal();
    event.preventDefault();
    return;
  }
  if (key === "1") {
    toggleViewerOneToOne();
    event.preventDefault();
    return;
  }
  if (key === "0") {
    resetViewerTransform();
    event.preventDefault();
    return;
  }
  if (["arrowleft", "a"].includes(key)) moveViewer(-1);
  else if (["arrowright", "d"].includes(key)) moveViewer(1);
  else if (["arrowup", "w"].includes(key)) {
    const p = photo();
    if (p) setDecision(p.id, "keep");
  } else if (["arrowdown", "s"].includes(key)) {
    const p = photo();
    if (p) setDecision(p.id, "remove");
  } else return;
  event.preventDefault();
}

function bindGlobalEvents() {
  $("#githubBtn").onclick = async () => {
    try {
      await json("/api/open-github", {});
    } catch (error) {
      toast(error.message);
    }
  };
  $("#themeBtn").onclick = () =>
    applyTheme(state.theme === "night" ? "day" : "night", true);
  $$("[data-close]").forEach((button) => (button.onclick = closeDialogs));
  $$("dialog[data-backdrop-close]").forEach((dialog) =>
    dialog.addEventListener("click", closeNoticeOnBackdrop),
  );
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#recentMenu")) closeRecentMenu();
    if (!event.target.closest(".multi-filter")) closeFilterMenus();
  });
  document.addEventListener("keydown", handleGlobalKeydown);
}

function startApplication() {
  initializeGalleryTools();
  bindSessionEvents();
  bindSimilarEvents();
  bindSettingsEvents();
  bindGalleryEvents();
  bindGlobalEvents();
  bindDropAffordance();
  boot().catch((error) => toast(error.message));
}

startApplication();
