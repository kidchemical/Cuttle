# Charts & tables in Cuttle chat

Open this when rendering tabular data or plots in a Cuttle chat reply.

## Tables

Use GitHub-flavored markdown pipe tables (rendered as HTML):

```markdown
| Enemy | Damage | Max HP |
|---|---:|---:|
| Rat | 5 | 20 |
```

Do **not** use ASCII / Vega text grids for ordinary tables when markdown tables suffice.

## Charts / plots

Prefer a real chart over “imagine a chart”. Emit Vega-Lite JSON in a `<vega>` block
(or a fenced `vega` / `vega-lite` code fence):

```xml
<vega>
{"$schema":"https://vega.github.io/schema/vega-lite/v5.json","description":"…",
 "data":{"values":[{"x":"A","y":12},{"x":"B","y":8}]},
 "mark":"bar","encoding":{"x":{"field":"x","type":"nominal"},"y":{"field":"y","type":"quantitative"}}}
</vega>
```

Allowed marks: `bar` | `line` | `point` | `area` | `arc` | `rect` | `text` | `boxplot`.
