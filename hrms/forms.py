from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from . import models as m


class BootstrapModelForm(forms.ModelForm):
    """Adds `form-control` / `form-select` / `form-check-input` to every
    field automatically so templates don't need per-field widget classes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault('class', 'form-select')
            elif isinstance(widget, forms.ClearableFileInput):
                widget.attrs.setdefault('class', 'form-control')
            else:
                widget.attrs.setdefault('class', 'form-control')
            if isinstance(widget, (forms.Textarea,)):
                widget.attrs.setdefault('rows', 3)


# ---------------------------------------------------------------------------
# Organisation Structure
# ---------------------------------------------------------------------------
class CompanyForm(BootstrapModelForm):
    class Meta:
        model = m.Company
        fields = [
            'name', 'logo', 'address', 'cin', 'pan', 'gstin', 'website', 'email', 'phone',
            'office_start_time', 'office_end_time', 'grace_minutes', 'grace_allowed_count',
        ]
        widgets = {
            'office_start_time': forms.TimeInput(attrs={'type': 'time'}),
            'office_end_time': forms.TimeInput(attrs={'type': 'time'}),
            'address': forms.Textarea(attrs={'rows': 2}),
        }


class DepartmentForm(BootstrapModelForm):
    class Meta:
        model = m.Department
        fields = ['company', 'name', 'description']


class DesignationForm(BootstrapModelForm):
    class Meta:
        model = m.Designation
        fields = ['company', 'department', 'title']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Narrow the department dropdown to the chosen company where possible
        company_id = None
        if self.data.get('company'):
            company_id = self.data.get('company')
        elif self.instance and self.instance.pk:
            company_id = self.instance.company_id
        if company_id:
            self.fields['department'].queryset = m.Department.objects.filter(company_id=company_id)


# ---------------------------------------------------------------------------
# Employee Management
# ---------------------------------------------------------------------------
class EmployeeForm(BootstrapModelForm):
    class Meta:
        model = m.Employee
        fields = [
            'company', 'department', 'designation', 'employee_code', 'first_name', 'last_name',
            'email', 'phone', 'gender','marital_status', 'date_of_birth', 'father_name', 'mother_name', 'address',
            'emergency_contact', 'date_of_joining', 'date_of_confirmation', 'employment_type', 'status',
            'holiday_calendar',
            'is_manager', 'reporting_manager','attendance_mode', 'leave_policy_mode',
        ]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'date_of_joining': forms.DateInput(attrs={'type': 'date'}),
            'date_of_confirmation': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Enter full address...'}),
            'emergency_contact': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Name, Relationship, Phone number'}),
            'attendance_mode': forms.Select(attrs={'class': 'form-select form-control'}),
            'leave_policy_mode': forms.Select(attrs={'class': 'form-select form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter reporting_manager to only show employees where is_manager=True
        mgr_qs = m.Employee.objects.filter(is_manager=True)
        if self.instance and self.instance.pk:
            mgr_qs = mgr_qs.exclude(pk=self.instance.pk)
        self.fields['reporting_manager'].queryset = mgr_qs
        self.fields['reporting_manager'].label_from_instance = lambda obj: f"{obj.full_name} ({obj.employee_code})"

class EmployeeBankDetailForm(BootstrapModelForm):
    class Meta:
        model = m.EmployeeBankDetail
        fields = ['account_holder', 'account_number', 'ifsc_code', 'bank_name', 'branch_name',
                  'account_type', 'is_verified']


class EmployeeDocumentForm(BootstrapModelForm):
    class Meta:
        model = m.EmployeeDocument
        fields = ['document_type', 'file', 'description', 'issued_on', 'expiry_on']
        widgets = {
            'issued_on': forms.DateInput(attrs={'type': 'date'}),
            'expiry_on': forms.DateInput(attrs={'type': 'date'}),
        }


class EmployeeNoticeForm(BootstrapModelForm):
    class Meta:
        model = m.EmployeeNotice
        fields = ['title', 'description', 'notice_date', 'is_active']
        widgets = {
            'notice_date': forms.DateInput(attrs={'type': 'date'}),
        }


# ---------------------------------------------------------------------------
# Attendance & Grace Policy
# ---------------------------------------------------------------------------
class AttendancePolicyForm(BootstrapModelForm):
    class Meta:
        model = m.AttendancePolicy
        fields = [
            'company','office_start_time', 'office_end_time', 'grace_minutes', 'grace_allowed_count','effective_from',
            'half_day_threshold_hours', 'full_day_threshold_hours', 'overtime_threshold_hours',
        ]
        widgets = {
            # HTML5 date and time pickers for better UI
            'effective_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'office_start_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'office_end_time': forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}),
            'company': forms.Select(attrs={'class': 'form-select'}),
            'grace_minutes': forms.NumberInput(attrs={'class': 'form-control'}),
            'grace_allowed_count': forms.NumberInput(attrs={'class': 'form-control'}),
        }


class AttendanceRecordForm(BootstrapModelForm):
    """For HR manual entry / correction of an attendance record."""
    class Meta:
        model = m.AttendanceRecord
        fields = ['employee', 'attendance_date', 'check_in', 'check_out', 'status', 'remarks']
        widgets = {
            'attendance_date': forms.DateInput(attrs={'type': 'date'}),
            'check_in': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'check_out': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }


class HolidayCalendarForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        # We pass the user from the view to the form
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if user:
            if user.is_superuser:
                # Superadmins see all companies
                self.fields['company'].queryset = m.Company.objects.all()
            else:
                # HR Managers only see companies they are assigned to manage
                self.fields['company'].queryset = user.employee_profile.managed_companies.all()

    class Meta:
        model = m.HolidayCalendar
        fields = ['company', 'name', 'is_default']  # Added 'company'
        widgets = {
            'company': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. North India Office'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class HolidayForm(BootstrapModelForm):
    class Meta:
        model = m.Holiday
        fields = ['calendar', 'date', 'name', 'type', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'type': forms.Select(attrs={'class': 'form-select'}),
        }

class HolidayCalendarForm(forms.ModelForm):
    class Meta:
        model = m.HolidayCalendar
        # Include all necessary fields
        fields = ['company', 'name', 'is_default', 'description']
        widgets = {
            'company': forms.Select(attrs={'class': 'form-select rounded-3'}),
            'name': forms.TextInput(
                attrs={'class': 'form-control rounded-3', 'placeholder': 'e.g. North India Office'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'description': forms.Textarea(attrs={'class': 'form-control rounded-3', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        # Pop user to filter companies
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        if user:
            if user.is_superuser:
                self.fields['company'].queryset = m.Company.objects.all()
            else:
                # Only show companies this HR is assigned to
                self.fields['company'].queryset = user.employee_profile.managed_companies.all()

        # Make company mandatory for creation
        self.fields['company'].empty_label = "--- Select Company ---"
        self.fields['company'].required = True


class BulkHolidayForm(forms.ModelForm):
    # This is a virtual field not in the DB, used for bulk selection
    target_calendars = forms.ModelMultipleChoiceField(
        queryset=m.HolidayCalendar.objects.none(),
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'list-unstyled'}),
        label="Apply to these Templates"
    )

    class Meta:
        model = m.Holiday
        fields = ['name', 'date', 'type', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Independence Day'}),
            'type': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Filter the checklists so HR only sees calendars for THEIR companies
        if user:
            if user.is_superuser:
                self.fields['target_calendars'].queryset = m.HolidayCalendar.objects.all().select_related('company')
            else:
                managed_ids = user.employee_profile.managed_companies.values_list('id', flat=True)
                self.fields['target_calendars'].queryset = m.HolidayCalendar.objects.filter(
                    company_id__in=managed_ids
                ).select_related('company')

        # Display the company name next to the calendar name in the checklist
        self.fields['target_calendars'].label_from_instance = lambda obj: f"{obj.name} ({obj.company.name})"

# ---------------------------------------------------------------------------
# Leave Management
# ---------------------------------------------------------------------------
class LeaveTypeForm(BootstrapModelForm):
    class Meta:
        model = m.LeaveType
        fields = [
            'company', 'name', 'code', 'allocation_mode', 'days_per_year', 'is_paid', 'is_carry_forward',
            'max_carry_forward', 'max_consecutive_days', 'min_consecutive_days', 'requires_approval',
            'requires_document', 'requires_relationship', 'requires_stage', 'applicable_gender','applicable_marital_status','description',
        ]


class LeaveBalanceForm(BootstrapModelForm):
    """HR allocation / correction screen — used, pending included so HR can
    fix a balance manually if something gets out of sync."""
    class Meta:
        model = m.LeaveBalance
        fields = ['employee', 'leave_type', 'year', 'allocated', 'used', 'pending', 'carried_forward']

from django import forms
from hrms import models as m


class LeaveBalanceForm(forms.ModelForm):
    class Meta:
        model = m.EmployeeLeaveBalance
        fields = '__all__'
        widgets = {
            'e_name': forms.Select(attrs={'class': 'form-select select2'}),
            'casual_leave': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'earned_leave': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'sick_leave': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'menstrual_leave': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'bereavement_leave': forms.NumberInput(attrs={'class': 'form-control', 'step': '1'}),
            'comp_off': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['e_name'].disabled = True

class LeaveApplicationForm(BootstrapModelForm):
    """Self-service: the employee applying is fixed by the view, not a form field."""
    class Meta:
        model = m.LeaveApplication
        fields = [
            'leave_type', 'day_type', 'start_date', 'end_date', 'reason',
            'supporting_document', 'relationship', 'leave_stage',
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Reason for leave...'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        day_type = cleaned_data.get('day_type')
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if day_type == 'half':
            if start_date:
                cleaned_data['end_date'] = start_date
        elif start_date and end_date and end_date < start_date:
            raise forms.ValidationError('End date cannot be before start date.')

        return cleaned_data

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        if employee:
            from django.db.models import Q
            # Match company and filter by gender & marital status criteria
            self.fields['leave_type'].queryset = m.LeaveType.objects.filter(
                company=employee.company
            ).filter(
                Q(applicable_gender='all') | Q(applicable_gender=employee.gender)
            ).filter(
                Q(applicable_marital_status='all') | Q(applicable_marital_status=employee.marital_status)
            )


class LeaveApplicationHRForm(LeaveApplicationForm):
    """Same as above, but HR picks which employee it's for."""
    class Meta(LeaveApplicationForm.Meta):
        fields = ['employee'] + LeaveApplicationForm.Meta.fields


class LeaveRejectForm(forms.Form):
    rejection_reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                      'placeholder': 'Mandatory reason for rejection...'}),
        required=True,
        help_text='Please provide a clear reason for the rejection.'
    )


class RejectionModalForm(forms.Form):
    """Universal rejection form enforcing mandatory remarks."""
    rejection_reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                      'placeholder': 'Mandatory reason / remarks for rejection...'}),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add Bootstrap classes to all fields
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control form-control-sm'})
            # Ensure Django doesn't block the save if a field is momentarily empty
            field.required = False


# ---------------------------------------------------------------------------
# Payroll & Salary Structure
# ---------------------------------------------------------------------------
class SalaryStructureForm(BootstrapModelForm):
    class Meta:
        model = m.SalaryStructure
        fields = ['company', 'name', 'description']




class SalaryComponentForm(BootstrapModelForm):
    class Meta:
        model = m.SalaryComponent
        fields = ['name', 'component_type', 'is_earning', 'is_deduction', 'is_taxable', 'display_order']


class EmployeeSalaryForm(BootstrapModelForm):
    class Meta:
        model = m.EmployeeSalary
        fields = [
            'employee', 'structure', 'ctc_annual', 'basic', 'hra', 'special_allowance',
            'pf_employee', 'pf_employer', 'esic_employee', 'esic_employer', 'professional_tax', 'tds',
            'effective_from', 'effective_to', 'is_active',
        ]
        widgets = {
            'effective_from': forms.DateInput(attrs={'type': 'date'}),
            'effective_to': forms.DateInput(attrs={'type': 'date'}),
        }


class LoanAdvanceForm(BootstrapModelForm):
    class Meta:
        model = m.LoanAdvance
        fields = ['employee', 'description', 'principal_amount', 'monthly_installment',
                  'remaining_balance', 'start_date', 'is_active']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
        }


class PayrollExtraForm(BootstrapModelForm):
    class Meta:
        model = m.PayrollExtra
        fields = ['employee', 'label', 'amount']


MONTH_CHOICES = [(i, m_name) for i, m_name in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June',
     'July', 'August', 'September', 'October', 'November', 'December'], start=1)]


class PayrollProcessForm(forms.Form):
    company = forms.ModelChoiceField(queryset=m.Company.objects.all())
    month = forms.ChoiceField(choices=MONTH_CHOICES)
    year = forms.IntegerField(min_value=2000, max_value=2100)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            widget.attrs.setdefault('class', 'form-select' if isinstance(widget, forms.Select) else 'form-control')


# ---------------------------------------------------------------------------
# Hiring / Recruitment Pipeline
# ---------------------------------------------------------------------------
class JobPostingForm(BootstrapModelForm):
    class Meta:
        model = m.JobPosting
        fields = ['company', 'department', 'designation', 'title', 'job_type', 'location',
                  'description', 'requirements', 'is_active', 'closing_date']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'requirements': forms.Textarea(attrs={'rows': 4}),
            'closing_date': forms.DateInput(attrs={'type': 'date'}),
        }


class CandidateForm(BootstrapModelForm):
    class Meta:
        model = m.Candidate
        fields = ['first_name', 'last_name', 'email', 'phone', 'city', 'state', 'resume', 'current_company', 'experience_years']

class ApplicationForm(BootstrapModelForm):
    class Meta:
        model = m.Application
        fields = ['candidate', 'job_posting', 'status', 'source', 'cover_letter']
        widgets = {
            'cover_letter': forms.Textarea(attrs={'rows': 3}),
        }



# -------this is mixture of two forms ----

class UnifiedCandidateForm(forms.ModelForm):
    # Add application-specific fields manually to the candidate form
    job_posting = forms.ModelChoiceField(
        queryset=m.JobPosting.objects.filter(is_active=True),
        required=True,
        label="Applying For"
    )
    status = forms.ChoiceField(choices=m.Application.Status.choices, initial='applied')
    source = forms.CharField(required=False)
    cover_letter = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)

    class Meta:
        model = m.Candidate
        fields = ['first_name', 'last_name', 'email', 'phone', 'city', 'state', 'resume', 'current_company', 'experience_years']


# --- UPDATE: InterviewForm — pipeline stage + scorecard --------------------
# --- UPDATE: ApplicationForm — add the new ATS metadata fields ------------
class ApplicationForm(forms.ModelForm):
    class Meta:
        model = m.Application
        fields = [
            'candidate', 'job_posting', 'status', 'source', 'cover_letter',
            'expected_ctc', 'current_ctc', 'notice_period', 'current_stage',
        ]
        widgets = {
            'cover_letter': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        job = None
        if self.instance and self.instance.pk:
            job = self.instance.job_posting
        elif 'job_posting' in self.data:
            job = m.JobPosting.objects.filter(pk=self.data.get('job_posting')).first()
        # Only offer stages that are actually in this job's pipeline
        if job:
            self.fields['current_stage'].queryset = m.RecruitmentStage.objects.filter(
                job_pipelines__job=job).distinct()
        else:
            self.fields['current_stage'].queryset = m.RecruitmentStage.objects.none()
        self.fields['current_stage'].required = False

SCORECARD_CATEGORIES = ['technical', 'communication', 'culture']


class InterviewForm(forms.ModelForm):
    # Individual 1-5 rating widgets that get packed into the `scorecard` JSON
    # field on save — friendlier for HR than hand-typing JSON.
    score_technical = forms.IntegerField(min_value=1, max_value=5, required=False, label='Technical (1-5)')
    score_communication = forms.IntegerField(min_value=1, max_value=5, required=False, label='Communication (1-5)')
    score_culture = forms.IntegerField(min_value=1, max_value=5, required=False, label='Culture Fit (1-5)')

    class Meta:
        model = m.Interview
        fields = [
            'pipeline_stage', 'interview_round', 'scheduled_on', 'interviewer',
            'mode', 'feedback', 'status',
        ]
        widgets = {
            'scheduled_on': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'feedback': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, application=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.application = application or getattr(self.instance, 'application', None)
        if self.application:
            self.fields['pipeline_stage'].queryset = m.JobPipeline.objects.filter(
                job=self.application.job_posting).select_related('stage').order_by('order')
        self.fields['pipeline_stage'].required = False
        self.fields['interview_round'].required = False

        if self.instance and self.instance.pk and self.instance.scorecard:
            self.fields['score_technical'].initial = self.instance.scorecard.get('technical')
            self.fields['score_communication'].initial = self.instance.scorecard.get('communication')
            self.fields['score_culture'].initial = self.instance.scorecard.get('culture')

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('pipeline_stage') and not cleaned.get('interview_round'):
            raise forms.ValidationError('Pick a pipeline stage or enter a free-text interview round.')
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        scorecard = {}
        for key in SCORECARD_CATEGORIES:
            value = self.cleaned_data.get(f'score_{key}')
            if value is not None:
                scorecard[key] = value
        instance.scorecard = scorecard
        if commit:
            instance.save()
        return instance



# --- NEW: RecruitmentStage --------------------------------------------------
class RecruitmentStageForm(forms.ModelForm):
    class Meta:
        model = m.RecruitmentStage
        fields = ['name', 'description', 'is_default']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


# --- NEW: JobPipeline — managed as an inline formset against one JobPosting
JobPipelineFormSet = inlineformset_factory(
    m.JobPosting, m.JobPipeline,
    fields=['stage', 'order'],
    extra=1, can_delete=True,
)


# --- NEW: OfferTemplate -----------------------------------------------------
class OfferTemplateForm(forms.ModelForm):
    class Meta:
        model = m.OfferTemplate
        fields = ['name', 'body_html', 'is_active']
        widgets = {'body_html': forms.Textarea(attrs={'rows': 14, 'class': 'font-monospace'})}


# --- UPDATE: OfferLetterForm — template picker + editable content ---------
class OfferLetterForm(forms.ModelForm):
    class Meta:
        model = m.OfferLetter
        fields = ['template', 'offer_date', 'ctc_offered', 'joining_date', 'expiry_date', 'content']
        widgets = {
            'offer_date': forms.DateInput(attrs={'type': 'date'}),
            'joining_date': forms.DateInput(attrs={'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'content': forms.Textarea(attrs={'rows': 14}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['template'].queryset = m.OfferTemplate.objects.filter(is_active=True)
        self.fields['content'].required = False


class ConvertToEmployeeForm(forms.Form):
    company = forms.ModelChoiceField(queryset=m.Company.objects.all())
    department = forms.ModelChoiceField(queryset=m.Department.objects.all(), required=False)
    designation = forms.ModelChoiceField(queryset=m.Designation.objects.all(), required=False)
    employee_code = forms.CharField(max_length=30)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            widget.attrs.setdefault('class', 'form-select' if isinstance(widget, forms.Select) else 'form-control')


# ---------------------------------------------------------------------------
# Asset Management
# ---------------------------------------------------------------------------


from django import forms
from . import models as m

class AssetCategoryForm(forms.ModelForm):
    class Meta:
        model = m.AssetCategory
        fields = ['name', 'required_fields']
        widgets = {
            # Standard Bootstrap styled input
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Laptop, Mobile, SIM Card'}),
            # Helpful placeholder showing exact field names accepted by the dynamic UI
            'required_fields': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'model_number, serial_number, device_password, additional_details'
            }),
        }

    def clean_name(self):
        # Prevent creating duplicates with different casing
        name = self.cleaned_data.get('name', '').strip()
        qs = m.AssetCategory.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A category with this name already exists.")
        return name


class AssetForm(forms.ModelForm):
    class Meta:
        model = m.Asset
        fields = [
            # Group 1: Categorization & Ownership
            'company', 'category', 'name', 'model_number',
            # Group 2: Dynamic Category-Belonging Fields
            'serial_number', 'sim', 'phone_number', 'device_password', 'additional_details',
            # Group 3: Custody & Status
            'employee', 'status', 'assigned_on',
            # Group 4: Vendor, Procurement & Warranty
            'vendor_name', 'vendor_contact', 'purchase_date', 'warranty_expiry', 'invoice_document',
            # Group 5: Remarks
            'remarks'
        ]

        widgets = {
            # Company and Category selections
            'company': forms.Select(attrs={'class': 'form-select'}),
            'category': forms.Select(attrs={'class': 'form-select', 'id': 'id_category'}),
            # Asset naming & specs
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. MacBook Pro 14" / iPhone 15'}),
            'model_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. A2992 / Latitude 5420'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Unique hardware serial number'}),
            'sim': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Jio / Airtel (ICCID number)'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. +91 9876543210'}),
            'device_password': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Login PIN or Master password'}),
            'additional_details': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'RAM, SSD, accessories...'}),
            # Custody
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'assigned_on': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            # Procurement & Warranty
            'vendor_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Apple India / Croma'}),
            'vendor_contact': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Vendor phone or support email'}),
            'purchase_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'warranty_expiry': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'invoice_document': forms.FileInput(attrs={'class': 'form-control'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Condition notes, scratch report...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Clear empty label so HR knows employee assignment is optional (stored in warehouse if blank)
        self.fields['employee'].empty_label = "--- Keep in Warehouse (Unassigned) ---"

        # Always order categories alphabetically
        self.fields['category'].queryset = m.AssetCategory.objects.all().order_by('name')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].empty_label = "--- Keep in Warehouse (Unassigned) ---"
        self.fields['category'].queryset = m.AssetCategory.objects.all().order_by('name')

        # Filter dropdown to show ONLY active employees
        emp_qs = m.Employee.objects.filter(status=m.Employee.Status.ACTIVE)

        # If editing an asset currently held by an employee, keep them in queryset
        if self.instance and self.instance.pk and self.instance.employee:
            emp_qs = m.Employee.objects.filter(
                models.Q(status=m.Employee.Status.ACTIVE) | models.Q(pk=self.instance.employee.pk)
            )

        self.fields['employee'].queryset = emp_qs.order_by('first_name', 'last_name')


    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get('employee')
        status = cleaned_data.get('status')
        assigned_on = cleaned_data.get('assigned_on')

        # Auto-synchronize status based on employee assignment
        if employee and status == m.Asset.Status.AVAILABLE:
            # If an employee is chosen, status cannot remain 'Available'
            cleaned_data['status'] = m.Asset.Status.ASSIGNED
            if not assigned_on:
                cleaned_data['assigned_on'] = timezone.localdate()
        elif not employee and status == m.Asset.Status.ASSIGNED:
            # If no employee is assigned, asset cannot be in 'Assigned' state
            cleaned_data['status'] = m.Asset.Status.AVAILABLE
            cleaned_data['assigned_on'] = None

        return cleaned_data
class AssetReturnForm(forms.Form):
    """Form used when returning/unassigning an asset."""
    returned_in_good_condition = forms.ChoiceField(
        choices=[('yes', 'Yes — In Good Condition'), ('no', 'No — Damaged / Missing Parts')],
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}),
        initial='yes',
        label='Return Condition'
    )
    return_remarks = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control',
                                      'placeholder': 'Mandatory remarks if asset is damaged or missing items...'}),
        required=False,
        label='Condition Notes / Remarks'
    )

    def clean(self):
        cleaned_data = super().clean()
        condition = cleaned_data.get('returned_in_good_condition')
        remarks = (cleaned_data.get('return_remarks') or '').strip()
        if condition == 'no' and not remarks:
            raise forms.ValidationError('Remarks are mandatory when an asset is not returned in good condition.')
        return cleaned_data


# ---------------------------------------------------------------------------
# Attendance Regularization & Punch Edit Forms (Module D)
# ---------------------------------------------------------------------------
class PunchRegularizationRequestForm(BootstrapModelForm):
    class Meta:
        model = m.PunchRegularizationRequest
        fields = ['attendance_date', 'requested_check_in', 'requested_check_out', 'reason', 'proof_document']
        widgets = {
            'attendance_date': forms.DateInput(attrs={'type': 'date'}),
            'requested_check_in': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'requested_check_out': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Detailed explanation for regularizing this punch...'}),
        }


class PunchRegularizationReviewForm(forms.Form):
    status = forms.ChoiceField(choices=[('approved', 'Approve'), ('rejected', 'Reject')])
    rejection_reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'Mandatory if rejecting...'}),
        required=False
    )

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        reason = (cleaned_data.get('rejection_reason') or '').strip()
        if status == 'rejected' and not reason:
            raise forms.ValidationError('Rejection reason is mandatory.')
        return cleaned_data


class ManualPunchEditForm(forms.ModelForm):
    """Admin manual punch edit with mandatory edit_reason."""
    edit_reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control',
                                      'placeholder': 'Mandatory audit reason for manual punch modification...'}),
        required=True,
        label='Reason for Edit (Audit Trail)'
    )

    class Meta:
        model = m.AttendanceRecord
        fields = ['check_in', 'check_out', 'status', 'edit_reason']
        widgets = {
            'check_in': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'check_out': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }


# ---------------------------------------------------------------------------
# Policies & Notices Forms (Module F)
# ---------------------------------------------------------------------------
class PolicyForm(BootstrapModelForm):
    class Meta:
        model = m.Policy
        fields = [
            'company', 'title', 'category', 'description', 'document',
            'effective_from', 'effective_to', 'is_mandatory', 'is_active', 'quiz_data'
        ]
        widgets = {
            'effective_from': forms.DateInput(attrs={'type': 'date'}),
            'effective_to': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'quiz_data': forms.HiddenInput(),
        }


class CompanyNoticeForm(BootstrapModelForm):
    class Meta:
        model = m.CompanyNotice
        fields = ['company', 'title', 'category', 'message', 'document', 'notice_date', 'expiry_date', 'is_active']
        widgets = {
            'notice_date': forms.DateInput(attrs={'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'message': forms.Textarea(attrs={'rows': 4}),
        }


# ---------------------------------------------------------------------------
# Performance Reviews
# ---------------------------------------------------------------------------
class PerformanceReviewForm(BootstrapModelForm):
    class Meta:
        model = m.PerformanceReview
        fields = ['employee', 'reviewer', 'review_period_from', 'review_period_to',
                  'review_date', 'rating', 'comments', 'goals', 'status']
        widgets = {
            'review_period_from': forms.DateInput(attrs={'type': 'date'}),
            'review_period_to': forms.DateInput(attrs={'type': 'date'}),
            'review_date': forms.DateInput(attrs={'type': 'date'}),
            'comments': forms.Textarea(attrs={'rows': 3}),
            'goals': forms.Textarea(attrs={'rows': 3}),
        }