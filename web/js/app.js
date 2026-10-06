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
  // F 切换同一张曝光的显示格式。不占用 W/S/A/D 与方向键：那些键在查看器里
  // 已有含义（保留/移除/上一张/下一张），格式切换是另一个维度。组内只有一份
  // 时 cycleViewerVariant 返回 false，此时不吞按键，交给浏览器默认行为。
  if (key === "f") {
    if (cycleViewerVariant(1)) event.preventDefault();
    return;
  }
  if (["arrowleft", "a"].includes(key)) moveViewer(-1);
  else if (["arrowright", "d"].includes(key)) moveViewer(1);
  else if (["arrowup", "w"].includes(key)) {
    const p = viewerCurrentPhoto();
    if (p) setDecision(p.id, "keep");
  } else if (["arrowdown", "s"].includes(key)) {
    const p = viewerCurrentPhoto();
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
