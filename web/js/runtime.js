const TOKEN = window.APP_TOKEN;
const DECISION_VALUES = ["undecided", "keep", "remove"],
  AI_VALUES = ["remove", "review", "no_suggestion"],
  FORMAT_VALUES = ["raw", "jpeg", "heif", "png", "other"],
  FORMAT_LABELS = {
    raw: "RAW",
    jpeg: "JPEG",
    heif: "HEIF",
    png: "PNG",
    other: "其他",
  },
  LIBRARY_SORT_VALUES = ["suggestion", "filename", "size", "taken"],
  LIBRARY_PAGE_SIZE = 120,
  SIMILAR_GROUP_PAGE_SIZE = 120;
const state = {
  project: null,
  view: "library",
  activeNav: "library",
  items: [],
  profiles: [],
  settings: {},
  recentProjects: [],
  recentQuery: "",
  recentMenuId: "",
  recentGeneration: 0,
  viewerIndex: 0,
  viewerNeedsRefresh: false,
  viewerDirtyIds: new Set(),
  editor: null,
  poll: 0,
  lastScan: null,
  theme: localStorage.getItem("Cullumi-theme") || "day",
  filters: {
    decisions: new Set(DECISION_VALUES),
    ai: new Set(AI_VALUES),
    formats: new Set(),
  },
  librarySort: "suggestion",
  librarySortDirection: "asc",
  library: { offset: 0, total: 0, done: false, loading: false, generation: 0 },
  similar: {
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
  },
  viewerTransform: {
    scale: 1,
    x: 0,
    y: 0,
    dragging: false,
    moved: false,
    suppressClick: false,
  },
  viewerMotion: { active: false, scrubbing: false },
  viewerClickTimer: null,
  // Which display rendition is currently mounted on #viewerImage, as an index
  // into viewer.js's VIEWER_TIER_WIDTHS (length == the original file). Kept in
  // the shared view state because it is what the scale hint reports on: the
  // hint must be able to say "still waiting for a sharper source" honestly.
  viewerTier: 0,
  // Tier index currently being fetched, or null when idle. Non-null also drives
  // the "细节加载中" state, so it must be set before the request goes out and
  // cleared on every exit path (success, stale, failure).
  viewerTierPending: null,
  // Set only by the explicit "view original" button, so that the swap jumps to
  // 1:1 on arrival. Automatic swaps must NOT reset the user's zoom -- that would
  // interrupt the very inspection the sharper source was fetched for.
  viewerTierAutoOneToOne: false,
  // Debounce handle for the tier check. Zoom is a high-frequency event; without
  // this a single scroll gesture would fire dozens of decode-and-re-encode
  // requests against a NAS.
  viewerTierTimer: null,
  // Debounce for the viewer diagnostic posts. Same rationale as the tier timer:
  // a drag fires applyViewerTransform continuously and each report would be a
  // round trip for numbers that only mean something once the gesture settles.
  viewerDiagTimer: null,
  // Photo ids whose original file failed to load. Kept per id rather than as a
  // single flag so moving to another photo and back does not re-attempt a
  // download that already failed, while the next photo still gets a fresh try.
  viewerOriginalFailed: new Set(),
  updateChecked: false,
  updateChecking: false,
};
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const CONFIRM_ACTION_CLASSES = ["danger", "primary", "confirm-accept-action"];
function prepareConfirmAction(className = "danger") {
  const button = $("#confirmOk");
  button.disabled = false;
  button.classList.remove(...CONFIRM_ACTION_CLASSES);
  button.classList.add(className);
  return button;
}
const api = async (path, body) => {
  const opts =
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-App-Token": TOKEN },
          body: JSON.stringify(body),
        };
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(
    path + sep + "token=" + encodeURIComponent(TOKEN),
    opts,
  );
  if (!res.ok) {
    let e = {};
    try {
      e = await res.json();
    } catch {}
    throw Error(e.error || `请求失败 (${res.status})`);
  }
  return res;
};
const json = async (p, b) => await (await api(p, b)).json();
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let toastTimer;
const toast = (m) => {
  const t = $("#toast"),
    dialogs = $$("dialog[open]"),
    host = dialogs.at(-1) || document.body;
  if (t.parentElement !== host) host.appendChild(t);
  t.textContent = m;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2400);
};
const formatSize = (n) =>
  n < 1024
    ? `${n} B`
    : n < 1048576
      ? `${(n / 1024).toFixed(1)} KB`
      : `${(n / 1048576).toFixed(1)} MB`;
const stageName = {
  starting: "准备扫描",
  discovering: "正在发现照片",
  analyzing: "正在解码与分析",
  hashing: "正在确认完全重复",
  grouping: "正在建立相似组",
  blink_detection: "正在检测眨眼",
  complete: "扫描完成",
  cancelled: "扫描已取消",
  error: "扫描出错",
};
function applyTheme(theme, persist = false) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("Cullumi-theme", theme);
  const night = theme === "night",
    button = $("#themeBtn");
  button.title = night ? "切换日间模式" : "切换夜间模式";
  button.setAttribute("aria-label", button.title);
  if (persist)
    json("/api/settings", { theme }).catch((e) =>
      toast(`主题保存失败：${e.message}`),
    );
}
applyTheme(state.theme);
