"""Hand-rolled SVG chart helpers.

Pure functions — server-side render of inline SVG strings. Embed in Jinja
templates with `{{ chart_svg | safe }}`. No JS, no client-side libraries.
Palette mirrors the Excel export (amber for revenue, navy for EBITDA).

Why hand-rolled SVG and not Chart.js or Plotly: M&A analysts review many
parties per day; load time matters. A 23-year line chart needs ~60 lines
of SVG — instant render, zero bundle weight, server-cacheable response.

For interactive analysis (hover tooltips, zoom, export) the analyst opens
the Excel download. Web is a viewer; the workbook is the workbench.
"""
from __future__ import annotations

# V4G palette (matched to financial_export.py)
_AMBER = "#E8A020"
_NAVY_DARK = "#0F1520"
_NAVY = "#1E2D45"
_MUTED = "#64748B"
_GRID = "#E2E8F0"
_ZERO = "#94A3B8"


def revenue_ebitda_svg(rows: list[dict], width: int = 800, height: int = 320) -> str:
    """Render a revenue + EBITDA timeline as inline SVG markup.

    Rows: each must have `period_end` (YYYY-MM-DD or None), `revenue_eur_m`,
    `ebitda_eur_m`. Optional: `period_label` (used for x-axis tick labels).
    Rows are sorted ascending by period_end internally — caller order is
    irrelevant. Rows missing `period_end` are dropped (no anchor on x-axis).
    NULL revenue/EBITDA values create gaps in the line (skipped, not zeroed).

    Returns a complete <svg>...</svg> string. Always returns a valid SVG —
    falls back to a labeled placeholder when there's nothing meaningful
    to plot.
    """
    if not rows:
        return _empty_chart_svg(width, height, "Geen financial data")

    by_year = sorted(
        [r for r in rows if r.get("period_end")],
        key=lambda r: r["period_end"],
    )
    if not by_year:
        return _empty_chart_svg(width, height, "Geen gedateerde periodes")

    valid_revenues = [
        float(r["revenue_eur_m"]) for r in by_year if r.get("revenue_eur_m") is not None
    ]
    valid_ebitdas = [
        float(r["ebitda_eur_m"]) for r in by_year if r.get("ebitda_eur_m") is not None
    ]
    if not valid_revenues and not valid_ebitdas:
        return _empty_chart_svg(width, height, "Geen revenue of EBITDA data")

    # ─── Layout ───────────────────────────────────────────────────────────
    pad_left, pad_right, pad_top, pad_bottom = 60, 30, 30, 50
    chart_x_min = pad_left
    chart_x_max = width - pad_right
    chart_y_min = pad_top
    chart_y_max = height - pad_bottom

    n = len(by_year)
    if n == 1:
        x_positions = [(chart_x_min + chart_x_max) / 2]
    else:
        x_positions = [
            chart_x_min + i / (n - 1) * (chart_x_max - chart_x_min)
            for i in range(n)
        ]

    # ─── Y range ──────────────────────────────────────────────────────────
    # Always include 0 in domain so the zero line is meaningful.
    all_values = valid_revenues + valid_ebitdas + [0.0]
    y_max_raw = max(all_values)
    y_min_raw = min(all_values)
    # Pad the top by 10% so the highest point doesn't touch the frame
    y_max = y_max_raw * 1.1 if y_max_raw > 0 else 1.0
    y_min = y_min_raw * 1.1 if y_min_raw < 0 else 0.0
    if y_max == y_min:
        y_max = y_min + 1.0

    def y_to_svg(value: float) -> float:
        normalized = (value - y_min) / (y_max - y_min)
        return chart_y_max - normalized * (chart_y_max - chart_y_min)

    # ─── Series data ─────────────────────────────────────────────────────
    revenue_points = " ".join(
        f"{x_positions[i]:.1f},{y_to_svg(float(r['revenue_eur_m'])):.1f}"
        for i, r in enumerate(by_year)
        if r.get("revenue_eur_m") is not None
    )
    ebitda_points = " ".join(
        f"{x_positions[i]:.1f},{y_to_svg(float(r['ebitda_eur_m'])):.1f}"
        for i, r in enumerate(by_year)
        if r.get("ebitda_eur_m") is not None
    )

    # ─── Tick marks ──────────────────────────────────────────────────────
    # Y: 5 evenly spaced grid lines + labels
    tick_count = 5
    y_ticks = [
        (y_min + (y_max - y_min) * i / tick_count, None)
        for i in range(tick_count + 1)
    ]
    y_ticks = [(value, y_to_svg(value)) for value, _ in y_ticks]

    # X: first, last, and ~3 intermediate (no clutter)
    label_indices = {0, n - 1}
    if n > 4:
        step = max(1, n // 5)
        label_indices.update(range(step, n - 1, step))
    x_labels = [
        (
            x_positions[i],
            by_year[i].get("period_label") or str(by_year[i]["period_end"])[:4],
        )
        for i in sorted(label_indices)
    ]

    # ─── Compose SVG ─────────────────────────────────────────────────────
    grid_lines = "\n".join(
        f'  <line x1="{chart_x_min}" y1="{y_pos:.1f}" '
        f'x2="{chart_x_max}" y2="{y_pos:.1f}" stroke="{_GRID}" stroke-width="1" />'
        for _, y_pos in y_ticks
    )
    y_label_texts = "\n".join(
        f'  <text x="{chart_x_min - 8}" y="{y_pos + 4:.1f}" '
        f'text-anchor="end" font-size="11" fill="{_MUTED}">{value:.0f}</text>'
        for value, y_pos in y_ticks
    )
    x_label_texts = "\n".join(
        f'  <text x="{x:.1f}" y="{chart_y_max + 18}" text-anchor="middle" '
        f'font-size="11" fill="{_MUTED}">{label}</text>'
        for x, label in x_labels
    )

    zero_line = ""
    if y_min < 0 < y_max:
        zero_y = y_to_svg(0)
        zero_line = (
            f'  <line x1="{chart_x_min}" y1="{zero_y:.1f}" '
            f'x2="{chart_x_max}" y2="{zero_y:.1f}" '
            f'stroke="{_ZERO}" stroke-width="1" stroke-dasharray="3,3" />'
        )

    # Data point dots — only for series with values, no dot for NULL years
    revenue_dots = "\n".join(
        f'  <circle cx="{x_positions[i]:.1f}" '
        f'cy="{y_to_svg(float(r["revenue_eur_m"])):.1f}" r="3" fill="{_AMBER}" />'
        for i, r in enumerate(by_year)
        if r.get("revenue_eur_m") is not None
    )
    ebitda_dots = "\n".join(
        f'  <circle cx="{x_positions[i]:.1f}" '
        f'cy="{y_to_svg(float(r["ebitda_eur_m"])):.1f}" r="3" fill="{_NAVY}" />'
        for i, r in enumerate(by_year)
        if r.get("ebitda_eur_m") is not None
    )

    legend_y = pad_top - 5
    legend_x = width - 200

    return f'''<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" \
class="chart-svg" role="img" aria-label="Revenue and EBITDA timeline in EUR millions">
  <title>Revenue + EBITDA timeline (EUR M)</title>

  <!-- Grid -->
{grid_lines}
{zero_line}

  <!-- Axes -->
  <line x1="{chart_x_min}" y1="{chart_y_min}" x2="{chart_x_min}" y2="{chart_y_max}" \
stroke="{_NAVY_DARK}" stroke-width="1.5" />
  <line x1="{chart_x_min}" y1="{chart_y_max}" x2="{chart_x_max}" y2="{chart_y_max}" \
stroke="{_NAVY_DARK}" stroke-width="1.5" />

  <!-- Y labels -->
{y_label_texts}
  <!-- X labels -->
{x_label_texts}

  <!-- Y axis title -->
  <text x="{chart_x_min - 38}" y="{(chart_y_min + chart_y_max) / 2:.0f}" \
text-anchor="middle" font-size="11" fill="{_MUTED}" \
transform="rotate(-90 {chart_x_min - 38} {(chart_y_min + chart_y_max) / 2:.0f})">EUR M</text>

  <!-- Revenue line + dots -->
  <polyline points="{revenue_points}" fill="none" stroke="{_AMBER}" \
stroke-width="2" stroke-linejoin="round" />
{revenue_dots}

  <!-- EBITDA line + dots -->
  <polyline points="{ebitda_points}" fill="none" stroke="{_NAVY}" \
stroke-width="2" stroke-linejoin="round" />
{ebitda_dots}

  <!-- Legend -->
  <g transform="translate({legend_x}, {legend_y})">
    <rect x="0" y="0" width="190" height="24" fill="white" stroke="{_GRID}" rx="3" />
    <line x1="10" y1="12" x2="30" y2="12" stroke="{_AMBER}" stroke-width="2" />
    <circle cx="20" cy="12" r="2.5" fill="{_AMBER}" />
    <text x="36" y="16" font-size="11" fill="{_NAVY_DARK}">Revenue</text>
    <line x1="98" y1="12" x2="118" y2="12" stroke="{_NAVY}" stroke-width="2" />
    <circle cx="108" cy="12" r="2.5" fill="{_NAVY}" />
    <text x="124" y="16" font-size="11" fill="{_NAVY_DARK}">EBITDA</text>
  </g>
</svg>'''


def _empty_chart_svg(width: int, height: int, message: str) -> str:
    """Fallback placeholder when there's nothing to plot."""
    return f'''<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" \
class="chart-svg chart-empty" role="img" aria-label="{message}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="none" \
stroke="{_GRID}" stroke-width="1" stroke-dasharray="4,4" />
  <text x="{width / 2}" y="{height / 2}" text-anchor="middle" \
font-size="14" fill="{_MUTED}">{message}</text>
</svg>'''
