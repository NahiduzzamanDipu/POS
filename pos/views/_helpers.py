"""Shared view utilities."""

import csv

from django.core.paginator import Paginator
from django.http import HttpResponse

PAGE_SIZE = 20


def paginate(request, queryset, per_page=PAGE_SIZE):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get('page'))


def csv_response(filename, header, rows):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def query_string(request, **overrides):
    """Current query string with some parameters replaced (for pagination links)."""
    params = request.GET.copy()
    for key, value in overrides.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    params.pop('page', None)
    encoded = params.urlencode()
    return f'&{encoded}' if encoded else ''
