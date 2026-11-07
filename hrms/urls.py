from django.urls import path

from . import views
from .api_views import ApplicationStageMoveAPIView, BulkResumeUploadAPIView

app_name = 'hrms'



urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    path('set-company/', views.set_active_company, name='set_active_company'),
    # --- Organisation (HR/Admin) ---
    path('org/company/', views.CompanyListView.as_view(), name='company_list'),
    path('org/company/add/', views.CompanyCreateView.as_view(), name='company_add'),
    path('org/company/<int:pk>/edit/', views.CompanyUpdateView.as_view(), name='company_edit'),

    path('org/departments/', views.DepartmentListView.as_view(), name='department_list'),
    path('org/departments/add/', views.DepartmentCreateView.as_view(), name='department_add'),
    path('org/departments/<int:pk>/edit/', views.DepartmentUpdateView.as_view(), name='department_edit'),
    path('org/departments/<int:pk>/delete/', views.DepartmentDeleteView.as_view(), name='department_delete'),

    path('org/designations/', views.DesignationListView.as_view(), name='designation_list'),
    path('org/designations/add/', views.DesignationCreateView.as_view(), name='designation_add'),
    path('org/designations/<int:pk>/edit/', views.DesignationUpdateView.as_view(), name='designation_edit'),
    path('org/designations/<int:pk>/delete/', views.DesignationDeleteView.as_view(), name='designation_delete'),

    # --- Employee ---
    path('employees/', views.EmployeeListView.as_view(), name='employee_list'),
    path('employees/add/', views.EmployeeCreateView.as_view(), name='employee_add'),
    path('employees/<int:pk>/', views.EmployeeDetailView.as_view(), name='employee_detail'),
    path('employees/<int:pk>/edit/', views.EmployeeUpdateView.as_view(), name='employee_edit'),
    path('employees/<int:pk>/delete/', views.EmployeeDeleteView.as_view(), name='employee_delete'),
    path('employees/<int:pk>/bank-detail/', views.EmployeeBankDetailUpdateView.as_view(), name='employee_bankdetail'),
    path('employees/<int:pk>/documents/add/', views.EmployeeDocumentCreateView.as_view(), name='employee_document_add'),
    path('employees/<int:pk>/documents/bulk-upload/', views.EmployeeDocumentBulkUploadView.as_view(), name='employee_document_bulk_upload'),
    path('employees/documents/<int:pk>/delete/', views.EmployeeDocumentDeleteView.as_view(), name='employee_document_delete'),
    path('employees/documents/<int:pk>/verify/', views.EmployeeDocumentVerifyView.as_view(), name='employee_document_verify'),
    path('employees/<int:pk>/notices/add/', views.EmployeeNoticeCreateView.as_view(), name='employee_notice_add'),
    path('employees/notices/<int:pk>/delete/', views.EmployeeNoticeDeleteView.as_view(), name='employee_notice_delete'),

    path('me/profile/', views.MyProfileView.as_view(), name='my_profile'),
    path('me/documents/', views.MyDocumentsView.as_view(), name='my_documents'),
    path('me/documents/upload/', views.EmployeeDocumentBulkUploadView.as_view(), name='my_documents_bulk_upload'),

    # # --- Hiring (HR/Admin) ---
    # path('hiring/jobs/', views.JobPostingListView.as_view(), name='jobposting_list'),
    # path('hiring/jobs/add/', views.JobPostingCreateView.as_view(), name='jobposting_add'),
    # path('hiring/jobs/<int:pk>/edit/', views.JobPostingUpdateView.as_view(), name='jobposting_edit'),
    # path('hiring/jobs/<int:pk>/delete/', views.JobPostingDeleteView.as_view(), name='jobposting_delete'),
    #
    # path('hiring/candidates/', views.CandidateListView.as_view(), name='candidate_list'),
    # path('hiring/candidates/add/', views.CandidateCreateView.as_view(), name='candidate_add'),
    # path('hiring/candidates/<int:pk>/', views.CandidateDetailView.as_view(), name='candidate_detail'),
    #
    # path('hiring/applications/', views.ApplicationListView.as_view(), name='application_list'),
    # path('hiring/applications/add/', views.ApplicationCreateView.as_view(), name='application_add'),
    # path('hiring/applications/<int:pk>/', views.ApplicationDetailView.as_view(), name='application_detail'),
    # path('hiring/applications/<int:pk>/status/', views.ApplicationStatusView.as_view(), name='application_status'),
    # path('hiring/applications/<int:pk>/unlock/', views.UnlockApplicationView.as_view(), name='application_unlock'),
    # path('hiring/applications/<int:pk>/offer/', views.OfferLetterCreateView.as_view(), name='offer_create'),
    #
    # path('hiring/interviews/', views.InterviewListView.as_view(), name='interview_list'),
    # path('hiring/interviews/mine/', views.MyInterviewsView.as_view(), name='my_interviews'),
    # path('hiring/applications/<int:pk>/interviews/add/', views.InterviewCreateView.as_view(), name='interview_add'),
    # path('hiring/interviews/<int:pk>/edit/', views.InterviewUpdateView.as_view(), name='interview_edit'),
    # path('hiring/interviews/<int:pk>/feedback/', views.InterviewFeedbackView.as_view(), name='interview_feedback'),
    #
    # path('hiring/offers/<int:pk>/<str:action>/', views.OfferLetterActionView.as_view(), name='offer_action'),
    # path('hiring/offers/<int:pk>/convert/', views.ConvertToEmployeeView.as_view(), name='offer_convert'),
    #
    # # Recruitment stages (reusable stage library)
    # path('hiring/stages/', views.RecruitmentStageListView.as_view(), name='recruitmentstage_list'),
    # path('hiring/stages/add/', views.RecruitmentStageCreateView.as_view(), name='recruitmentstage_add'),
    # path('hiring/stages/<int:pk>/edit/', views.RecruitmentStageUpdateView.as_view(), name='recruitmentstage_edit'),
    # path('hiring/stages/<int:pk>/delete/', views.RecruitmentStageDeleteView.as_view(), name='recruitmentstage_delete'),
    #
    # # Per-job pipeline management
    # path('hiring/jobs/<int:pk>/pipeline/', views.JobPipelineManageView.as_view(), name='jobpipeline_manage'),
    #
    # # Offer templates
    # path('hiring/offer-templates/', views.OfferTemplateListView.as_view(), name='offertemplate_list'),
    # path('hiring/offer-templates/add/', views.OfferTemplateCreateView.as_view(), name='offertemplate_add'),
    # path('hiring/offer-templates/<int:pk>/edit/', views.OfferTemplateUpdateView.as_view(), name='offertemplate_edit'),
    # path('hiring/offer-templates/<int:pk>/delete/', views.OfferTemplateDeleteView.as_view(), name='offertemplate_delete'),
    # path('hiring/offer-templates/<int:template_pk>/preview/<int:application_pk>/',
    #      views.OfferTemplatePreviewView.as_view(), name='offertemplate_preview'),

    # --- Hiring (HR/Admin) ---
    path('hiring/jobs/', views.JobPostingListView.as_view(), name='jobposting_list'),
    path('hiring/jobs/add/', views.JobPostingCreateView.as_view(), name='jobposting_add'),
    path('hiring/jobs/<int:pk>/edit/', views.JobPostingUpdateView.as_view(), name='jobposting_edit'),
    path('hiring/jobs/<int:pk>/delete/', views.JobPostingDeleteView.as_view(), name='jobposting_delete'),
    path('hiring/jobs/<int:pk>/', views.JobKanbanView.as_view(), name='jobposting_detail'),

    path('hiring/candidates/', views.CandidateListView.as_view(), name='candidate_list'),
    path('hiring/candidates/add/', views.CandidateCreateView.as_view(), name='candidate_add'),
    path('hiring/candidates/<int:pk>/', views.CandidateDetailView.as_view(), name='candidate_detail'),
    path('hiring/candidates/<int:pk>/edit/', views.CandidateUpdateView.as_view(), name='candidate_edit'),

    path('hiring/applications/', views.ApplicationListView.as_view(), name='application_list'),
    path('hiring/applications/add/', views.ApplicationCreateView.as_view(), name='application_add'),
    path('hiring/applications/<int:pk>/', views.ApplicationDetailView.as_view(), name='application_detail'),
    path('hiring/applications/<int:pk>/status/', views.ApplicationStatusView.as_view(), name='application_status'),
    path('hiring/applications/<int:pk>/unlock/', views.UnlockApplicationView.as_view(), name='application_unlock'),
    path('hiring/applications/<int:pk>/offer/', views.OfferLetterCreateView.as_view(), name='offer_create'),

    path('hiring/interviews/', views.InterviewListView.as_view(), name='interview_list'),
    path('hiring/interviews/mine/', views.MyInterviewsView.as_view(), name='my_interviews'),
    path('hiring/applications/<int:pk>/interviews/add/', views.InterviewCreateView.as_view(), name='interview_add'),
    path('hiring/interviews/<int:pk>/edit/', views.InterviewUpdateView.as_view(), name='interview_edit'),
    path('hiring/interviews/<int:pk>/feedback/', views.InterviewFeedbackView.as_view(), name='interview_feedback'),

    path('hiring/offers/<int:pk>/<str:action>/', views.OfferLetterActionView.as_view(), name='offer_action'),
    path('hiring/offers/<int:pk>/convert/', views.ConvertToEmployeeView.as_view(), name='offer_convert'),

    # Recruitment stages (reusable stage library)
    path('hiring/stages/', views.RecruitmentStageListView.as_view(), name='recruitmentstage_list'),
    path('hiring/stages/add/', views.RecruitmentStageCreateView.as_view(), name='recruitmentstage_add'),
    path('hiring/stages/<int:pk>/edit/', views.RecruitmentStageUpdateView.as_view(), name='recruitmentstage_edit'),
    path('hiring/stages/<int:pk>/delete/', views.RecruitmentStageDeleteView.as_view(), name='recruitmentstage_delete'),

    # Per-job pipeline management + bulk upload page
    path('hiring/jobs/<int:pk>/pipeline/', views.JobPipelineManageView.as_view(), name='jobpipeline_manage'),
    path('hiring/jobs/<int:pk>/kanban/', views.JobKanbanView.as_view(), name='job_kanban'),
    path('hiring/jobs/<int:pk>/bulk-upload/', views.BulkUploadPageView.as_view(), name='bulk_upload'),

    # Talent Pool
    path('hiring/talent-pool/', views.TalentPoolView.as_view(), name='talent_pool'),

    # Application notes (HTMX)
    path('hiring/applications/<int:pk>/notes/add/', views.ApplicationNoteCreateView.as_view(), name='application_note_add'),

    # Offer templates
    path('hiring/offer-templates/', views.OfferTemplateListView.as_view(), name='offertemplate_list'),
    path('hiring/offer-templates/add/', views.OfferTemplateCreateView.as_view(), name='offertemplate_add'),
    path('hiring/offer-templates/<int:pk>/edit/', views.OfferTemplateUpdateView.as_view(), name='offertemplate_edit'),
    path('hiring/offer-templates/<int:pk>/delete/', views.OfferTemplateDeleteView.as_view(),
         name='offertemplate_delete'),
    path('hiring/offer-templates/<int:template_pk>/preview/<int:application_pk>/',
         views.OfferTemplatePreviewView.as_view(), name='offertemplate_preview'),

    # --- Attendance ---
    path('attendance/daily-monitor/', views.AttendanceDailyMonitorView.as_view(), name='attendance_daily_monitor'),
    path('attendance/records/', views.AttendanceRecordListView.as_view(), name='attendance_records'),
    path('attendance/matrix/', views.AttendanceMatrixView.as_view(), name='attendance_matrix'),
    path('attendance/records/add/', views.AttendanceRecordCreateView.as_view(), name='attendance_record_add'),
    path('attendance/records/<int:pk>/edit/', views.AttendanceRecordUpdateView.as_view(), name='attendance_record_edit'),
    path('attendance/punch/edit-ajax/', views.manual_punch_edit_ajax, name='manual_punch_edit_ajax'),
    path('attendance/quick-action/', views.quick_action_ajax, name='attendance_quick_action'),
    path('attendance/regularize/', views.PunchRegularizationListView.as_view(), name='regularization_list'),
    path('attendance/regularize/apply/', views.PunchRegularizationCreateView.as_view(), name='regularization_apply'),
    path('attendance/regularize/<int:pk>/review/', views.PunchRegularizationReviewView.as_view(), name='regularization_review'),

    path('attendance/policy/', views.AttendancePolicyListView.as_view(), name='attendance_policy'),
    path('attendance/policy/<int:pk>/configure/', views.AttendancePolicyFormView.as_view(), name='attendance_policy_configure'),
    path('attendance/policy/add/', views.AttendancePolicyCreateView.as_view(), name='attendance_policy_create'),
    path('attendance/policy-version/<int:pk>/edit/', views.AttendancePolicyUpdateView.as_view(), name='attendance_policy_edit'),

    path('attendance/me/', views.MyAttendanceView.as_view(), name='my_attendance'),
    path('attendance/check-in/', views.CheckInView.as_view(), name='attendance_check_in'),
    path('attendance/check-out/', views.CheckOutView.as_view(), name='attendance_check_out'),

    path('attendance/holidays/', views.HolidayListView.as_view(), name='holiday_list'),
    path('attendance/holidays/add/', views.HolidayCreateView.as_view(), name='holiday_add'),
    path('attendance/holidays/<int:pk>/edit/', views.HolidayUpdateView.as_view(), name='holiday_edit'),
    path('attendance/holidays/<int:pk>/delete/', views.HolidayDeleteView.as_view(), name='holiday_delete'),

    path('attendance/report/<int:emp_id>/<int:month>/<int:year>/', views.EmployeePunchReportView.as_view(), name='employee_punch_report'),
    path('attendance/calendars/manage/', views.HolidayCalendarManageView.as_view(), name='holiday_calendar_manage'),
    path('attendance/penalties/', views.PenaltyListView.as_view(), name='attendance_penalties'),
    path(
        'attendance/matrix/auto-approve-leaves/', views.AutoApproveAbsentLeaveView.as_view(),
        name='auto_approve_absent_leaves'),
    # --- Manager ---
    path('manager/dashboard/', views.ManagerDashboardView.as_view(), name='manager_dashboard'),
    path('manager/leave/<int:pk>/approve/', views.ManagerLeaveApproveView.as_view(), name='manager_leave_approve'),
    path('manager/leave/<int:pk>/reject/', views.ManagerLeaveRejectView.as_view(), name='manager_leave_reject'),

    # --- Leave ---
    path('payroll/runs/<int:pk>/export-csv/', views.PayrollExportCSVView.as_view(), name='payroll_export_csv'),
    path('leave/', views.LeaveApplicationListView.as_view(), name='my_leave'),
    path('leave/apply/', views.LeaveApplicationCreateView.as_view(), name='leave_apply'),
    path('leave/<int:pk>/', views.LeaveDetailView.as_view(), name='leave_detail'),
    path('leave/<int:pk>/reapply/', views.LeaveReapplyView.as_view(), name='leave_reapply'),
    path('leave/<int:pk>/approve/', views.LeaveApproveView.as_view(), name='leave_approve'),
    path('leave/<int:pk>/reject/', views.LeaveRejectView.as_view(), name='leave_reject'),
    path('leave/<int:pk>/cancel/', views.LeaveCancelView.as_view(), name='leave_cancel'),

    path('leave/balance/', views.LeaveBalanceListView.as_view(), name='leave_balance'),
    path('leave/history/<int:pk>/', views.EmployeeLeaveHistoryView.as_view(), name='employee_leave_history'),

    path('leave/balance/add/', views.LeaveBalanceCreateView.as_view(), name='leave_balance_add'),
    path('leave/balance/<int:pk>/edit/', views.LeaveBalanceUpdateView.as_view(), name='leave_balance_edit'),

    path('leave/types/', views.LeaveTypeListView.as_view(), name='leavetype_list'),
    path('leave/types/add/', views.LeaveTypeCreateView.as_view(), name='leavetype_add'),
    path('leave/types/<int:pk>/edit/', views.LeaveTypeUpdateView.as_view(), name='leavetype_edit'),
    path('leave/types/<int:pk>/delete/', views.LeaveTypeDeleteView.as_view(), name='leavetype_delete'),
    path('leave-bank/', views.LeaveBankListView.as_view(), name='leave-bank'),

    # --- Payroll ---
    path('payroll/structures/', views.SalaryStructureListView.as_view(), name='salarystructure_list'),
    path('payroll/structures/add/', views.SalaryStructureCreateView.as_view(), name='salarystructure_add'),
    path('payroll/structures/<int:pk>/', views.SalaryStructureDetailView.as_view(), name='salarystructure_detail'),
    path('payroll/structures/<int:pk>/edit/', views.SalaryStructureUpdateView.as_view(), name='salarystructure_edit'),
    path('payroll/structures/<int:pk>/delete/', views.SalaryStructureDeleteView.as_view(), name='salarystructure_delete'),
    path('payroll/structures/<int:pk>/components/add/', views.SalaryComponentCreateView.as_view(), name='salarycomponent_add'),
    path('payroll/components/<int:pk>/delete/', views.SalaryComponentDeleteView.as_view(), name='salarycomponent_delete'),

    path('payroll/employee-salaries/', views.EmployeeSalaryListView.as_view(), name='employee_salary_list'),
    path('payroll/employee-salaries/add/', views.EmployeeSalaryCreateView.as_view(), name='employee_salary_add'),
    path('payroll/employee-salaries/<int:pk>/edit/', views.EmployeeSalaryUpdateView.as_view(), name='employee_salary_edit'),

    path('payroll/loans/', views.LoanAdvanceListView.as_view(), name='loan_list'),
    path('payroll/loans/add/', views.LoanAdvanceCreateView.as_view(), name='loan_add'),
    path('payroll/loans/<int:pk>/edit/', views.LoanAdvanceUpdateView.as_view(), name='loan_edit'),

    path('payroll/extras/', views.PayrollExtraListView.as_view(), name='extra_list'),
    path('payroll/extras/add/', views.PayrollExtraCreateView.as_view(), name='extra_add'),

    path('payroll/runs/', views.PayrollRunListView.as_view(), name='payrollrun_list'),
    path('payroll/runs/process/', views.PayrollProcessView.as_view(), name='payroll_process'),
    path('payroll/runs/<int:pk>/', views.PayrollRunDetailView.as_view(), name='payrollrun_detail'),

    path('payroll/payslips/', views.PaySlipListView.as_view(), name='my_payslips'),
    path('payroll/payslips/<int:pk>/', views.PaySlipDetailView.as_view(), name='payslip_detail'),
    path('payroll/payslips/<int:pk>/pdf/', views.PaySlipPDFView.as_view(), name='payslip_pdf'),

    # --- Policies & Notices ---
    path('policies/', views.PolicyListView.as_view(), name='policy_list'),
    path('policies/manage/', views.PolicyManagementView.as_view(), name='policy_manage'),
    path('policies/add/', views.PolicyCreateView.as_view(), name='policy_add'),
    path('policies/<int:pk>/', views.PolicyDetailView.as_view(), name='policy_detail'),
    path('policies/<int:pk>/edit/', views.PolicyUpdateView.as_view(), name='policy_edit'),
    path('policies/<int:pk>/delete/', views.PolicyDeleteView.as_view(), name='policy_delete'),

    path('notices/', views.CompanyNoticeListView.as_view(), name='notice_list'),
    path('notices/add/', views.CompanyNoticeCreateView.as_view(), name='notice_add'),
    path('notices/<int:pk>/', views.CompanyNoticeDetailView.as_view(), name='notice_detail'),
    path('notices/<int:pk>/edit/', views.CompanyNoticeUpdateView.as_view(), name='notice_edit'),
    path('notices/<int:pk>/delete/', views.CompanyNoticeDeleteView.as_view(), name='notice_delete'),

    # --- Assets ---
    path('assets/', views.AssetListView.as_view(), name='asset_list'),
    path('assets/add/', views.AssetCreateView.as_view(), name='asset_add'),
    path('ajax/create-category/', views.create_category_ajax, name='create_category_ajax'),
    path('assets/<int:pk>/edit/', views.AssetUpdateView.as_view(), name='asset_edit'),
    path('assets/<int:pk>/return/', views.AssetReturnView.as_view(), name='asset_return'),
    path('assets/<int:pk>/', views.AssetDetailView.as_view(), name='asset_detail'),

    # --- Performance ---
    path('performance/', views.PerformanceReviewListView.as_view(), name='performance_list'),
    path('performance/add/', views.PerformanceReviewCreateView.as_view(), name='performance_add'),
    path('performance/<int:pk>/', views.PerformanceReviewDetailView.as_view(), name='performance_detail'),
    path('performance/<int:pk>/edit/', views.PerformanceReviewUpdateView.as_view(), name='performance_edit'),
    path('performance/<int:pk>/acknowledge/', views.PerformanceAcknowledgeView.as_view(), name='performance_acknowledge'),


# Punch-In Endpoints (CBVs)
# Mobile camera & GPS screen for remote staff.
    path('attendance/punch-in/', views.PunchInPageView.as_view(template_name='hrms/attendance/punch_in.html'), name='punch_in_page'),

# Shared tablet screen at the Delhi office
    path('attendance/kiosk/', views.KioskPageView.as_view(template_name='hrms/attendance/kiosk.html'), name='kiosk_page'),

    # APIs
    path('attendance/api/mobile-punch/', views.MobilePunchInView.as_view(), name='api_mobile_punch'),  # .
    path('attendance/api/kiosk-punch/', views.KioskPunchInView.as_view(), name='api_kiosk_punch'),  #.
    path('attendance/api/location-ping/', views.LocationPingView.as_view(), name='api_location_ping'),
    # path('attendance/api/enroll-face/', views.EnrollFaceBiometricView.as_view(), name='api_enroll_face'),
    path('attendance/enroll-face/', views.FaceEnrollmentView.as_view(), name='enroll_face'),
    # Admin Monitoring
    path('attendance/live-tracking/', views.LiveTrackingDashboardView.as_view(), name='live_tracking_dashboard'),
    path('attendance/api/live-feed/', views.LiveTrackingFeedAPIView.as_view(), name='api_live_feed'),
    path('attendance/biometrics/', views.BiometricStatusListView.as_view(), name='biometric_status_list'),


# Bulk Salary Bank Payout Sheet (Finance / HR only)
    path('payroll/bulk-payout/', views.BulkPaymentDisbursementView.as_view(), name='bulk_payment_payout'),

    # 2. Comp Off History (Self-Service)
    path(
        'leave/comp-off/my-history/',
        views.CompOffHistoryView.as_view(),
        name='comp_off_history'
    ),

    # 3. Comp Off Management (HR Overview & Filtering)
    path(
        'leave/comp-off/hr-overview/',
        views.CompOffHRView.as_view(),
        name='comp_off_hr'
    ),

    # --- Hiring automation endpoints ---
    path('api/applications/<int:pk>/move-stage/', ApplicationStageMoveAPIView.as_view(), name='application_move_stage'),
    path('api/jobs/<int:job_id>/bulk-upload-resumes/', BulkResumeUploadAPIView.as_view(), name='bulk_upload_resumes'),
    path('hiring/applications/bulk-download-resumes/', views.bulk_download_resumes, name='bulk_download_resumes'),

    # --- Pipeline management API ---
    path('api/jobs/<int:pk>/pipeline/reorder/', views.PipelineReorderAPIView.as_view(), name='pipeline_reorder'),
    path('api/jobs/<int:pk>/pipeline/add-stage/', views.PipelineAddStageAPIView.as_view(), name='pipeline_add_stage'),
    path('api/pipeline-stages/<int:pk>/remove/', views.PipelineRemoveStageAPIView.as_view(), name='pipeline_remove_stage'),

    # --- Application quick-email API ---
    path('api/applications/<int:pk>/send-email/', views.ApplicationSendEmailAPIView.as_view(), name='application_send_email'),






]