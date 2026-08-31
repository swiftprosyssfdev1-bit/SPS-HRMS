"""
Workflow knowledge layer — how a multi-step action actually happens,
including the real permission check and the real next step, taken
directly from the corresponding view function. No step is included
unless it is backed by a specific line of code (noted in 'source').

Each workflow is a linear or branching sequence of steps. matcher.py
should render these as a numbered sequence rather than re-describing
the flow in hardcoded prose in responses.py.
"""

PROJECTS_WORKFLOWS = {

    'submit_update': {
        'label': 'Submit a Project Update',
        'source': 'projects/views.py::submit_project_update, review_project_submission',
        'steps': [
            {'step': 'Open Projects', 'route': 'projects:list'},
            {'step': 'Open the relevant project', 'route': 'projects:project_detail'},
            {
                'step': 'Permission check: only the project lead can submit',
                'check': 'project.lead_id == request.user.id',
                'on_fail': "\"Only the project lead can submit updates for review.\" (redirect back to project_detail)",
            },
            {
                'step': 'Fill and submit the Submit Update form',
                'form_fields': ['notes (textarea)', 'file (optional attachment)'],
                'validation': 'At least one of notes or file must be provided.',
            },
            {'step': 'A ProjectSubmission is created with status=PENDING_REVIEW'},
            {
                'step': 'Review by the project\'s creating Manager or HR',
                'branches': [
                    {
                        'who': "The Manager who created the project (project.created_by_id == user.id)",
                        'can_do': 'Approve (final) or send back for corrections',
                    },
                    {
                        'who': 'HR / SENIOR_HR',
                        'can_do': 'Send back for corrections only — cannot give final approval',
                    },
                    {
                        'who': 'Admin',
                        'can_do': 'View-only — cannot approve or request corrections on this feature',
                    },
                ],
            },
            {'step': 'Submission status becomes APPROVED, or stays as NEEDS_CORRECTION with feedback from the reviewer'},
        ],
    },

    'create_project': {
        'label': 'Create / Publish a Project',
        'source': 'projects/views.py::create_project',
        'steps': [
            {
                'step': 'Permission check: only a Manager can create a project',
                'check': 'request.user.is_manager()',
                'on_fail': "\"Only Managers can create projects.\" (redirect to projects:list)",
            },
            {
                'step': "If the creator is a General Manager, they must first choose a Project Manager",
                'condition': "request.user.role == GENERAL_MANAGER",
                'form_fields': ['project_manager (must be a PROJECT_MANAGER in the same branch)'],
            },
            {
                'step': 'Fill the Create Project form',
                'form_fields': ['name (required)', 'description', 'requirements_file', 'start_date', 'end_date', 'employees', 'lead'],
                'validation': (
                    'For a non-GM Manager: employees and lead are required, and the lead must be one of '
                    'the selected employees. For a GM: employees/lead are optional. Only role=EMPLOYEE '
                    'users can ever be selected as employees or lead.'
                ),
            },
            {'step': 'Project is created directly with status=APPROVED — no separate approval step in current flow, immediately visible to the assigned team, HR, and Admin'},
        ],
    },

    'complete_project': {
        'label': 'Mark a Project as Completed',
        'source': 'projects/views.py::complete_project',
        'steps': [
            {'step': 'Open the project detail page', 'route': 'projects:project_detail'},
            {
                'step': 'Permission check',
                'check': 'request.user.is_manager() AND project.status != COMPLETED',
                'on_fail': "\"Only Managers can mark a project as completed.\"",
            },
            {'step': 'Click "Mark Completed" (POST to complete_project)'},
            {'step': 'project.status becomes COMPLETED; project.completed_by and completed_at are recorded'},
            {'step': 'Project moves into the Completed section on the Project List'},
        ],
    },

    'set_project_target': {
        'label': 'Set the Project Daily Target',
        'source': 'projects/views.py::set_project_target',
        'steps': [
            {'step': 'Open the project detail page', 'route': 'projects:project_detail'},
            {
                'step': 'Permission check',
                'check': 'project.lead_id == user.id OR project.manager_id == user.id OR project.gm_id == user.id',
                'on_fail': "\"Permission denied.\"",
            },
            {'step': 'Enter a new Daily Target value and submit'},
            {'step': 'If changed, the new target is pushed onto every currently assigned member\'s production-tracker entry for today'},
        ],
    },
}


WORKFLOW_KNOWLEDGE_REGISTRY = {
    'projects': PROJECTS_WORKFLOWS,
}


def get_workflow(nav_key, workflow_key):
    """Returns the workflow dict, or None if not confirmed — callers must
    not fabricate a workflow when this returns None."""
    feature_workflows = WORKFLOW_KNOWLEDGE_REGISTRY.get(nav_key)
    if not feature_workflows:
        return None
    return feature_workflows.get(workflow_key)
