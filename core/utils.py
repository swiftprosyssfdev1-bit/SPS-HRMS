from django.db.models import Q
from core.models import User

def get_active_branch(request):
    from core.models import Branch
    user = request.user
    can_switch = user_can_switch_branch(user)
    if not can_switch:
        return getattr(user, 'branch', None)

    branch_id = request.session.get('active_branch_id')
    if branch_id and str(branch_id).isdigit():
        if user.role in ('ADMIN', 'SENIOR_HR', 'COO'):
            branch = Branch.objects.filter(id=branch_id).first()
        else:
            branch = user.accessible_branches.filter(id=branch_id).first()
        if branch:
            return branch

    if user.role in ('ADMIN', 'SENIOR_HR', 'COO'):
        return user.branch or Branch.objects.first()
    return user.branch or user.accessible_branches.first()

def user_can_switch_branch(user):
    if not user.is_authenticated:
        return False
    if user.role in ('ADMIN', 'SENIOR_HR', 'COO'):
        return True
    if user.role == 'HR':
        if user.can_access_all_branches:
            return True
        return user.accessible_branches.exclude(id=user.branch_id).exists()
    return False

def get_manager_team(manager):
    team = User.objects.filter(
        Q(department__manager=manager) |
        Q(manager=manager) |
        Q(branch__manager=manager, manager__isnull=True, department__isnull=True)
    ).exclude(id=manager.id)

    if manager.role == User.ROLE_MANAGER and manager.department_id:
        dept_team_ids = User.objects.filter(
            department=manager.department, branch=manager.branch
        ).exclude(id=manager.id).values_list('id', flat=True)
        team = team | User.objects.filter(id__in=list(dept_team_ids))

    if manager.role == User.ROLE_PROJECT_MANAGER:
        from projects.models import ProjectAssignment
        project_team_ids = ProjectAssignment.objects.filter(
            project__manager=manager,
            project__status__in=['PENDING', 'APPROVED', 'COMPLETED'],
            is_current=True,
        ).values_list('user_id', flat=True)
        team = team | User.objects.filter(id__in=project_team_ids).exclude(id=manager.id)

    if manager.is_cto():
        software_team_ids = User.objects.filter(
            department__name__icontains='software'
        ).exclude(id=manager.id).values_list('id', flat=True)
        team = team | User.objects.filter(id__in=list(software_team_ids))

    return team.distinct()


def get_gm_scope(gm):
    direct = get_manager_team(gm)
    ids = set(direct.values_list('id', flat=True))
    
    pm_ids = set(direct.filter(role=User.ROLE_PROJECT_MANAGER).values_list('id', flat=True))
    
    from projects.models import Project, ProjectAssignment
    gm_projects = Project.objects.filter(gm=gm, status__in=['PENDING', 'APPROVED', 'COMPLETED'])
    
    for proj in gm_projects:
        if proj.manager_id:
            pm_ids.add(proj.manager_id)
            ids.add(proj.manager_id)
        if proj.lead_id:
            ids.add(proj.lead_id)
            
        proj_member_ids = ProjectAssignment.objects.filter(project=proj, is_current=True).values_list('user_id', flat=True)
        ids.update(proj_member_ids)
        
    return User.objects.filter(id__in=ids)


def get_gm_pms(gm):
    direct_pms = get_manager_team(gm).filter(role=User.ROLE_PROJECT_MANAGER)
    
    from projects.models import Project
    project_pm_ids = Project.objects.filter(
        gm=gm, status__in=['PENDING', 'APPROVED', 'COMPLETED'], manager__isnull=False
    ).values_list('manager_id', flat=True)
    
    return (direct_pms | User.objects.filter(id__in=project_pm_ids).distinct()).distinct()
