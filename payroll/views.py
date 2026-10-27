import calendar
from datetime import date

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.http import FileResponse, JsonResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.decorators import hr_or_admin_required, hr_only_required
from core.models import User, Department
from payroll.models import PayrollRun, Payslip, SalaryStructure, AllowanceCycle, Allowance, AllowanceRequest
from payroll.forms import SalaryStructureForm, PayslipDeductionsForm, AllowanceForm, AllowanceRequestForm
from payroll.utils import compute_payslip_for_user, generate_payslip_pdf, generate_allowance_excel



def _cycle_bounds(year, month):
    """Pay-cycle bounds for the '25th to 25th' salary calendar: a cycle
    labelled by (year, month) runs from the 26th of the PREVIOUS month
    through the 25th of THIS month. E.g. cycle "2026-02" (February) runs
    26-Jan-2026 to 25-Feb-2026. Handles the January/previous-year rollover."""
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    start = date(prev_year, prev_month, 26)
    end = date(year, month, 25)
    return start, end


# --- Branch-scoping helpers for payroll actions on a specific employee ---
def _can_manage_payroll_for(request, emp_user):
    """Whether the acting user (request.user) is allowed to view/edit
    payroll data (salary structure, deductions, credit salary) for
    `emp_user`, given the currently active branch.

    Mirrors the exact same rule payroll_runs()/run_payroll_for_all() already
    use to build the employee list: if there's no active_branch (Admin /
    Senior HR / COO viewing "All Branches"), everyone is in scope. If an
    active_branch IS set — either because the acting HR is locked to one
    branch, or because they've switched to view one specific branch —
    only that branch's employees are in scope, EXCEPT COO/Senior HR
    themselves, who are branch-wide roles and stay manageable regardless
    (same carve-out payroll_runs() applies when building the list, so an
    employee editable here is always one that would actually show up in
    that HR user's own payroll list).
    """
    active_branch = getattr(request, 'active_branch', None)
    if not active_branch:
        return True
    if emp_user.role in ('COO', 'SENIOR_HR'):
        return True
    return emp_user.branch_id == active_branch.id


def _deny_cross_branch(request, emp_user, is_ajax=False):
    """Standard "not in your branch" response — JSON for AJAX callers,
    a redirect+message otherwise. Returns the response to give back."""
    msg = f"{emp_user} is not in a branch you have access to."
    if is_ajax:
        return JsonResponse({'success': False, 'reason': 'wrong_branch', 'error': msg}, status=403)
    messages.error(request, msg)
    return redirect('payroll:runs')


def _month_bounds(year, month):
    # Kept as an alias so any other code/templates referencing the old
    # calendar-month helper keep working; payroll itself now uses _cycle_bounds.
    return _cycle_bounds(year, month)


def _recent_months(today, count=6):
    """Recent pay cycles for the dropdown, labelled '26 Dec - 25 Jan 2026'
    style so HR can see the actual credit window, not just a calendar month."""
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        start, end = _cycle_bounds(year, month)
        label = f"{start.strftime('%d %b')} - {end.strftime('%d %b %Y')}"
        months.append((f"{year:04d}-{month:02d}", label))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return months


@login_required
def my_payslips(request):
    if request.user.role in ('ADMIN', 'HR', 'SENIOR_HR'):
        payslips_qs = Payslip.objects.filter(user=request.user).select_related('payroll_run')
    else:
        payslips_qs = Payslip.objects.filter(user=request.user, is_released=True).select_related('payroll_run')

    years = sorted({
        d.year for d in payslips_qs.values_list('payroll_run__start_date', flat=True)
    }, reverse=True)

    selected_year = request.GET.get('year')
    if selected_year and selected_year.isdigit() and int(selected_year) in years:
        selected_year = int(selected_year)
        payslips_qs = payslips_qs.filter(payroll_run__start_date__year=selected_year)
    else:
        selected_year = None

    payslips = payslips_qs.order_by('-payroll_run__start_date')

    return render(request, 'payroll/my_payslips.html', {
        'payslips': payslips, 'years': years, 'selected_year': selected_year,
    })


@login_required
def download_payslip(request, payslip_id):
    payslip = get_object_or_404(Payslip.objects.select_related('user'), id=payslip_id)
    is_owner = payslip.user == request.user
    is_hr_or_admin = request.user.is_hr_or_admin()
    # HR, Senior HR and COO may view/download ANY employee's payslip
    # (subject to branch scoping below). Admin is deliberately excluded
    # here — Admin is view-only elsewhere in payroll, and salary/payslip
    # data must stay private to HR leadership and the employee themselves,
    # even though Admin can still see the Payroll Runs status table.
    can_view_others = request.user.role in ('HR', 'SENIOR_HR', 'COO')

    if not (is_owner or can_view_others):
        messages.error(request, "You cannot access this payslip.")
        return redirect('core:dashboard')

    if can_view_others and not is_owner and not _can_manage_payroll_for(request, payslip.user):
        messages.error(request, "This payslip belongs to a branch you don't have access to.")
        return redirect('payroll:runs')

    if is_owner and not is_hr_or_admin and not payslip.is_released:
        messages.error(request, "This payslip hasn't been released yet.")
        return redirect('payroll:my_payslips')
    
    # Always regenerate so the PDF reflects the current layout/data —
    # a stale file that merely exists on disk is not "up to date".
    generate_payslip_pdf(payslip)
    payslip.refresh_from_db()

    return FileResponse(payslip.pdf_file.open('rb'), as_attachment=True, filename=payslip.pdf_file.name)
   

@hr_or_admin_required
def payroll_runs(request):
    role_labels = {
        'ADMIN': 'Admin', 'MANAGER': 'Manager', 'EMPLOYEE': 'Employee', 'HR': 'HR',
        'SENIOR_HR': 'GM (HR)', 'PROJECT_MANAGER': 'Project Manager',
        'GENERAL_MANAGER': 'General Manager', 'CTO': 'CTO', 'COO': 'COO',
    }

    active_branch = getattr(request, 'active_branch', None)

    all_users = User.objects.select_related('department', 'profile').exclude(
        profile__status__in=['ONBOARDING', 'EXITED']   # only Active employees
    )
    if active_branch:
        # COO and Senior HR are branch-wide roles (see
        # core.permissions.can_view_all_branches) and may not belong to any
        # single branch — always include them regardless of which branch is
        # currently being viewed, or their payroll can never be set up /
        # credited.
        all_users = all_users.filter(Q(branch=active_branch) | Q(role__in=['COO', 'SENIOR_HR']))

    months = _recent_months(timezone.localdate())

    released_payslips = Payslip.objects.filter(
        is_released=True, user__in=all_users
    ).select_related('payroll_run')
    credited_map = {}
    for p in released_payslips:
        # Key by the cycle's END-date year-month, matching the label used
        # in the dropdown (_recent_months/_cycle_bounds label a cycle by
        # its end month, e.g. "26 Jul - 25 Aug 2026" -> "2026-08"), not the
        # start date — otherwise a correctly credited/released payslip
        # never matches the selected month and always shows "Not Credited".
        key = p.payroll_run.end_date.strftime('%Y-%m')
        credited_map.setdefault(p.user_id, set()).add(key)

    structure_user_ids = set(
        SalaryStructure.objects.filter(user__in=all_users).values_list('user_id', flat=True)
    )

    employees = [
        {
            'user': emp,
            'role': emp.role,
            'role_label': role_labels.get(emp.role, emp.role),
            'credited_months': ','.join(sorted(credited_map.get(emp.id, set()))),
            'has_salary_structure': emp.id in structure_user_ids,
        }
        for emp in all_users.order_by('role', 'first_name', 'username')
    ]

    return render(request, 'payroll/runs.html', {'employees': employees, 'months': months})

@hr_or_admin_required
def employee_payroll_history(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    # Admin can see the Payroll Runs status table, but must not be able to
    # open an individual employee's payslip history / PDFs — that stays
    # restricted to HR, Senior HR and COO. (Admin viewing their own row is
    # still fine.)
    if request.user.role == 'ADMIN' and emp_user.id != request.user.id:
        messages.error(request, "Admin does not have access to view individual payslips.")
        return redirect('payroll:runs')
    if not _can_manage_payroll_for(request, emp_user):
        return _deny_cross_branch(request, emp_user)
    payslips = Payslip.objects.filter(user=emp_user).select_related('payroll_run').order_by('-payroll_run__start_date')
    has_salary_structure = SalaryStructure.objects.filter(user=emp_user).exists()
    months = _recent_months(timezone.localdate())

    credited_months = ','.join(sorted({
        p.payroll_run.start_date.strftime('%Y-%m') for p in payslips if p.is_released
    }))

    return render(request, 'payroll/employee_payroll_history.html', {
        'emp_user': emp_user, 'payslips': payslips,
        'has_salary_structure': has_salary_structure, 'months': months,
        'credited_months': credited_months,
    })


@hr_only_required
def credit_salary(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not _can_manage_payroll_for(request, emp_user):
        return _deny_cross_branch(request, emp_user, is_ajax)

    if request.method == 'POST':
        month_value = request.POST.get('month', '')
        try:
            year, month = (int(part) for part in month_value.split('-'))
            start_date, end_date = _cycle_bounds(year, month)
        except (ValueError, AttributeError):
            msg = "Please select a valid month."
            if is_ajax:
                return JsonResponse({'success': False, 'reason': 'invalid_month', 'error': msg}, status=400)
            messages.error(request, msg)
            return redirect('payroll:employee_payroll_history', user_id=emp_user.id)

        if not SalaryStructure.objects.filter(user=emp_user).exists():
            msg = f"{emp_user} has no salary structure set up. Set one up first."
            if is_ajax:
                return JsonResponse({
                    'success': False, 'reason': 'no_structure', 'error': msg,
                    'user_id': emp_user.id, 'user_name': str(emp_user),
                }, status=400)
            messages.error(request, msg)
            return redirect('payroll:employee_payroll_history', user_id=emp_user.id)

        run, _ = PayrollRun.objects.get_or_create(
            start_date=start_date, end_date=end_date, defaults={'processed_by': request.user},
        )
        payslip = compute_payslip_for_user(emp_user, run)
        if not payslip:
            msg = f"Could not generate a payslip for {emp_user}."
            if is_ajax:
                return JsonResponse({'success': False, 'reason': 'generation_failed', 'error': msg}, status=400)
            messages.error(request, msg)
            return redirect('payroll:employee_payroll_history', user_id=emp_user.id)

        payslip.is_released = True
        payslip.released_by = request.user
        payslip.released_at = timezone.now()
        payslip.save()
        generate_payslip_pdf(payslip)

        msg = f"Salary credited to {emp_user} for {start_date.strftime('%B %Y')}."
        if is_ajax:
            return JsonResponse({
                'success': True, 'message': msg,
                'user_id': emp_user.id, 'month': month_value,
            })
        messages.success(request, msg)

    if is_ajax:
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)
    return redirect('payroll:employee_payroll_history', user_id=emp_user.id)


@hr_only_required
def run_payroll_for_all(request):
    """Runs payroll for every eligible, active employee for one pay cycle
    in a single request: creates/reuses the PayrollRun, computes each
    person's Payslip, releases it and generates the PDF. Employees with no
    SalaryStructure or who are already credited for that cycle are skipped
    and reported back, not treated as failures."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    month_value = request.POST.get('month', '')
    try:
        year, month = (int(part) for part in month_value.split('-'))
        start_date, end_date = _cycle_bounds(year, month)
    except (ValueError, AttributeError):
        return JsonResponse({'success': False, 'error': 'Please select a valid pay cycle.'}, status=400)

    active_branch = getattr(request, 'active_branch', None)
    all_users = User.objects.select_related('department', 'profile').exclude(
        profile__status__in=['ONBOARDING', 'EXITED']
    )
    if active_branch:
        all_users = all_users.filter(Q(branch=active_branch) | Q(role__in=['COO', 'SENIOR_HR']))

    already_credited_ids = set(
        Payslip.objects.filter(
            is_released=True, user__in=all_users,
            payroll_run__start_date=start_date, payroll_run__end_date=end_date,
        ).values_list('user_id', flat=True)
    )
    structure_user_ids = set(
        SalaryStructure.objects.filter(user__in=all_users).values_list('user_id', flat=True)
    )

    run, _ = PayrollRun.objects.get_or_create(
        start_date=start_date, end_date=end_date, defaults={'processed_by': request.user},
    )

    credited, skipped_no_structure, skipped_already = [], [], []
    for emp_user in all_users.exclude(id__in=already_credited_ids).order_by('role', 'first_name', 'username'):
        if emp_user.id not in structure_user_ids:
            skipped_no_structure.append(str(emp_user))
            continue
        payslip = compute_payslip_for_user(emp_user, run)
        if not payslip:
            skipped_no_structure.append(str(emp_user))
            continue
        payslip.is_released = True
        payslip.released_by = request.user
        payslip.released_at = timezone.now()
        payslip.save()
        generate_payslip_pdf(payslip)
        credited.append(emp_user.id)

    return JsonResponse({
        'success': True,
        'month': month_value,
        'credited_ids': credited,
        'credited_count': len(credited),
        'skipped_no_structure': skipped_no_structure,
        'already_credited_count': len(already_credited_ids),
        'message': (
            f"Payroll run complete for {start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}: "
            f"{len(credited)} payslip(s) generated and released."
            + (f" {len(skipped_no_structure)} skipped (no salary structure)." if skipped_no_structure else "")
        ),
    })


@hr_or_admin_required
def salary_structures(request):
    # The all-employees salary list is retired: salary structures are now
    # opened per employee from Payroll Runs (Salary Structure button).
    # To bring the old list back, restore the three lines that used to be
    # here (queryset + render of payroll/salary_structures.html).
    return redirect('payroll:runs')


@hr_only_required
def edit_salary_structure(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    if not _can_manage_payroll_for(request, emp_user):
        return _deny_cross_branch(request, emp_user)
    structure, _ = SalaryStructure.objects.get_or_create(user=emp_user, defaults={'basic': 0})

    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ''

    if request.method == 'POST':
        form = SalaryStructureForm(request.POST, instance=structure)
        if form.is_valid():
            form.save()
            messages.success(request, 'Salary structure saved.')
            return redirect(next_url or reverse('payroll:runs'))
    else:
        form = SalaryStructureForm(instance=structure)

    return render(request, 'payroll/edit_salary_structure.html', {
        'emp_user': emp_user, 'structure': structure, 'form': form, 'next_url': next_url,
    })

@hr_only_required
def edit_payslip_deductions(request, payslip_id):
    """HR fills in TDS / P.Tax-LWF / bonus-deduction for a specific payslip,
    then the PDF is regenerated so the download always reflects the latest
    figures."""
    payslip = get_object_or_404(Payslip.objects.select_related('user'), id=payslip_id)
    if not _can_manage_payroll_for(request, payslip.user):
        return _deny_cross_branch(request, payslip.user)

    if request.method == 'POST':
        form = PayslipDeductionsForm(request.POST, instance=payslip)
        if form.is_valid():
            payslip = form.save(commit=False)
            total_deduction = (
                payslip.pf_deduction + payslip.tds + payslip.p_tax_lwf
                + payslip.bonus_deduction + payslip.other_deductions
            )
            payslip.net_pay = payslip.gross_pay + payslip.bonus - total_deduction
            payslip.save()
            generate_payslip_pdf(payslip)
            messages.success(request, 'Payslip deductions updated.')
            return redirect('payroll:employee_payroll_history', user_id=payslip.user_id)
    else:
        form = PayslipDeductionsForm(instance=payslip)

    return render(request, 'payroll/edit_payslip_deductions.html', {
        'payslip': payslip, 'form': form,
    })


@hr_or_admin_required
def allowance_list(request):
    """HR picks a month/year cycle and sees only employees who already
    have an Allowance row for that cycle — i.e. either an HR-approved
    AllowanceRequest was applied, or HR added one manually. Employees
    with nothing yet don't clutter the list; HR can still add a fresh
    entry for anyone via the picker below the table."""
    active_branch = getattr(request, 'active_branch', None)
    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    cycle, _ = AllowanceCycle.objects.get_or_create(year=year, month=month)

    employees = User.objects.filter(role__in=['EMPLOYEE', 'MANAGER', 'PROJECT_MANAGER'])
    if active_branch:
        employees = employees.filter(branch=active_branch)
    employees = employees.select_related('department')

    allowances = Allowance.objects.filter(cycle=cycle, user__in=employees).select_related(
        'user', 'user__department'
    ).order_by('user__first_name')

    rows = [{'user': a.user, 'allowance': a} for a in allowances]

    # Employees who don't have a row yet for this cycle — offered as a
    # simple picker so HR can still add one manually.
    covered_ids = {a.user_id for a in allowances}
    # Only count requests actually actionable on the approvals page right
    # now (PENDING_HR). Requests still sitting with a Project Manager
    # (PENDING_PM) aren't HR's to review yet, so they're excluded here —
    # otherwise this banner would count them but the approvals page
    # wouldn't show them (nothing pending, nothing in history), which
    # looks like a bug.
    pending_pm_hr_count = AllowanceRequest.objects.filter(
        user__in=employees, year=year, month=month,
        status=AllowanceRequest.STATUS_PENDING_HR,
    ).exclude(user=request.user).count()
    employees_without_row = employees.exclude(id__in=covered_ids).order_by('first_name')

    return render(request, 'payroll/allowance_list.html', {
        'cycle': cycle, 'rows': rows, 'year': year, 'month': month,
        'month_name': calendar.month_name[month],
        'years': range(today.year - 2, today.year + 2),
        'months': [(i, calendar.month_name[i]) for i in range(1, 13)],
        'employees_without_row': employees_without_row,
        'pending_pm_hr_count': pending_pm_hr_count,
    })


@hr_only_required
def edit_allowance(request, cycle_id, user_id):
    cycle = get_object_or_404(AllowanceCycle, id=cycle_id)
    emp = get_object_or_404(User, id=user_id)
    instance = Allowance.objects.filter(cycle=cycle, user=emp).first()

    if request.method == 'POST':
        form = AllowanceForm(request.POST, instance=instance)
        if form.is_valid():
            allowance = form.save(commit=False)
            allowance.cycle = cycle
            allowance.user = emp
            if not allowance.pk:
                allowance.created_by = request.user
            allowance.save()
            messages.success(request, f"Allowance saved for {emp}.")
            return redirect(f"{reverse('payroll:allowance_list')}?year={cycle.year}&month={cycle.month}")
    else:
        form = AllowanceForm(instance=instance)

    return render(request, 'payroll/edit_allowance.html', {
        'form': form, 'cycle': cycle, 'employee': emp,
    })


@hr_or_admin_required
def download_allowance_excel(request, cycle_id):
    cycle = get_object_or_404(AllowanceCycle, id=cycle_id)
    active_branch = getattr(request, 'active_branch', None)
    allowances = Allowance.objects.filter(cycle=cycle).select_related('user')
    if active_branch:
        allowances = allowances.filter(user__branch=active_branch)
    buffer = generate_allowance_excel(cycle, allowances)
    filename = f"Allowance_{cycle.month:02d}_{cycle.year}.xlsx"
    return FileResponse(buffer, as_attachment=True, filename=filename)


@login_required
def my_allowance_requests(request):
    """Every employee can request an allowance here — pick a type, amount,
    month, and attach proof (PDF/JPG/PNG). Production employees are routed
    to their Project Manager first; everyone else goes straight to HR."""
    today = timezone.localdate()

    if request.method == 'POST':
        form = AllowanceRequestForm(request.POST, request.FILES)
        if form.is_valid():
            req = form.save(commit=False)
            req.user = request.user
            req.status = req.initial_status()
            req.save()
            if req.status == AllowanceRequest.STATUS_PENDING_PM:
                messages.success(request, "Allowance request submitted — sent to your Project Manager for approval.")
            else:
                messages.success(request, "Allowance request submitted — sent to HR for approval.")
            return redirect('payroll:my_allowance_requests')
    else:
        form = AllowanceRequestForm(initial={'month': today.month, 'year': today.year})

    requests_qs = AllowanceRequest.objects.filter(user=request.user).select_related(
        'reviewed_by_pm', 'reviewed_by_hr'
    )

    return render(request, 'payroll/my_allowance_requests.html', {
        'form': form, 'requests': requests_qs,
        'years': range(today.year - 1, today.year + 2),
        'months': [(i, calendar.month_name[i]) for i in range(1, 13)],
    })


@login_required
def allowance_request_approvals(request):
    """Queue of allowance requests awaiting this user's decision: Project
    Managers see requests from employees on projects they manage;
    HR/Senior HR see requests that have cleared the PM stage (or never
    needed one)."""
    user = request.user
    context = {}

    if user.role in ('HR', 'SENIOR_HR'):
        active_branch = getattr(request, 'active_branch', None)
        pending_qs = AllowanceRequest.objects.filter(
            status=AllowanceRequest.STATUS_PENDING_HR
        ).exclude(user=user).select_related('user', 'user__department', 'reviewed_by_pm')
        if active_branch:
            pending_qs = pending_qs.filter(user__branch=active_branch)
        context['pending_requests'] = pending_qs

        history_qs = AllowanceRequest.objects.filter(
            status__in=[AllowanceRequest.STATUS_APPROVED, AllowanceRequest.STATUS_REJECTED]
        ).exclude(user=user).select_related('user', 'user__department', 'reviewed_by_pm', 'reviewed_by_hr')
        if active_branch:
            history_qs = history_qs.filter(user__branch=active_branch)
        context['history'] = history_qs[:200]

    elif user.role == 'GENERAL_MANAGER':
        # Per policy: GM does not manage or view allowance requests raised
        # by managers/PMs under them — that page belongs to HR and to the
        # PM/manager who actually manages the requester's project.
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    elif user.is_manager():
        from projects.models import ProjectAssignment
        team_ids = ProjectAssignment.objects.filter(
            project__manager=user, is_current=True
        ).values_list('user_id', flat=True)
        context['pending_requests'] = AllowanceRequest.objects.filter(
            user_id__in=team_ids, status=AllowanceRequest.STATUS_PENDING_PM
        ).select_related('user', 'user__department')
        context['history'] = AllowanceRequest.objects.filter(
            user_id__in=team_ids
        ).exclude(status=AllowanceRequest.STATUS_PENDING_PM).select_related(
            'user', 'user__department', 'reviewed_by_pm', 'reviewed_by_hr'
        )[:200]

    else:
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    return render(request, 'payroll/allowance_approvals.html', context)


@login_required
def review_allowance_request(request, request_id, decision):
    user = request.user
    req = get_object_or_404(AllowanceRequest, id=request_id)

    if user.role == 'ADMIN':
        messages.error(request, "Admin has view-only access and cannot approve or reject requests.")
        return redirect('payroll:allowance_request_approvals')

    if decision not in ('approve', 'reject'):
        messages.error(request, "Invalid decision.")
        return redirect('payroll:allowance_request_approvals')

    remarks = request.POST.get('remarks', '')

    # ---- Stage: Project Manager review (production employees only) ----
    if req.status == AllowanceRequest.STATUS_PENDING_PM:
        approving_pm = req.get_pm()
        is_overseeing_gm = user.role == 'GENERAL_MANAGER'
        if not is_overseeing_gm and not (user.is_manager() and approving_pm and approving_pm.id == user.id):
            messages.error(request, "You cannot review this request.")
            return redirect('payroll:allowance_request_approvals')

        req.reviewed_by_pm = user
        req.pm_reviewed_at = timezone.now()
        req.pm_remarks = remarks

        if decision == 'approve':
            req.status = AllowanceRequest.STATUS_PENDING_HR
            req.save()
            messages.success(request, f"Request approved and forwarded to HR for {req.user}.")
        else:
            req.status = AllowanceRequest.STATUS_REJECTED
            req.save()
            messages.success(request, f"Request rejected for {req.user}.")
        return redirect('payroll:allowance_request_approvals')

    # ---- Stage: HR review — final decision, applies the amount to the
    # employee's Allowance line-item for that month/year. ----
    if user.role in ('HR', 'SENIOR_HR') and req.status == AllowanceRequest.STATUS_PENDING_HR:
        if req.user_id == user.id:
            messages.error(request, "You cannot approve or reject your own request.")
            return redirect('payroll:allowance_request_approvals')

        req.reviewed_by_hr = user
        req.hr_reviewed_at = timezone.now()
        req.hr_remarks = remarks

        if decision == 'approve':
            req.status = AllowanceRequest.STATUS_APPROVED
            req.apply_to_allowance(user)
            req.save()
            messages.success(request, f"Request approved for {req.user} and added to their {calendar.month_name[req.month]} {req.year} allowances.")
        else:
            req.status = AllowanceRequest.STATUS_REJECTED
            req.save()
            messages.success(request, f"Request rejected for {req.user}.")
        return redirect('payroll:allowance_request_approvals')

    messages.error(request, "You cannot review this request.")
    return redirect('core:dashboard')
