from datetime import date
import calendar
from collections import defaultdict
from django.db.models import Sum, Avg, Count

from core.models import User
from production_tracker.models import DailyProduction
from attendance.models import AttendanceRecord
from leaves.models import LeaveRequest

def get_working_days(year, month):
    num_days = calendar.monthrange(year, month)[1]
    sundays = 0
    for day in range(1, num_days + 1):
        if calendar.weekday(year, month, day) == 6:
            sundays += 1
    return num_days - sundays

def calculate_appraisal_data(employee, start_year, start_month):
    # Generates data for 12 months starting from start_year/start_month
    months = []
    current_year = start_year
    current_month = start_month
    
    for _ in range(12):
        months.append((current_year, current_month))
        current_month += 1
        if current_month > 12:
            current_month = 1
            current_year += 1
            
    # Initialize structures
    data = {
        'months_labels': [f"{calendar.month_abbr[m]} '{str(y)[-2:]}" for y, m in months],
        'projects': defaultdict(lambda: {'target': [0]*12, 'actual': [0]*12}),
        'total_project': {'target': [0]*12, 'actual': [0]*12},
        'efficiency': {'target': [0.9]*12, 'actual': [0.0]*12},
        'absenteeism': {'target': [0.0]*12, 'actual': [0.0]*12}
    }
    
    # 1. Project targets and actuals, plus Efficiency
    for idx, (y, m) in enumerate(months):
        records = DailyProduction.objects.filter(
            employee=employee,
            date__year=y,
            date__month=m
        ).select_related('project')
        
        # Aggregate by project
        project_aggregates = records.values('project__name').annotate(
            t_sum=Sum('target_day'),
            a_sum=Sum('achieved'),
        )
        
        total_m_target = 0
        total_m_actual = 0
        
        for agg in project_aggregates:
            p_name = agg['project__name'] or 'Unassigned'
            p_t = agg['t_sum'] or 0
            p_a = agg['a_sum'] or 0
            
            data['projects'][p_name]['target'][idx] = p_t
            data['projects'][p_name]['actual'][idx] = p_a
            
            total_m_target += p_t
            total_m_actual += p_a
            
        data['total_project']['target'][idx] = total_m_target
        data['total_project']['actual'][idx] = total_m_actual
        
        # Average Efficiency for the month
        eff_avg = records.aggregate(Avg('efficiency_percentage'))['efficiency_percentage__avg']
        if eff_avg is not None:
            data['efficiency']['actual'][idx] = float(eff_avg) / 100.0
            
        # 2. Absenteeism
        wd = get_working_days(y, m)
        if wd > 0:
            present_days = AttendanceRecord.objects.filter(
                user=employee, date__year=y, date__month=m
            ).count()
            
            from datetime import date
            first_day = date(y, m, 1)
            last_day = date(y, m, calendar.monthrange(y, m)[1])
            
            leave_requests = LeaveRequest.objects.filter(
                user=employee, 
                status='APPROVED',
                request_type='LEAVE',
                start_date__lte=last_day,
                end_date__gte=first_day
            )
            
            approved_leaves = 0
            for lr in leave_requests:
                overlap_start = max(first_day, lr.start_date)
                overlap_end = min(last_day, lr.end_date)
                approved_leaves += (overlap_end - overlap_start).days + 1
            
            absent_days = wd - present_days - approved_leaves
            if absent_days < 0:
                absent_days = 0
                
            data['absenteeism']['actual'][idx] = float(absent_days) / wd
            
    # Convert projects from defaultdict to standard dict
    data['projects'] = dict(data['projects'])
    return data
