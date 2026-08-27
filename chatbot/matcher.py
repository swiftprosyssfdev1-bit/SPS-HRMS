"""
Rule-based Your Assist answer engine.

Order of resolution for every incoming message:
  1. Dynamic HRMS-data intents (leave balance, today's attendance, manager,
     payslip...) — answered from the LOGGED-IN user's own records only.
     There is no way to query another employee's data through this bot.
  2. FAQ table match (question/keywords set up by HR/Admin in the admin site).
  3. Fallback: "please contact HR".

Returns a dict: {"reply": str, "source": "DATA"|"FAQ"|"UNKNOWN", "faq": FAQ|None}
"""
import re
from datetime import date

from django.urls import reverse, NoReverseMatch


def _normalize(text):
    return re.sub(r'\s+', ' ', text.strip().lower())


def _any_word(text, *phrases):
    """Word-boundary match, not substring match — so short trigger words
    like 'hi' don't accidentally match inside other words (e.g. 'this',
    'shine'). Multi-word phrases are matched as-is with boundaries at
    each end."""
    for p in phrases:
        pattern = r'\b' + re.escape(p) + r'\b'
        if re.search(pattern, text):
            return True
    return False


# ---------------------------------------------------------------------------
# Dynamic, per-user data intents. Each function takes (user, text) and
# returns a reply string, or None if this intent doesn't apply.
# ---------------------------------------------------------------------------

def _intent_leave_balance(user, text):
    if not _any_word(text, 'leave balance', 'how many leave', 'how many leaves', 'leaves left',
                      'cl balance', 'el balance', 'sick balance', 'casual leave balance',
                      'earned leave balance', 'sick leave balance', 'remaining leave',
                      'leaves remaining', 'leaves can i take', 'leaves do i have',
                      'leave do i have', 'leaves i have'):
        return None
    balance = getattr(user, 'leave_balance', None)
    if not balance:
        return "I couldn't find a leave balance record for you yet. Please contact HR."
    return (
        f"Here's your current leave balance:\n"
        f"• Casual Leave (CL): {balance.cl_balance}\n"
        f"• Earned Leave (EL): {balance.el_balance}\n"
        f"• Sick Leave: {balance.sick_balance}"
    )


def _intent_apply_leave(user, text):
    if not _any_word(text, 'apply leave', 'apply for leave', 'how to apply leave',
                      'request leave', 'take leave', 'apply permission', 'leave request',
                      'give a leave', 'give leave', 'raise a leave', 'raise leave',
                      'submit a leave', 'submit leave', 'put a leave', 'put leave',
                      'apply for permission', 'request permission', 'request for permission',
                      'where to apply for permission', 'where to request for permission',
                      'permission request', 'apply for a permission', 'how to apply permission',
                      'how to request permission', 'permission', 'work from home', 'wfh',
                      'apply wfh', 'apply for wfh', 'wfh request', 'request wfh'):
        return None
    try:
        url = reverse('leaves:my_leaves')
    except NoReverseMatch:
        url = None
    msg = "Go to My Leaves and use the Apply button — pick the leave type and dates, add a reason, then submit."
    if url:
        msg += f" You can go there directly: {url}"
    return msg


def _intent_leave_status(user, text):
    if not _any_word(text, 'leave status', 'my leave request', 'is my leave approved',
                      'leave approved', 'pending leave'):
        return None
    if _any_word(text, 'team', 'my team', "team's", 'teams'):
        # "team leave status" etc. should go to the team intent instead.
        return None
    from leaves.models import LeaveRequest
    latest = LeaveRequest.objects.filter(user=user).order_by('-applied_at').first()
    if not latest:
        return "You don't have any leave requests on record."
    label = latest.pending_approver_label
    when = latest.start_date or latest.permission_date
    return f"Your most recent request ({latest.get_request_type_display()}, {when}) status: {label}."


def _intent_team_leave_status(user, text):
    if not _any_word(text, 'team leave', "team's leave", 'my team leave', 'teams leave status',
                      'team leave status', 'team leave request'):
        return None
    from projects.models import Project, ProjectAssignment
    is_lead_or_tl = Project.objects.filter(lead=user).exists() or ProjectAssignment.objects.filter(
        user=user, is_current=True, role_on_project__iexact='Team Leader'
    ).exists()
    if not (is_lead_or_tl or user.is_manager()):
        return (
            "Team Leave Status is only visible to project leads/team leaders and "
            "managers. If you think you should have access, please raise a query to HR."
        )
    try:
        url = reverse('leaves:approvals')
    except NoReverseMatch:
        url = None
    msg = "You can see your team's leave requests and their status under Team Leave Status."
    if url:
        msg += f" Direct link: {url}"
    return msg


def _intent_who_approves(user, text):
    if not _any_word(text, 'who approves my leave', 'who is my manager', 'my reporting manager',
                      'who approves', 'my manager name'):
        return None
    if user.is_software_dept():
        return "As part of the Software department, your leave/permission requests are routed to the CTO."
    manager = user.manager
    if manager:
        return f"Your reporting manager is {manager.get_full_name() or manager.username}."
    if user.department and user.department.manager:
        return f"Your department manager is {user.department.manager.get_full_name() or user.department.manager.username}."
    return "I couldn't find a manager assigned to you in the system. Please check with HR."


def _intent_leaves_taken(user, text):
    if not _any_word(text, 'did i take a leave', 'did i take leave', 'have i taken leave',
                      'leaves taken', 'leave taken', 'how many leaves have i taken',
                      'how many leaves i took', 'leaves used', 'leave used', 'used leave',
                      'leaves i have taken', 'leave history'):
        return None
    from leaves.models import LeaveRequest
    today = date.today()
    qs = LeaveRequest.objects.filter(
        user=user, request_type='LEAVE', status='APPROVED', start_date__year=today.year
    )
    if not qs.exists():
        return f"You haven't taken any approved leave so far in {today.year}."
    total_days = 0
    by_type = {}
    for lr in qs:
        days = (lr.end_date - lr.start_date).days + 1 if lr.start_date and lr.end_date else 0
        total_days += days
        by_type[lr.get_leave_type_display()] = by_type.get(lr.get_leave_type_display(), 0) + days
    breakdown = ", ".join(f"{k}: {v}" for k, v in by_type.items())
    return f"In {today.year}, you've taken {total_days} day(s) of approved leave so far ({breakdown})."


def _intent_increment_status(user, text):
    if not _any_word(text, 'my increment', 'increment status', 'when is my increment',
                      'increment history', 'my last increment', 'did i get an increment',
                      'increment percentage'):
        return None
    from increments.models import IncrementRequest
    latest = IncrementRequest.objects.filter(user=user).order_by('-created_at').first()
    if not latest:
        return "I couldn't find any increment request on record for you yet. Please contact HR."
    status_label = latest.get_status_display()
    if latest.status == 'APPLIED':
        return (
            f"Your most recent increment: Basic went from Rs. {latest.current_basic} to "
            f"Rs. {latest.requested_basic} (+{latest.increment_percent}%), effective {latest.effective_date}."
        )
    return f"Your most recent increment request is currently: {status_label}."


def _intent_attendance_today(user, text):
    if not _any_word(text, "today's attendance", 'attendance today', 'did i punch in',
                      'have i punched in', 'my punch', 'checked in today', 'am i punched in'):
        return None
    from attendance.models import AttendanceRecord
    rec = AttendanceRecord.objects.filter(user=user, date=date.today()).first()
    if not rec or not rec.in_time:
        return "You haven't punched in today yet."
    if rec.out_time:
        return f"Today you punched in at {rec.in_time.strftime('%I:%M %p')} and out at {rec.out_time.strftime('%I:%M %p')} ({rec.total_hours} hrs)."
    return f"You punched in today at {rec.in_time.strftime('%I:%M %p')} and haven't punched out yet."


def _intent_today_production(user, text):
    if not _any_word(text, "today's production", 'today production', 'my achieved', 'did i achieve',
                      'todays achievement', "today's achievement", 'production today', 'my output today'):
        return None
    from production_tracker.models import DailyProduction
    rec = DailyProduction.objects.filter(employee=user, date=date.today()).select_related('project').first()
    if not rec:
        return "No production entry found for you today yet."
    return (
        f"Today's production ({rec.project.name if rec.project else 'no project set'}): "
        f"Target {rec.actual_target or rec.target_day}, Achieved {rec.achieved}, "
        f"Efficiency {rec.efficiency_percentage}%."
    )


def _intent_resignation_status(user, text):
    if not _any_word(text, 'my resignation', 'resignation status', 'notice period', 'my exit date',
                      'when do i leave', 'is my resignation approved'):
        return None
    from employees.models import ResignationRequest
    latest = ResignationRequest.objects.filter(user=user).order_by('-submitted_at').first()
    if not latest:
        return "You don't have a resignation request on record."
    status_label = latest.get_status_display()
    if latest.status == 'ACCEPTED' and latest.exit_date:
        return f"Your resignation is approved. Notice period: {latest.notice_period_days} day(s), exit date {latest.exit_date}."
    return f"Your resignation request status: {status_label}."


def _intent_query_status(user, text):
    if not _any_word(text, 'my query', 'my queries', 'query status', 'is my query resolved',
                      'did hr reply', 'query reply'):
        return None
    from queries.models import EmployeeQuery
    latest = EmployeeQuery.objects.filter(user=user).order_by('-created_at').first()
    if not latest:
        return "You haven't raised any queries yet."
    status_label = latest.get_status_display()
    replied = "Yes, someone has replied." if latest.recipient_has_replied else "No reply yet."
    return f"Your most recent query (to {latest.get_recipient_role_display()}) is {status_label}. {replied}"


def _intent_overtime_status(user, text):
    if not _any_word(text, 'my overtime', 'overtime permission', 'sunday permission',
                      'do i have overtime', 'overtime approved'):
        return None
    from attendance.models import OvertimePermission
    latest = OvertimePermission.objects.filter(employee=user).order_by('-date').first()
    if not latest:
        return "You don't have any overtime/Sunday work permission on record."
    return (
        f"Your most recent overtime/Sunday permission: {latest.get_permission_type_display()} "
        f"on {latest.date}, authorized by {latest.authorized_by.get_full_name() if latest.authorized_by else 'N/A'}."
    )


def _intent_wfh_status(user, text):
    if not _any_word(text, 'my wfh', 'work from home status', 'is my wfh approved', 'wfh request status'):
        return None
    from leaves.models import LeaveRequest
    latest = LeaveRequest.objects.filter(user=user, request_type='WFH').order_by('-applied_at').first()
    if not latest:
        return "You don't have any Work From Home requests on record."
    return f"Your most recent WFH request ({latest.start_date} to {latest.end_date}) status: {latest.pending_approver_label}."


def _intent_my_department(user, text):
    if not _any_word(text, 'my department', 'which department', 'what department am i in'):
        return None
    if not user.department:
        return "I couldn't find a department assigned to you. Please contact HR."
    mgr = user.department.manager
    msg = f"You're in the {user.department.name} department."
    if mgr:
        msg += f" Department manager: {mgr.get_full_name() or mgr.username}."
    return msg


def _intent_pending_leave_approvals_count(user, text):
    if not _any_word(text, 'how many pending leave', 'pending leaves count', 'how many leaves to approve',
                      'how many leave requests', 'leaves waiting for approval'):
        return None
    if not (user.is_hr_or_admin() or user.is_manager() or user.is_coo()):
        return "This is only available to Managers, HR, or the COO."
    from leaves.models import LeaveRequest
    if user.is_hr_or_admin():
        count = LeaveRequest.objects.filter(status='PENDING_HR').count()
    elif user.is_coo() or user.role in ('CTO', 'GENERAL_MANAGER'):
        count = LeaveRequest.objects.filter(status='PENDING_COO').count()
    else:
        pending = LeaveRequest.objects.filter(status='PENDING_MANAGER')
        count = sum(1 for r in pending if r.get_manager() and r.get_manager().id == user.id)
    return f"There are {count} leave request(s) currently pending your approval."


def _intent_pending_increments_count(user, text):
    if not _any_word(text, 'how many pending increment', 'pending increments count',
                      'increment requests pending', 'how many increments to review'):
        return None
    if not (user.is_hr_or_admin() or user.is_coo()):
        return "This is only available to HR or the COO."
    from increments.models import IncrementRequest
    if user.is_coo():
        count = IncrementRequest.objects.filter(status='PENDING_COO').count()
    else:
        count = IncrementRequest.objects.exclude(status__in=['APPLIED', 'REJECTED']).count()
    return f"There are {count} increment request(s) currently pending review."


def _intent_employee_headcount(user, text):
    if not _any_word(text, 'how many employees', 'total employees', 'headcount', 'total staff',
                      'number of employees'):
        return None
    if not (user.is_hr_or_admin() or user.is_manager() or user.is_coo()):
        return "This is only available to Managers, HR, or the COO."
    qs = _role_data_scope(user)
    if user.role in ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'CTO'):
        qs = qs.exclude(id=user.id)
    return f"There are currently {qs.count()} active employee(s) in your permitted scope."


def _intent_present_today_count(user, text):
    if not _any_word(text, 'how many present today', 'how many people are present', 'attendance count today',
                      'how many punched in today', 'today attendance count'):
        return None
    if not (user.is_hr_or_admin() or user.is_manager() or user.is_coo()):
        return "This is only available to Managers, HR, or the COO."
    from attendance.models import AttendanceRecord
    ids = _role_data_scope(user).values_list('id', flat=True)
    count = AttendanceRecord.objects.filter(date=date.today(), user_id__in=ids, in_time__isnull=False).count()
    return f"{count} employee(s) in your permitted scope have punched in today so far."


def _intent_salary_allowances(user, text):
    if not _any_word(text, 'allowance', 'hra', 'conveyance', 'salary structure', 'my salary',
                      'basic pay', 'ctc', 'bonus amount', 'bonus', 'my bonus', 'how much bonus',
                      'gross salary', 'my gross'):
        return None
    structure = getattr(user, 'salary_structure', None)
    try:
        request_url = reverse('payroll:my_allowance_requests')
    except NoReverseMatch:
        request_url = None

    if not structure:
        msg = "I couldn't find a salary structure on record for you. Please contact HR."
    else:
        msg = (
            f"Here's your current salary structure:\n"
            f"• Basic: {structure.basic}\n"
            f"• HRA: {structure.hra}\n"
            f"• Conveyance: {structure.conveyance}\n"
            f"• Other Allowances: {structure.other_allowances}\n"
            f"• Bonus: {structure.bonus}\n"
            f"• Gross: {structure.gross}"
        )
    if request_url:
        msg += f"\n\nTo request a change to your allowances, visit {request_url} and submit a request there."
    return msg


def _intent_my_target(user, text):
    if not _any_word(text, 'daily target', 'my target', "today's target", 'target for today',
                      'production target', 'what is my target'):
        return None
    from projects.models import ProjectAssignment
    assignments = ProjectAssignment.objects.filter(user=user, is_current=True).select_related('project')
    targeted = [a.project for a in assignments if a.project and a.project.daily_target]
    if not targeted:
        # No project target configured — let this fall through to the
        # normal fallback flow (forward to HR / denied) rather than
        # guessing, since the chatbot genuinely has no answer here.
        return None
    if len(targeted) == 1:
        p = targeted[0]
        return f"Your Daily Target on \"{p.name}\" is {p.daily_target}. This is the same target set for every team member on that project."
    lines = "\n".join(f"• {p.name}: {p.daily_target}" for p in targeted)
    return f"You're on multiple projects with a Daily Target set:\n{lines}"


def _intent_my_projects(user, text):
    if not _any_word(text, 'how many project', 'how many projects', 'my projects', 'my project',
                      'projects assigned', 'project count', 'which projects', 'what projects'):
        return None
    from projects.models import ProjectAssignment
    assignments = ProjectAssignment.objects.filter(user=user, is_current=True).select_related('project')
    count = assignments.count()
    if count == 0:
        return "You're not currently assigned to any active project."
    names = ", ".join(a.project.name for a in assignments[:10])
    msg = f"You're currently on {count} project(s): {names}."
    try:
        url = reverse('projects:my_history')
    except NoReverseMatch:
        url = None
    if url:
        msg += f" Full history: {url}"
    return msg


def _intent_payslip(user, text):
    if not _any_word(text, 'payslip', 'pay slip', 'salary slip', 'download payslip'):
        return None
    try:
        url = reverse('payroll:my_payslips')
    except NoReverseMatch:
        url = None
    msg = "You can view and download your payslips from My Payslips."
    if url:
        msg += f" Direct link: {url}"
    return msg


def _intent_greeting(user, text):
    if not _any_word(text, 'hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening') \
       and text not in ('doubt', 'help', 'query', 'question'):
        return None
    return (
        "Hi! I can help with:\n"
        "• Leave balance / status / taken (e.g. \"what's my leave balance\")\n"
        "• Attendance & production (e.g. \"did I punch in today\", \"today's production\")\n"
        "• Payslip, salary, allowances & bonus\n"
        "• Increment status\n"
        "• Overtime/Sunday & WFH request status\n"
        "• Resignation status\n"
        "• Your queries to HR/Manager\n"
        "Just type your question and I'll try to help!"
    )


def _intent_logout(user, text):
    if not _any_word(text, 'logout', 'log out', 'sign out', 'signout'):
        return None
    try:
        url = reverse('core:logout')
    except NoReverseMatch:
        url = None
    msg = "Click Logout at the bottom of the sidebar to sign out."
    if url:
        msg += f" Direct link: {url}"
    return msg


# ---------------------------------------------------------------------------
# Generic site-navigation catch-all. Rather than one hand-written function
# per page, this is a table of every self-service page (name -> keywords ->
# url name) so the bot can point to ANY page in the app without a dedicated
# intent function for each one. Only parameter-less URLs are listed (pages
# that don't need an id in the path), since the bot has no way to guess
# which specific project/employee/etc. id the user means.
#
# Add a new page here any time a new self-service feature/button is added,
# and the bot will immediately be able to point to it.
# ---------------------------------------------------------------------------
HR_OR_ADMIN = lambda u: u.is_hr_or_admin()
MANAGER_TIER = lambda u: u.is_manager() or u.is_hr_or_admin() or u.is_coo()
COO_ONLY = lambda u: u.is_coo()

NAV_PAGES = [
    # (keywords to match, url name, human label used in the reply)
    (['dashboard', 'home page'], 'core:dashboard', 'Dashboard'),
    (['edit profile', 'edit my profile', 'update profile', 'update my profile', 'my details',
      'update my details', 'change my details', 'update personal details', 'change personal details',
      'change my info', 'update my info', 'change my personal details', 'edit personal details',
      'update personal information', 'change personal information'], 'employees:edit_my_profile', 'Edit My Profile'),
    (['my profile', 'view profile', 'personal details', 'personal information'], 'employees:my_profile', 'My Profile'),
    (['resign', 'resignation', 'quit', 'notice period'], 'employees:apply_resignation', 'Apply for Resignation'),
    (['my resignation'], 'employees:my_resignation', 'My Resignation'),
    (['employee directory', 'staff directory', 'colleague list', 'team directory',
      'see employees', 'view employees', 'list of employees'], 'employees:directory', 'Employee Directory'),
    (['wfh directory', 'work from home list'], 'employees:wfh_directory', 'WFH Directory'),
    (['my department'], 'employees:my_department', 'My Department'),
    (['my appraisal', 'appraisal history', 'performance appraisal'], 'employees:my_appraisals', 'My Appraisals'),
    (['punch in', 'punch out', 'mark attendance', 'put attendance', 'give attendance',
      'submit attendance', 'take attendance', 'record attendance', 'log attendance',
      'where to punch', 'how to punch'], 'attendance:punch', 'Attendance Punch'),
    (['attendance archive', 'monthly attendance', 'monthly attendance archive'], 'attendance:my_monthly_attendance_archive', 'My Monthly Attendance Archive'),
    (['my attendance', 'attendance history', 'weekly attendance', 'yearly attendance',
      'see attendance', 'view attendance', 'where to see attendance', 'check attendance'], 'attendance:my_attendance_view', 'My Attendance'),
    (['overtime', 'sunday permission', 'my overtime'], 'attendance:my_overtime_permissions', 'My Overtime / Sunday Permissions'),
    (['apply leave', 'apply for leave', 'my leaves', 'leave application', 'apply for permission',
      'request permission', 'permission request', 'where to apply for permission',
      'where to request for permission'], 'leaves:my_leaves', 'Permission / Leave'),
    (['leave notification'], 'leaves:notifications', 'Leave Notifications'),
    (['my payslip', 'payslip', 'pay slip', 'salary slip'], 'payroll:my_payslips', 'Payslips'),
    (['allowance request', 'request allowance'], 'payroll:my_allowance_requests', 'Allowance Requests'),
    (['my project', 'project history'], 'projects:my_history', 'My Project History'),
    (['project list', 'all projects', 'where to see projects', 'view projects', 'see projects',
      'projects page', 'show projects', 'where are the projects', 'where to check project',
      'check project'], 'projects:list', 'Projects'),
    (['daily entry', 'production entry', 'hourly tracker', 'daily tracker'], 'production_tracker:daily_entry', 'My Hourly Tracker'),
    (['my tracker history', 'production history'], 'production_tracker:my_tracker', 'My Tracker History'),
    (['raise a query', 'my queries', 'ask a query', 'contact hr'], 'queries:my_queries', 'Ask a Query'),
    (['team leave status', 'leave approvals', 'view leave approvals', 'where to see leave approvals',
      'pending leave approvals', 'approve leave', 'approve leaves'], 'leaves:approvals', 'Team Leave Status', None, MANAGER_TIER),
    (['team queries'], 'queries:manager_queries', 'Team Queries', None, MANAGER_TIER),
    (['team attendance', 'monthly register'], 'attendance:team_monthly_attendance', 'Team Monthly Attendance', None, HR_OR_ADMIN),

    # ---- HR / Admin pages ----
    (['attendance reports'], 'attendance:reports', 'Attendance Reports', None, HR_OR_ADMIN),
    (['who is active today', 'active today', 'active employees today'], 'attendance:active_attendance_today', 'Active Today', None, HR_OR_ADMIN),
    (['overtime permissions', 'manage overtime', 'grant overtime'], 'attendance:overtime_permissions', 'Overtime Permissions', None, MANAGER_TIER),
    (['resignation requests', 'resignation list', 'pending resignations'], 'employees:resignation_list', 'Resignation Requests', None, HR_OR_ADMIN),
    (['onboard employee', 'add employee', 'new employee', 'create employee'], 'employees:onboard', 'Onboard Employee', None, HR_OR_ADMIN),
    (['onboard hr', 'add hr', 'new hr user'], 'employees:onboard_hr', 'Onboard HR', None, HR_OR_ADMIN),
    (['onboarding training', 'training feedback', 'new joiner training'], 'employees:training_feedback_list', 'Onboarding Training', None, HR_OR_ADMIN),
    (['my team training', 'team onboarding training'], 'employees:my_team_training', 'Onboarding Training (My Team)', None, MANAGER_TIER),
    (['appraisal dashboard'], 'employees:appraisal_dashboard', 'Appraisal Dashboard', None, MANAGER_TIER),
    (['export appraisal', 'download appraisals'], 'employees:appraisal_export', 'Export Appraisals', None, MANAGER_TIER),
    (['appraisal calculator'], 'increments:appraisal_calculator', 'Appraisal Calculator', None, HR_OR_ADMIN),
    (['increment list', 'increment requests', 'all increments'], 'increments:list', 'Increments', None, HR_OR_ADMIN),
    (['new increment', 'create increment', 'raise an increment'], 'increments:create', 'New Increment Request', None, HR_OR_ADMIN),
    (['my feedback requests', 'increment feedback requests'], 'increments:manager_feedback_list', 'My Feedback Requests', None, MANAGER_TIER),
    (['report inbox', 'manager report inbox'], 'increments:manager_report_inbox', 'Report Inbox', None, MANAGER_TIER),
    (['hr review reports'], 'increments:hr_review_reports', 'HR Review Reports', None, HR_OR_ADMIN),
    (['hr reports inbox'], 'increments:hr_reports_inbox', 'HR Reports Inbox', None, HR_OR_ADMIN),
    (['hr coo approval', 'coo approval queue'], 'increments:hr_coo_approval', 'HR COO Approval', None, HR_OR_ADMIN),
    (['pending salary updates', 'apply increment'], 'increments:hr_pending_salary_updates', 'Pending Salary Updates', None, HR_OR_ADMIN),
    (['hr feedback inbox'], 'increments:hr_feedback_inbox', 'HR Feedback Inbox', None, HR_OR_ADMIN),
    (['payroll runs', 'run payroll'], 'payroll:runs', 'Payroll Runs', None, HR_OR_ADMIN),
    (['salary structures', 'edit salary structure'], 'payroll:salary_structures', 'Salary Structures', None, HR_OR_ADMIN),
    (['allowance list', 'manage allowances'], 'payroll:allowance_list', 'Allowances', None, HR_OR_ADMIN),
    (['allowance approvals', 'allowance request approvals'], 'payroll:allowance_request_approvals', 'Allowance Request Approvals', None, MANAGER_TIER),
    (['tracker dashboard', 'production dashboard', 'set target', 'how to set target', 'set daily target',
      'set team target', 'change target', 'update target', 'assign target'], 'production_tracker:dashboard', 'Tracker Dashboard', None, MANAGER_TIER),
    (['project approvals', 'pending projects'], 'projects:approvals', 'Project Approvals', None, HR_OR_ADMIN),
    (['create project', 'new project'], 'projects:create', 'Create Project', None, MANAGER_TIER),
    (['hr queries', 'queries to hr'], 'queries:hr_queries', 'HR Queries', None, HR_OR_ADMIN),
    (['admin queries', 'queries to admin'], 'queries:admin_queries', 'Admin Queries', None, HR_OR_ADMIN),

    # ---- COO / CTO pages ----
    (['coo dashboard'], 'core:coo_dashboard', 'COO Dashboard', None, COO_ONLY),
    (['leave approval', 'approve leave', 'coo leave approval', 'where to see leave approval',
      'leave approvals coo'], 'core:coo_dashboard', 'COO Dashboard — Leave Approval tab', 'tab=leave_approval', COO_ONLY),
    (['increment approval', 'approve increment', 'coo increment approval',
      'where to see increment approval', 'increment approvals coo'], 'core:coo_dashboard',
     'COO Dashboard — Increment Approval tab', 'tab=increment_approval', COO_ONLY),
    (['manpower', 'manpower report', 'where to see manpower', 'headcount report',
      'branch manpower'], 'core:coo_dashboard', 'COO Dashboard — Manpower tab', 'tab=manpower', COO_ONLY),
    (['reports', 'download reports', 'where to see reports', 'coo reports',
      'summary reports'], 'core:coo_dashboard', 'COO Dashboard — Reports tab', 'tab=reports', COO_ONLY),
]

# Bare single-word queries — deliberately small and unambiguous, tried only
# after NAV_PAGES so they never shadow a more specific phrase there.
NAV_BARE_FALLBACK = [
    (['attendance'], 'attendance:my_attendance_view', 'My Attendance'),
    (['projects', 'project'], 'projects:list', 'Projects'),
]


def _intent_directory_bare(user, text):
    """Bare 'directory' (no qualifying word like 'employee'/'staff' in front)
    doesn't match any NAV_PAGES phrase, so it fell through to the fallback
    reply. Route it by role instead: COO has their own leadership Directory
    tab on the COO dashboard; HR/Admin have the full employee directory.
    Other roles don't have a directory page, so leave this unmatched and let
    it fall through to FAQ/fallback as before."""
    if not _any_word(text, 'directory'):
        return None
    if user.is_coo():
        try:
            url = reverse('core:coo_dashboard')
        except NoReverseMatch:
            return None
        return f"You can find that under COO Dashboard — Directory tab. Direct link: {url}?tab=directory"
    if user.is_hr_or_admin():
        try:
            url = reverse('employees:directory')
        except NoReverseMatch:
            return None
        return f"You can find that under Employee Directory. Direct link: {url}"
    return None


def _intent_navigation(user, text):
    """Catch-all: 'where is X', 'how do I get to X', 'open X' for any
    self-service page listed in NAV_PAGES. Runs after all the more specific
    data-driven intents, so those still take priority when they apply.
    Entries with a role_check only match for users who pass it — for those,
    an employee (or anyone else without access) gets a plain denial instead
    of a link to a page they can't open."""
    for entry in NAV_PAGES:
        keywords = entry[0]
        url_name = entry[1]
        label = entry[2]
        query_suffix = entry[3] if len(entry) > 3 else None
        role_check = entry[4] if len(entry) > 4 else None
        if _any_word(text, *keywords):
            if role_check and not role_check(user):
                return "That page isn't available for your role. Please contact your Manager or HR if you think you should have access."
            try:
                url = reverse(url_name)
            except NoReverseMatch:
                continue
            if query_suffix:
                url = f"{url}?{query_suffix}"
            return f"You can find that under {label}. Direct link: {url}"

    # Bare single-word fallback — checked only if nothing more specific above
    # matched, so a generic word like "attendance" never shadows a more
    # specific phrase like "attendance reports" or "team attendance".
    for keywords, url_name, label in NAV_BARE_FALLBACK:
        if _any_word(text, *keywords):
            try:
                url = reverse(url_name)
            except NoReverseMatch:
                continue
            return f"You can find that under {label}. Direct link: {url}"
    return None


def _intent_raise_query(user, text):
    if not _any_word(text, 'raise a query', 'ask a query', 'query for manager', 'query to manager',
                      'query for hr', 'query to hr', 'contact manager', 'message my manager',
                      'raise query', 'ask query'):
        return None
    try:
        url = reverse('queries:my_queries')
    except NoReverseMatch:
        url = None
    msg = "To send a query to your manager or HR, go to My Queries, pick the recipient, and submit your message."
    if url:
        msg += f" Direct link: {url}"
    return msg


def _intent_worked_sunday(user, text):
    if not _any_word(text, 'sunday'):
        return None
    if not _any_word(text, 'work', 'worked', 'attendance', 'present', 'punch', 'punched'):
        return None
    from attendance.models import AttendanceRecord
    from datetime import timedelta
    today = date.today()
    # Most recent Sunday on or before today (weekday(): Monday=0 ... Sunday=6)
    days_since_sunday = (today.weekday() - 6) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    rec = AttendanceRecord.objects.filter(user=user, date=last_sunday).first()
    when = last_sunday.strftime('%d %b %Y')
    if not rec or not rec.in_time:
        return f"No, you don't have a punch-in recorded for the most recent Sunday ({when})."
    if rec.out_time:
        return (
            f"Yes — on {when} (Sunday) you punched in at {rec.in_time.strftime('%I:%M %p')} "
            f"and out at {rec.out_time.strftime('%I:%M %p')} ({rec.total_hours} hrs)."
        )
    return f"Yes — on {when} (Sunday) you punched in at {rec.in_time.strftime('%I:%M %p')} and haven't punched out yet."


def _intent_attendance_month(user, text):
    has_present_word = _any_word(text, 'present', 'attended', 'worked')
    has_month_word = _any_word(text, 'month', 'this month')
    has_days_word = _any_word(text, 'days', 'day')
    if not (has_present_word and has_month_word and has_days_word):
        return None
    from attendance.models import AttendanceRecord
    today = date.today()
    records = AttendanceRecord.objects.filter(
        user=user, date__year=today.year, date__month=today.month, in_time__isnull=False
    )
    count = records.count()
    return f"You've been present {count} day(s) so far this month ({today.strftime('%B %Y')})."


def _intent_appraisal_calculator_howto(user, text):
    if not (_any_word(text, 'appraisal calculator') and
            _any_word(text, 'how', 'what', 'explain', 'work', 'works', 'use')):
        return None
    if not user.is_hr_or_admin():
        return None
    try:
        url = reverse('increments:appraisal_calculator')
    except NoReverseMatch:
        url = None
    msg = (
        "Appraisal Calculator builds a 12-month performance appraisal for one employee:\n"
        "1. Pick the employee and a start month/year — that's the FIRST month of their "
        "12-month appraisal cycle (the tool auto-fills the other 11 months and the end date).\n"
        "2. It pulls Production/QA targets vs. achieved and Efficiency automatically from "
        "their monthly records, and saves it as a DRAFT appraisal.\n"
        "3. On the preview screen, fill in the manual scores (Quality, Attrition, "
        "Leadership, Process) — these aren't auto-calculated.\n"
        "4. Export to Excel for the final report, or update the manual scores anytime before finalizing."
    )
    if url:
        msg += f"\nDirect link: {url}"
    return msg


def _intent_onboarding_training_howto(user, text):
    if not (_any_word(text, 'onboarding training', 'training feedback', 'new joiner training') and
            _any_word(text, 'how', 'what', 'explain', 'work', 'works', 'use')):
        return None
    if user.is_hr_or_admin():
        url_name, label = 'employees:training_feedback_list', 'Onboarding Training'
    elif user.is_manager() or user.is_coo():
        url_name, label = 'employees:my_team_training', 'Onboarding Training (My Team)'
    else:
        return None
    try:
        url = reverse(url_name)
    except NoReverseMatch:
        url = None
    msg = (
        "Onboarding Training tracks feedback for new joiners during their onboarding period: "
        "a manager submits training feedback for each new team member, then HR reviews it "
        "under this page. Anything pending shows up as a badge count in the sidebar."
    )
    if url:
        msg += f"\nDirect link ({label}): {url}"
    return msg



# ---------------------------------------------------------------------------
# Extended dynamic role-aware data intents.
# These intentionally query the live HRMS tables instead of returning static
# FAQ text.  Every team/company query is scoped before any rows are exposed.
# ---------------------------------------------------------------------------

def _display_name(u):
    return u.get_full_name() or u.username


def _role_data_scope(user):
    """Return the User queryset this role may inspect for aggregate/team data.

    The chatbot never accepts an employee name/id from the message and then
    blindly queries it.  Scope is derived only from the authenticated user.
    """
    from core.models import User
    qs = User.objects.filter(is_active=True)

    if user.role == User.ROLE_ADMIN:
        return qs
    if user.role == User.ROLE_COO:
        return qs
    if user.role in (User.ROLE_HR, User.ROLE_SENIOR_HR):
        if getattr(user, 'can_access_all_branches', False):
            return qs
        branches = user.accessible_branches.all()
        if branches.exists():
            return qs.filter(branch__in=branches)
        return qs.filter(branch=user.branch) if user.branch_id else qs.none()
    if user.role == User.ROLE_CTO:
        return qs.filter(department__name__iexact='Software')
    if user.role == User.ROLE_GENERAL_MANAGER:
        if user.branch_id:
            return qs.filter(branch_id=user.branch_id)
        if user.department_id:
            return qs.filter(department_id=user.department_id)
        return qs.filter(id=user.id)
    if user.role in (User.ROLE_MANAGER, User.ROLE_PROJECT_MANAGER):
        from projects.models import ProjectAssignment
        ids = set(qs.filter(manager_id=user.id).values_list('id', flat=True))
        ids.update(ProjectAssignment.objects.filter(
            project__manager=user, is_current=True
        ).values_list('user_id', flat=True))
        ids.update(ProjectAssignment.objects.filter(
            project__lead=user, is_current=True
        ).values_list('user_id', flat=True))
        ids.add(user.id)
        return qs.filter(id__in=ids)
    return qs.filter(id=user.id)


def _team_or_scope_denied(user):
    from core.models import User
    if user.role in (User.ROLE_EMPLOYEE,):
        return "This information is available only to Managers, Project Managers, General Managers, HR, CTO, COO, or Admin."
    return None


def _intent_my_profile_live(user, text):
    if not _any_word(text, 'my employee id', 'my employee number', 'my joining date',
                      'when did i join', 'my branch', 'my designation', 'my role'):
        return None
    lines = [f"Name: {_display_name(user)}"]
    if getattr(user, 'employee_id', None):
        lines.append(f"Employee ID: {user.employee_id}")
    if user.role:
        lines.append(f"Role: {user.get_role_display()}")
    if user.department:
        lines.append(f"Department: {user.department.name}")
    if user.branch:
        lines.append(f"Branch: {user.branch.name}")
    if user.date_joined_company:
        lines.append(f"Company joining date: {user.date_joined_company}")
    return "Here are your current profile details:\n" + "\n".join(f"• {x}" for x in lines)


def _intent_team_headcount(user, text):
    if not _any_word(text, 'my team headcount', 'how many in my team', 'team size',
                      'number of people in my team', 'my team members count'):
        return None
    denied = _team_or_scope_denied(user)
    if denied:
        return denied
    qs = _role_data_scope(user)
    count = qs.exclude(id=user.id).count()
    return f"Your current accessible team/scope contains {count} other active employee(s)."


def _intent_team_attendance_today(user, text):
    if not _any_word(text, 'team attendance today', 'my team attendance today',
                      'who is present today', 'who is absent today',
                      'team present today', 'team absent today'):
        return None
    denied = _team_or_scope_denied(user)
    if denied:
        return denied
    from attendance.models import AttendanceRecord
    qs = _role_data_scope(user).exclude(id=user.id)
    ids = list(qs.values_list('id', flat=True))
    records = AttendanceRecord.objects.filter(date=date.today(), user_id__in=ids)
    present_ids = set(records.filter(in_time__isnull=False).values_list('user_id', flat=True))
    names_present = [_display_name(u) for u in qs.filter(id__in=present_ids)[:12]]
    absent_qs = qs.exclude(id__in=present_ids)
    present = len(present_ids)
    total = qs.count()
    if _any_word(text, 'who is present today', 'team present today'):
        people = ', '.join(names_present) if names_present else 'No one has punched in yet.'
        return f"{present} of {total} people in your accessible team/scope have punched in today.\nPresent: {people}"
    if _any_word(text, 'who is absent today', 'team absent today'):
        names = ', '.join(_display_name(u) for u in absent_qs[:12]) if total else 'None'
        return f"{absent_qs.count()} people have no punch-in recorded today.\nNot punched in: {names}"
    return f"Today's team attendance: {present} present out of {total} active people in your accessible scope."


def _intent_team_leave_today(user, text):
    if not _any_word(text, 'who is on leave today', 'team leave today',
                      'my team leave today', 'employees on leave today',
                      'who are on leave today'):
        return None
    denied = _team_or_scope_denied(user)
    if denied:
        return denied
    from leaves.models import LeaveRequest
    qs = _role_data_scope(user).exclude(id=user.id)
    ids = list(qs.values_list('id', flat=True))
    active = LeaveRequest.objects.filter(
        user_id__in=ids, request_type__in=['LEAVE', 'WFH'],
        status='APPROVED'
    ).filter(
        start_date__lte=date.today(), end_date__gte=date.today()
    ).select_related('user')
    if not active.exists():
        return "No approved Leave/WFH record is found for your accessible team/scope today."
    lines = []
    for r in active[:20]:
        lines.append(f"• {_display_name(r.user)} — {r.get_request_type_display()}")
    return f"{active.count()} employee(s) are on approved Leave/WFH today:\n" + '\n'.join(lines)


def _intent_team_production_today(user, text):
    if not _any_word(text, 'team production today', 'my team production today',
                      'team output today', 'team achievement today',
                      'team efficiency today'):
        return None
    denied = _team_or_scope_denied(user)
    if denied:
        return denied
    from production_tracker.models import DailyProduction
    ids = list(_role_data_scope(user).exclude(id=user.id).values_list('id', flat=True))
    rows = DailyProduction.objects.filter(employee_id__in=ids, date=date.today()).select_related('employee', 'project')
    if not rows.exists():
        return "No production records are available for your accessible team/scope today."
    achieved = sum(r.achieved for r in rows)
    target = sum(r.actual_target or r.target_day for r in rows)
    eff = round((achieved / target) * 100, 2) if target else 0
    return f"Today's team production: {achieved} achieved against {target} target, overall efficiency about {eff}%. Records: {rows.count()}."


def _intent_pending_leave_details(user, text):
    if not _any_word(text, 'show pending leave requests', 'list pending leave',
                      'pending leave requests', 'leave requests awaiting approval',
                      'who is waiting for leave approval'):
        return None
    if not (user.is_hr_or_admin() or user.is_manager() or user.is_coo()):
        return "You don't have permission to view pending approval queues."
    from leaves.models import LeaveRequest
    if user.is_hr_or_admin():
        qs = LeaveRequest.objects.filter(status='PENDING_HR')
    elif user.is_coo():
        qs = LeaveRequest.objects.filter(status='PENDING_COO')
    else:
        # Manager/PM/GM: only show requests whose resolved approver is the
        # authenticated manager, rather than every PENDING_MANAGER row.
        qs = LeaveRequest.objects.filter(status='PENDING_MANAGER')
        qs = [r for r in qs.select_related('user') if r.get_manager() and r.get_manager().id == user.id]
    if not qs:
        return "There are no leave requests currently waiting for your approval."
    lines = []
    for r in list(qs)[:15]:
        when = r.start_date or r.permission_date
        lines.append(f"• {_display_name(r.user)} — {r.get_request_type_display()} — {when} — {r.get_status_display()}")
    return f"Pending leave requests: {len(qs)}\n" + '\n'.join(lines)


def _intent_my_payslip_live(user, text):
    if not _any_word(text, 'latest payslip details', 'my latest payslip',
                      'net salary this month', 'net pay', 'take home salary',
                      'salary credited', 'my payslip amount'):
        return None
    from payroll.models import Payslip
    slip = Payslip.objects.filter(user=user, is_released=True).select_related('payroll_run').order_by('-payroll_run__end_date', '-generated_at').first()
    if not slip:
        return "No released payslip is available for you yet."
    return (
        f"Your latest released payslip ({slip.payroll_run.start_date} to {slip.payroll_run.end_date}):\n"
        f"• Gross pay: Rs. {slip.gross_pay}\n"
        f"• Deductions: Rs. {slip.pf_deduction + slip.tds + slip.p_tax_lwf + slip.bonus_deduction + slip.other_deductions}\n"
        f"• Net pay: Rs. {slip.net_pay}\n"
        f"• Present days: {slip.days_present}\n"
        f"• LOP days: {slip.lop_days}"
    )


def _intent_allowance_request_status(user, text):
    if not _any_word(text, 'my allowance request', 'allowance request status',
                      'allowance requests', 'travel allowance status',
                      'internet allowance status', 'incentive request status',
                      'night shift allowance status'):
        return None
    from payroll.models import AllowanceRequest
    req = AllowanceRequest.objects.filter(user=user).order_by('-submitted_at').first()
    if not req:
        return "You don't have an allowance request on record."
    return (
        f"Your latest allowance request is {req.get_status_display()}: "
        f"{req.get_allowance_type_display()} for Rs. {req.amount}, "
        f"for {req.month:02d}/{req.year}."
    )


def _intent_my_project_details(user, text):
    if not _any_word(text, 'project details', 'my project details', 'project manager',
                      'project lead', 'project target', 'when does my project end'):
        return None
    from projects.models import ProjectAssignment
    assignments = ProjectAssignment.objects.filter(user=user, is_current=True).select_related(
        'project', 'project__manager', 'project__lead'
    )
    if not assignments.exists():
        return "You are not currently assigned to an active project."
    lines = []
    for a in assignments[:10]:
        p = a.project
        manager = _display_name(p.manager) if p.manager else 'Not assigned'
        lead = _display_name(p.lead) if p.lead else 'Not assigned'
        lines.append(
            f"• {p.name} — status: {p.get_status_display()}, role: {a.role_on_project or 'Member'}, "
            f"PM: {manager}, Lead: {lead}, daily target: {p.daily_target}"
        )
    return "Your current project details:\n" + '\n'.join(lines)


def _intent_scope_employee_search(user, text):
    """Safe directory-style lookup for a name only when the user has scope.
    This is deliberately conservative: it requires an explicit 'who/find'
    phrase and only returns basic directory fields, never salary/attendance.
    """
    if not _any_word(text, 'find employee', 'find an employee', 'employee details',
                      'who is', 'employee directory'):
        return None
    # Do not attempt fuzzy extraction from arbitrary questions here; the
    # existing directory page remains the authoritative search UI.
    if user.role == 'EMPLOYEE':
        return None
    try:
        url = reverse('employees:directory')
    except NoReverseMatch:
        url = None
    msg = "Use Employee Directory to search employees within your permitted scope."
    if url:
        msg += f" Direct link: {url}"
    return msg

DYNAMIC_INTENTS = [
    # Live, role-aware queries first
    _intent_my_profile_live,
    _intent_team_headcount,
    _intent_team_attendance_today,
    _intent_team_leave_today,
    _intent_team_production_today,
    _intent_pending_leave_details,
    _intent_my_payslip_live,
    _intent_allowance_request_status,
    _intent_my_project_details,
    _intent_scope_employee_search,
    _intent_leave_balance,
    _intent_apply_leave,
    _intent_leave_status,
    _intent_leaves_taken,
    _intent_team_leave_status,
    _intent_who_approves,
    _intent_attendance_today,
    _intent_today_production,
    _intent_resignation_status,
    _intent_query_status,
    _intent_overtime_status,
    _intent_wfh_status,
    _intent_my_department,
    _intent_pending_leave_approvals_count,
    _intent_pending_increments_count,
    _intent_employee_headcount,
    _intent_present_today_count,
    _intent_worked_sunday,
    _intent_attendance_month,
    _intent_salary_allowances,
    _intent_payslip,
    _intent_increment_status,
    _intent_my_target,
    _intent_my_projects,
    _intent_logout,
    _intent_raise_query,
    _intent_appraisal_calculator_howto,
    _intent_onboarding_training_howto,
    _intent_directory_bare,
    _intent_navigation,
    _intent_greeting,
]


# ---------------------------------------------------------------------------
# FAQ matching
# ---------------------------------------------------------------------------

def _match_faq(text, user):
    from chatbot.models import FAQ
    best = None
    for faq in FAQ.objects.filter(is_active=True):
        if faq.target_roles != 'ALL':
            allowed_roles = [r.strip() for r in faq.target_roles.split(',')]
            if user.role not in allowed_roles:
                continue
                
        for term in faq.all_match_terms():
            if term and term in text:
                if best is None or faq.priority > best.priority:
                    best = faq
                break
    return best


FALLBACK_REPLY = (
    "Sorry, I don't have an answer for that yet. Would you like me to forward this question to HR?"
)


def get_reply(user, raw_message):
    text = _normalize(raw_message)
    if not text:
        return {"reply": "Could you type your question?", "source": "UNKNOWN", "faq": None}

    for intent in DYNAMIC_INTENTS:
        reply = intent(user, text)
        if reply:
            return {"reply": reply, "source": "DATA", "faq": None}

    faq = _match_faq(text, user)
    if faq:
        return {"reply": faq.answer, "source": "FAQ", "faq": faq}

    return {"reply": FALLBACK_REPLY, "source": "UNKNOWN", "faq": None}