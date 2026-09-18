from datetime import date, timedelta
from decimal import Decimal
from django.utils import timezone as dj_timezone

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q


from core.decorators import hr_or_admin_required, hr_only_required
from core.models import User
from employees.models import EmployeeProfile
from increments.models import IncrementRequest,  IncrementCycleSkip, IncrementFeedback, CooReport
from increments.forms import IncrementRequestForm, IncrementFeedbackForm, REPORT_FILE_EXTENSIONS
from payroll.models import SalaryStructure
from projects.models import ProjectAssignment
from increments.forms import IncrementRequestForm, ManagerIncrementRequestForm, IncrementFeedbackForm


def _report_file_extension_ok(uploaded_file):
    return uploaded_file.name.rsplit('.', 1)[-1].lower() in REPORT_FILE_EXTENSIONS if '.' in uploaded_file.name else False


from core.decorators import coo_required

def _add_years(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Feb 29 landing on a non-leap year
        return d.replace(month=2, day=28, year=d.year + years)


def _last_increment_date(user):
    """The date their pay was last actually increased, or their joining
    date if they've never had one — this is the baseline for the next
    one-year anniversary."""
    last = IncrementRequest.objects.filter(user=user, status='APPROVED').order_by('-effective_date').first()
    if last:
        return last.effective_date
    return user.date_joined_company


def due_for_annual_increment_count(active_branch=None):
    """How many active employees hit their one-year anniversary within the
    next 30 days (same rule as Section 2 of increment_list). Used to badge
    the Increments sidebar link so HR notices without opening the page."""
    today = timezone.localdate()
    window_end = today + timedelta(days=30)
    pending_user_ids = set(
        IncrementRequest.objects.filter(status='PENDING').values_list('user_id', flat=True)
    )
    active_profiles = EmployeeProfile.objects.filter(
        status='ACTIVE'
    ).exclude(user_id__in=pending_user_ids).select_related('user')
    if active_branch:
        active_profiles = active_profiles.filter(user__branch=active_branch)

    count = 0
    for profile in active_profiles:
        user = profile.user
        if not user or not user.pk:
            continue
        base_date = _last_increment_date(user)
        if not base_date:
            continue
        anniversary = _add_years(base_date, 1)
        if anniversary > window_end:
            continue
        if IncrementCycleSkip.objects.filter(user=user, anniversary_date=anniversary).exists():
            continue
        count += 1
    return count


@hr_only_required
def increment_list(request):
    today = timezone.localdate()
    active_branch = getattr(request, 'active_branch', None)

    all_requests = IncrementRequest.objects.select_related(
        'user', 'feedback_manager', 'approved_by'
    ).prefetch_related('feedback').order_by('-created_at')
    if active_branch:
        all_requests = all_requests.filter(user__branch=active_branch)

    # ---------- Section 1: Active Increment Requests ----------
    seen_users = set()
    in_progress = []
    for inc in all_requests.filter(status='PENDING'):
        if inc.user_id in seen_users:
            continue
        seen_users.add(inc.user_id)
        in_progress.append(inc)
    pending_user_ids = {inc.user_id for inc in in_progress}

    # ---------- Section 2: Due for Annual Increment ----------
    window_end = today + timedelta(days=30)
    active_profiles = EmployeeProfile.objects.filter(
        status='ACTIVE'
    ).exclude(user_id__in=pending_user_ids).select_related('user')
    if active_branch:
        active_profiles = active_profiles.filter(user__branch=active_branch)

    due_soon = []
    for profile in active_profiles:
        user = profile.user
        # Guard: skip any profile whose linked user is missing or has no pk.
        if not user or not user.pk:
            continue

        base_date = _last_increment_date(user)
        if not base_date:
            continue
        anniversary = _add_years(base_date, 1)
        if anniversary > window_end:
            continue
        if IncrementCycleSkip.objects.filter(user=user, anniversary_date=anniversary).exists():
            continue
        try:
            current_basic = user.salary_structure.basic
        except SalaryStructure.DoesNotExist:
            current_basic = 0
        due_soon.append({
            'user': user,
            'anniversary_date': anniversary,
            'days_left': (anniversary - today).days,
            'last_increment_date': base_date,
            'current_basic': current_basic,
        })
    due_soon.sort(key=lambda d: d['anniversary_date'])

    # ---------- Section 3: Increment History ----------
    history_by_user = {}
    for inc in all_requests.exclude(status='PENDING'):
        if not inc.user_id:
            continue
        entry = history_by_user.setdefault(inc.user_id, {'user': inc.user, 'increments': [], 'latest': inc})
        entry['increments'].append(inc)
    history_cards = sorted(history_by_user.values(), key=lambda h: h['latest'].created_at, reverse=True)

    just_completed = None
    confirm_id = request.GET.get('confirm')
    if confirm_id:
        just_completed = IncrementRequest.objects.filter(id=confirm_id).select_related('user').first()

    return render(request, 'increments/list.html', {
        'in_progress': in_progress,
        'due_soon': due_soon,
        'history_cards': history_cards,
        'just_completed': just_completed,
    })

def _current_pm_for(user):
    """The Project Manager currently assigned to this employee, via their
    current project assignment on a project led by a Project Manager.
    Mirrors the same lookup used to route leave requests to a PM."""
    assignment = ProjectAssignment.objects.filter(
        user=user, is_current=True,
        project__manager__role='PROJECT_MANAGER',
        project__status__in=['APPROVED', 'COMPLETED'],
    ).select_related('project__manager').order_by('-project__start_date').first()
    return assignment.project.manager if assignment else None


def _current_gm_for(pm_user):
    """The General Manager this Project Manager reports to, via
    department headship or a direct manager link — same lookup pattern
    core.utils.get_manager_team uses in reverse."""
    dept = pm_user.department
    if dept and dept.manager_id and dept.manager.role == 'GENERAL_MANAGER':
        return dept.manager
    if pm_user.manager_id and pm_user.manager.role == 'GENERAL_MANAGER':
        return pm_user.manager
    return None

@hr_only_required
def create_increment(request):
    preset_user_id = request.GET.get('user')
    active_branch = getattr(request, 'active_branch', None)
    active_tab = request.GET.get('tab', 'employee')

    employee_form = None
    manager_form = None

    if request.method == 'POST':
        form_type = request.POST.get('form_type', 'employee')
        active_tab = form_type

        if form_type == 'manager':
            manager_form = ManagerIncrementRequestForm(
                request.POST, request.FILES, acting_user=request.user, branch=active_branch
            )
            employee_form = IncrementRequestForm(acting_user=request.user, branch=active_branch)

            if manager_form.is_valid():
                mgr_users = list(manager_form.cleaned_data['user'])
                percent = manager_form.cleaned_data['increment_percent']
                manual_current_basic = manager_form.cleaned_data.get('current_basic')
                reason = manager_form.cleaned_data.get('reason', '')

                for mgr_user in mgr_users:
                    if len(mgr_users) == 1 and manual_current_basic is not None:
                        current_basic = manual_current_basic
                    else:
                        try:
                            current_basic = mgr_user.salary_structure.basic
                        except SalaryStructure.DoesNotExist:
                            current_basic = Decimal('0')
                    requested_basic = round(float(current_basic) * (1 + float(percent) / 100), 2)

                    increment = IncrementRequest.objects.create(
                        user=mgr_user,
                        current_basic=current_basic,
                        requested_basic=requested_basic,
                        effective_date=manager_form.cleaned_data['effective_date'],
                        reason=reason,
                        requested_by=request.user,
                        forwarded_by=request.user,
                        forwarded_at=dj_timezone.now(),
                        hr_notes=reason,
                        status='PENDING_COO',
                    )
                    IncrementFeedback.objects.create(
                        increment_request=increment,
                        manager=None,
                        suggested_percent=percent,
                        report_file=manager_form.cleaned_data['report_file'],
                        description=reason,
                    )
                if len(mgr_users) == 1:
                    messages.success(request, f"Increment request for {mgr_users[0]} sent to the COO for approval.")
                else:
                    names = ', '.join(str(m) for m in mgr_users)
                    messages.success(request, f"Increment requests for {len(mgr_users)} managers ({names}) sent to the COO for approval.")
                return redirect('increments:list')
        else:
            employee_form = IncrementRequestForm(request.POST, acting_user=request.user, branch=active_branch)
            manager_form = ManagerIncrementRequestForm(acting_user=request.user, branch=active_branch)

            if employee_form.is_valid():
                emp_users = list(employee_form.cleaned_data['user'])
                manual_feedback_manager = employee_form.cleaned_data.get('feedback_manager')
                manual_current_basic = employee_form.cleaned_data.get('current_basic')
                created_for_feedback = []
                created_direct = []

                for emp_user in emp_users:
                    if len(emp_users) == 1:
                        if emp_user.role in ('EMPLOYEE', 'PROJECT_MANAGER'):
                            if manual_feedback_manager:
                                feedback_manager = manual_feedback_manager
                            elif emp_user.role == 'PROJECT_MANAGER':
                                feedback_manager = _current_gm_for(emp_user)
                            else:
                                feedback_manager = _current_pm_for(emp_user)
                        else:
                            feedback_manager = None
                        current_basic = manual_current_basic
                    else:
                        if emp_user.role == 'PROJECT_MANAGER':
                            feedback_manager = _current_gm_for(emp_user)
                        else:
                            feedback_manager = _current_pm_for(emp_user)
                        try:
                            current_basic = emp_user.salary_structure.basic
                        except SalaryStructure.DoesNotExist:
                            current_basic = Decimal('0')

                    IncrementRequest.objects.create(
                        user=emp_user,
                        current_basic=current_basic,
                        requested_basic=employee_form.cleaned_data.get('requested_basic'),
                        effective_date=employee_form.cleaned_data['effective_date'],
                        reason=employee_form.cleaned_data.get('reason', ''),
                        requested_by=request.user,
                        feedback_manager=feedback_manager,
                        status='PENDING',
                    )
                    if feedback_manager:
                        created_for_feedback.append(str(emp_user))
                    else:
                        created_direct.append(str(emp_user))

                if len(emp_users) == 1:
                    if created_for_feedback:
                        messages.success(request, f"Increment request sent to {feedback_manager} for feedback.")
                    else:
                        messages.success(request, f"Increment request created for {emp_users[0]}.")
                else:
                    parts = []
                    if created_for_feedback:
                        parts.append(f"{len(created_for_feedback)} sent for feedback ({', '.join(created_for_feedback)})")
                    if created_direct:
                        parts.append(f"{len(created_direct)} created directly ({', '.join(created_direct)})")
                    messages.success(request, f"Increment requests created for {len(emp_users)} employees — " + "; ".join(parts) + ".")
                return redirect('increments:list')
    else:
        initial = {}
        if preset_user_id:
            initial['user'] = [preset_user_id]
        employee_form = IncrementRequestForm(acting_user=request.user, branch=active_branch, initial=initial)
        manager_form = ManagerIncrementRequestForm(acting_user=request.user, branch=active_branch)

    # ---------- Employee tab: dropdown auto-fill data ----------
    users_qs = User.objects.filter(role='EMPLOYEE').exclude(id=request.user.id)
    if active_branch:
        users_qs = users_qs.filter(branch=active_branch)
    users_qs = users_qs.select_related('department__manager', 'manager', 'salary_structure')

    user_roles_json = {}
    user_basic_json = {}
    user_manager_json = {}
    user_names_json = {}
    for u in users_qs:
        user_roles_json[str(u.id)] = u.role
        try:
            user_basic_json[str(u.id)] = float(u.salary_structure.basic)
        except SalaryStructure.DoesNotExist:
            user_basic_json[str(u.id)] = 0

        pm = _current_pm_for(u)
        user_manager_json[str(u.id)] = pm.id if pm else None
        user_names_json[str(u.id)] = u.get_full_name() or u.username

    # ---------- Manager tab: dropdown auto-fill data ----------
    manager_qs = User.objects.filter(role__in=['MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER']).exclude(id=request.user.id)
    if active_branch:
        manager_qs = manager_qs.filter(branch=active_branch)
    manager_qs = manager_qs.select_related('salary_structure')

    manager_role_json = {}
    manager_basic_json = {}
    manager_names_json = {}
    for m in manager_qs:
        manager_role_json[str(m.id)] = m.get_role_display()
        try:
            manager_basic_json[str(m.id)] = float(m.salary_structure.basic)
        except SalaryStructure.DoesNotExist:
            manager_basic_json[str(m.id)] = 0
        manager_names_json[str(m.id)] = m.get_full_name() or m.username

    return render(request, 'increments/create.html', {
        'form': employee_form,
        'manager_form': manager_form,
        'active_tab': active_tab,
        'user_roles_json': user_roles_json,
        'user_basic_json': user_basic_json,
        'user_manager_json': user_manager_json,
        'user_names_json': user_names_json,
        'manager_role_json': manager_role_json,
        'manager_basic_json': manager_basic_json,
        'manager_names_json': manager_names_json,
    })

@hr_only_required
def approve_increment(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)
    if increment.needs_feedback:
        messages.error(request, "This increment must be approved or rejected by the assigned manager, not HR.")
        return redirect('increments:list')

    increment.status = 'APPROVED'
    increment.approved_by = request.user
    increment.save()

    structure, _ = SalaryStructure.objects.get_or_create(user=increment.user, defaults={'basic': 0})
    structure.basic = increment.requested_basic
    structure.save()

    IncrementCycleSkip.objects.filter(user=increment.user).delete()
    return redirect(f"{reverse('increments:list')}?confirm={increment.id}")

@hr_only_required
def reject_increment(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)
    if increment.needs_feedback:
        messages.error(request, "This increment must be approved or rejected by the assigned manager, not HR.")
        return redirect('increments:list')

    increment.status = 'REJECTED'
    increment.approved_by = request.user
    increment.save()
    messages.info(request, f"Increment rejected for {increment.user}.")
    return redirect('increments:list')


@hr_only_required
def dismiss_due_increment(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    if request.method == 'POST':
        anniversary_str = request.POST.get('anniversary_date', '')
        try:
            y, m, d = (int(p) for p in anniversary_str.split('-'))
            IncrementCycleSkip.objects.get_or_create(
                user=emp_user, anniversary_date=date(y, m, d),
                defaults={'skipped_by': request.user},
            )
            messages.info(request, f"Dismissed the increment reminder for {emp_user} this cycle.")
        except (ValueError, TypeError):
            messages.error(request, "Something went wrong dismissing that reminder.")
    return redirect('increments:list')


@hr_or_admin_required
def increment_history_detail(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    increments = IncrementRequest.objects.filter(
        user=emp_user
    ).exclude(status='PENDING').select_related(
        'approved_by', 'feedback_manager', 'requested_by'
    ).prefetch_related('feedback').order_by('-effective_date')
    return render(request, 'increments/history_detail.html', {'emp_user': emp_user, 'increments': increments})


@login_required
def manager_feedback_list(request):
    if not (request.user.is_manager() or request.user.is_cto()):
        messages.error(request, "Only Managers can view this page.")
        return redirect('core:dashboard')

    # GM now gets personally assigned as feedback_manager on PM increment
    # requests (see IncrementRequestForm / _current_gm_for), same as a PM
    # gets assigned on an Employee's request — so this is a plain,
    # personal-assignment lookup for every role.
    increments = IncrementRequest.objects.filter(
        status='PENDING', feedback_manager=request.user
    ).select_related('user', 'feedback_manager').order_by('-created_at')
    return render(request, 'increments/manager_feedback_list.html', {'increments': increments})

@login_required
def submit_increment_feedback(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)

    if not (request.user.is_manager() or request.user.is_cto()) or increment.feedback_manager_id != request.user.id:
        messages.error(request, "You cannot act on this employee's increment.")
        return redirect('increments:manager_feedback_list')

    if increment.status != 'PENDING':
        messages.info(request, "This increment request has already moved past the feedback stage.")
        return redirect('increments:manager_feedback_list')

    existing_feedback = IncrementFeedback.objects.filter(increment_request=increment).first()

    if request.method == 'POST':
        form = IncrementFeedbackForm(request.POST, request.FILES, instance=existing_feedback)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.increment_request = increment
            feedback.manager = request.user
            feedback.save()

            increment.status = 'REPORT_SUBMITTED'
            increment.report_submitted_at = dj_timezone.now()
            increment.save()

            messages.success(request, f"Report and feedback submitted for {increment.user}. HR will review it next.")
            return redirect('increments:manager_feedback_list')
    else:
        form = IncrementFeedbackForm(instance=existing_feedback)

    return render(request, 'increments/submit_feedback.html', {'form': form, 'increment': increment})



# ---------- HR requests a performance report — routed straight into ----------
# ---------- the PM's "Increment Feedback" queue (status=PENDING).    ----------
@hr_only_required
def request_performance_report(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    dept = emp_user.department

    # Software department employees are monitored end-to-end by the CTO —
    # increment reports for them go to the CTO, not the department manager.
    if emp_user.is_software_dept():
        manager = User.objects.filter(role='CTO').first()
    elif emp_user.role == 'PROJECT_MANAGER':
        manager = _current_gm_for(emp_user)
    else:
        manager = _current_pm_for(emp_user) or (dept.manager if (dept and dept.manager_id) else emp_user.manager)

    if not manager:
        messages.error(request, f"{emp_user} has no manager assigned — cannot request a report.")
        return redirect('increments:list')

    try:
        current_basic = emp_user.salary_structure.basic
    except SalaryStructure.DoesNotExist:
        current_basic = 0

    IncrementRequest.objects.create(
        user=emp_user,
        current_basic=current_basic,
        requested_by=request.user,
        feedback_manager=manager,
        status='PENDING',
        report_requested_at=dj_timezone.now(),
    )
    messages.success(request, f"Performance report requested from {manager} for {emp_user}.")
    return redirect('increments:list')


# ---------- Step 2: Manager writes and submits the report ----------
@login_required
def manager_report_inbox(request):
    if not (request.user.is_manager() or request.user.is_cto()):
        messages.error(request, "Only Managers can view this page.")
        return redirect('core:dashboard')

    pending = IncrementRequest.objects.filter(
        status='PENDING_REPORT', feedback_manager=request.user
    ).select_related('user').order_by('-report_requested_at')
    return render(request, 'increments/manager_report_inbox.html', {'requests': pending})


@login_required
def submit_performance_report(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id, status='PENDING_REPORT')
    if not (request.user.is_manager() or request.user.is_cto()) or increment.feedback_manager_id != request.user.id:
        messages.error(request, "You cannot submit a report for this employee.")
        return redirect('increments:manager_report_inbox')

    if request.method == 'POST':
        report_text = request.POST.get('performance_report', '').strip()
        if not report_text:
            messages.error(request, "Write the performance report before submitting.")
            return redirect('increments:submit_performance_report', increment_id=increment.id)
        increment.performance_report = report_text
        increment.report_submitted_at = dj_timezone.now()
        increment.status = 'REPORT_SUBMITTED'
        increment.save()
        messages.success(request, f"Report submitted for {increment.user}. HR will review it next.")
        return redirect('increments:manager_report_inbox')

    return render(request, 'increments/submit_performance_report.html', {'increment': increment})


# ---------- Step 3: HR reviews and forwards to CEO ----------
@hr_only_required
def hr_review_reports(request):
    pending = IncrementRequest.objects.filter(
        status='REPORT_SUBMITTED'
    ).select_related('user', 'feedback_manager').prefetch_related('feedback').order_by('-report_submitted_at')
    return render(request, 'increments/hr_review_reports.html', {'requests': pending})


@hr_only_required
def hr_forward_to_coo(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id, status='REPORT_SUBMITTED')

    if request.method == 'POST':
        increment.hr_notes = request.POST.get('hr_notes', '').strip()
        increment.forwarded_by = request.user
        increment.forwarded_at = dj_timezone.now()
        increment.status = 'PENDING_COO'
        increment.save()
        messages.success(request, f"Forwarded {increment.user}'s report to the COO for a decision.")

    return redirect('increments:hr_review_reports')


# ---------- Step 4: COO analyzes and decides the percentage ----------
# (lives in core/coo_views.py — see below)


# ---------- Step 5: HR applies the COO's percentage to the salary ----------
@hr_only_required
def hr_pending_salary_updates(request):
    active_branch = getattr(request, 'active_branch', None)
    pending = IncrementRequest.objects.filter(
        status='COO_DECIDED'
    ).select_related('user', 'coo_decided_by').order_by('-coo_decided_at')
    if active_branch:
        pending = pending.filter(user__branch=active_branch)
    return render(request, 'increments/hr_pending_salary_updates.html', {'requests': pending})


@hr_only_required
def hr_apply_increment(request, increment_id):
    active_branch = getattr(request, 'active_branch', None)
    lookup = dict(id=increment_id, status='COO_DECIDED')
    if active_branch:
        lookup['user__branch'] = active_branch
    increment = get_object_or_404(IncrementRequest, **lookup)

    if request.method == 'POST':
        effective_date_raw = request.POST.get('effective_date', '')
        percent = float(increment.coo_percent or 0)

        increment.requested_basic = round(float(increment.current_basic) * (1 + percent / 100), 2)
        increment.effective_date = effective_date_raw or dj_timezone.localdate()
        increment.applied_by = request.user
        increment.applied_at = dj_timezone.now()
        increment.status = 'APPLIED'
        increment.save()

        structure, _ = SalaryStructure.objects.get_or_create(user=increment.user, defaults={'basic': 0})
        structure.basic = increment.requested_basic
        structure.save()

        IncrementCycleSkip.objects.filter(user=increment.user).delete()
        messages.success(request, f"{increment.user}'s salary updated to Rs. {increment.requested_basic} ({percent}% increment).")

    return redirect('increments:hr_pending_salary_updates')

@hr_only_required
def hr_feedback_inbox(request):
    """Every increment request where the assigned manager has submitted
    feedback — HR reviews it here and decides."""
    requests = IncrementRequest.objects.filter(
        status='PENDING', feedback__isnull=False
    ).select_related('user', 'feedback_manager', 'feedback').order_by('-feedback__submitted_at')
    return render(request, 'increments/hr_feedback_inbox.html', {'requests': requests})

@hr_only_required
def hr_reports_inbox(request):
    """Reports tab: every request where the manager has submitted a report
    (any status from REPORT_SUBMITTED onwards), so the data always stays
    visible on this page even after forwarding to the COO."""
    active_branch = getattr(request, 'active_branch', None)
    requests = IncrementRequest.objects.filter(
        status__in=['REPORT_SUBMITTED', 'PENDING_COO', 'COO_DECIDED', 'APPLIED', 'REJECTED'],
        report_submitted_at__isnull=False,
    ).select_related('user', 'feedback_manager').prefetch_related('feedback').order_by('-report_submitted_at')
    if active_branch:
        requests = requests.filter(user__branch=active_branch)
    return render(request, 'increments/hr_reports_inbox.html', {'requests': requests})



@hr_only_required
def hr_coo_approval(request):
    active_branch = getattr(request, 'active_branch', None)

    eligible_requests = IncrementRequest.objects.exclude(
        status__in=['PENDING_COO', 'COO_DECIDED', 'APPLIED', 'REJECTED']
    ).select_related('user', 'feedback_manager', 'feedback').order_by('-created_at')

    sent = IncrementRequest.objects.filter(
        forwarded_by__isnull=False
    ).select_related('user', 'feedback', 'coo_decided_by').order_by('-forwarded_at')

    reports = CooReport.objects.select_related('employee', 'uploaded_by').order_by('-uploaded_at')

    if active_branch:
        eligible_requests = eligible_requests.filter(user__branch=active_branch)
        sent = sent.filter(user__branch=active_branch)
        reports = reports.filter(
            Q(employee__branch=active_branch) | Q(employee__isnull=True)
        )

    if request.method == 'POST' and 'report_file' in request.FILES:
        uploaded_file = request.FILES['report_file']
        if not _report_file_extension_ok(uploaded_file):
            messages.error(request, "Only DOC, DOCX, XLS, XLSX, or PDF files are accepted for reports.")
            return redirect('increments:hr_coo_approval')
        employee_id = request.POST.get('employee_id') or None
        CooReport.objects.create(
            employee_id=employee_id,
            report_file=uploaded_file,
            notes=request.POST.get('notes', '').strip(),
            uploaded_by=request.user,
        )
        messages.success(request, "Report sent to the COO for review.")
        return redirect('increments:hr_coo_approval')

    return render(request, 'increments/hr_coo_approval.html', {
        'eligible_requests': eligible_requests,
        'sent': sent,
        'reports': reports,
    })

@hr_only_required
def hr_send_to_coo(request, increment_id):
    increment = get_object_or_404(IncrementRequest, id=increment_id)

    if increment.status in ('PENDING_COO', 'COO_DECIDED', 'APPLIED', 'REJECTED'):
        messages.error(request, "This request has already been sent to the COO or finalized.")
        return redirect('increments:hr_coo_approval')

    if request.method == 'POST':
        feedback, _ = IncrementFeedback.objects.get_or_create(
            increment_request=increment,
            defaults={'manager': increment.feedback_manager}
        )

        uploaded_file = request.FILES.get('report_file')
        if uploaded_file:
            if not _report_file_extension_ok(uploaded_file):
                messages.error(request, "Only DOC, DOCX, XLS, XLSX, or PDF files are accepted for reports.")
                return redirect('increments:hr_coo_approval')
            feedback.report_file = uploaded_file
            feedback.save()

        increment.hr_notes = request.POST.get('hr_notes', '').strip()
        increment.forwarded_by = request.user
        increment.forwarded_at = dj_timezone.now()
        increment.status = 'PENDING_COO'
        increment.save()

        if uploaded_file:
            messages.success(request, f"Sent {increment.user}'s report (with file) to the COO for approval.")
        else:
            messages.success(request, f"Sent {increment.user}'s request to the COO (no file attached).")

    return redirect('increments:hr_coo_approval')

from increments.models import Appraisal
from increments.services.appraisal_calculator import calculate_appraisal_data

@login_required
@hr_or_admin_required
def appraisal_calculator(request):
    if request.method == 'POST':
        emp_id = request.POST.get('employee')
        month_year = request.POST.get('start_month') # Format YYYY-MM
        
        if emp_id and month_year:
            y, m = month_year.split('-')
            
            # Create or update DRAFT appraisal
            # Calculate data
            emp = get_object_or_404(User, id=emp_id)
            calc_data = calculate_appraisal_data(emp, int(y), int(m))
            
            # end date is 11 months from start
            from dateutil.relativedelta import relativedelta
            from datetime import date
            sd = date(int(y), int(m), 1)
            ed = sd + relativedelta(months=11)
            
            appraisal = Appraisal.objects.create(
                employee=emp,
                start_date=sd,
                end_date=ed,
                status='DRAFT',
                calculated_data=calc_data,
                created_by=request.user
            )
            return redirect('increments:appraisal_preview', appraisal_id=appraisal.id)
            
    employees = User.objects.filter(role='EMPLOYEE', profile__status='ACTIVE')
    return render(request, 'increments/appraisal_calculator.html', {'employees': employees})

@login_required
@hr_or_admin_required
def appraisal_preview(request, appraisal_id):
    appraisal = get_object_or_404(Appraisal, id=appraisal_id)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_manual':
            appraisal.quality_score = request.POST.get('quality_score')
            appraisal.attrition_score = request.POST.get('attrition_score')
            appraisal.leadership_score = request.POST.get('leadership_score')
            appraisal.process_score = request.POST.get('process_score')
            appraisal.save()
            messages.success(request, "Manual KPIs saved.")
            return redirect('increments:appraisal_preview', appraisal_id=appraisal.id)
            
    # Calculate some totals for display
    data = appraisal.calculated_data
    proj_actual_total = sum(data['total_project']['actual'])
    proj_target_total = sum(data['total_project']['target'])
    proj_score = (proj_actual_total / proj_target_total) * 100 if proj_target_total > 0 else 0
    
    eff_actual = sum(data['efficiency']['actual']) / 12
    eff_target = sum(data['efficiency']['target']) / 12
    eff_score = (eff_actual / eff_target) * 100 if eff_target > 0 else 0
    
    abs_actual = sum(data['absenteeism']['actual']) / 12
    abs_score = abs_actual * 100
    
    context = {
        'appraisal': appraisal,
        'data': data,
        'proj_score': proj_score,
        'eff_score': eff_score,
        'abs_score': abs_score
    }
    return render(request, 'increments/appraisal_preview.html', context)
import openpyxl
from django.http import HttpResponse
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

@login_required
@hr_or_admin_required
def appraisal_export_excel(request, appraisal_id):
    appraisal = get_object_or_404(Appraisal, id=appraisal_id)
    data = appraisal.calculated_data
    emp = appraisal.employee

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Appraisal"

    N_COLS = 15  # Label + 12 months + Total + Max Score

    # ---------- Style palette ----------
    NAVY = '1F3864'
    LIGHT_BLUE = 'D9E2F3'
    SECTION_FILL = 'BDD7EE'
    SUBTOTAL_FILL = 'DDEBF7'
    SCORE_FILL = 'FFE699'
    WHITE_FONT = Font(color='FFFFFF', bold=True, size=11, name='Calibri')
    TITLE_FONT = Font(bold=True, size=16, color=NAVY, name='Calibri')
    SUBTITLE_FONT = Font(bold=False, size=10, color='595959', name='Calibri', italic=True)
    HEADER_FONT = Font(bold=True, size=10, color=NAVY, name='Calibri')
    SECTION_FONT = Font(bold=True, size=10, color=NAVY, name='Calibri')
    LABEL_FONT = Font(size=10, name='Calibri')
    SCORE_FONT = Font(bold=True, size=10, color='7F6000', name='Calibri')
    thin = Side(style='thin', color='B7B7B7')
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
    CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
    LEFT = Alignment(horizontal='left', vertical='center')

    def style_row(row_num, fill=None, font=None, number_format=None, start_col=1, end_col=N_COLS):
        for c in range(start_col, end_col + 1):
            cell = ws.cell(row=row_num, column=c)
            cell.border = BORDER
            cell.alignment = CENTER if c > 1 else LEFT
            if fill:
                cell.fill = PatternFill(start_color=fill, end_color=fill, fill_type='solid')
            if font:
                cell.font = font
            # Apply the number format to the monthly + total columns only (2-14).
            # Column 15 (Max Score) always holds a plain numeric constant.
            if number_format and 2 <= c <= 14:
                cell.number_format = number_format
            elif c == 15:
                cell.number_format = '0.00'

    # ---------- Title block ----------
    ws.merge_cells('A1:O1')
    ws['A1'] = 'ANNUAL PERFORMANCE APPRAISAL'
    ws['A1'].font = TITLE_FONT
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 26

    ws.merge_cells('A2:O2')
    ws['A2'] = f"Appraisal Period: {appraisal.start_date.strftime('%b %Y')} - {appraisal.end_date.strftime('%b %Y')}"
    ws['A2'].font = SUBTITLE_FONT
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 16

    ws.append([])  # blank spacer row 3

    # ---------- Employee info ----------
    r = 4
    ws.cell(row=r, column=1, value='EMP ID')
    ws.cell(row=r, column=2, value='NAME')
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=13)
    ws.cell(row=r, column=14, value='Max Score')
    style_row(r, fill=LIGHT_BLUE, font=HEADER_FONT)

    r += 1
    emp_row = r
    ws.cell(row=r, column=1, value=emp.employee_id)
    ws.cell(row=r, column=2, value=emp.get_full_name())
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=13)
    style_row(r, font=LABEL_FONT)

    r += 1
    month_header_row = r
    ws.cell(row=r, column=1, value='')
    for i, m in enumerate(data['months_labels']):
        ws.cell(row=r, column=2 + i, value=m)
    ws.cell(row=r, column=14, value='Total')
    style_row(r, fill=NAVY, font=WHITE_FONT)

    def add_section_header(title):
        nonlocal r
        r += 1
        ws.cell(row=r, column=1, value=title)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=N_COLS)
        style_row(r, fill=SECTION_FILL, font=SECTION_FONT)

    def add_data_row(label, values, total=None, max_score=None, fmt=None):
        nonlocal r
        r += 1
        ws.cell(row=r, column=1, value=label)
        for i, v in enumerate(values):
            ws.cell(row=r, column=2 + i, value=v)
        if total is not None:
            ws.cell(row=r, column=14, value=total)
        if max_score is not None:
            ws.cell(row=r, column=15, value=max_score)
        style_row(r, font=LABEL_FONT, number_format=fmt)

    def add_score_row(label, values, total, max_score, fmt='0.0%'):
        nonlocal r
        r += 1
        ws.cell(row=r, column=1, value=label)
        for i, v in enumerate(values):
            ws.cell(row=r, column=2 + i, value=v)
        ws.cell(row=r, column=14, value=total)
        ws.cell(row=r, column=15, value=max_score)
        style_row(r, fill=SCORE_FILL, font=SCORE_FONT, number_format=fmt)

    # ---------- Projects ----------
    for p_name, p_data in data['projects'].items():
        add_section_header(p_name)
        add_data_row('Target', p_data['target'], total=sum(p_data['target']))
        add_data_row('Actual', p_data['actual'], total=sum(p_data['actual']))

    # Total Project
    add_section_header('Total Project')
    tot_t = data['total_project']['target']
    tot_a = data['total_project']['actual']
    r += 1
    ws.cell(row=r, column=1, value='Target')
    for i, v in enumerate(tot_t):
        ws.cell(row=r, column=2 + i, value=v)
    ws.cell(row=r, column=14, value=sum(tot_t))
    style_row(r, fill=SUBTOTAL_FILL, font=Font(bold=True, size=10))

    r += 1
    ws.cell(row=r, column=1, value='Actual')
    for i, v in enumerate(tot_a):
        ws.cell(row=r, column=2 + i, value=v)
    ws.cell(row=r, column=14, value=sum(tot_a))
    style_row(r, fill=SUBTOTAL_FILL, font=Font(bold=True, size=10))

    # Project score row
    p_scores = [(tot_a[i] / tot_t[i]) if tot_t[i] > 0 else 0 for i in range(12)]
    tot_sum_a, tot_sum_t = sum(tot_a), sum(tot_t)
    p_overall = (tot_sum_a / tot_sum_t) if tot_sum_t > 0 else 0
    add_score_row('Project Score (%)', p_scores, p_overall, 50)

    # ---------- Quality (manual) ----------
    for label in ['Quality 1', 'Quality 2', 'Quality 3', 'Total Quality']:
        add_section_header(label)
        add_data_row('Target', [''] * 12)
        add_data_row('Actual', [''] * 12)

    q_score = appraisal.quality_score or ''
    add_score_row('Quality Score', [''] * 12, q_score, 50, fmt='0.00')

    # ---------- Efficiency ----------
    add_section_header('Efficiency / Productivity')
    eff_t = data['efficiency']['target']
    eff_a = data['efficiency']['actual']
    add_data_row('Target', eff_t, total=sum(eff_t))
    add_data_row('Actual', eff_a, total=sum(eff_a))

    e_scores = [(eff_a[i] / eff_t[i]) if eff_t[i] > 0 else 0 for i in range(12)]
    e_overall = (sum(eff_a) / sum(eff_t)) if sum(eff_t) > 0 else 0
    add_score_row('Efficiency Score (%)', e_scores, e_overall, 25)

    # ---------- Attrition ----------
    add_section_header('Attrition Management')
    add_data_row('Target', [''] * 12)
    add_data_row('Actual', [''] * 12)

    # ---------- Absenteeism ----------
    add_section_header('Absenteeism')
    abs_t = data['absenteeism']['target']
    abs_a = data['absenteeism']['actual']
    add_data_row('Target', abs_t, total=sum(abs_t))
    add_data_row('Actual', abs_a, total=sum(abs_a))

    add_score_row('Attrition Score', [''] * 12, appraisal.attrition_score or '', 20, fmt='0.00')

    # ---------- Leadership ----------
    add_section_header('Leadership & Team Development')
    add_data_row('Target', [''] * 12)
    add_data_row('Actual', [''] * 12)

    # ---------- Process Improvement ----------
    add_section_header('Process Improvement / Innovation')
    add_data_row('Target', [''] * 12)
    add_data_row('Actual', [''] * 12)

    add_score_row('Process Score', [''] * 12, appraisal.process_score or '', 5, fmt='0.00')

    # ---------- Overall Total ----------
    r += 1
    ws.cell(row=r, column=1, value='TOTAL SCORE')
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=13)
    total_max = 50 + 50 + 25 + 20 + 5

    def _to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    # Achieved total = each section's earned points, not its ratio/label:
    # Project and Efficiency are stored as ratios (e.g. 1.04 = 104%) so
    # convert them to points against their own max; Quality/Attrition/
    # Process are entered manually as absolute points already.
    achieved_total = (
        (p_overall * 50) +
        _to_float(appraisal.quality_score) +
        (e_overall * 25) +
        _to_float(appraisal.attrition_score) +
        _to_float(appraisal.process_score)
    )
    ws.cell(row=r, column=14, value=round(achieved_total, 2))
    ws.cell(row=r, column=14).number_format = '0.00'
    ws.cell(row=r, column=15, value=total_max)
    style_row(r, fill=NAVY, font=WHITE_FONT)

    last_row = r

    # ---------- Column widths (auto-fit) ----------
    ws.column_dimensions['A'].width = 32
    for col_idx in range(2, 14):
        ws.column_dimensions[get_column_letter(col_idx)].width = 10
    ws.column_dimensions['N'].width = 12  # Total
    ws.column_dimensions['O'].width = 12  # Max Score

    # Auto-fit safeguard based on actual content length (caps applied)
    for col_idx in range(1, N_COLS + 1):
        col_letter = get_column_letter(col_idx)
        max_len = 0
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, min_row=1, max_row=last_row):
            for cell in row:
                if cell.value is not None:
                    max_len = max(max_len, len(str(cell.value)))
        if col_idx == 1:
            ws.column_dimensions[col_letter].width = max(24, min(max_len + 4, 36))
        else:
            ws.column_dimensions[col_letter].width = max(9, min(max_len + 3, 16))

    # ---------- Sheet-wide polish ----------
    ws.freeze_panes = get_column_letter(2) + str(month_header_row + 1)
    ws.sheet_view.showGridLines = False
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Appraisal_{emp.employee_id}_{appraisal.start_date.strftime("%Y")}.xlsx"'
    wb.save(response)
    return response
