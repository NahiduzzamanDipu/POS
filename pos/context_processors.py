"""Template context shared by every page."""

from pathlib import Path

from django.conf import settings

from .models import StoreSetting
from .permissions import capabilities_for, navigation_for

_ASSET_DIR = Path(__file__).resolve().parent / 'static' / 'pos'


def _asset_version():
    """Newest mtime across our CSS/JS, used to bust the browser cache.

    Without this a stylesheet change can sit invisible behind a cached copy,
    which looks exactly like the change never happened.
    """
    try:
        stamps = [f.stat().st_mtime for f in _ASSET_DIR.rglob('*') if f.is_file()]
        return str(int(max(stamps))) if stamps else '1'
    except OSError:
        return '1'


_CACHED_VERSION = _asset_version()


def _mark_active(items, path):
    """Highlight the deepest nav entry whose URL prefixes the current path.

    A parent (Reports) also counts as active when one of its children is open,
    so the submenu stays expanded on every report page.
    """
    candidates = []
    for item in items:
        candidates.append(item)
        candidates.extend(item.get('children', []))

    best = None
    for item in candidates:
        if path == item['url'] or (item['url'] != '/' and path.startswith(item['url'])):
            if best is None or len(item['url']) > len(best['url']):
                best = item

    for item in items:
        children = item.get('children', [])
        for child in children:
            child['is_active'] = child is best
        item['is_active'] = item is best
        item['is_open'] = bool(children) and (
            item is best or any(c['is_active'] for c in children)
        )
    return items


def store_context(request):
    store = StoreSetting.load()
    user = getattr(request, 'user', None)
    signed_in = bool(user and user.is_authenticated)
    nav = _mark_active(navigation_for(user), request.path) if signed_in else []
    return {
        'store': store,
        'currency': store.currency_symbol,
        'nav_items': nav,
        'capabilities': capabilities_for(user) if signed_in else frozenset(),
        # Recomputed every request while developing so edits show immediately.
        'asset_version': _asset_version() if settings.DEBUG else _CACHED_VERSION,
    }
