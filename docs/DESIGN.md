# Design system

Read this before writing any chart or picking any colour. The palette below is
validated for colour-vision deficiency and contrast in both themes — do not
substitute values by eye.

## Tokens

Define every colour as a custom property on `:root`, then reference by role.
Declare dark values under **both** a media query and a `[data-theme]` scope so an
explicit toggle wins in either direction.

```css
:root{
  color-scheme: light;
  --s0:#ffffff; --s1:#fcfcfb; --s2:#f5f4f1; --bd:#e4e3de;
  --tp:#0b0b0b; --ts:#52514e; --tm:#84837c;
  /* sequential blue, light -> dark, for magnitude */
  --l0:#f0efec; --l1:#cde2fb; --l2:#9ec5f4; --l3:#5598e7; --l4:#2a78d6; --l5:#104281;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --s0:#121211; --s1:#1a1a19; --s2:#232321; --bd:#34332f;
    --tp:#ffffff; --ts:#c3c2b7; --tm:#8e8d84;
    /* dark-surface steps, chosen for this surface — not an inversion */
    --l0:#2f2f2d; --l1:#184f95; --l2:#256abf; --l3:#3987e5; --l4:#6da7ec; --l5:#9ec5f4;
  }
}
:root[data-theme="dark"]{ /* same dark block, repeated */ }
```

## Categorical series

Assign in this fixed order, never cycled. Cap at eight, then fold into "Other".

| # | Hue | Light | Dark |
|---|---|---|---|
| 1 | blue | `#2a78d6` | `#3987e5` |
| 2 | orange | `#eb6834` | `#d95926` |
| 3 | aqua | `#1baf7a` | `#199e70` |
| 4 | yellow | `#eda100` | `#c98500` |
| 5 | magenta | `#e87ba4` | `#d55181` |
| 6 | green | `#008300` | `#008300` |
| 7 | violet | `#4a3aa7` | `#9085e9` |
| 8 | red | `#e34948` | `#e66767` |

For scatter, bubble or small multiples, only the **first three** clear the
all-pairs separation floor. Past three, facet instead.

## Status

Reserved. Never reused as a series colour, and always paired with an icon or
label so state is never colour-alone.

`good #1baf7a` · `warning #eda100` · `serious #e34948` · `critical #8c1d1c`

## Rules

- Sequential = one hue, light to dark. Diverging = two hues with a neutral grey
  midpoint. Never a rainbow, never a hue at the diverging midpoint.
- **One y-axis per chart.** Two measures of different scale means two charts.
- Colour follows the entity, never its rank. Changing a filter must not repaint
  the surviving series.
- Text wears text tokens, never the series colour. A coloured mark beside the
  label carries identity.
- Legend whenever there are two or more series; direct labels up to four. A single
  series needs no legend — the title names it.
- Every chart needs a hover tooltip and a table equivalent.
- Thin marks, recessive grid and axes, 2px gap between adjacent fills.
- Heatmap intensity uses fourth-root compression (`(v/peak)**0.25`), or one huge
  day flattens the rest of the year.
- `min-width: 0` on grid children. Without it a wide table pushes the page sideways.

## Layout

- Side gutter of at least 16px at every width.
- Only tables, code blocks and the heatmap may scroll horizontally, each inside
  its own `overflow-x: auto` container. The page body never does.
- Verify at 1440, 1024, 768 and 390px. Zero horizontal page overflow is a gate,
  not a preference.
