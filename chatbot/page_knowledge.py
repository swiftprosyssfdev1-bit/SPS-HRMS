"""
Page/feature knowledge layer — what a page IS, what's ON it, and what
actions are available — extracted directly from the real application
code (urls.py, views.py, models.py, templates), not invented.

This is the "PAGE" layer under a FEATURE in the target application map:

    APP -> FEATURE -> PAGE -> {purpose, navigation, visibility,
                                permissions, actions, forms, workflow,
                                related pages, invalid/nonexistent paths}

navigation/visibility/permissions for the sidebar itself still live in
navigation.py + permissions.py — this file only adds page-level and
action-level detail on top of that, and cross-references navigation.py
by NAV key instead of duplicating labels/routes.

Every fact below is traceable to a specific file/line at the time this
was written. If the code changes, this file goes stale until updated —
do not hand-edit facts that aren't backed by the source. Nothing here
should ever be treated as evidence of intent to hallucinate around; the
matcher must fall back to "not confirmed in the application" rather
than inventing a field or step not present here.
"""

# ---------------------------------------------------------------------------
# FEATURE: Projects  (app: projects)
# nav key: 'projects' (see chatbot/navigation.py NAV['projects'])
# source: projects/urls.py, projects/views.py, projects/models.py,
#         templates/projects/*.html
# ---------------------------------------------------------------------------

PROJECTS = {
    'feature': 'Projects',
    'app': 'projects',
    'nav_key': 'projects',  # cross-ref into chatbot.navigation.NAV

    'pages': {

        # -------------------------------------------------------------
        'list': {
            'label': 'Project List',
            'route': 'projects:list',
            'template': 'projects/list.html',
            'source': 'projects/views.py::project_list',
            'purpose': (
                "Shows the projects the user has access to, grouped into "
                "Completed, Active, and Upcoming sections."
            ),
            'visibility': (
                "HR, Admin, and COO see every APPROVED/COMPLETED project "
                "company-wide (optionally scoped to the active branch). "
                "Everyone else sees only projects where they are assigned, "
                "the lead, the manager, the creator, or the GM."
            ),
            'displays': [
                'Completed projects section',
                'Active projects section',
                'Upcoming projects section (start date not yet reached)',
            ],
            'actions': [
                {'action': 'Open a project', 'requires': 'must have view access to that project', 'leads_to': 'project_detail'},
                {'action': 'Create a project', 'requires': 'request.user.is_manager()', 'leads_to': 'create'},
            ],
            'related_pages': ['project_detail', 'create', 'my_history'],
        },

        # -------------------------------------------------------------
        'create': {
            'label': 'Create Project',
            'route': 'projects:create',
            'template': 'projects/create.html',
            'source': 'projects/views.py::create_project',
            'purpose': "Publish a new project. Projects go live immediately (status=APPROVED) — there is no separate approval step in current use.",
            'who_can_submit': "Only Managers (request.user.is_manager()). HR, Admin, and Employees cannot reach this action.",
            'form_fields': [
                'name (required)',
                'description',
                'requirements_file (file upload)',
                'start_date',
                'end_date',
                'employees (multi-select, required unless the creator is a General Manager)',
                'lead (must be one of the selected employees; required unless GM)',
                'project_manager (only shown/required if the creator is a General Manager — must be a PROJECT_MANAGER in the same branch)',
            ],
            'extra_rules': [
                'Only role=EMPLOYEE users can ever be assigned to a project or set as lead.',
                'The employee picker is scoped to the Production department in the same branch as the creator.',
                'A General Manager must pick a Project Manager first; for a GM, assigning employees/lead is optional (the PM can do it later).',
            ],
            'related_pages': ['list', 'project_detail'],
        },

        # -------------------------------------------------------------
        'project_detail': {
            'label': 'Project Details',
            'route': 'projects:project_detail',
            'template': 'projects/detail.html',
            'source': 'projects/views.py::project_detail',
            'purpose': "Full view of a single project — team, targets, submissions, and available management actions.",
            'visibility': (
                "Viewable by: HR/Admin/COO (view all), the project creator, the project manager, "
                "any assigned team member, or the GM on the project. Anyone else is redirected back to the list "
                "with 'You do not have access to this project.'"
            ),
            'displays': [
                'Project info (name, description, dates, requirements file)',
                'Assigned team members and roles',
                'Daily Target / QA Daily Target',
                'Submissions (progress updates) with status',
            ],
            'actions': [
                {'action': 'Assign a team member', 'requires': "can_edit_team = _can_manage_project(user) i.e. user.is_manager(); employee must be same branch as project, role=EMPLOYEE, and not in Software dept", 'form_fields': ['user', 'role_on_project (required)', 'start_date (required)']},
                {'action': 'Edit project', 'requires': '_can_manage_project(user) (is_manager)', 'leads_to': 'edit_project'},
                {'action': 'Delete project', 'requires': '_can_manage_project(user) (is_manager)', 'leads_to': 'delete_project'},
                {'action': 'Mark project completed', 'requires': 'request.user.is_manager() AND project.status != COMPLETED', 'leads_to': 'complete_project'},
                {'action': 'Set project-wide Daily Target', 'requires': 'is_lead OR is_project_manager (manager on project) OR is the GM on the project', 'leads_to': 'set_project_target', 'form_fields': ['daily_target']},
                {'action': 'Set an individual member override target', 'requires': 'same access as above (reachable from this page for managers/leads)', 'leads_to': 'set_individual_target', 'form_fields': ['work_type', 'target_day', 'effective_date']},
                {'action': 'Submit a project update', 'requires': 'project.lead_id == request.user.id (must be the project lead)', 'leads_to': 'submit_update — see PROJECTS["workflows"]["submit_update"] in workflow_knowledge.py'},
                {'action': 'Approve a submitted update', 'requires': 'is_creator_manager (project.created_by_id == user.id)', 'leads_to': 'review_submission (approve)'},
                {'action': 'Request corrections on a submitted update', 'requires': 'is_creator_manager OR user.role in (HR, SENIOR_HR)', 'leads_to': 'review_submission (correct)'},
            ],
            'related_pages': ['list', 'edit_project', 'delete_project', 'my_history'],
        },

        # -------------------------------------------------------------
        'edit_project': {
            'label': 'Edit Project',
            'route': 'projects:edit_project',
            'template': 'projects/edit_project.html',
            'source': 'projects/views.py::edit_project',
            'purpose': "Update a project's core details.",
            'who_can_submit': "_can_manage_project(user) i.e. user.is_manager(). HR, Admin, and Employees cannot reach this action (redirected with an error).",
            'form_fields': ['name (required)', 'description', 'start_date', 'end_date', 'lead', 'daily_target (whole number)', 'requirements_file'],
            'extra_rules': [
                'Changing daily_target immediately pushes the new target onto every currently assigned member\'s today entry in the production tracker (creating one if needed).',
            ],
            'related_pages': ['project_detail'],
        },

        # -------------------------------------------------------------
        'delete_project': {
            'label': 'Delete Project',
            'route': 'projects:delete_project',
            'template': 'projects/confirm_delete_project.html',
            'source': 'projects/views.py::delete_project',
            'purpose': "Permanently delete a project after a confirmation step.",
            'who_can_submit': "_can_manage_project(user) i.e. user.is_manager().",
            'related_pages': ['project_detail', 'list'],
        },

        # -------------------------------------------------------------
        'my_history': {
            'label': 'My Project History',
            'route': 'projects:my_history',
            'template': 'projects/my_history.html',
            'source': 'projects/views.py::my_project_history',
            'purpose': "Shows the logged-in user's own project assignments (past and current).",
            'visibility': 'Any logged-in user — shows only their own ProjectAssignment records.',
            'related_pages': ['list', 'project_detail'],
        },

        # -------------------------------------------------------------
        'approvals': {
            'label': 'Project Approvals (legacy)',
            'route': 'projects:approvals',
            'template': 'projects/approvals.html',
            'source': 'projects/views.py::project_approvals, approve_project, reject_project',
            'purpose': "Legacy PENDING-project approval queue. Kept for compatibility but effectively idle — create_project now publishes projects directly as APPROVED, so this queue is normally empty.",
            'who_can_submit': "hr_or_admin_required to view; hr_only_required to approve/reject.",
            'related_pages': ['list'],
        },
    },

    # ---------------------------------------------------------------------
    # Paths that look plausible but do NOT exist in this application —
    # kept here so the chatbot can give a grounded negative answer instead
    # of guessing. Add to this list whenever a user asks about something
    # that isn't in urlpatterns/templates above.
    # ---------------------------------------------------------------------
    'nonexistent': [
        {
            'asked_about': 'Project Tracker -> My History (under Projects)',
            'reality': (
                "There is no 'Project Tracker' sidebar path under Projects. "
                "The user's own project history is at Projects -> My Project History "
                "(projects:my_history). Hourly/production tracking is a separate "
                "feature (production_tracker app), not part of Projects."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# FEATURE: Attendance  (app: attendance)
# nav key: 'attendance_punch' / 'my_attendance' (see chatbot/navigation.py)
# source: attendance/urls.py, attendance/views.py
# ---------------------------------------------------------------------------

ATTENDANCE = {
    'feature': 'Attendance',
    'app': 'attendance',
    'nav_key': 'attendance',

    'pages': {

        'punch': {
            'label': 'Punch In/Out',
            'route': 'attendance:punch',
            'source': 'attendance/views.py::punch',
            'purpose': "Record daily attendance — punch in, punch out, start break, end break.",
            'visibility': "@login_required — any logged-in user, for their own attendance only.",
            'actions': [
                {'action': 'Punch in', 'requires': "no in_time recorded yet today"},
                {'action': 'Start break', 'requires': 'already punched in, not yet punched out, no break already running'},
                {'action': 'End break', 'requires': 'a break is currently running'},
                {'action': 'Punch out', 'requires': 'already punched in and not yet punched out'},
            ],
            'extra_rules': [
                "Punching out while on break automatically ends the break and adds its duration to total_break_minutes.",
                "Every punch action regenerates that user's monthly attendance sheet for the current month.",
            ],
            'related_pages': ['my_attendance_view'],
        },

        'my_attendance_view': {
            'label': 'My Attendance',
            'route': 'attendance:my_attendance_view',
            'source': 'attendance/views.py::my_attendance_view, download_my_attendance_view',
            'purpose': "Unified weekly / monthly / yearly view of the logged-in user's own attendance, with a downloadable summary.",
            'visibility': "@login_required — own records only.",
            'related_pages': ['punch', 'my_monthly_attendance_archive'],
        },

        'my_monthly_attendance_archive': {
            'label': 'My Monthly Attendance Archive',
            'route': 'attendance:my_monthly_attendance_archive',
            'source': 'attendance/views.py::my_monthly_attendance_archive',
            'purpose': "Archive of the logged-in user's past monthly attendance sheets.",
            'visibility': "@login_required — own records only.",
            'related_pages': ['my_attendance_view'],
        },

        'reports': {
            'label': 'Attendance Reports',
            'route': 'attendance:reports',
            'source': 'attendance/views.py::attendance_reports',
            'purpose': "Company-wide attendance records (most recent 200) and a late-punch count.",
            'visibility': "@hr_or_admin_required — HR, Senior HR, and Admin only.",
            'related_pages': ['employee_attendance', 'team_monthly_attendance'],
        },

        'employee_attendance_view': {
            'label': "Employee Attendance (HR view)",
            'route': 'attendance:employee_attendance_view',
            'source': 'attendance/views.py::employee_attendance_view, download_employee_attendance_view, monthly_attendance_archive_hr',
            'purpose': "HR/Admin view of one specific employee's weekly/monthly/yearly attendance, with download and a monthly archive.",
            'visibility': "@hr_or_admin_required.",
            'related_pages': ['reports', 'update_monthly_overrides'],
        },

        'team_monthly_attendance': {
            'label': 'Team Monthly Attendance (Monthly Register)',
            'route': 'attendance:team_monthly_attendance',
            'source': 'attendance/views.py::team_monthly_attendance, download_team_monthly_attendance',
            'purpose': "Combined monthly attendance register for ALL active employees, downloadable.",
            'visibility': "@hr_or_admin_required — despite being labelled 'Team' this is company-wide, not scoped to one manager's direct reports.",
            'related_pages': ['reports'],
        },

        'active_attendance_today': {
            'label': 'Active Today',
            'route': 'attendance:active_attendance_today',
            'source': 'attendance/views.py::active_attendance_today',
            'purpose': "Who is currently active (punched in, not out) today, cross-referenced against who's on approved leave today.",
            'visibility': "@hr_or_admin_required.",
            'related_pages': ['reports'],
        },

        'update_monthly_overrides': {
            'label': 'Monthly Attendance Overrides',
            'route': 'attendance:update_monthly_overrides',
            'source': 'attendance/views.py::update_monthly_overrides',
            'purpose': "Manually override an employee's CL Quota and Total (PH/Sunday) count for one specific month — e.g. to account for festival holidays that aren't auto-detected.",
            'who_can_submit': "@hr_only_required — HR/Senior HR only. Admin is view-only here, same as elsewhere in the app.",
            'form_fields': ['year', 'month', 'cl_quota_override', 'ph_sunday_override'],
            'related_pages': ['employee_attendance_view'],
        },

        'overtime_permissions': {
            'label': 'Overtime Permissions (management)',
            'route': 'attendance:overtime_permissions',
            'source': 'attendance/views.py::overtime_permissions, revoke_overtime_permission, _overtime_manageable_projects',
            'purpose': "Grant/manage OT & Sunday-work permissions for a team; the list of manageable projects depends on the viewer's role.",
            'visibility': (
                "@login_required, but only useful for HR/SENIOR_HR (all projects), Managers "
                "(projects they manage/lead), or Admin — an Employee reaching this page sees nothing to manage."
            ),
            'actions': [
                {'action': 'Revoke a permission', 'requires': 'role in (HR, SENIOR_HR) OR the requester manages/leads the permission\'s project'},
            ],
            'related_pages': ['my_overtime_permissions', 'approve_overtime_request'],
        },

        'my_overtime_permissions': {
            'label': 'My Overtime / Sunday Permissions',
            'route': 'attendance:my_overtime_permissions',
            'source': 'attendance/views.py::my_overtime_permissions',
            'purpose': "Employee's own OT/Sunday/Holiday hub — submit new OT requests here, and track both submitted requests and any permissions already granted.",
            'visibility': "@login_required — own records; project choices limited to projects the user is currently assigned to or leads.",
            'form_fields': ['project (must be one of the user\'s current/led projects)', 'date', 'request_type', 'start_time', 'end_time', 'reason'],
            'extra_rules': [
                'If the requester is a Manager, the request routes straight to PENDING_HR.',
                'Otherwise it routes to PENDING_MANAGER if the employee has a manager, else straight to PENDING_HR.',
            ],
            'related_pages': ['overtime_permissions'],
        },

        'approve_overtime_request': {
            'label': 'Approve/Reject OT Request (Manager step)',
            'route': 'attendance:approve_overtime_request',
            'source': 'attendance/views.py::approve_overtime_request, reject_overtime_request',
            'purpose': "First-stage approval of an employee's OT/Sunday/Holiday request.",
            'who_can_submit': "The request's direct manager only (user.is_manager() AND manager.id == user.id), and only while status is PENDING_MANAGER.",
            'related_pages': ['approve_overtime_request_hr'],
        },

        'approve_overtime_request_hr': {
            'label': 'Approve/Reject OT Request (HR step)',
            'route': 'attendance:approve_overtime_request_hr',
            'source': 'attendance/views.py::approve_overtime_request_hr, reject_overtime_request_hr',
            'purpose': "Final HR approval stage of an OT/Sunday/Holiday request.",
            'who_can_submit': "role in (HR, SENIOR_HR) only.",
            'related_pages': ['approve_overtime_request'],
        },
    },

    'nonexistent': [
        {
            'asked_about': 'Biometric attendance / fingerprint punch',
            'reality': (
                "There is no biometric device integration. Attendance is tracked entirely "
                "through HRMS login-page punch in/out timestamps (attendance:punch)."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# FEATURE: Leaves  (app: leaves)
# nav key: 'my_leaves' / 'leave_approvals' (see chatbot/navigation.py)
# source: leaves/urls.py, leaves/views.py
# ---------------------------------------------------------------------------

LEAVES = {
    'feature': 'Leaves',
    'app': 'leaves',
    'nav_key': 'leaves',

    'pages': {

        'my_leaves': {
            'label': 'My Leaves',
            'route': 'leaves:my_leaves',
            'source': 'leaves/views.py::my_leaves',
            'purpose': "Apply for Permission, Leave, or WFH, and see your own leave balance and request history.",
            'visibility': "@login_required — own records only.",
            'displays': ['Current leave balance', 'Your past requests', 'Leave type options'],
            'actions': [
                {'action': 'Apply for Permission', 'requires': 'permission_date is required'},
                {'action': 'Apply for Leave', 'requires': 'leave_type, start_date and end_date are required'},
                {'action': 'Apply for WFH', 'requires': 'start_date and end_date are required'},
            ],
            'extra_rules': [
                "Only full-day 'LEAVE' type requests deduct from the leave balance — Permission and WFH requests do not.",
                "PF-applicable employees start with a 0 balance that accrues 1 day/month; non-PF employees start with a flat 12.",
                "A new request auto-routes to PENDING_MANAGER or straight to PENDING_HR depending on the requester's role (see leave_approvals workflow).",
            ],
            'related_pages': ['leave_approvals', 'notifications', 'check_balance'],
        },

        'leave_approvals': {
            'label': 'Leave Approvals',
            'route': 'leaves:approvals',
            'source': 'leaves/views.py::leave_approvals',
            'purpose': "Review pending leave/permission/WFH requests. What's shown depends entirely on the viewer's role — this is not one queue.",
            'visibility': (
                "HR/SENIOR_HR: all PENDING_HR requests for their branch (forwarded by a manager), plus a read-only record of every "
                "APPROVED/REJECTED request in the branch. "
                "ADMIN: every request in the branch, view-only, plus who's on leave today. "
                "CTO: PENDING_MANAGER requests for the Software department (CTO acts as that department's manager). "
                "GENERAL_MANAGER: PENDING_MANAGER requests from their own direct team only (excluding Project Managers, who route to COO); "
                "read-only visibility into their PMs' leave status. "
                "MANAGER (incl. Project Manager): PENDING_MANAGER requests from their own team only. "
                "Project Lead / Team Leader (no formal manager role): read-only view of their project team's requests, no approve/reject authority. "
                "Anyone else: 'You do not have permission to view this page', redirected to Dashboard."
            ),
            'related_pages': ['my_leaves', 'review_leave'],
        },

        'review_leave': {
            'label': 'Approve/Reject a Leave Request',
            'route': 'leaves:review_leave',
            'source': 'leaves/views.py::review_leave',
            'purpose': "Two-stage approval action (approve/reject) on one specific leave request.",
            'who_can_submit': (
                "Stage PENDING_MANAGER (final at this stage only in the sense that it moves to HR next): only the request's "
                "current_approver (or, if unset, get_manager()) — must be a Manager or the CTO. "
                "Stage PENDING_HR (final decision): only role HR/SENIOR_HR, cannot review their own request, and if the request has "
                "a target_hr set, only that specific HR user can act. "
                "ADMIN can never approve/reject — view-only everywhere in this feature."
            ),
            'extra_rules': [
                "Manager approval moves status to PENDING_HR and notifies the employee; rejection sets REJECTED immediately.",
                "HR approval sets APPROVED and triggers the balance deduction (_apply_balance_deduction); HR rejection sets REJECTED.",
            ],
            'related_pages': ['leave_approvals'],
        },

        'notifications': {
            'label': 'Leave Notifications',
            'route': 'leaves:notifications',
            'source': 'leaves/views.py::notifications',
            'purpose': "The logged-in user's own leave-related notifications (approvals, rejections, forwards). Opening this page marks all as read.",
            'visibility': "@login_required — own notifications only.",
            'related_pages': ['my_leaves'],
        },

        'check_balance': {
            'label': 'Check Leave Balance (API)',
            'route': 'leaves:check_balance',
            'source': 'leaves/views.py::check_leave_balance',
            'purpose': "JSON API used by the Apply-for-Leave form to show available balance, days used this month, pending days, and projected LOP days for a chosen date range, before the employee submits.",
            'visibility': "@login_required — own balance only. GET only.",
            'related_pages': ['my_leaves'],
        },

        'appreciation_report': {
            'label': 'Appreciation Report',
            'route': 'leaves:appreciation_report',
            'source': 'leaves/views.py::appreciation_report',
            'purpose': "Lists projects where NO team member took any leave in a selected month/year — a 'perfect attendance team' report, filterable by month and year.",
            'who_can_submit': "role in (HR, SENIOR_HR, GENERAL_MANAGER, COO, ADMIN) — everyone else gets 'Permission denied.'",
            'related_pages': ['leave_approvals'],
        },
    },

    'nonexistent': [
        {
            'asked_about': 'Separate Casual Leave (CL) / Sick Leave (SL) balances',
            'reality': (
                "There is only ONE combined LeaveBalance per employee — leave_type is recorded on each "
                "request for record-keeping, but CL/EL/Sick all draw from the same single pool, matching "
                "the Leave Policy document (see SwiftProsys_Leave_Policy.docx)."
            ),
        },
        {
            'asked_about': 'A Reporting Manager / TL manual approval step for Permission or WFH',
            'reality': (
                "Permission and WFH requests still go through the same PENDING_MANAGER -> PENDING_HR "
                "flow as Leave in the code (initial_status()) — the same review_leave action handles all "
                "three request_types, there is no separate manual step outside the system."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# FEATURE: Employees (HR letters + WFH directory)  (app: employees)
# nav key: 'issue_letter' / 'wfh_directory' (see chatbot/navigation.py NAV)
# source: employees/urls.py, employees/views.py, employees/forms.py,
#         employees/models.py, templates/employees/*.html
# ---------------------------------------------------------------------------

EMPLOYEES = {
    'feature': 'Employees (HR Letters & WFH)',
    'app': 'employees',
    'nav_key': 'issue_letter',

    'pages': {

        'issue_letter': {
            'label': 'Issue Letter',
            'route': 'employees:issue_letter',
            'template': 'employees/issue_letter.html',
            'source': 'employees/views.py::issue_letter, employees/forms.py::IssueLetterForm',
            'purpose': (
                "HR issues an official letter to one or more employees in one go. "
                "Each selected employee gets their own copy of the same uploaded file, "
                "which then shows up on their own My Letters page."
            ),
            'who_can_submit': (
                "@hr_only_required (core.decorators) — role must be HR or SENIOR_HR. "
                "Admin is redirected back with 'Admin has view-only access. Only HR can perform this action.' "
                "HR/Admin/Senior HR accounts themselves are never valid recipients — the employee "
                "picker excludes ADMIN, HR, and SENIOR_HR roles."
            ),
            'form_fields': [
                'doc_type (Letter Type — one of: Offer, Appointment, Company Experience, Promotion, Relieving, Increment Letter)',
                'employees (checkbox multi-select — issue the same letter to several people at once)',
                'file (single file upload, required)',
            ],
            'extra_rules': [
                'The employee picker is scoped to the HR user\'s active branch, if one is set.',
                'One uploaded file is copied individually to each selected recipient (separate EmployeeDocument rows), not shared by reference.',
                'The page also shows a "Recently Issued" list of the last 15 HR-type letters issued (any branch/recipient).',
                'This replaced the old per-employee-profile document upload method for HR letter types — that old form/URL no longer exists.',
            ],
            'displays': ['Issue Letter form', 'Recently Issued list (last 15)'],
            'related_pages': ['my_letters'],
        },

        # -------------------------------------------------------------
        'my_letters': {
            'label': 'My Letters',
            'route': 'employees:my_letters',
            'template': 'employees/my_letters.html',
            'source': 'employees/views.py::my_letters',
            'purpose': "Every employee/manager's own page for official letters HR has issued to them.",
            'visibility': "@login_required — shows only the logged-in user's own HR-type documents.",
            'extra_rules': ['Opening this page clears the "new letter" sidebar notification badge.'],
            'related_pages': ['issue_letter'],
        },

        # -------------------------------------------------------------
        'wfh_directory': {
            'label': 'Work From Home',
            'route': 'employees:wfh_directory',
            'template': 'employees/wfh_directory.html',
            'source': 'employees/views.py::wfh_directory',
            'purpose': (
                "Lists everyone currently Work From Home, right now, in the viewer's active branch."
            ),
            'who_can_submit': (
                "@hr_or_admin_required (core.decorators) — role must be HR, SENIOR_HR, ADMIN, or COO. "
                "Note: the sidebar link itself ('Work From Home', in the Employees section) is only shown "
                "to HR/SENIOR_HR (templates/core/base.html) — Admin/COO can still open the page by direct URL, "
                "they just have no sidebar shortcut to it."
            ),
            'displays': [
                'Employees permanently flagged work_type=WFH on their profile (status ACTIVE) — labeled "Profile (Permanent)"',
                "Employees with an APPROVED day-based WFH leave request covering today — labeled with the request's date range",
            ],
            'extra_rules': [
                'Combines two different sources (permanent profile flag + leaves app approved WFH requests covering today) into one deduplicated, sorted list.',
                'If there is no active branch on the request, the list is empty.',
            ],
            'related_pages': ['leave_approvals'],
        },
    },

    'nonexistent': [
        {
            'asked_about': 'Per-employee-profile document upload for HR letter types (old method)',
            'reality': (
                "This was replaced by the dedicated Issue Letter page (employees:issue_letter), "
                "which issues to one or many employees at once. The old per-profile upload URL/view "
                "for HR letter types no longer exists."
            ),
        },
    ],
}



# ---------------------------------------------------------------------------
# FEATURE: Motivational Posts  (app: core)
# nav key: 'motivational_posts'
# source: core/urls.py, core/views.py::manage_motivational_post,
#         core/models.py::MotivationalPost,
#         templates/core/manage_motivational_post.html,
#         templates/components/motivational_modal.html
# ---------------------------------------------------------------------------

MOTIVATIONAL = {
    'feature': 'Motivational Posts',
    'app': 'core',
    'nav_key': 'motivational_posts',

    'pages': {
        'manage_motivational_post': {
            'label': 'Motivational Posts',
            'route': 'core:manage_motivational_post',
            'template': 'core/manage_motivational_post.html',
            'source': 'core/views.py::manage_motivational_post',
            'purpose': (
                "HR, Admin and COO upload the motivational post (title, image and/or quote) "
                "that employees see as a pop-up on their dashboard."
            ),
            'who_can_submit': (
                "Role must be HR, SENIOR_HR, ADMIN or COO. Everyone else is redirected to the "
                "dashboard with 'Access denied.'"
            ),
            'form_fields': ['title (optional)', 'image (optional)', 'quote (optional)', 'is_active'],
            'extra_rules': [
                'Only ONE post can be active at a time — saving an active post automatically deactivates the previous active one.',
                'Employees do not manage posts; they only see the currently active post as a pop-up on the dashboard (and COO dashboard).',
                'The pop-up is shown once per day per user — closing it hides that same post until the next day.',
            ],
            'displays': ['Upload form', 'List of all posts (newest first)'],
        },
    },
}

# ---------------------------------------------------------------------------
# Registry so matcher.py can look up a feature's page knowledge by nav_key
# without importing every feature module directly.
# ---------------------------------------------------------------------------
PAGE_KNOWLEDGE_REGISTRY = {
    'projects': PROJECTS,
    'attendance': ATTENDANCE,
    'leaves': LEAVES,
    'employees': EMPLOYEES,
    'motivational_posts': MOTIVATIONAL,
}


def get_feature(nav_key):
    """Returns the page-knowledge dict for a feature, or None if this
    feature hasn't been added to the registry yet — callers must treat
    None as 'not confirmed', never fall back to guessing."""
    return PAGE_KNOWLEDGE_REGISTRY.get(nav_key)


def get_page(nav_key, page_key):
    feature = get_feature(nav_key)
    if not feature:
        return None
    return feature['pages'].get(page_key)


def _fmt_list(items):
    return "; ".join(str(i) for i in items) if items else ""


def to_rag_documents():
    """Flattens every registered feature/page into one text chunk per page,
    formatted for semantic retrieval (explicit labels so a loosely-phrased
    question like "can a PM see other teams' projects" still lands on the
    right chunk via embedding similarity, not exact keyword match).

    Returns a list of (text, metadata) tuples. Called by the
    build_rag_index management command — see chatbot/rag_engine.py's
    docstring for the full list of indexed sources. Only features actually
    present in PAGE_KNOWLEDGE_REGISTRY are emitted, so partially-audited
    apps never produce invented/blank chunks.
    """
    docs = []
    for nav_key, feature in PAGE_KNOWLEDGE_REGISTRY.items():
        feature_label = feature.get('feature', nav_key)
        for page_key, page in feature.get('pages', {}).items():
            lines = [
                f"[Feature: {feature_label}] [Page: {page.get('label', page_key)}] "
                f"[Route: {page.get('route', '')}] [Source: {page.get('source', '')}]",
            ]
            if page.get('purpose'):
                lines.append(f"Purpose: {page['purpose']}")
            if page.get('visibility'):
                lines.append(f"Who can view this page: {page['visibility']}")
            if page.get('who_can_submit'):
                lines.append(f"Who can use/submit this: {page['who_can_submit']}")
            if page.get('displays'):
                lines.append(f"What it shows: {_fmt_list(page['displays'])}")
            if page.get('form_fields'):
                lines.append(f"Form fields: {_fmt_list(page['form_fields'])}")
            if page.get('extra_rules'):
                lines.append(f"Rules: {_fmt_list(page['extra_rules'])}")
            if page.get('actions'):
                action_lines = []
                for a in page['actions']:
                    bit = a.get('action', '')
                    if a.get('requires'):
                        bit += f" (requires: {a['requires']})"
                    action_lines.append(bit)
                lines.append(f"Actions available on this page: {_fmt_list(action_lines)}")
            if page.get('related_pages'):
                lines.append(f"Related pages: {_fmt_list(page['related_pages'])}")
            text = "\n".join(lines)
            docs.append((text, {
                "source": f"page_knowledge:{nav_key}:{page_key}",
                "source_type": "page_knowledge",
                "source_name": f"{feature_label} — {page.get('label', page_key)}",
            }))

        for item in feature.get('nonexistent', []):
            text = (
                f"[Feature: {feature_label}] [Does NOT exist] "
                f"Asked about: {item.get('asked_about', '')}\n"
                f"Reality: {item.get('reality', '')}"
            )
            docs.append((text, {
                "source": f"page_knowledge:{nav_key}:nonexistent",
                "source_type": "page_knowledge",
                "source_name": f"{feature_label} — nonexistent path",
            }))
    return docs
