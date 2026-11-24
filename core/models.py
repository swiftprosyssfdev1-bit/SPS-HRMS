from django.conf import settings
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

    # Display titles. `role` stays the PERMISSION level that every view,
    # template and approval chain checks. A title is a more specific name
    # shown to people, stored in `role_title`; the account gets exactly the
    # permissions of the base role it maps to.
    #   key                          -> (base permission role, label)
    ROLE_TITLES = {
        'HR_EXECUTIVE': (ROLE_HR, 'HR Executive'),
        'HR_ASSISTANT': (ROLE_HR, 'HR Assistant'),
        'ASSISTANT_PROJECT_MANAGER': (ROLE_PROJECT_MANAGER, 'Assistant Project Manager'),
        'PROJECT_LEAD': (ROLE_EMPLOYEE, 'Project Lead'),   # employee-level: a PM assigns projects to them
    }

    # What the Onboard / Role dropdowns offer. 'Manager' (same as General
    # Manager) and plain 'HR' (now HR Executive / HR Assistant) are no longer
    # selectable; accounts that already have them keep working unchanged.
    SELECTABLE_ROLE_CHOICES = [
        (ROLE_COO, 'COO'),
        (ROLE_CTO, 'CTO'),
        (ROLE_ADMIN, 'Admin'),
        (ROLE_SENIOR_HR, 'GM (HR)'),
        ('HR_EXECUTIVE', 'HR Executive'),
        ('HR_ASSISTANT', 'HR Assistant'),
        (ROLE_PROJECT_MANAGER, 'Project Manager'),
        ('ASSISTANT_PROJECT_MANAGER', 'Assistant Project Manager'),
        ('PROJECT_LEAD', 'Project Lead'),
        (ROLE_GENERAL_MANAGER, 'General Manager'),
        (ROLE_EMPLOYEE, 'Employee'),
    ]

    is_senior_hr = models.BooleanField(default=False)
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default=ROLE_EMPLOYEE)
    role_title = models.CharField(
        max_length=30, blank=True, default='',
        help_text="Display title (e.g. HR Assistant). Permissions always come from `role`.",
    )
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

    

    @classmethod
    def split_role_key(cls, key):
        """Dropdown value -> (permission role, role_title key or '')."""
        if key in cls.ROLE_TITLES:
            return cls.ROLE_TITLES[key][0], key
        return key, ''

    @property
    def role_key(self):
        """The dropdown value that represents this user's current role."""
        title = self.ROLE_TITLES.get(self.role_title)
        return self.role_title if title and title[0] == self.role else self.role

    def get_role_display(self):
        title = self.ROLE_TITLES.get(self.role_title)
        if title and title[0] == self.role:      # ignore a stale title
            return title[1]
        return dict(self.ROLE_CHOICES).get(self.role, self.role)

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


class ActivityLog(models.Model):
    """Audit trail of who added / changed what on employee records.
    Names are stored as snapshots so entries stay readable even if the
    actor or the employee is later deleted."""
    ADDED = 'ADDED'
    UPDATED = 'UPDATED'
    ROLE = 'ROLE'
    STATUS = 'STATUS'
    SALARY = 'SALARY'
    CREDENTIALS = 'CREDENTIALS'
    DOCUMENT = 'DOCUMENT'
    ONBOARDED = 'ONBOARDED'
    RESIGNATION = 'RESIGNATION'
    DELETED = 'DELETED'
    ACTION_CHOICES = [
        (ADDED, 'Employee added'),
        (UPDATED, 'Profile updated'),
        (ROLE, 'Role changed'),
        (STATUS, 'Status changed'),
        (SALARY, 'Salary updated'),
        (CREDENTIALS, 'Credentials reset'),
        (DOCUMENT, 'Document deleted'),
        (ONBOARDED, 'Onboarding completed'),
        (RESIGNATION, 'Resignation reviewed'),
        (DELETED, 'Employee deleted'),
    ]

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    actor_name = models.CharField(max_length=150, blank=True)
    actor_role = models.CharField(max_length=50, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    target = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    target_name = models.CharField(max_length=150, blank=True)
    branch = models.ForeignKey('core.Branch', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    summary = models.CharField(max_length=255)
    changes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"{self.actor_name or 'System'}: {self.summary}"

