"""
Rich, step-by-step response formatters for every chatbot intent.
All formatting lives here — matcher.py only does routing and DB queries.
Follows the Implementation.txt architecture: one response module, all intents use it.
"""
from django.urls import reverse, NoReverseMatch
from chatbot.permissions import get_data_scope
from chatbot import navigation as nav


def _url(name, *args, fallback=''):
    try:
        return reverse(name, args=args) if args else reverse(name)
    except NoReverseMatch:
        return fallback


# ---------------------------------------------------------------------------
# Payslip
# ---------------------------------------------------------------------------

def payslip_response(user, latest_payslip=None):
    scope = get_data_scope(user)
    my_key = 'coo_payslips' if user.role == 'COO' else 'my_payslips'
    my_url = _url(nav.NAV[my_key]['route'])
    runs_url = _url(nav.NAV['payroll_runs']['route'])

    if scope in ('HR', 'ADMIN', 'MANAGEMENT'):
        return (
            "As HR/Admin, you can manage payroll here:\n"
            f"• {nav.nav_line('payroll_runs')}\n"
            "• Click **Run Payroll** or select a specific employee to view their history\n"
            f"• To download your own payslip: {nav.nav_line(my_key)}"
        )

    if latest_payslip:
        try:
            download_url = _url('payroll:download_payslip', latest_payslip.id)
        except Exception:
            download_url = None
        try:
            run = getattr(latest_payslip, 'payroll_run', None)
            if run and getattr(run, 'period_label', None):
                month_str = run.period_label
            elif getattr(latest_payslip, 'generated_at', None):
                month_str = latest_payslip.generated_at.strftime('%B %Y')
            else:
                month_str = 'Latest'
        except Exception:
            month_str = 'Latest'
        msg = (
            f"Your latest payslip is for **{month_str}**.\n\n"
            "To download it:\n"
            f"1. Go to {nav.nav_line(my_key)}\n"
            "2. Find the month you need\n"
            "3. Click **Download PDF** — it saves to your computer\n"
        )
        if download_url:
            msg += f"\n⬇️ [Direct Download — {month_str}]({download_url})"
        return msg

    return (
        "To download your payslip:\n"
        f"1. Go to {nav.nav_line(my_key)}\n"
        "2. Find the month you need\n"
        "3. Click **Download PDF** — it saves to your computer\n\n"
        "If you have no payslip on record yet, please contact HR."
    )


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------

def apply_leave_response(user):
    scope = get_data_scope(user)
    my_key = 'my_leaves'
    approvals_key = (
        'leave_requests_admin_hr'
        if user.role in ('ADMIN', 'HR', 'SENIOR_HR')
        else 'leave_approvals_manager'
    )

    if scope in ('HR', 'ADMIN'):
        return (
            f"As HR/Admin, you can review requests from {nav.nav_line(approvals_key)}.\n"
            "• Approve or reject pending requests from there\n"
            f"• Apply for your own leave from {nav.nav_line(my_key)}"
        )
    if scope in ('TEAM', 'PROJECT', 'MANAGEMENT', 'BRANCH'):
        return (
            f"To apply for your own leave: {nav.nav_line(my_key)}\n"
            "1. Click **Apply**, fill in the dates and reason, then Submit\n"
            "2. Your manager will be notified\n\n"
            f"To approve your team's leaves: {nav.nav_line(approvals_key)}"
        )
    return (
        "To apply for leave, permission, or WFH:\n"
        f"1. Go to {nav.nav_line(my_key)}\n"
        "2. Click the **Apply** button\n"
        "3. Choose the type: **Leave**, **Permission**, or **WFH**\n"
        "4. Fill in the dates, times, and reason\n"
        "5. Click **Submit** — your manager will receive a notification automatically"
    )


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

def attendance_response(user):
    scope = get_data_scope(user)
    my_key = 'my_attendance'
    punch_key = 'attendance_punch'
    team_key = 'team_attendance'

    if scope in ('HR', 'ADMIN'):
        return (
            "As HR/Admin, you can view attendance here:\n"
            f"• {nav.nav_line(team_key)}\n"
            f"• To view your own attendance: {nav.nav_line(my_key)}\n"
            f"• To punch in/out: {nav.nav_line(punch_key)}"
        )
    if scope in ('TEAM', 'MANAGEMENT', 'BRANCH', 'TECHNICAL'):
        msg = f"To view your own attendance: {nav.nav_line(my_key)}"
        if nav.is_visible(user, team_key):
            msg += f"\n\nTo view your team's attendance: {nav.nav_line(team_key)}"
        return msg
    return (
        f"To view your attendance: {nav.nav_line(my_key)}\n"
        "You can see your daily punch-in/punch-out times and use the month filter to check older records.\n\n"
        f"To punch in/out: {nav.nav_line(punch_key)}"
    )


# ---------------------------------------------------------------------------
# Production Tracker / Hourly Tracker
# ---------------------------------------------------------------------------

def tracker_response(user):
    if nav.is_visible(user, 'hourly_tracker_dashboard'):
        return (
            f"To manage hourly tracking, use {nav.nav_line('hourly_tracker_dashboard')}.\n"
            "• See each employee's daily targets and achievement\n"
            "• Set individual employee targets from the dashboard"
        )

    if nav.is_visible(user, 'my_hourly_tracker'):
        return (
            f"To fill your Hourly Tracker: {nav.nav_line('my_hourly_tracker')}\n"
            "1. Fill in your hourly production numbers for the day\n"
            "2. Click **Save**"
        )

    return (
        "The Hourly Tracker isn't available to you from the current sidebar. "
        "If you need access, please check with your manager or HR."
    )


# ---------------------------------------------------------------------------
# Raise a Query / Helpdesk
# ---------------------------------------------------------------------------

def raise_query_response(user):
    scope = get_data_scope(user)
    my_key = 'my_queries'
    inbox_key = (
        'admin_queries' if user.role == 'ADMIN'
        else 'hr_queries' if user.role in ('HR', 'SENIOR_HR')
        else 'manager_queries'
    )

    if scope in ('HR', 'ADMIN'):
        return (
            f"You can view employee queries from {nav.nav_line(inbox_key)}.\n"
            "• Click an open query to read it and reply\n"
            f"• Your own queries: {nav.nav_line(my_key)}"
        )
    if scope in ('TEAM', 'PROJECT', 'MANAGEMENT', 'BRANCH', 'TECHNICAL'):
        msg = (
            f"To raise a query: {nav.nav_line(my_key)}\n"
            "1. Click **New Query**, fill in your question, and submit"
        )
        if nav.is_visible(user, 'manager_queries'):
            msg += f"\n\nTo view queries from your team members: {nav.nav_line('manager_queries')}"
        return msg
    return (
        f"To raise a query or complaint to HR, your Manager, or Admin: {nav.nav_line(my_key)}\n"
        "1. Click **New Query**\n"
        "2. Choose who to send it to: **HR**, **Manager**, or **Admin**\n"
        "3. Type your question or issue clearly\n"
        "4. Click **Submit** — they will be notified and will reply in the system"
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def projects_response(user):
    """Role- and permission-aware project guidance using navigation.py."""
    is_lead = nav.is_project_lead_of_any(user)

    # Management users use the Projects page, then open a project to
    # review submitted updates on that project's detail page. There is no
    # user-facing "Projects -> Approvals" sidebar destination.
    if nav.is_visible(user, 'projects_management'):
        msg = (
            f"To manage projects, open {nav.nav_line('projects_management')}.\n"
            "This lists Active / Upcoming / Completed projects; click a project to open its detail page.\n"
            "Review submitted project updates from the project's detail page."
        )
        if is_lead:
            msg += "\n" + "On any project you lead, use **Submit Update** on the project's detail page to send an update for review."
        return msg

    if not nav.is_visible(user, 'projects'):
        return (
            "The **Projects** sidebar item isn't available for you right now — "
            "it only shows up once you have at least one project assignment "
            "(and isn't shown for the Software department). "
            "If you believe you should have a project assigned, check with your manager."
        )

    msg = (
        f"Open {nav.nav_line('projects')}.\n\n"
        "There you'll see the projects you're assigned to — Active, Upcoming, and Completed — "
        "and you can click into any of them to view its details."
    )
    if is_lead:
        msg += (
            "\n\nSince you're the lead on at least one project, you'll also see a "
            "**Submit Update** option on that project's detail page to send a progress update for review."
        )
    else:
        msg += (
            "\n\nSubmitting a project update for review is only available to the project's lead. "
            "If you need a status update sent, contact your project lead."
        )
    return msg


# ---------------------------------------------------------------------------
# Employee Directory
# ---------------------------------------------------------------------------

def directory_response(user):
    if nav.is_visible(user, 'coo_directory'):
        return (
            f"Go to {nav.nav_line('coo_directory')}.\n"
            "You can browse colleagues company-wide from the Directory tab."
        )

    if nav.is_visible(user, 'employee_directory'):
        return (
            f"Go to {nav.nav_line('employee_directory')}.\n"
            "Search for a colleague by name or department to view their contact details and reporting manager."
        )

    return (
        "The Directory isn't available in your current sidebar. "
        "If you need access to employee details, please check with HR or your manager."
    )
