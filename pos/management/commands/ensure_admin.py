"""Make sure an administrator account exists, so a fresh deploy is reachable.

Run automatically by `.cpanel.yml` on every deploy. It is idempotent: on a
database that already has the account it changes nothing, so it is safe to run
again and again.

    python manage.py ensure_admin
    python manage.py ensure_admin --reset-password      # forget the password?

The email and password come from the environment when set, so a real
deployment can pick its own without editing code:

    ADMIN_EMAIL, ADMIN_PASSWORD

An existing account's password is never touched unless --reset-password is
given. Silently resetting it on every deploy would undo the change an
administrator made after the last one.
"""

import os

from django.core.management.base import BaseCommand
from django.db import transaction

from pos.models import Role, User

DEFAULT_EMAIL = 'odelltech@gmail.com'
DEFAULT_PASSWORD = 'Pos@12345'
DEFAULT_USERNAME = 'admin'


class Command(BaseCommand):
    help = 'Create the administrator account if it is missing (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            default=os.getenv('ADMIN_EMAIL', DEFAULT_EMAIL),
            help='Sign-in email for the administrator.',
        )
        parser.add_argument(
            '--password',
            default=os.getenv('ADMIN_PASSWORD', DEFAULT_PASSWORD),
            help='Password to set when the account is created.',
        )
        parser.add_argument(
            '--reset-password',
            action='store_true',
            help='Also reset the password on an account that already exists.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        password = options['password']

        # Match on email first (it is the sign-in identifier), then fall back
        # to the conventional username so an older install is adopted rather
        # than duplicated.
        user = (
            User.objects.filter(email__iexact=email).first()
            or User.objects.filter(username=DEFAULT_USERNAME).first()
        )

        if user is None:
            user = User(username=DEFAULT_USERNAME)
            created = True
        else:
            created = False

        user.email = email
        user.role = Role.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        if not user.first_name:
            user.first_name = "O'dell"
        if not user.last_name:
            user.last_name = 'Administrator'

        if created or options['reset_password']:
            user.set_password(password)

        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(
                f'Created the administrator: {email}'
            ))
            self.stdout.write('Sign in, then change the password under your name > Change password.')
        elif options['reset_password']:
            self.stdout.write(self.style.SUCCESS(
                f'Reset the password for {email} ({user.employee_id}).'
            ))
        else:
            self.stdout.write(
                f'Administrator already present: {email} ({user.employee_id}). '
                'Password left as it is -- pass --reset-password to change it.'
            )
