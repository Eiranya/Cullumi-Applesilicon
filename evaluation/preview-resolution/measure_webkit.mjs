// 预览分辨率排查：真实 WebKit 复现照片库卡片，量化浏览器重采样环节。
//
// 只读：把一张缩略图读成 base64 塞进 HTML，不写仓库、不连应用。
//
// 用法：
//   cd /Users/inori95/.workbuddy/binaries/node/workspace
//   THUMB_FILE=<缩略图路径> \
//   NODE_PATH=$PWD/node_modules \
//   /Users/inori95/.workbuddy/binaries/node/versions/22.22.2-3/bin/node \
//     /Users/inori95/WorkBuddy/编程任务/cullumi-macos/evaluation/preview-resolution/measure_webkit.mjs
//
// 输出 measurements-webkit.json，并把逐项渲染图写进 <out>/renders/。

import { readFileSync, writeFileSync, mkdirSync, existsSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

// ESM 不认 NODE_PATH，按绝对路径解析工作区里的 playwright-core
const require = createRequire(
  '/Users/inori95/.workbuddy/binaries/node/workspace/package.json');
const { webkit } = require('playwright-core');

// pathname 会做百分号编码，中文路径必须用 fileURLToPath 还原
const OUT = dirname(fileURLToPath(import.meta.url));
// 结果目录沿用上游约定：evaluation/performance-results 已被 .gitignore 忽略
const RESULTS = join(OUT, '..', 'performance-results', 'preview-resolution');
const RENDERS = join(RESULTS, 'renders');
if (!existsSync(RENDERS)) mkdirSync(RENDERS, { recursive: true });

const WEBKIT = '/Users/inori95/Library/Caches/ms-playwright/webkit-2358/pw_run.sh';
const THUMB = process.env.THUMB_FILE;
if (!THUMB) {
  console.error('需要 THUMB_FILE=<缩略图路径>');
  process.exit(1);
}

const b64 = readFileSync(THUMB).toString('base64');
const DATA_URI = `data:image/jpeg;base64,${b64}`;

// 与 web/css/base.css:411-454 一致的卡片布局
function html({ src, imageRendering = '' }) {
  return `<!doctype html><meta charset="utf-8"><style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #f5f5f7; font: 13px -apple-system; }
  .gallery {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 14px;
    padding: 0 40px;
  }
  .card { background: #fff; border-radius: 10px; overflow: hidden; }
  .thumb { aspect-ratio: 4 / 3; background: #ececf0; overflow: hidden; }
  .thumb img {
    width: 100%; height: 100%; object-fit: contain; display: block;
    ${imageRendering ? `image-rendering: ${imageRendering};` : ''}
  }
  </style>
  <div class="gallery">
    ${Array.from({ length: 6 }, () => `<div class="card"><div class="thumb">
      <img src="${src}"></div></div>`).join('')}
  </div>`;
}

const boxW = 1212; // 得到 213.19 CSS px 列宽，与截屏实测一致

async function newPage(browser, dpr) {
  const ctx = await browser.newContext({
    viewport: { width: boxW, height: 900 },
    deviceScaleFactor: dpr,
  });
  const page = await ctx.newPage();
  return { ctx, page };
}

async function geometry(page) {
  await page.waitForFunction(() =>
    [...document.images].every((i) => i.complete && i.naturalWidth > 0));
  return page.evaluate(() => {
    const img = document.querySelector('.thumb img');
    const r = img.getBoundingClientRect();
    const cs = getComputedStyle(img);
    return {
      cssBox: { w: r.width, h: r.height, x: r.x, y: r.y },
      natural: { w: img.naturalWidth, h: img.naturalHeight },
      devicePixelRatio: window.devicePixelRatio,
      imageRendering: cs.imageRendering,
      columnWidth: getComputedStyle(document.querySelector('.card')).width,
    };
  });
}

async function renderCrop(page, g, file, dpr) {
  const clip = {
    x: g.cssBox.x, y: g.cssBox.y,
    width: g.cssBox.w, height: g.cssBox.h,
  };
  // clip 用 CSS px，playwright 会按 dpr 放大输出 -> 得到设备像素图
  await page.screenshot({ path: file, clip });
  return file;
}

const res = { viewport: boxW, webkit: WEBKIT, thumbFile: THUMB };

const browser = await webkit.launch({ executablePath: WEBKIT });

// ---- 1) 卡片图片框的实际尺寸（是否非整数 -> 触发小数布局重采样）---------
{
  const { ctx, page } = await newPage(browser, 1);
  await page.setContent(html({ src: DATA_URI }));
  const g = await geometry(page);
  res.geometry_dpr1 = g;
  res.geometry_dpr1.fractional = Number.isInteger(g.cssBox.w) ? 'integer'
    : 'fractional';
  res.geometry_dpr1.oversample = +(g.natural.w / g.cssBox.w).toFixed(2);
  await ctx.close();
}

// ---- 2) DPR 1 vs 2：同一张 512 源，盒内需要的设备像素差一倍 -------------
for (const dpr of [1, 2]) {
  const { ctx, page } = await newPage(browser, dpr);
  await page.setContent(html({ src: DATA_URI }));
  const g = await geometry(page);
  await renderCrop(page, g, join(RENDERS, `dpr${dpr}.png`), dpr);
  res[`dpr${dpr}`] = {
    devicePxNeeded: Math.round(g.cssBox.w * dpr),
    oversample: +(g.natural.w / (g.cssBox.w * dpr)).toFixed(2),
    render: join(RENDERS, `dpr${dpr}.png`),
  };
  await ctx.close();
}

// ---- 3) image-rendering 各模式 -----------------------------------------
for (const mode of ['', 'auto', 'high-quality', '-webkit-optimize-contrast',
                    'pixelated']) {
  const { ctx, page } = await newPage(browser, 1);
  await page.setContent(html({ src: DATA_URI, imageRendering: mode }));
  const g = await geometry(page);
  const name = mode === '' ? 'default' : mode.replace(/^-webkit-/, 'webkit-');
  await renderCrop(page, g, join(RENDERS, `ir-${name}.png`), 1);
  res[`image_rendering_${name}`] = {
    effective: g.imageRendering,
    render: join(RENDERS, `ir-${name}.png`),
  };
  await ctx.close();
}

// ---- 4) 源尺寸扫描：源越大屏上是否越锐？--------------------------------
// 由外部脚本先生成各尺寸 PNG，这里只负责渲染进同一个固定盒
const sizes = (process.env.SIZES || '').split(',').filter(Boolean);
if (sizes.length) {
  const sweepDpr = Number(process.env.SWEEP_DPR || 1);
  res.size_sweep_dpr = sweepDpr;
  res.size_sweep = {};
  const dir = process.env.SIZES_DIR;
  for (const label of sizes) {
    const file = join(dir, `s${label}.png`);
    if (!existsSync(file)) continue;
    const uri = `data:image/png;base64,${readFileSync(file).toString('base64')}`;
    const { ctx, page } = await newPage(browser, sweepDpr);
    await page.setContent(html({ src: uri }));
    const g = await geometry(page);
    await renderCrop(page, g, join(RENDERS, `sz${label}@dpr${sweepDpr}.png`),
                     sweepDpr);
    res.size_sweep[label] = {
      natural: g.natural.w,
      devicePxNeeded: Math.round(g.cssBox.w * sweepDpr),
      oversample: +(g.natural.w / (g.cssBox.w * sweepDpr)).toFixed(2),
      render: join(RENDERS, `sz${label}@dpr${sweepDpr}.png`),
    };
    await ctx.close();
  }
}

// ---- 5) 严格对照：无留边的 215×143 盒，逐算法渲染 ----------------------
// 卡片用 object-fit:contain，会在 4:3 盒里留上下底色边，干扰清晰度比较。
// 这里去掉留边，让 img 的盒子恰好等于内容尺寸（215 x 143，512:341 的比例），
// 于是渲染结果可以和 PIL 的 512->(215,143) 逐像素对照。
function htmlTight({ src, imageRendering = '', boxW = 215, boxH = 143 }) {
  return `<!doctype html><meta charset="utf-8"><style>
  body { margin: 0; background: #ffffff; }
  img { display: block; width: ${boxW}px; height: ${boxH}px;
        ${imageRendering ? `image-rendering: ${imageRendering};` : ''} }
  </style><img src="${src}">`;
}

{
  res.tight = { boxCssPx: [215, 143], variants: {} };
  for (const mode of ['', '-webkit-optimize-contrast']) {
    for (const dpr of [1, 2]) {
      const { ctx, page } = await newPage(browser, dpr);
      await page.setContent(htmlTight({ src: DATA_URI, imageRendering: mode }));
      await page.waitForFunction(() => {
        const i = document.querySelector('img');
        return i && i.complete && i.naturalWidth > 0;
      });
      const g = await page.evaluate(() => {
        const i = document.querySelector('img');
        const r = i.getBoundingClientRect();
        return { w: r.width, h: r.height, dpr: window.devicePixelRatio };
      });
      await page.screenshot({
        path: join(RENDERS, `tight-ir${mode || 'default'}@dpr${dpr}.png`),
        clip: { x: 0, y: 0, width: g.w, height: g.h },
      });
      const key = `${mode || 'default'}@dpr${dpr}`;
      res.tight.variants[key] = {
        devicePx: [Math.round(g.w * g.dpr), Math.round(g.h * g.dpr)],
        render: join(RENDERS, `tight-ir${mode || 'default'}@dpr${dpr}.png`),
      };
      await ctx.close();
    }
  }
}

// ---- 6) 整数盒 vs 小数盒：验证小数布局是否真的损失清晰度 ---------------
// 真实卡片列宽 = repeat(auto-fill, minmax(210px,1fr)) 在 1212px 视口下的
// 结果。210 是整数，但 1fr 分配后得到 215.19px，不是整数像素。浏览器要
// 把 512px 的源缩进一个 215.19 CSS px 的盒子，落点不在整像素上。
{
  res.integer_vs_fractional = {};
  for (const [label, w] of [['int215', 215], ['frac215_19', 215.19],
                            ['int216', 216], ['frac215_5', 215.5]]) {
    const { ctx, page } = await newPage(browser, 1);
    await page.setContent(htmlTight({
      src: DATA_URI, boxW: w, boxH: Math.round(w * 341 / 512) }));
    await page.waitForFunction(() => {
      const i = document.querySelector('img');
      return i && i.complete && i.naturalWidth > 0;
    });
    const g = await page.evaluate(() => {
      const i = document.querySelector('img');
      const r = i.getBoundingClientRect();
      return { w: r.width, h: r.height };
    });
    await page.screenshot({
      path: join(RENDERS, `box-${label}.png`),
      clip: { x: 0, y: 0, width: g.w, height: g.h },
    });
    res.integer_vs_fractional[label] = {
      cssWidth: g.w, cssHeight: g.h,
      render: join(RENDERS, `box-${label}.png`),
    };
    await ctx.close();
  }
}

await browser.close();
writeFileSync(join(RESULTS, 'measurements-webkit.json'),
  JSON.stringify(res, null, 2), 'utf8');
console.log(JSON.stringify(res, null, 2));
