// Processing status of a similarity group. Derived server-side from the
// decisions of every member, so it always matches what the group actually
// contains -- a group's membership changes when a rescan merges or splits it,
// and a stored copy would go stale at that moment.
const GROUP_STATUS_META = {
  untouched: { label: "未处理", hint: "这一组还没有做出任何决定" },
  partial: { label: "部分处理", hint: "这一组只有部分照片做出了决定" },
  done: { label: "已处理", hint: "这一组每张照片都已做出决定" },
};

function similarGroupStatusMeta(group) {
  // An unrecognised or missing status counts as untouched, so a group payload
  // from an older response still renders instead of showing "undefined".
  const key = GROUP_STATUS_META[group?.status] ? group.status : "untouched";
  const meta = GROUP_STATUS_META[key];
  const decided = Number.isFinite(group?.decided_count)
    ? group.decided_count
    : 0;
  return {
    key,
    label: meta.label,
    hint: `${meta.hint}（${decided}/${group?.count ?? 0}）`,
  };
}

function similarStatusFilterLabel(value) {
  return (
    {
      all: "全部状态",
      untouched: "未处理",
      partial: "部分处理",
      done: "已处理",
    }[value] || "全部状态"
  );
}

function syncSimilarStatusHint(total) {
  const hint = $("#similarStatusHint");
  if (!hint) return;
  const filter = state.similar.statusFilter;
  if (filter === "all") {
    hint.textContent = "";
    return;
  }
  const label = similarStatusFilterLabel(filter);
  hint.textContent = total ? `${total} 组「${label}」` : `没有「${label}」的相似组`;
}

// Caption under a folder in the sidebar.
//
// `count` is the number of CARDS the group shows and `capture_count` the
// number of FILES behind them. They differ only for a "similar" group, whose
// members are folded to one card per exposure -- so a group can hold several
// files and still show a single photo. "1 张相似照片" would then be a claim
// about similarity that isn't there: nothing is being compared to anything,
// the files are one shot. Those groups are captioned by what they actually
// are, and `capture_count` is what makes the line informative rather than
// just different.
//
// Exact groups keep their own wording: byte-identical copies really are N
// photos, and folding them would be the bug, not the fix.
function similarFolderCaption(group) {
  if (group.kind === "exact") return "完全重复";
  const count = Number(group.count) || 0;
  if (count !== 1) return `${group.count} 张相似照片`;
  const files = Number(group.capture_count) || 0;
  // A one-member group cannot satisfy the two-member minimum that creates a
  // group at all, so `files` below 2 means the payload is not what this
  // branch assumes. Fall back to the plain wording instead of printing
  // "同一张照片的 1 个文件".
  return files > 1 ? `同一张照片的 ${files} 个文件` : "1 张相似照片";
}

function similarFolder(group, compact = false) {
  const coverImages = group.covers
    .map(
      (photo, index) =>
        `<img class="folder-cover cover-${index}" loading="lazy" src="${photo.thumb_url}" alt="">`,
    )
    .reverse()
    .join("");
  const name = group.recommended.relative_path.split("/").pop();
  const status = similarGroupStatusMeta(group);
  return `<button class="similar-folder ${compact ? "compact" : ""} ${group.id === state.similar.selectedId ? "active" : ""}" data-similar-group="${group.id}"><span class="folder-stack">${coverImages}<i>${group.count} 张</i><em class="folder-status" data-status="${status.key}" title="${esc(status.hint)}">${status.label}</em></span><span class="folder-caption"><b title="${esc(group.recommended.relative_path)}">${esc(name)}</b><small>${similarFolderCaption(group)}</small></span></button>`;
}
function renderSimilarFolders() {
  const selected = !!state.similar.selectedId;
  $("#similarFolders").innerHTML = state.similar.groups
    .map((group) => similarFolder(group, selected))
    .join("");
  $("#similarFolders").classList.toggle(
    "compact",
    selected && state.similar.mode === "side",
  );
  $("#similarFolderPane").classList.toggle(
    "hidden",
    selected && state.similar.mode === "expanded",
  );
}
function similarFormatValues() {
  return state.similar.formatCategories.map((item) => item.id);
}
function similarPhotoFormat(photo) {
  return FORMAT_VALUES.includes(photo.format_category)
    ? photo.format_category
    : "other";
}
function similarFormatCategories(members) {
  const counts = new Map();
  members.forEach((photo) => {
    const category = similarPhotoFormat(photo);
    counts.set(category, (counts.get(category) || 0) + 1);
  });
  return FORMAT_VALUES.filter((category) => counts.has(category)).map(
    (category) => ({
      id: category,
      label: FORMAT_LABELS[category],
      count: counts.get(category),
    }),
  );
}
function similarDecisionValue(photo) {
  return photo.decision || "undecided";
}
function similarAiValue(photo) {
  return ["remove", "review"].includes(photo.suggestion)
    ? photo.suggestion
    : "no_suggestion";
}
function similarRecommendationRank(photo) {
  return photo._viewerKind === "recommended" ? 1 : 0;
}
function similarQualityScore(photo) {
  const score = Number(photo.quality_score);
  return photo.quality_score !== null && photo.quality_score !== "" &&
    Number.isFinite(score)
    ? score
    : -1;
}
function compareSimilarPhotos(left, right) {
  const direction = state.similar.sortDirection === "desc" ? -1 : 1;
  let result = 0;
  if (state.similar.sort === "suggestion") {
    result =
      similarRecommendationRank(left) - similarRecommendationRank(right);
    if (!result)
      result = similarQualityScore(left) - similarQualityScore(right);
  } else if (state.similar.sort === "filename") {
    const leftName = left.relative_path.split("/").pop() || "",
      rightName = right.relative_path.split("/").pop() || "";
    result = leftName.localeCompare(rightName, undefined, {
      numeric: true,
      sensitivity: "base",
    });
  } else if (state.similar.sort === "size") {
    result = (Number(left.size) || 0) - (Number(right.size) || 0);
  } else if (state.similar.sort === "taken") {
    const leftTaken = String(left.taken || ""),
      rightTaken = String(right.taken || "");
    if (!leftTaken || !rightTaken) {
      if (leftTaken !== rightTaken) return leftTaken ? -1 : 1;
    } else result = leftTaken.localeCompare(rightTaken);
  }
  if (result) return result * direction;
  const pathResult = left.relative_path.localeCompare(
    right.relative_path,
    undefined,
    { numeric: true, sensitivity: "base" },
  );
  const fallback = pathResult || left.id - right.id;
  return state.similar.sort === "suggestion" ? fallback * direction : fallback;
}
function syncSimilarControls() {
  similarTools?.sync();
}
function applySimilarMode() {
  const selected = !!state.similar.selectedId,
    expanded = state.similar.mode === "expanded",
    visible = state.view === "similar" && selected;
  $("#similarBrowser").classList.toggle("detail-open", selected);
  $("#similarBrowser").classList.toggle(
    "detail-expanded",
    selected && expanded,
  );
  $("#similarDetail").classList.toggle("hidden", !selected);
  $("#similarViewActions").classList.toggle("hidden", !visible);
  $("#similarCollapseBtn").classList.toggle("hidden", expanded);
  $("#similarExpandBtn").classList.toggle("hidden", expanded);
  $("#similarBackBtn").classList.toggle("hidden", !expanded);
  $("#similarCloseBtn").classList.toggle("hidden", !expanded);
  $("#similarFolderPane").classList.toggle("hidden", selected && expanded);
  syncSimilarControls();
  document.body.classList.toggle(
    "similar-detail-open",
    state.view === "similar" && selected,
  );
  document.body.classList.toggle(
    "similar-side-open",
    state.view === "similar" && selected && !expanded,
  );
}
function blinkStatusLabel(photo, recommended, kind) {
  const profile = state.profiles.find(
    (item) => item.id === state.project?.profile_id,
  );
  if (
    recommended ||
    kind !== "similar" ||
    profile?.similarity?.blink?.enabled === false
  )
    return "";
  if (
    photo.blink_status !== "closed" ||
    (photo.blink_closed_face_count || 0) < 1
  )
    return "";
  const faceCount = Math.max(0, Number(photo.blink_face_count) || 0);
  const uncertainCount = Math.max(
    0,
    Number(photo.blink_uncertain_face_count) || 0,
  );
  if (!faceCount) return "";
  const minimum = Number(
    profile?.similarity?.blink?.reliable_coverage_min ?? 0.8,
  );
  return (faceCount - uncertainCount) / faceCount >= minimum ? "眨眼" : "";
}
function updateSimilarGroupSentinel() {
  const sentinel = $("#similarGroupSentinel");
  sentinel.textContent = state.similar.done
    ? ""
    : state.similar.loading
      ? "正在加载更多相似组…"
      : "继续向下滚动加载";
  sentinel.classList.toggle("hidden", state.similar.done);
}
async function loadSimilarView(reset = false) {
  if (!state.project || state.view !== "similar") return;
  if (reset) {
    state.similar.offset = 0;
    state.similar.total = 0;
    state.similar.done = false;
    state.similar.loading = false;
    state.similar.generation += 1;
    state.similar.groups = [];
    renderSimilarFolders();
  }
  if (state.similar.loading || state.similar.done) return;
  const generation = state.similar.generation,
    params = new URLSearchParams({
      project_id: state.project.id,
      search: state.similar.listSearch,
      status: state.similar.statusFilter,
      limit: String(SIMILAR_GROUP_PAGE_SIZE),
      offset: String(state.similar.offset),
    });
  state.similar.loading = true;
  updateSimilarGroupSentinel();
  try {
    const list = await json(`/api/similar-groups?${params.toString()}`);
    if (generation !== state.similar.generation || state.view !== "similar")
      return;
    const known = new Set(state.similar.groups.map((group) => group.id));
    state.similar.groups.push(
      ...list.items.filter((group) => !known.has(group.id)),
    );
    state.similar.offset += list.items.length;
    state.similar.total = list.total;
    state.similar.done =
      state.similar.offset >= list.total || !list.items.length;
    renderSimilarFolders();
    applySimilarMode();
    if (state.similar.selectedId && reset) {
      try {
        await loadSimilarGroupMembers();
      } catch (error) {
        closeSimilarDetail(false);
        toast("原相似组已发生变化，已返回相似组列表");
      }
    } else if (!state.similar.selectedId) {
      state.items = [];
      // The subtitle names the scope so a filtered list never reads as the
      // whole library: "3 组未处理" rather than "3 组相似照片".
      const scope =
        state.similar.statusFilter === "all"
          ? "相似照片"
          : similarStatusFilterLabel(state.similar.statusFilter);
      $("#viewSubtitle").textContent =
        `${list.total} 组${scope}${state.similar.groups.some((group) => group.face_safe) ? " · 人物照片请检查表情" : ""}`;
      $("#empty").classList.toggle("hidden", !!list.total);
      if (!list.total && state.similar.statusFilter !== "all") {
        // A filter that matches nothing must not reuse the "scan first" copy:
        // the library does have groups, this status just has none of them.
        const label = similarStatusFilterLabel(state.similar.statusFilter);
        $("#emptyTitle").textContent = "没有符合筛选的相似组";
        $("#emptyText").textContent =
          `当前筛选为「${label}」。换一个状态，或选回「全部状态」。`;
      } else {
        $("#emptyTitle").textContent = "这里还没有内容";
        $("#emptyText").textContent = "扫描完成后会显示结果。";
      }
    }
    syncSimilarStatusHint(list.total);
  } finally {
    if (generation === state.similar.generation) {
      state.similar.loading = false;
      updateSimilarGroupSentinel();
    }
  }
}
async function loadSimilarGroupMembers() {
  const groupId = state.similar.selectedId;
  const detail = await json(
    `/api/similar-group?project_id=${state.project.id}&group_id=${encodeURIComponent(groupId)}`,
  );
  if (groupId !== state.similar.selectedId) return;
  const decorated = detail.members.map((photo) => {
    const recommended =
      (photo.similarity_source_id || photo.id) === detail.recommended_id;
    const blinkLabel = blinkStatusLabel(photo, recommended, detail.kind);
    return {
      ...photo,
      _viewerBadge: recommended ? "推荐保留" : "可考虑移除",
      _viewerKind: recommended ? "recommended" : "candidate-remove",
      _blinkLabel: blinkLabel,
    };
  });
  const previousValues = similarFormatValues(),
    selectedAll =
      !!previousValues.length && setEquals(state.similar.formats, previousValues);
  state.similar.detail = { ...detail, members: decorated };
  state.similar.formatCategories = similarFormatCategories(decorated);
  const availableValues = similarFormatValues();
  state.similar.formats =
    !previousValues.length || selectedAll
      ? new Set(availableValues)
      : new Set(
          [...state.similar.formats].filter((value) =>
            availableValues.includes(value),
          ),
        );
  renderSimilarGroupMembers();
  renderSimilarFolders();
  applySimilarMode();
}
function renderSimilarGroupMembers() {
  const detail = state.similar.detail;
  if (!detail || detail.id !== state.similar.selectedId) return;
  const query = state.similar.memberSearch.trim().toLocaleLowerCase(),
    decorated = detail.members
      .filter(
        (photo) =>
          state.similar.decisions.has(similarDecisionValue(photo)) &&
          state.similar.ai.has(similarAiValue(photo)) &&
          state.similar.formats.has(similarPhotoFormat(photo)) &&
          (!query || photo.relative_path.toLocaleLowerCase().includes(query)),
      )
      .sort(compareSimilarPhotos),
    allDecisionsSelected = setEquals(
      state.similar.decisions,
      DECISION_VALUES,
    ),
    allAiSelected = setEquals(state.similar.ai, AI_VALUES),
    allFormatsSelected = setEquals(
      state.similar.formats,
      similarFormatValues(),
    ),
    filtered =
      !allDecisionsSelected || !allAiSelected || !allFormatsSelected;
  state.items = decorated;
  $("#viewSubtitle").textContent =
    `当前组 ${detail.count} 张${detail.face_safe ? " · 人物照片请检查表情" : ""}${query || filtered ? ` · 显示 ${decorated.length} 张` : ""}`;
  $("#similarDetailGallery").innerHTML = decorated
    .map((photo, index) => {
      const recommended =
        (photo.similarity_source_id || photo.id) === detail.recommended_id;
      const extra = recommended
        ? ""
        : detail.kind === "exact"
          ? "完全重复"
          : `相似度 ${Math.round((photo.group_similarity || 0) * 100)}%`;
      return photoCard(
        photo,
        index,
        recommended ? "推荐保留" : "可考虑移除",
        recommended ? "recommended" : "candidate-remove",
        extra,
      );
    })
    .join("");
  $("#empty").classList.toggle("hidden", !!decorated.length);
  if (!decorated.length) {
    $("#emptyTitle").textContent = "当前筛选没有结果";
    $("#emptyText").textContent = "没有照片符合当前组的搜索和查看条件";
  }
  syncSimilarControls();
}
async function openSimilarGroup(groupId) {
  state.similar.selectedId = groupId;
  state.similar.memberSearch = "";
  state.similar.detail = null;
  state.similar.formatCategories = [];
  state.similar.decisions = new Set(DECISION_VALUES);
  state.similar.ai = new Set(AI_VALUES);
  state.similar.formats = new Set();
  state.similar.mode = window.innerWidth <= 850 ? "expanded" : "side";
  $("#searchInput").value = "";
  $("#searchInput").placeholder = "搜索当前组照片";
  renderSimilarFolders();
  applySimilarMode();
  try {
    await loadSimilarGroupMembers();
  } catch (e) {
    closeSimilarDetail();
    toast(e.message);
  }
}
function closeSimilarDetail(restoreSearch = true) {
  state.similar.selectedId = "";
  state.similar.mode = "closed";
  state.similar.memberSearch = "";
  state.similar.detail = null;
  state.similar.formatCategories = [];
  state.similar.decisions = new Set(DECISION_VALUES);
  state.similar.ai = new Set(AI_VALUES);
  state.similar.formats = new Set();
  state.items = [];
  if (restoreSearch) {
    $("#searchInput").value = state.similar.listSearch;
    $("#searchInput").placeholder = "搜索相似组中的照片";
  }
  $("#similarDetailGallery").innerHTML = "";
  renderSimilarFolders();
  applySimilarMode();
  $("#viewSubtitle").textContent =
    `${state.similar.total} 组相似照片${state.similar.groups.some((group) => group.face_safe) ? " · 人物照片请检查表情" : ""}`;
  $("#empty").classList.toggle("hidden", !!state.similar.total);
}
function expandSimilarDetail() {
  if (!state.similar.selectedId) return;
  state.similar.mode = "expanded";
  renderSimilarFolders();
  applySimilarMode();
}

function collapseSimilarDetail() {
  if (!state.similar.selectedId) return;
  state.similar.mode = "side";
  renderSimilarFolders();
  applySimilarMode();
}

function similarGroupComplete() {
  const members = state.similar.detail?.members || [];
  return members.length > 0 && members.every((photo) => photo.decision);
}

// Recompute the open group's status from its (already patched) members and
// update just that badge. A full re-render would rebuild every card in the
// sidebar on each decision; refetching would discard the optimistic local
// state the caller just applied.
function refreshSelectedGroupStatus() {
  const detail = state.similar.detail;
  if (!detail?.id) return;
  const members = detail.members || [];
  const decided = members.filter((photo) => photo.decision).length;
  const status =
    !members.length || !decided
      ? "untouched"
      : decided === members.length
        ? "done"
        : "partial";
  const group = state.similar.groups.find((item) => item.id === detail.id);
  if (!group) return;
  detail.status = status;
  detail.decided_count = decided;
  if (group.status === status && group.decided_count === decided) return;
  group.status = status;
  group.decided_count = decided;
  const badge = document.querySelector(
    `.similar-folder[data-similar-group="${detail.id}"] .folder-status`,
  );
  if (!badge) return;
  const meta = similarGroupStatusMeta(group);
  badge.dataset.status = meta.key;
  badge.textContent = meta.label;
  badge.title = meta.hint;
}

async function advanceSimilarGroup(keepViewer = false) {
  if (
    state.view !== "similar" ||
    !state.similar.selectedId ||
    state.similar.mode === "expanded"
  ) return false;
  const currentId = state.similar.selectedId;
  let index = state.similar.groups.findIndex((group) => group.id === currentId);
  if (index < 0) return false;
  if (index + 1 >= state.similar.groups.length && !state.similar.done)
    await loadSimilarView(false);
  index = state.similar.groups.findIndex((group) => group.id === currentId);
  const next = state.similar.groups[index + 1];
  if (!next) return false;
  await openSimilarGroup(next.id);
  if (keepViewer && state.items.length) {
    state.viewerNeedsRefresh = false;
    state.viewerDirtyIds.clear();
    openViewer(0);
  }
  return true;
}

function bindSimilarEvents() {
  $("#similarCollapseBtn").onclick = () => closeSimilarDetail();
  $("#similarBackBtn").onclick = () => closeSimilarDetail();
  $("#similarExpandBtn").onclick = expandSimilarDetail;
  $("#similarCloseBtn").onclick = collapseSimilarDetail;
  $("#similarStatusFilter").onchange = (event) => {
    // A new filter describes a different list, so the accumulated pages have
    // to go: they were fetched under the previous status.
    state.similar.statusFilter = event.target.value;
    loadSimilarView(true).catch((error) => toast(error.message));
  };
  $("#similarFolderPane").onclick = (event) => {
    if (
      state.similar.mode === "side" &&
      !event.target.closest("[data-similar-group]")
    )
      closeSimilarDetail();
  };
  $("#similarFolders").onclick = (event) => {
    const button = event.target.closest("[data-similar-group]");
    if (!button) return;
    event.stopPropagation();
    openSimilarGroup(button.dataset.similarGroup);
  };
  const similarObserver = new IntersectionObserver(
    (entries) => {
      if (
        entries.some((entry) => entry.isIntersecting) &&
        state.view === "similar"
      )
        loadSimilarView(false).catch((error) => toast(error.message));
    },
    { root: $("#similarFolderPane"), rootMargin: "400px 0px" },
  );
  similarObserver.observe($("#similarGroupSentinel"));
  window.addEventListener("resize", () => {
    if (
      state.view === "similar" &&
      state.similar.selectedId &&
      window.innerWidth <= 850 &&
      state.similar.mode === "side"
    )
      expandSimilarDetail();
  });
}
