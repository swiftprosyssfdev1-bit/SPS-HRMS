"""
Single source of truth for the SPS HRMS sidebar / navigation, so the
chatbot's answers to "where can I find X" always match the real UI in
templates/core/base.html instead of drifting out of sync with it.

Each entry below was taken directly from base.html: the label, the
route name behind the link, and the exact visibility condition guarding
it. If the sidebar changes, update this file — do not hardcode
navigation text anywhere else in the chatbot.

`visible_to(user)` is the authoritative visibility check for an item;
`describe(user, key)` returns a role-aware, permission-aware sentence
that can be dropped straight into a chatbot response.
"""
from django.urls import reverse, NoReverseMatch


def _url(name, *args, fallback=''):
    try:
        return reverse(name, args=args) if args else reverse(name)
    except NoReverseMatch:
        return fallback


def _dept_name(user):
    return getattr(getattr(user, 'department', None), 'name', '') or ''


# ---------------------------------------------------------------------------
# Sidebar map — key: {label, route, section, visible_to(user)->bool, what}
# visible_to mirrors the exact {% if %} condition in templates/core/base.html.
# ---------------------------------------------------------------------------

def _visible_projects(user):
    # base.html: {% if not user.is_software_dept and user.project_assignments.exists %}
    return (not user.is_software_dept()) and user.project_assignments.exists()


def _is_project_lead_or_tl(user):
    # Reproduces core/context_processors.py::is_project_lead_or_tl exactly —
    # that value is computed per-request in a context processor, not stored
    # on the user, so it's recomputed here the same way.
    from projects.models import Project, ProjectAssignment
    return Project.objects.filter(lead=user).exists() or ProjectAssignment.objects.filter(
        user=user, is_current=True, role_on_project__iexact='Team Leader'
    ).exists()


def _visible_hourly_tracker(user):
    # base.html: dept name contains 'Production' or 'Quality', AND
    # (role == EMPLOYEE OR is_project_lead_or_tl)
    dept = _dept_name(user)
    in_prod_or_qa = ('Production' in dept) or ('Quality' in dept)
    if not in_prod_or_qa:
        return False
    if user.role == 'EMPLOYEE':
        return True
    return _is_project_lead_or_tl(user)


NAV = {
    'dashboard': {
        'label': 'Dashboard', 'route': 'core:dashboard', 'section': 'top',
        'visible_to': lambda user: True,
        'what': 'your personal dashboard / homepage',
    },
    'my_profile': {
        'label': 'My Profile', 'route': 'employees:my_profile', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'your own profile details',
    },
    'attendance_punch': {
        'label': 'Punch In/Out', 'route': 'attendance:punch', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'mark your daily attendance',
    },
    'my_attendance': {
        'label': 'My Attendance', 'route': 'attendance:my_attendance_view', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'your own attendance history',
    },
    'my_leaves': {
        'label': 'My Leaves', 'route': 'leaves:my_leaves', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'apply for leave and see your leave balance/history',
    },
    'my_payslips': {
        'label': 'My Payslips', 'route': 'payroll:my_payslips', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'download your own payslips',
    },
    'my_letters': {
        'label': 'My Letters', 'route': 'employees:my_letters', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'official letters issued to you',
    },
    'my_allowance_requests': {
        'label': 'Allowance Requests', 'route': 'payroll:my_allowance_requests', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'raise or track your own allowance requests',
    },
    'projects': {
        'label': 'Projects', 'route': 'projects:list', 'section': 'My Profile',
        'visible_to': _visible_projects,
        'what': 'the projects you are assigned to (Active / Upcoming / Completed) — open one to see its details',
        'condition_note': 'visible only if you are NOT in the Software department and you have at least one project assignment',
    },
    'hourly_tracker': {
        'label': 'My Hourly Tracker / Hourly Tracker', 'route': 'production_tracker:daily_entry', 'section': 'My Profile',
        'visible_to': _visible_hourly_tracker,
        'what': 'daily hourly production/QA entries, with a "My History" button on that page',
        'condition_note': 'visible only for Production/Quality department employees or project leads/team leads',
    },
    'ask_query': {
        'label': 'Ask a Query', 'route': 'queries:my_queries', 'section': 'My Profile',
        'visible_to': lambda user: True,
        'what': 'raise a query/complaint to HR, your Manager, or Admin',
    },
    'my_resignation': {
        'label': 'Resignation', 'route': 'employees:my_resignation', 'section': 'My Profile',
        'visible_to': lambda user: getattr(user, 'has_applied_resignation', False),
        'what': 'your submitted resignation and its status',
    },
    'employee_directory': {
        'label': 'Directory', 'route': 'employees:directory', 'section': 'Employees',
        'visible_to': lambda user: user.is_manager() or user.is_hr_or_admin() or user.is_coo(),
        'what': 'browse all colleagues, their department, and reporting manager',
    },
    'leave_approvals': {
        'label': 'Leave Approvals', 'route': 'leaves:approvals', 'section': 'Manager/HR',
        'visible_to': lambda user: user.is_manager() or user.is_hr_or_admin(),
        'what': "review and approve/reject your team's leave requests",
    },
    'payroll_runs': {
        'label': 'Payroll Runs', 'route': 'payroll:runs', 'section': 'HR/Manager',
        'visible_to': lambda user: user.is_manager() or user.is_hr_or_admin(),
        'what': 'run payroll and view payroll history for the team/company',
    },
    'increments': {
        'label': 'Increments', 'route': 'increments:list', 'section': 'Manager/HR',
        'visible_to': lambda user: user.is_manager() or user.is_hr_or_admin(),
        'what': 'employees due for an increment/appraisal',
    },
    'project_approvals': {
        'label': 'Projects (management)', 'route': 'projects:list', 'section': 'Manager/HR',
        'visible_to': lambda user: user.is_manager() or user.is_hr_or_admin(),
        'what': 'create, manage, and review submitted updates for projects',
    },
    'coo_dashboard': {
        'label': 'COO Dashboard', 'route': 'core:coo_dashboard', 'section': 'COO',
        'visible_to': lambda user: user.is_coo(),
        'what': 'company-wide manpower, leave approvals, increment approvals, and reports',
    },
    'admin_queries': {
        'label': 'Queries (admin/HR inbox)', 'route': 'queries:admin_queries', 'section': 'Manager/HR',
        'visible_to': lambda user: user.is_hr_or_admin(),
        'what': "review employees' submitted queries",
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
