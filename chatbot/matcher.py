"""
Rule-based Your Assist answer engine.

Order of resolution for every incoming message:
  1. Dynamic HRMS-data intents (leave balance, today's attendance, manager,
     payslip...) — answered from the LOGGED-IN user's own records only.
     There is no way to query another employee's data through this bot.
  2. Deterministic SQL routing for known live-data keywords (headcount,
     "who manages X") — bypasses the LLM for facts RAG is told never to guess.
  2b. Exact/near-exact FAQ match — short-circuits the LLM ONLY when the
      message is clearly the same question as one in the FAQ table, so
      those answers stay word-for-word instead of being paraphrased by RAG.
  3. LLM (RAG over policy docs + the FAQ table) — first attempt at
     everything else (i.e. anything that wasn't a near-exact FAQ hit).
  4. Loose FAQ match (substring/keyword) — fallback for when the LLM has no
     answer.
  5. One more live-SQL attempt (HR/SENIOR_HR/ADMIN only) for phrasing step 2
     didn't anticipate.
  6. Fallback: "please contact HR".

Returns a dict: {"reply": str, "source": "DATA"|"SQL"|"RAG"|"FAQ"|"UNKNOWN", "faq": FAQ|None}
"""
import difflib
import re
from datetime import date

from django.urls import reverse, NoReverseMatch


def _normalize(text):
    return re.sub(r'\s+', ' ', text.strip().lower())


def _fuzzy_word(text, target, threshold=0.8):
    """Loose match for one target word against each word in text — tolerates
    common typos (e.g. 'appriciation', 'appritioation' for 'appreciation')
    that _any_word's exact word-boundary match would miss. Threshold 0.8 is
    chosen to catch those typos while still excluding unrelated but
    similar-looking words (e.g. 'application' scores ~0.78 against
    'appreciation' and is correctly excluded)."""
    for word in text.split():
        word = word.strip(' ?.!,;:"\'')
        if not word:
            continue
        if difflib.SequenceMatcher(None, word, target).ratio() >= threshold:
            return True
    return False


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
    return f"You have {balance.balance} day(s) of leave remaining this year (out of 12)."


def _intent_apply_leave(user, text):
    # NOTE: 'take leave' and bare 'permission' were removed from this list —
    # they're substrings of many unrelated questions (e.g. "what happens if
    # I take leave after my 12 days are over", "what's the permission
    # policy") and were hijacking those questions before they could reach
    # the FAQ table or RAG. Keep triggers specific to an actual "how do I
    # apply" request.
    # Bare 'leave request' was ALSO removed for the same reason — it's a
    # substring of "who is approving my leave request?", "status of my
    # leave request", etc., and was hijacking those before they reached
    # _intent_who_approves / _intent_leave_status. Keep to phrases that
    # clearly mean "I want to submit one", not just any mention of the term.
    if not _any_word(text, 'apply leave', 'apply for leave', 'how to apply leave',
                      'request leave', 'apply permission', 'raise a leave request',
                      'submit a leave request', 'new leave request', 'make a leave request',
                      'give a leave', 'give leave', 'raise a leave', 'raise leave',
                      'submit a leave', 'submit leave', 'put a leave', 'put leave',
                      'apply for permission', 'request permission', 'request for permission',
                      'where to apply for permission', 'where to request for permission',
                      'permission request', 'apply for a permission', 'how to apply permission',
                      'how to request permission', 'work from home', 'wfh',
                      'apply wfh', 'apply for wfh', 'wfh request', 'request wfh'):
        return None
    from chatbot.responses import apply_leave_response
    return apply_leave_response(user)


def _intent_leave_status(user, text):
    # Bare 'my leave request' was removed — same substring-hijack problem as
    # apply_leave's old 'leave request' trigger (see note above): it matched
    # "who is approving my leave request?" before that could reach
    # _intent_who_approves. Kept to phrasings that clearly ask for STATUS.
    if not _any_word(text, 'leave status', 'status of my leave request', 'my leave request status',
                      'check my leave request', 'track my leave request', 'is my leave approved',
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
                      'who approves', 'my manager name', 'who is approving', 'who is approving my leave',
                      'is approving my leave', 'who will approve', 'who approve', 'who is approve',
                      'approving my leave', 'who do i report to', 'who i report to',
                      'i report to'):
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
    # "show me the employees in my department" / "who is in my department" —
    # this intent used to answer these with JUST the department name, since
    # 'my department' is a substring of both. Point them to the actual
    # roster instead of leaving them thinking that was the whole answer.
    if _any_word(text, 'employees', 'who is in', "who's in", 'list', 'show me', 'team members', 'colleagues'):
        from chatbot.responses import directory_response
        msg += "\n\n" + directory_response(user)
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
    # "How many employees are absent/present today" is a more specific
    # attendance-count question that shares this phrase's trigger words —
    # let _intent_attendance_count_today answer those instead of this
    # generic total-headcount reply.
    if _any_word(text, 'absent', 'present today', 'punched in', 'attendance count'):
        return None
    if not (user.is_hr_or_admin() or user.is_manager() or user.is_coo()):
        return "This is only available to Managers, HR, or the COO."
    qs = _role_data_scope(user)
    if user.role in ('MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER', 'CTO'):
        qs = qs.exclude(id=user.id)
    return f"There are currently {qs.count()} active employee(s) in your permitted scope."


def _intent_present_today_count(user, text):
    """Handles both 'how many present today' and 'how many absent today'
    style questions — previously only present-count phrasing was handled,
    so absent-count questions fell through to the generic headcount intent
    (fixed to bail out above) or, worse, straight to the LLM."""
    wants_absent = _any_word(text, 'how many absent today', 'how many employees are absent',
                              'how many people are absent', 'absent count today',
                              'how many are absent today', 'who is absent today')
    wants_present = _any_word(text, 'how many present today', 'how many people are present',
                               'attendance count today', 'how many punched in today',
                               'today attendance count', 'how many employees are present')
    if not (wants_absent or wants_present):
        return None
    if not (user.is_hr_or_admin() or user.is_manager() or user.is_coo()):
        return "This is only available to Managers, HR, or the COO."
    from attendance.models import AttendanceRecord
    ids = list(_role_data_scope(user).values_list('id', flat=True))
    total = len(ids)
    present_count = AttendanceRecord.objects.filter(
        date=date.today(), user_id__in=ids, in_time__isnull=False
    ).count()
    if wants_absent:
        return (
            f"{max(total - present_count, 0)} employee(s) in your permitted scope have not "
            f"punched in today (out of {total})."
        )
    return f"{present_count} employee(s) in your permitted scope have punched in today so far."


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
    if not _any_word(text, 'payslip', 'pay slip', 'salary slip', 'download payslip',
                      'how to download payslip', 'where to download payslip',
                      'get payslip', 'view payslip'):
        return None
    # "Can I view ANOTHER employee's payslip?" is a permission/authorization
    # question, not a "how do I get my own payslip" request — but 'payslip'
    # alone matched it, so it was answering with own-payslip download
    # instructions instead. Let it fall through to RAG/FAQ, which already
    # handles the equivalent "another employee's salary" question correctly.
    if _any_word(text, 'another employee', 'other employee', "someone else's", 'colleague',
                 'anyone else', "employee's payslip", 'another person', 'other person'):
        return None
    from chatbot.responses import payslip_response
    from payroll.models import Payslip
    latest = Payslip.objects.filter(user=user).order_by('-generated_at').first()
    return payslip_response(user, latest)


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
    has_percentage_word = _any_word(text, 'percentage', 'percent') or '%' in text
    # 'percentage'/'percent' now also qualifies alongside present/days —
    # "what is my attendance percentage this month?" has 'month' + a
    # percentage word but no 'present'/'attended'/'days', so it used to
    # miss this intent entirely and fall through to RAG, which then
    # hallucinated a bogus formula instead of just computing the real
    # number from AttendanceRecord. See chat history 2026-08-29.
    if not (has_month_word and (has_present_word or has_percentage_word) and (has_days_word or has_percentage_word)):
        return None
    from attendance.models import AttendanceRecord
    today = date.today()
    records = AttendanceRecord.objects.filter(
        user=user, date__year=today.year, date__month=today.month, in_time__isnull=False
    )
    count = records.count()
    if not has_percentage_word:
        return f"You've been present {count} day(s) so far this month ({today.strftime('%B %Y')})."

    # Elapsed working days this month so far (Mon–Sat, excluding Sunday) —
    # same "working day" definition used by _intent_perfect_attendance.
    elapsed_working_days = sum(
        1 for d in range(1, today.day + 1)
        if date(today.year, today.month, d).weekday() != 6
    )
    if elapsed_working_days == 0:
        return f"You've been present {count} day(s) so far this month ({today.strftime('%B %Y')})."
    pct = round(count / elapsed_working_days * 100, 1)
    return (
        f"You've been present {count} of {elapsed_working_days} working day(s) so far this month "
        f"({today.strftime('%B %Y')}) — that's {pct}%."
    )


# Matches "last/past N days", optionally with "working"/"business" in
# between ("last 7 working days", "past 10 business days") — the earlier
# exact-phrase list only caught "last 7 days" verbatim and missed the very
# common "N working days" phrasing, silently falling through to RAG, which
# hallucinated a nonexistent separate "Production Tracker app". See chat
# history 2026-08-29.
_RECENT_DAYS_RE = re.compile(r'\b(?:last|past)\s+(\d{1,3})\s*(?:working|business)?\s*days?\b')


def _intent_attendance_recent_days(user, text):
    """'My attendance for the last N days' / 'past week'. Kept separate
    from _intent_attendance_month since it needs 'last'/'past' + a rolling
    window, not a calendar-month window. This is answerable directly from
    AttendanceRecord, so it's handled here instead of being left to fall
    through to the LLM, which (with no real grounding for a query this
    specific) has been observed to fall back to a generic "as an AI
    language model I don't have access to your personal data" refusal —
    wrong, since the bot does have the logged-in user's own attendance."""
    match = _RECENT_DAYS_RE.search(text)
    if match:
        num_days = int(match.group(1))
    elif _any_word(text, 'last week', 'past week'):
        num_days = 7
    else:
        return None
    num_days = max(1, min(num_days, 90))  # sane bounds — avoid a 10000-day query
    from datetime import timedelta
    from attendance.models import AttendanceRecord
    today = date.today()
    start = today - timedelta(days=num_days - 1)
    records = AttendanceRecord.objects.filter(
        user=user, date__gte=start, date__lte=today, in_time__isnull=False
    ).order_by('date')
    present_days = records.count()
    lines = [
        f"• {r.date.strftime('%d %b')} — in: {r.in_time.strftime('%I:%M %p') if r.in_time else '-'}, "
        f"out: {r.out_time.strftime('%I:%M %p') if r.out_time else 'not punched out'}"
        for r in records[:num_days]
    ]
    header = (
        f"You were present {present_days} of {num_days} day(s) from "
        f"{start.strftime('%d %b')} to {today.strftime('%d %b')}:"
    )
    if not lines:
        return header
    return header + "\n" + "\n".join(lines)


def _intent_perfect_attendance(user, text):
    """Who, within the asker's scope, has been present every working day
    (Mon-Sat, i.e. excluding Sundays) so far this month. Lateness is not
    considered — this only checks for zero missed working days."""
    if not _any_word(text, 'perfect attendance', 'full attendance', '100% attendance',
                      'best attendance', 'no absence this month', 'no absences this month',
                      'attendance leaderboard', 'who has perfect attendance'):
        return None
    denied = _team_or_scope_denied(user)
    if denied:
        return denied
    from attendance.models import AttendanceRecord
    from django.db.models import Count
    today = date.today()
    month_start = today.replace(day=1)
    # Working days elapsed so far this month, treating Sunday as the
    # standard week off (consistent with _intent_worked_sunday elsewhere
    # in this file). Django's week_day lookup: Sunday=1.
    expected_days = sum(
        1 for d in range(1, today.day + 1)
        if date(today.year, today.month, d).weekday() != 6
    )
    if expected_days == 0:
        return (
            "Not enough working days have passed yet this month to determine "
            "perfect attendance."
        )
    qs = _role_data_scope(user)
    ids = list(qs.values_list('id', flat=True))
    counts = dict(
        AttendanceRecord.objects.filter(
            user_id__in=ids, date__gte=month_start, date__lte=today, in_time__isnull=False,
        ).exclude(date__week_day=1)
        .values('user_id').annotate(c=Count('id')).values_list('user_id', 'c')
    )
    perfect_ids = [uid for uid, cnt in counts.items() if cnt >= expected_days]
    if not perfect_ids:
        return (
            f"No one in your accessible scope has perfect attendance so far this month "
            f"({today.strftime('%B %Y')})."
        )
    names = ', '.join(_display_name(u) for u in qs.filter(id__in=perfect_ids)[:15])
    return (
        f"{len(perfect_ids)} people have perfect attendance so far this month "
        f"({expected_days} working day(s) elapsed, Sundays excluded): {names}."
    )


def _intent_team_appreciation(user, text):
    """Which project team(s) qualify for HR's Appreciation Report: teams
    where no member (within the asker's own accessible scope) took leave
    in the given month. Mirrors leaves.views.appreciation_report's logic
    and permission set, but — like every other intent in this file —
    derives scope strictly from the authenticated user rather than a
    request-level branch switcher, so for COO/GENERAL_MANAGER/ADMIN the
    result may cover a broader or narrower set of branches than the web
    page's current 'active branch' selection."""
    if not (_fuzzy_word(text, 'appreciation') or _any_word(text, 'appreciation report')):
        return None
    from core.models import User
    if user.role not in (User.ROLE_HR, User.ROLE_SENIOR_HR, User.ROLE_GENERAL_MANAGER,
                          User.ROLE_COO, User.ROLE_ADMIN):
        return "The Appreciation Report is available to HR, General Manager, COO, and Admin only."
    from datetime import timedelta
    from projects.models import Project, ProjectAssignment
    from leaves.models import LeaveRequest
    today = date.today()
    if _any_word(text, 'this month'):
        month, year = today.month, today.year
        label = today.strftime('%B %Y')
    else:
        last_month_date = today.replace(day=1) - timedelta(days=1)
        month, year = last_month_date.month, last_month_date.year
        label = last_month_date.strftime('%B %Y')
    scope_ids = set(_role_data_scope(user).values_list('id', flat=True))
    projects = Project.objects.filter(status__in=['PENDING', 'APPROVED', 'COMPLETED'])
    winners = []
    for project in projects:
        team_ids = set(ProjectAssignment.objects.filter(
            project=project, is_current=True, user_id__in=scope_ids
        ).values_list('user_id', flat=True))
        if not team_ids:
            continue
        leaves_taken = LeaveRequest.objects.filter(
            user_id__in=team_ids,
            status__in=['APPROVED', 'PENDING_HR', 'PENDING_MANAGER'],
            start_date__year=year, start_date__month=month,
        ).exists()
        if not leaves_taken:
            winners.append(project.name)
    if not winners:
        return (
            f"No project team in your accessible scope qualifies for appreciation in {label} "
            f"— every team had at least one member on leave."
        )
    names = ', '.join(winners[:15])
    return f"{len(winners)} team(s) qualify for appreciation in {label} (no member took leave): {names}."


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


def _intent_daily_summary(user, text):
    """'Today's HR summary' style broad multi-part questions — present/
    absent/on-leave counts, pending approvals, team productivity. Every
    piece here is already computable from existing data (same queries as
    _intent_team_attendance_today / _intent_team_leave_today /
    _intent_pending_leave_approvals_count / _intent_team_production_today),
    but no single intent used to combine them, so a broad question like
    this fell through to RAG, which had no chance of answering something
    this multi-part and just hung until the frontend's 180s abort. See
    chat history 2026-08-29. Each section is wrapped individually so one
    missing model/table doesn't blank out the whole summary."""
    if not _any_word(text, "today's summary", 'daily summary', 'hr summary',
                      "today's hr summary", 'give me a summary', 'summary for today',
                      'overview of today', "today's overview", 'daily overview',
                      'team summary', "today's report"):
        return None
    denied = _team_or_scope_denied(user)
    if denied:
        return denied

    lines = []

    try:
        from attendance.models import AttendanceRecord
        qs = _role_data_scope(user).exclude(id=user.id)
        ids = list(qs.values_list('id', flat=True))
        total = len(ids)
        present = AttendanceRecord.objects.filter(
            date=date.today(), user_id__in=ids, in_time__isnull=False
        ).count()
        lines.append(f"• Attendance: {present} present, {total - present} absent, out of {total}.")
    except Exception:
        lines.append("• Attendance: unavailable right now.")

    try:
        from leaves.models import LeaveRequest
        ids = list(_role_data_scope(user).exclude(id=user.id).values_list('id', flat=True))
        on_leave = LeaveRequest.objects.filter(
            user_id__in=ids, request_type__in=['LEAVE', 'WFH'], status='APPROVED',
            start_date__lte=date.today(), end_date__gte=date.today(),
        ).count()
        lines.append(f"• On leave/WFH today: {on_leave}.")
    except Exception:
        lines.append("• On leave/WFH: unavailable right now.")

    try:
        from leaves.models import LeaveRequest
        if user.is_hr_or_admin():
            pending = LeaveRequest.objects.filter(status='PENDING_HR').count()
        elif user.is_coo() or user.role in ('CTO', 'GENERAL_MANAGER'):
            pending = LeaveRequest.objects.filter(status='PENDING_COO').count()
        else:
            pending_qs = LeaveRequest.objects.filter(status='PENDING_MANAGER')
            pending = sum(1 for r in pending_qs if r.get_manager() and r.get_manager().id == user.id)
        lines.append(f"• Pending your leave approval: {pending}.")
    except Exception:
        lines.append("• Pending leave approvals: unavailable right now.")

    try:
        from production_tracker.models import DailyProduction
        ids = list(_role_data_scope(user).exclude(id=user.id).values_list('id', flat=True))
        rows = DailyProduction.objects.filter(employee_id__in=ids, date=date.today())
        if rows.exists():
            achieved = sum(r.achieved for r in rows)
            target = sum(r.actual_target or r.target_day for r in rows)
            eff = round((achieved / target) * 100, 2) if target else 0
            lines.append(f"• Team productivity: {achieved} achieved vs {target} target ({eff}% efficiency).")
        else:
            lines.append("• Team productivity: no production records for today yet.")
    except Exception:
        lines.append("• Team productivity: unavailable right now.")

    return f"Here's today's summary ({date.today().strftime('%d %b %Y')}):\n" + "\n".join(lines)


def _intent_pending_leave_details(user, text):
    if not _any_word(text, 'show pending leave requests', 'list pending leave',
                      'pending leave requests', 'leave requests awaiting approval',
                      'who is waiting for leave approval',
                      'leave requests currently pending', 'leave requests are currently pending',
                      'leave requests are pending', 'what leave requests are pending',
                      'leave requests pending approval'):
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


def _intent_security_block(user, text):
    """Hard-coded security firewall. Runs before any LLM call.
    Blocks attempts to extract credentials, other employees' salaries/payslips."""
    # Credential fishing attempts
    if _any_word(text, 'password', 'username and password', 'login credential',
                  'give me the password', 'what is the password', 'admin password',
                  'hr password', 'hr username'):
        return (
            "⚠️ I'm not able to share login credentials. "
            "If you've forgotten your password, please contact your system administrator."
        )
    # Trying to get another employee's payslip or salary
    if _any_word(text, 'coo payslip', 'hr payslip', 'give a hr payslip',
                  'give coo payslip', "someone else's payslip",
                  'coo salary', 'hr salary', 'manager salary',
                  'what is the salary of', 'how much does the coo', 'how much does hr earn'):
        return (
            "⚠️ I can only show **your own** payslip and salary information. "
            "I'm not able to access or share another employee's financial data."
        )
    return None


def _intent_bot_identity(user, text):
    """Handles questions about what the chatbot is — prevents it from
    claiming to be human or denying being the assistant."""
    if _any_word(text, 'who are you', 'what are you', 'are you human', 'are you a robot',
                  'are you a bot', 'are you an ai', 'are you real', 'you said you are my assist',
                  'you are my assist', 'are you assist', 'you are not my assist'):
        return (
            "I'm **Your Assist** — an AI chatbot built into SPS HRMS to help you with "
            "HR-related questions. I'm not a human. I can help you with leave, attendance, "
            "payslips, projects, and general HR queries. How can I help you today?"
        )
    return None


def _intent_role_capabilities(user, text):
    """Answers 'what can I access / do with my role' directly from
    get_data_scope() instead of letting it fall through to RAG and then
    the SQL last-resort fallback — a broad, non-data question like this has
    zero chance of being answered by either, and was previously chaining
    up to 3 slow local-LLM calls (RAG, then SQL-gen, then SQL-format)
    before timing out client-side at 180s. See chat history 2026-08-29."""
    if not _any_word(text, 'what can i access', 'what can i do', 'what am i allowed to do',
                      'my access', 'my permissions', 'what features can i',
                      'what can i see', 'my role allows', 'access with my role',
                      'my capabilities', 'what can i access with my current role',
                      'what do i have access to', 'what sections can i see'):
        return None

    from chatbot.permissions import get_data_scope
    scope = get_data_scope(user)
    raw_role = getattr(user, 'role', 'Employee')
    # .title() mangles acronym roles ("HR" -> "Hr", "COO" -> "Coo") — keep
    # known acronyms as-is, title-case everything else.
    _ACRONYM_ROLES = {'HR', 'SENIOR_HR', 'COO', 'CTO', 'ADMIN'}
    role_label = raw_role if raw_role.upper() in _ACRONYM_ROLES else raw_role.replace('_', ' ').title()
    if raw_role.upper() == 'SENIOR_HR':
        role_label = 'Senior HR'

    common = "Everyone can view their own profile, attendance, payslips, and raise queries to HR."

    scope_extras = {
        'SELF': "As an Employee, you're scoped to your own records: attendance, permission/leave/WFH requests, payslips, allowance requests, your assigned projects, and your hourly tracker.",
        'TEAM': "As a Manager, you additionally see your team: leave approvals, allowance request approvals, your team's hourly tracker, directory, projects, increment feedback, onboarding training, and team queries.",
        'PROJECT': "As a Project Manager, you additionally see: your team's leave/allowance/increment approvals, the hourly tracker for your team, projects, directory, and team queries.",
        'BRANCH': "As a General Manager, you additionally see branch-wide manpower, attendance, and leave data across your branch.",
        'HR': "As HR, you additionally have: employee directory (view + onboard), hourly tracker, overtime/Sunday permissions, allowance requests, resignations, leave requests, payroll runs, increments, appraisal calculator, reports, COO approvals, pending salary updates, projects, and employee queries — company-wide.",
        'MANAGEMENT': "As COO, you additionally see company-wide branch manpower, leave approvals, increment approvals, reports, and team attendance across all branches.",
        'TECHNICAL': "As CTO, you additionally have technical/company-wide oversight views alongside your own profile and attendance.",
        'ADMIN': "As Admin, you have full system access: all HR functions plus system administration.",
    }

    extra = scope_extras.get(scope, "")
    reply = f"With your current role (**{role_label}**):\n\n{common}"
    if extra:
        reply += f"\n\n{extra}"
    reply += "\n\nCheck the left sidebar for the exact menu items available to you."
    return reply


def _intent_forward_to_hr(user, text):
    """Handles requests to forward a message/query to HR — redirects properly."""
    if _any_word(text, 'forward to hr', 'forward this to hr', 'send this to hr',
                  'tell hr', 'inform hr', 'message to hr', 'contact hr about this',
                  'can you forward', 'forward my message'):
        try:
            url = reverse('queries:my_queries')
        except NoReverseMatch:
            url = '/queries/me/'
        return (
            "I can't forward messages directly, but you can easily send a message to HR yourself:\n"
            f"1. Go to **Queries → My Queries**: {url}\n"
            "2. Click **New Query**\n"
            "3. Select **HR** as the recipient\n"
            "4. Type your message and click **Submit**\n"
            "HR will be notified immediately and will reply in the system."
        )
    return None


def _intent_conversational_fallback(user, text):
    """Handles casual/social messages that are not HR-related."""
    if _any_word(text, 'really', 'are you sure', 'you sure', 'is that right',
                  'is that correct', 'seriously'):
        return (
            "Yes — that reflects the data available to me right now. If something looks "
            "off, let me know the details and I can take another look, or you can verify "
            "directly on the relevant HRMS page."
        )
    if _any_word(text, 'can we be friends', 'can we be frnds', 'be my friend',
                  'be frnds', 'common man', 'come on man', 'okay', 'oh okay',
                  'alright', 'thank you', 'thanks', 'ok', 'got it', 'understood',
                  'sure', 'great', 'awesome', 'nice', 'good'):
        if _any_word(text, 'thank you', 'thanks', 'ok', 'okay', 'oh okay',
                      'got it', 'understood', 'sure', 'great', 'awesome', 'nice',
                      'alright', 'good'):
            return "You're welcome! Let me know if you have any other HR questions. 😊"
        return (
            "I'm Your Assist — an AI HR chatbot! While I'm not able to be friends in the "
            "traditional sense, I'm always here to help you with HR questions. "
            "What can I assist you with today?"
        )
    return None


def _intent_tracker_navigation(user, text):

    if not _any_word(text, 'hourly tracker', 'where to see tracker', 'where to fill tracker',
                      'how to fill tracker', 'daily entry', 'production tracker', 'tracker page',
                      'where is tracker', 'how to use tracker', 'fill production',
                      'where to see hourly tracker', 'how to see hourly tracker',
                      'production entry', 'fill hourly', 'hourly production',
                      'enter my hourly production', 'enter production details',
                      'enter hourly production', 'log production', 'log my production',
                      'where can i enter my production', 'where to enter production'):
        return None
    from chatbot.responses import tracker_response
    return tracker_response(user)


def _intent_attendance_navigation(user, text):
    if not _any_word(text, 'where to see attendance', 'how to see attendance',
                      'where is attendance', 'how to view attendance',
                      'where to view attendance', 'check my attendance',
                      'view my attendance', 'see my attendance'):
        return None
    from chatbot.responses import attendance_response
    return attendance_response(user)


def _intent_raise_query_navigation(user, text):
    if not _any_word(text, 'how to raise query', 'how to raise a query', 'raise a query',
                      'contact hr', 'ask hr', 'how to contact hr', 'where to ask hr',
                      'raise ticket', 'helpdesk', 'where to raise query', 'send query to hr'):
        return None
    from chatbot.responses import raise_query_response
    return raise_query_response(user)


def _intent_projects_navigation(user, text):
    if not _any_word(text, 'where to see projects', 'how to view projects',
                      'where is projects', 'project page', 'how to submit project update',
                      'where is project list', 'project list page'):
        return None
    from chatbot.responses import projects_response
    return projects_response(user)


def _intent_who_is_on_leave_today(user, text):
    # Was an exact-phrase list ('who is on leave today', 'who is on leave')
    # — broke on any inserted qualifier, e.g. "who is on APPROVED leave
    # today" doesn't contain 'who is on leave' as a contiguous substring,
    # so it fell through to RAG, which hallucinated a garbled non-answer
    # ("The employee on approved leave today is not specified in the
    # question."). Switched to an AND-based check, same fix pattern as
    # _intent_attendance_month's percentage fix. See chat history 2026-08-29.
    has_who_on = _any_word(text, 'who is on', "who's on", 'who all is on', 'who all are on')
    has_leave_word = _any_word(text, 'leave')
    if has_who_on and has_leave_word:
        from leaves.models import LeaveRequest
        from django.utils import timezone
        
        today = timezone.localdate()
        
        # 1. Fetch live MySQL data via ORM
        base_query = LeaveRequest.objects.filter(
            status='APPROVED', 
            request_type__in=['LEAVE', 'WFH'],
            start_date__lte=today, 
            end_date__gte=today
        ).select_related('user', 'user__department')
        
        # 2. Check Role Permission & Enforce in Python
        role = getattr(user, 'role', 'EMPLOYEE').upper()
        
        if role in ['EMPLOYEE', 'MANAGER']:
            # Show only their department/team
            dept = getattr(user, 'department', None)
            if not dept:
                return "I couldn't find your department to show team leaves."
            leaves = base_query.filter(user__department=dept)
            scope = f"in your department ({dept.name})"
            
        elif role in ['HR', 'SENIOR_HR', 'ADMIN']:
            # Show HR-wide details
            leaves = base_query.all()
            scope = "company-wide"
            
        elif role == 'COO':
            # Show branch/company level
            leaves = base_query.all()
            scope = "across all branches"
            
        elif role == 'CTO':
            # Show technical teams only
            leaves = base_query.filter(user__department__name__in=['IT', 'Engineering', 'Technical'])
            scope = "in Technical/IT departments"
            
        else:
            # Fallback constraint
            leaves = base_query.filter(user__department=getattr(user, 'department', None))
            scope = "in your department"
            
        # 3. Generate Role-Specific Response
        count = leaves.count()
        if count == 0:
            return f"There is no one currently on approved leave today {scope}."
            
        names = ", ".join([f"{l.user.first_name or l.user.username}" for l in leaves])
        return f"There are **{count}** employees on leave today {scope}: {names}."
    return None

def _intent_who_am_i(user, text):
    if _any_word(text, 'who am i', 'my name', 'do you know me', 'do you know who i am',
                  'who im', 'who i m', 'who i am', 'what im', 'what am i',
                  'my role', 'what is my role', 'what role am i', 'am i a coo',
                  'am i not a coo', 'am i an employee', 'am i hr', 'am i a manager',
                  'what position am i', 'my designation', 'my position'):
        name = getattr(user, 'first_name', '') or getattr(user, 'username', 'Employee')
        # Use the same display source as _intent_my_profile_live
        # (get_role_display, from ROLE_CHOICES) instead of formatting the
        # raw role code — that mismatch used to show "Senior Hr" here vs.
        # "GM (HR)" for the identical user via the other phrasing.
        if hasattr(user, 'get_role_display'):
            role = user.get_role_display()
        else:
            role = getattr(user, 'role', 'EMPLOYEE').replace('_', ' ').title()
        dept = getattr(user, 'department', None)
        dept_str = f" in the **{dept.name}** department" if dept else ""
        return f"You are logged in as **{name}**{dept_str}. Your role in the system is **{role}**."
    return None


DYNAMIC_INTENTS = [
    # === SECURITY FIREWALL — always runs first ===
    _intent_security_block,

    # Bot identity — runs early so it doesn't fall through to RAG
    _intent_bot_identity,
    _intent_role_capabilities,

    # Live, role-aware queries first
    _intent_my_profile_live,
    _intent_team_headcount,
    _intent_team_attendance_today,
    _intent_team_leave_today,
    _intent_team_production_today,
    _intent_daily_summary,
    _intent_pending_leave_details,
    _intent_my_payslip_live,
    _intent_allowance_request_status,
    _intent_my_project_details,
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
    _intent_attendance_recent_days,
    _intent_perfect_attendance,
    _intent_salary_allowances,
    _intent_payslip,
    _intent_increment_status,
    _intent_my_target,
    _intent_my_projects,
    _intent_logout,
    _intent_raise_query,
    _intent_forward_to_hr,
    _intent_team_appreciation,
    _intent_appraisal_calculator_howto,
    _intent_onboarding_training_howto,
    _intent_directory_bare,
    _intent_greeting,
    _intent_tracker_navigation,
    _intent_attendance_navigation,
    _intent_raise_query_navigation,
    _intent_projects_navigation,
    _intent_who_is_on_leave_today,
    # Deliberately last: its 'who is' trigger is broad by design (bare-name
    # directory lookups have no more specific pattern to match), so every
    # other intent above — who_approves, leave_status, team_leave_status,
    # who_is_on_leave_today, who_am_i — must get first refusal.
    _intent_scope_employee_search,
    _intent_who_am_i,
    _intent_conversational_fallback,
]


# ---------------------------------------------------------------------------
# FAQ matching
# ---------------------------------------------------------------------------

def _strip_punct(text):
    """Drops leading/trailing punctuation (?, ., !, etc.) so 'what is the
    pay day?' and 'what is the pay day' compare equal. Internal punctuation
    is left alone — this is just for whole-string comparison."""
    return text.strip(' ?.!,;:')


def _match_faq_exact(text, user, threshold=0.90):
    """Stricter sibling of _match_faq: only fires when the message is
    basically THE SAME question as an FAQ entry (exact match after
    punctuation-stripping, or a high fuzzy-similarity ratio) — never on a
    short keyword merely appearing somewhere inside a longer, different
    question. Used to short-circuit the LLM so these specific answers come
    back word-for-word instead of being paraphrased by RAG.

    Returns the single best FAQ match, or None if nothing clears the bar."""
    from chatbot.models import FAQ
    stripped_text = _strip_punct(text)
    if not stripped_text:
        return None

    best = None
    best_ratio = 0.0
    for faq in FAQ.objects.filter(is_active=True):
        if faq.target_roles != 'ALL':
            allowed_roles = [r.strip() for r in faq.target_roles.split(',')]
            if user.role not in allowed_roles:
                continue

        for term in faq.all_match_terms():
            term_stripped = _strip_punct(term)
            if not term_stripped:
                continue
            if stripped_text == term_stripped:
                ratio = 1.0
            else:
                ratio = difflib.SequenceMatcher(None, stripped_text, term_stripped).ratio()
            if ratio >= threshold and (
                best is None or ratio > best_ratio or (ratio == best_ratio and faq.priority > best.priority)
            ):
                best, best_ratio = faq, ratio
    return best


def _match_faq(text, user):
    """Word-boundary match against each FAQ's question/keywords, same as
    the dynamic intents use via _any_word — plain substring containment
    used to let short keywords match inside unrelated words (e.g. FAQ
    keyword "pay" matching inside "display", "payslip" pulling in "pay
    day" questions, etc)."""
    from chatbot.models import FAQ
    best = None
    for faq in FAQ.objects.filter(is_active=True):
        if faq.target_roles != 'ALL':
            allowed_roles = [r.strip() for r in faq.target_roles.split(',')]
            if user.role not in allowed_roles:
                continue

        for term in faq.all_match_terms():
            if term and _any_word(text, term):
                if best is None or faq.priority > best.priority:
                    best = faq
                break
    return best


FALLBACK_REPLY = (
    "Sorry, I don't have an answer for that yet. Would you like me to forward this question to HR?"
)


def get_reply(user, raw_message, chat_history=None):
    """chat_history: optional list of {"q": str, "a": str} dicts, most recent
    last — passed through to the RAG prompt so short follow-ups ("what about
    casual leave?") can resolve against the previous turn. Callers (views.py)
    are responsible for persisting/trimming this across requests; matcher and
    rag_engine treat it as read-only context for a single call."""
    text = _normalize(raw_message)
    if not text:
        return {"reply": "Could you type your question?", "source": "UNKNOWN", "faq": None}

    for intent in DYNAMIC_INTENTS:
        reply = intent(user, text)
        if reply:
            return {"reply": reply, "source": "DATA", "faq": None}

    # 2. Deterministic SQL Routing for common live-data keywords — kept
    # ahead of the LLM on purpose: these are numeric/factual lookups (who
    # manages X, headcount) that RAG's own prompt explicitly refuses to
    # guess at, so answering them straight from the DB is both faster and
    # more reliable than routing through the LLM first.
    if _any_word(
        text,
        'who is the manager', 'how many users', 'how many employees', 'manager of',
        'how many people', 'how many staff', 'how many members', 'headcount',
        'how many employees are there', 'who manages', 'who heads', 'department manager',
        'who is the head of', 'total employees', 'total users', 'number of employees',
        'number of users',
    ):
        try:
            from chatbot.sql_engine import query_live_data
            sql_reply = query_live_data(user, raw_message)
            if sql_reply and "cannot answer" not in sql_reply.lower() and "trouble" not in sql_reply.lower():
                return {"reply": sql_reply, "source": "SQL", "faq": None}
        except Exception:
            pass

    # 2b. Exact/near-exact FAQ match — short-circuits the LLM so a question
    # that's clearly one of the FAQ entries comes back word-for-word, not as
    # an LLM paraphrase of it. Deliberately strict (see _match_faq_exact) so
    # it only claims genuine matches and leaves everything else for RAG.
    exact_faq = _match_faq_exact(text, user)
    if exact_faq:
        return {"reply": exact_faq.answer, "source": "FAQ", "faq": exact_faq}

    # 3. LLM (RAG) tries first for everything else. Note: the RAG index
    # already includes every active FAQ row (see rag_engine.py's docstring),
    # so most FAQ-covered questions get answered here anyway — just phrased
    # by the model instead of returned as the FAQ's exact stored text.
    try:
        from chatbot.rag_engine import rag_answer
        reply = rag_answer(user, raw_message, chat_history=chat_history)
        if reply:
            return {"reply": reply, "source": "RAG", "faq": None}
    except Exception:
        import logging
        logging.getLogger(__name__).exception("RAG Engine failed")

    # 4. Loose FAQ match (substring/keyword) — fallback for when the LLM had
    # nothing (index missing, Ollama down, or it genuinely said
    # NO_ANSWER_IN_CONTEXT). A safe last stop before escalating to a live
    # SQL attempt or giving up.
    faq = _match_faq(text, user)
    if faq:
        return {"reply": faq.answer, "source": "FAQ", "faq": faq}

    # 5. Last resort — if neither RAG nor FAQ had an answer and this user is
    # even allowed to run live SQL, try it once before giving up. Catches
    # phrasing step 2's keyword list doesn't anticipate (e.g. "who's in
    # charge of IT?"). Only attempted for HR/SENIOR_HR/ADMIN —
    # sql_security.validate_sql blocks everyone else anyway, so skipping the
    # call for other roles avoids a wasted LLM round-trip that would just be
    # rejected.
    #
    # Gated on a data-shaped phrasing check (who/how many/count/list/manager
    # etc) — without this, ANY unanswered question chains 2 MORE untimed
    # local-LLM calls (SQL generation + answer formatting) on top of the RAG
    # call that already ran, for questions that have zero chance of being a
    # valid SQL lookup (e.g. "what can I access with my role?"). That
    # stacking was the direct cause of the 180s client-side timeout — see
    # chat history 2026-08-29.
    if getattr(user, 'role', None) in ('HR', 'SENIOR_HR', 'ADMIN') and _any_word(
        text, 'who', 'how many', 'how much', 'count', 'list', 'manager', 'head of',
        'department', 'headcount', 'total', 'number of',
    ):
        try:
            from chatbot.sql_engine import query_live_data
            sql_reply = query_live_data(user, raw_message)
            if sql_reply and "cannot answer" not in sql_reply.lower() and "trouble" not in sql_reply.lower() \
                    and "couldn't find any data" not in sql_reply.lower():
                return {"reply": sql_reply, "source": "SQL", "faq": None}
        except Exception:
            pass

    return {"reply": FALLBACK_REPLY, "source": "UNKNOWN", "faq": None}