from datetime import datetime
from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.http import HttpResponse
import openpyxl
from django.utils import timezone

from core.decorators import role_required
from core.models import User
from .forms import DailyProductionForm
from .models import (
    HOUR_SLOTS, STANDARD_SHIFT_HOURS, DailyProduction, HourlyEntry, ProductionEvent,
    apply_project_daily_target,
)


def _is_production_employee(user):
    return bool(user.department and (
        'Production' in user.department.name or
        user.department.is_qa
    ))


def _led_projects(user):
    """Projects this user leads â€” as the assigned Project.lead, or via a
    current ProjectAssignment with role_on_project 'Team Leader'. PL and TL
    are the same person/role in this system, so both routes count."""
    from projects.models import Project, ProjectAssignment
    led_ids = set(Project.objects.filter(lead=user).values_list('id', flat=True))
    led_ids |= set(ProjectAssignment.objects.filter(
        user=user, is_current=True, role_on_project__iexact='Team Leader'
    ).values_list('project_id', flat=True))
    return Project.objects.filter(id__in=led_ids)


def _is_project_lead_or_tl(user):
    return _led_projects(user).exists()


def _tl_pl_user_ids():
    """User ids who are a PL/TL for any project. PL/TL is a project-level
    assignment, not a User.role — these people still have role='EMPLOYEE'
    on their account, so every employee queryset below must explicitly
    exclude them. They don't fill their own hourly sheet and don't carry
    a target/count of their own; they only appear as the person setting
    targets for their team."""
    from projects.models import Project, ProjectAssignment
    ids = set(Project.objects.exclude(lead__isnull=True).values_list('lead_id', flat=True))
    ids |= set(ProjectAssignment.objects.filter(
        is_current=True, role_on_project__iexact='Team Leader'
    ).values_list('user_id', flat=True))
    return ids


def _parse_date(date_str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return timezone.localdate()


def _last_target(employee_id, project_id, before_date, work_type=None):
    """Most recent target_day this employee had (on this project, or with
    no project for the Unassigned section) from BEFORE the selected date.
    Used as the Team Overview's fallback display when there's no entry yet
    for the selected date, instead of always showing the project's current
    live target regardless of which date is being viewed.

    work_type restricts the lookup to PRODUCTION or QA rows specifically —
    an employee can have both for the same project/date, and a QA row's
    fallback should never pick up a Production target (or vice versa).
    Pass None to match either (used where the row isn't work-type-scoped)."""
    qs = DailyProduction.objects.filter(employee_id=employee_id, date__lt=before_date)
    qs = qs.filter(project_id=project_id) if project_id else qs.filter(project__isnull=True)
    if work_type:
        qs = qs.filter(work_type=work_type)
    last = qs.order_by('-date', '-id').first()
    return last.target_day if last else None


def _locked_hour_slots(employee, date, exclude_daily_id=None):
    """slot_index -> {'project': label, 'count': n} for every clock hour
    already filled on this employee's OTHER DailyProduction rows for
    `date` (any project/work type). A given hour can only ever belong to
    one project on one day, so these slots must be treated as read-only
    and unavailable everywhere else the employee opens an entry for that
    date -- this is what stops the same 2 worked hours being logged again
    against a second project."""
    qs = DailyProduction.objects.filter(employee=employee, date=date).select_related('project')
    if exclude_daily_id:
        qs = qs.exclude(id=exclude_daily_id)
    locked = {}
    for r in qs.prefetch_related('hourly_entries'):
        for e in r.hourly_entries.all():
            if e.count is not None:
                locked[e.slot_index] = {
                    'project': r.project.name if r.project else r.get_work_type_display(),
                    'count': e.count,
                }
    return locked


@login_required
def daily_entry(request):
    """Employee self-service: fill a daily production row plus its hourly
    counts. QA/Production employees can have SEVERAL rows on the same
    date (one per project they worked that day) — pick which one with
    ?row=<id>, or start a fresh one with ?new=1. SSH/HOR-style employees
    who only ever touch one project a day just get a single row, same as
    before.

    PL/TL, PM and GM don't have a personal sheet of their own â€” they work
    from the Team Overview dashboard instead, so they're redirected there.
    """
    if request.user.role in ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER') or _is_project_lead_or_tl(request.user):
        return redirect('production_tracker:dashboard')

    if not _is_production_employee(request.user):
        raise PermissionDenied("You do not have permission to view this page.")

    selected_date = _parse_date(request.GET.get('date') or request.POST.get('date'))
    today = timezone.localdate()
    if selected_date > today:
        selected_date = today

    day_records = list(
        DailyProduction.objects.filter(employee=request.user, date=selected_date)
        .select_related('project', 'process', 'event', 'grouping')
        .order_by('id')
    )

    row_id = request.GET.get('row') or request.POST.get('row_id')
    is_new = (request.GET.get('new') == '1') or (request.POST.get('new_row') == '1')

    daily = None
    if not is_new and row_id and row_id.isdigit():
        daily = next((r for r in day_records if r.id == int(row_id)), None)
    if daily is None and not is_new:
        daily = day_records[0] if day_records else None
    if daily is None:
        # A brand-new project row for this date — not saved to the DB
        # until the form is actually submitted, so a stray page load
        # never creates an empty row. Work Type is fixed by the
        # employee's own department, not a free choice on the form: a
        # Quality employee's rows are always QA, a Production employee's
        # are always Production.
        default_work_type = 'QA' if (
            request.user.department and request.user.department.is_qa
        ) else 'PRODUCTION'
        daily = DailyProduction(
            employee=request.user, date=selected_date, branch=request.user.branch,
            shift=getattr(request.user.profile, 'shift', '') if hasattr(request.user, 'profile') else '',
            work_type=default_work_type,
        )

    is_new_unsaved = daily.pk is None

    if not is_new_unsaved:
        # Make sure all 24 hourly slots exist so the form always has a
        # full set of inputs to render, in the same order every time.
        existing_slots = set(daily.hourly_entries.values_list('slot_index', flat=True))
        missing = [
            HourlyEntry(daily=daily, slot_index=idx, hour_start=start, hour_end=end)
            for idx, (start, end) in enumerate(HOUR_SLOTS) if idx not in existing_slots
        ]
        if missing:
            HourlyEntry.objects.bulk_create(missing)

    if request.method == 'POST':
        if not is_new_unsaved and daily.is_submitted and request.user.role == 'EMPLOYEE':
            messages.error(request, "This entry is already submitted and cannot be edited.")
            return redirect(f"{reverse('production_tracker:daily_entry')}?date={selected_date}&row={daily.id}")

        form = DailyProductionForm(request.POST, instance=daily, user=request.user)
        if form.is_valid():
            daily = form.save(commit=False)
            daily.employee = request.user
            daily.date = selected_date
            daily.is_submitted = True
            if not daily.branch_id:
                daily.branch = request.user.branch
            daily.sync_target_from_project()
            daily.save()

            if is_new_unsaved:
                HourlyEntry.objects.bulk_create([
                    HourlyEntry(daily=daily, slot_index=idx, hour_start=start, hour_end=end)
                    for idx, (start, end) in enumerate(HOUR_SLOTS)
                ])

            # Any clock hour already booked to another project row today
            # is locked -- ignore whatever was posted for it (the input is
            # disabled in the UI, but this is the real enforcement) so the
            # same worked hours can never be double-counted across projects.
            locked_map = _locked_hour_slots(request.user, selected_date, exclude_daily_id=daily.id)
            entries_by_slot = {e.slot_index: e for e in daily.hourly_entries.all()}
            for idx, _slot in enumerate(HOUR_SLOTS):
                entry = entries_by_slot[idx]
                if idx in locked_map:
                    entry.count = None
                else:
                    raw = (request.POST.get(f'hour_{idx}') or '').strip()
                    entry.count = int(raw) if raw.isdigit() else None
                entry.save()

            daily.recalculate()
            messages.success(request, f"Hourly production saved for {selected_date.strftime('%d-%b-%Y')}.")
            return redirect(f"{reverse('production_tracker:daily_entry')}?date={selected_date}&row={daily.id}")
    else:
        form = DailyProductionForm(instance=daily, user=request.user)

    if not is_new_unsaved and daily.is_submitted and request.user.role == 'EMPLOYEE':
        for field in form.fields.values():
            field.disabled = True

    # Hours already used by this employee's OTHER project rows today --
    # these slots are locked here: shown read-only with which project used
    # them, and not available to fill in again on this row.
    locked_map = _locked_hour_slots(
        request.user, selected_date, exclude_daily_id=daily.id if not is_new_unsaved else None
    )

    if is_new_unsaved:
        hourly_rows = [
            {
                'index': idx, 'label': f"{start} - {end}", 'count': None,
                'locked': idx in locked_map, 'locked_by': locked_map.get(idx),
            }
            for idx, (start, end) in enumerate(HOUR_SLOTS)
        ]
    else:
        # Re-fetch so counts render correctly on GET and after a failed POST.
        entries_by_slot = {e.slot_index: e for e in daily.hourly_entries.all()}
        hourly_rows = [
            {
                'index': idx, 'label': f"{start} - {end}",
                'count': entries_by_slot[idx].count if idx in entries_by_slot else None,
                'locked': idx in locked_map, 'locked_by': locked_map.get(idx),
            }
            for idx, (start, end) in enumerate(HOUR_SLOTS)
        ]

    # Day-wide totals across ALL of today's project rows (this row's own
    # filled hours + every locked hour used elsewhere), so the employee
    # always sees their real total worked hours for the day, not just
    # this one project's slice of it.
    own_used_slots = {row['index'] for row in hourly_rows if row['count'] is not None}
    day_used_slots = own_used_slots | set(locked_map.keys())
    day_hours_used = len(day_used_slots)
    day_hours_remaining = max(STANDARD_SHIFT_HOURS - day_hours_used, 0)

    # Map of project id -> {target, qa_target} so the form can instantly
    # show the manager's Target/Day the moment an employee picks a
    # project, without needing to save first (JS in daily_entry.html).
    project_targets = {
        str(pid): {'target': target or 0, 'qa_target': qa_target or 0}
        for pid, target, qa_target in form.fields['project'].queryset.values_list(
            'id', 'daily_target', 'qa_daily_target'
        )
    }

    context = {
        'form': form,
        'daily': daily,
        'selected_date': selected_date,
        'hourly_rows': hourly_rows,
        'today': today,
        'event_options': ProductionEvent.objects.filter(is_active=True).order_by('name'),
        'day_records': day_records,
        'is_new_unsaved': is_new_unsaved,
        'project_targets': project_targets,
        'day_hours_used': day_hours_used,
        'day_hours_remaining': day_hours_remaining,
        'day_total_hours': STANDARD_SHIFT_HOURS,
    }
    return render(request, 'production_tracker/daily_entry.html', context)


@login_required
def my_tracker(request):
    """Employee's own history, grouped like My Appraisals: this month's
    totals up top, then a card per day with the hourly breakdown."""
    if not _is_production_employee(request.user):
        raise PermissionDenied("You do not have permission to view this page.")

    today = timezone.localdate()
    month_start = today.replace(day=1)

    records = DailyProduction.objects.filter(employee=request.user).prefetch_related('hourly_entries').order_by('-date')

    month_records = [r for r in records if r.date >= month_start]
    total_target = sum(r.actual_target for r in month_records)
    total_achieved = sum(r.achieved for r in month_records)
    avg_efficiency = round((total_achieved / total_target * 100), 2) if total_target else 0

    context = {
        'records': records,
        'total_target': total_target,
        'total_achieved': total_achieved,
        'avg_efficiency': avg_efficiency,
    }
    return render(request, 'production_tracker/my_tracker.html', context)


@login_required
def employee_tracker_history(request, user_id):
    is_manager_role = request.user.role in ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'HR', 'SENIOR_HR', 'ADMIN', 'COO')
    is_tl_pl = _led_projects(request.user).exists()
    if not (is_manager_role or is_tl_pl):
        raise PermissionDenied("You do not have permission to view this page.")

    emp_user = get_object_or_404(User, id=user_id, role='EMPLOYEE')
    if emp_user.id in _tl_pl_user_ids():
        # A PL/TL has no personal hourly sheet or target/count of their
        # own — nothing to show on this page. Redirect quietly rather than
        # throwing a hard 403, since this is normally just a stale link
        # from a dashboard loaded before they were excluded from it.
        messages.info(request, "This user is a PL/TL and doesn't have a personal hourly tracker.")
        return redirect('production_tracker:dashboard')

    today = timezone.localdate()
    month_start = today.replace(day=1)

    records = DailyProduction.objects.filter(employee=emp_user).prefetch_related('hourly_entries').order_by('-date')

    month_records = [r for r in records if r.date >= month_start]
    total_target = sum(r.actual_target for r in month_records)
    total_achieved = sum(r.achieved for r in month_records)
    avg_efficiency = round((total_achieved / total_target * 100), 2) if total_target else 0

    context = {
        'records': records,
        'total_target': total_target,
        'total_achieved': total_achieved,
        'avg_efficiency': avg_efficiency,
        'is_manager_view': True,
        'emp_user': emp_user,
    }
    return render(request, 'production_tracker/my_tracker.html', context)


@login_required
def tracker_dashboard(request):
    """Team overview for PL/TL, PM, GM and HR â€” grouped by PROJECT so the
    Daily Target is set ONCE per project (see set_project_target) and
    applies to every current member automatically, instead of being typed
    in separately for each employee.

    PL/TL (project lead, or a current 'Team Leader' assignment â€” the same
    role/person) only see the project(s) they lead. PM/GM/HR/Admin/COO see
    every Production employee, branch-scoped the same way appraisal_dashboard
    is. Employees with no current project assignment fall into a separate
    'Unassigned' section that still supports the old per-employee target.
    """
    # HR, Admin, COO see all projects/employees globally (subject to branch scoping)
    is_hr_admin = request.user.role in ('HR', 'SENIOR_HR', 'ADMIN', 'COO')
    # PM, GM and Manager only see projects they are assigned to manage or lead
    is_ops_manager = request.user.role in ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER')
    # A QA Manager doesn't get listed as Project.manager/lead the way a
    # Production PM does â€” they oversee whichever QA employees are
    # currently working, across however many projects that spans. So their
    # view (and set_project_target permission below) is derived from their
    # own department's current assignments instead.
    is_qa_dept_manager = (
        request.user.role == 'MANAGER' and request.user.department
        and request.user.department.is_qa
    )

    led_projects = _led_projects(request.user)
    is_tl_pl = led_projects.exists()
    if not (is_hr_admin or is_ops_manager or is_tl_pl):
        raise PermissionDenied("You do not have permission to view this page.")

    from projects.models import ProjectAssignment, Project

    selected_date = _parse_date(request.GET.get('date'))

    if is_hr_admin:
        from django.db.models import Q
        employees = User.objects.filter(
            role='EMPLOYEE',
            profile__status='ACTIVE'
        ).filter(
            Q(department__name__icontains='Production') |
            Q(department__is_qa=True)
        )
        if not (request.user.role == 'ADMIN' or getattr(request.user, 'can_access_all_branches', False)):
            employees = employees.filter(branch=request.user.branch)
        assignments = ProjectAssignment.objects.filter(
            user__in=employees, is_current=True
        ).exclude(project__status='COMPLETED').select_related('project')
    elif is_qa_dept_manager:
        from django.db.models import Q
        # A QA Manager needs to see every project in scope up front — not
        # just ones a QA employee already happens to be on — so they can
        # set the QA Daily Target on a project before anyone's assigned to
        # it yet. Scoped the same way project_list scopes projects to a
        # branch: via the project's manager, creator, or any assignee.
        qa_dept_projects = Project.objects.exclude(status='COMPLETED')
        if not getattr(request.user, 'can_access_all_branches', False):
            qa_dept_projects = qa_dept_projects.filter(
                Q(manager__branch=request.user.branch) |
                Q(created_by__branch=request.user.branch) |
                Q(assignments__user__branch=request.user.branch)
            ).distinct()

        qa_employees = User.objects.filter(
            role='EMPLOYEE', department=request.user.department, profile__status='ACTIVE'
        )
        if not getattr(request.user, 'can_access_all_branches', False):
            qa_employees = qa_employees.filter(branch=request.user.branch)
        assignments = ProjectAssignment.objects.filter(
            user__in=qa_employees, is_current=True
        ).exclude(project__status='COMPLETED').select_related('project', 'user')
        assigned_ids = set(assignments.values_list('user_id', flat=True))
        # A QA employee can log hours against ANY branch project on the
        # daily entry form (see DailyProductionForm), not just ones they
        # have a formal current ProjectAssignment to — so anyone who
        # already submitted a QA entry for this date must be included too,
        # or their entry silently never appears here even though it was
        # saved (see daily_records_for_date below for how they get
        # slotted into the right project group).
        submitted_ids = set(
            DailyProduction.objects.filter(
                employee__in=qa_employees, date=selected_date, work_type='QA'
            ).values_list('employee_id', flat=True)
        )
        employees = User.objects.filter(id__in=(assigned_ids | submitted_ids))
    elif is_ops_manager:
        managed_ids = set(Project.objects.filter(manager=request.user).values_list('id', flat=True))
        managed_ids |= set(Project.objects.filter(gm=request.user).values_list('id', flat=True))
        managed_ids |= set(Project.objects.filter(lead=request.user).values_list('id', flat=True))
        managed_ids |= set(ProjectAssignment.objects.filter(user=request.user, is_current=True).values_list('project_id', flat=True))
        
        assignments = ProjectAssignment.objects.filter(
            project_id__in=managed_ids, is_current=True, user__role='EMPLOYEE',
            user__profile__status='ACTIVE'
        ).exclude(project__status='COMPLETED').select_related('project', 'user')
        if not getattr(request.user, 'can_access_all_branches', False):
            assignments = assignments.filter(user__branch=request.user.branch)
        assigned_ids = set(assignments.values_list('user_id', flat=True))
        # Same gap as the QA Manager branch above: an employee can submit
        # an entry for any of this PM's managed projects without having a
        # formal current ProjectAssignment to it — include them too so
        # their entry doesn't silently vanish from Team Overview.
        submitted_ids = set(
            DailyProduction.objects.filter(
                project_id__in=managed_ids, date=selected_date,
                employee__role='EMPLOYEE', employee__profile__status='ACTIVE'
            ).values_list('employee_id', flat=True)
        )
        employees = User.objects.filter(id__in=(assigned_ids | submitted_ids))
    else:
        assignments = ProjectAssignment.objects.filter(
            project__in=led_projects, is_current=True, user__role='EMPLOYEE',
            user__profile__status='ACTIVE'
        ).exclude(project__status='COMPLETED').select_related('project', 'user')
        employees = User.objects.filter(id__in=assignments.values_list('user_id', flat=True))

    # A PL/TL still has role='EMPLOYEE' on their account, so every branch
    # above can otherwise pull them in as a trackable row. They don't have
    # a personal sheet or a target/count of their own — exclude them here,
    # once, regardless of which branch built `employees`.
    employees = employees.exclude(id__in=_tl_pl_user_ids())

    # We don't need a single employee_project dict anymore
    # because employees can be assigned to multiple projects simultaneously,
    # and QA/Production employees can also have several rows for the SAME
    # project on one date (e.g. a normal-production row plus a separate
    # permission row) â€” so each employee can carry a LIST of rows here,
    # not just one.

    day_records_by_emp = OrderedDict()
    for r in DailyProduction.objects.filter(date=selected_date, employee__in=employees).select_related('project'):
        day_records_by_emp.setdefault(r.employee_id, []).append(r)

    project_groups = OrderedDict()
    unassigned_rows = []

    if is_qa_dept_manager:
        # Seed every in-scope project now, even ones with no QA member on
        # them yet, so the QA Manager can open the target form for any of
        # them. Projects that do have current QA members get filled in
        # below by the normal employee loop.
        for proj in qa_dept_projects.order_by('name'):
            project_groups.setdefault(proj, [])

    # Map user ID to their active assignments for fast lookup
    user_assignments = {}
    for a in assignments:
        user_assignments.setdefault(a.user_id, []).append(a.project)

    for emp in employees:
        emp_records = day_records_by_emp.get(emp.id, [])
        projs = user_assignments.get(emp.id)
        # Projects reachable from formal assignment PLUS any project this
        # employee actually submitted a record against today — an
        # employee can log hours against any branch project (see
        # DailyProductionForm), not only ones they're formally assigned
        # to, so a submitted record must still surface under its real
        # project instead of silently landing in "Unassigned".
        record_proj_ids = {r.project_id for r in emp_records if r.project_id}
        proj_ids_covered = {p.id for p in projs} if projs else set()
        extra_proj_ids = record_proj_ids - proj_ids_covered
        all_projs = list(projs) if projs else []
        if extra_proj_ids:
            all_projs += list(Project.objects.filter(id__in=extra_proj_ids))

        if all_projs:
            # Add this employee's row(s) to EVERY project they are assigned
            # to (or have a submitted entry for) so managers of any of
            # their projects can see them.
            for proj in all_projs:
                proj_records = [r for r in emp_records if r.project_id == proj.id]
                row = {'user': emp, 'records': proj_records}
                if not proj_records:
                    wt = 'QA' if is_qa_dept_manager else 'PRODUCTION'
                    row['fallback_target'] = _last_target(emp.id, proj.id, selected_date, work_type=wt)
                project_groups.setdefault(proj, []).append(row)
        else:
            no_project_records = [r for r in emp_records if not r.project_id]
            row = {'user': emp, 'records': no_project_records}
            if not no_project_records:
                wt = 'QA' if is_qa_dept_manager else 'PRODUCTION'
                row['fallback_target'] = _last_target(emp.id, None, selected_date, work_type=wt)
            unassigned_rows.append(row)

    can_set_target = request.user.role in ('GENERAL_MANAGER', 'PROJECT_MANAGER', 'MANAGER') or is_tl_pl
    # Production Target: PM/GM/TL (and Admin) — never the QA Manager.
    # QA Target: the QA Manager only.
    can_set_production_target = can_set_target and not is_qa_dept_manager
    can_set_qa_target = is_qa_dept_manager
    context = {
        'project_groups': list(project_groups.items()),
        'unassigned_rows': unassigned_rows,
        'selected_date': selected_date,
        'today': timezone.localdate(),
        'can_set_target': can_set_target,
        'is_qa_dept_manager': is_qa_dept_manager,
        'can_set_production_target': can_set_production_target,
        'can_set_qa_target': can_set_qa_target,
    }
    return render(request, 'production_tracker/dashboard.html', context)


@login_required
def set_project_target(request, project_id):
    """PL/TL (or PM/GM) set ONE Daily Target for the whole project here â€”
    it's pushed onto every currently assigned member's row for the
    selected date, so nobody has to type a number per employee anymore.
    Production and QA get separate targets (project.daily_target /
    project.qa_daily_target), selected via the work_type POST field."""
    from django.db.models import Q
    from projects.models import Project, ProjectAssignment
    project = get_object_or_404(Project, id=project_id)
    selected_date = _parse_date(request.POST.get('date'))
    work_type = request.POST.get('work_type') if request.POST.get('work_type') in ('PRODUCTION', 'QA') else 'PRODUCTION'

    is_hr_admin = request.user.role in ('COO', 'ADMIN')
    is_ops_manager = request.user.role in ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER')
    is_qa_dept_manager = (
        request.user.role == 'MANAGER' and request.user.department
        and request.user.department.is_qa
    )
    is_tl_pl = project.lead_id == request.user.id

    allowed = False
    if is_hr_admin:
        allowed = True
    elif is_qa_dept_manager:
        # No Project.manager/lead entry to check here — a QA Manager can
        # set the QA target for any project in their branch's scope, even
        # before any of their QA employees are assigned to it yet, so the
        # target is ready the moment someone's added. Scoped the same way
        # the dashboard scopes the project list.
        if getattr(request.user, 'can_access_all_branches', False):
            allowed = True
        else:
            allowed = Project.objects.filter(id=project.id).filter(
                Q(manager__branch=request.user.branch) |
                Q(created_by__branch=request.user.branch) |
                Q(assignments__user__branch=request.user.branch)
            ).exists()
    elif is_ops_manager or is_tl_pl:
        if project.manager_id == request.user.id or project.lead_id == request.user.id or project.gm_id == request.user.id:
            allowed = True
        elif ProjectAssignment.objects.filter(project=project, user=request.user, is_current=True).exists():
            allowed = True

    if not allowed:
        raise PermissionDenied("You don't have permission to set the target for this project.")

    if selected_date < timezone.localdate():
        messages.error(request, "You can't change the target for a past date — only today or a future date.")
        return redirect(f"{reverse('production_tracker:dashboard')}?date={selected_date}")

    if is_qa_dept_manager and work_type != 'QA':
        raise PermissionDenied("QA Managers can only set the QA target.")

    if (is_ops_manager or is_tl_pl) and not is_qa_dept_manager and work_type == 'QA':
        raise PermissionDenied("Only the QA Manager can set the QA target.")

    if request.method == 'POST':
        raw = (request.POST.get('daily_target') or '').strip()
        if not raw.isdigit():
            messages.error(request, "Enter a valid target.")
            return redirect(f"{reverse('production_tracker:dashboard')}?date={selected_date}")

        if work_type == 'QA':
            project.qa_daily_target = int(raw)
            project.save(update_fields=['qa_daily_target'])
        else:
            project.daily_target = int(raw)
            project.save(update_fields=['daily_target'])
        updated = apply_project_daily_target(project, selected_date, work_type=work_type)
        label = 'QA Daily Target' if work_type == 'QA' else 'Daily Target'
        messages.success(
            request,
            f'{label} set to {raw} for "{project.name}" â€” applied to {updated} team member(s) on {selected_date.strftime("%d-%b-%Y")}.'
        )

    return redirect(f"{reverse('production_tracker:dashboard')}?date={selected_date}")


@role_required('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER')
def set_target(request, user_id):
    """Manual per-employee Target/Day â€” kept only for employees with no
    current project assignment. Once an employee is on a project, that
    project's Daily Target always wins (see set_project_target)."""
    emp_user = get_object_or_404(User, id=user_id, role='EMPLOYEE')
    selected_date = _parse_date(request.POST.get('date'))

    if request.user.role == 'ADMIN':
        raise PermissionDenied("Admins can only view targets, not set them.")

    if not getattr(request.user, 'can_access_all_branches', False):
        if emp_user.branch_id != request.user.branch_id:
            raise PermissionDenied("You can't set a target for an employee outside your branch.")

    if request.method == 'POST':
        raw = (request.POST.get('target_day') or '').strip()
        if not raw.isdigit():
            messages.error(request, "Enter a valid target.")
            return redirect(f"{reverse('production_tracker:dashboard')}?date={selected_date}")

        # Update existing record instead of creating blank 0-hour drafts.
        dailies = DailyProduction.objects.filter(
            employee=emp_user,
            date=selected_date,
            project=None,
        )
        if dailies.exists():
            daily = dailies.first()
            daily.target_day = int(raw)
            # If this employee's row is linked to a project that has its own
            # Daily Target configured, that project value always wins — target
            # setting is meant to happen once on the Project now, not per
            # employee. Manual entry here only sticks for employees with no
            # project (or a project with no target configured).
            daily.sync_target_from_project()
            daily.save(update_fields=['target_day', 'updated_at'])
            daily.recalculate()
            if daily.project_id and daily.project.daily_target:
                messages.success(
                    request,
                    f"{emp_user} is on \"{daily.project.name}\", which has a Daily Target of "
                    f"{daily.project.daily_target} — that's what was applied (your entry of {raw} was overridden)."
                    if int(raw) != daily.target_day else
                    f"Target set to {raw} for {emp_user} on {selected_date.strftime('%d-%b-%Y')}."
                )
            else:
                messages.success(request, f"Target set to {raw} for {emp_user} on {selected_date.strftime('%d-%b-%Y')}.")
        else:
            messages.warning(request, f"No existing entry found for {emp_user} on {selected_date.strftime('%d-%b-%Y')} to apply target to.")

    return redirect(f"{reverse('production_tracker:dashboard')}?date={selected_date}")


@role_required('MANAGER', 'PROJECT_MANAGER', 'HR', 'SENIOR_HR', 'ADMIN', 'COO')
def export_tracker_excel(request):
    selected_date = _parse_date(request.GET.get('date'))
    month_start = selected_date.replace(day=1)
    
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)
        
    from django.db.models import Q
    employees = User.objects.filter(
        role='EMPLOYEE',
        profile__status='ACTIVE'
    ).filter(
        Q(department__name__icontains='Production') |
        Q(department__is_qa=True)
    ).exclude(id__in=_tl_pl_user_ids())
    if not (request.user.role == 'ADMIN' or getattr(request.user, 'can_access_all_branches', False)):
        employees = employees.filter(branch=request.user.branch)
    records = DailyProduction.objects.filter(
        employee__in=employees,
        date__gte=month_start,
        date__lt=month_end
    ).select_related('employee', 'project', 'process', 'event', 'grouping').prefetch_related(
        'hourly_entries'
    ).order_by('date', 'employee__employee_id')
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Monthly Report"

    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from itertools import groupby

    # ---------- Style palette (matches the Appraisal report) ----------
    NAVY = '1F3864'
    STRIPE_FILL = 'F2F6FC'
    TITLE_FONT = Font(bold=True, size=15, color=NAVY, name='Calibri')
    SUBTITLE_FONT = Font(italic=True, size=10, color='595959', name='Calibri')
    HEADER_FONT = Font(bold=True, size=10, color='FFFFFF', name='Calibri')
    HEADER_FILL = PatternFill(start_color=NAVY, end_color=NAVY, fill_type='solid')
    HEADER_ALIGN = Alignment(wrap_text=True, horizontal='center', vertical='center')
    DATA_FONT = Font(size=10, name='Calibri')
    thin = Side(style='thin', color='D9D9D9')
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

    headers = [
        'Sl. No', 'Branch\nCode', 'Month', 'Date', 'Production\nStart Date', 'EMPID', 'Names', 
        'Shift', 'Work Type', 'Project', 'Process', 'Event', 'Target/\nDay', 'Achieved', 'Deviation', 
        'No of Hrs\nWorked', 'Actual Target', '%', 'Error Count', 'Reason', 'Grouping'
    ]
    
    for start, end in HOUR_SLOTS:
        headers.append(f"{start} -\n{end}")
        
    headers.extend(["PL's comments", 'PM Comments', 'Target / HR'])
    n_cols = len(headers)

    # ---------- Title block ----------
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    ws.cell(row=1, column=1, value='MONTHLY PRODUCTION TRACKER REPORT')
    ws.cell(row=1, column=1).font = TITLE_FONT
    ws.cell(row=1, column=1).alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 24

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n_cols)
    ws.cell(row=2, column=1, value=f"Reporting Month: {month_start.strftime('%B %Y')}")
    ws.cell(row=2, column=1).font = SUBTITLE_FONT
    ws.cell(row=2, column=1).alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 16

    ws.append([])  # blank spacer row 3

    def write_header_row(row_idx):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=row_idx, column=c, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = HEADER_ALIGN
            cell.border = BORDER
        ws.row_dimensions[row_idx].height = 32

    first_header_row = 4
    write_header_row(first_header_row)
    first_data_row = first_header_row + 1
    current_row = first_data_row

    # ---------- Grouped by DATE: everyone who worked that day, together,
    # under a repeated mini-header — one block per date, blank-row
    # separated, so a manager can read straight down a single day
    # instead of hunting one employee's rows across the whole month.
    for work_date, day_records in groupby(records, key=lambda r: r.date):
        day_records = list(day_records)

        for idx, record in enumerate(day_records, 1):
            emp = record.employee
            branch_code = emp.branch.code if emp.branch else '-'
            month_str = record.date.strftime('%b-%Y')
            date_val = record.date
            prod_start_date = emp.date_joined_company.strftime('%Y-%m-%d') if emp.date_joined_company else '-'
            empid = emp.employee_id or '-'
            name = emp.get_full_name() or emp.username
            shift = record.shift or '-'
            work_type = record.get_work_type_display()
            project = record.project.name if record.project else '-'
            process = record.process.name if record.process else '-'
            event = record.event.name if record.event else '-'

            target_day = record.target_day or 0
            achieved = record.achieved or 0
            eff = float(record.efficiency_percentage) / 100.0 if record.efficiency_percentage else 0.0
            err_count = record.error_count or 0
            hrs_worked = record.hours_worked or 0
            actual_target = record.actual_target or 0
            deviation = achieved - actual_target
            reason = record.reason or '-'
            grouping = record.grouping.name if record.grouping else '-'

            row = [
                idx, branch_code, month_str, date_val, prod_start_date, empid, name,
                shift, work_type, project, process, event, target_day, achieved, deviation,
                hrs_worked, actual_target, eff, err_count, reason, grouping
            ]

            entries_by_slot = {e.slot_index: e.count for e in record.hourly_entries.all()}
            for slot_idx, _ in enumerate(HOUR_SLOTS):
                row.append(entries_by_slot.get(slot_idx, None))

            row.append(record.pl_comments or '-')
            row.append(record.pm_comments or '-')
            target_hr = round(target_day / 8, 2) if target_day else 0
            row.append(target_hr)

            ws.append(row)

            # Style the newly appended row: borders, font, zebra striping,
            # centered alignment, and number formats for date/percentage columns.
            row_fill = PatternFill(start_color=STRIPE_FILL, end_color=STRIPE_FILL, fill_type='solid') if idx % 2 == 0 else None
            for c in range(1, n_cols + 1):
                cell = ws.cell(row=current_row, column=c)
                cell.font = DATA_FONT
                cell.border = BORDER
                cell.alignment = Alignment(horizontal='center', vertical='center') if c != 20 else Alignment(horizontal='left', vertical='center')
                if row_fill:
                    cell.fill = row_fill
            ws.cell(row=current_row, column=4).number_format = 'YYYY-MM-DD'  # Date
            ws.cell(row=current_row, column=18).number_format = '0.00%'      # Efficiency %
            current_row += 1

        # Blank spacer row, then a repeated header row before the next date's block.
        current_row += 1
        write_header_row(current_row)
        current_row += 1

    last_row = current_row - 1

    # ---------- Auto-fit columns based on content ----------
    for col_idx in range(1, n_cols + 1):
        column_letter = get_column_letter(col_idx)
        max_len = 0
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, min_row=first_header_row, max_row=max(last_row, first_header_row)):
            for cell in row:
                if cell.value is not None:
                    cell_val = str(cell.value)
                    for line in cell_val.split('\n'):
                        max_len = max(max_len, len(line))
        # Padding, capped so no single column dominates (reasons/comments get more room)
        adjusted_width = max(9, min(max_len + 2, 30))
        ws.column_dimensions[column_letter].width = adjusted_width

    # A few columns benefit from a bit more room regardless of content length
    ws.column_dimensions['G'].width = max(ws.column_dimensions['G'].width, 18)  # Names
    ws.column_dimensions['J'].width = max(ws.column_dimensions['J'].width, 15)  # Project
    ws.column_dimensions['L'].width = max(ws.column_dimensions['L'].width, 20)  # Event
    ws.column_dimensions['T'].width = max(ws.column_dimensions['T'].width, 25)  # Reason

    # ---------- Sheet-wide polish ----------
    ws.freeze_panes = get_column_letter(1) + str(first_data_row)
    ws.sheet_view.showGridLines = False
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Hourly_Tracker_Report_{month_start.strftime("%b_%Y")}.xlsx"'
    wb.save(response)
    return response
