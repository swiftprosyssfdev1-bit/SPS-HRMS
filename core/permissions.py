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
