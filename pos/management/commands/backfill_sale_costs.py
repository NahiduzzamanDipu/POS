"""Optionally estimate the cost of sale lines recorded before cost tracking.

Sales made before the ``SaleItem.unit_cost`` snapshot existed carry no cost, so
the Profits report excludes them and says so rather than valuing them at a cost
the business never paid.

If you accept an approximation for those old lines, this command fills them in
from each product's *current* cost price. That is an estimate, not history: if
a product has been repriced since, the figure will be wrong. It is opt-in for
exactly that reason, and it reports on what it would do unless you pass
``--commit``.

    python manage.py backfill_sale_costs              # report only, changes nothing
    python manage.py backfill_sale_costs --commit     # actually write the estimates

Lines that already have a cost are never touched, so running it twice is safe.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F

from pos.models import SaleItem


class Command(BaseCommand):
    help = (
        'Estimate unit_cost on sale lines recorded before cost tracking, '
        'using each product\'s current cost price. Reports only unless --commit.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Write the estimates. Without this the command only reports.',
        )

    def handle(self, *args, **options):
        missing = SaleItem.objects.filter(unit_cost__isnull=True)
        total = missing.count()

        if not total:
            self.stdout.write(self.style.SUCCESS(
                'Every sale line already has a recorded cost. Nothing to do.'
            ))
            return

        # A product priced at zero tells us nothing, so those stay unknown
        # rather than being backfilled as "free".
        fillable = missing.filter(product__cost_price__gt=0)
        fillable_count = fillable.count()
        skipped = total - fillable_count

        self.stdout.write(f'Sale lines with no recorded cost : {total}')
        self.stdout.write(f'  can be estimated               : {fillable_count}')
        self.stdout.write(f'  product has no cost price      : {skipped}')

        if not options['commit']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Nothing was written. Re-run with --commit to apply the estimates.'
            ))
            self.stdout.write(
                'Be aware: this uses each product\'s cost price as it stands '
                'today, so any product repriced since the sale will be '
                'estimated incorrectly.'
            )
            return

        with transaction.atomic():
            updated = fillable.update(unit_cost=F('product__cost_price'))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Estimated the cost on {updated} sale line(s).'
        ))
        if skipped:
            self.stdout.write(
                f'{skipped} line(s) were left unknown because their product '
                'has no cost price set. Set those costs under Products and run '
                'this again if you want them included.'
            )
