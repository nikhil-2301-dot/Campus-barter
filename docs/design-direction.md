# Design direction: "Drafting table" (approved by the user in C6)

Source of truth for how Campus Barter looks. Code lives in `app/static/css/tokens.css` and `base.css`.
A test (tests/test_design_tokens.py) keeps this file, the tokens and the contrast rules in sync.

## Brief in 3 lines
- **Purpose:** help students find, offer and swap study resources (books, notes, PYQs books, mini drafters, lab coats, calculators) inside their college community.
- **Audience:** engineering students, mostly first to final year, using a desktop browser, often in a hurry before an exam or practical.
- **Direction:** an engineer's drafting table. Pale graph-paper ground, ink-navy lettering, pencil-yellow highlights. Information is laid out like a drawing sheet's title block: ruled cells that carry real facts.

## Palette (every colour that exists; nothing else is allowed in CSS)
| Token | Hex | Role |
|-------|-----|------|
| paper | #EEF1EC | page ground (pale graph-paper grey-green) |
| sheet | #FBFCFA | surfaces that hold content (cards, forms, the main sheet) |
| ink | #1D2B4F | body text, headings, strong rules |
| ink-muted | #4A5675 | secondary text (still AA on paper and sheet) |
| pencil | #F2C14E | highlight fill only; text on it is always ink |
| link | #1F4FA3 | links |
| action | #1D2B4F | primary button fill |
| action-hover | #14203C | primary button hover |
| on-action | #FBFCFA | text on the primary button |
| control-border | #6F7A8F | input and control outlines (3:1 or better) |
| focus | #1D2B4F | keyboard focus ring |
| grid | #DCE2DA | graph-paper lines (decoration only, never carries meaning) |
| line | #B9C2BB | decorative dividers (never the only boundary of a control) |
| ok | #1B5E3B | success text |
| ok-bg | #E3F1E8 | success tint |
| danger | #8F1F1F | error text |
| danger-bg | #FBE7E5 | error tint |
| info | #1D4E9A | information text |
| info-bg | #E4ECF8 | information tint |
| warning | #5C4300 | warning text |
| warning-bg | #FCEFC4 | warning tint |
| brand | #04386F | landing page navy (header, hero, why-us card) |
| brand-yellow | #FDE08B | landing page highlight and primary landing button |
| brand-blue | #4D9BF5 | landing page secondary button and icons |
| brand-green | #16A34A | landing page "offer" chips and icons |
| brand-orange | #F97316 | landing page "need" chips and icons |
| brand-mist | #F6F9FC | landing page alternate section ground |
| brand-blue-tint | #E3EEFC | landing page tint behind blue items |
| brand-green-tint | #E4F5EA | landing page tint behind green items |
| brand-orange-tint | #FFEBDD | landing page tint behind orange items |
Contrast is computed in the test: body text on paper and sheet >= 7:1 (AAA), all other text >= 4.5:1 (AA),
control borders and the focus ring >= 3:1.

## Type
- **Headings: Barlow Condensed** (600/700). Narrow, technical, reads like drawing-sheet lettering.
- **Body and forms: IBM Plex Sans** (400/500/600). Plain and very legible at small sizes.
- Fallbacks: "Arial Narrow"/sans-serif for headings, system-ui for body. The page must look acceptable with fallbacks only.
- Loaded from Google Fonts with `display=swap`. No monospace face for labels.
- Scale (rem): 0.8125, 0.9375, 1, 1.125, 1.4, 1.75, 2.25, 3. Body line-height 1.55, headings 1.15, line length max 68ch. Sentence case everywhere: no all-caps labels, no tracked-out eyebrows.

## Layout concept
Page = one large "sheet" with a strong 2px ink border on a graph-paper ground.
Listings and request details use the **title block**: a ruled grid of labelled cells, each cell one fact.
```
+--------------------------------------------------+
| Mini drafter (Omega)                  [Available]|   <- title row: item name + status stamp
+-------------------+------------------------------+
| Item type         | Mini drafter                 |
| Condition         | Good                         |
| Offered as        | Lend                         |
| Owner             | A. Sharma, Third year        |
+-------------------+------------------------------+
| Wanted in return  | Engineering mathematics book |
+--------------------------------------------------+
```
Alignment: left aligned text; forms in a single column; tables left aligned with numbers right aligned.
Shape: panels have square corners; controls 3px; dialogs 8px; status stamps 2px. Not one radius for everything. No drop shadows: depth comes from rules and ground contrast.

## Principles
1. **One memorable thing: the title block.** Everything else stays quiet.
2. **Structure carries information.** A rule or border exists because it separates facts. Numbers appear only for real sequences (the request steps: sent, accepted, completed).
3. **Status looks like a stamp** (small ruled rectangle, ink text on a tint), never a coloured pill.
4. **Motion is rare.** One page-load reveal on the landing page (C18) and motion that answers a click (dialogs opening). Everything is off under prefers-reduced-motion.
5. **Quality floor on every page:** visible keyboard focus, labels on every input, reduced motion respected, AA contrast.

## Review against generic defaults (done in C6, as the frontend-design skill asks)
- Cream ground + high-contrast serif + terracotta accent: NOT used (cool grey-green ground, sans type, navy/yellow).
- Near-black with a single acid accent: not used.
- Broadsheet look with hairlines and zero radius everywhere: avoided (mixed radii, ruled cells carry facts).
- Identical rounded cards with soft shadows and gradients: avoided (no shadows, no gradients).
- Template chrome (all-caps eyebrows, middle-dot meta strings, mono labels, arrows on buttons): avoided by rule.

## Words (copy rules)
Sentence case. Buttons say what happens: "Post an item", "Send request", "Mark as returned". The same action keeps the same name in the button and in the message after it. Errors say what went wrong and how to fix it, with no apology. Empty screens invite the next action.

## Decisions and limits
- Light mode only for v1 (a dark theme can be added later by redefining the colour tokens).
- Web page for desktop browsers; the layout is fluid but there is no dedicated mobile design.
- Google Fonts needs the C14 content-security-policy to allow fonts.googleapis.com and fonts.gstatic.com (note for C14).
- Page layout (done in C8): one bordered sheet (`.page`) under the site header on the graph-paper ground, a narrow column (`.narrow`) for forms, a footer, and a skip link. Pages start with `page_header()`.
