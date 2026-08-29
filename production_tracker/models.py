from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models


# The 24 one-hour slots a day is broken into, in the same order as the
# existing "HRMS_Hourly_tracker" Excel sheet (06:00 -> next day 06:00).
# slot_index (0-23) is what HourlyEntry stores; this list is the single
# source of truth for ordering and labels everywhere else in this app.
HOUR_SLOTS = [
    ('06:00', '07:00'), ('07:00', '08:00'), ('08:00', '09:00'), ('09:00', '10:00'),
    ('10:00', '11:00'), ('11:00', '12:00'), ('12:00', '13:00'), ('13:00', '14:00'),
    ('14:00', '15:00'), ('15:00', '16:00'), ('16:00', '17:00'), ('17:00', '18:00'),
    ('18:00', '19:00'), ('19:00', '20:00'), ('20:00', '21:00'), ('21:00', '22:00'),
    ('22:00', '23:00'), ('23:00', '00:00'), ('00:00', '01:00'), ('01:00', '02:00'),
    ('02:00', '03:00'), ('03:00', '04:00'), ('04:00', '05:00'), ('05:00', '06:00'),
]

# A "full" production day used to prorate Target/Day when someone works
# fewer hours than a full shift (matches the Excel's Target/Day -> Actual
# Target column, e.g. 1500 target at 7/8 hrs worked -> 1313 actual target).
STANDARD_SHIFT_HOURS = 8


class ProductionProcess(models.Model):
    """Master data: e.g. Full_QC, Training, Online — managed by HR/Admin
    in Django admin so entry-screen dropdowns stay clean instead of free
    text like the old Excel sheet."""
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Process'
        verbose_name_plural = 'Processes (master data)'

    def __str__(self):
        return self.name


class ProductionEvent(models.Model):
    """Master data: e.g. Seamens Details, War Records, Convict Register."""
    name = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Event'
        verbose_name_plural = 'Events (master data)'

    def __str__(self):
        return self.name


class ProductionGrouping(models.Model):
    """Master data: e.g. Complex File, Permission, Leave, Training, Normal.

    is_non_working flags groupings (Leave/Training/Permission etc.) that
    should NOT be treated as a production shortfall when this feeds the
    monthly Appraisal module later — a day marked Leave shouldn't drag an
    employee's efficiency % down to 0%."""
    name = models.CharField(max_length=100, unique=True)
    is_non_working = models.BooleanField(
        default=False,
        help_text="Check for Leave/Training/Permission-type groupings that shouldn't count as a production shortfall."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Grouping'
        verbose_name_plural = 'Groupings (master data)'

    def __str__(self):
        return self.name


class DailyProduction(models.Model):
    """One row per employee per day per (work type + project) — the daily
    header record the Excel sheet's non-hourly columns map to. The 24
    hourly counts live in HourlyEntry, linked via hourly_entries, instead
    of 24 DB columns."""

    WORK_TYPE_CHOICES = [
        ('PRODUCTION', 'Production'),
        ('QA', 'QA'),
    ]

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='daily_production_records'
    )
    date = models.DateField()

    branch = models.ForeignKey('core.Branch', null=True, blank=True, on_delete=models.SET_NULL)
    shift = models.CharField(max_length=10, blank=True)

    # Every row is either a Production row or a QA row for a given
    # project — one employee can have a Production row AND a QA row for
    # the SAME project on the SAME date (they're tracked/appraised
    # separately), plus more rows for other projects.
    work_type = models.CharField(max_length=10, choices=WORK_TYPE_CHOICES, default='PRODUCTION')

    project = models.ForeignKey(
        'projects.Project', null=True, blank=True, on_delete=models.SET_NULL, related_name='daily_production_records'
    )
    process = models.ForeignKey(ProductionProcess, null=True, blank=True, on_delete=models.SET_NULL)
    event = models.ForeignKey(ProductionEvent, null=True, blank=True, on_delete=models.SET_NULL)
    grouping = models.ForeignKey(ProductionGrouping, null=True, blank=True, on_delete=models.SET_NULL)

    target_day = models.PositiveIntegerField(default=0, verbose_name='Target / Day')
    error_count = models.PositiveIntegerField(default=0)
    reason = models.CharField(max_length=255, blank=True)
    pl_comments = models.TextField(blank=True, verbose_name="PL's Comments")
    pm_comments = models.TextField(blank=True, verbose_name='PM Comments')

    # Denormalized / cached from hourly_entries via recalculate() so list
    # and dashboard screens don't need to re-sum hourly rows every time.
    hours_worked = models.PositiveIntegerField(default=0)
    achieved = models.PositiveIntegerField(default=0)
    actual_target = models.PositiveIntegerField(default=0)
    efficiency_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_submitted = models.BooleanField(default=False)

    class Meta:
        # No unique_together on (employee, date): QA/Production employees
        # work several projects in one day, so one employee can have
        # multiple DailyProduction rows on the same date (one per project,
        # same pattern as the old Excel sheet — e.g. two separate SSH rows
        # on the same day, one for normal production and one for a
        # permission block). SSH/HOR-style single-project employees just
        # end up with exactly one row per date, same as before.
        ordering = ['-date']

    def __str__(self):
        return f"{self.employee} - {self.date}"

    @property
    def deviation(self):
        return self.achieved - self.target_day

    def sync_target_from_project(self):
        if not self.project_id:
            return
            
        # PL, PM, and GM do not have production targets for themselves.
        if self.employee.role in ['PROJECT_MANAGER', 'GENERAL_MANAGER'] or self.project.lead_id == self.employee_id:
            self.target_day = 0
            return
            
        override = EmployeeTargetOverride.objects.filter(
            employee_id=self.employee_id,
            project_id=self.project_id,
            work_type=self.work_type,
            effective_date__lte=self.date
        ).order_by('-effective_date').first()
        
        if override:
            self.target_day = override.target_day
            return

        target = self.project.qa_daily_target if self.work_type == 'QA' else self.project.daily_target
        if target:
            self.target_day = target

    def recalculate(self, save=True):
        """Sums this day's HourlyEntry rows into hours_worked / achieved,
        then prorates target_day into actual_target the same way the
        Excel does (Target/Day * hours_worked / STANDARD_SHIFT_HOURS),
        and derives efficiency %. Call after saving hourly entries."""
        entries = list(self.hourly_entries.exclude(count__isnull=True))
        self.hours_worked = len(entries)
        self.achieved = sum(e.count for e in entries)

        if self.hours_worked and self.target_day:
            raw = Decimal(self.target_day) * Decimal(self.hours_worked) / Decimal(STANDARD_SHIFT_HOURS)
            self.actual_target = int(raw.to_integral_value(rounding=ROUND_HALF_UP))
        else:
            self.actual_target = 0

        if self.actual_target:
            eff = (Decimal(self.achieved) / Decimal(self.actual_target)) * 100
            self.efficiency_percentage = eff.quantize(Decimal('0.01'))
        else:
            self.efficiency_percentage = Decimal('0.00')

        if save:
            self.save(update_fields=['hours_worked', 'achieved', 'actual_target', 'efficiency_percentage', 'updated_at'])


def apply_project_daily_target(project, date, work_type='PRODUCTION'):
    """Sets/refreshes a Project's Daily Target on every one of its
    currently assigned members' rows for `date` — creating the row if the
    member hasn't started their entry yet. This is the single place that
    implements "one target per project, not one per employee": PL/TL and
    PM/GM never need to type a number per employee, they set it once here
    (from the Team Overview dashboard or the Edit Project page) and it's
    pushed out to the whole team. work_type defaults to 'PRODUCTION' since
    that's the common case; pass 'QA' to target members' QA rows for the
    same project instead.

    A QA target only ever reaches Quality-department members, and a
    Production target only ever reaches everyone else — otherwise a QA
    Manager's "Set for Team" would silently create QA rows for Production
    staff on the same project (and vice versa), which isn't what either
    manager is scoping their target to. Returns how many members were
    updated."""
    target = project.qa_daily_target if work_type == 'QA' else project.daily_target
    if not target:
        return 0
    members = project.assignments.filter(is_current=True).select_related('user__department')
    if work_type == 'QA':
        members = members.filter(user__department__is_qa=True)
    else:
        members = members.exclude(user__department__is_qa=True)
    member_ids = members.values_list('user_id', flat=True)
    updated = 0
    for uid in member_ids:
        # Filtering on project AND work_type (not just employee+date) means
        # this always lands on — or creates — THIS project's own row of
        # THIS work type for the member, even if they already have other
        # project/work-type rows for the same date.
        daily, _created = DailyProduction.objects.get_or_create(
            employee_id=uid, date=date, project=project, work_type=work_type,
        )
        daily.sync_target_from_project()
        daily.save(update_fields=['target_day', 'updated_at'])
        daily.recalculate()
        updated += 1
    return updated


class HourlyEntry(models.Model):
    """One hour's count for a DailyProduction row. count is null until the
    employee actually fills that hour in (mirrors the Excel's blank cells
    for hours outside a shift)."""
    daily = models.ForeignKey(DailyProduction, on_delete=models.CASCADE, related_name='hourly_entries')
    slot_index = models.PositiveSmallIntegerField(help_text='Index into production_tracker.models.HOUR_SLOTS')
    hour_start = models.CharField(max_length=5)
    hour_end = models.CharField(max_length=5)
    count = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('daily', 'slot_index')
        ordering = ['slot_index']

    def __str__(self):
        return f"{self.daily} [{self.hour_start}-{self.hour_end}] = {self.count}"

    @property
    def label(self):
        return f"{self.hour_start} - {self.hour_end}"


class EmployeeTargetOverride(models.Model):
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='target_overrides')
    project = models.ForeignKey('projects.Project', on_delete=models.CASCADE, related_name='target_overrides')
    work_type = models.CharField(max_length=10, choices=DailyProduction.WORK_TYPE_CHOICES, default='PRODUCTION')
    effective_date = models.DateField()
    target_day = models.PositiveIntegerField()
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='set_targets')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'project', 'work_type', 'effective_date')
        ordering = ['-effective_date']
