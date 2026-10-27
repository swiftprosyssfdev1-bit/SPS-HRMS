from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q


from core.decorators import hr_or_admin_required, hr_only_required
from core.models import User
from projects.models import Project, ProjectAssignment, ProjectSubmission


def _can_manage_project(user):
    """Only a Manager (Manager / Project Manager / General Manager / CTO)
    can update or delete a project. HR, Admin, COO, and Employees can never
    manage a project — HR/Admin/COO get full read visibility instead (see
    project_list and project_detail), but no edit/delete/assign rights."""
    return user.is_manager()


def _can_view_all_projects(user):
    """HR, Admin, and COO can see projects outside their own team, read-only.
    The list and detail pages narrow this to the branch they are working in."""
    return user.is_hr_or_admin() or user.is_coo()


def _project_branch_q(branch):
    """Which branch does a project belong to? The branch of its manager; if the
    manager has none (or there is no manager), its creator's; if neither, the
    branch of its CURRENT team. A past team member never pulls a project into
    another branch."""
    return (
        Q(manager__branch=branch)
        | Q(manager__branch__isnull=True, created_by__branch=branch)
        | Q(manager__branch__isnull=True, created_by__branch__isnull=True,
            assignments__is_current=True, assignments__user__branch=branch)
    )


def _project_in_branch(project, branch):
    """Python twin of _project_branch_q for a single project."""
    if branch is None:
        return True
    for owner in (project.manager, project.created_by):
        if owner is not None and owner.branch_id is not None:
            return owner.branch_id == branch.id
    return project.assignments.filter(is_current=True, user__branch=branch).exists()


def _project_home_branch(project):
    """The branch a project belongs to, used to stop people from another branch
    being added to it. Manager's branch, else creator's, else the single
    branch all CURRENT members share. None if it can't be decided."""
    for owner in (project.manager, project.created_by):
        if owner is not None and owner.branch_id is not None:
            return owner.branch
    branches = {}
    for a in project.assignments.select_related('user__branch'):
        if a.is_current and a.user.branch_id:
            branches[a.user.branch_id] = a.user.branch
    return next(iter(branches.values())) if len(branches) == 1 else None


def _can_manage_this_project(user, project):
    """A Manager may edit / delete / complete a project only if it is theirs:
    they created it, manage it, lead it, or are its GM. A General Manager or
    CTO may also manage any project in their own branch. (Previously ANY
    manager could change ANY project by typing its URL.)"""
    if not user.is_manager():
        return False
    if user.id in (project.created_by_id, project.manager_id, project.gm_id, project.lead_id):
        return True
    if user.role in (User.ROLE_GENERAL_MANAGER, User.ROLE_CTO):
        return _project_in_branch(project, user.branch)
    return False


def _can_set_targets(user, project):
    return user.id in (project.lead_id, project.manager_id, project.gm_id)


@login_required
def project_list(request):
    active_branch = getattr(request, 'active_branch', None)

    if _can_view_all_projects(request.user):
        projects = Project.objects.filter(status__in=['APPROVED', 'COMPLETED']).prefetch_related('assignments__user')
        if active_branch:
            projects = projects.filter(_project_branch_q(active_branch)).distinct()
    else:
        # Includes created_by and gm so a General Manager who published a project
        # (and assigned a different Project Manager to run it) still sees
        # it here — previously only assignments/lead/manager were checked,
        # which excluded the GM entirely once a PM was assigned as manager.
        projects = Project.objects.filter(status__in=['APPROVED', 'COMPLETED']).filter(
            Q(assignments__user=request.user) |
            Q(lead=request.user) |
            Q(manager=request.user) |
            Q(created_by=request.user) |
            Q(gm=request.user)
        ).distinct().prefetch_related('assignments__user')

    completed_projects, active_projects, upcoming_projects = [], [], []
    for project in projects:
        if project.phase == 'COMPLETED':
            completed_projects.append(project)
        elif project.phase == 'UPCOMING':
            upcoming_projects.append(project)
        else:
            active_projects.append(project)

    return render(request, 'projects/list.html', {
        'completed_projects': completed_projects,
        'active_projects': active_projects,
        'upcoming_projects': upcoming_projects,
    })

@login_required
def create_project(request):
    """Only Managers can create/publish projects. HR, Admin and Employees
    cannot — Admin gets view-only access to projects and their updates.
    A General Manager additionally picks which Project Manager owns the
    project before choosing the Project Leader."""
    if not request.user.is_manager():
        messages.error(request, "Only Managers can create projects.")
        return redirect('projects:list')

    is_general_manager = request.user.role == User.ROLE_GENERAL_MANAGER
    project_managers = User.objects.none()
    if is_general_manager:
        project_managers = User.objects.filter(
            role=User.ROLE_PROJECT_MANAGER, branch=request.user.branch
        ).order_by('first_name', 'username')

    if request.method == 'POST':
        name = request.POST.get('name')
        employee_ids = request.POST.getlist('employees')
        lead_id = request.POST.get('lead')
        project_manager_id = request.POST.get('project_manager')

        if not name:
            messages.error(request, 'Project name is required.')
            return redirect('projects:create')

        # A General Manager must assign a Project Manager before anything
        # else — the Project Leader can only be chosen after that.
        assigned_manager = request.user
        assigned_gm = request.user if is_general_manager else None
        
        if is_general_manager:
            valid_pm_ids = set(str(uid) for uid in project_managers.values_list('id', flat=True))
            if not project_manager_id or project_manager_id not in valid_pm_ids:
                messages.error(request, 'Select a Project Manager for this project.')
                return redirect('projects:create')
            assigned_manager = User.objects.get(id=project_manager_id)

        # For General Managers, assigning employees and a lead is optional (the PM can do it later).
        # For other Managers, it's required immediately.
        if not is_general_manager:
            if not employee_ids:
                messages.error(request, 'Select at least one employee to assign to this project.')
                return redirect('projects:create')
            if not lead_id or lead_id not in employee_ids:
                messages.error(request, 'Choose a project lead from among the assigned employees.')
                return redirect('projects:create')

        if employee_ids:
            # Safety: only role=EMPLOYEE users can ever be assigned or lead.
            valid_employee_ids = set(
                str(uid) for uid in User.objects.filter(id__in=employee_ids, role='EMPLOYEE').values_list('id', flat=True)
            )
            employee_ids = [uid for uid in employee_ids if uid in valid_employee_ids]
            if not employee_ids:
                messages.error(request, 'Select at least one valid employee.')
                return redirect('projects:create')
            if lead_id and lead_id not in valid_employee_ids:
                messages.error(request, 'The project lead must be one of the assigned employees.')
                return redirect('projects:create')

        # Projects are published directly by the Manager who creates them —
        # no separate approval step. They immediately become visible to
        # Admin and HR (view-only) as well as the assigned team.
        project = Project.objects.create(
            name=name,
            description=request.POST.get('description', ''),
            requirements_file=request.FILES.get('requirements_file'),
            start_date=request.POST.get('start_date') or None,
            end_date=request.POST.get('end_date') or None,
            created_by=request.user,
            gm=assigned_gm,
            manager=assigned_manager,
            lead_id=lead_id if lead_id else None,
            status='APPROVED',
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        if employee_ids:
            for emp_id in employee_ids:
                ProjectAssignment.objects.create(
                    project=project, user_id=emp_id, is_current=True,
                    role_on_project='Team Member', start_date=project.start_date,
                )

        messages.success(request, f'"{project.name}" has been published. Assigned employees can now see it.')
        return redirect('projects:project_detail', project_id=project.id)

    # Every project only ever draws from the Production department in
    # practice, so this form's Department dropdown (and the employees
    # it's populated from) is scoped to Production only.
    employees = User.objects.filter(
        role='EMPLOYEE', branch=request.user.branch, department__name__icontains='Production'
    ).select_related('department').order_by('first_name', 'username')
    departments = sorted({emp.department.name for emp in employees if emp.department})
    employees_data = [
        {
            'id': emp.id,
            'name': emp.get_full_name() or emp.username,
            'empId': emp.employee_id or '-',
            'dept': emp.department.name if emp.department else 'No Department',
        }
        for emp in employees
    ]
    return render(request, 'projects/create.html', {
        'departments': departments,
        'employees_json': employees_data,
        'is_general_manager': is_general_manager,
        'project_managers': project_managers,
    })

@hr_or_admin_required
def project_approvals(request):
    """Legacy approval queue — kept for compatibility but effectively idle
    now that projects are published directly by Managers."""
    pending = Project.objects.filter(status='PENDING').select_related('created_by')
    return render(request, 'projects/approvals.html', {'projects': pending})


@hr_only_required
def approve_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    project.status = 'APPROVED'
    project.reviewed_by = request.user
    project.reviewed_at = timezone.now()
    project.save()
    messages.success(request, f'"{project.name}" approved.')
    return redirect('projects:approvals')


@hr_only_required
def reject_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    project.status = 'REJECTED'
    project.reviewed_by = request.user
    project.reviewed_at = timezone.now()
    project.save()
    messages.info(request, f'"{project.name}" rejected.')
    return redirect('projects:approvals')


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    assignments = project.assignments.select_related('user')

    is_assigned = assignments.filter(user_id=request.user.id).exists()
    is_lead = project.lead_id == request.user.id
    is_project_manager = project.manager_id == request.user.id
    active_branch = getattr(request, 'active_branch', None)
    can_view = (
        (_can_view_all_projects(request.user) and _project_in_branch(project, active_branch)) or
        project.created_by_id == request.user.id or
        is_project_manager or
        is_assigned or
        project.gm_id == request.user.id
    )
    if not can_view:
        messages.error(request, "You do not have access to this project.")
        return redirect('projects:list')
    # Project-level management (edit name/description/dates, delete the
    # project outright): HR, Admin, or any Manager can do this. Employees
    # can never manage a project.
    can_manage_project = _can_manage_this_project(request.user, project)
    can_edit_team = can_manage_project
    is_creator_manager = project.created_by_id == request.user.id

    # Marking a project as successfully completed is a Manager action,
    # available while the project isn't already completed.
    can_complete_project = can_manage_project and project.status != 'COMPLETED'

    # Submission review permissions:
    # - The creating Manager can fully approve an update OR request corrections.
    # - HR can request corrections (to flag issues) but can never give final
    #   approval — that stays with the Manager who owns the project.
    # - Admin is always view-only here, same as everywhere else on projects.
    can_set_targets = _can_set_targets(request.user, project)
    can_approve_submissions = is_creator_manager
    can_request_corrections = is_creator_manager or request.user.role in ('HR', 'SENIOR_HR')

    if request.method == 'POST' and can_edit_team:
        # Server-side branch guard: employee must be in the same branch as the project.
        target_user_id = request.POST.get('user')
        target_user = get_object_or_404(User, id=target_user_id)
        project_branch = _project_home_branch(project)
        if project_branch and target_user.branch != project_branch:
            messages.error(request, 'You can only assign employees from the same branch as this project.')
            return redirect('projects:project_detail', project_id=project.id)
        if target_user.role != 'EMPLOYEE':
            messages.error(request, 'Only Employees can be assigned to a project.')
            return redirect('projects:project_detail', project_id=project.id)
        if target_user.is_software_dept():
            messages.error(request, 'Software department employees cannot be assigned to a project.')
            return redirect('projects:project_detail', project_id=project.id)
        role_on_project = request.POST.get('role_on_project', '').strip()
        start_date = request.POST.get('start_date') or None
        if not role_on_project or not start_date:
            messages.error(request, 'Role and Start Date are required to assign a team member.')
            return redirect('projects:project_detail', project_id=project.id)
        ProjectAssignment.objects.create(
            project=project,
            user=target_user,
            role_on_project=role_on_project,
            start_date=start_date,
        )
        messages.success(request, 'Team member assigned to project.')
        return redirect('projects:project_detail', project_id=project.id)

    # Only show employees from the same branch as the project, and never
    # Software department staff — they're routed separately (leave,
    # increments) and aren't meant to be assigned onto client Projects.
    project_branch = _project_home_branch(project)
    employees = User.objects.filter(role='EMPLOYEE').exclude(
        id__in=assignments.values_list('user_id', flat=True)
    ).exclude(department__name__iexact='Software')
    if project_branch:
        employees = employees.filter(branch=project_branch)
    employees = employees.select_related('department').order_by('first_name', 'username')
    submissions = project.submissions.select_related('submitted_by', 'reviewed_by')

    return render(request, 'projects/detail.html', {
        'project': project, 'assignments': assignments, 'can_set_targets': can_set_targets, 'employees': employees, 'can_edit': can_edit_team,
        'can_manage_project': can_manage_project, 'can_complete_project': can_complete_project,
        'is_lead': is_lead, 'can_approve_submissions': can_approve_submissions,
        'can_request_corrections': can_request_corrections, 'submissions': submissions,
    })


@login_required
def edit_project(request, project_id):
    """HR, Admin, or any Manager can update a project's core details.
    Employees cannot reach this view."""
    project = get_object_or_404(Project, id=project_id)
    if not _can_manage_this_project(request.user, project):
        messages.error(request, "You do not have permission to edit this project.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        name = request.POST.get('name')
        if not name:
            messages.error(request, 'Project name is required.')
            return redirect('projects:edit_project', project_id=project.id)
        project.name = name
        project.description = request.POST.get('description', '')
        project.start_date = request.POST.get('start_date') or None
        project.end_date = request.POST.get('end_date') or None
        
        lead_id = request.POST.get('lead')
        if lead_id:
            if not (lead_id.isdigit() and project.assignments.filter(user_id=lead_id, is_current=True).exists()):
                messages.error(request, 'The project lead must be a current member of the project team.')
                return redirect('projects:edit_project', project_id=project.id)
            project.lead_id = lead_id
        else:
            project.lead = None

        raw_target = (request.POST.get('daily_target') or '').strip()
        target_changed = False
        if raw_target:
            if not raw_target.isdigit():
                messages.error(request, "Daily Target must be a whole number.")
                return redirect('projects:edit_project', project_id=project.id)
            new_target = int(raw_target)
            target_changed = new_target != project.daily_target
            project.daily_target = new_target
        else:
            target_changed = project.daily_target != 0
            project.daily_target = 0

        if request.FILES.get('requirements_file'):
            project.requirements_file = request.FILES['requirements_file']
        project.save()

        if target_changed and project.daily_target:
            # Push the new target onto every currently assigned member's
            # today's row (creating one if they haven't started it yet), so
            # everyone on the project is in sync immediately rather than
            # waiting for their next save.
            from production_tracker.models import apply_project_daily_target
            updated = apply_project_daily_target(project, timezone.localdate())
            messages.success(request, f'"{project.name}" has been updated. Daily Target of {project.daily_target} applied to {updated} team member(s) for today.')
        else:
            messages.success(request, f'"{project.name}" has been updated.')
        return redirect('projects:project_detail', project_id=project.id)

    # Pass current assignments so PM can pick a lead
    current_assignments = project.assignments.filter(is_current=True).select_related('user')

    return render(request, 'projects/edit_project.html', {'project': project, 'current_assignments': current_assignments})


@login_required
def delete_project(request, project_id):
    """HR, Admin, or any Manager can delete a project outright.
    Employees cannot reach this view."""
    project = get_object_or_404(Project, id=project_id)
    if not _can_manage_this_project(request.user, project):
        messages.error(request, "You do not have permission to delete this project.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        name = project.name
        project.delete()
        messages.success(request, f'"{name}" has been deleted.')
        return redirect('projects:list')

    return render(request, 'projects/confirm_delete_project.html', {'project': project})


@login_required
def complete_project(request, project_id):
    """A Manager marks the project as successfully completed. Once
    completed, it moves into the "Successfully Completed" section on the
    projects list."""
    project = get_object_or_404(Project, id=project_id)
    if not _can_manage_this_project(request.user, project):
        messages.error(request, "Only this project's Manager can mark it as completed.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        project.status = 'COMPLETED'
        project.completed_by = request.user
        project.completed_at = timezone.now()
        project.save()
        messages.success(request, f'"{project.name}" marked as successfully completed.')
    return redirect('projects:project_detail', project_id=project.id)


@login_required
def submit_project_update(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if project.lead_id != request.user.id:
        messages.error(request, "Only the project lead can submit updates for review.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        notes = request.POST.get('notes', '')
        if not notes and not request.FILES.get('file'):
            messages.error(request, "Add notes or attach a file before submitting.")
            return redirect('projects:project_detail', project_id=project.id)
        ProjectSubmission.objects.create(
            project=project,
            submitted_by=request.user,
            notes=notes,
            file=request.FILES.get('file'),
            status='PENDING_REVIEW',
        )
        messages.success(request, 'Update submitted for review.')
    return redirect('projects:project_detail', project_id=project.id)


@login_required
def review_project_submission(request, submission_id, decision):
    """The project's creating Manager can fully approve an update or send it
    back for corrections. HR can also send updates back for corrections
    (since HR can see every project's updates) but can never give final
    approval — that stays with the owning Manager. Admin never reaches this
    view successfully; Admin is view-only on projects everywhere."""
    submission = get_object_or_404(ProjectSubmission, id=submission_id)
    project = submission.project
    user = request.user

    is_creator_manager = project.created_by_id == user.id
    is_hr = user.role in ('HR', 'SENIOR_HR')

    if not (is_creator_manager or is_hr):
        messages.error(request, "You cannot review this submission.")
        return redirect('projects:project_detail', project_id=project.id)

    if decision == 'approve' and not is_creator_manager:
        messages.error(request, "Only the project's Manager can approve an update. HR can request corrections instead.")
        return redirect('projects:project_detail', project_id=project.id)

    if request.method == 'POST':
        submission.feedback = request.POST.get('feedback', '')
        submission.reviewed_by = user
        submission.reviewed_at = timezone.now()
        submission.status = 'APPROVED' if decision == 'approve' else 'NEEDS_CORRECTION'
        submission.save()
        messages.success(
            request,
            f"Submission {'approved' if decision == 'approve' else 'sent back for corrections'}."
        )
    return redirect('projects:project_detail', project_id=project.id)


@login_required
def my_project_history(request):
    assignments = ProjectAssignment.objects.filter(user=request.user).select_related('project')
    return render(request, 'projects/my_history.html', {'assignments': assignments})


@login_required
def set_individual_target(request, project_id, user_id):
    from django.shortcuts import get_object_or_404, redirect
    from django.contrib import messages
    from projects.models import Project
    from production_tracker.models import EmployeeTargetOverride, DailyProduction
    project = get_object_or_404(Project, id=project_id)
    if not _can_set_targets(request.user, project):
        messages.error(request, 'Permission denied.')
        return redirect('projects:project_detail', project_id=project.id)
    if request.method == 'POST':
        if not project.assignments.filter(user_id=user_id).exists():
            messages.error(request, 'That employee is not on this project.')
            return redirect('projects:project_detail', project_id=project_id)
        work_type = request.POST.get('work_type', 'PRODUCTION')
        try:
            target_day = int(request.POST.get('target_day') or 0)
        except ValueError:
            messages.error(request, 'Target must be a whole number.')
            return redirect('projects:project_detail', project_id=project_id)
        effective_date = request.POST.get('effective_date')
        if target_day > 0 and effective_date:
            EmployeeTargetOverride.objects.update_or_create(
                employee_id=user_id, project=project, work_type=work_type, effective_date=effective_date,
                defaults={'target_day': target_day, 'set_by': request.user}
            )
            # Apply to today and future
            dailies = DailyProduction.objects.filter(employee_id=user_id, project=project, work_type=work_type, date__gte=effective_date)
            for d in dailies:
                d.sync_target_from_project()
                d.save(update_fields=['target_day'])
                d.recalculate()
            messages.success(request, 'Individual target set successfully.')
        elif target_day == 0 and effective_date:
            EmployeeTargetOverride.objects.filter(employee_id=user_id, project=project, work_type=work_type, effective_date=effective_date).delete()
            dailies = DailyProduction.objects.filter(employee_id=user_id, project=project, work_type=work_type, date__gte=effective_date)
            for d in dailies:
                d.sync_target_from_project()
                d.save(update_fields=['target_day'])
                d.recalculate()
            messages.success(request, 'Individual target removed for that date.')
    return redirect('projects:project_detail', project_id=project_id)


@login_required
def set_project_target(request, project_id):
    from django.shortcuts import get_object_or_404, redirect
    from django.contrib import messages
    from projects.models import Project
    project = get_object_or_404(Project, id=project_id)
    if request.method == 'POST':
        if not (project.lead_id == request.user.id or project.manager_id == request.user.id or project.gm_id == request.user.id):
            messages.error(request, 'Permission denied.')
            return redirect('projects:project_detail', project_id=project.id)
        try:
            target = int(request.POST.get('daily_target') or 0)
        except ValueError:
            messages.error(request, 'Daily Target must be a whole number.')
            return redirect('projects:project_detail', project_id=project.id)
        if target < 0:
            messages.error(request, 'Daily Target cannot be negative.')
            return redirect('projects:project_detail', project_id=project.id)
        if target != project.daily_target:
            project.daily_target = target
            project.save(update_fields=['daily_target'])
            from production_tracker.models import apply_project_daily_target
            from django.utils import timezone
            updated = apply_project_daily_target(project, timezone.localdate())
            messages.success(request, f'Project target set to {target}. Applied to {updated} members today.')
    return redirect('projects:project_detail', project_id=project.id)
