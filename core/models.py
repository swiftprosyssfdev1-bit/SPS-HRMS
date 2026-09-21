from django.contrib.auth.models import AbstractUser
from django.db import models
from core.validators import COUNTRY_CODES


class Branch(models.Model):
    """A physical office location. Like Department, a Branch can have a
    designated Manager overseeing it."""
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True)
    manager = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='managed_branches'
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"


class Department(models.Model):
    name = models.CharField(max_length=100)
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.CASCADE, related_name='departments'
    )
    manager = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='managed_departments'
    )
    # When set (typically for a branch's "Production" department), every
    # employee HR assigns to this department is automatically added as a
    # current team member on this project — so the project's Manager/Lead
    # sees them without HR doing a separate step in Projects.
    default_project = models.ForeignKey(
        'projects.Project', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='auto_assign_departments',
        help_text="Employees added to this department are automatically added to this project's team."
    )
    # Explicit flag for "is this a Quality/QA department" instead of
    # guessing from the department's name (e.g. `'Quality' in name`),
    # which false-positives on any department whose name happens to
    # contain that word (e.g. "IT Quality Compliance") and gives HR no
    # way to correct a wrongly-flagged department. This is the single
    # source of truth for Work Type defaulting, QA target routing, etc.
    is_qa = models.BooleanField(
        default=False,
        verbose_name='QA Department',
        help_text="Employees in this department get Work Type = QA and are counted for QA targets."
    )

    class Meta:
        ordering = ['branch__name', 'name']
        unique_together = ('name', 'branch')

    def __str__(self):
        if self.branch:
            return f"{self.name} ({self.branch.code})"
        return self.name


class User(AbstractUser):
   
    ROLE_COO = 'COO'
    ROLE_CTO = 'CTO'
    ROLE_ADMIN = 'ADMIN'
    ROLE_SENIOR_HR = 'SENIOR_HR'
    ROLE_HR = 'HR'
    ROLE_MANAGER = 'MANAGER'
    ROLE_PROJECT_MANAGER = 'PROJECT_MANAGER'
    ROLE_GENERAL_MANAGER = 'GENERAL_MANAGER'
    ROLE_EMPLOYEE = 'EMPLOYEE'
    ROLE_CHOICES = [
        (ROLE_COO, 'COO'),
        (ROLE_CTO, 'CTO'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_SENIOR_HR, 'GM (HR)'),
        (ROLE_HR, 'HR'),
        (ROLE_MANAGER, 'Manager'),
        (ROLE_PROJECT_MANAGER, 'Project Manager'),
        (ROLE_GENERAL_MANAGER, 'General Manager'),
        (ROLE_EMPLOYEE, 'Employee'),
    ]
    is_senior_hr = models.BooleanField(default=False)
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default=ROLE_EMPLOYEE)
    employee_id = models.CharField(max_length=20, unique=True, null=True, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name='employees')
    manager = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='team_members')
    date_joined_company = models.DateField(null=True, blank=True)

    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name='staff'
    )
    accessible_branches = models.ManyToManyField(
        'core.Branch', blank=True, related_name='hr_admins',
        help_text="Branches this HR user can view/manage. Ignored for Admin — Admin always has access to every branch."
    )

    # Only ever set by Admin (see toggle_branch_admin_access). An HR user
    # with this True can switch between and see every branch. Every other
    # HR is locked to their own assigned branch everywhere branch
    # filtering applies — they never see a switcher at all.
    can_access_all_branches = models.BooleanField(default=False)

    phone_country_code = models.CharField(max_length=5, choices=COUNTRY_CODES, default='+91')
    phone = models.CharField(max_length=10, blank=True)

    

    def is_hr_or_admin(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_HR, self.ROLE_SENIOR_HR)

    def is_manager(self):
        return self.role in (self.ROLE_MANAGER, self.ROLE_PROJECT_MANAGER, self.ROLE_GENERAL_MANAGER, self.ROLE_CTO)

    def is_coo(self):
        return self.role == self.ROLE_COO

    def is_cto(self):
        return self.role == self.ROLE_CTO

    def is_software_dept(self):
        """True if this user belongs to a department literally named
        'Software' — used to route their leave/permission/increment
        requests to the CTO instead of the normal chain."""
        return bool(self.department and self.department.name.strip().lower() == 'software')

    def __str__(self):
        return self.get_full_name() or self.username

class MotivationalPost(models.Model):
    title = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to='motivational_posts/', null=True, blank=True)
    quote = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey('core.User', on_delete=models.SET_NULL, null=True, related_name='uploaded_posts')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.is_active:
            MotivationalPost.objects.filter(is_active=True).update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title or 'Quote'} - {self.created_at.date()}"
