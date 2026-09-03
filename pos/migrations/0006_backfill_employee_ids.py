"""Give an employee ID to accounts that predate automatic generation.

Fills blanks only. Existing IDs are never changed or renumbered.
"""

from django.db import migrations

PREFIX = 'EMP-'
WIDTH = 3


def backfill(apps, schema_editor):
    User = apps.get_model('pos', 'User')

    highest = 0
    for value in User.objects.exclude(employee_id__isnull=True).values_list(
        'employee_id', flat=True
    ):
        suffix = (value or '')[len(PREFIX):]
        if (value or '').startswith(PREFIX) and suffix.isdigit():
            highest = max(highest, int(suffix))

    missing = User.objects.filter(employee_id__isnull=True).order_by('pk')
    for user in missing:
        highest += 1
        user.employee_id = f'{PREFIX}{highest:0{WIDTH}d}'
        user.save(update_fields=['employee_id'])


def noop(apps, schema_editor):
    """Reverse leaves the assigned IDs in place -- removing them loses history."""


class Migration(migrations.Migration):

    dependencies = [
        ('pos', '0005_alter_user_employee_id'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
