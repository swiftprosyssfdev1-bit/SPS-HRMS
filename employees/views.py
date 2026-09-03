from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone
from django.urls import reverse

from core.decorators import admin_only_required, hr_or_admin_required, hr_only_required, hr_admin_or_manager_required, role_required
from core.models import User, Branch
from core.utils import user_can_switch_branch

from attendance.models import AttendanceRecord
from employees.models import EmployeeProfile, EmployeeDocument, ResignationRequest, BankDetail, TrainingFeedback
from employees.forms import (
    EmployeeSelfEditForm, NewEmployeeForm, IssueLetterForm, SelfDocumentForm,
    RoleChangeForm, ResignationRequestForm, HREmployeeEditForm,
    EmployeeIdentityForm, BankDetailForm, OnboardHRForm,
    TrainingFeedbackForm, TrainingDurationForm, TrainingReviewForm,
)
from projects.models import ProjectAssignment
from employees.id_utils import (
    generate_enrollment_id, generate_employee_id,
    enrollment_prefix_for_branch, employee_id_prefix_for_branch, split_id,
)
from payroll.models import SalaryStructure
from payroll.forms import SalaryStructureForm
from increments.models import IncrementRequest


def _present_user_ids_today():
    today = timezone.localdate()
    return set(
        AttendanceRecord.objects.filter(date=today, in_time__isnull=False).values_list('user_id', flat=True)
    )


def _can_apply_resignation(user):
    # HR can resign too â€” but their resignation is reviewed by Admin, not
    # by another HR colleague. Admin itself never applies for resignation.
    # PM and GM are included here too â€” previously missing, which meant
    # neither Edit Profile nor Apply for Resignation ever showed on their
    # My Profile page.
    return user.role in ('EMPLOYEE', 'MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'HR', 'SENIOR_HR', 'ADMIN', 'COO', 'CTO')

def _can_review_resignation(acting_user, resignation):
    """HR reviews resignations from Employees/Managers. Admin reviews
    resignations from HR â€” an HR resignation never goes to another HR."""
    if resignation.user.role in ('HR', 'SENIOR_HR'):
        return acting_user.role == 'ADMIN'
    return acting_user.role in ('HR', 'SENIOR_HR')


def _can_edit_identity(acting_user, emp_user):
    """Senior HR and Admin can edit anyone. HR can edit anyone except Admins, COOs, and Senior HR.
    A Manager can only edit members of their own department, and never themselves."""
    if acting_user.role in ('SENIOR_HR', 'ADMIN', 'COO'):
        return True
    if acting_user.role == 'HR':
        if emp_user.role in ('ADMIN', 'COO', 'SENIOR_HR'):
            return False
        return True
    if acting_user.is_manager():
        return emp_user.department_id is not None and emp_user.department_id == acting_user.department_id and emp_user.id != acting_user.id
    return False


def _department_sort_key(dept_name):
    """Data Entry departments first, then Software, then everything else
    alphabetically."""
    lower = dept_name.lower()
    if 'data entry' in lower:
        priority = 0
    elif 'software' in lower:
        priority = 1
    else:
        priority = 2
    return (priority, lower)


ROLE_WITHIN_DEPT_PRIORITY = {'MANAGER': 0, 'EMPLOYEE': 1}

# Directory ordering for everyone above regular Employees: COO, CTO, General
# Manager, Project Manager, Manager, then HR / GM (HR). Employees always
# come last, grouped by department.
DIRECTORY_ROLE_PRIORITY = {
    'COO': 0,
    'CTO': 1,
    'GENERAL_MANAGER': 2,
    'PROJECT_MANAGER': 3,
    'MANAGER': 4,
    'SENIOR_HR': 5,
    'HR': 5,
    'ADMIN': 0,
}


def _directory_sort_key(emp):
    """COO, CTO, General Manager, Project Manager, Manager, then HR / GM
    (HR) come first â€” alphabetical within each role. Everyone else
    (Employees) is grouped by department (Data Entry, then Software, then
    the rest alphabetically), alphabetical within each department."""
    name = (emp.first_name or emp.username).lower()

    role_priority = DIRECTORY_ROLE_PRIORITY.get(emp.role)
    if role_priority is not None:
        return (0, role_priority, name)

    dept_key = _department_sort_key(emp.department.name if emp.department else 'zzz_no_department')
    return (1, dept_key, name)


BRANCH_ORDER = ['B01', 'B02', 'B03', 'B04']


def _primary_branch(branches):
    """Picks the 'home' branch for an HR/Admin/COO user out of their
    selected accessible branches â€” lowest branch code wins (Chennai first)."""
    branches = list(branches)
    if not branches:
        return None
    branches.sort(key=lambda b: BRANCH_ORDER.index(b.code) if b.code in BRANCH_ORDER else 99)
    return branches[0]


@login_required
def my_profile(request):
    profile, _ = EmployeeProfile.objects.get_or_create(user=request.user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=request.user)
    documents = request.user.documents.all()
    latest_resignation = ResignationRequest.objects.filter(
        user=request.user
    ).order_by('-submitted_at').first()
    
    if latest_resignation and latest_resignation.status in ['ACCEPTED', 'REJECTED'] and not latest_resignation.employee_viewed_review:
        latest_resignation.employee_viewed_review = True
        latest_resignation.save(update_fields=['employee_viewed_review'])

    return render(request, 'employees/my_profile.html', {
        'profile': profile, 'bank_detail': bank_detail, 'documents': documents,
        'can_apply_resignation': _can_apply_resignation(request.user),
        'latest_resignation': latest_resignation,
    })


@login_required
def edit_my_profile(request):
    """Employee's own edit page â€” separate from the read-only my_profile
    view. Photo, the handful of self-editable fields, bank details, and
    document upload/delete all live here."""
    profile, _ = EmployeeProfile.objects.get_or_create(user=request.user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = EmployeeSelfEditForm(request.POST, request.FILES, instance=profile, user_instance=request.user)
        bank_form = BankDetailForm(request.POST, instance=bank_detail)

        saved_anything = False
        if form.is_valid():
            form.save()
            saved_anything = True
        else:
            messages.error(request, 'Please fix the highlighted profile fields below.')

        if bank_form.is_valid():
            bank_form.save()
            saved_anything = True
        else:
            messages.error(request, 'Please fix the highlighted bank detail fields below.')

        if saved_anything:
            messages.success(request, 'Your profile has been updated.')
        return redirect('employees:edit_my_profile')
    else:
        form = EmployeeSelfEditForm(instance=profile, user_instance=request.user)
        bank_form = BankDetailForm(instance=bank_detail)

    documents = request.user.documents.all()
    doc_form = SelfDocumentForm()

    return render(request, 'employees/edit_my_profile.html', {
        'form': form,
        'profile': profile,
        'documents': documents,
        'doc_form': doc_form,
        'bank_form': bank_form,
        'bank_detail': bank_detail,
    })


@login_required
def upload_own_document(request):
    if request.method == 'POST':
        form = SelfDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(commit=False)
            doc.user = request.user
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, 'Document uploaded.')
        else:
            messages.error(request, 'Please choose a valid document type and file.')
    return redirect('employees:my_profile')


@login_required
def delete_own_document(request, doc_id):
    """An employee can remove a document they uploaded to their own
    profile. Scoped to request.user so nobody can delete someone else's
    document by guessing an id."""
    doc = get_object_or_404(EmployeeDocument, id=doc_id, user=request.user)
    if request.method == 'POST':
        doc.delete()
        messages.success(request, 'Document deleted.')
    return redirect('employees:my_profile')


@login_required
def apply_resignation(request):
    if not _can_apply_resignation(request.user):
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    if ResignationRequest.objects.filter(user=request.user, status='PENDING').exists():
        messages.info(request, "You already have a resignation request pending HR's review.")
        return redirect('employees:my_profile')

    if request.method == 'POST':
        form = ResignationRequestForm(request.POST)
        if form.is_valid():
            resignation = form.save(commit=False)
            resignation.user = request.user
            resignation.save()
            messages.success(request, "Your resignation request has been submitted to HR.")
            return redirect('employees:my_profile')
    else:
        form = ResignationRequestForm()

    return render(request, 'employees/apply_resignation.html', {'form': form})


@hr_or_admin_required
def resignation_list(request):
    active_branch = getattr(request, 'active_branch', None)
    resignations = ResignationRequest.objects.select_related('user').all()
    if active_branch:
        resignations = resignations.filter(user__branch=active_branch)
    resignations = list(resignations)
    for r in resignations:
        r.can_review = _can_review_resignation(request.user, r)
    return render(request, 'employees/resignation_list.html', {'resignations': resignations})


@login_required
def approve_resignation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id)
    if not _can_review_resignation(request.user, resignation):
        messages.error(request, "You don't have permission to review this resignation.")
        return redirect('employees:resignation_list')
    if resignation.status != 'PENDING':
        messages.error(request, "This resignation cannot be approved at this stage.")
        return redirect('employees:resignation_list')

    if request.method == 'POST':
        custom_days = request.POST.get('custom_days')
        try:
            days = int(custom_days) if custom_days else int(request.POST.get('notice_period_days', 30))
        except ValueError:
            messages.error(request, "Enter a valid number of days.")
            return redirect('employees:resignation_list')

        if days < 0:
            messages.error(request, "Notice period cannot be negative.")
            return redirect('employees:resignation_list')

        # approve() sets profile.status='EXITED', exit_date and exit_reason.
        resignation.approve(hr_user=request.user, notice_period_days=days)
        
        from django.core.mail import send_mail
        from django.conf import settings
        
        # Notify Employee
        if resignation.user.email:
            send_mail(
                subject='Resignation Approved',
                message=f'Dear {resignation.user.get_full_name()},\n\nYour resignation has been approved by HR. You are required to serve a notice period of {days} days. Your final exit date will be {resignation.exit_date}.\n\nBest regards,\nHR Team',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[resignation.user.email],
                fail_silently=True,
            )
        
        # Notify Manager
        if resignation.user.manager and resignation.user.manager.email:
            send_mail(
                subject='Team Member Resignation Approved',
                message=f'Dear {resignation.user.manager.get_full_name()},\n\nThe resignation of your team member {resignation.user.get_full_name()} has been approved by HR. They will serve a notice period of {days} days and their final exit date is {resignation.exit_date}.\n\nBest regards,\nHR Team',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[resignation.user.manager.email],
                fail_silently=True,
            )

        messages.success(
            request,
            f"Resignation approved for {resignation.user}. Notice period: {days} days. Exit date: {resignation.exit_date}."
        )
    return redirect('employees:resignation_list')


@login_required
def reject_resignation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id)
    if not _can_review_resignation(request.user, resignation):
        messages.error(request, "You don't have permission to review this resignation.")
        return redirect('employees:resignation_list')
    if resignation.status != 'PENDING':
        messages.error(request, "This resignation cannot be rejected at this stage.")
        return redirect('employees:resignation_list')

    if request.method == 'POST':
        resignation.reject(hr_user=request.user)
        
        from django.core.mail import send_mail
        from django.conf import settings
        
        # Notify Employee
        if resignation.user.email:
            send_mail(
                subject='Resignation Rejected',
                message=f'Dear {resignation.user.get_full_name()},\n\nYour resignation request has been reviewed and rejected by HR. If you have any questions, please reach out to the HR department.\n\nBest regards,\nHR Team',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[resignation.user.email],
                fail_silently=True,
            )
            
        # Notify Manager
        if resignation.user.manager and resignation.user.manager.email:
            send_mail(
                subject='Team Member Resignation Rejected',
                message=f'Dear {resignation.user.manager.get_full_name()},\n\nThe resignation request of your team member {resignation.user.get_full_name()} has been rejected by HR.\n\nBest regards,\nHR Team',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[resignation.user.manager.email],
                fail_silently=True,
            )

        messages.info(request, f"Resignation rejected for {resignation.user}.")
    return redirect('employees:resignation_list')


@login_required
def negotiate_resignation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id)
    if not _can_review_resignation(request.user, resignation):
        messages.error(request, "You don't have permission to review this resignation.")
        return redirect('employees:resignation_list')
    if resignation.status != 'PENDING':
        messages.error(request, "You can only negotiate while the request is pending.")
        return redirect('employees:resignation_list')

    if request.method == 'POST':
        message = request.POST.get('message', '').strip()
        if not message:
            messages.error(request, "Write a message before sending.")
            return redirect('employees:resignation_list')
        resignation.send_negotiation(hr_user=request.user, message=message)
        messages.success(request, f"Your message has been sent to {resignation.user}.")
    return redirect('employees:resignation_list')


@login_required
def accept_resignation_offer(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id, user=request.user)
    if resignation.status != 'NEGOTIATING':
        messages.error(request, "There is no active offer to accept.")
        return redirect('employees:my_profile')

    if request.method == 'POST':
        resignation.accept_offer()
        messages.success(request, "You've accepted HR's offer. Your resignation has been withdrawn.")
    return redirect('employees:my_profile')


@login_required
def quit_after_negotiation(request, resignation_id):
    resignation = get_object_or_404(ResignationRequest, id=resignation_id, user=request.user)
    if resignation.status != 'NEGOTIATING':
        messages.error(request, "There is no active offer to respond to.")
        return redirect('employees:my_profile')

    if request.method == 'POST':
        resignation.quit_anyway(message=request.POST.get('message', ''))
        messages.success(request, "Your response has been sent to HR. They'll confirm your notice period shortly.")
    return redirect('employees:my_profile')


@hr_only_required
def onboard_employee(request):
    active_branch = getattr(request, 'active_branch', None)
    if request.method == 'POST':
        form = NewEmployeeForm(request.POST, acting_user=request.user)
        if form.is_valid():
            user = form.save(commit=False)
            if not user_can_switch_branch(request.user):
                user.branch = request.user.branch
            user.set_password(form.cleaned_data['password'])
            department = form.cleaned_data.get('department')
            if department and department.manager_id and user.role != User.ROLE_MANAGER:
                user.manager = department.manager
            else:
                user.manager = None
            user.employee_id = generate_employee_id(user.branch)
            user.save()
            profile = EmployeeProfile.objects.create(user=user, status='ONBOARDING')
            profile.enrollment_id = generate_enrollment_id(user.branch)
            profile.save()
            BankDetail.objects.get_or_create(user=user)
            # Auto-create training feedback record so HR can track the 30-day
            # training period and PM can submit feedback.
            TrainingFeedback.objects.create(
                employee=user,
                training_start_date=user.date_joined_company,
                training_duration_days=30,
            )
            project = form.cleaned_data.get('project')
            if project:
                ProjectAssignment.objects.create(
                    project=project, user=user,
                    role_on_project='Team Member',
                    start_date=timezone.localdate(),
                )
            messages.success(request, f'{user} onboarded successfully (Employee ID {user.employee_id}, Enrollment ID {profile.enrollment_id}). Share their login credentials securely.')
            return redirect('employees:employee_detail', user_id=user.id)
    else:
        initial = {}
        if active_branch and not user_can_switch_branch(request.user):
            initial['branch'] = active_branch.id
        form = NewEmployeeForm(initial=initial, acting_user=request.user)

    return render(request, 'employees/onboard.html', {'form': form, 'locked_branch': not user_can_switch_branch(request.user)})


@role_required('ADMIN')
def toggle_branch_admin_access(request, user_id):
    emp_user = get_object_or_404(User, id=user_id, role='HR')
    if request.method == 'POST':
        emp_user.can_access_all_branches = not emp_user.can_access_all_branches
        emp_user.save()
        state = "granted" if emp_user.can_access_all_branches else "revoked"
        messages.success(request, f"All-branch access {state} for {emp_user}.")
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_or_admin_required
def wfh_directory(request):
    """Lists everyone currently Work From Home in the active branch â€”
    both employees permanently flagged WFH on their profile, and anyone
    with an APPROVED day-based WFH request (leaves app) covering today."""
    from leaves.utils import approved_wfh_today

    active_branch = getattr(request, 'active_branch', None)

    if not active_branch:
        profiles = EmployeeProfile.objects.none()
    else:
        profiles = EmployeeProfile.objects.filter(
            work_type='WFH', status='ACTIVE'
        ).select_related('user', 'user__department', 'user__branch').filter(user__branch=active_branch)

    wfh_rows = [
        {'user': p.user, 'source': 'Profile (Permanent)'}
        for p in sorted(profiles, key=lambda p: _directory_sort_key(p.user))
    ]

    already_listed_ids = {row['user'].id for row in wfh_rows}
    for req in approved_wfh_today(active_branch):
        if req.user_id in already_listed_ids:
            continue
        wfh_rows.append({
            'user': req.user,
            'source': f"Approved Request ({req.start_date:%d %b} \u2013 {req.end_date:%d %b})",
        })
        already_listed_ids.add(req.user_id)

    wfh_rows.sort(key=lambda row: _directory_sort_key(row['user']))

    return render(request, 'employees/wfh_directory.html', {'wfh_rows': wfh_rows})


@hr_or_admin_required
def joiners_exits_list(request):
    """Recent New Joiners / Exited Employees for a given month.
    HR/Senior HR see their own active branch; COO/Admin can pass a
    branch= query param (e.g. from the branch-wise dashboard table)."""
    import calendar
    from datetime import date

    list_type = request.GET.get('type', 'joiners')
    if list_type not in ('joiners', 'exited'):
        list_type = 'joiners'

    today = timezone.localdate()
    month_param = request.GET.get('month')
    try:
        sel_year, sel_month = (int(p) for p in month_param.split('-'))
        month_start = date(sel_year, sel_month, 1)
    except (ValueError, AttributeError, TypeError):
        month_start = today.replace(day=1)
    month_end = date(
        month_start.year, month_start.month,
        calendar.monthrange(month_start.year, month_start.month)[1]
    )

    branch_id = request.GET.get('branch')
    if request.user.role in ('COO', 'ADMIN') and branch_id:
        branch = Branch.objects.filter(id=branch_id).first()
    else:
        branch = getattr(request, 'active_branch', None)

    rows = EmployeeProfile.objects.exclude(user__role='ADMIN').select_related(
        'user', 'user__branch', 'user__department'
    )
    if request.user.role in ('HR', 'SENIOR_HR'):
        rows = rows.exclude(user__role__in=['HR', 'SENIOR_HR'])
    if branch:
        rows = rows.filter(user__branch=branch)

    if list_type == 'exited':
        rows = rows.filter(
            status='EXITED', exit_date__gte=month_start, exit_date__lte=month_end
        ).order_by('-exit_date')
    else:
        rows = rows.filter(
            user__date_joined_company__gte=month_start, user__date_joined_company__lte=month_end
        ).order_by('-user__date_joined_company')

    return render(request, 'employees/joiners_exits.html', {
        'list_type': list_type,
        'rows': rows,
        'month_label': month_start.strftime('%B %Y'),
        'selected_month': month_start.strftime('%Y-%m'),
        'branch': branch,
    })


@role_required('ADMIN', 'HR', 'SENIOR_HR', 'COO', 'GENERAL_MANAGER', 'MANAGER', 'PROJECT_MANAGER', 'CTO')
def employee_directory(request):
    query = request.GET.get('q', '')
    department_filter = request.GET.get('department', '')
    status_filter = request.GET.get('status', '')

    employees = User.objects.select_related('department', 'profile', 'branch')

    active_branch = getattr(request, 'active_branch', None)
    is_gm = request.user.role == 'GENERAL_MANAGER'
    is_team_scoped_manager = request.user.role in ('MANAGER', 'PROJECT_MANAGER', 'CTO')

    if is_gm:
        # A GM's directory is bounded by their own reporting hierarchy â€”
        # never the whole branch, and never another GM's people.
        from core.utils import get_gm_scope
        gm_ids = get_gm_scope(request.user).values_list('id', flat=True)
        employees = employees.filter(id__in=list(gm_ids))
    elif is_team_scoped_manager:
        # A Manager/PM/CTO only sees their own team â€” the same reporting-
        # hierarchy + project-team logic used for a GM's direct reports.
        from core.utils import get_manager_team
        team_ids = get_manager_team(request.user).values_list('id', flat=True)
        employees = employees.filter(id__in=list(team_ids))
    elif active_branch:
        # Everyone else sees the directory for the currently selected active branch (Admins and COOs are also visible everywhere)
        employees = employees.filter(Q(branch=active_branch) | Q(role__in=['ADMIN', 'COO'])).distinct()
    else:
        # Prevent users from seeing all data if they lack an active branch
        employees = User.objects.none()

    if query:
        employees = employees.filter(
            Q(first_name__icontains=query) |
            Q(username__icontains=query) |
            Q(employee_id__icontains=query)
        )

    if department_filter:
        if department_filter == 'No Department':
            employees = employees.filter(department__isnull=True)
        else:
            employees = employees.filter(department__name=department_filter)

    employees = list(employees)

    base_qs = User.objects.exclude(id=request.user.id).exclude(profile__status='ONBOARDING')
    if request.user.role in ('HR', 'SENIOR_HR'):
        base_qs = base_qs.exclude(role__in=['HR', 'SENIOR_HR'])
    if is_gm:
        from core.utils import get_gm_scope
        base_qs = base_qs.filter(id__in=list(get_gm_scope(request.user).values_list('id', flat=True)))
    elif is_team_scoped_manager:
        from core.utils import get_manager_team
        base_qs = base_qs.filter(id__in=list(get_manager_team(request.user).values_list('id', flat=True)))
    elif active_branch:
        base_qs = base_qs.filter(branch=active_branch)
    dept_qs = base_qs.select_related('department')
    dept_names = sorted(
        {emp.department.name for emp in dept_qs if emp.department},
        key=_department_sort_key,
    )
    has_no_dept = base_qs.filter(department__isnull=True).exists()
    departments = dept_names + (['No Department'] if has_no_dept else [])

    employee_rows = []
    for emp in employees:
        profile = getattr(emp, 'profile', None)
        if profile and profile.status == 'EXITED':
            emp_status = 'EXITED'
        elif profile and profile.status == 'ONBOARDING':
            emp_status = 'ONBOARD'
        else:
            emp_status = 'ACTIVE'

        if status_filter and status_filter != emp_status:
            continue
        emp.display_status = emp_status
        employee_rows.append(emp)

    employee_rows.sort(key=_directory_sort_key)

    from projects.models import ProjectAssignment
    all_emp_ids = [emp.id for emp in employee_rows]
    active_pas = ProjectAssignment.objects.filter(
        user_id__in=all_emp_ids, is_current=True
    ).exclude(project__status='COMPLETED').select_related('project')

    if is_gm:
        active_pas = active_pas.filter(Q(project__gm=request.user) | Q(project__created_by=request.user))
    elif is_team_scoped_manager:
        active_pas = active_pas.filter(Q(project__manager=request.user) | Q(project__created_by=request.user))

    current_projects = {pa.user_id: pa.project.name for pa in active_pas}
    for emp in employee_rows:
        emp.current_project = current_projects.get(emp.id)

    # For PM/CTO/Manager/GM, group their team project-wise: any employee
    # assigned to a project is grouped by that project. If unassigned,
    # Production employees fall into 'Unassigned' while non-production
    # employees group by their role.
    is_team_view = is_gm or is_team_scoped_manager
    directory_groups = []
    if is_team_view:
        project_groups = {}
        role_groups = {}
        for emp in employee_rows:
            if emp.current_project:
                project_groups.setdefault(emp.current_project, []).append(emp)
            else:
                is_production = bool(emp.department and 'production' in emp.department.name.lower() and emp.role == 'EMPLOYEE')
                if is_production:
                    project_groups.setdefault('Unassigned', []).append(emp)
                else:
                    key = emp.get_role_display()
                    role_groups.setdefault(key, []).append(emp)

        for name in sorted(project_groups, key=lambda n: (n == 'Unassigned', n)):
            directory_groups.append((name, 'project', project_groups[name]))
        for name in sorted(role_groups):
            directory_groups.append((name, 'role', role_groups[name]))

    return render(request, 'employees/directory.html', {
        'employees': employee_rows,
        'query': query,
        'departments': departments,
        'selected_department': department_filter,
        'selected_status': status_filter,
        'is_team_view': is_team_view,
        'directory_groups': directory_groups,
    })


@hr_admin_or_manager_required
def employee_detail(request, user_id):
    """HR/Admin/Manager view. Read-only by default. HR/Admin/the employee's
    Manager can toggle Edit mode (?edit=1)."""
    emp_user = get_object_or_404(User, id=user_id)

    if request.user.role in ('HR', 'SENIOR_HR') and emp_user.role == 'ADMIN':
        messages.error(request, "You do not have permission to view this account.")
        return redirect('employees:directory')

    if request.user.is_manager() and request.user.role not in ('HR', 'SENIOR_HR', 'ADMIN'):
        if emp_user.department_id != request.user.department_id:
            messages.error(request, "You can only view profiles of your own department's staff.")
            return redirect('employees:my_department')

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=emp_user)
    documents = emp_user.documents.all()
    role_form = RoleChangeForm(instance=emp_user, acting_user=request.user)
    latest_resignation = ResignationRequest.objects.filter(user=emp_user).first()
    can_review_resignation = _can_review_resignation(request.user, latest_resignation) if latest_resignation else False

    salary_structure = SalaryStructure.objects.filter(user=emp_user).first()

    increments_qs = IncrementRequest.objects.filter(user=emp_user, status='APPROVED').order_by('-effective_date')
    increment_count = increments_qs.count()
    latest_increment = increments_qs.first()

    can_edit_identity = _can_edit_identity(request.user, emp_user)
    edit_mode = can_edit_identity and request.GET.get('edit') == '1'

    return render(request, 'employees/employee_detail.html', {
        'emp_user': emp_user, 'profile': profile, 'bank_detail': bank_detail,
        'documents': documents,
        'role_form': role_form, 'latest_resignation': latest_resignation,
        'can_review_resignation': can_review_resignation,
        'salary_structure': salary_structure,
        'increment_count': increment_count, 'latest_increment': latest_increment,
        'edit_mode': edit_mode, 'can_edit_identity': can_edit_identity,
    })


@login_required
def update_employee_status(request, user_id):
    """HR moves any employee between Onboarding, Active, and Exited.
    Admin can only do this for HR accounts specifically. Moving
    EXITED -> ACTIVE (a rejoin) automatically preserves their prior
    joining date in old_joining_date."""
    emp_user = get_object_or_404(User, id=user_id)

    is_authorized = (
        request.user.role in ('HR', 'SENIOR_HR') or
        (request.user.role == 'ADMIN' and emp_user.role in ('HR', 'SENIOR_HR', 'ADMIN'))
    )
    if not is_authorized:
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('employees:edit_employee_profile', user_id=emp_user.id)

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)

    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in ('ONBOARDING', 'ACTIVE', 'EXITED'):
            if new_status == 'ACTIVE' and profile.status == 'EXITED' and not profile.old_joining_date:
                profile.old_joining_date = emp_user.date_joined_company
            profile.status = new_status
            profile.save()
            messages.success(request, f"{emp_user}'s status changed to {profile.get_status_display()}.")
        else:
            messages.error(request, "Invalid status selected.")
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_only_required
def issue_letter(request):
    """HR issues an official letter (offer, promotion, experience,
    relieving, appointment, increment) to one or more employees in one
    go. Each selected employee gets their own copy of the file, visible
    on their own Letters page — HR/Admin accounts are never a valid
    recipient here."""
    active_branch = getattr(request, 'active_branch', None)
    employee_queryset = User.objects.exclude(role__in=['ADMIN', 'HR', 'SENIOR_HR']).select_related('department', 'branch')
    if active_branch:
        employee_queryset = employee_queryset.filter(branch=active_branch)
    employee_queryset = employee_queryset.order_by('first_name', 'username')

    preselect_id = request.GET.get('employee')

    if request.method == 'POST':
        form = IssueLetterForm(request.POST, request.FILES, employee_queryset=employee_queryset)
        if form.is_valid():
            doc_type = form.cleaned_data['doc_type']
            recipients = form.cleaned_data['employees']
            uploaded_file = form.cleaned_data['file']

            for recipient in recipients:
                uploaded_file.seek(0)
                EmployeeDocument.objects.create(
                    user=recipient,
                    doc_type=doc_type,
                    file=ContentFile(uploaded_file.read(), name=uploaded_file.name),
                    uploaded_by=request.user,
                )

            names = ', '.join((r.get_full_name() or r.username) for r in recipients)
            messages.success(request, f'Letter issued to {recipients.count()} employee(s): {names}.')
            return redirect('employees:issue_letter')
        else:
            messages.error(request, 'Please fix the errors below.')
    else:
        initial = {}
        if preselect_id and preselect_id.isdigit():
            initial['employees'] = [int(preselect_id)]
        form = IssueLetterForm(employee_queryset=employee_queryset, initial=initial)

    recent_letters = EmployeeDocument.objects.filter(
        doc_type__in=EmployeeDocument.HR_DOC_TYPES
    ).select_related('user').order_by('-uploaded_at')[:15]

    return render(request, 'employees/issue_letter.html', {
        'form': form,
        'recent_letters': recent_letters,
    })


@login_required
def my_letters(request):
    """Every employee/manager's own page for the official letters HR has
    issued to them — offer, promotion, experience, relieving, etc.
    Opening this page clears the "new letter" sidebar badge."""
    letters = request.user.documents.filter(
        doc_type__in=EmployeeDocument.HR_DOC_TYPES
    ).order_by('-uploaded_at')
    letters.filter(viewed_by_employee=False).update(viewed_by_employee=True)
    return render(request, 'employees/my_letters.html', {'letters': letters})


@hr_only_required
def delete_document(request, user_id, doc_id):
    """HR removes a document from an employee's profile. Redirects back
    to wherever the delete was triggered from (edit profile page or the
    Issue Letter page's Recently Issued list)."""
    emp_user = get_object_or_404(User, id=user_id)
    doc = get_object_or_404(EmployeeDocument, id=doc_id, user=emp_user)
    if request.method == 'POST':
        doc.delete()
        messages.success(request, 'Document deleted.')
    if request.POST.get('next') == 'issue_letter':
        return redirect('employees:issue_letter')
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_admin_or_manager_required
def edit_employee_profile(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)

    if not _can_edit_identity(request.user, emp_user):
        messages.error(request, "You do not have permission to edit this profile.")
        return redirect('employees:employee_detail', user_id=emp_user.id)

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    bank_detail, _ = BankDetail.objects.get_or_create(user=emp_user)

    employee_id_prefix = employee_id_prefix_for_branch(emp_user.branch)
    enrollment_prefix = enrollment_prefix_for_branch(emp_user.branch)

    if request.method == 'POST':
        form = HREmployeeEditForm(request.POST, instance=emp_user, acting_user=request.user)
        identity_form = EmployeeIdentityForm(request.POST, request.FILES, instance=profile)
        bank_form = BankDetailForm(request.POST, instance=bank_detail)

        enrollment_suffix = request.POST.get('enrollment_suffix', '').strip()
        employee_id_suffix = request.POST.get('employee_id_suffix', '').strip()

        saved_anything = False

        # Each form is validated and saved on its own — one section having
        # no changes (or being left blank) never blocks another section
        # from saving.
        if form.is_valid():
            user_obj = form.save(commit=False)
            if 'new_password' in form.cleaned_data and form.cleaned_data['new_password']:
                user_obj.set_password(form.cleaned_data['new_password'])
                
            if emp_user.role in ('HR', 'SENIOR_HR'):
                user_obj.branch = emp_user.branch  # HR branch is admin-set, not editable here

            current_employee_id_prefix = employee_id_prefix_for_branch(user_obj.branch)
            current_enrollment_prefix = enrollment_prefix_for_branch(user_obj.branch)

            if employee_id_suffix:
                user_obj.employee_id = f"{current_employee_id_prefix}{employee_id_suffix}"

            if user_obj.role != User.ROLE_MANAGER:
                manual_manager = form.cleaned_data.get('manager')
                dept = form.cleaned_data.get('department')
                if manual_manager:
                    # HR explicitly picked who this person reports to â€”
                    # e.g. a General Manager, for an Assistant / Team
                    # Member who isn't tied to any department or project.
                    user_obj.manager = manual_manager
                elif dept and dept.manager_id:
                    user_obj.manager = dept.manager
                else:
                    user_obj.manager = None

            user_obj.save()
            if emp_user.role in ('HR', 'SENIOR_HR'):
                user_obj.accessible_branches.set(form.cleaned_data.get('accessible_branches'))

            employee_id_prefix = current_employee_id_prefix
            enrollment_prefix = current_enrollment_prefix
            saved_anything = True
        else:
            messages.error(request, f"Some basic profile fields need fixing â€” those weren't saved. Errors: {form.errors}")

        if identity_form.is_valid():
            profile_obj = identity_form.save(commit=False)
            if enrollment_suffix:
                profile_obj.enrollment_id = f"{enrollment_prefix}{enrollment_suffix}"
            profile_obj.save()
            saved_anything = True
        else:
            messages.error(request, f"Some identity fields need fixing â€” those weren't saved. Errors: {identity_form.errors}")

        if bank_form.is_valid():
            bank_form.save()
            saved_anything = True
        else:
            messages.error(request, "Some bank details need fixing â€” those weren't saved.")

        if saved_anything:
            messages.success(request, f"{emp_user}'s profile has been updated.")

        return redirect('employees:edit_employee_profile', user_id=emp_user.id)
    else:
        form = HREmployeeEditForm(instance=emp_user, acting_user=request.user)
        identity_form = EmployeeIdentityForm(instance=profile)
        bank_form = BankDetailForm(instance=bank_detail)

    documents = emp_user.documents.all()
    role_form = RoleChangeForm(instance=emp_user, acting_user=request.user)
    salary_structure, _ = SalaryStructure.objects.get_or_create(user=emp_user, defaults={'basic': 0})
    salary_form = SalaryStructureForm(instance=salary_structure)

    return render(request, 'employees/edit_employee_profile.html', {
        'emp_user': emp_user, 'form': form, 'identity_form': identity_form, 'bank_form': bank_form,
        'documents': documents, 'role_form': role_form,
        'profile': profile, 'bank_detail': bank_detail, 'salary_form': salary_form,
        'is_admin_viewer': request.user.role == 'ADMIN',
        # FIXED: was hardcoded to 'HR' only in the duplicate definition that
        # used to win â€” Admin now correctly sees Role & Access too.
        'can_change_role': request.user.role in ('HR', 'SENIOR_HR', 'ADMIN'),
        'enrollment_prefix': enrollment_prefix,
        'employee_id_prefix': employee_id_prefix,
        'enrollment_suffix': request.POST.get('enrollment_suffix', split_id(profile.enrollment_id, enrollment_prefix)),
        'employee_id_suffix': request.POST.get('employee_id_suffix', split_id(emp_user.employee_id, employee_id_prefix)),
    })


@hr_or_admin_required
def change_role(request, user_id):
    """FIXED: this used to be defined twice. The version that was actually
    running blocked Admin entirely ('Admin has view-only access and cannot
    change roles') â€” which contradicted the Role & Access dropdown we built
    for Admin. That block has been removed: both HR and Admin can now
    change roles, with RoleChangeForm itself restricting which roles HR
    (vs Admin) is allowed to pick from."""
    emp_user = get_object_or_404(User, id=user_id)

    if emp_user == request.user:
        messages.error(request, "You cannot change your own role.")
        return redirect('employees:edit_employee_profile', user_id=emp_user.id)

    if request.user.role in ('HR', 'SENIOR_HR') and emp_user.role in ('HR', 'SENIOR_HR', 'ADMIN'):
        messages.error(request, "HR cannot change the role of an HR or Admin account.")
        return redirect('employees:edit_employee_profile', user_id=emp_user.id)

    if request.method == 'POST':
        form = RoleChangeForm(request.POST, instance=emp_user, acting_user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"{emp_user}'s role changed to {emp_user.get_role_display()}.")
        else:
            messages.error(request, "Invalid role selection.")
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@hr_only_required
def delete_employee(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    if emp_user == request.user:
        messages.error(request, "You cannot delete your own account.")
        return redirect('employees:directory')

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    if profile.status != 'EXITED':
        messages.error(request, "This employee can only be deleted after their resignation has been accepted.")
        return redirect('employees:employee_detail', user_id=emp_user.id)

    if request.method == 'POST':
        name = str(emp_user)
        emp_user.delete()
        messages.success(request, f"{name} has been removed from the system")
        return redirect('employees:directory')
    return render(request, 'employees/confirm_delete.html', {'emp_user': emp_user})


@hr_only_required
def update_salary_structure(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    structure, _ = SalaryStructure.objects.get_or_create(user=emp_user, defaults={'basic': 0})
    if request.method == 'POST':
        form = SalaryStructureForm(request.POST, instance=structure)
        if form.is_valid():
            form.save()
            messages.success(request, 'Salary structure saved.')
        else:
            messages.error(request, 'Please fix the highlighted salary fields.')
    return redirect('employees:edit_employee_profile', user_id=emp_user.id)


@login_required
def my_department(request):
    if not request.user.is_manager():
        messages.error(request, "Only managers can view this page.")
        return redirect('core:dashboard')

    staff = list(
        User.objects.filter(
            department=request.user.department,
            branch=request.user.branch,
        ).exclude(id=request.user.id).select_related('profile')
    )
    present_user_ids = _present_user_ids_today()
    for s in staff:
        s.is_present_today = s.id in present_user_ids
    return render(request, 'employees/my_department.html', {'staff': staff, 'department': request.user.department})


@login_required
def team_member_detail(request, user_id):
    if not request.user.is_manager():
        messages.error(request, "Only managers can view this page.")
        return redirect('core:dashboard')

    emp_user = get_object_or_404(User, id=user_id, department=request.user.department, branch=request.user.branch)
    if emp_user.id == request.user.id:
        messages.error(request, "You cannot view your own team member page.")
        return redirect('employees:my_department')

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    records = AttendanceRecord.objects.filter(user=emp_user)[:30]

    today = timezone.localdate()
    is_onboarding = profile.status == 'ONBOARDING'
    is_present_today = AttendanceRecord.objects.filter(
        user=emp_user, date=today, in_time__isnull=False
    ).exists()

    return render(request, 'employees/team_member_detail.html', {
        'emp_user': emp_user, 'profile': profile, 'records': records,
        'is_onboarding': is_onboarding, 'is_present_today': is_present_today,
    })


@login_required
def my_resignation(request):
    if not _can_apply_resignation(request.user):
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    resignations = ResignationRequest.objects.filter(user=request.user).order_by('-submitted_at')
    pending_resignation = resignations.filter(status__in=['PENDING', 'NEGOTIATING']).first()

    return render(request, 'employees/my_resignation.html', {
        'resignations': resignations,
        'pending_resignation': pending_resignation,
    })


@hr_or_admin_required
def onboard_hr(request):
    """FIXED: the role-dispatch block used to be indented at the same
    level as `if form.is_valid():` instead of inside it â€” meaning it ran
    even on an invalid form (crashing with UnboundLocalError on `role`).
    The Employee/Manager branch was also missing a `return redirect(...)`,
    which meant a successful onboard of an Employee/Manager here would
    fall through and implicitly return None, crashing with
    'view didn't return an HttpResponse'. Both are fixed below.

    HR can now also reach this view to onboard another HR, assigning
    branch access herself â€” independent of Admin. OnboardHRForm restricts
    an HR actor to only the HR role and to branches she herself can
    access."""
    if request.method == 'POST':
        form = OnboardHRForm(request.POST, acting_user=request.user)
        if form.is_valid():
            role = form.cleaned_data['role']
            user = form.save(commit=False)
            user.role = role
            user.set_password(form.cleaned_data['password'])

            if role in (User.ROLE_EMPLOYEE, User.ROLE_MANAGER):
                branch = form.cleaned_data['branch']
                department = form.cleaned_data.get('department')
                user.branch = branch
                if department and department.manager_id and role != User.ROLE_MANAGER:
                    user.manager = department.manager
                else:
                    user.manager = None
                user.employee_id = generate_employee_id(user.branch)
                user.save()

                profile = EmployeeProfile.objects.create(user=user, status='ONBOARDING')
                profile.enrollment_id = generate_enrollment_id(user.branch)
                profile.save()
                BankDetail.objects.get_or_create(user=user)

                role_label = 'Manager' if role == User.ROLE_MANAGER else 'Employee'
                messages.success(
                    request,
                    f'{user} onboarded as {role_label} successfully (Employee ID {user.employee_id}, Enrollment ID {profile.enrollment_id}).'
                )
                return redirect('employees:employee_detail', user_id=user.id)

            else:  # HR, Admin, or COO
                user.manager = None
                accessible = form.cleaned_data['accessible_branches']
                user.branch = _primary_branch(accessible)

                if role in (User.ROLE_HR, User.ROLE_SENIOR_HR):
                    user.employee_id = generate_employee_id(user.branch)

                user.save()
                user.accessible_branches.set(accessible)

                profile = EmployeeProfile.objects.create(user=user, status='ONBOARDING')
                if role in (User.ROLE_HR, User.ROLE_SENIOR_HR):
                    profile.enrollment_id = generate_enrollment_id(user.branch)
                    profile.save()

                if role in (User.ROLE_HR, User.ROLE_SENIOR_HR):
                    role_label = 'Senior HR' if role == User.ROLE_SENIOR_HR else 'HR'
                    messages.success(
                        request,
                        f'{user} onboarded as {role_label} successfully (Employee ID {user.employee_id}, Enrollment ID {profile.enrollment_id}). Branch access: {", ".join(b.name for b in accessible)}.'
                    )
                elif role == User.ROLE_COO:
                    messages.success(request, f'{user} onboarded as COO successfully.')
                else:
                    messages.success(request, f'{user} onboarded as Admin successfully.')

                return redirect('employees:employee_detail', user_id=user.id)
        # form invalid â€” fall through to re-render with errors below
    else:
        form = OnboardHRForm(acting_user=request.user)

    return render(request, 'employees/onboard_hr.html', {'form': form})

@login_required
def complete_onboarding(request, user_id):
    emp_user = get_object_or_404(User, id=user_id)
    user = request.user

    can_complete = (
        (user.role in ('HR', 'SENIOR_HR') and emp_user.role in ('EMPLOYEE', 'MANAGER')) or
        (user.role == 'ADMIN' and emp_user.role in ('HR', 'SENIOR_HR', 'ADMIN'))
    )
    if not can_complete:
        messages.error(request, "You do not have permission to activate this user.")
        return redirect('employees:employee_detail', user_id=emp_user.id)

    profile, _ = EmployeeProfile.objects.get_or_create(user=emp_user)
    if request.method == 'POST':
        profile.status = 'ACTIVE'
        profile.save()
        messages.success(request, f"{emp_user} has completed onboarding and is now Active.")
    return redirect('employees:employee_detail', user_id=emp_user.id)
import csv
from django.http import HttpResponse
from datetime import datetime
from django.db.models import Sum
from .models import ProductionAppraisal
from .forms import ProductionAppraisalFormSet

HOUR_SLOTS = ['9-10', '10-11', '11-12', '12-13', '14-15', '15-16', '16-17', '17-18']

@login_required
@role_required('HR', 'SENIOR_HR', 'ADMIN')
def appraisal_dashboard(request):
    """Increment-relevant appraisal calculation (target vs actual, and the
    resulting efficiency %). This is only for HR's own manual review when
    deciding increments â€” not shown on Manager/PM/COO dashboards."""
    employees = User.objects.filter(
        role='EMPLOYEE',
        department__name__icontains='Production',
        profile__status='ACTIVE'
    )
    if not (request.user.role == 'ADMIN' or getattr(request.user, 'can_access_all_branches', False)):
        employees = employees.filter(branch=request.user.branch)

    date_str = request.GET.get('date', timezone.localdate().strftime('%Y-%m-%d'))
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        selected_date = timezone.localdate()

    rows = ProductionAppraisal.objects.filter(date=selected_date, user__in=employees)
    totals = {}
    for r in rows:
        d = totals.setdefault(r.user_id, {'target': 0, 'actual': 0})
        d['target'] += r.target
        d['actual'] += r.actual

    employee_data = []
    for emp in employees:
        t = totals.get(emp.id)
        if t:
            total_target = t['target']
            total_actual = t['actual']
            efficiency = min((total_actual / total_target * 100) if total_target else 0, 100)
        else:
            total_target = total_actual = efficiency = 0
        employee_data.append({
            'user': emp,
            'total_target': total_target,
            'total_actual': total_actual,
            'efficiency': efficiency,
        })

    context = {
        'employees': employee_data,
        'selected_date': selected_date,
    }
    return render(request, 'employees/appraisal_dashboard.html', context)

@login_required
@role_required('MANAGER', 'PROJECT_MANAGER', 'HR', 'SENIOR_HR', 'ADMIN')
def appraisal_entry(request, user_id, date_str):
    emp = get_object_or_404(User, id=user_id, role='EMPLOYEE')
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        selected_date = timezone.localdate()

    if not ProductionAppraisal.objects.filter(user=emp, date=selected_date).exists():
        for slot in HOUR_SLOTS:
            ProductionAppraisal.objects.create(user=emp, date=selected_date, hour_slot=slot)

    queryset = ProductionAppraisal.objects.filter(user=emp, date=selected_date).order_by('id')

    if request.method == 'POST':
        formset = ProductionAppraisalFormSet(request.POST, queryset=queryset)
        if formset.is_valid():
            formset.save()
            messages.success(request, f'Appraisal saved for {emp.get_full_name()} on {selected_date}')
            return redirect(f"{reverse('employees:appraisal_dashboard')}?date={selected_date}")
    else:
        formset = ProductionAppraisalFormSet(queryset=queryset)

    context = {
        'emp': emp,
        'selected_date': selected_date,
        'formset': formset,
    }
    return render(request, 'employees/appraisal_entry.html', context)

@login_required
def my_appraisals(request):
    if not (request.user.department and 'Production' in request.user.department.name):
        raise PermissionDenied("You do not have permission to view this page.")

    today = timezone.localdate()
    month_start = today.replace(day=1)

    records = ProductionAppraisal.objects.filter(user=request.user).order_by('-date', 'id')

    grouped = {}
    for r in records:
        grouped.setdefault(r.date, []).append(r)

    grouped_appraisals = []
    total_target = 0
    total_actual = 0
    for date, recs in grouped.items():
        day_target = sum(r.target for r in recs)
        day_actual = sum(r.actual for r in recs)
        day_efficiency = min((day_actual / day_target * 100) if day_target else 0, 100)
        grouped_appraisals.append({
            'date': date,
            'efficiency': day_efficiency,
            'records': recs,
        })
        if date >= month_start:
            total_target += day_target
            total_actual += day_actual

    avg_efficiency = min((total_actual / total_target * 100) if total_target else 0, 100)

    context = {
        'grouped_appraisals': grouped_appraisals,
        'total_target': total_target,
        'total_actual': total_actual,
        'avg_efficiency': round(avg_efficiency, 2)
    }
    return render(request, 'employees/my_appraisals.html', context)

@login_required
@role_required('HR', 'SENIOR_HR', 'ADMIN')
def export_appraisals(request):
    date_str = request.GET.get('date', timezone.localdate().strftime('%Y-%m-%d'))
    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        selected_date = timezone.localdate()

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="appraisals_{selected_date}.csv"'

    writer = csv.writer(response)
    writer.writerow(['Employee ID', 'Name', 'Department', 'Branch', 'Date', 'Total Target', 'Total Actual', 'Efficiency %'])

    rows = ProductionAppraisal.objects.filter(date=selected_date).select_related('user__department', 'user__branch')
    if not (request.user.role in ['ADMIN', 'COO'] or getattr(request.user, 'can_access_all_branches', False)):
        rows = rows.filter(user__branch=request.user.branch)

    totals = {}
    for r in rows:
        d = totals.setdefault(r.user, {'target': 0, 'actual': 0})
        d['target'] += r.target
        d['actual'] += r.actual

    for user, t in totals.items():
        efficiency = (t['actual'] / t['target'] * 100) if t['target'] else 0
        writer.writerow([
            user.employee_id,
            user.get_full_name(),
            user.department.name if user.department else '',
            user.branch.name if user.branch else '',
            selected_date,
            t['target'],
            t['actual'],
            round(efficiency, 2)
        ])

    return response


# ---------------------------------------------------------------------------
# Training Feedback views
# ---------------------------------------------------------------------------

from datetime import timedelta

@login_required
def notice_period_list(request):
    if request.user.role not in ('HR', 'SENIOR_HR'):
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    active_branch = getattr(request, 'active_branch', None)

    qs = ResignationRequest.objects.select_related(
        'user', 'user__department', 'user__branch', 'user__profile'
    ).filter(status='ACCEPTED', user__profile__status='EXITED')

    if active_branch:
        qs = qs.filter(user__branch=active_branch)

    if request.method == 'POST':
        from employees.forms import NoticePeriodDurationForm
        rr_id = request.POST.get('rr_id')
        rr = get_object_or_404(ResignationRequest, id=rr_id)
        duration_form = NoticePeriodDurationForm(request.POST, instance=rr)
        if duration_form.is_valid():
            new_days = duration_form.cleaned_data['notice_period_days']
            if rr.reviewed_at:
                new_exit = timezone.localtime(rr.reviewed_at).date() + timedelta(days=new_days)
            else:
                new_exit = timezone.localdate() + timedelta(days=new_days)
            
            rr.exit_date = new_exit
            rr.notice_period_days = new_days
            rr.save()
            
            profile = rr.user.profile
            profile.exit_date = new_exit
            profile.save()
            
            messages.success(
                request,
                f"Notice period updated to {new_days} days for {rr.user.get_full_name()}."
            )
        else:
            messages.error(request, "Invalid duration value.")
        return redirect('employees:notice_period_list')

    from employees.forms import NoticePeriodDurationForm
    records = []
    today = timezone.localdate()
    for rr in qs:
        # Only show employees currently serving notice period (exit date in future)
        if rr.exit_date and rr.exit_date < today:
            continue
            
        days_left = (rr.exit_date - today).days if rr.exit_date else 0
        records.append({
            'rr': rr,
            'days_left': days_left,
            'duration_form': NoticePeriodDurationForm(instance=rr),
        })
        
    records.sort(key=lambda x: x['days_left'])

    return render(request, 'employees/notice_period_list.html', {
        'records': records,
        'active_branch': active_branch,
    })

@login_required
def training_feedback_list(request):
    """HR/Senior HR: see all onboarding employees with their training status.
    HR can also update the training duration for any employee from this page."""
    if request.user.role not in ('HR', 'SENIOR_HR'):
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    active_branch = getattr(request, 'active_branch', None)

    # Base queryset â€” only ONBOARDING employees who have a training record
    qs = TrainingFeedback.objects.select_related(
        'employee', 'employee__department', 'employee__branch',
        'submitted_by', 'reviewed_by',
    ).filter(employee__profile__status='ONBOARDING')

    if active_branch:
        qs = qs.filter(employee__branch=active_branch)

    # Handle duration update POST (HR changes training_duration_days)
    if request.method == 'POST':
        tf_id = request.POST.get('tf_id')
        tf = get_object_or_404(TrainingFeedback, id=tf_id)
        duration_form = TrainingDurationForm(request.POST, instance=tf)
        if duration_form.is_valid():
            duration_form.save()
            messages.success(
                request,
                f"Training duration updated to {tf.training_duration_days} days for {tf.employee.get_full_name()}."
            )
        else:
            messages.error(request, "Invalid duration value. Please enter a positive number.")
        return redirect('employees:training_feedback_list')

    # Attach a blank duration form to each record so the template can render inline forms
    records = []
    for tf in qs:
        records.append({
            'tf': tf,
            'duration_form': TrainingDurationForm(instance=tf),
        })

    return render(request, 'employees/training_feedback_list.html', {
        'records': records,
        'active_branch': active_branch,
    })


@login_required
def review_training_feedback(request, tf_id):
    """HR/Senior HR: read PM's submitted feedback and mark it as Reviewed
    with optional HR notes."""
    if request.user.role not in ('HR', 'SENIOR_HR'):
        messages.error(request, "You do not have permission to perform this action.")
        return redirect('core:dashboard')

    tf = get_object_or_404(TrainingFeedback, id=tf_id)

    if tf.status != 'SUBMITTED':
        messages.error(request, "Only submitted feedback can be reviewed.")
        return redirect('employees:training_feedback_list')

    if request.method == 'POST':
        review_form = TrainingReviewForm(request.POST, instance=tf)
        if review_form.is_valid():
            tf_obj = review_form.save(commit=False)
            tf_obj.status = 'REVIEWED'
            tf_obj.reviewed_by = request.user
            tf_obj.reviewed_at = timezone.now()
            tf_obj.save()
            messages.success(request, f"Training feedback for {tf.employee.get_full_name()} marked as reviewed.")
            return redirect('employees:training_feedback_list')
    else:
        review_form = TrainingReviewForm(instance=tf)

    return render(request, 'employees/review_training_feedback.html', {
        'tf': tf,
        'review_form': review_form,
    })


@login_required
def submit_training_feedback(request, user_id):
    """Project Manager: submit training feedback for an onboarding employee.
    The PM can re-submit (edit) the feedback as long as HR hasn't reviewed it."""
    if request.user.role != 'PROJECT_MANAGER':
        messages.error(request, "Only Project Managers can submit training feedback.")
        return redirect('core:dashboard')

    emp_user = get_object_or_404(User, id=user_id)
    profile = getattr(emp_user, 'profile', None)

    if not profile or profile.status != 'ONBOARDING':
        messages.error(request, "Training feedback can only be submitted for employees currently in onboarding.")
        return redirect('employees:my_team_training')

    tf = getattr(emp_user, 'training_feedback', None)
    if not tf:
        messages.error(request, "No training record found for this employee. Please contact HR.")
        return redirect('employees:my_team_training')

    if tf.status == 'REVIEWED':
        messages.error(request, "HR has already reviewed the feedback for this employee. No further changes are allowed.")
        return redirect('employees:my_team_training')

    if request.method == 'POST':
        form = TrainingFeedbackForm(request.POST, request.FILES, instance=tf)
        if form.is_valid():
            tf_obj = form.save(commit=False)
            tf_obj.submitted_by = request.user
            tf_obj.submitted_at = timezone.now()
            tf_obj.status = 'SUBMITTED'
            tf_obj.save()
            messages.success(request, f"Training feedback for {emp_user.get_full_name()} submitted successfully.")
            return redirect('employees:my_team_training')
    else:
        form = TrainingFeedbackForm(instance=tf)

    return render(request, 'employees/submit_training_feedback.html', {
        'form': form,
        'emp_user': emp_user,
        'tf': tf,
    })


@login_required
def my_team_training(request):
    """Project Manager: see all onboarding employees and their training
    feedback status, with a link to submit/edit feedback."""
    if request.user.role != 'PROJECT_MANAGER':
        messages.error(request, "You do not have permission to view this page.")
        return redirect('core:dashboard')

    from core.utils import get_manager_team
    pm_team_ids = get_manager_team(request.user).values_list('id', flat=True)

    onboarding_users = User.objects.filter(
        id__in=pm_team_ids,
        profile__status='ONBOARDING',
    ).select_related('profile', 'department', 'branch')

    training_records = {
        tf.employee_id: tf
        for tf in TrainingFeedback.objects.filter(employee__in=onboarding_users)
    }

    team_rows = []
    for emp in onboarding_users:
        team_rows.append({
            'emp': emp,
            'tf': training_records.get(emp.id),
        })

    return render(request, 'employees/my_team_training.html', {
        'team_rows': team_rows,
    })

@login_required
def download_document(request, doc_id):
    import os
    from django.http import FileResponse, Http404
    from employees.models import EmployeeDocument
    
    doc = get_object_or_404(EmployeeDocument, id=doc_id)
    
    if doc.user_id != request.user.id and not request.user.is_hr_or_admin():
        messages.error(request, 'You do not have permission to download this document.')
        return redirect('core:dashboard')
        
    if not doc.file or not os.path.exists(doc.file.path):
        raise Http404('File not found')
        
    response = FileResponse(open(doc.file.path, 'rb'))
    response['Content-Disposition'] = f'attachment; filename="{os.path.basename(doc.file.name)}"'
    return response
