from django.contrib import admin

from . import models as m

#add holiday calendar in admin file
@admin.register(m.Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'created_at')
    search_fields = ('name', 'email')


@admin.register(m.Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'company')
    list_filter = ('company',)
    search_fields = ('name',)


@admin.register(m.Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ('title', 'department', 'company', 'level')
    list_filter = ('company', 'department')


@admin.register(m.Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'employee_code', 'company', 'department', 'leave_policy_mode', 'status','created_at')
    list_filter = ('leave_policy_mode', 'status', 'company', 'employment_type')
    filter_horizontal = ('managed_companies',)


admin.site.register(m.EmployeeBankDetail)
admin.site.register(m.EmployeeDocument)
admin.site.register(m.EmployeeNotice)

# -------hiring----------
admin.site.register(m.RecruitmentStage)
admin.site.register(m.JobPosting)
admin.site.register(m.JobPipeline)
admin.site.register(m.Candidate)
admin.site.register(m.Application)
admin.site.register(m.RecruitmentAuditLog)
admin.site.register(m.Interview)
admin.site.register(m.OfferLetter)
admin.site.register(m.OfferTemplate)


# -------------------
admin.site.register(m.AttendancePolicy)
@admin.register(m.AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('employee', 'attendance_date', 'check_in', 'check_out', 'status', 'total_hours')
    list_filter = ('status', 'attendance_date')
    search_fields = ('employee__employee_code', 'employee__first_name')


@admin.register(m.AttendancePenalty)
class AttendancePenaltyAdmin(admin.ModelAdmin):
    list_display = ('employee', 'penalty_date', 'late_minutes', 'deduction_days', 'deduction_source', 'status')
    list_filter = ('status', 'penalty_date')
    search_fields = ('employee__first_name', 'employee__employee_code')


admin.site.register(m.GraceUsageTracker)
admin.site.register(m.Holiday)

admin.site.register(m.LeaveType)
admin.site.register(m.LeaveBalance)
admin.site.register(m.EmployeeLeaveBalance)



@admin.register(m.LeaveApplication)
class LeaveApplicationAdmin(admin.ModelAdmin):
    list_display = ('employee', 'leave_type', 'start_date', 'end_date', 'status')
    list_filter = ('status', 'leave_type')
admin.site.register(m.EmployeeLeaveBalanceLive)


admin.site.register(m.SalaryStructure)
admin.site.register(m.SalaryComponent)
admin.site.register(m.EmployeeSalary)
admin.site.register(m.PayrollRun)
admin.site.register(m.PaySlip)
admin.site.register(m.LoanAdvance)
admin.site.register(m.PayrollExtra)

admin.site.register(m.Policy)
admin.site.register(m.PolicyAcknowledgement)
admin.site.register(m.CompanyNotice)
admin.site.register(m.NoticeRead)

admin.site.register(m.HolidayCalendar)
admin.site.register(m.PunchRegularizationRequest)
admin.site.register(m.MonthlyLeaveAccrual)
admin.site.register(m.LeaveApprovalLog)
admin.site.register(m.AssetAssignmentHistory)
admin.site.register(m.PayslipDownloadLog)

admin.site.register(m.AssetCategory)
admin.site.register(m.Asset)
admin.site.register(m.PerformanceReview)


@admin.register(m.CompOffRecord)
class CompOffRecordAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'worked_date', 'holiday_name',
        'hours_worked', 'credits_earned', 'status',
        'availed_on_date',
    )
    list_filter  = ('status', 'worked_date')
    search_fields = ('employee__first_name', 'employee__last_name', 'employee__employee_code')
    readonly_fields = ('attendance_record', 'availed_leave_app', 'created_at', 'updated_at')
    ordering = ('-worked_date',)

admin.site.register(m.EmployeeBiometric)

