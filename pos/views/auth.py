"""Authentication views (BR-001, use case 1)."""

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from ..forms import LoginForm, RegistrationForm
from ..models import StoreSetting
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
            return redirect(request.GET.get('next') or 'pos:dashboard')
        log_activity(
            None,
            'LOGIN_FAILED',
            'User',
            description=f"username={request.POST.get('username', '')[:40]}",
            request=request,
        )
    return render(request, 'pos/login.html', {'form': form})


def logout_view(request):
    if request.user.is_authenticated:
        log_activity(request.user, 'LOGOUT', 'User', request.user.pk, request=request)
        logout(request)
        messages.info(request, 'You have been signed out.')
    return redirect('pos:login')


@never_cache
@csrf_protect
def register(request):
    """Self-service sign-up (see :class:`~pos.forms.RegistrationForm`)."""
    store = StoreSetting.load()
    if not store.allow_self_registration:
        messages.error(
            request, 'Self-registration is turned off. Ask an administrator for an account.'
        )
        return redirect('pos:login')
    if request.user.is_authenticated:
        return redirect('pos:dashboard')

    requires_approval = store.require_registration_approval
    form = RegistrationForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        user = form.save(requires_approval=requires_approval)
        log_activity(
            user if user.is_active else None,
            'REGISTERED',
            'User',
            user.pk,
            f'{user.username} self-registered as Cashier'
            + (' (awaiting approval)' if requires_approval else ''),
            request=request,
        )
        if requires_approval:
            messages.success(
                request,
                'Your account was created and is waiting for a manager to approve it. '
                'You will be able to sign in once it is enabled.',
            )
            return redirect('pos:login')

        login(request, user)
        messages.success(request, f'Welcome, {user.display_name}.')
        return redirect('pos:dashboard')

    return render(
        request,
        'pos/register.html',
        {'form': form, 'requires_approval': requires_approval},
    )


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
