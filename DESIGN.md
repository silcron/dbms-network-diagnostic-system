> 이전 설계·실험 기록입니다. 현재 DB 3개 확장본은 [README](README.md), 최신 증빙은 [최종 실행 자료](evidence/final-20261114/)를 참고하세요.

# DBMS Network Diagnostic System — Preserved Design Contract

This contract records the existing interface before Task 3 presentation changes. It is extracted from `app/static/index.html`, `app/static/style.css`, and the baseline captures under `.omo/evidence/upstream-adoption/qa-setup-baseline/`. The approved direction is preservation: new diagnostic facts use the current result containers and do not introduce a new layout, palette, component style, or motion language.

## 1. Atmosphere & Identity

The interface is a restrained classroom diagnostic sheet: white paper on a cool gray field, dark technical typography, fine rules, and one muted brown accent. Its signature is the explicit browser → web client → diagnostic server → DBMS route, followed by a compact status summary and seven-layer trace.

## 2. Color

| Role | Existing token | Value | Usage |
|---|---|---:|---|
| Text / primary | `--ink` | `#18232e` | Headings, values, controls |
| Text / secondary | `--muted` | `#64717c` | Descriptions, labels, trace details |
| Border / rule | `--line` | `#dce1e5` | Dividers, inputs, cards, grids |
| Accent | `--accent` | `#8a6949` | Eyebrow, section headings, route emphasis |
| Surface / paper | `--paper` | `#ffffff` | Main sheet, controls, cards |
| Surface / field | `--soft` | `#f7f8f8` | Page background, disabled select |
| Status / normal | existing selector | `#23724a` | NORMAL result |
| Status / warning | existing selector | `#a56a08` | WARNING result |
| Status / down | existing selector | `#b43b3b` | DOWN result |

No new color is required for Task 3. Phase, cause, and timing text use the existing secondary text treatment.

## 3. Typography

- Primary stack: `"Noto Sans KR", "Malgun Gothic", sans-serif`.
- Display: 36px, bold browser default weight, `-0.04em` tracking.
- Section heading: 17px, accent color.
- Summary value: 20px, bold.
- Body: browser default 16px.
- Secondary body and trace: 14px.
- Labels and timing: 13px.
- Captions and notes: 12px; route-node detail is 11px.
- Existing Korean wrapping and font metrics are the reference. Task 3 adds short inline facts only.

## 4. Spacing & Layout

- Desktop sheet: maximum width 980px, 48px outer margin, 48px inner padding.
- Mobile breakpoint: 700px; the sheet becomes full width with 28px vertical and 20px horizontal padding.
- Form and route spacing use the existing 4px-derived values: 4, 8, 12, 16, 20, 24, 28, 32, and 48px.
- Desktop route: four equal nodes in one row with arrows. Mobile route: one column with rotated arrows.
- Desktop summary: four equal columns. Mobile summary: two columns.
- OSI rows: 150px label plus fluid text on desktop; one column on mobile.
- Task 3 keeps this geometry unchanged and places new optional text inside `.detail`.

## 5. Components

### Connection controls

- Structure: host input, port input, LIVE/DEMO radio group, DEMO scenario select, primary diagnose button.
- States: default, checked mode, disabled scenario, disabled/loading button, browser-native focus.
- Accessibility: visible labels, fieldset/legend for mode, native controls, no custom keyboard behavior.

### Route flow

- Structure: four bordered nodes joined by directional arrows and a caption.
- Variants: MariaDB LIVE target or explicitly simulated DEMO target.
- Responsive behavior: horizontal desktop flow and vertical mobile flow.

### Result summary

- Structure: four cells for status, mode, client RTT, and timestamp.
- Variants: NORMAL, WARNING, DOWN status color only.
- State: hidden until a successful diagnostic response is rendered.

### Diagnostic detail

- Structure: database name, explanation, and one muted optional-facts line.
- Task 3 rule: connect/auth, query, total, failed phase, cause, and DB error number are appended only when valid and present. Older payloads leave the line empty. DEMO timings, if ever present, are labelled as simulated.

### OSI trace

- Structure: ordered seven-row trace with bold layer name, explanation, and a truthfulness note.
- State: replaced atomically for each successful result.

## 6. Motion & Interaction

The current surface has no decorative animation. Interaction feedback is immediate: mode selection changes labels and help copy, the diagnose button disables during work, and the result appears after success. Task 3 adds no animation. Browser-native focus and reduced-motion behavior remain unchanged.

## 7. Depth & Surface

The existing strategy is mixed but restrained: thin borders divide controls and data, while the desktop paper sheet uses one soft shadow (`0 18px 50px rgba(25,35,45,.08)`). Mobile removes the surrounding field by making the sheet full width. Task 3 adds no surface or elevation.

## 8. Accessibility Constraints & Accepted Debt

- Preserve Korean document language, native form semantics, fieldset grouping, `aria-live="polite"` on results, and readable single-column reflow at 375px.
- New diagnostic facts must be text content, must omit invalid/nonfinite values, and must not rely on color alone.
- Error messages remain visible in the existing notice region; the diagnose button is re-enabled after every outcome.
- Accepted debt for this scoped change: the existing interface relies on browser-native focus styling and uses implicit spacing values rather than a complete CSS spacing-token layer. The approved preservation constraint leaves those pre-existing choices unchanged.
