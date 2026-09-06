"""Authentication views (BR-001, use case 1).

Staff sign in with an email address or a phone number; the role comes from the
account, never from the sign-in screen. There is no public sign-up -- accounts
are created by an Administrator or Manager under Employees.
"""

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from ..forms import LoginForm
from ..services import log_activity


@never_cache
@csrf_protect
def login_view(request):
    if request.user.is_authenticated:
        return redirect('pos:dashboard')

    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            log_activity(user, 'LOGIN', 'User', user.pk, request=request)
            messages.success(request, f'Welcome back, {user.display_name}.')
            # The role decides what the account can reach; every signed-in user
            # lands on the dashboard, which itself adapts to their capabilities.
            return redirect(request.GET.get('next') or 'pos:dashboard')
        log_activity(
            None,
            'LOGIN_FAILED',
            'User',
            description=f"identifier={request.POST.get('username', '')[:40]}",
            request=request,
        )
    return render(request, 'pos/login.html', {'form': form})


def logout_view(request):
    if request.user.is_authenticated:
        log_activity(request.user, 'LOGOUT', 'User', request.user.pk, request=request)
        logout(request)
        messages.info(request, 'You have been signed out.')
    return redirect('pos:login')


@login_required
@never_cache
@csrf_protect
def change_password(request):
    """Let a signed-in user change their own password.

    Uses Django's ``PasswordChangeForm``, so the current password is verified
    and the new one goes through the configured validators and hashers. The
    session is re-keyed afterwards so the user is not logged out.
    """
    form = PasswordChangeForm(request.user, request.POST or None)
    for field in form.fields.values():
        field.widget.attrs.setdefault('class', 'input')

    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)   # keep this session signed in
        log_activity(user, 'PASSWORD_CHANGED', 'User', user.pk, request=request)
        messages.success(request, 'Your password has been changed.')
        return redirect('pos:dashboard')

    return render(request, 'pos/change_password.html', {
        'page_title': 'Change Password',
        'form': form,
    })
