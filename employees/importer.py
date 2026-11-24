"""Bulk import of EXISTING employees from the Employee Master Excel template.

Flow (see employees.views.import_employees):
    parse_workbook()   -> raw rows
    build_context()    -> what already exists in the DB for the chosen branch
    validate_rows()    -> cleaned data + per-row errors   (pure, no DB writes)
    commit_rows()      -> UPDATES the profile of the existing account each row matches

Accounts are created ONLY through the Onboard page. A row is matched to an
existing account only when BOTH its Emp ID Number (User.employee_id) AND its
Enrollment No (EmployeeProfile.enrollment_id) are filled in and belong to the
SAME existing employee. Anything else (blank ID, unknown employee, one ID not
matching, IDs of two different employees) is an error for that row and nothing
is saved for it. Matched accounts keep their username, password, role, branch
and both IDs; only the profile data from the sheet is updated, and blank cells
never overwrite existing data. A row that matches no account is reported as an
error - nothing is ever created. The whole file is applied in one transaction:
if any row has an error, nothing is saved.
"""
import datetime as dt
import io
import re
from collections import Counter
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from core.validators import (
    aadhar_validator, alnum_id_validator, bank_account_validator,
    ifsc_validator, name_validator, pan_validator, phone_validator,
)
from employees.id_utils import (
    SEQ_WIDTH, employee_id_prefix_for_branch, enrollment_prefix_for_branch,
)

MAX_ROWS = 1000
MAX_FILE_BYTES = 5 * 1024 * 1024

# (header exactly as in Employee_master_Template.xlsx, internal key or None).
# None = column is kept in the template but not imported (no matching field
# in the HRMS yet).
TEMPLATE_COLUMNS = [
    ('S  No', None),
    ('Enrollment No', 'enrollment_id'),
    ('Emp  ID\nNumber', 'employee_id'),
    ('Name', 'name'),
    ('Designation', 'designation'),
    ('D O J', 'doj'),
    ('Gross Salary', None),
    ('Shift', 'shift'),
    ('Sex', 'gender'),
    ('Project/Department', 'department'),
    ('Father/Husband Name', None),
    ('Married Status', 'marital'),
    ('D-O-B', 'dob'),
    ('Qualifications', 'qualification'),
    ('Qualification Status', None),
    ('Currently Pursuing', None),
    ('Previous Experience', 'prev_exp'),
    ('Current Company Experience', 'cur_exp'),
    ('Casual Leave', None),
    ('Aadhar Number', 'aadhar'),
    ('Pan Number', 'pan'),
    ('Mobile Number', 'mobile'),
    ('Emergency Contact Number', 'emergency'),
    ('UAN/PF Number', 'uan'),
    ('PF/NPF', 'pf'),
    ('ESI Number', 'esi'),
    ('Account Number', 'account'),
    ('IFSC', 'ifsc'),
    ('Address', 'address'),
    ('Email ID', 'email'),
    ('Blood Group', 'blood'),
    ('No of Increments', None),
    ('Increment Date', None),
    ('Increment Amount', None),
    ('Refer by', 'referred_by'),
    ("Enrollment Number\n(If Employe's only)", 'referrer_enrollment'),
    ('Reference 1 - ( College Faculty / Previous Company)', 'ref1'),
    ('Reference 2 - Relatives', 'ref2'),
    ('Shift Time', None),
    ('Resign Date', 'resign_date'),
    ('Reason', 'reason'),
    ('Old-Joining Date', 'old_joining'),
]

REQUIRED_KEYS = ('name', 'doj')
REQUIRED_HEADERS = {'name': 'Name', 'doj': 'D O J'}
IGNORED_HEADERS = [h.replace('\n', ' ') for h, k in TEMPLATE_COLUMNS if k is None]


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


HEADER_TO_KEY = {_norm(h): k for h, k in TEMPLATE_COLUMNS if k}

# ---- value maps -----------------------------------------------------------
STATUS_MAP = {
    'active': 'ACTIVE', 'working': 'ACTIVE', 'onboarding': 'ONBOARDING', 'onboard': 'ONBOARDING',
    'exited': 'EXITED', 'exit': 'EXITED', 'resigned': 'EXITED', 'left': 'EXITED',
    'inactive': 'EXITED', 'relieved': 'EXITED',
}
SHIFT_MAP = {
    'general': 'GENERAL', 'generalshift': 'GENERAL', 'gen': 'GENERAL',
    'first': 'FIRST', 'firstshift': 'FIRST', '1st': 'FIRST', '1': 'FIRST',
    'second': 'SECOND', 'secondshift': 'SECOND', '2nd': 'SECOND', '2': 'SECOND',
    'night': 'NIGHT', 'nightshift': 'NIGHT',
}
GENDER_MAP = {'male': 'MALE', 'm': 'MALE', 'female': 'FEMALE', 'f': 'FEMALE'}
MARITAL_MAP = {
    'single': 'SINGLE', 'unmarried': 'SINGLE', 'married': 'MARRIED', 'other': 'OTHER',
}
EMPLOYMENT_MAP = {
    'permanent': 'PERMANENT', 'confirmed': 'PERMANENT',
    'probation': 'PROBATION', 'probationperiod': 'PROBATION', 'probationary': 'PROBATION',
}
PF_MAP = {'pf': True, 'yes': True, 'y': True, 'npf': False, 'nonpf': False, 'no': False, 'n': False}
YESNO_MAP = {
    'yes': True, 'y': True, 'received': True, 'issued': True, 'done': True, 'true': True,
    'no': False, 'n': False, 'notreceived': False, 'pending': False, 'notissued': False, 'false': False,
}
BLOOD_GROUPS = {'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'}

DATE_FORMATS = (
    '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y-%m-%d', '%d/%m/%y', '%d-%m-%y',
    '%d.%m.%y', '%d-%b-%Y', '%d %b %Y', '%d %B %Y', '%d-%B-%Y', '%b %d, %Y',
)


# ---- small cleaning helpers -----------------------------------------------
def _text(value):
    """Cell -> single-line stripped string ('' for empty)."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime('%d/%m/%Y')
    return re.sub(r'\s+', ' ', str(value)).strip()


def _digits(value):
    """For numeric identifiers: kills Excel's '9876543210.0', spaces, dashes."""
    s = _text(value)
    s = re.sub(r'\.0+$', '', s)
    return re.sub(r'[\s\-]', '', s)


def _phone(value):
    s = _digits(value)
    if s.startswith('+91'):
        s = s[3:]
    elif len(s) == 12 and s.startswith('91'):
        s = s[2:]
    elif len(s) == 11 and s.startswith('0'):
        s = s[1:]
    return s


def _parse_date(value):
    """Returns (date|None, error|None)."""
    if value is None or _text(value) == '':
        return None, None
    if isinstance(value, dt.datetime):
        return value.date(), None
    if isinstance(value, dt.date):
        return value, None
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:  # Excel serial
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(value)), None
    s = _text(value)
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date(), None
        except ValueError:
            continue
    return None, f"'{s}' is not a valid date (use DD/MM/YYYY)"


def _run(validator, value):
    try:
        validator(value)
        return None
    except ValidationError as exc:
        return exc.messages[0]


def _choice(value, mapping):
    key = re.sub(r'[^a-z0-9]', '', _text(value).lower())
    return mapping.get(key)


def _name_parts(full):
    parts = full.split(' ')
    return parts[0], ' '.join(parts[1:])


def _next_free_id(prefix, taken_ids, taken_usernames=None):
    """Highest numeric suffix among taken_ids for this prefix, +1 — and keep
    going if the candidate is somehow already a username."""
    top = 0
    for val in taken_ids:
        if val and val.startswith(prefix) and val[len(prefix):].isdigit():
            top = max(top, int(val[len(prefix):]))
    while True:
        top += 1
        candidate = f"{prefix}{top:0{SEQ_WIDTH}d}"
        if not taken_usernames or candidate.lower() not in taken_usernames:
            return candidate


# ---- data containers ------------------------------------------------------
@dataclass
class ImportContext:
    """Snapshot of what already exists, so validation needs no further queries."""
    branch: object
    departments: dict = field(default_factory=dict)      # lower name -> Department
    employee_ids: set = field(default_factory=set)
    enrollment_ids: set = field(default_factory=set)
    usernames: set = field(default_factory=set)          # lower-cased
    emp_owner: dict = field(default_factory=dict)        # employee_id -> user pk
    enr_owner: dict = field(default_factory=dict)        # enrollment_id -> user pk
    username_owner: dict = field(default_factory=dict)   # lower username -> user pk (only users with no employee_id)
    user_info: dict = field(default_factory=dict)        # user pk -> (branch_id, label)
    user_emp_id: dict = field(default_factory=dict)      # user pk -> saved employee_id
    user_enr_id: dict = field(default_factory=dict)      # user pk -> saved enrollment_id
    aadhars: dict = field(default_factory=dict)          # aadhar -> user pk
    pans: dict = field(default_factory=dict)             # PAN -> user pk
    people_by_dob: dict = field(default_factory=dict)    # (name_lower, iso dob) -> label
    people_by_doj: dict = field(default_factory=dict)    # (name_lower, iso doj) -> label
    today: dt.date = field(default_factory=dt.date.today)

    @property
    def employee_prefix(self):
        return employee_id_prefix_for_branch(self.branch)

    @property
    def enrollment_prefix(self):
        return enrollment_prefix_for_branch(self.branch)


@dataclass
class RowResult:
    row_no: int
    name: str = ''
    data: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    imported: bool = False
    skipped: bool = False
    match_user_id: int = None      # set when the row updates an existing account
    match_label: str = ''
    changes: list = field(default_factory=list)   # [(label, old, new)] that would really change
    unchanged: bool = False        # matched account already holds exactly these values
    target: object = None          # the User that was saved (set by commit_rows)

    @property
    def is_update(self):
        return self.match_user_id is not None

    @property
    def status(self):
        """Row status shown in the result table: Updated / No Changes / Error.
        A valid row that has changes but is not saved yet (preview) reads
        'Will Update'."""
        if self.errors:
            return 'Error'
        if self.unchanged:
            return 'No Changes'
        return 'Updated' if self.imported else 'Will Update'

    @property
    def ok(self):
        return not self.errors


def departments_for_branch(branch):
    """{lower name: Department} usable in `branch`: the branch's own departments
    plus shared ones that are not tied to any branch. A branch-specific
    department wins over a shared one with the same name."""
    from django.db.models import Q
    from core.models import Department

    found = {}
    qs = Department.objects.filter(Q(branch=branch) | Q(branch__isnull=True)).select_related('manager')
    for dept in sorted(qs, key=lambda d: d.branch_id is not None):   # shared first, branch-specific overrides
        found[dept.name.strip().lower()] = dept
    return found


def build_context(branch):
    """Load existing IDs / departments / people for `branch` from the DB."""
    from core.models import User
    from employees.models import EmployeeProfile

    ctx = ImportContext(branch=branch)
    ctx.departments = departments_for_branch(branch)
    for u in User.objects.all().only('id', 'username', 'employee_id', 'first_name', 'last_name', 'branch_id'):
        label = f"{u.get_full_name() or u.username} ({u.employee_id or u.username})"
        ctx.user_info[u.pk] = (u.branch_id, label)
        ctx.usernames.add(u.username.lower())
        if u.employee_id:
            ctx.employee_ids.add(u.employee_id)
            ctx.emp_owner[u.employee_id.upper()] = u.pk
            ctx.user_emp_id[u.pk] = u.employee_id.upper()
        else:
            ctx.username_owner[u.username.lower()] = u.pk
    for user_id, enr, aadhar, pan in EmployeeProfile.objects.values_list(
        'user_id', 'enrollment_id', 'aadhar_no', 'pan_no'
    ):
        if enr:
            ctx.enrollment_ids.add(enr)
            ctx.enr_owner[enr.upper()] = user_id
            ctx.user_enr_id[user_id] = enr.upper()
        if aadhar:
            ctx.aadhars[aadhar] = user_id
        if pan:
            ctx.pans[pan.upper()] = user_id
    for u in User.objects.filter(branch=branch).select_related('profile'):
        name_key = (u.get_full_name() or u.username).strip().lower()
        label = f"{u.get_full_name() or u.username} ({u.employee_id or 'no ID'})"
        profile = getattr(u, 'profile', None)
        if profile and profile.date_of_birth:
            ctx.people_by_dob[(name_key, profile.date_of_birth.isoformat())] = label
        if u.date_joined_company:
            ctx.people_by_doj[(name_key, u.date_joined_company.isoformat())] = label
    return ctx


# ---- reading the workbook -------------------------------------------------
def parse_workbook(file_obj):
    """Returns (rows, file_errors). rows = [(excel_row_no, {key: raw_value})]."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    except Exception:
        return [], ["Could not read the file. Please upload the Excel template as a .xlsx file."]

    ws = wb.worksheets[0]
    all_rows = ws.iter_rows(values_only=True)

    header_map = {}   # column index -> key
    header_row_no = 0
    for row_no, row in enumerate(all_rows, start=1):
        keys = {i: HEADER_TO_KEY[_norm(v)] for i, v in enumerate(row) if v is not None and _norm(v) in HEADER_TO_KEY}
        if len(keys) >= 3:          # looks like the header row
            header_map, header_row_no = keys, row_no
            break
        if row_no >= 10:
            break
    if not header_map:
        return [], ["Could not find the header row. Please use the downloaded template without changing the column headings."]

    missing = [REQUIRED_HEADERS[k] for k in REQUIRED_KEYS if k not in header_map.values()]
    if missing:
        return [], [f"Required column(s) missing from the sheet: {', '.join(missing)}."]

    rows = []
    for row_no, row in enumerate(all_rows, start=header_row_no + 1):
        raw = {key: (row[i] if i < len(row) else None) for i, key in header_map.items()}
        if all(_text(v) == '' for v in raw.values()):
            continue                # fully blank row
        rows.append((row_no, raw))
        if len(rows) > MAX_ROWS:
            return [], [f"Too many rows. Please import at most {MAX_ROWS} employees at a time."]
    if not rows:
        return [], ["The sheet has no employee rows below the header."]
    return rows, []


# ---- validation -----------------------------------------------------------
def _validate_row(row_no, raw, ctx):
    res = RowResult(row_no=row_no)
    err = res.errors.append
    d = res.data

    # Name ------------------------------------------------------------------
    name = _text(raw.get('name')).replace('.', ' ')
    name = re.sub(r'\s+', ' ', name).strip()
    res.name = name
    if not name:
        err("Name is required.")
    else:
        msg = _run(name_validator, name)
        if msg:
            err(f"Name: {msg}")
        d['first_name'], d['last_name'] = _name_parts(name)
        if len(d['first_name']) > 150 or len(d['last_name']) > 150:
            err("Name is too long.")

    # Dates -----------------------------------------------------------------
    doj, e = _parse_date(raw.get('doj'))
    if e:
        err(f"D O J: {e}")
    elif doj is None:
        err("D O J (date of joining) is required.")
    elif doj > ctx.today:
        err("D O J cannot be in the future.")
    d['doj'] = doj

    dob, e = _parse_date(raw.get('dob'))
    if e:
        err(f"D-O-B: {e}")
    elif dob and dob >= ctx.today:
        err("D-O-B cannot be today or in the future.")
    elif dob and doj and dob >= doj:
        err("D-O-B must be before D O J.")
    d['dob'] = dob

    old_join, e = _parse_date(raw.get('old_joining'))
    if e:
        err(f"Old-Joining Date: {e}")
    elif old_join and doj and old_join > doj:
        err("Old-Joining Date cannot be after D O J.")
    d['old_joining'] = old_join

    resign, e = _parse_date(raw.get('resign_date'))
    if e:
        err(f"Resign Date: {e}")
    elif resign and doj and resign < doj:
        err("Resign Date cannot be before D O J.")
    d['resign_date'] = resign
    d['reason'] = _text(raw.get('reason'))

    # Status ----------------------------------------------------------------
    status_raw = _text(raw.get('status'))
    if status_raw:
        status = _choice(status_raw, STATUS_MAP)
        if not status:
            err(f"Status '{status_raw}' is not valid (use Active or Exited).")
            status = 'ACTIVE'
    else:
        status = 'EXITED' if resign else 'ACTIVE'
    if status == 'EXITED' and not resign:
        err("Resign Date is required when Status is Exited.")
    if status != 'EXITED' and resign:
        err("Resign Date is filled but Status is not Exited.")
    d['status'] = status

    # Simple choice fields --------------------------------------------------
    def choice_field(key, label, mapping, default='', hint=''):
        raw_val = _text(raw.get(key))
        if not raw_val:
            return default
        out = _choice(raw_val, mapping)
        if out is None:
            err(f"{label} '{raw_val}' is not valid{hint}.")
            return default
        return out

    d['pf'] = choice_field('pf', 'PF/NPF', PF_MAP, True, ' (use PF or NPF)')
    d['id_card'] = choice_field('id_card', 'ID Status', YESNO_MAP, False, ' (use Yes or No)')
    d['shift'] = choice_field('shift', 'Shift', SHIFT_MAP, '', ' (General / First / Second / Night)')
    d['gender'] = choice_field('gender', 'Sex', GENDER_MAP, '', ' (Male or Female)')
    d['marital'] = choice_field('marital', 'Married Status', MARITAL_MAP, '', ' (Single / Married / Other)')
    d['employment_status'] = choice_field(
        'employment_status', 'Employment Status', EMPLOYMENT_MAP, 'PERMANENT', ' (Permanent or Probation Period)'
    )

    blood = _text(raw.get('blood')).upper().replace(' ', '')
    blood = blood.replace('POSITIVE', '+').replace('NEGATIVE', '-').replace('+VE', '+').replace('-VE', '-')
    if blood and blood not in BLOOD_GROUPS:
        err(f"Blood Group '{_text(raw.get('blood'))}' is not valid (e.g. A+, O-).")
        blood = ''
    d['blood'] = blood

    # Free text -------------------------------------------------------------
    d['designation'] = _text(raw.get('designation'))[:100]
    d['qualification'] = _text(raw.get('qualification'))[:200]
    d['prev_exp'] = _text(raw.get('prev_exp'))[:200]
    d['cur_exp'] = _text(raw.get('cur_exp'))[:100]
    d['address'] = _text(raw.get('address'))
    d['uan'] = _digits(raw.get('uan'))[:30]
    d['esi'] = _digits(raw.get('esi'))[:30]
    d['referred_by'] = _text(raw.get('referred_by'))[:150]
    d['referrer_enrollment'] = _text(raw.get('referrer_enrollment'))[:20]
    d['ref1'] = _text(raw.get('ref1'))[:150]
    d['ref2'] = _text(raw.get('ref2'))[:150]
    if len(d['designation']) != len(_text(raw.get('designation'))):
        err("Designation is longer than 100 characters.")

    # Contact ---------------------------------------------------------------
    for key, label in (('mobile', 'Mobile Number'), ('emergency', 'Emergency Contact Number')):
        val = _phone(raw.get(key))
        if val:
            msg = _run(phone_validator, val)
            if msg:
                err(f"{label}: {msg}")
        d[key] = val

    email = _text(raw.get('email'))
    if email:
        try:
            validate_email(email)
        except ValidationError:
            err(f"Email ID '{email}' is not a valid email address.")
    d['email'] = email

    # Government / bank IDs -------------------------------------------------
    aadhar = _digits(raw.get('aadhar'))
    if aadhar:
        msg = _run(aadhar_validator, aadhar)
        if msg:
            err(f"Aadhar Number: {msg}")
    d['aadhar'] = aadhar

    pan = _text(raw.get('pan')).upper().replace(' ', '')
    if pan:
        msg = _run(pan_validator, pan)
        if msg:
            err(f"Pan Number: {msg}")
    d['pan'] = pan

    account = _digits(raw.get('account'))
    if account:
        msg = _run(bank_account_validator, account)
        if msg:
            err(f"Account Number: {msg}")
    d['account'] = account

    ifsc = _text(raw.get('ifsc')).upper().replace(' ', '')
    if ifsc:
        msg = _run(ifsc_validator, ifsc)
        if msg:
            err(f"IFSC: {msg}")
    d['ifsc'] = ifsc

    # Department ------------------------------------------------------------
    dept_raw = _text(raw.get('department'))
    d['department'] = None
    if dept_raw:
        dept = ctx.departments.get(dept_raw.lower())
        if not dept:
            available = ', '.join(sorted(x.name for x in ctx.departments.values())) or 'none created yet'
            err(f"Department '{dept_raw}' does not exist for {ctx.branch.name}. Available: {available}.")
        d['department'] = dept

    # Identifiers used to find the existing employee (never generated, never changed)
    for key, label in (('employee_id', 'Employee ID'), ('enrollment_id', 'Enrollment ID')):
        val = _text(raw.get(key)).replace(' ', '').upper()
        d[key] = val
        if val and (len(val) > 20 or _run(alnum_id_validator, val)):
            err(f"{label} '{val}' may contain only letters, numbers and hyphens (max 20 characters).")

    d['_provided'] = {k for k, v in raw.items() if _text(v) != ''}
    return res


def _resolve_match(r, ctx, claimed):
    """Find the existing employee this row belongs to and record it on the row.

    BOTH Employee ID and Enrollment ID are required and must belong to the same
    existing employee. Otherwise the row gets an error and is never saved.
    `claimed` = user pks already targeted by an earlier row of this file."""
    emp = r.data.get('employee_id')
    enr = r.data.get('enrollment_id')

    # 1. Missing identifiers ----------------------------------------------------
    if not emp or not enr:
        if not emp and not enr:
            r.errors.append("Employee ID and Enrollment ID are both missing. Both are required to find the employee.")
        elif not emp:
            r.errors.append("Employee ID is missing. Both Employee ID and Enrollment ID are required.")
        else:
            r.errors.append("Enrollment ID is missing. Both Employee ID and Enrollment ID are required.")
        return

    by_emp = ctx.emp_owner.get(emp)
    by_enr = ctx.enr_owner.get(enr)

    # 2. Unknown employee ----------------------------------------------------------
    if by_emp is None and by_enr is None:
        r.errors.append("Employee not found. Only existing employees can be updated.")
        return

    # 3. IDs belong to two different employees ---------------------------------
    if by_emp is not None and by_enr is not None and by_emp != by_enr:
        r.errors.append(
            f"Conflicting IDs: Employee ID '{emp}' belongs to {ctx.user_info[by_emp][1]}, but "
            f"Enrollment ID '{enr}' belongs to {ctx.user_info[by_enr][1]}. "
            f"Both must belong to the same employee."
        )
        return

    # 4. Only one of the two IDs matches ---------------------------------------
    if by_enr is None:
        label = ctx.user_info[by_emp][1]
        saved = ctx.user_enr_id.get(by_emp)
        r.errors.append(
            f"Enrollment ID mismatch: '{enr}' does not match {label}"
            + (f" (saved Enrollment ID is {saved})." if saved else " (no Enrollment ID is saved for this employee).")
        )
        return
    if by_emp is None:
        label = ctx.user_info[by_enr][1]
        saved = ctx.user_emp_id.get(by_enr)
        r.errors.append(
            f"Employee ID mismatch: '{emp}' does not match {label}"
            + (f" (saved Employee ID is {saved})." if saved else " (no Employee ID is saved for this employee).")
        )
        return

    # 5. Both IDs match the same employee --------------------------------------
    match = by_emp
    branch_id, label = ctx.user_info[match]
    if branch_id != ctx.branch.id:
        r.errors.append(
            f"{label} belongs to a different branch. Select that branch to update this employee."
        )
        return
    if match in claimed:
        r.errors.append(f"Another row in this file already updates {label}.")
        return
    claimed.add(match)
    r.match_user_id, r.match_label = match, label


def validate_rows(rows, ctx):
    """rows from parse_workbook -> list[RowResult] with IDs assigned."""
    results = [_validate_row(row_no, raw, ctx) for row_no, raw in rows]

    # Cross-row uniqueness (inside the file) ---------------------------------
    def dup_counts(key):
        return Counter(r.data.get(key) for r in results if r.data.get(key))

    labels = {
        'employee_id': 'Emp ID Number', 'enrollment_id': 'Enrollment No',
        'aadhar': 'Aadhar Number', 'pan': 'Pan Number',
    }
    first_row = {k: {} for k in labels}     # value -> row number of its first appearance
    claimed = set()
    for r in results:
        for key, label in labels.items():
            val = r.data.get(key)
            if not val:
                continue
            if val in first_row[key]:
                r.errors.append(f"{label} '{val}' is already used in row {first_row[key][val]} of this file.")
            else:
                first_row[key][val] = r.row_no

        # Existing employee? Both IDs must match the same account.
        _resolve_match(r, ctx, claimed)

        # Aadhar / PAN must not belong to someone else
        for key, label, owners in (('aadhar', 'Aadhar Number', ctx.aadhars), ('pan', 'Pan Number', ctx.pans)):
            val = r.data.get(key)
            owner = owners.get(val) if val else None
            if owner is not None and owner != r.match_user_id:
                r.errors.append(f"{label} '{val}' already belongs to another employee.")


    # What would actually change? (re-uploading the same file = nothing) ---
    from core.models import User
    ids = {r.match_user_id for r in results if r.ok and r.is_update}
    users = User.objects.select_related('profile', 'bank_detail').in_bulk(ids) if ids else {}
    for r in results:
        if r.ok and r.is_update:
            plan = _plan_changes(users[r.match_user_id], r.data)
            r.changes = [(label, _show(label, old), _show(label, new)) for _s, _a, label, old, new in plan]
            r.unchanged = not plan
    return results


# ---- writing to the database ----------------------------------------------
# sheet key -> EmployeeProfile attribute (used when updating an existing account)
PROFILE_UPDATE_MAP = {
    'pf': 'is_pf_applicable', 'id_card': 'id_card_received',
    'shift': 'shift', 'gender': 'gender', 'marital': 'marital_status', 'designation': 'designation',
    'dob': 'date_of_birth', 'qualification': 'qualification', 'prev_exp': 'previous_experience',
    'cur_exp': 'current_company_experience', 'employment_status': 'employment_status',
    'aadhar': 'aadhar_no', 'pan': 'pan_no', 'emergency': 'emergency_contact_phone',
    'uan': 'uan_pf_number', 'esi': 'esi_number', 'address': 'address', 'blood': 'blood_group',
    'referred_by': 'referred_by_name', 'referrer_enrollment': 'referred_by_enrollment_id',
    'ref1': 'reference1_name', 'ref2': 'reference2_name', 'old_joining': 'old_joining_date',
    'resign_date': 'exit_date', 'reason': 'exit_reason',
}


FIELD_LABELS = {k: h.replace('\n', ' ') for h, k in TEMPLATE_COLUMNS if k}
FIELD_LABELS.update({'name': 'Name', 'doj': 'D O J', 'manager': 'Manager', 'enrollment_id': 'Enrollment No',
                     'employee_id': 'Emp ID Number', 'account_holder': 'Account holder name'})
SENSITIVE_KEYS = {'aadhar', 'pan', 'account'}


def _norm_val(v):
    if v is None:
        return ''
    if hasattr(v, 'pk'):
        return v.pk
    return v


SENSITIVE_LABELS = {FIELD_LABELS[k] for k in SENSITIVE_KEYS}


def _show(label, value):
    """Printable old/new value for the preview table (IDs/bank numbers masked)."""
    if value is None or value == '':
        return '(empty)'
    if label in SENSITIVE_LABELS:
        return 'changed'
    if isinstance(value, dt.date):
        return value.strftime('%d/%m/%Y')
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


def _plan_changes(user, d):
    """Compare the sheet row with what is saved. Returns only the fields that
    differ: [(scope, attr, label, old, new)] where scope is user/profile/bank.
    Blank cells are ignored, so they never count as a change."""
    given = d['_provided']
    dept = d['department']
    profile = getattr(user, 'profile', None)
    bank = getattr(user, 'bank_detail', None)
    plan = []

    def add(scope, obj, attr, key, new):
        old = getattr(obj, attr, None) if obj is not None else None
        if _norm_val(old) != _norm_val(new):
            plan.append((scope, attr, FIELD_LABELS.get(key, key), old, new))

    add('user', user, 'first_name', 'name', d['first_name'])
    add('user', user, 'last_name', 'name', d['last_name'])
    add('user', user, 'date_joined_company', 'doj', d['doj'])
    if 'email' in given:
        add('user', user, 'email', 'email', d['email'])
    if 'mobile' in given:
        add('user', user, 'phone', 'mobile', d['mobile'])
    if dept:
        add('user', user, 'department', 'department', dept)
        if not user.manager_id and dept.manager_id and dept.manager_id != user.pk:
            add('user', user, 'manager', 'manager', dept.manager)

    for key, attr in PROFILE_UPDATE_MAP.items():
        if key in given:
            add('profile', profile, attr, key, d[key])
    if 'status' in given or 'resign_date' in given:
        add('profile', profile, 'status', 'status', d['status'])

    holder = f"{d['first_name']} {d['last_name']}".strip()
    if 'account' in given:
        add('bank', bank, 'account_number', 'account', d['account'])
    if 'ifsc' in given:
        add('bank', bank, 'ifsc_code', 'ifsc', d['ifsc'])
    if ('account' in given or 'ifsc' in given) and not (bank and bank.account_holder_name):
        add('bank', bank, 'account_holder_name', 'account_holder', holder)
    return plan


def _update_existing(r, ctx):
    """Save only the fields that differ. Username, password, role and branch
    are never touched."""
    from core.models import User
    from employees.models import BankDetail, EmployeeProfile

    user = User.objects.select_related('profile', 'bank_detail').get(pk=r.match_user_id)
    plan = _plan_changes(user, r.data)
    if not plan:
        return False
    r.target = user

    by_scope = {'user': [], 'profile': [], 'bank': []}
    for scope, attr, _label, _old, new in plan:
        by_scope[scope].append((attr, new))

    if by_scope['user']:
        for attr, new in by_scope['user']:
            setattr(user, attr, new)
            if attr == 'phone' and not user.phone_country_code:
                user.phone_country_code = '+91'
        user.save()
    if by_scope['profile']:
        profile = getattr(user, 'profile', None) or EmployeeProfile(user=user)
        for attr, new in by_scope['profile']:
            setattr(profile, attr, new)
        profile.save()
    if by_scope['bank']:
        bank = getattr(user, 'bank_detail', None) or BankDetail(user=user)
        for attr, new in by_scope['bank']:
            setattr(bank, attr, new)
        bank.save()
    return True


def commit_rows(results, ctx):
    """Update the existing account of every ok row that really has changes.
    Runs in ONE transaction: a failure part-way rolls every row back. Refuses
    to write anything if any row has an error. Returns the number updated."""
    from django.db import transaction

    if any(not r.ok for r in results):
        for r in results:
            r.skipped = True
        return 0
    updated = 0
    with transaction.atomic():
        for r in results:
            if r.unchanged:
                continue                      # same data already saved - nothing to do
            if _update_existing(r, ctx):
                r.imported = True
                updated += 1
    return updated


# ---- downloadable template ------------------------------------------------
def build_template_workbook(branch, department_names=()):
    """The Employee Master template (same headings as the file HR already
    uses) plus an Instructions sheet specific to the chosen branch."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'

    head_fill = PatternFill('solid', fgColor='1E3A8A')
    req_fill = PatternFill('solid', fgColor='EA580C')
    required_headers = {_norm(v) for v in REQUIRED_HEADERS.values()}
    for col, (header, _key) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = req_fill if _norm(header) in required_headers else head_fill
        cell.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
        ws.column_dimensions[get_column_letter(col)].width = 18
    ws.row_dimensions[1].height = 42
    ws.freeze_panes = 'A2'

    info = wb.create_sheet('Instructions')
    emp_ex = f"{employee_id_prefix_for_branch(branch)}{'0' * (SEQ_WIDTH - 1)}1"
    enr_ex = f"{enrollment_prefix_for_branch(branch)}{'0' * (SEQ_WIDTH - 1)}1"
    lines = [
        (f"Employee import template - {branch.name}", True),
        ("", False),
        ("Fill one employee per row on 'Sheet1' (starting row 2), then upload it from Employees > Import Employees.", False),
        ("Do not rename or delete the column headings. Orange headings (Name, D O J) are mandatory.", False),
        ("", False),
        ("Rules", True),
        ("Dates: DD/MM/YYYY (e.g. 24/10/2026).", False),
        ("This import only UPDATES accounts that already exist (create them in Onboard first - GM, PM, COO, CTO, employees, all of them). Username, password, role and branch are not changed, and blank cells do not erase saved data.", False),
        (f"Emp ID Number and Enrollment No: both required - this is how the existing account is found. Format: {emp_ex}", False),
        (f"Enrollment No format: {enr_ex}", False),
        ("Project/Department: must match one of the departments listed below (spelling not case-sensitive).", False),
        ("Resign Date: fill it only for an employee who has left - the employee is then marked as Exited. Leave blank for working employees.", False),
        ("PF/NPF: PF or NPF.  Sex: Male or Female.", False),
        ("Shift: General / First / Second / Night.  Married Status: Single / Married / Other.", False),
        ("Mobile / Emergency Contact: 10 digits.  Aadhar: 12 digits.  Pan: ABCDE1234F.  IFSC: HDFC0001234.", False),
        ("Account Number: 9 to 18 digits.  Blood Group: A+, A-, B+, B-, AB+, AB-, O+, O-.", False),
        ("Name: letters, numbers, spaces, hyphen and apostrophe. Full stops are removed automatically (S. Kumar -> S Kumar).", False),
        ("", False),
        ("Columns that are kept in the template but NOT imported (no matching field in the HRMS yet)", True),
        (', '.join(IGNORED_HEADERS), False),
        ("", False),
        (f"Departments in {branch.name}", True),
    ]
    lines += [(name, False) for name in sorted(department_names)] or [("(no departments created yet)", False)]
    for i, (text, bold) in enumerate(lines, start=1):
        c = info.cell(row=i, column=1, value=text)
        c.font = Font(bold=bold, size=13 if i == 1 else 11)
        c.alignment = Alignment(wrap_text=True, vertical='top')
    info.column_dimensions['A'].width = 120

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
