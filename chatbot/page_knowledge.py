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


# Registry so matcher.py can look up a feature's page knowledge by nav_key
# without importing every feature module directly.
PAGE_KNOWLEDGE_REGISTRY = {
    'projects': PROJECTS,
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
