"""
Regenerates the PDF for every existing Payslip, so old stale files (from
before the current formatted layout was written) get replaced with the
current design.

Usage:
    python manage.py regenerate_payslips
    python manage.py regenerate_payslips --user emp        # just one user
    python manage.py regenerate_payslips --recalculate     # also recompute
                                                             # amounts first
"""
from django.core.management.base import BaseCommand
from payroll.models import Payslip
from payroll.utils import generate_payslip_pdf, compute_payslip_for_user


class Command(BaseCommand):
    help = "Regenerate PDF files for existing payslips (fixes stale/old-format PDFs)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--user', type=str, default=None,
            help="Only regenerate payslips for this username."
        )
        parser.add_argument(
            '--recalculate', action='store_true',
            help="Also recompute the payslip figures (basic/HRA/gross/net) "
                 "from current salary structure & attendance before "
                 "rebuilding the PDF. Use this if amounts are showing as 0."
        )

    def handle(self, *args, **options):
        qs = Payslip.objects.select_related('user', 'payroll_run')
        if options['user']:
            qs = qs.filter(user__username=options['user'])

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No matching payslips found."))
            return

        updated = 0
        for payslip in qs:
            if options['recalculate']:
                recomputed = compute_payslip_for_user(payslip.user, payslip.payroll_run)
                if recomputed:
                    payslip = recomputed
                else:
                    self.stdout.write(self.style.WARNING(
                        f"Skipped {payslip.user}: no salary structure set up."
                    ))
                    continue

            generate_payslip_pdf(payslip)
            updated += 1
            self.stdout.write(f"Regenerated: {payslip.user} - {payslip.payroll_run}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Regenerated {updated} of {total} payslip PDF(s)."
        ))
