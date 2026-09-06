"""Grouped bar charts for the Reports section.

Geometry is computed here from real query results and rendered as inline SVG by
``pos/templates/pos/_chart.html``. That keeps the chart printable, keeps it
working offline (no CDN, no chart library), and keeps the numbers coming from
the database rather than from JavaScript.
"""

from decimal import Decimal

ZERO = Decimal('0.00')

# Series colours, matching the palette already used across the interface.
SERIES_COLOURS = ['#f56a1c', '#2563eb', '#15a05a', '#7c3aed', '#c2760b', '#0891b2']

CHART_WIDTH = 960
CHART_HEIGHT = 300
PADDING_LEFT = 68
PADDING_RIGHT = 16
PADDING_TOP = 16
PADDING_BOTTOM = 46


def _nice_ceiling(value):
    """Round an axis maximum up to a readable number."""
    value = float(value or 0)
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        candidate = magnitude * step
        if candidate >= value:
            return candidate
    return magnitude * 10


def build_grouped_chart(rows, *, label_key, series, title='', empty_message=None):
    """Build a grouped bar chart.

    ``rows``   -- an iterable of dicts (straight from a ``values().annotate()``)
    ``series`` -- ``[(key, label), ...]``; one bar per key within each group

    Returns ``None`` when there is nothing to plot, so templates can simply
    omit the chart rather than drawing an empty box.
    """
    rows = [dict(row) for row in rows]
    if not rows:
        return None

    values = [
        float(row.get(key) or 0) for row in rows for key, _label in series
    ]
    if not any(values):
        return None

    top = _nice_ceiling(max(values))
    plot_width = CHART_WIDTH - PADDING_LEFT - PADDING_RIGHT
    plot_height = CHART_HEIGHT - PADDING_TOP - PADDING_BOTTOM
    group_width = plot_width / len(rows)
    # Leave a gap between groups; split the rest between the bars in a group.
    bar_width = max((group_width * 0.68) / len(series), 3)

    groups = []
    for index, row in enumerate(rows):
        group_x = PADDING_LEFT + index * group_width
        bars = []
        for series_index, (key, series_label) in enumerate(series):
            value = float(row.get(key) or 0)
            height = (value / top) * plot_height if top else 0
            bars.append({
                'label': series_label,
                'value': row.get(key) or ZERO,
                'x': group_x + (group_width - bar_width * len(series)) / 2
                     + series_index * bar_width,
                'y': PADDING_TOP + plot_height - height,
                'width': bar_width,
                'height': max(height, 0),
                'colour': SERIES_COLOURS[series_index % len(SERIES_COLOURS)],
            })
        groups.append({
            'label': str(row.get(label_key) or '—'),
            'centre': group_x + group_width / 2,
            'bars': bars,
        })

    gridlines = []
    for step in range(5):
        fraction = step / 4
        gridlines.append({
            'y': PADDING_TOP + plot_height - fraction * plot_height,
            'value': round(top * fraction, 2),
        })

    return {
        'title': title,
        'width': CHART_WIDTH,
        'height': CHART_HEIGHT,
        'baseline': PADDING_TOP + plot_height,
        'axis_x': PADDING_LEFT,
        'axis_right': CHART_WIDTH - PADDING_RIGHT,
        'groups': groups,
        'gridlines': gridlines,
        'legend': [
            {'label': label, 'colour': SERIES_COLOURS[i % len(SERIES_COLOURS)]}
            for i, (_key, label) in enumerate(series)
        ],
        'rotate_labels': len(rows) > 8,
        'empty_message': empty_message,
    }


def shorten(text, limit=18):
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + '…'


# ---------------------------------------------------------------------------
# Trend and share charts
#
# Same contract as build_grouped_chart: geometry is computed here from real
# query results and drawn as inline SVG by the templates. No chart library and
# no CDN, so the dashboard still renders when the shop's internet is down.
# ---------------------------------------------------------------------------
LINE_HEIGHT = 260
LINE_PADDING_BOTTOM = 40


def build_line_chart(rows, *, label_key, series, title=''):
    """Line/area chart. ``series`` is ``[(key, label), ...]``, one line each.

    Returns ``None`` when there is nothing to plot, so a template can simply
    leave the panel out rather than draw an empty box.
    """
    rows = [dict(row) for row in rows]
    if len(rows) < 2:
        return None

    values = [float(row.get(key) or 0) for row in rows for key, _label in series]
    if not any(values):
        return None

    top = _nice_ceiling(max(values))
    plot_width = CHART_WIDTH - PADDING_LEFT - PADDING_RIGHT
    plot_height = LINE_HEIGHT - PADDING_TOP - LINE_PADDING_BOTTOM
    baseline = PADDING_TOP + plot_height
    step = plot_width / (len(rows) - 1)

    def _x(index):
        return PADDING_LEFT + index * step

    def _y(value):
        return baseline - (float(value or 0) / top) * plot_height if top else baseline

    lines = []
    for series_index, (key, series_label) in enumerate(series):
        points = [
            {
                'x': round(_x(i), 2),
                'y': round(_y(row.get(key)), 2),
                'label': str(row.get(label_key) or ''),
                'value': row.get(key) or ZERO,
            }
            for i, row in enumerate(rows)
        ]
        path = ' '.join(
            f'{"M" if i == 0 else "L"}{p["x"]},{p["y"]}' for i, p in enumerate(points)
        )
        area = (
            f'{path} L{points[-1]["x"]},{baseline} L{points[0]["x"]},{baseline} Z'
        )
        lines.append({
            'label': series_label,
            'colour': SERIES_COLOURS[series_index % len(SERIES_COLOURS)],
            'path': path,
            'area': area,
            'points': points,
            # Only the first series gets a filled area; stacking translucent
            # fills makes every line harder to read, not easier.
            'fill': series_index == 0,
        })

    gridlines = [
        {
            'y': round(baseline - (step_index / 4) * plot_height, 2),
            'value': round(top * step_index / 4, 2),
        }
        for step_index in range(5)
    ]

    # Thin the x labels so they never collide on a long range.
    every = max(1, len(rows) // 12)
    ticks = [
        {'x': round(_x(i), 2), 'label': str(row.get(label_key) or '')}
        for i, row in enumerate(rows)
        if i % every == 0 or i == len(rows) - 1
    ]

    return {
        'title': title,
        'width': CHART_WIDTH,
        'height': LINE_HEIGHT,
        'baseline': baseline,
        'axis_x': PADDING_LEFT,
        'axis_right': CHART_WIDTH - PADDING_RIGHT,
        'lines': lines,
        'gridlines': gridlines,
        'ticks': ticks,
        'legend': [
            {'label': label, 'colour': SERIES_COLOURS[i % len(SERIES_COLOURS)]}
            for i, (_key, label) in enumerate(series)
        ],
    }


DONUT_SIZE = 190
DONUT_STROKE = 26


def build_donut(rows, *, label_key, value_key, title='', centre_label=''):
    """Share-of-total ring. Returns ``None`` when every slice is zero."""
    import math

    rows = [dict(row) for row in rows]
    slices_in = [
        (str(row.get(label_key) or 'Other'), float(row.get(value_key) or 0), row.get(value_key) or ZERO)
        for row in rows
    ]
    slices_in = [s for s in slices_in if s[1] > 0]
    total = sum(s[1] for s in slices_in)
    if not slices_in or total <= 0:
        return None

    radius = (DONUT_SIZE - DONUT_STROKE) / 2
    centre = DONUT_SIZE / 2
    circumference = 2 * math.pi * radius

    segments = []
    offset = 0.0
    for index, (label, value, raw) in enumerate(slices_in):
        fraction = value / total
        length = fraction * circumference
        segments.append({
            'label': label,
            'value': raw,
            'percent': round(fraction * 100, 1),
            'colour': SERIES_COLOURS[index % len(SERIES_COLOURS)],
            # dasharray draws one arc; dashoffset rotates it into place.
            'dash': f'{length:.3f} {circumference - length:.3f}',
            'offset': f'{-offset:.3f}',
        })
        offset += length

    return {
        'title': title,
        'size': DONUT_SIZE,
        'centre': centre,
        'radius': radius,
        'stroke': DONUT_STROKE,
        'segments': segments,
        'total': total,
        'centre_label': centre_label,
    }
