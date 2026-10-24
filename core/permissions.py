def can_view_all_branches(user):
    """Can view data for all branches (e.g. Admin, COO, GM HR)."""
    if not user.is_authenticated:
        return False
    return user.role in ('ADMIN', 'COO', 'SENIOR_HR') or getattr(user, 'can_access_all_branches', False)

def can_approve_leaves(user):
    """Can act as a final approver for leaves (HR roles)."""
    if not user.is_authenticated:
        return False
    return user.role in ('HR', 'SENIOR_HR')

def can_give_increments(user):
    """Can initiate an increment request directly (HR roles)."""
    if not user.is_authenticated:
        return False
    return user.role in ('HR', 'SENIOR_HR')

def can_approve_increments(user):
    """Can give final approval for increments (COO)."""
    if not user.is_authenticated:
        return False
    return user.role == 'COO'

def can_access_user_branch(acting_user, target_user):
    """Can acting_user view/manage target_user based on branch authorization."""
    if not acting_user.is_authenticated:
        return False
    if can_view_all_branches(acting_user):
        return True
    if acting_user.id == target_user.id:
        return True
    if not target_user.branch_id:
        return False
    allowed_ids = set()
    if acting_user.branch_id:
        allowed_ids.add(acting_user.branch_id)
    allowed_ids.update(acting_user.accessible_branches.values_list('id', flat=True))
    return target_user.branch_id in allowed_ids
