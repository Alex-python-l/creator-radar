# Iris Auth Design QA

## Evidence

- Source visual truth: `/Users/Zhuanz/.codex/generated_images/019f74a6-f7ce-7662-abf3-c9d5888707ff/exec-00210587-a03f-4e6f-9629-b4f2f5c9f674.png`
- Browser implementation: `http://localhost:3000/zh/login`
- Login screenshot: `docs/design-qa/auth-login-1440x1024.webp`
- Register screenshot: `docs/design-qa/auth-register-1440x1024.webp`
- Viewport and state: `1440x1024`, Chinese locale, invite registration enabled
- Full-view comparison: `docs/design-qa/auth-login-full-comparison.webp`
- Focused form and artwork comparison: `docs/design-qa/auth-login-focused-comparison.webp`

## Findings

- No actionable P0, P1, or P2 differences remain.
- Typography: the implementation keeps the source hierarchy and compact form scale. The product font stack is retained; letter spacing remains neutral.
- Spacing and layout: the form uses one 410px content axis without a card, while the flower occupies the right half with a complete flower head and intentional leaf overflow.
- Colors and tokens: cream, iris purple, muted gray, green trend, and small yellow flower accents match the selected direction with sufficient form contrast.
- Image quality: generated WebP assets remain sharp at desktop sizes, transparent flower edges have no visible chroma halo, and `object-contain` prevents subject cropping.
- Copy and content: all authentication copy remains localized; decorative account and alert labels switch with locale and are hidden from assistive technology.

## Comparison History

1. First comparison: `docs/design-qa/auth-login-first-comparison.webp`
   - Earlier P2 findings: the form sat too low, and the flower was too high and large relative to the selected mock.
   - Fixes: moved login content to `14vh` top alignment; reduced and lowered the login flower while preserving the full flower head.
2. Final comparison: `docs/design-qa/auth-login-full-comparison.webp`
   - Post-fix result: visual balance, whitespace, flower completeness, and data-card hierarchy match the selected horizontal garden composition. No P0/P1/P2 findings remain.

## Responsive And Interaction Checks

- Verified without horizontal overflow at `1920x1080`, `1440x1024`, `1280x800`, `768x1024`, `430x932`, and `390x844`.
- Verified login, invite registration, open registration, and closed registration states.
- Verified password visibility toggle, login/register switching, and client-side password mismatch validation.
- Follow-up transition check: login/register switching keeps exactly one flower image mounted, both data cards retain identical coordinates, and six rapid toggles produce no overlap or console diagnostics.
- Browser console checked after the final image-loading update; no new errors or warnings were produced.
- Mobile hides data cards, keeps a low-opacity partial iris, and allows the longer registration form to scroll naturally.

## Implementation Checklist

- [x] Complete horizontal watercolor canvas
- [x] Distinct login and registration flower poses
- [x] Unframed compact form layout
- [x] Localized decorative monitoring data
- [x] Responsive and reduced-motion behavior
- [x] Authentication behavior preserved

## Iris Workspace Design QA

### Evidence

- Selected source: `/Users/Zhuanz/.codex/generated_images/019f74a6-f7ce-7662-abf3-c9d5888707ff/exec-6a16dd9a-0f49-4aa7-ad23-a11653b436b3.png`
- Production Radar: `https://igotyoufrank.cn/en`
- Production Ops: `https://igotyoufrank.cn/en/studio`
- Viewport and state: `1702x921`, English locale, authenticated admin, live production data
- Radar screenshot: `docs/design-qa/iris-workspace-radar-1702x921.jpg`
- Ops screenshot: `docs/design-qa/iris-workspace-ops-1702x921.jpg`
- Selected source and Radar comparison: `docs/design-qa/iris-workspace-radar-comparison.webp`
- Ops before and after comparison: `docs/design-qa/iris-workspace-ops-before-after.webp`

### Findings

- No actionable P0, P1, or P2 differences remain.
- Shared shell: the 232px cream sidebar, serif Iris wordmark, segmented product switcher, active iris markers, watercolor paper field, and botanical edge wash match the selected direction.
- Radar: title and account count share one axis; the group rail, publishing chart, task strip, filters, and table retain the real data workflow while matching the selected hierarchy and density.
- Ops: the existing operational dashboard keeps its live metrics and actions while adopting the same cream, iris-purple, restrained-radius, and translucent-surface system.
- Artwork: the sidebar flower renders at `420x560` with its flower head intact. A global image-width constraint found during QA was explicitly overridden so the botanical composition is not reduced to a cropped leaf fragment.
- Layout: both production pages fit the available viewport without horizontal document overflow. Radar retains natural vertical scrolling for the full account table.

### Comparison History

1. First production comparison found the shared sidebar flower too low and too faint relative to the selected source.
2. The artwork was enlarged and lifted, then a second production measurement showed the browser constraining its width to 231px.
3. The image constraint was corrected with an explicit maximum-width override. The final production comparison shows a complete flower head, visible paper texture, and matching visual balance.

### Interaction And Runtime Checks

- Verified the Monitor to Ops product switch through the rendered control and confirmed the localized production URL.
- Verified Radar and Ops live data, sidebar navigation visibility, and the active product state.
- Verified production responses: login `200`, auth session `200`, unauthenticated Studio API `401`, and Studio health `ok` with database `ok`.
- Chrome production logs contain no application errors or warnings; only extension debug messages were present.
- Re-ran ESLint and TypeScript checks after the final visual fix. The only lint output is the two pre-existing Studio `<img>` performance warnings.

final result: passed

## Iris Botanical Accent System QA

### Evidence

- Generated transparent assets: `apps/web/assets/botanical/iris-cluster.webp`, `iris-sprig.webp`, `iris-petals.webp`, and `iris-meadow.webp`
- Landing: `docs/design-qa/botanical-landing-1440-top.png`, `botanical-landing-1440-pricing.png`, and `botanical-landing-390-top.png`
- Authentication: `docs/design-qa/botanical-auth-login-1440.png`, `botanical-auth-register-1440.png`, and `botanical-auth-login-390.png`
- Application: `docs/design-qa/botanical-apply-1440.png` and `botanical-apply-390.png`
- Workspaces: `docs/design-qa/botanical-monitor-1440.png` and `botanical-ops-1440.png`
- Same-input comparisons: `docs/design-qa/botanical-landing-comparison.webp`, `botanical-auth-comparison.webp`, `botanical-monitor-comparison.webp`, and `botanical-ops-comparison.webp`

### Findings And Corrections

- No actionable P0, P1, or P2 visual differences remain. The existing cream-paper, serif editorial hierarchy, Iris purple, radii, spacing, routes, and information density remain intact.
- The landing now uses distinct cluster, sprig, petal, and meadow compositions across the hero, product proof, pricing, CTA, and footer instead of repeating one flower.
- Login, registration, and application preserve one primary Iris focal point while adding subordinate buds, petals, and distant foliage only around open canvas edges.
- Monitor and Ops use the same accent component at restrained opacity. Decorations stay behind page titles and empty canvas regions and do not overlap charts, tables, filters, error lists, or action controls.
- The first `390x844` hero check found mobile petals sitting too close to the heading. They were moved to the lower-right open area and the final screenshot confirms clean text contrast.
- All four transparent WebP assets have transparent corners, clean watercolor edges, intact subjects, and no visible chroma fringe. Combined size is under 530 KB while retaining source dimensions from 1086 to 2172 pixels.

### Responsive, Interaction, And Runtime Checks

- Checked Chinese and English landing, login, registration, application, Monitor, and Ops routes in the Codex in-app browser.
- Checked desktop `1440x1024` and mobile `390x844`; earlier passes also covered `768x1024` and `430x932`. No horizontal document overflow or broken image remained.
- Verified login-to-registration switching, application form layout, Monitor-to-Ops navigation, and the Ops session controls remain reachable. Decorative images are `aria-hidden`, non-interactive, and behind product controls.
- Verified `prefers-reduced-motion` disables both botanical animation classes.
- Web tests, TypeScript, ESLint, and production build pass. ESLint retains only the two pre-existing Studio `<img>` performance warnings.

final result: passed

## Iris Full-Product Landing Follow-up QA

### Evidence

- Production audit before the correction: `docs/design-qa/landing-product-audit-before-top.png` and `docs/design-qa/landing-product-audit-before-pricing.png`
- Local implementation after the correction: `docs/design-qa/landing-product-after-1440-top.png` and `docs/design-qa/landing-product-after-1440-pricing.png`
- Production implementation after deployment: `docs/design-qa/landing-product-production-1440-top.png`, `docs/design-qa/landing-product-production-1440-pricing.png`, and `docs/design-qa/landing-product-production-390-top.png`
- Responsive checks: `docs/design-qa/landing-product-after-768-top.png`, `docs/design-qa/landing-product-after-430-top.png`, and `docs/design-qa/landing-product-after-390-pricing.png`
- Sanitized Ops evidence asset: `apps/web/assets/marketing/iris-ops-dashboard-demo.png`
- Viewports and state: Chinese public landing, signed out; desktop `1440x1000`, tablet `768x900`, mobile `430x900` and `390x900`

### Findings And Corrections

- P1 product-positioning issue fixed: the landing previously described Iris as Monitor only. The hero now presents Monitor and Ops together with separate real product surfaces, and the workflow proof uses the Ops dashboard.
- P1 pricing issue fixed: every plan now explicitly includes the complete Monitor + Ops product. Tiers remain workspace-based and differ only by shared monitored-account capacity; Ops has no separate seat fee.
- P2 capability coverage fixed: the four capability blocks now cover competitor intelligence, account/proxy/cloud-phone operations, scheduling and four-platform publishing, comment tasks, retries, and operational health.
- P2 product story fixed: the workflow now moves from opportunity discovery to organized operations, publishing and engagement, and performance review.
- The revised monthly prices are Pro `¥699 / $99` and Scale `¥1,999 / $299`; approved trial and Custom remain unchanged.
- The new Ops screenshot contains only fictional, sanitized data and retains the existing Iris visual system instead of exposing production accounts, filenames, groups, or users.

### Responsive And Runtime Checks

- No horizontal document overflow at `1440`, `768`, `430`, or `390`.
- The desktop hero keeps both product surfaces legible; tablet and mobile stack the evidence cards without clipping the content flow.
- Pricing remains four columns on wide desktop and one column at `390`, with all prices, capacity labels, included-product copy, and CTAs visible.
- Web tests, TypeScript, lint, and production build pass. Lint retains only the two pre-existing Studio `<img>` performance warnings.
- Production browser checks confirm both product screenshots load, all four plan links preserve their plan query, the login link targets `/zh/login`, and the console has no errors or warnings.

final result: passed

## Iris Pricing Landing Design QA

### Evidence

- Source visual truth: `/Users/Zhuanz/.codex/generated_images/019f758c-2bab-7351-b935-c4b2d3feb4c4/exec-0811cf74-45f5-488b-9a71-012021878407.png`
- Browser implementation: `http://localhost:3000/zh`
- Viewport and state: Chinese public landing, signed out; desktop `1440x1000`, tablet `768x900`, mobile `430x900` and `390x900`
- Full-view section comparison (source left, browser implementation right): `docs/design-qa/landing-full-comparison.webp`
- Desktop hero: `docs/design-qa/landing-hero-1440x1000.webp`
- Tablet hero: `docs/design-qa/landing-hero-768x900.webp`
- Focused product evidence: `docs/design-qa/landing-product-1440x1000.webp`
- Focused pricing evidence: `docs/design-qa/landing-pricing-1440x1000.webp`
- Mobile hero evidence: `docs/design-qa/landing-hero-430x900.webp` and `docs/design-qa/landing-hero-390x900.webp`
- Mobile pricing evidence: `docs/design-qa/landing-pricing-390x900.webp`
- Form validation evidence: `docs/design-qa/landing-apply-validation-430x900.webp`

### Findings

- No actionable P0, P1, or P2 differences remain.
- Fonts and typography: the serif editorial hierarchy, compact sans-serif UI copy, neutral tracking, and two-line desktop hero match the selected direction; mobile wrapping remains readable at both tested widths.
- Spacing and layout rhythm: the header, hero split, platform strip, rounded capability panel, evidence workflow, pricing cards, CTA, and footer preserve the source order and generous cream-paper spacing. The fourth trial card is an intentional product requirement.
- Colors and tokens: cream, iris purple, lavender surfaces, muted gray, fine borders, and restrained shadows map to existing Iris tokens and the source palette.
- Image quality and asset fidelity: the hero and workflow use a generated sanitized product screenshot; the Iris mark, watercolor field, and botanical art reuse repository assets without CSS-drawn substitutes. Images remain sharp and correctly cropped at all tested breakpoints.
- Copy and content: Chinese and English text only claims implemented monitoring capabilities. Public pricing is exactly trial 20, Pro 50, Scale 200, and Custom 500+ with independent CNY/USD monthly prices.
- Responsive behavior: no horizontal document overflow at `1440`, `768`, `430`, or `390`; the hero stacks cleanly, pricing becomes one column on mobile, and all CTA targets remain visible.

### Comparison History

1. First comparison: `docs/design-qa/landing-first-hero-comparison.webp`
   - Earlier P2 findings: the desktop headline wrapped to three lines, changing the source hierarchy; the first test pass also exposed stale `/monitor` links and form values being cleared after server-side validation.
   - Fixes: widened the hero text track and normalized the display size to restore two lines; corrected monitor/login/group links; preserved submitted form values and selected plan/account range on validation errors.
2. Intermediate comparison found that a dark capability band drifted from the selected light lavender panel.
   - Fix: restored the rounded lavender surface, subtle border, purple icons, and watercolor decoration shown by the visual truth.
3. Final comparison: `docs/design-qa/landing-full-comparison.webp`
   - Post-fix evidence shows aligned hero proportions, section order, capability treatment, product proof, pricing hierarchy, CTA band, and footer. No P0/P1/P2 findings remain.

### Interaction And Runtime Checks

- Verified every landing CTA route and Scale/Custom query preselection in Chinese and English.
- Verified required company/contact fields, email-or-WeChat validation, consent, expected-account selection, selected plan persistence, and preservation of entered values after an error.
- Verified a visible native keyboard focus outline on the Iris header link.
- Verified unauthenticated `/zh/monitor` redirects to login and the public landing/application routes render without authentication.
- Browser console was checked in a fresh tab after the final pass: no errors or warnings.
- Web test suite, TypeScript, lint, and production build were run after implementation.

### Follow-up Polish

- P3: none required for launch; the Next.js development toolbar appears only in local QA screenshots and is absent from production.

final result: passed
