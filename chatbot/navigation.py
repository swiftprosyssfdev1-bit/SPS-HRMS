"""
Single source of truth for the SPS HRMS sidebar / navigation, so the
chatbot's answers to "where can I find X" always match the real UI in
templates/core/base.html instead of drifting out of sync with it.

Each entry below was taken directly from base.html: the label, the
route name behind the link, and the exact visibility condition guarding
it. If the sidebar changes, update this file — do not hardcode
navigation text anywhere else in the chatbot.

IMPORTANT — one route can have multiple sidebar entries. base.html
re-uses the same route under different labels/sections for different
roles (e.g. leaves:approvals is "Leave Requests" for Admin/HR, "Leave
Approvals" for Manager/PM/GM/CTO, and "Team Leave Status" for a project
lead/TL with no formal manager role — three different NAV keys below,
same route). Never assume one route = one label.

`visible_to(user)` is the authoritative visibility check for an item —
copied from the exact {% if %} condition guarding that link in
base.html, not from a permission helper that happens to overlap.
"""
from django.urls import reverse, NoReverseMatch


def _url(name, *args, fallback=''):
    try:
        return reverse(name, args=args) if args else reverse(name)
    except NoReverseMatch:
        return fallback


def _dept_name(user):
    return getattr(getattr(user, 'department', None), 'name', '') or ''


def _is_qa_dept(user):
    return bool(getattr(getattr(user, 'department', None), 'is_qa', False))


# ---------------------------------------------------------------------------
# base.html's top-level {% if %}/{% elif %}/{% else %} chain has exactly
# four branches. Every sidebar link lives in exactly one of them. Naming
# these explicitly makes every visible_to below traceable back to which
# branch it came from.
# ---------------------------------------------------------------------------
_ADMIN = ('ADMIN',)
_HR = ('HR', 'SENIOR_HR')
_COO = ('COO',)
# Everything else falls into the {% else %} branch: EMPLOYEE, MANAGER,
# PROJECT_MANAGER, GENERAL_MANAGER, CTO.
_MGR3 = ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER')
_MGR_CTO = ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'CTO')


def _in_else_branch(user):
    return user.role not in (_ADMIN + _HR + _COO)


def _visible_projects(user):
    # base.html line 325: {% if not user.is_software_dept and user.project_assignments.exists %}
    # (else-branch only — this is the personal "My Projects" link)
    return _in_else_branch(user) and (not user.is_software_dept()) and user.project_assignments.exists()


def _visible_projects_management(user):
    # base.html: "Projects" (management) appears for ADMIN (line 90),
    # HR/SENIOR_HR (line 218), GENERAL_MANAGER/PROJECT_MANAGER (line 365-370,
    # with a projects_needing_setup_count badge), and MANAGER only if NOT
    # in a QA department (line 371-377). CTO never gets this link.
    if user.role in (_ADMIN + _HR + ('GENERAL_MANAGER', 'PROJECT_MANAGER')):
        return True
    if user.role == 'MANAGER':
        return not _is_qa_dept(user)
    return False


def _is_project_lead_or_tl(user):
    # Reproduces core/context_processors.py::is_project_lead_or_tl exactly —
    # that value is computed per-request in a context processor, not stored
    # on the user, so it's recomputed here the same way.
    from projects.models import Project, ProjectAssignment
    return Project.objects.filter(lead=user).exists() or ProjectAssignment.objects.filter(
        user=user, is_current=True, role_on_project__iexact='Team Leader'
    ).exists()


def _visible_my_hourly_tracker(user):
    # base.html line 331-337 (else-branch): dept name contains 'Production'
    # or 'Quality', AND role == EMPLOYEE, AND NOT a project lead/TL
    # (leads/TLs get the dashboard link instead — see below).
    dept = _dept_name(user)
    in_prod_or_qa = ('Production' in dept) or ('Quality' in dept)
    if not in_prod_or_qa or user.role != 'EMPLOYEE':
        return False
    return not _is_project_lead_or_tl(user)


def _visible_hourly_tracker_dashboard(user):
    # base.html: the "Hourly Tracker" (dashboard) link appears in FOUR
    # separate places — line 59 (ADMIN), line 136 (HR/SENIOR_HR), line 354
    # (MANAGER/PROJECT_MANAGER/GENERAL_MANAGER only — NOT CTO, despite CTO
    # sharing the "Manager" section header), and line 333 (Production/
    # Quality dept EMPLOYEE who is also a project lead/TL).
    # COO and CTO never get this link anywhere in the sidebar.
    if user.role in (_ADMIN + _HR + _MGR3):
        return True
    dept = _dept_name(user)
    in_prod_or_qa = ('Production' in dept) or ('Quality' in dept)
    return in_prod_or_qa and user.role == 'EMPLOYEE' and _is_project_lead_or_tl(user)


def _visible_my_overtime(user):
    # base.html line 295: {% if user.role != 'PROJECT_MANAGER' and user.role
    # != 'GENERAL_MANAGER' and not user.is_software_dept %} — else-branch only.
    return (
        _in_else_branch(user)
        and user.role not in ('PROJECT_MANAGER', 'GENERAL_MANAGER')
        and not user.is_software_dept()
    )


def _visible_allowance_approvals(user):
    # base.html line 392-397: inside the MANAGER/PROJECT_MANAGER/
    # GENERAL_MANAGER/CTO block, shown to everyone in it EXCEPT GENERAL_MANAGER.
    # Also shown to HR/SENIOR_HR (line 158, separate block).
    if user.role in _HR:
        return True
    return user.role in ('MANAGER', 'PROJECT_MANAGER', 'CTO')


NAV = {
    'dashboard': {
        'label': 'Dashboard', 'route': 'core:dashboard', 'section': 'top',
        'visible_to': lambda user: True,
        'what': 'your personal dashboard / homepage',
    },

    # ---- Profile: THREE variants of the same underlying page ----
    'edit_profile_admin_hr': {
        'label': 'Edit Profile', 'route': 'employees:my_profile', 'section': 'My Profile',
        'visible_to': lambda user: user.role in (_ADMIN + _HR),
        'what': 'edit your own profile details',
    },
    'my_profile': {
        'label': 'My Profile', 'route': 'employees:my_profile', 'section': 'My Profile',
        'visible_to': _in_else_branch,
        'what': 'view/edit your own profile details',
        'condition_note': 'Employee/Manager/PM/GM/CTO see this labeled "My Profile"; Admin/HR see the same page labeled "Edit Profile" instead.',
    },
    'coo_edit_profile': {
        'label': 'Edit Profile', 'route': 'employees:edit_my_profile', 'section': 'My Profile',
        'visible_to': lambda user: user.role in _COO,
        'what': "edit your own profile details",
        'condition_note': 'COO uses a DIFFERENT route (employees:edit_my_profile) than everyone else\'s profile-edit link (employees:my_profile).',
    },

    'attendance_punch': {
        'label': 'Attendance', 'route': 'attendance:punch', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'mark your daily attendance (punch in/out, breaks)',
    },
    'my_attendance': {
        'label': 'My Monthly Attendance', 'route': 'attendance:my_attendance_view', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'your own attendance history',
        'condition_note': 'Labeled "My Monthly Attendance" for Admin/HR/COO, just "Monthly Attendance" (no "My") for everyone else — same page.',
    },
    'my_overtime_permissions': {
        'label': 'My Overtime / Sunday Permissions', 'route': 'attendance:my_overtime_permissions', 'section': 'My Profile',
        'visible_to': _visible_my_overtime,
        'what': 'request OT/Sunday/Holiday work for yourself',
        'condition_note': 'Not shown to Project Manager, General Manager, or anyone in the Software department.',
    },
    'my_leaves': {
        'label': 'Permission / Leave / WFH', 'route': 'leaves:my_leaves', 'section': 'My Profile',
        'visible_to': lambda user: user.role not in _COO,
        'what': 'apply for Permission, Leave, or WFH, and see your own balance/history',
        'condition_note': 'COO does not have this link in the sidebar.',
    },
    'my_payslips': {
        'label': 'Payslips', 'route': 'payroll:my_payslips', 'section': 'My Profile',
        'visible_to': lambda user: user.role not in _COO,
        'what': 'download your own payslips',
        'condition_note': 'COO gets a different link for this — see coo_payslips (goes to the COO Dashboard payslips tab, not this route).',
    },
    'coo_payslips': {
        'label': 'My Payslips', 'route': 'core:coo_dashboard', 'section': 'My Profile',
        'visible_to': lambda user: user.role in _COO,
        'what': 'download your own payslips',
        'condition_note': 'Actual link is core:coo_dashboard?tab=payslips — a tab on the COO Dashboard, not a separate page.',
    },
    'my_letters': {
        'label': 'My Letters', 'route': 'employees:my_letters', 'section': 'My Profile',
        'visible_to': lambda user: user.role in _COO or _in_else_branch(user),
        'what': 'official letters HR has issued to you (offer, promotion, experience, relieving, etc.)',
        'condition_note': 'NOT shown to Admin or HR/Senior HR (they issue letters, this is the recipient-side page).',
    },
    'my_allowance_requests': {
        'label': 'Allowance Requests', 'route': 'payroll:my_allowance_requests', 'section': 'My Profile',
        'visible_to': _in_else_branch,
        'what': 'raise or track your own allowance requests',
        'condition_note': 'Only the Employee/Manager/PM/GM/CTO branch — not Admin, HR, or COO.',
    },
    'projects': {
        'label': 'Projects', 'route': 'projects:list', 'section': 'My Profile',
        'visible_to': _visible_projects,
        'what': 'the projects you are assigned to (Active / Upcoming / Completed) — open one to see its details',
        'condition_note': 'Personal view: visible only if you are NOT in the Software department and have at least one project assignment.',
    },
    'my_hourly_tracker': {
        'label': 'My Hourly Tracker', 'route': 'production_tracker:daily_entry', 'section': 'My Profile',
        'visible_to': _visible_my_hourly_tracker,
        'what': 'fill your own daily hourly production/QA entries, with a "My History" button on that page',
        'condition_note': (
            'Production/Quality department, role EMPLOYEE, and NOT a project lead/team leader '
            '(leads/TLs see "Hourly Tracker" — the team dashboard — instead of this).'
        ),
    },
    'hourly_tracker_dashboard': {
        'label': 'Hourly Tracker', 'route': 'production_tracker:dashboard', 'section': 'varies by role',
        'visible_to': _visible_hourly_tracker_dashboard,
        'what': (
            "see every employee under you and their daily targets/achievement — this is the page to "
            "set or change an individual employee's hourly target"
        ),
        'condition_note': (
            'Shown to: Admin, HR/Senior HR, Manager/Project Manager/General Manager (NOT CTO), and to '
            'Production/Quality department project leads or team leaders. '
            'Sidebar section: "Employees" for Admin/HR; "Manager" section for Manager/PM/GM; '
            '"My Profile" for a lead/TL. COO and CTO never get this link at all.'
        ),
    },
    'my_queries': {
        'label': 'Ask a Query', 'route': 'queries:my_queries', 'section': 'My Profile',
        'visible_to': _in_else_branch,
        'what': 'raise a query/complaint to HR, your Manager, or Admin',
        'condition_note': 'NOT shown to Admin, HR/Senior HR, or COO — they each have their own queries inbox instead (see admin_queries / hr_queries / manager_queries).',
    },
    'my_resignation': {
        'label': 'My Resignation', 'route': 'employees:my_resignation', 'section': 'My Profile',
        'visible_to': lambda user: False,
        'what': 'your submitted resignation and its status',
        'condition_note': (
            'CODE BUG, not a permission rule: base.html gates this on a template variable '
            '"has_applied_resignation" that no context processor ever sets, so this link never '
            'renders for anyone right now. The underlying page (employees:my_resignation) and the '
            'apply flow (employees:apply_resignation) both work fine if reached directly — there is '
            'just no sidebar link to them currently. Flag this to Ani/dev team; do not treat as intentional.'
        ),
    },

    # ---- Directory: two variants ----
    'employee_directory': {
        'label': 'Directory', 'route': 'employees:directory', 'section': 'Employees',
        'visible_to': lambda user: user.role in (_ADMIN + _HR + _MGR_CTO),
        'what': 'browse all colleagues, their department, and reporting manager',
        'condition_note': 'COO does NOT use this route — see coo_directory (a tab on the COO Dashboard instead).',
    },

    'team_attendance': {
        'label': 'Team Attendance', 'route': 'attendance:team_monthly_attendance', 'section': 'Employees',
        'visible_to': lambda user: user.role in (_ADMIN + _HR + _COO),
        'what': "your team's monthly attendance records",
        'condition_note': 'Admin, HR/Senior HR, and COO only — Managers/PM/GM/CTO do NOT get this link.',
    },
    'overtime_approvals': {
        'label': 'Overtime / Sunday Permissions', 'route': 'attendance:overtime_permissions', 'section': 'Employees',
        'visible_to': lambda user: user.role in (_ADMIN + _HR + ('MANAGER',)),
        'what': "review and approve/reject your team's OT/Sunday/Holiday work requests",
        'condition_note': 'Admin/HR/Manager only — NOT Project Manager, General Manager, or CTO.',
    },
    'resignations_list': {
        'label': 'Resignations', 'route': 'employees:resignation_list', 'section': 'Employees',
        'visible_to': lambda user: user.role in (_ADMIN + _HR),
        'what': "view employees' submitted resignations",
    },
    'notice_period_list': {
        'label': 'Notice Period', 'route': 'employees:notice_period_list', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'track employees currently serving notice period',
        'condition_note': 'HR/Senior HR only — Admin does not get this link.',
    },
    'training_feedback_hr': {
        'label': 'Onboarding Training', 'route': 'employees:training_feedback_list', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': "review employees' onboarding training feedback",
    },
    'training_feedback_pm': {
        'label': 'Onboarding Training', 'route': 'employees:my_team_training', 'section': 'Manager',
        'visible_to': lambda user: user.role == 'PROJECT_MANAGER',
        'what': "your team's onboarding training feedback",
        'condition_note': 'Project Manager only — different route than the HR version above, though same label.',
    },
    'team_appreciation': {
        'label': 'Team Appreciation', 'route': 'leaves:appreciation_report', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'team appreciation report',
    },
    'issue_letter': {
        'label': 'Issue Letter', 'route': 'employees:issue_letter', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'issue an official letter (offer, appointment, experience, promotion, relieving, increment) to one or more employees at once',
        'condition_note': (
            'Uses core.decorators.hr_only_required (role in HR/SENIOR_HR). '
            'Admin gets redirected with "Admin has view-only access" — Admin is view-only in this system, not blocked by role name alone.'
        ),
    },
    'wfh_directory': {
        'label': 'Work From Home', 'route': 'employees:wfh_directory', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'see everyone currently Work From Home in your branch, right now',
        'condition_note': (
            'Only HR/Senior HR get this link in the sidebar. The view itself uses '
            '@hr_or_admin_required (core.decorators), so Admin and COO can also open it by '
            'direct URL even though neither has a sidebar link to it.'
        ),
    },
    'allowances_list': {
        'label': 'Allowances', 'route': 'payroll:allowance_list', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'manage allowance types/records',
    },
    'allowance_approvals': {
        'label': 'Allowance Requests', 'route': 'payroll:allowance_request_approvals', 'section': 'Employees',
        'visible_to': _visible_allowance_approvals,
        'what': "review and approve/reject your team's allowance requests",
        'condition_note': 'HR/Senior HR, Manager, Project Manager, or CTO — NOT General Manager.',
    },

    # ---- leaves:approvals: THREE different sidebar identities, same route ----
    'leave_requests_admin_hr': {
        'label': 'Leave Requests', 'route': 'leaves:approvals', 'section': 'Employees',
        'visible_to': lambda user: user.role in (_ADMIN + _HR),
        'what': 'review leave/permission/WFH requests',
        'condition_note': 'Admin/HR call this "Leave Requests" — Manager/PM/GM/CTO see the same route as "Leave Approvals" (separate NAV key).',
    },
    'leave_approvals_manager': {
        'label': 'Leave Approvals', 'route': 'leaves:approvals', 'section': 'Manager',
        'visible_to': lambda user: user.role in _MGR_CTO,
        'what': "review and approve/reject your team's leave/permission/WFH requests",
    },
    'team_leave_status': {
        'label': 'Team Leave Status', 'route': 'leaves:approvals', 'section': 'My Profile',
        'visible_to': _is_project_lead_or_tl,
        'what': "read-only view of your project team's leave requests",
        'condition_note': 'For a project lead/TL who is NOT a formal Manager — read-only, no approve/reject action.',
    },

    'payroll_runs': {
        'label': 'Payroll Runs', 'route': 'payroll:runs', 'section': 'Employees',
        'visible_to': lambda user: user.role in (_ADMIN + _HR),
        'what': 'run payroll and view payroll history for the team/company',
        'condition_note': 'Admin/HR only — Managers do NOT get this link.',
    },
    'increments_list': {
        'label': 'Increments', 'route': 'increments:list', 'section': 'Employees',
        'visible_to': lambda user: user.role in (_ADMIN + _HR),
        'what': 'employees due for an increment/appraisal',
        'condition_note': 'Admin/HR only. Managers see a different page — "Increment Feedback" (increment_feedback_manager).',
    },
    'appraisal_calculator': {
        'label': 'Appraisal Calculator', 'route': 'increments:appraisal_calculator', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'manual appraisal-for-increment calculation reference tool',
    },
    'increments_reports': {
        'label': 'Reports', 'route': 'increments:hr_reports_inbox', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'increment reports inbox',
    },
    'increments_review_reports': {
        'label': 'Review Reports', 'route': 'increments:hr_review_reports', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'review submitted increment reports',
    },
    'increments_coo_approval': {
        'label': 'Coo Approval', 'route': 'increments:hr_coo_approval', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'increments ready for COO approval',
    },
    'increments_pending_salary': {
        'label': 'Pending Salary Updates', 'route': 'increments:hr_pending_salary_updates', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': 'approved increments still pending a salary-structure update',
    },
    'increment_feedback_manager': {
        'label': 'Increment Feedback', 'route': 'increments:manager_feedback_list', 'section': 'Manager',
        'visible_to': lambda user: user.role in _MGR_CTO,
        'what': "give feedback on your team's increment appraisals",
    },
    'projects_management': {
        'label': 'Projects', 'route': 'projects:list', 'section': 'Employees / Manager',
        'visible_to': _visible_projects_management,
        'what': 'create, manage, and review submitted updates for projects',
        'condition_note': 'Admin, HR/Senior HR, General Manager, Project Manager always; Manager only if NOT in a QA department. CTO never gets this link.',
    },

    # ---- Queries: FOUR different inboxes, four different routes ----
    'admin_queries': {
        'label': 'Employee Queries', 'route': 'queries:admin_queries', 'section': 'Employees',
        'visible_to': lambda user: user.role in _ADMIN,
        'what': "review employees' submitted queries",
        'condition_note': 'Admin-only route. HR uses a DIFFERENT route (queries:hr_queries) for its own "Employee Queries" link — same label, different page.',
    },
    'hr_queries': {
        'label': 'Employee Queries', 'route': 'queries:hr_queries', 'section': 'Employees',
        'visible_to': lambda user: user.role in _HR,
        'what': "review employees' submitted queries",
    },
    'manager_queries': {
        'label': 'Team Queries', 'route': 'queries:manager_queries', 'section': 'Manager',
        'visible_to': lambda user: user.role in _MGR_CTO,
        'what': "review your team's submitted queries",
    },

    # ---- COO: the "COO Dashboard" is really 5 separate tab links, no single generic link ----
    'coo_directory': {
        'label': 'Directory', 'route': 'core:coo_dashboard', 'section': 'COO',
        'visible_to': lambda user: user.role in _COO,
        'what': 'browse all colleagues company-wide',
        'condition_note': 'Actual link is core:coo_dashboard?tab=directory. There is no plain "COO Dashboard" sidebar link — each tab has its own labeled entry.',
    },
    'coo_manpower': {
        'label': 'Manpower by Branch', 'route': 'core:coo_dashboard', 'section': 'COO',
        'visible_to': lambda user: user.role in _COO,
        'what': 'company-wide manpower numbers by branch',
        'condition_note': 'Actual link is core:coo_dashboard?tab=manpower.',
    },
    'coo_leave_approval': {
        'label': 'Leave Approval', 'route': 'core:coo_dashboard', 'section': 'COO',
        'visible_to': lambda user: user.role in _COO,
        'what': 'approve leave requests routed to COO',
        'condition_note': 'Actual link is core:coo_dashboard?tab=leave_approval.',
    },
    'coo_increment_approval': {
        'label': 'Increment Approval', 'route': 'core:coo_dashboard', 'section': 'COO',
        'visible_to': lambda user: user.role in _COO,
        'what': 'approve increments routed to COO',
        'condition_note': 'Actual link is core:coo_dashboard?tab=increment_approval.',
    },
    'coo_reports': {
        'label': 'Reports', 'route': 'core:coo_dashboard', 'section': 'COO',
        'visible_to': lambda user: user.role in _COO,
        'what': 'company-wide reports',
        'condition_note': 'Actual link is core:coo_dashboard?tab=reports.',
    },
}


def visible_items(user):
    """Return the dict of NAV entries actually visible to this user right now."""
    out = {}
    for key, item in NAV.items():
        try:
            if item['visible_to'](user):
                out[key] = item
        except Exception:
            continue
    return out


def is_visible(user, key):
    item = NAV.get(key)
    if not item:
        return False
    try:
        return bool(item['visible_to'](user))
    except Exception:
        return False


def nav_line(key):
    """Human-readable 'Sidebar -> Page' line for a nav key, with its real URL."""
    item = NAV[key]
    url = _url(item['route'])
    return f"**{item['label']}** in the left sidebar ({url})"


# ---------------------------------------------------------------------------
# Project-specific permission helpers (used because "who can Submit Update"
# is a per-project fact, not a static sidebar fact)
# ---------------------------------------------------------------------------

def is_project_lead_of_any(user):
    """True if this user is the `lead` on at least one Project — i.e. the
    only role permitted to use Submit Update, per projects/views.py's
    `if project.lead_id != request.user.id` check."""
    try:
        return user.led_projects.exists()
    except Exception:
        return False
