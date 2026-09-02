"""Grouped bar charts for the Reports section.

Geometry is computed here from real query results and rendered as inline SVG by
``pos/templates/pos/_chart.html``. That keeps the chart printable, keeps it
working offline (no CDN, no chart library), and keeps the numbers coming from
the database rather than from JavaScript.
"""

from decimal import Decimal

ZERO = Decimal('0.00')

# Series colours, matching the palette already used across the interface.
SERIES_COLOURS = ['#2563eb', '#16a34a', '#b45309', '#7c3aed']

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
