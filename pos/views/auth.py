"""Authentication views (BR-001, use case 1).

Staff sign in with an email address or a phone number; the role comes from the
account, never from the sign-in screen. There is no public sign-up -- accounts
are created by an Administrator or Manager under Employees.

The login form is the one page an attacker can always reach, so it is rate
limited. See :func:`_lockout_remaining`.
"""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from ..forms import LoginForm
from ..models import ActivityLog
from ..services import client_ip, log_activity

# Brute-force limits. Generous enough that a cashier fumbling their password
# on a busy morning is not locked out, tight enough that guessing a password
# over the internet is hopeless: 10 tries per quarter hour is roughly 350 a
# day against a password space of billions.
MAX_FAILED_ATTEMPTS = 10
LOCKOUT_WINDOW = timedelta(minutes=15)


def _recent_failures(request):
    """Failed sign-ins from this address inside the lockout window.

    Counted from ActivityLog rather than the cache deliberately: it is shared
    across the several worker processes a host runs, and it survives a restart,
    neither of which is true of the default local-memory cache.
    """
    ip = client_ip(request)
    if not ip:
        return ActivityLog.objects.none()
    return ActivityLog.objects.filter(
        action='LOGIN_FAILED',
        ip_address=ip,
        created_at__gte=timezone.now() - LOCKOUT_WINDOW,
    )


def _lockout_remaining(request):
    """Minutes left on a lockout, or 0 when this address may still try."""
    failures = _recent_failures(request)
    if failures.count() < MAX_FAILED_ATTEMPTS:
        return 0

    oldest = failures.order_by('created_at').first()
    if oldest is None:
        return 0
    unlocks_at = oldest.created_at + LOCKOUT_WINDOW
    remaining = (unlocks_at - timezone.now()).total_seconds() / 60
    return max(int(remaining) + 1, 1)


@never_cache
@csrf_protect
def login_view(request):
    if request.user.is_authenticated:
        return redirect('pos:dashboard')

    locked_for = _lockout_remaining(request) if request.method == 'POST' else 0

    if locked_for:
        # Refuse before touching the password, so a locked-out attacker learns
        # nothing about whether the guess was right.
        messages.error(
            request,
            f'Too many failed sign-in attempts. Try again in {locked_for} '
            f'minute{"" if locked_for == 1 else "s"}, or ask an administrator '
            'to reset your password.',
        )
        return render(request, 'pos/login.html', {'form': LoginForm(request), 'locked': True})

    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            log_activity(user, 'LOGIN', 'User', user.pk, request=request)
            messages.success(request, f'Welcome back, {user.display_name}.')

            _warn_about_a_default_password(request, user)

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

        # Warn on the last couple of attempts rather than locking without notice.
        left = MAX_FAILED_ATTEMPTS - _recent_failures(request).count()
        if 0 < left <= 3:
            messages.warning(
                request,
                f'{left} attempt{"" if left == 1 else "s"} left before this '
                'address is locked out for 15 minutes.',
            )

    return render(request, 'pos/login.html', {'form': form})


def _warn_about_a_default_password(request, user):
    """Nag an administrator still using the password the installer set.

    The deployment default is published in the README, so an install that
    keeps it is effectively unprotected.
    """
    from pos.management.commands.ensure_admin import DEFAULT_PASSWORD

    if user.is_superuser and user.check_password(DEFAULT_PASSWORD):
        messages.warning(
            request,
            'This account still uses the default password from the '
            'installation guide, which is public. Change it now under your '
            'name > Change password.',
        )


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
