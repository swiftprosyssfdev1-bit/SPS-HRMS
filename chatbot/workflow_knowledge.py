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


ATTENDANCE_WORKFLOWS = {

    'request_overtime': {
        'label': 'Request OT / Sunday / Holiday Work',
        'source': 'attendance/views.py::my_overtime_permissions, approve_overtime_request, approve_overtime_request_hr',
        'steps': [
            {'step': 'Open My Overtime / Sunday Permissions', 'route': 'attendance:my_overtime_permissions'},
            {
                'step': 'Fill and submit the request form',
                'form_fields': ['project (must be one you are currently assigned to or lead)', 'date', 'request_type', 'start_time', 'end_time', 'reason'],
            },
            {
                'step': 'Routing decision on submit',
                'branches': [
                    {'who': 'If the requester is a Manager', 'can_do': 'Request goes straight to PENDING_HR'},
                    {'who': 'If the requester has a manager', 'can_do': 'Request goes to PENDING_MANAGER'},
                    {'who': 'If the requester has no manager', 'can_do': 'Request goes straight to PENDING_HR'},
                ],
            },
            {
                'step': 'Manager stage (only if PENDING_MANAGER)',
                'check': 'user.is_manager() AND manager.id == request.user.id AND status == PENDING_MANAGER',
                'on_fail': 'Manager cannot act on requests that are not theirs or not at this stage.',
            },
            {
                'step': 'HR stage (final)',
                'check': 'role in (HR, SENIOR_HR)',
                'on_fail': 'Only HR/Senior HR can give final approval.',
            },
            {'step': 'Request becomes APPROVED (creates/updates the OvertimePermission) or REJECTED with the reviewer recorded'},
        ],
    },

    'punch_cycle': {
        'label': 'Daily Punch In/Out/Break Cycle',
        'source': 'attendance/views.py::punch',
        'steps': [
            {'step': 'Open Punch In/Out', 'route': 'attendance:punch'},
            {'step': 'Punch In', 'check': 'no in_time recorded yet today'},
            {'step': 'Optionally Start Break / End Break any number of times', 'check': 'break can only start if punched in and not yet punched out; can only end if currently running'},
            {'step': 'Punch Out', 'check': 'punched in and not yet punched out'},
            {'step': "Punching out while a break is running auto-ends the break first and adds its duration to total_break_minutes"},
            {'step': "That user's monthly attendance sheet is regenerated after every action (in/out/break)"},
        ],
    },
}


LEAVES_WORKFLOWS = {

    'apply_and_approve_leave': {
        'label': 'Apply for Leave/Permission/WFH and Get It Approved',
        'source': 'leaves/views.py::my_leaves, leave_approvals, review_leave; leaves/models.py::LeaveRequest.initial_status',
        'steps': [
            {'step': 'Open My Leaves', 'route': 'leaves:my_leaves'},
            {
                'step': 'Choose request type and fill the form',
                'branches': [
                    {'who': 'Permission', 'can_do': 'requires permission_date (from_time/to_time/num_hours optional fields)'},
                    {'who': 'Leave', 'can_do': 'requires leave_type, start_date, end_date'},
                    {'who': 'WFH', 'can_do': 'requires start_date, end_date only, no leave_type'},
                ],
            },
            {'step': 'Submit — status is set by LeaveRequest.initial_status() based on the requester\'s role/hierarchy, e.g. PENDING_MANAGER (SLA deadline set to 24h) or routed straight to PENDING_HR/COO depending on role'},
            {
                'step': 'Manager-stage review (only if status is PENDING_MANAGER)',
                'check': '(user.is_manager() OR user.is_cto()) AND user.id == (leave.current_approver or leave.get_manager()).id',
                'on_fail': '"You cannot review this request."',
                'branches': [
                    {'who': 'Approve', 'can_do': 'status -> PENDING_HR, employee notified "approved by your manager and forwarded to HR"'},
                    {'who': 'Reject', 'can_do': 'status -> REJECTED (final), employee notified'},
                ],
            },
            {
                'step': 'HR-stage review (only if status is PENDING_HR) — final decision',
                'check': 'user.role in (HR, SENIOR_HR) AND leave.user_id != user.id AND (no target_hr set, or target_hr == user)',
                'on_fail': '"You cannot approve or reject your own request." or "This request was routed to a different HR colleague."',
                'branches': [
                    {'who': 'Approve', 'can_do': 'status -> APPROVED, leave balance is deducted (LEAVE type only), employee notified'},
                    {'who': 'Reject', 'can_do': 'status -> REJECTED, employee notified'},
                ],
            },
            {'step': 'Admin can view every request in the leave_approvals queue but has NO approve/reject action anywhere in this workflow'},
        ],
    },
}


WORKFLOW_KNOWLEDGE_REGISTRY = {
    'projects': PROJECTS_WORKFLOWS,
    'attendance': ATTENDANCE_WORKFLOWS,
    'leaves': LEAVES_WORKFLOWS,
}


def get_workflow(nav_key, workflow_key):
    """Returns the workflow dict, or None if not confirmed — callers must
    not fabricate a workflow when this returns None."""
    feature_workflows = WORKFLOW_KNOWLEDGE_REGISTRY.get(nav_key)
    if not feature_workflows:
        return None
    return feature_workflows.get(workflow_key)


def to_rag_documents():
    """Flattens every registered workflow into one text chunk per workflow,
    numbered-step prose so the embedding model can match it against a
    dynamically-phrased "how do I..." / "what happens after..." question.
    Called by the build_rag_index management command. Only features
    actually present in WORKFLOW_KNOWLEDGE_REGISTRY are emitted."""
    docs = []
    for nav_key, workflows in WORKFLOW_KNOWLEDGE_REGISTRY.items():
        for wf_key, wf in workflows.items():
            lines = [f"[Workflow: {wf.get('label', wf_key)}] [Source: {wf.get('source', '')}]"]
            for i, step in enumerate(wf.get('steps', []), start=1):
                bit = f"Step {i}: {step.get('step', '')}"
                if step.get('check'):
                    bit += f" (permission check: {step['check']})"
                if step.get('on_fail'):
                    bit += f" (if not allowed: {step['on_fail']})"
                if step.get('form_fields'):
                    bit += f" (fields: {'; '.join(step['form_fields'])})"
                lines.append(bit)
                for branch in step.get('branches', []):
                    lines.append(f"  - {branch.get('who', '')}: {branch.get('can_do', '')}")
            text = "\n".join(lines)
            docs.append((text, {
                "source": f"workflow_knowledge:{nav_key}:{wf_key}",
                "source_type": "workflow_knowledge",
                "source_name": f"{wf.get('label', wf_key)}",
            }))
    return docs
