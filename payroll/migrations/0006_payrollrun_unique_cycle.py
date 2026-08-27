from django.db import migrations, models


def merge_duplicate_payroll_runs(apps, schema_editor):
    """Safety step before the unique constraint below: if the database
    already has more than one PayrollRun row for the same (start_date,
    end_date) — possible pre-fix, since get_or_create() alone isn't
    race-safe — merge them into a single row before the constraint is
    added, or the migration will fail with an IntegrityError.

    For each duplicate group: keep the earliest-created run, move every
    Payslip pointing at the other run(s) onto the kept run (Payslip has
    its own unique_together on (payroll_run, user), so if the same user
    somehow has a payslip on more than one duplicate run, the newer/
    later one is dropped rather than causing another integrity error —
    this shouldn't happen in practice since crediting an employee twice
    for the same month is itself guarded against in the UI, but this
    makes the migration itself safe to run either way), then delete the
    now-empty duplicate run rows.
    """
    PayrollRun = apps.get_model('payroll', 'PayrollRun')
    Payslip = apps.get_model('payroll', 'Payslip')

    seen = {}
    for run in PayrollRun.objects.order_by('created_at', 'id'):
        key = (run.start_date, run.end_date)
        if key not in seen:
            seen[key] = run
            continue

        keeper = seen[key]
        for payslip in Payslip.objects.filter(payroll_run=run):
            if Payslip.objects.filter(payroll_run=keeper, user_id=payslip.user_id).exists():
                # Keeper already has a payslip for this user this cycle —
                # drop the duplicate-run's payslip rather than violate
                # Payslip's own (payroll_run, user) unique_together.
                payslip.delete()
            else:
                payslip.payroll_run = keeper
                payslip.save(update_fields=['payroll_run'])
        run.delete()


def noop_reverse(apps, schema_editor):
    # Nothing to reverse: merging duplicates is a one-way cleanup, not a
    # data transformation we need to undo when un-applying this migration.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0005_allowancerequest'),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_payroll_runs, noop_reverse),
        migrations.AddConstraint(
            model_name='payrollrun',
            constraint=models.UniqueConstraint(fields=['start_date', 'end_date'], name='payroll_run_unique_cycle'),
        ),
    ]
