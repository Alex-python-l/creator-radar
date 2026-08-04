# 工作台鸢尾花可见度 QA（2026-07-20）

## 范围

- Monitor：页头花枝 `14%`、页头花瓣 `12%`、额度/空状态花瓣 `15%`。
- Ops：页头花枝 `14%`、页头花瓣 `12%`。
- 认证工作台共享壳层：侧栏花簇 `24%`、花枝 `15%`、花瓣 `12%`；内容区花枝 `12%`、花瓣与底部叶丛 `10%`。
- Admin 实际使用独立壳层，原本没有装饰实例；本次复用同一组现有素材与上述共享壳层数值，未新增素材或业务接口。

## 视觉结论

- 在 `1440×1024`、`768×1024`、`430×932`、`390×844` 下，Monitor、Ops、Admin 均无 document 级横向溢出。
- 花簇和边缘植物已从近似水印提升为可识别的品牌层，仍位于内容层之后；标题、表格、卡片和按钮对比度未受影响。
- 所有装饰图片均为 `aria-hidden` 且计算样式 `pointer-events: none`；Monitor 导入弹窗、时间筛选、Monitor/Ops 切换、Ops 会话切换与 Admin 用户筛选均可正常操作。
- 现有固定宽度侧栏在窄屏继续压缩内容画布，数据区通过原有横向滚动容器访问；本次按范围不改变布局、尺寸或信息密度。
- `prefers-reduced-motion: reduce` 仍由全局样式关闭 `botanical-drift` 与 `botanical-sway`。
- QA 同时发现 Monitor 空分组标题误从 `accountsPage` 命名空间取值；已改为既有 `groups.allMixed` 文案，避免部署后显示原始翻译键。

## 对比与截图

- Monitor 前后对比：`botanical-visibility-monitor-comparison.webp`
- Ops 前后对比：`botanical-visibility-ops-comparison.webp`
- 各页面四档截图：`botanical-visibility-{monitor,ops,admin}-{1440x1024,768x1024,430x932,390x844}.png`

对比结果：新版本在不改变构图的前提下显著提高了侧栏主花、页头花枝和底部叶丛的辨识度；未发现 P0/P1/P2 视觉回归。

## 自动化验证

- Web tests：通过（155 tests）。
- TypeScript：通过。
- ESLint：通过；保留 2 条既有 Studio `<img>` 警告。
- Production build：通过。
