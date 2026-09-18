import io
from datetime import date
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.http import FileResponse
from django.db.models import Q

from core.decorators import  coo_required
from core.models import User, Branch

from leaves.models import LeaveRequest, LeaveNotification
from increments.models import IncrementRequest
from payroll.models import SalaryStructure, Payslip
from employees.models import EmployeeProfile
from leaves.utils import not_expired_leaves, approved_on_leave_for_date
from attendance.models import AttendanceRecord


def _notify(user, leave, message):
    if user is None:
        return
    LeaveNotification.objects.create(user=user, leave_request=leave, message=message)


# Roles the COO oversees directly: HR, every Manager variant, and Admin.
COO_DIRECTORY_ROLES = ['ADMIN', 'HR', 'SENIOR_HR', 'MANAGER', 'PROJECT_MANAGER', 'GENERAL_MANAGER']

# Admin first, then HR, then every Manager variant grouped together
# (dept-wise ordering is applied on top of this in _coo_directory_sort_key).
_ROLE_TIER = {'ADMIN': 0, 'HR': 1, 'SENIOR_HR': 1}


def _coo_directory_sort_key(u):
    name = (u.first_name or u.username).lower()
    tier = _ROLE_TIER.get(u.role, 2)  # 2 = any manager variant
    if tier != 2:
        return (tier, '', name)
    dept_name = u.department.name if u.department else 'zzz_no_department'
    return (tier, dept_name.lower(), name)


def _notify(user, leave, message):
    if user is None:
        return
    LeaveNotification.objects.create(user=user, leave_request=leave, message=message)


@coo_required
def coo_dashboard(request):
    tab = request.GET.get('tab', 'directory')
    context = {'tab': tab}

    try:
        from core.models import MotivationalPost
        context['motivational_post'] = MotivationalPost.objects.filter(is_active=True).first()
    except Exception:
        pass

    coo_branch = request.user.branch

    if tab == 'directory':
        leaders = list(
            User.objects.filter(role__in=COO_DIRECTORY_ROLES)
            .select_related('department', 'profile')
        )
        for u in leaders:
            u.dept_display = u.department.name if u.department else '-'
        leaders.sort(key=_coo_directory_sort_key)
        context['leaders'] = leaders

    elif tab == 'leave_approval':
        coo_reviewable_users = User.objects.filter(
            role__in=COO_DIRECTORY_ROLES
        )

        context['requests'] = LeaveRequest.objects.filter(
            status='PENDING_COO'
        ).select_related('user', 'user__department').order_by('-applied_at')

        history_qs = LeaveRequest.objects.filter(
            user__in=coo_reviewable_users
        ).exclude(status='PENDING_COO').select_related('user').order_by('-applied_at')
        today = timezone.localdate()
        context['history'] = not_expired_leaves(history_qs, today)[:100]
    elif tab == 'increment_approval':
        context['pending_coo'] = IncrementRequest.objects.filter(
            status='PENDING_COO'
        ).select_related('user', 'feedback_manager', 'feedback', 'forwarded_by').order_by('-forwarded_at')
        context['decided'] = IncrementRequest.objects.filter(
            coo_decided_by__isnull=False
        ).select_related('user').order_by('-coo_decided_at')[:50]

    elif tab == 'manpower':
        # Per-branch, per-day manpower snapshot: total active employees,
        # present today, on leave, and absent — for every branch (B01-B04),
        # for a COO-selected date (defaults to today).
        date_str = request.GET.get('date')
        if date_str:
            try:
                selected_date = date.fromisoformat(date_str)
            except ValueError:
                selected_date = timezone.localdate()
        else:
            selected_date = timezone.localdate()

        branch_rows = []
        for branch in Branch.objects.all().order_by('code'):
            active_profiles = EmployeeProfile.objects.filter(
                user__branch=branch, status='ACTIVE'
            ).exclude(user__role__in=['ADMIN'])
            active_user_ids = set(active_profiles.values_list('user_id', flat=True))
            total = len(active_user_ids)

            present_count = AttendanceRecord.objects.filter(
                date=selected_date, in_time__isnull=False, user_id__in=active_user_ids
            ).values('user_id').distinct().count()

            on_leave_records = [
                r for r in approved_on_leave_for_date(selected_date, branch)
                if r.user_id in active_user_ids
            ]
            on_leave_count = len(on_leave_records)

            absent_count = max(0, total - present_count - on_leave_count)

            branch_rows.append({
                'branch': branch,
                'total': total,
                'present': present_count,
                'on_leave': on_leave_count,
                'absent': absent_count,
                'on_leave_records': on_leave_records,
            })

        context['selected_date'] = selected_date
        context['branch_rows'] = branch_rows

    elif tab == 'reports':
        context['hr_reports'] = IncrementRequest.objects.filter(
            status__in=['REPORT_SUBMITTED', 'PENDING_COO', 'COO_DECIDED', 'APPLIED', 'REJECTED'],
            report_submitted_at__isnull=False,
        ).select_related('user', 'feedback_manager').prefetch_related('feedback').order_by('-report_submitted_at')

    elif tab == 'payslips':
        context['payslips'] = Payslip.objects.filter(
            user=request.user, is_released=True
        ).select_related('payroll_run').order_by('-payroll_run__start_date')

    return render(request, 'core/coo_dashboard.html', context)

@coo_required
def coo_review_leave(request, leave_id, decision):
    leave = get_object_or_404(LeaveRequest, id=leave_id, status='PENDING_COO')
    label = leave.get_request_type_display()

    if request.method == 'POST':
        leave.reviewed_by_hr = request.user  # reuse this field to record the final approver
        leave.hr_reviewed_at = timezone.now()

        if decision == 'approve':
            leave.status = 'APPROVED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was approved by the CEO.")
            messages.success(request, f"{label} approved for {leave.user}.")
        else:
            leave.status = 'REJECTED'
            leave.save()
            _notify(leave.user, leave, f"Your {label.lower()} request was rejected by the CEO.")
            messages.success(request, f"{label} rejected for {leave.user}.")

    return redirect(f"/coo/?tab=leave_approval")


@coo_required
def coo_decide_increment(request, increment_id, decision):
    increment = get_object_or_404(IncrementRequest, id=increment_id, status='PENDING_COO')

    if request.method == 'POST':
        increment.coo_decided_by = request.user
        increment.coo_decided_at = timezone.now()

        if decision == 'approve':
            percent_raw = request.POST.get('coo_percent', '').strip()
            try:
                percent = float(percent_raw)
            except ValueError:
                messages.error(request, "Enter a valid increment percentage.")
                return redirect('/coo/?tab=increment_approval')

            if percent <= -9999.99 or percent >= 9999.99:
                messages.error(request, "Enter a percentage between -9999.99 and 9999.99.")
                return redirect('/coo/?tab=increment_approval')

            increment.coo_percent = percent
            increment.coo_comment = request.POST.get('coo_comment', '').strip()
            increment.status = 'COO_DECIDED'
            increment.save()
            messages.success(request, f"Decision sent to HR: {percent}% increment for {increment.user}.")
        else:
            reason = request.POST.get('coo_rejection_reason', '').strip()
            if not reason:
                messages.error(request, "A rejection reason is required.")
                return redirect('/coo/?tab=increment_approval')
            increment.coo_rejection_reason = reason
            increment.status = 'REJECTED'
            increment.save()
            messages.info(request, f"Increment rejected for {increment.user}.")

    return redirect('/coo/?tab=increment_approval')

@coo_required
def coo_download_report(request, report_type):
    """Generates a styled PDF report for the COO to download."""
    import io
    from django.utils import timezone
    from django.http import FileResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor("#2B3674"),
        spaceAfter=20,
        alignment=1 # Center
    )
    
    today = timezone.localdate()
    elements.append(Paragraph(f"Date: {today}", styles['Normal']))
    elements.append(Spacer(1, 10))

    if report_type == 'directory':
        elements.append(Paragraph("Leadership Directory Report", title_style))
        data = [['Name', 'Role', 'Department']]
        
        leaders = User.objects.filter(
            Q(role__in=['GENERAL_MANAGER', 'PROJECT_MANAGER', 'SENIOR_HR']) | Q(role='HR', is_senior_hr=True)
        ).select_related('department')
        
        for u in leaders:
            data.append([
                u.get_full_name() or u.username,
                u.get_role_display(),
                str(u.department) if u.department else '-'
            ])
            
    elif report_type == 'leave_summary':
        elements.append(Paragraph("Leadership Leave Summary", title_style))
        data = [['Employee', 'Type', 'Status', 'Date']]
        
        requests = LeaveRequest.objects.filter(
            user__in=User.objects.filter(Q(role='GENERAL_MANAGER') | Q(role='SENIOR_HR') | Q(role='HR', is_senior_hr=True))
        ).select_related('user').order_by('-applied_at')[:100]
        
        for r in requests:
            data.append([
                str(r.user),
                r.get_request_type_display(),
                r.get_status_display(),
                str(r.applied_at.date())
            ])
            
    elif report_type == 'increment_summary':
        elements.append(Paragraph("Increment Approvals Report", title_style))
        data = [['Employee', 'Status', 'Approved %', 'Decision Date']]
        
        increments = IncrementRequest.objects.filter(
            coo_decided_by__isnull=False
        ).select_related('user').order_by('-coo_decided_at')
        
        for i in increments:
            data.append([
                str(i.user),
                i.get_status_display(),
                f"{i.coo_percent}%" if i.coo_percent else '-',
                str(i.coo_decided_at.date()) if i.coo_decided_at else '-'
            ])
            
    else:
        elements.append(Paragraph("Unknown report type", title_style))
        data = []

    if data and len(data) > 1:
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#4318FF")),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#F4F7FE")),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor("#E2E8F0")),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F4F7FE")]),
        ]))
        elements.append(table)
    elif data:
        elements.append(Paragraph("No records found.", styles['Normal']))

    doc.build(elements)
    buffer.seek(0)
    filename = f"{report_type}_report_{today}.pdf"
    return FileResponse(buffer, as_attachment=True, filename=filename)
