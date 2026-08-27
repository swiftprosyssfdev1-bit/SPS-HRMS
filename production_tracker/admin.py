from django.contrib import admin

from .models import DailyProduction, HourlyEntry, ProductionEvent, ProductionGrouping, ProductionProcess


@admin.register(ProductionProcess)
class ProductionProcessAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    search_fields = ('name',)


@admin.register(ProductionEvent)
class ProductionEventAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    search_fields = ('name',)


@admin.register(ProductionGrouping)
class ProductionGroupingAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_non_working', 'is_active')
    list_filter = ('is_non_working', 'is_active')
    search_fields = ('name',)


class HourlyEntryInline(admin.TabularInline):
    model = HourlyEntry
    extra = 0
    fields = ('hour_start', 'hour_end', 'count')
    readonly_fields = ('hour_start', 'hour_end')
    can_delete = False


@admin.register(DailyProduction)
class DailyProductionAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'date', 'branch', 'shift', 'work_type', 'project', 'process', 'event',
        'grouping', 'target_day', 'achieved', 'actual_target', 'efficiency_percentage',
    )
    list_filter = ('branch', 'shift', 'work_type', 'project', 'grouping', 'date')
    search_fields = ('employee__employee_id', 'employee__first_name', 'employee__last_name')
    date_hierarchy = 'date'
    inlines = [HourlyEntryInline]
    readonly_fields = ('hours_worked', 'achieved', 'actual_target', 'efficiency_percentage', 'created_at', 'updated_at')
