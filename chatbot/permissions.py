"""
Centralized role detection and data scope helpers for the chatbot.
Every intent uses get_data_scope(user) instead of scattering
authorization logic across 30+ functions, as recommended in Implementation.txt.
"""


def get_role(user):
    return getattr(user, 'role', 'EMPLOYEE').upper()


def get_data_scope(user):
    """Maps a user's role to a scope string that intents use to decide
    what data to fetch and what response to return."""
    role = get_role(user)
    if role == 'EMPLOYEE':
        return 'SELF'
    if role == 'MANAGER':
        return 'TEAM'
    if role == 'PROJECT_MANAGER':
        return 'PROJECT'
    if role == 'GENERAL_MANAGER':
        return 'BRANCH'
    if role == 'HR':
        return 'HR'
    if role == 'SENIOR_HR':
        return 'HR'
    if role == 'COO':
        return 'MANAGEMENT'
    if role == 'CTO':
        return 'TECHNICAL'
    if role == 'ADMIN':
        return 'ADMIN'
    return 'SELF'


def can_see_team_data(user):
    scope = get_data_scope(user)
    return scope in ('TEAM', 'PROJECT', 'BRANCH', 'HR', 'MANAGEMENT', 'TECHNICAL', 'ADMIN')


def can_see_company_data(user):
    scope = get_data_scope(user)
    return scope in ('HR', 'MANAGEMENT', 'ADMIN')
