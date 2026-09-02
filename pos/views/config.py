"""System settings and audit trail (modules 16 and 17)."""

from django.contrib import messages
from django.shortcuts import redirect, render

from ..forms import StoreSettingForm
from ..models import ActivityLog, StoreSetting
from ..permissions import ACTIVITY_VIEW, SETTINGS_MANAGE, require
from ..services import log_activity
from ._helpers import paginate, query_string


@require(SETTINGS_MANAGE)
def settings_view(request):
    store = StoreSetting.load()
    form = StoreSettingForm(request.POST or None, instance=store)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_activity(request.user, 'SETTINGS_UPDATED', 'StoreSetting', 1, request=request)
        messages.success(request, 'Store settings were saved.')
        return redirect('pos:settings')
    return render(
        request,
        'pos/settings.html',
        {'page_title': 'Settings', 'form': form, 'store': store},
    )


@require(ACTIVITY_VIEW)
def activity_log(request):
    logs = ActivityLog.objects.select_related('user')
    action = request.GET.get('action', '').strip()
    user_id = request.GET.get('user', '')
    if action:
        logs = logs.filter(action__icontains=action)
    if user_id.isdigit():
        logs = logs.filter(user_id=int(user_id))

    from ..models import User

    return render(
        request,
        'pos/activity_log.html',
        {
            'page_title': 'Activity Log',
            'page_obj': paginate(request, logs, 40),
            'users': User.objects.all(),
            'selected_action': action,
            'selected_user': user_id,
            'querystring': query_string(request),
        },
    )
