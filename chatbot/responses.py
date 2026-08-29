"""
Rich, step-by-step response formatters for every chatbot intent.
All formatting lives here — matcher.py only does routing and DB queries.
Follows the Implementation.txt architecture: one response module, all intents use it.
"""
from django.urls import reverse, NoReverseMatch
from chatbot.permissions import get_data_scope


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
    my_url = _url('payroll:my_payslips')
    runs_url = _url('payroll:runs')

    if scope in ('HR', 'ADMIN', 'MANAGEMENT'):
        return (
            "As HR/Admin, you can manage payroll here:\n"
            f"• **Payroll → Payroll Runs**: {runs_url}\n"
            "• Click **Run Payroll** or select a specific employee to view their history\n"
            f"• To download your own payslip: **Payroll → My Payslips**: {my_url}"
        )

    # Employee / Manager / others — show own payslip + direct download
    if latest_payslip:
        try:
            download_url = _url('payroll:download_payslip', latest_payslip.id)
        except Exception:
            download_url = None
        try:
            # Payslip has no month field; use payroll_run period or generated_at
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
            "1. Go to **Payroll → My Payslips** in the sidebar\n"
            "2. Find the month you need\n"
            "3. Click **Download PDF** — it saves to your computer\n"
        )
        if download_url:
            msg += f"\n⬇️ [Direct Download — {month_str}]({download_url})"
        return msg

    return (
        "To download your payslip:\n"
        "1. Go to **Payroll → My Payslips** in the sidebar\n"
        f"   Direct link: {my_url}\n"
        "2. Find the month you need\n"
        "3. Click **Download PDF** — it saves to your computer\n\n"
        "If you have no payslip on record yet, please contact HR."
    )


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------

def apply_leave_response(user):
    scope = get_data_scope(user)
    my_url = _url('leaves:my_leaves')
    approvals_url = _url('leaves:approvals')

    if scope in ('HR', 'ADMIN'):
        return (
            "As HR, you can:\n"
            f"• View all leave requests → **Leaves → Leave Approvals**: {approvals_url}\n"
            "• Approve or reject pending requests from there\n"
            f"• Apply for your own leave → **Leaves → My Leaves**: {my_url}"
        )
    if scope in ('TEAM', 'PROJECT', 'MANAGEMENT', 'BRANCH'):
        return (
            "To apply for your own leave:\n"
            f"1. Go to **Leaves → My Leaves**: {my_url}\n"
            "2. Click **Apply**, fill in the dates and reason, then Submit\n"
            "3. Your manager will be notified\n\n"
            f"To approve your team's leaves: **Leaves → Leave Approvals**: {approvals_url}"
        )
    # Employee / default
    return (
        "To apply for leave, permission, or WFH:\n"
        f"1. Go to **Leaves → My Leaves** in the sidebar: {my_url}\n"
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
    my_url = _url('attendance:my_attendance_view')
    punch_url = _url('attendance:punch')
    team_url = _url('attendance:team_monthly_attendance')

    if scope in ('HR', 'ADMIN'):
        return (
            "As HR, you can view attendance here:\n"
            "• Full team sheet → **Attendance → Team Monthly Register**\n"
            "• Daily punch records → **Attendance → Daily Attendance**\n"
            "• **Attendance → Reports** for downloadable reports"
        )
    if scope in ('TEAM', 'MANAGEMENT', 'BRANCH', 'TECHNICAL'):
        return (
            "To view your own attendance:\n"
            f"1. Go to **Attendance → My Attendance**: {my_url}\n\n"
            "To view your team's attendance:\n"
            f"• **Attendance → Team Monthly Register**: {team_url}"
        )
    # Employee
    return (
        "To view your attendance:\n"
        f"1. Go to **Attendance → My Attendance** in the sidebar: {my_url}\n"
        "2. You can see your daily punch-in/punch-out times\n"
        "3. Use the month filter to check older records\n\n"
        f"To punch in/out: **Attendance → Punch**: {punch_url}"
    )


# ---------------------------------------------------------------------------
# Production Tracker / Hourly Tracker
# ---------------------------------------------------------------------------

def tracker_response(user):
    scope = get_data_scope(user)
    entry_url = _url('production_tracker:daily_entry')
    dashboard_url = _url('production_tracker:dashboard')
    history_url = _url('production_tracker:my_tracker')

    if scope in ('HR', 'ADMIN', 'MANAGEMENT', 'BRANCH'):
        return (
            "As a Manager/HR, use the Production Tracker Dashboard:\n"
            f"• **Production Tracker → Dashboard**: {dashboard_url}\n"
            "• See each employee's daily targets and achievement\n"
            "• Set individual employee targets from the dashboard"
        )
    if scope in ('TEAM', 'TECHNICAL', 'PROJECT'):
        return (
            "For your own daily entry:\n"
            f"1. Go to **Production Tracker → Daily Entry**: {entry_url}\n"
            "2. Fill in your hourly production numbers and click Save\n\n"
            f"To view your team's production dashboard: **Production Tracker → Dashboard**: {dashboard_url}"
        )
    # Employee
    return (
        "To fill the Hourly Tracker:\n"
        f"1. Go to **Production Tracker → Daily Entry** in the sidebar: {entry_url}\n"
        "2. Fill in your hourly production numbers for the day\n"
        "3. Click **Save**\n\n"
        f"To view your past production history: **Production Tracker → My Tracker History**: {history_url}"
    )


# ---------------------------------------------------------------------------
# Raise a Query / Helpdesk
# ---------------------------------------------------------------------------

def raise_query_response(user):
    scope = get_data_scope(user)
    my_url = _url('queries:my_queries')
    hr_url = _url('queries:hr_queries')
    mgr_url = _url('queries:manager_queries')

    if scope in ('HR', 'ADMIN'):
        return (
            "As HR, you can view employee queries here:\n"
            f"• **Queries → HR Inbox**: {hr_url}\n"
            "• Click on any open query to read it and reply\n"
            f"• Your own queries: **Queries → My Queries**: {my_url}"
        )
    if scope in ('TEAM', 'PROJECT', 'MANAGEMENT', 'BRANCH', 'TECHNICAL'):
        return (
            "To raise a query:\n"
            f"1. Go to **Queries → My Queries**: {my_url}\n"
            "2. Click **New Query**, fill in your question, and submit\n\n"
            f"To view queries from your team members: **Queries → Manager Inbox**: {mgr_url}"
        )
    # Employee
    return (
        "To raise a query or complaint to HR or your Manager:\n"
        f"1. Go to **Queries → My Queries** in the sidebar: {my_url}\n"
        "2. Click **New Query**\n"
        "3. Choose who to send it to: **HR**, **Manager**, or **Admin**\n"
        "4. Type your question or issue clearly\n"
        "5. Click **Submit** — they will be notified and will reply in the system"
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def projects_response(user):
    scope = get_data_scope(user)
    my_url = _url('projects:my_history')
    list_url = _url('projects:list')
    approvals_url = _url('projects:approvals')

    if scope in ('TEAM', 'PROJECT', 'MANAGEMENT', 'BRANCH', 'HR', 'ADMIN'):
        return (
            "To manage projects:\n"
            f"• View all projects → **Projects → Projects List**: {list_url}\n"
            f"• Review team submissions → **Projects → Approvals**: {approvals_url}\n"
            f"• Your own project history → **Projects → My History**: {my_url}"
        )
    return (
        "To view your projects:\n"
        f"1. Go to **Projects → My History** in the sidebar: {my_url}\n"
        "2. See all projects you are currently assigned to\n"
        "3. Click a project to submit a progress update\n\n"
        f"To browse all active company projects: **Projects → Projects List**: {list_url}"
    )


# ---------------------------------------------------------------------------
# Employee Directory
# ---------------------------------------------------------------------------

def directory_response(user):
    scope = get_data_scope(user)
    url = _url('employees:directory')
    if scope in ('HR', 'ADMIN'):
        return (
            f"Go to **Employees → Directory** in the sidebar: {url}\n"
            "As HR, you can view all employee contact details, departments, and managers.\n"
            "You also have access to onboard new employees from **Employees → Onboard Employee**."
        )
    return (
        f"Go to **Employees → Directory** in the sidebar: {url}\n"
        "Search for any colleague by name or department to view their contact details and reporting manager."
    )
