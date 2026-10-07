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
// ── 按显示尺寸换源（tier swap）────────────────────────────────────────────
//
// 问题：CSS `transform: scale()` 是在**已解码位图**之上重采样。一张 7008px 的
// 照片若先缩到 2048px 预览再放大，浏览器是在插值一张 2048px 的图——放大多少倍
// 就糊多少倍。原图加载了也没用：原图同样会被 CSS 缩放插值。
//
// 做法（与 Lightroom / Capture One 相同）：**显示多大，就请求多大像素的图**，
// 让浏览器永远只做降采样。
//
//   needed = offsetWidth * scale * DPR
//
// 即「当前 scale 下，这张图在屏幕上占据多少个设备像素」。只要供给宽度 >= needed，
// 浏览器就是降采样；不足才会插值。
//
// 三档：2048（首屏）/ 4096（中档）/ 原图（最高档，w 不带 = 直接发源文件）。
// 四档：1024（缩略/首屏）/ 2048 / 4096 / 原图（最高档，w 不带 = 直接发源文件）。
//
// 为什么最下面要有 1024 而不是直接从 2048 起步：浏览器把大位图**缩很小**时走的是
// 快速降采样路径。实测（3440×1440 无损截图，DPR=1）：7008px 的位图显示在 967px 的
// 舞台里，屏幕高频能量只有「原图直接 Lanczos 降采样」的 50%，反推等效分辨率约 724px。
// 而只要供给宽度接近显示尺寸（≤2 倍差），损失就基本消失。所以每一档的意义不只是
// 「够不够清晰」，还包括「别让浏览器自己缩太多」。
const VIEWER_TIER_WIDTHS = [1024, 2048, 4096];
// 载入指示器的默认文案。换源时会临时改成「正在载入 4096px 细节…」之类，
// 所以默认值必须单独存一份，供各处复位使用。
const VIEWER_LOADING_TEXT = "正在载入原图…";
// 原图档用 Infinity 当宽度：它在比较里永远「够宽」，于是升档到它之后不会再因
// needed 增大而降回来（否则会在原图与 4096 之间来回抖）。
const VIEWER_TIER_ORIGINAL = Infinity;
// 降档死区系数。升档看「needed > 当前档宽」，降档看「needed < 下一档宽 × 本值」，
// 两个阈值不对称 = 滞回。没有它，在临界点反复微调会不停请求、缓存来回失效。
//
// 0.75 的来历：滚轮一个参照格默认 ×1.06（见 VIEWER_WHEEL_BASE），25% 的死区远宽
// 于它，所以连续滚轮缩放**不会**触发抖动；而死区又不是无限大，所以放大后仍会真的
// 降档省内存。
const VIEWER_TIER_HYSTERESIS = 0.75;
// 请求节流。缩放是高频事件（滚轮每帧一次），必须等 scale 稳定下来再换源，
// 否则一次连续滚动能打出几十个请求。
const VIEWER_TIER_SETTLE_MS = 150;
// 已放弃的档位。NAS 读不到 / 请求失败时记下来，避免每帧重试同一个坏档位。
const VIEWER_TIER_FAILED = new Set();

// 当前 scale 需要的源像素数。
// CSS `.viewer img` 的 max-width / max-height 留白，作为拿不到容器尺寸时的退路。
const VIEWER_FIT_INSET_W = 170;
const VIEWER_FIT_INSET_H = 145;
// 位图在 scale=1 时应占据的 CSS 尺寸（「适应窗口」的尺寸）。
//
// 这是缩放的基准。之所以要自己算而不是读 offsetWidth：缩放现在写进 width/height，
// offsetWidth 会随缩放变化，拿它当基准等于每帧把上一帧的结果再乘一次。
function viewerFitSize(target) {
  const natural = target.naturalWidth || 0,
    naturalHeight = target.naturalHeight || 0;
  if (!natural || !naturalHeight) return null;
  const box = target.parentElement,
    maxW = (box && box.clientWidth) ||
      Math.max(1, (window.innerWidth || 0) - VIEWER_FIT_INSET_W),
    maxH = (box && box.clientHeight) ||
      Math.max(1, (window.innerHeight || 0) - VIEWER_FIT_INSET_H);
  const k = Math.min(maxW / natural, maxH / naturalHeight, 1);
  return { w: natural * k, h: naturalHeight * k };
}
// 当前呈现对象在 scale=1 时的尺寸。动图用的 <video> 由 CSS 撑满媒体框、没有
// naturalWidth，所以取容器尺寸——保持它原来的行为不变。
function viewerUnscaledSize() {
  const target = viewerTransformTarget();
  if (target === $("#viewerVideo")) {
    const box = target.parentElement;
    return { w: (box && box.clientWidth) || 0, h: (box && box.clientHeight) || 0 };
  }
  return viewerFitSize(target) || { w: 0, h: 0 };
}
function viewerNeededPixels() {
  const base = viewerUnscaledSize();
  if (!base.w) return 0;
  const dpr = window.devicePixelRatio || 1;
  return base.w * state.viewerTransform.scale * dpr;
}
// 当前挂着的位图实际有多少像素可取（视频走 <video>，没有 naturalWidth）。
function viewerBitmapPixels() {
  if (state.viewerMotion.active) return Infinity;
  const target = viewerTransformTarget();
  return target.naturalWidth || 0;
}
// 当前挂着的位图是否**小于源文件**的像素——即挂的是 1024/2048/4096 预览档，
// 而不是原图本身。
//
// 这是「1:1」是否诚实的判据。位图比源文件小时，即便 scale 恰好落在**这张位图**
// 的 1:1 点，一个屏幕像素对应的也是被缩过的源像素：它满足「一个位图像素对一个
// 屏幕像素」，却不满足「一个源像素对一个屏幕像素」。此时若报「已 1:1」，恰好
// 复现用户投诉的那种说谎——听起来在看真像素，看到的却是预览位图。
//
// 只有在**同时**知道源宽度与当前位图宽度时才下这个结论；缺任一项即返回 false
// （无从判断就不指控），避免在没有 items 的调用路径上让提示闪烁。
function viewerBitmapBelowSource() {
  const p = state.items[state.viewerIndex];
  if (!p) return false;
  const source = Number(p.width) || 0,
    have = viewerBitmapPixels();
  if (!source || !have) return false;
  return have < source * 0.999;
}
// 为 needed 挑选档位下标。current 为当前已载入的档位，滞回逻辑只看它。
//
// 升档：needed 超过当前档的可用像素（真的不够了）。
// 降档：needed 已经明显低于下一档（×0.75 以下），否则留在原档。
function viewerPickTier(needed, current) {
  let tier = 0;
  while (
    tier < VIEWER_TIER_WIDTHS.length &&
    needed > VIEWER_TIER_WIDTHS[tier]
  ) {
    tier += 1;
  }
  if (tier > current) return Math.min(tier, VIEWER_TIER_WIDTHS.length);
  if (tier < current) {
    // 允许**跨档**下降，死区只用于相邻档之间。
    //
    // 原来的实现要求 `tier + 1 === current`，也就是一次只降一档。从原图档缩回
    // 「整幅显示」时目标是首屏档（差 2~3 档），条件恒假 → 永远保持原图，于是
    // 浏览器被迫把 7008px 位图缩到几百像素显示（实测损失一半高频细节），而
    // 「整幅显示」本该是最清晰的状态。跨档直接跳只多花一个请求，却把这条自相
    // 矛盾的路径彻底去掉。
    //
    // 相邻档之间仍保留死区，避免需求在边界徘徊时来回换图。
    if (
      tier + 1 === current &&
      needed >= VIEWER_TIER_WIDTHS[tier] * VIEWER_TIER_HYSTERESIS
    )
      return current;
    return tier;
  }
  return current;
}
// 该档位对应的图片 URL。原图档不带 w=，服务端直接发源文件。
function viewerTierUrl(p, tier) {
  if (tier >= VIEWER_TIER_WIDTHS.length) return p.photo_url;
  return `${p.photo_url}&w=${VIEWER_TIER_WIDTHS[tier]}`;
}
function viewerTierWidth(tier) {
  return tier >= VIEWER_TIER_WIDTHS.length
    ? VIEWER_TIER_ORIGINAL
    : VIEWER_TIER_WIDTHS[tier];
}
function viewerTierLabel(tier) {
  return tier >= VIEWER_TIER_WIDTHS.length ? "原图" : `${VIEWER_TIER_WIDTHS[tier]}px`;
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
    base = viewerUnscaledSize();
  if (!natural || !base.w) return 1;
  return Math.max(1, natural / base.w);
}
// 当前是否**正好**处在 1:1。
//
// 这里必须用「等于」而不是「小于等于」。小于 1:1 点意味着位图被缩着放（每个 CSS
// 像素塞了多于一个源像素），那是降采样而非 1:1；把它一并报成「已 1:1」会让状态
// 提示说谎——一张4000px 的图缩到 800px 宽、放到 3 倍，听起来像 1:1，其实每个屏幕
// 像素里挤了 1.67 个源像素。只有恰好落在该点（±0.1% 容忍浮点误差）才叫 1:1。
//
// 「位图够不够铺满当前 scale」是另一个问题，由 viewerNeedsBetterSource 回答；
// 两者必须分开。scale 恰好等于 1:1 点，但当前挂的是一张 2048px 的图而照片有
// 7008px 时，它**同时**是 1:1 且像素不够——状态提示必须两个都报，不能只报前者。
function viewerIsOneToOne() {
  const oneToOne = viewerOneToOneScale(),
    scale = state.viewerTransform.scale;
  if (!viewerHasPixels()) return false;
  return Math.abs(scale - oneToOne) <= oneToOne * 0.001;
}
// 位图是否已经解码出尺寸。没有它时不能对「是否 1:1」下结论——那会让提示在图片
// 尚未加载时先闪一次错误的「已 1:1」。
function viewerHasPixels() {
  const target = viewerTransformTarget();
  return Boolean(target.naturalWidth && target.offsetWidth);
}
// 换源是否在途。
function viewerIsSourcingDetail() {
  return state.viewerTierPending !== null;
}
// 换源是否必要：当前位图的像素不足以铺满当前 scale。
function viewerNeedsBetterSource() {
  if (state.viewerMotion.active || !viewerHasPixels()) return false;
  const have = viewerBitmapPixels();
  if (!have) return false;
  return viewerNeededPixels() > have * 1.001;
}
// 是否已经**物理上**到顶：原图本身的像素宽度都供不上当前 scale。
//
// 这是换源修好之后「已插值」应有的归宿。旧文案说「已插值 N%，细节非原始像素」，
// 听起来像缺陷；实际上放大到超过原图分辨率之后，任何来源都补不出不存在的像素，
// 插值不可避免。诚实地说「到顶了」比给一个可修复的假象有用。
//
// 判据必须是**位图**的实际宽度，不是数据库里的原图宽度。两者在换源还没落地时
// 并不相等：首屏挂的是 2048px 预览、而 p.width 一直是 7008，于是放大到 2048 以上
// 就会报「已到原始像素上限」——用户看到的却是一张 2048px 的位图被拉伸，提示在说谎。
// 只有当前位图确实（接近）原图尺寸时，「到顶」才是一句诚实的话。
function viewerAtPixelCeiling() {
  const p = state.items[state.viewerIndex];
  if (!p || state.viewerMotion.active || !viewerHasPixels()) return false;
  if (viewerIsSourcingDetail()) return false;
  const sourceWidth = Number(p.width) || 0;
  const bitmapWidth = viewerBitmapPixels();
  if (!sourceWidth || !bitmapWidth) return false;
  if (bitmapWidth < sourceWidth * 0.999) return false;
  return viewerNeededPixels() > sourceWidth * 1.001;
}
// 「适应」的高亮：分段控件里只剩它一个视图状态，判据与状态提示同源——「适应」是
// 落在整幅显示（scale = 1）且不是 1:1 的情形。1:1 本身既没有独立按钮、也没有快捷键
// 了（它只在「查看原图」到货后自动到达），放大到中间倍率时「适应」也不点亮。
//
// 高亮还必须排除「位图其实是预览档」这一情形：否则挂了 2048 预览、scale 落在它的
// 1:1 点时「适应」会误亮，而状态提示同时写着「非原图 1:1」——两处口径必须一致。
function renderViewerZoomState() {
  const fit = $("#viewerFit"),
    exact =
      viewerIsOneToOne() &&
      !viewerNeedsBetterSource() &&
      !viewerBitmapBelowSource();
  fit.classList.toggle(
    "viewer-zoom-active",
    !exact && Math.abs(state.viewerTransform.scale - 1) <= 1e-6,
  );
}
// 状态提示。必须如实告诉用户「看到的不是原始像素」，因为这是用户投诉的原点。
//
// 换源模型下有四态，比旧的三态多一个「细节加载中」：换源在途是这个模型引入的新
// 状态，旧模型里根本不存在（旧模型自始至终只有一张位图，不存在「换」）。
//
// 1. 整幅显示：未到 1:1，每屏幕像素含多于一个源像素（降采样，清晰）。
// 2. 已 1:1：恰好一个源像素对一个 CSS 像素，且当前位图确实供得上所需像素，
//    并且这张位图就是原图本身。
// 3. 非原图 1:1：scale 落在**当前预览位图**的 1:1 点，但位图比源文件小——满足
//    「一个位图像素对一个屏幕像素」，不满足「一个源像素对一个屏幕像素」。必须
//    与状态 2 分开，否则「已 1:1」又是一句谎话（用户投诉的原点）。
// 4. 细节加载中：正在换更高档的源，此刻显示的还是旧档（不闪、不空白）。
// 5. 已到原始像素上限：needed 超过原图宽度，插值在物理上不可避免。
function renderViewerScaleHint() {
  renderViewerZoomState();
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
  hint.classList.remove("viewer-scale-hint-exact");
  // 换源在途：如实说正在加载，且**不**报 1:1——旧档此刻确实还没供上所需像素。
  if (viewerIsSourcingDetail()) {
    hint.textContent = `细节加载中 · 目标 ${viewerTierLabel(
      state.viewerTierPending,
    )}`;
    return;
  }
  // 报的是**当前挂着的那张位图**的分辨率，不是数据库里源文件的。
  //
  // 两者在换源落地之前并不相等：首屏挂 2048px 预览时 p.width 仍是 7008，于是提示会
  // 写「已 1:1（原始分辨率 7008 × 4672）」——用户看到的其实是 2048px 位图被放大，
  // 提示在说谎。状态提示的全部价值就在于它说的是屏幕上正在发生的事，所以以位图为准；
  // 位图小于源文件时把两者都写出来，用户才知道还需不需要等。
  const bitmapWidth = viewerBitmapPixels(),
    bitmapHeight = viewerTransformTarget().naturalHeight || 0,
    shownWidth = bitmapWidth || width,
    shownHeight = bitmapHeight || height,
    size = `${shownWidth} × ${shownHeight}`,
    awaiting = bitmapWidth > 0 && bitmapWidth < width * 0.999
      ? ` · 原图 ${width} × ${height} 未载入`
      : "";
  if (viewerAtPixelCeiling()) {
    hint.textContent = `已到原始像素上限 · ${size}${awaiting}`;
    return;
  }
  if (viewerIsOneToOne() && !viewerNeedsBetterSource()) {
    // 位图其实是预览档：此刻只是「这张位图的 1:1」，不是原图的 1:1。如实说明，
    // 且**不**点亮 exact——那颗绿点与「已 1:1」同样会让人以为已在看真像素。
    // 用户点「查看原图」把原图真正下发后，这里自然回到「已 1:1」。
    if (viewerBitmapBelowSource()) {
      hint.textContent = `当前 ${bitmapWidth}px 位图 · 非原图 1:1${awaiting}`;
      return;
    }
    hint.textContent = `已 1:1（${size}）${awaiting}`;
    hint.classList.add("viewer-scale-hint-exact");
    return;
  }
  hint.textContent = `整幅显示 · ${size}${awaiting}`;
}
// 诊断上报：把浏览器此刻的真实测量值写到 cullumi.log。
//
// 为什么需要它：模糊的投诉无法靠读代码定论——每一层报出来的数字都合理，唯一能
// 定位问题的是「投诉那一刻浏览器实际量到了什么」。这个函数只读不写，不影响显示。
//
// 关键字段是 naturalWidth / offsetWidth：前者是浏览器**真正解码出来**的位图宽度，
// 后者是它**实际布局**的宽度。两者相除就是 CSS transform 之外真正被插值的倍数。
// 若 offsetWidth 已被 CSS max-width 压住（.viewer img 有 max-width:calc(100vw - 170px)），
// 那么无论换到多高的档位，浏览器都是先按布局尺寸栅格化、再由 transform 放大——
// 换源永远救不回来。这几个数字能一次性把那种情况暴露出来。
const VIEWER_DIAG_DELAY = 400;
function scheduleViewerDiag() {
  clearTimeout(state.viewerDiagTimer);
  state.viewerDiagTimer = setTimeout(reportViewerDiag, VIEWER_DIAG_DELAY);
}
async function reportViewerDiag() {
  const p = state.items[state.viewerIndex];
  if (!p) return;
  const img = $("#viewerImage");
  // 缩放过程中的中间态没有诊断价值：等 transform 稳定下来再报。
  if (state.viewerTransform.dragging) {
    scheduleViewerDiag();
    return;
  }
  const width = Number(p.width) || 0;
  const payload = {
    photo: p.relative_path,
    photo_id: p.id,
    // 源文件真实像素
    source: `${width}x${Number(p.height) || 0}`,
    // 浏览器解码出来的位图（换源是否真的换了，看这一项）
    bitmap: `${img.naturalWidth || 0}x${img.naturalHeight || 0}`,
    // 浏览器布局尺寸（被 max-width 压住的那个）
    layout: `${img.offsetWidth || 0}x${img.offsetHeight || 0}`,
    // CSS transform 现在的倍率，以及它与 1:1 点的关系
    scale: Number(state.viewerTransform.scale.toFixed(4)),
    one_to_one: Number(viewerOneToOneScale().toFixed(4)),
    dpr: window.devicePixelRatio,
    // 换源状态：当前档位（0=预览，>=2=原图）、在途目标、失败过的档
    tier: state.viewerTier,
    tier_pending: state.viewerTierPending,
    tier_failed: [...VIEWER_TIER_FAILED],
    // 界面上给用户看的那句话，原样带走
    hint: $("#viewerScaleHint").textContent,
    src: (img.getAttribute("src") || "").split("&token=")[0].split("&w=").pop(),
  };
  try {
    await json("/api/diagnostics/viewer", payload);
  } catch (error) {
    // 诊断失败不能影响看图，但也必须留下痕迹：最初这里只有 catch {}，结果诊断
    // 静默失效，却让人以为「没触发」——和它本要解决的问题是同一类错误。
    // 失败时退回 console，WebKit 控制台在Safari/开发者工具里可见。
    console.warn("viewer-diag 失败", error, payload);
  }
}

function applyViewerTransform() {
  const t = state.viewerTransform,
    target = viewerTransformTarget();
  [$("#viewerImage"), $("#viewerVideo")].forEach((media) => {
    if (media !== target) {
      media.style.transform = "";
      media.style.width = "";
      media.style.height = "";
      media.style.maxWidth = "";
      media.style.maxHeight = "";
      media.classList.remove("zoomed", "dragging");
    }
  });
  // 缩放写进布局尺寸，不再用 transform: scale(…)。
  //
  // transform 缩放拉伸的是**已经栅格化好的纹理**，浏览器不会因为 transform 改变
  // 而按新的尺寸重新采样。实测（3440×1440 无损截图，DPR=1）：提示写着「已 1:1
  // （7008 × 4672）」、位图确实是原图，但屏幕上那块区域的高频能量只有源图真实
  // 1:1 像素的 8%，反推等效分辨率约 1200~1500px —— 一张那么小的纹理被拉到 7008px。
  // 也就是说：换源换到原图是**无效的**，合成层始终用按布局尺寸栅格化的那一张。
  //
  // 把目标尺寸直接写进 width/height，浏览器就必须按该尺寸重新栅格化。平移仍交给
  // transform：纯平移不改变采样，不引入模糊。
  //
  // 不为 width/height 加过渡：那会让每一帧都触发一次大图重栅格化，7008px 的照片
  // 会卡成幻灯片。缩放瞬间到位反而更利落。
  const isImage = target === $("#viewerImage");
  let residual = 1;
  if (isImage) {
    const fit = viewerFitSize(target),
      natural = target.naturalWidth || 0;
    if (fit && natural) {
      // 布局尺寸封顶在「1:1」那一档，再往上交给 transform。
      //
      // 两个理由：
      // ① 超大元素会被浏览器截断，而截断后宽高不再同比 —— 表现就是「拉到一定
      //    程度后图片被拉宽」。封顶在 naturalWidth 处，布局尺寸永远不会失控。
      // ② 1:1 以内必须用布局尺寸，浏览器才会按显示尺寸重新栅格化（这是放大后
      //    不糊的前提）；1:1 以外本就是在放大不存在的像素，用什么方式都一样。
      const oneToOne = Math.max(1, natural / fit.w),
        layoutScale = Math.min(t.scale, oneToOne);
      residual = t.scale / layoutScale;
      target.style.width = `${fit.w * layoutScale}px`;
      target.style.height = `${fit.h * layoutScale}px`;
      // max-width/max-height 会把显式尺寸再压回去，必须让开。
      target.style.maxWidth = "none";
      target.style.maxHeight = "none";
    }
  }
  target.style.transform = `translate3d(${t.x}px,${t.y}px,0) scale(${
    isImage ? residual : t.scale
  })`;
  target.classList.toggle("zoomed", t.scale > 1);
  target.classList.toggle("dragging", t.dragging);
  renderViewerScaleHint();
  scheduleViewerTierCheck();
  scheduleViewerDiag();
}
function clampViewerPan() {
  const t = state.viewerTransform,
    base = viewerUnscaledSize(),
    // 超出部分取自「未缩放尺寸 × 倍率」，不是 offsetWidth × 倍率：缩放已写进
    // width/height，offsetWidth 含了倍率，再乘一次会把可拖动范围放大一个数量级。
    maxX = Math.max(0, (base.w * (t.scale - 1)) / 2),
    maxY = Math.max(0, (base.h * (t.scale - 1)) / 2);
  t.x = Math.max(-maxX, Math.min(maxX, t.x));
  t.y = Math.max(-maxY, Math.min(maxY, t.y));
}
// ── 换源调度 ───────────────────────────────────────────────────────────────
//
// 缩放是高频事件（滚轮每帧一次）。若每次 applyViewerTransform 都直接发请求，一次
// 连续滚动能打出几十个请求，每个都是 NAS 上的解码+重编码。所以这里做两件事：
//
//   1. 节流：等 scale 稳定 VIEWER_TIER_SETTLE_MS 后才检查一次。
//   2. 去重：同一档位不重复请求——目标档位等于当前档位就直接返回。
function scheduleViewerTierCheck() {
  clearTimeout(state.viewerTierTimer);
  state.viewerTierTimer = setTimeout(
    syncViewerTier,
    VIEWER_TIER_SETTLE_MS,
  );
}
// 检查并按需换源。可被 loadViewerOriginal 直接调用（跳过等待）。
function syncViewerTier({ force = false } = {}) {
  const p = state.items[state.viewerIndex];
  if (!p || state.viewerMotion.active) return;
  // 换源在途时不并发发第二个请求：等它回来后自然会再触发一次检查。
  if (state.viewerTierPending !== null) return;
  // 动态照片的封面本来就是从视频里抽出的 JPEG，用原图 URL 即可，不参与换源。
  if (p.media_type === "motion_photo" || !p.preview_url) return;

  const current = state.viewerTier;
  const needed = viewerNeededPixels();
  // 位图尺寸未知时无法判断需求，等 load 事件再来。
  if (!needed || !viewerBitmapPixels()) return;

  let tier = force
    ? VIEWER_TIER_WIDTHS.length
    : viewerPickTier(needed, current);

  // 目标档位已失败（NAS 读不到）→ 退回当前能用的那一档，绝不留下空白。
  while (
    tier !== current &&
    VIEWER_TIER_FAILED.has(`${p.id}:${tier}`)
  ) {
    tier -= 1;
    if (tier < 0) return;
  }
  // 同一档位不重复请求。这是防抖的最后一道闸：即使滞回算错，也不会刷请求。
  if (tier === current) return;
  // force 来自「查看原图」按钮：原图比预览大得多，之前那个「适应窗口」的倍率现在
  // 对应真实的 1:1，到货后直接落过去。自动换源**不**落——那会把用户当前的缩放倍率
  // 重置掉，反而打断正在进行的判读。
  state.viewerTierAutoOneToOne = Boolean(force);
  loadViewerTier(p, tier);
}
// 用一个独立的 Image 预加载，成功后才替换 <img> 的 src。
//
// 关键点（与 loadViewerOriginal 同一模式）：**新图到达前保持现有图不动**。直接改
// src 会先把画面清空，加载失败就只剩一个破图；NAS 抖动时那样非常难看。
function loadViewerTier(p, tier) {
  const photoId = p.id,
    url = viewerTierUrl(p, tier),
    indicator = $("#viewerLoading"),
    probe = new Image();
  state.viewerTierPending = tier;
  indicator.classList.remove("hidden");
  indicator.textContent = `正在载入${viewerTierLabel(tier)}细节…`;
  renderViewerScaleHint();
  probe.onload = () => {
    if (state.items[state.viewerIndex]?.id !== photoId) {
      // 用户已经翻到下一张：过期响应直接丢弃，否则会把上一张的图装到当前照片上。
      VIEWER_TIER_FAILED.add(`${photoId}:${tier}`);
      state.viewerTierPending = null;
      indicator.classList.add("hidden");
      indicator.textContent = VIEWER_LOADING_TEXT;
      return;
    }
    const img = $("#viewerImage");
    img.src = url;
    state.viewerTierPending = null;
    state.viewerTier = tier;
    indicator.classList.add("hidden");
    indicator.textContent = VIEWER_LOADING_TEXT;
    img.addEventListener(
      "load",
      () => {
        // 新位图的 naturalWidth 变了，offsetWidth 也可能变（CSS max-* 会重新布局）。
        // 此时旧的 scale 是按旧布局算的，必须重夹一次，否则会平移出画面。
        clampViewerPan();
        if (state.viewerTierAutoOneToOne) {
          state.viewerTierAutoOneToOne = false;
          resetViewerTransform();
          // 到货后自动落到真实 1:1：直接把 scale 设到 1:1 点并清掉平移。
          //
          // 快捷键 `1` 按用户裁决撤销后，原先复用的「跳到 1:1」函数一并删除，
          // 这里保留它等效的落位动作——这是 1:1 唯一残留的触发入口，必须工作。
          // 语义不变：目标即 1:1 目的地，不是开关（resetViewerTransform 先把
          // 画面归位到整幅显示，再由下面这一步推到 1:1）。
          Object.assign(state.viewerTransform, {
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
        applyViewerTransform();
        syncViewerOriginalState();
      },
      { once: true },
    );
    img.addEventListener(
      "error",
      () => {
        // 换源失败：保留已经显示着的旧档位，只把这一档记为失败。
        // 降级必须是「无感的视觉损失」，而不是一个坏掉的预览器。
        state.viewerTierAutoOneToOne = false;
        VIEWER_TIER_FAILED.add(`${photoId}:${tier}`);
        if (tier >= VIEWER_TIER_WIDTHS.length)
          state.viewerOriginalFailed.add(photoId);
        state.viewerTier = Math.max(0, tier - 1);
        state.viewerTierPending = null;
        indicator.classList.add("hidden");
        indicator.textContent = VIEWER_LOADING_TEXT;
        if (state.items[state.viewerIndex]?.id !== photoId) return;
        toast(`${viewerTierLabel(tier)}细节载入失败，已保留当前画质`);
        syncViewerOriginalState();
      },
      { once: true },
    );
    syncViewerOriginalState();
  };
  probe.onerror = () => {
    // 预加载就失败（NAS 不可读 / 请求出错）：旧图原样留着，不动 src。
    VIEWER_TIER_FAILED.add(`${photoId}:${tier}`);
    state.viewerTierPending = null;
    indicator.classList.add("hidden");
    indicator.textContent = VIEWER_LOADING_TEXT;
    if (state.items[state.viewerIndex]?.id !== photoId) return;
    state.viewerTierAutoOneToOne = false;
    if (tier >= VIEWER_TIER_WIDTHS.length)
      state.viewerOriginalFailed.add(photoId);
    state.viewerTier = Math.max(0, tier - 1);
    // 必须告知用户。静默降级看起来像「Cullumi 就只能这么糊」，而真实原因是
    // 取图失败——这两件事对用户的下一步动作完全不同（重试网络 vs 接受画质）。
    toast(`${viewerTierLabel(tier)}细节载入失败，已保留当前画质`);
    syncViewerOriginalState();
    renderViewerScaleHint();
  };
  probe.src = url;
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
// 缩放上限必须够到「原始像素」这一档，否则 1:1 在常规照片上会撞上限而停在
// 一个仍然插值的倍率上——那正是这个功能要解决的问题，不能自己复现。
const VIEWER_MAX_SCALE = 32;

// ── 滚轮缩放：按滚动量、按设备 ────────────────────────────────────────────
//
// 旧实现是「一格固定 ×1.18」，与滚动幅度无关，用户反馈「滚一格太多」。新实现把
// 倍率做成滚动量的指数函数：
//
//   factor = exp(-deltaY_px * VIEWER_WHEEL_BASE * deviceSensitivity)
//
// 为什么用指数而不是线性：指数映射下「放大 N 档再缩小 N 档」恰好回到原处——
// 两次滚动的倍率互为倒数（exp(a)·exp(-a) = 1），线性映射做不到这一点。
//
// 定标依据（文档值，见 README「已知限制」与「大图预览」）：
//   · 带刻度的鼠标滚轮，一个齿格在 Chrome/Edge/Windows 上是 |deltaY| = 100 CSS px
//     （deltaMode 0）；Firefox 用 deltaMode 1、每格 3 行。取 100px 作为「一格」的
//     参照量（VIEWER_WHEEL_REFERENCE_NOTCH_PX）。
//   · 旧行为一格 ×1.18 被判定过多，故把一个参照格的目标倍率压到 ×1.06。
//   · 于是 VIEWER_WHEEL_BASE = ln(1.06)/100：deviceSensitivity=1.0 时，
//     -deltaY_px = 100 恰好得到 exp(ln 1.06) = 1.06。
//
// 触控板与鼠标的原始 deltaY 量级差异极大（触控板每事件 0.5–3px、60–120 事件/秒；
// 鼠标每格 100px 级、3–8 事件/秒），单一常数无法同时满足两者，所以灵敏度分开设置。
const VIEWER_WHEEL_REFERENCE_NOTCH_PX = 100;
const VIEWER_WHEEL_ZOOM_PER_NOTCH = 1.06;
const VIEWER_WHEEL_BASE =
  Math.log(VIEWER_WHEEL_ZOOM_PER_NOTCH) / VIEWER_WHEEL_REFERENCE_NOTCH_PX;
// deltaMode 1（行）/ 2（页）到像素的换算，用于把不同单位归一到像素量级。
const VIEWER_WHEEL_LINE_PX = 16;
const VIEWER_WHEEL_PAGE_PX = 800;
// 设备识别的会话阈值。
const VIEWER_WHEEL_SESSION_GAP_MS = 250; // 事件间隔超过它即视为新一次滚动
const VIEWER_WHEEL_MIN_EVENTS = 3; // 会话内事件数达到它才启用方差判据
const VIEWER_WHEEL_MOUSE_DELTA_PX = 20; // |deltaY| 达到它更像刻度滚轮
const VIEWER_WHEEL_TRACKPAD_DELTA_PX = 8; // |deltaY| 不超过它更像触控板
const VIEWER_WHEEL_TRACKPAD_CV = 0.35; // 触控板量级波动大
const VIEWER_WHEEL_MOUSE_CV = 0.25; // 刻度滚轮量级稳定
const VIEWER_WHEEL_SENSITIVITY_MIN = 0.5;
const VIEWER_WHEEL_SENSITIVITY_MAX = 2.0;

// 把事件的滚动量归一到「像素」量级，消除 deltaMode 的单位差异（Firefox 用行）。
function viewerWheelPixels(event) {
  const delta = Number(event.deltaY) || 0,
    mode = Number(event.deltaMode) || 0;
  if (mode === 1) return delta * VIEWER_WHEEL_LINE_PX;
  if (mode === 2) return delta * VIEWER_WHEEL_PAGE_PX;
  return delta;
}
// 开启一次新的滚动会话。判定必须限定在一次会话内，否则上一次滚动的特征会污染
// 下一次，用户换设备/换手势后要滚很久才会被重新认出来。
function resetViewerWheelSession(now) {
  state.viewerWheel = {
    lastTime: Number(now) || 0,
    count: 0,
    sum: 0,
    sumSq: 0,
    mouse: 0,
    trackpad: 0,
  };
}
// 按滚动特征推断事件来自哪种设备。浏览器**不**提供直接答案，只能推断：
//   · deltaMode != 0（行/页）→ 刻度滚轮（触控板总是像素模式）
//   · |deltaY| 很大（≥20px）→ 刻度滚轮；很小（≤8px）→ 触控板
//   · 会话内量级方差：触控板持续小而多变，滚轮离散而近乎恒定
// 判定按时序计入会话累计分数，避免单个异常事件把结论整体带偏。
function viewerWheelDeviceKind(event) {
  const now = Number(event.timeStamp) || 0,
    mag = Math.abs(Number(event.deltaY) || 0),
    mode = Number(event.deltaMode) || 0;
  let session = state.viewerWheel;
  if (!session || !(now - session.lastTime <= VIEWER_WHEEL_SESSION_GAP_MS)) {
    resetViewerWheelSession(now);
    session = state.viewerWheel;
  }
  session.lastTime = now;
  session.count += 1;
  session.sum += mag;
  session.sumSq += mag * mag;
  if (mode !== 0) session.mouse += 2;
  if (mag >= VIEWER_WHEEL_MOUSE_DELTA_PX) session.mouse += 2;
  else if (mag <= VIEWER_WHEEL_TRACKPAD_DELTA_PX) session.trackpad += 1;
  if (session.count >= VIEWER_WHEEL_MIN_EVENTS) {
    const mean = session.sum / session.count,
      variance = Math.max(0, session.sumSq / session.count - mean * mean),
      cv = mean > 0 ? Math.sqrt(variance) / mean : 0;
    if (mean < VIEWER_WHEEL_MOUSE_DELTA_PX && cv > VIEWER_WHEEL_TRACKPAD_CV)
      session.trackpad += 2;
    else if (mean >= VIEWER_WHEEL_MOUSE_DELTA_PX && cv < VIEWER_WHEEL_MOUSE_CV)
      session.mouse += 2;
  }
  return session.mouse > session.trackpad ? "mouse" : "trackpad";
}
// 实际生效的设备：用户在设置里可以强制指定，覆盖自动识别。
function viewerWheelDevice(event) {
  const override = (state.settings && state.settings.viewer_wheel_device) || "auto";
  if (override === "mouse" || override === "trackpad") return override;
  return viewerWheelDeviceKind(event);
}
// 该设备的灵敏度，来自设置。取不到时退回 1.0，并夹在滑杆的量程内。
function viewerWheelSensitivity(kind) {
  const settings = state.settings || {},
    key =
      kind === "mouse"
        ? "viewer_wheel_mouse_sensitivity"
        : "viewer_wheel_trackpad_sensitivity",
    value = Number(settings[key]);
  if (!Number.isFinite(value)) return 1;
  return Math.min(
    VIEWER_WHEEL_SENSITIVITY_MAX,
    Math.max(VIEWER_WHEEL_SENSITIVITY_MIN, value),
  );
}
// 一次滚轮事件对应的缩放倍率（>1 放大 / <1 缩小）。
function viewerWheelZoomFactor(event) {
  const kind = viewerWheelDevice(event),
    pixels = viewerWheelPixels(event),
    sensitivity = viewerWheelSensitivity(kind);
  return Math.exp(-pixels * VIEWER_WHEEL_BASE * sensitivity);
}

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
// 说明：曾经有一个「一键跳到真实 1:1」函数（toggleViewerOneToOne），由键盘 `1`
// 触发，并被「查看原图」到货后的自动落位复用。用户裁决撤销 `1` 快捷键后，它只剩
// 自动落位这一个调用点，因此该函数已删除，其落位动作内联进 loadViewerTier 的到货
// 回调（见上文 state.viewerTierAutoOneToOne 分支）。1:1 仍由「查看原图」到货后自动
// 抵达，行为不变。
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
  // 首屏先要预览图（服务端按 w= 缓存的 JPEG），更高档位留给缩放时按需换源。
  // 动态照片的封面本来就是一个提取出来的 JPEG，用原图 URL 即可。
  img.src =
    p.media_type === "motion_photo" || !p.preview_url
      ? p.photo_url
      : p.preview_url;
  // 换源状态必须随照片一起重置：上一张的档位/在途请求对这张没有意义，
  // 留在 state.viewerTierPending 里会让新照片永远显示「细节加载中」。
  clearTimeout(state.viewerTierTimer);
  state.viewerTier = 0;
  state.viewerTierPending = null;
  VIEWER_TIER_FAILED.clear();
  $("#viewerLoading").classList.add("hidden");
  $("#viewerLoading").textContent = VIEWER_LOADING_TEXT;
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
  // 位图解码完成后才知道 naturalWidth / offsetWidth，状态提示要等这一刻，
  // 换源判定同样要等：此刻才知道「首屏这张 2048px 的图够不够铺满当前 scale」。
  const img = $("#viewerImage");
  const settled = () => {
    syncViewerOriginalState();
    syncViewerTier();
    // 打开时也报一次：否则「只打开不缩放」这一路径完全没有数据，而那恰恰是
    // 判断「首屏位图够不够」最关键的一次测量。
    scheduleViewerDiag();
  };
  if (img.complete && img.naturalWidth) settled();
  else img.addEventListener("load", settled, { once: true });
}
// 「已载入原图」= <img> 当前挂的就是 photo_url 本身。
//
// 判定刻意不看尺寸：尺寸法在「原图恰好被缩到预览图大小」时会误判为已载入，
// 于是状态提示声称看的是原始像素，实际却在放大一张预览图——正是要修的毛病。
//
// 也**不能**只比较 "&w=" 之前的前缀。换源时 viewerTierUrl 生成的是
// `photo_url + "&w=2048"`，前缀因此恰好等于 photo_url —— 只要用户在首屏
// 停留（打开即会按需换到 2048 档），中间档就会被误认成原图，于是：
//   ① 「查看原图」按钮自称「原图」（画面其实还是 2048 位图）；
//   ② loadViewerOriginal 认作「已在原图」而早退，原图永远不下发；
//   ③ 落到 1:1 时得到的是**2048 位图的** 1:1（放大约 1.07×），而不是
//      7008 原图的 1:1（约 3.6×）—— 即用户报的「加载原图后 1:1 不对」。
//
// 精确整串比较同时给对两种情形：photo_url 本身 → 真；
// `photo_url&w=NNNN`（客户端中间档）与服务端 preview_url（&w= 夹在
// &token 与 &v 之间）→ 假。
function viewerShowingOriginal() {
  const p = state.items[state.viewerIndex],
    img = $("#viewerImage");
  if (!p || !p.photo_url) return false;
  return (img.getAttribute("src") || "") === p.photo_url;
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
      : state.viewerTier > 0
        ? `当前 ${viewerTierLabel(state.viewerTier)} 画质，放大可自动换源`
        : "";
  }
  renderViewerScaleHint();
}
// 「查看原图」= 直接跳到最高档（原图）。
//
// 换源模型下这个按钮的语义变了：以前是「唯一的清晰来源」，现在缩放会自动换源，
// 它变成「我不想等，直接给我原图」。所以它走的是同一套 loadViewerTier，只是
// 跳过滞回与节流（force），并且到货后自动落到真实 1:1——这是 1:1 唯一的入口。
function loadViewerOriginal() {
  const p = state.items[state.viewerIndex];
  if (!p || state.viewerMotion.active || viewerShowingOriginal()) return;
  if (state.viewerTierPending !== null) return;
  // 动态照片的封面本身就是抽出的 JPEG，无更高档可换。
  if (p.media_type === "motion_photo" || !p.preview_url) return;
  clearTimeout(state.viewerTierTimer);
  syncViewerTier({ force: true });
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
  $("#viewerFit").onclick = resetViewerTransform;
  $("#viewerOriginal").onclick = loadViewerOriginal;
  // 窗口尺寸变了要重排。
  //
  // 缩放改成写 width/height 之后，浏览器不再靠 `.viewer img` 的 max-* 自动把图排回
  // 容器大小——适应窗口的尺寸现在由 JS 算。少了这一条，拉大窗口后图会停在旧尺寸上，
  // 这是改用布局尺寸方案必须自己补上的一课。
  window.addEventListener("resize", () => {
    if (!$("#viewer").open) return;
    clampViewerPan();
    applyViewerTransform();
  });
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
        // 倍率来自「这次滚了多远」与该设备的灵敏度，不再是固定的 ×1.18。
        const factor = viewerWheelZoomFactor(event);
        if (factor === 1) return;
        zoomViewer(factor, event.clientX, event.clientY);
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
