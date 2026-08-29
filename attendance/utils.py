import calendar
import io
from datetime import date, timedelta
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from django.core.files.base import ContentFile
from django.utils import timezone as dj_timezone

from attendance.models import AttendanceRecord, MonthlyAttendanceSheet, OvertimePermission


STANDARD_DAILY_HOURS_CAP = Decimal('8.00')


def normalize_year_month(year, month):
    """Wraps month overflow/underflow into the correct year, e.g. month=13 -> next Jan."""
    year, month = int(year), int(month)
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return year, month


def get_cl_quota(user):
    """Fixed monthly CL quota shown on the sheet (separate from LeaveBalance).
    2 for Manager, PM, GM, COO, CTO — 1 for everyone else."""
    SENIOR_ROLES = {'MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'COO', 'CTO'}
    if user.role in SENIOR_ROLES:
        return 2
    return 1


def _permissions_map(user, first_day, last_day):
    """date -> OvertimePermission for this user within the range."""
    perms = OvertimePermission.objects.filter(
        employee=user, date__gte=first_day, date__lte=last_day
    ).select_related('project')
    return {p.date: p for p in perms}

def _monthly_overrides(user, year, month):
    """Returns (cl_quota_override, ph_sunday_override) for this user/month —
    either is None if HR hasn't set an override."""
    sheet = MonthlyAttendanceSheet.objects.filter(user=user, year=year, month=month).first()
    if not sheet:
        return None, None
    return sheet.cl_quota_override, sheet.ph_sunday_override

def _authorized_hours(record, is_sunday, permission):
    """Returns (counted_hours, was_capped).

    - Sunday hours only count if a permission with covers_sunday() exists
      for that date; otherwise they're excluded entirely (0).
    - Weekday hours beyond STANDARD_DAILY_HOURS_CAP only count if a
      permission with covers_overtime() exists; otherwise they're capped
      at the standard 8 hours.
    """
    if not record or not record.total_hours:
        return Decimal('0.00'), False

    raw = record.total_hours

    if is_sunday:
        if permission and permission.covers_sunday():
            return raw, False
        return Decimal('0.00'), raw > 0

    if raw > STANDARD_DAILY_HOURS_CAP:
        if permission and permission.covers_overtime():
            return raw, False
        return STANDARD_DAILY_HOURS_CAP, True

    return raw, False


def compute_authorized_hours_for_range(user, start_date, end_date):
    """Used by payroll — total worked hours after stripping unauthorized
    Sunday work and unauthorized overtime."""
    records = AttendanceRecord.objects.filter(user=user, date__gte=start_date, date__lte=end_date)
    permissions = _permissions_map(user, start_date, end_date)
    total = Decimal('0.00')
    for r in records:
        hours, _ = _authorized_hours(r, r.date.weekday() == 6, permissions.get(r.date))
        total += hours
    return total


def build_summary_for_range(user, first_day, last_day, cl_override=None, ph_override=None,
                             label=None):
    """Core attendance-summary builder for an arbitrary date range (used
    directly by payroll for its 26th-25th pay cycle, and by
    build_monthly_summary() below for the calendar-month version used on
    the attendance screens). Same LOP/CL/overtime logic either way — only
    the date window differs.

    cl_override / ph_override let the caller pass in HR's monthly
    overrides (see _monthly_overrides) when they're relevant; pass None to
    skip overrides entirely (e.g. for a cross-month payroll cycle where no
    single calendar month's override cleanly applies)."""
    total_days_in_range = (last_day - first_day).days + 1

    records = AttendanceRecord.objects.filter(user=user, date__gte=first_day, date__lte=last_day)
    records_by_date = {r.date: r for r in records}
    permissions_by_date = _permissions_map(user, first_day, last_day)

    daily_rows = []
    sundays = 0
    on_duty_days = 0          # any day attended, including Sunday
    on_duty_non_sunday = 0    # attended days excluding Sunday (counts against WKD)
    sunday_hours = Decimal('0.00')
    night_duty_count = 0
    total_hours_worked = Decimal('0.00')
    overtime_hours = Decimal('0.00')

    current_date = first_day
    while current_date <= last_day:
        is_sunday = current_date.weekday() == 6  # Monday=0 ... Sunday=6
        if is_sunday:
            sundays += 1

        record = records_by_date.get(current_date)
        permission = permissions_by_date.get(current_date)
        hours, was_capped = _authorized_hours(record, is_sunday, permission)
        attended = bool(record and record.in_time)

        if attended:
            on_duty_days += 1
            total_hours_worked += hours
            if not is_sunday:
                on_duty_non_sunday += 1

        if is_sunday and record:
            sunday_hours += hours

        if is_sunday:
            if hours > 0:
                overtime_hours += hours
        elif hours > STANDARD_DAILY_HOURS_CAP:
            overtime_hours += (hours - STANDARD_DAILY_HOURS_CAP)

        if record and record.is_night_duty:
            night_duty_count += 1

        daily_rows.append({
            'date': current_date,
            'day_label': current_date.strftime('%d %b'),
            'is_sunday': is_sunday,
            'hours': hours,
            'was_capped': was_capped,
            'permission': permission,
            'in_time': record.in_time if record else None,
            'out_time': record.out_time if record else None,
            'attended': attended,
        })
        current_date += timedelta(days=1)

    wkd = total_days_in_range - sundays  # working days excluding Sunday
    actual_leave = max(0, wkd - on_duty_non_sunday)  # WKD days not attended

    cl_quota = cl_override if cl_override is not None else get_cl_quota(user)
    total_ph_sunday = ph_override if ph_override is not None else sundays

    lop = max(0, actual_leave - cl_quota)
    cl_used = actual_leave - lop  # CL days actually consumed (paid)

    overtime_days = (overtime_hours / STANDARD_DAILY_HOURS_CAP).quantize(Decimal('0.01'))
    total_days_to_pay = (
        Decimal(on_duty_non_sunday) + Decimal(total_ph_sunday) + Decimal(cl_used) + overtime_days
    ).quantize(Decimal('0.01'))

    return {
        'user': user,
        'start_date': first_day,
        'end_date': last_day,
        'month_name': label or f"{first_day.strftime('%d %b')} - {last_day.strftime('%d %b %Y')}",
        'days_in_month': total_days_in_range,
        'daily_rows': daily_rows,
        'total_days_worked': on_duty_days,
        'on_duty': on_duty_days,
        'cl_quota': cl_quota,
        'cl_used': cl_used,
        'sunday_hours': sunday_hours,
        'total_hours_worked': total_hours_worked,
        'overtime_hours': overtime_hours,
        'overtime_days': overtime_days,
        'total_days_to_pay': total_days_to_pay,
        'wkd': wkd,
        'actual_leave': actual_leave,
        'lop': lop,
        'total_ph_sunday': total_ph_sunday,
        'night_duty': night_duty_count,
        'has_cl_override': cl_override is not None,
        'has_ph_override': ph_override is not None,
    }


def build_payroll_cycle_summary(user, start_date, end_date):
    """LOP/attendance summary for a payroll cycle's exact dates (e.g. the
    26th-to-25th pay period), instead of a calendar month. CL/PH overrides
    are pulled from whichever calendar month contains the cycle's END date
    (the month the cycle is labelled/paid under), same convention payroll
    already uses elsewhere for this cycle."""
    cl_override, ph_override = _monthly_overrides(user, end_date.year, end_date.month)
    label = f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"
    summary = build_summary_for_range(user, start_date, end_date, cl_override, ph_override, label=label)
    # Keep the year/month keys the monthly template already reads (both the
    # dropdown state and the cycle's Excel filename), labelled by the cycle's
    # END date — same convention pay_cycle_bounds()/payroll use.
    summary['year'] = end_date.year
    summary['month'] = end_date.month
    return summary


def build_monthly_summary(user, year, month):
    """Builds the full monthly attendance summary dict for one employee
    over a calendar month. Thin wrapper around build_summary_for_range()
    that also keeps the year/month keys older callers/templates expect."""
    year, month = normalize_year_month(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    first_day = date(year, month, 1)
    last_day = date(year, month, days_in_month)

    cl_override, ph_override = _monthly_overrides(user, year, month)
    summary = build_summary_for_range(
        user, first_day, last_day, cl_override, ph_override, label=first_day.strftime('%B %Y')
    )
    summary['year'] = year
    summary['month'] = month
    return summary
def autofit_columns(ws, min_width=8, max_width=28, padding=2):
    """Sets each column's width to fit its longest cell value (openpyxl has
    no built-in autofit — Excel only autofits on open, not on generation).
    Scans every used cell per column, so identity/header/day/summary
    columns all size to their own real content instead of a fixed guess."""
    widths = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            length = len(str(cell.value))
            col_letter = cell.column_letter
            if length > widths.get(col_letter, 0):
                widths[col_letter] = length

    for col_letter, length in widths.items():
        ws.column_dimensions[col_letter].width = max(min_width, min(length + padding, max_width))


def generate_monthly_excel(summary):
    """Builds an .xlsx workbook (in-memory) as a row-per-day attendance
    report: a title band, an employee-info block, a Daily Attendance table
    (one row per date with Day/Status/In/Out/Hours), and a Monthly Summary
    key-value table below it. Replaces the older one-row-per-employee,
    one-column-per-day register layout."""
    wb = Workbook()
    ws = wb.active
    ws.title = f"{summary['month']:02d}-{summary['year']}"

    bold = Font(bold=True)
    title_font = Font(bold=True, size=14)
    section_font = Font(bold=True, size=12, color="FFFFFF")
    section_fill = PatternFill(start_color="0057FF", end_color="0057FF", fill_type="solid")
    table_header_fill = PatternFill(start_color="1F2A44", end_color="1F2A44", fill_type="solid")
    table_header_font = Font(bold=True, color="FFFFFF")
    weekend_fill = PatternFill(start_color="FFF1E8", end_color="FFF1E8", fill_type="solid")
    identity_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    center = Alignment(horizontal='center', vertical='center')
    left = Alignment(horizontal='left', vertical='center')
    thin = Side(style='thin', color='CCCCCC')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    REPORT_WIDTH = 6  # number of columns the daily-attendance table uses (Date..Hours)

    user = summary['user']
    row = 1

    # --- Title band ---------------------------------------------------
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=REPORT_WIDTH)
    c = ws.cell(row=row, column=1, value='MONTHLY ATTENDANCE REPORT')
    c.font = title_font
    c.alignment = center
    row += 2

    # --- Employee info block -------------------------------------------
    info_rows = [
        ('Employee Name', user.get_full_name() or user.username),
        ('Employee ID', user.employee_id or '-'),
        ('Period', summary['month_name']),
    ]
    for label, value in info_rows:
        label_cell = ws.cell(row=row, column=1, value=label)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=REPORT_WIDTH)
        value_cell = ws.cell(row=row, column=2, value=value)
        label_cell.font = bold
        label_cell.fill = identity_fill
        label_cell.border = border
        value_cell.fill = identity_fill
        value_cell.alignment = left
        for col in range(1, REPORT_WIDTH + 1):
            ws.cell(row=row, column=col).border = border
        row += 1
    row += 1

    # --- Daily Attendance section header --------------------------------
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=REPORT_WIDTH)
    c = ws.cell(row=row, column=1, value='DAILY ATTENDANCE')
    c.font = section_font
    c.fill = section_fill
    c.alignment = center
    row += 1

    table_headers = ['Date', 'Day', 'Status', 'In', 'Out', 'Hours']
    for col, h in enumerate(table_headers, start=1):
        c = ws.cell(row=row, column=col, value=h)
        c.font = table_header_font
        c.fill = table_header_fill
        c.alignment = center
        c.border = border
    row += 1

    for day in summary['daily_rows']:
        d = day['date']
        is_weekend = day['is_sunday'] or d.weekday() == 5  # Sunday or Saturday
        if day['attended']:
            status = 'Present'
        elif day['is_sunday']:
            status = 'Sunday'
        else:
            status = 'Absent'

        in_str = dj_timezone.localtime(day['in_time']).strftime('%H:%M') if day['in_time'] else '--'
        out_str = dj_timezone.localtime(day['out_time']).strftime('%H:%M') if day['out_time'] else '--'
        hours_val = float(day['hours']) if day['hours'] else 0

        row_values = [d.strftime('%d-%b'), d.strftime('%a'), status, in_str, out_str, hours_val]
        for col, val in enumerate(row_values, start=1):
            c = ws.cell(row=row, column=col, value=val)
            c.alignment = center
            c.border = border
            if is_weekend:
                c.fill = weekend_fill
        row += 1
    row += 1

    # --- Monthly Summary section -----------------------------------------
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=REPORT_WIDTH)
    c = ws.cell(row=row, column=1, value='MONTHLY SUMMARY')
    c.font = section_font
    c.fill = section_fill
    c.alignment = center
    row += 1

    summary_pairs = [
        ('Working Days (Excl. Sunday)', summary['wkd']),
        ('Present', summary['on_duty']),
        ('CL', summary['cl_quota']),
        ('Actual Leave', summary['actual_leave']),
        ('LOP', summary['lop']),
        ('Sunday Hours', float(summary['sunday_hours'])),
        ('Total Hours', float(summary['total_hours_worked'])),
        ('PH / Sunday', summary['total_ph_sunday']),
        ('Total Days in Month', summary['days_in_month']),
        ('Night Duty', summary['night_duty']),
    ]
    for label, value in summary_pairs:
        label_cell = ws.cell(row=row, column=1, value=label)
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=REPORT_WIDTH)
        value_cell = ws.cell(row=row, column=2, value=value)
        label_cell.font = bold
        label_cell.border = border
        value_cell.alignment = left
        for col in range(1, REPORT_WIDTH + 1):
            ws.cell(row=row, column=col).border = border
        row += 1

    autofit_columns(ws, min_width=8, max_width=28, padding=2)
    ws.freeze_panes = 'A1'

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer



def regenerate_and_save_monthly_sheet(user, year, month):
    """Recomputes the summary for this user/month and re-saves the Excel
    snapshot. Called automatically on every punch."""
    year, month = normalize_year_month(year, month)
    summary = build_monthly_summary(user, year, month)
    buffer = generate_monthly_excel(summary)

    sheet, _ = MonthlyAttendanceSheet.objects.get_or_create(user=user, year=year, month=month)
    filename = f"attendance_{user.username}_{year}_{month:02d}.xlsx"
    sheet.excel_file.save(filename, ContentFile(buffer.read()), save=True)
    return sheet


def build_team_monthly_summary(users, year, month):
    """Builds the monthly summary for every user in `users`, for the same
    month. Returns a list of summary dicts (same shape as
    build_monthly_summary), one per employee."""
    year, month = normalize_year_month(year, month)
    summaries = []
    for u in users:
        summaries.append(build_monthly_summary(u, year, month))
    return summaries


def pay_cycle_bounds(year, month):
    """Pay-cycle bounds for the '26th to 25th' salary calendar: a cycle
    labelled by (year, month) runs from the 26th of the PREVIOUS month
    through the 25th of THIS month. E.g. cycle "2026-02" (February) runs
    26-Jan-2026 to 25-Feb-2026. Kept in sync with payroll._cycle_bounds —
    if you change one, change the other."""
    year, month = normalize_year_month(year, month)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    start = date(prev_year, prev_month, 26)
    end = date(year, month, 25)
    return start, end


def build_team_cycle_summary(users, start_date, end_date):
    """Same as build_team_monthly_summary, but over an exact date range
    (the 26th-25th pay cycle) instead of a calendar month — used for the
    attendance register export so its day columns match payroll's cycle."""
    summaries = []
    for u in users:
        cl_override, ph_override = _monthly_overrides(u, end_date.year, end_date.month)
        label = f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"
        summaries.append(build_summary_for_range(u, start_date, end_date, cl_override, ph_override, label=label))
    return summaries


def generate_team_monthly_excel(summaries, start_date, end_date, branch_name=None):
    """Generates an attendance Excel matching HRMS_Format.xlsx:
    SL | Name | Emp ID | Shift | Day1..DayN | summary columns
    Each day cell = 8 (present), 0 (absent), None (Sunday).

    `start_date`/`end_date` are the exact pay-cycle dates to show as
    columns (e.g. 26th of one month to 25th of the next) — NOT
    necessarily a calendar month, so the day count/columns always match
    whatever range the summaries themselves were built over.
    """
    wb = Workbook()
    ws = wb.active

    if not summaries:
        ws.title = f"{start_date.strftime('%d%b')}-{end_date.strftime('%d%b%Y')}"[:31]
        ws['A1'] = 'No active employees found.'
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        return buffer

    days_in_cycle = (end_date - start_date).days + 1
    all_dates = [start_date + timedelta(days=i) for i in range(days_in_cycle)]
    month_label = f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"
    ws.title = month_label.replace(' ', '')[:31]

    # ---------- Style palette (matches other Prostaff reports) ----------
    NAVY = '1F3864'
    SUMMARY_TINT = 'EAF0FA'   # soft navy tint for the summary block
    STRIPE_FILL = 'F5F8FC'    # very light zebra stripe for readability
    WEEKEND_FILL = 'F2F2F2'   # neutral grey for Sunday/off cells (was loud peach)

    thin = Side(style='thin', color='D9D9D9')
    thick_left = Side(style='medium', color='A6A6A6')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    section_border = Border(left=thick_left, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left_align = Alignment(horizontal='left', vertical='center')

    header_fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    summary_fill = PatternFill(start_color=SUMMARY_TINT, end_color=SUMMARY_TINT, fill_type="solid")
    weekend_fill = PatternFill(start_color=WEEKEND_FILL, end_color=WEEKEND_FILL, fill_type="solid")
    stripe_fill = PatternFill(start_color=STRIPE_FILL, end_color=STRIPE_FILL, fill_type="solid")

    hdr_font = Font(bold=True, size=9, color="FFFFFF")
    norm9    = Font(size=9)
    title_font = Font(bold=True, size=14, color=NAVY)
    subtitle_font = Font(italic=True, size=10, color="595959")

    total_cols = 4 + days_in_cycle + 18

    ws.row_dimensions[1].height = 24
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    title_cell = ws.cell(row=1, column=1, value="ATTENDANCE REGISTER")
    title_cell.font = title_font
    title_cell.alignment = Alignment(horizontal='center', vertical='center')

    ws.row_dimensions[2].height = 16
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=total_cols)
    subtitle_text = f"{month_label}" + (f"  |  Branch: {branch_name}" if branch_name else "")
    subtitle_cell = ws.cell(row=2, column=1, value=subtitle_text)
    subtitle_cell.font = subtitle_font
    subtitle_cell.alignment = Alignment(horizontal='center', vertical='center')

    # Rotated alignment for date and summary headers (90 degrees like HRMS_Format)
    rotated = Alignment(horizontal='center', vertical='bottom', text_rotation=90)
    rotated_fixed = Alignment(horizontal='center', vertical='center', text_rotation=90)

    HDR_ROW = 3
    ws.row_dimensions[HDR_ROW].height = 100  # tall row for rotated text

    fixed_headers = ['SL', 'Name', 'New Emp ID', 'Shift']
    for ci, h in enumerate(fixed_headers, start=1):
        c = ws.cell(row=HDR_ROW, column=ci, value=h)
        c.font = hdr_font; c.fill = header_fill
        c.alignment = rotated_fixed; c.border = border

    day_start_col = len(fixed_headers) + 1
    for di, dt in enumerate(all_dates):
        ci = day_start_col + di
        if hasattr(dt, 'strftime'):
            label = f"{dt.day}-{dt.strftime('%b')}"
        else:
            label = str(dt)
        c = ws.cell(row=HDR_ROW, column=ci, value=label)
        c.font = hdr_font; c.fill = header_fill
        c.alignment = rotated
        c.border = section_border if di == 0 else border

    summary_headers = [
        'Days worked', 'CL', 'OFF/OD/OT', 'Sundays', 'TOTAL', 'DAYSWKD',
        'ACTUAL LEAVE', 'LEAVE', 'PH / SUNDAY', 'TOTAL (PH+LVE+OFF)',
        'MONTHDAYS', 'NIGHT DUTY', 'ESI LEAVE', 'Sanctioned LOP',
        'lop cases', 'DOJ', 'Salary', 'PF/NON PF', 'Remarks',
    ]
    sum_start_col = day_start_col + days_in_cycle
    for si, h in enumerate(summary_headers):
        ci = sum_start_col + si
        c = ws.cell(row=HDR_ROW, column=ci, value=h)
        c.font = hdr_font; c.fill = header_fill
        c.alignment = rotated
        c.border = section_border if si == 0 else border

    for idx, summary in enumerate(summaries, start=1):
        user = summary['user']
        row_num = HDR_ROW + idx
        ws.row_dimensions[row_num].height = 15
        row_stripe = (idx % 2 == 0)

        profile = getattr(user, 'profile', None)
        shift_label = profile.get_shift_display() if (profile and getattr(profile, 'shift', None)) else 'General'

        fixed_vals = [idx, user.get_full_name() or user.username, user.employee_id or '-', shift_label]
        for ci, val in enumerate(fixed_vals, start=1):
            c = ws.cell(row=row_num, column=ci, value=val)
            c.font = norm9
            c.alignment = left_align if ci == 2 else center
            c.border = border
            if row_stripe:
                c.fill = stripe_fill

        # Keyed by the FULL date, not just day-of-month — a 26th-25th cycle
        # spans two calendar months, so e.g. "the 3rd" could mean 3-Jan or
        # 3-Feb and a day-of-month-only key would collide/overwrite.
        day_map = {d['date']: d for d in summary['daily_rows']}

        for di, dt in enumerate(all_dates):
            ci = day_start_col + di
            day_data = day_map.get(dt)
            is_sunday = (dt.weekday() == 6)
            if is_sunday:
                val = None
                fill = weekend_fill
            elif day_data and day_data.get('attended'):
                val = 8
                fill = stripe_fill if row_stripe else None
            else:
                val = 0
                fill = stripe_fill if row_stripe else None
            c = ws.cell(row=row_num, column=ci, value=val)
            c.font = norm9; c.alignment = center
            c.border = section_border if di == 0 else border
            if fill:
                c.fill = fill

        lop    = summary.get('lop', 0)
        doj    = user.date_joined_company
        salary = summary.get('salary') or 0

        pf_status = 'PF' if (profile and getattr(profile, 'is_pf_applicable', False)) else 'NON PF'

        summary_vals = [
            summary['total_days_worked'],           # Days worked
            summary.get('cl_quota', 0),             # CL
            float(summary.get('overtime_hours', 0)),# OFF/OD/OT
            float(summary.get('sunday_hours', 0)),  # Sundays
            float(summary.get('total_hours_worked', 0)), # TOTAL
            summary['total_days_worked'],           # DAYSWKD
            summary.get('actual_leave', 0),         # ACTUAL LEAVE
            summary.get('actual_leave', 0),         # LEAVE
            summary.get('total_ph_sunday', 0),      # PH/SUNDAY
            float(summary.get('total_days_to_pay', 0)), # TOTAL(PH+LVE+OFF)
            summary['days_in_month'],               # MONTHDAYS
            0,                                      # NIGHT DUTY
            0,                                      # ESI LEAVE
            lop,                                    # Sanctioned LOP
            lop,                                    # lop cases
            doj,                                    # DOJ
            float(salary),                          # Salary
            pf_status,                              # PF/NON PF
            '',                                     # Remarks
        ]
        for si, val in enumerate(summary_vals):
            ci = sum_start_col + si
            c = ws.cell(row=row_num, column=ci, value=val)
            c.font = norm9; c.alignment = center
            c.border = section_border if si == 0 else border
            c.fill = summary_fill
            if si == 15 and val:  # DOJ
                c.number_format = 'DD-MM-YYYY'

    # ── Auto-fit column widths ────────────────────────────────────────────────
    col_widths = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            # Measure text length
            if hasattr(cell.value, 'strftime'):
                text = cell.value.strftime('%d-%b')
            else:
                text = str(cell.value)
            # Handle multi-line strings — use longest line
            max_line = max((len(line) for line in text.split('\n')), default=1)
            col = cell.column
            col_widths[col] = max(col_widths.get(col, 0), max_line)

    for col, width in col_widths.items():
        letter = get_column_letter(col)
        # Day columns: cap at 7, fixed cols: cap at 30
        if col >= day_start_col and col < sum_start_col:
            ws.column_dimensions[letter].width = max(7, min(width + 1, 9))
        elif col == 2:  # Name — allow wider
            ws.column_dimensions[letter].width = max(width + 2, 20)
        else:
            ws.column_dimensions[letter].width = max(width + 2, 8)

    ws.freeze_panes = get_column_letter(day_start_col) + str(HDR_ROW + 1)
    ws.sheet_view.showGridLines = False
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def get_week_options(year, month):
    """List of (week_number, label) for the week dropdown, splitting the
    month into consecutive 7-day chunks starting on the 1st."""
    year, month = normalize_year_month(year, month)
    days_in_month = calendar.monthrange(year, month)[1]
    options = []
    week_number = 1
    start_day = 1
    while start_day <= days_in_month:
        end_day = min(start_day + 6, days_in_month)
        label = f"Week {week_number} ({start_day}-{end_day} {date(year, month, 1).strftime('%b')})"
        options.append((week_number, label))
        start_day += 7
        week_number += 1
    return options


def build_weekly_summary(user, year, month, week_number):
    year, month = normalize_year_month(year, month)
    days_in_month = calendar.monthrange(year, month)[1]

    start_day = (week_number - 1) * 7 + 1
    if start_day > days_in_month:
        week_number = 1
        start_day = 1
    end_day = min(start_day + 6, days_in_month)

    first_day = date(year, month, start_day)
    last_day = date(year, month, end_day)

    records = AttendanceRecord.objects.filter(user=user, date__gte=first_day, date__lte=last_day)
    records_by_date = {r.date: r for r in records}
    permissions_by_date = _permissions_map(user, first_day, last_day)

    daily_rows = []
    total_hours = Decimal('0.00')
    days_worked = 0
    sundays = 0
    night_duty_count = 0

    current = first_day
    while current <= last_day:
        is_sunday = current.weekday() == 6
        if is_sunday:
            sundays += 1
        record = records_by_date.get(current)
        permission = permissions_by_date.get(current)
        hours, was_capped = _authorized_hours(record, is_sunday, permission)
        attended = bool(record and record.in_time)
        if attended:
            days_worked += 1
            total_hours += hours
        if record and record.is_night_duty:
            night_duty_count += 1
        daily_rows.append({
            'date': current,
            'day_label': current.strftime('%d %b (%a)'),
            'is_sunday': is_sunday,
            'hours': hours,
            'was_capped': was_capped,
            'in_time': record.in_time if record else None,
            'out_time': record.out_time if record else None,
            'attended': attended,
        })
        current += timedelta(days=1)

    return {
        'user': user, 'year': year, 'month': month, 'week_number': week_number,
        'start_date': first_day, 'end_date': last_day,
        'label': f"Week {week_number}: {first_day.strftime('%d %b')} - {last_day.strftime('%d %b %Y')}",
        'daily_rows': daily_rows,
        'days_worked': days_worked,
        'total_hours_worked': total_hours,
        'sundays': sundays,
        'night_duty': night_duty_count,
        'total_days': (last_day - first_day).days + 1,
    }


def build_yearly_summary(user, year):
    months_data = []
    total_hours = Decimal('0.00')
    total_days_worked = 0
    total_actual_leave = 0
    total_lop = 0
    total_night_duty = 0
    total_cl_quota = 0

    for m in range(1, 13):
        s = build_monthly_summary(user, year, m)
        months_data.append(s)
        total_hours += s['total_hours_worked']
        total_days_worked += s['total_days_worked']
        total_actual_leave += s['actual_leave']
        total_lop += s['lop']
        total_night_duty += s['night_duty']
        total_cl_quota += s['cl_quota']

    return {
        'user': user, 'year': year,
        'months': months_data,
        'total_hours_worked': total_hours,
        'total_days_worked': total_days_worked,
        'total_actual_leave': total_actual_leave,
        'total_lop': total_lop,
        'total_night_duty': total_night_duty,
        'total_cl_quota': total_cl_quota,
    }


def generate_weekly_excel(summary):
    wb = Workbook()
    ws = wb.active
    ws.title = f"Week {summary['week_number']}"
    user = summary['user']
    bold = Font(bold=True)
    header_fill = PatternFill(start_color="0057FF", end_color="0057FF", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    ws['A1'] = 'Employee'; ws['B1'] = user.get_full_name() or user.username
    ws['A2'] = 'Week'; ws['B2'] = summary['label']
    ws['A1'].font = bold
    ws['A2'].font = bold

    headers = ['Date', 'Day', 'In Time', 'Out Time', 'Hours']
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = header_font
        c.fill = header_fill

    row = 5
    for d in summary['daily_rows']:
        ws.cell(row=row, column=1, value=d['date'].strftime('%d-%m-%Y'))
        ws.cell(row=row, column=2, value=d['date'].strftime('%A'))
        ws.cell(row=row, column=3, value=d['in_time'].strftime('%I:%M %p') if d['in_time'] else '-')
        ws.cell(row=row, column=4, value=d['out_time'].strftime('%I:%M %p') if d['out_time'] else '-')
        ws.cell(row=row, column=5, value=float(d['hours']))
        row += 1

    row += 1
    ws.cell(row=row, column=1, value='Total Hours').font = bold
    ws.cell(row=row, column=2, value=float(summary['total_hours_worked']))
    row += 1
    ws.cell(row=row, column=1, value='Days Worked').font = bold
    ws.cell(row=row, column=2, value=summary['days_worked'])

    for col_letter in ['A', 'B', 'C', 'D', 'E']:
        ws.column_dimensions[col_letter].width = 16

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def generate_yearly_excel(summary):
    wb = Workbook()
    ws = wb.active
    ws.title = f"{summary['year']}"
    user = summary['user']
    bold = Font(bold=True)
    header_fill = PatternFill(start_color="0057FF", end_color="0057FF", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    ws['A1'] = 'Employee'; ws['B1'] = user.get_full_name() or user.username
    ws['A2'] = 'Year'; ws['B2'] = summary['year']
    ws['A1'].font = bold
    ws['A2'].font = bold

    headers = ['Month', 'Days Worked', 'CL Quota', 'Total Hours', 'Actual Leave', 'LOP', 'Night Duty']
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = header_font
        c.fill = header_fill

    row = 5
    for s in summary['months']:
        ws.cell(row=row, column=1, value=s['month_name'])
        ws.cell(row=row, column=2, value=s['total_days_worked'])
        ws.cell(row=row, column=3, value=s['cl_quota'])
        ws.cell(row=row, column=4, value=float(s['total_hours_worked']))
        ws.cell(row=row, column=5, value=s['actual_leave'])
        ws.cell(row=row, column=6, value=s['lop'])
        ws.cell(row=row, column=7, value=s['night_duty'])
        row += 1

    row += 1
    ws.cell(row=row, column=1, value='TOTAL').font = bold
    ws.cell(row=row, column=2, value=summary['total_days_worked']).font = bold
    ws.cell(row=row, column=3, value=summary['total_cl_quota']).font = bold
    ws.cell(row=row, column=4, value=float(summary['total_hours_worked'])).font = bold
    ws.cell(row=row, column=5, value=summary['total_actual_leave']).font = bold
    ws.cell(row=row, column=6, value=summary['total_lop']).font = bold
    ws.cell(row=row, column=7, value=summary['total_night_duty']).font = bold

    for col_letter in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:
        ws.column_dimensions[col_letter].width = 16

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
