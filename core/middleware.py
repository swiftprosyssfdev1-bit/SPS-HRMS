from django.utils.deprecation import MiddlewareMixin
from core.utils import get_active_branch

class ActiveBranchMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if request.path.startswith('/static/') or request.path.startswith('/media/') or request.path.startswith('/admin/'):
            return None

        if hasattr(request, 'user') and request.user.is_authenticated:
            request.active_branch = get_active_branch(request)
        else:
            request.active_branch = None
