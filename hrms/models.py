"""
HRMS models — mirrors the ER diagram (HRMS – Django Models).
Organised into the same 9 sections as the diagram, in one file for now.
Split into models/ package later if it grows unwieldy.
"""
from django.conf import settings
from django.db import models
from django.contrib.auth import get_user_model
import re
from django.db.models import Max
from decimal import Decimal
from datetime import datetime, date, time
from django.utils import timezone

User = get_user_model()

# ---------------------------------------------------------------------------
# 0. ABSTRACT BASE
# ---------------------------------------------------------------------------
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

# ---------------------------------------------------------------------------
# 1. ORGANISATION STRUCTURE
# ---------------------------------------------------------------------------
class Company(TimeStampedModel):
    name = models.CharField(max_length=255)
    logo = models.ImageField(upload_to='company/logo/', blank=True, null=True)
    address = models.TextField(blank=True)
    cin = models.CharField('CIN', max_length=50, blank=True)
    pan = models.CharField('PAN', max_length=20, blank=True)
    gstin = models.CharField('GSTIN', max_length=20, blank=True)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    office_start_time = models.TimeField(default='10:00')
    office_end_time = models.TimeField(default='18:00')
    grace_minutes = models.PositiveIntegerField(default=15)
    grace_allowed_count = models.PositiveIntegerField(default=3)
    # ADD THESE 3 FIELDS TO MATCH THE POLICY MODEL:
    half_day_threshold_hours = models.DecimalField(max_digits=4, decimal_places=2, default=4.0)
    full_day_threshold_hours = models.DecimalField(max_digits=4, decimal_places=2, default=8.0)
    overtime_threshold_hours = models.DecimalField(max_digits=4, decimal_places=2, default=9.0)

    class Meta:
        verbose_name_plural = 'Companies'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_policy_for_date(self, target_date):
        """
        The 'Time Travel' logic:
        Checks if a historical policy exists for the given date.
        If no history matches, it falls back to the master Company settings.
        """
        # Find the most recent policy that started BEFORE or ON the target date
        policy = self.policy_history.filter(effective_from__lte=target_date).order_by('-effective_from').first()
        return policy if policy else self

    def save(self, *args, **kwargs):
        # We only check for changes if the Company already exists (update mode)
        if self.pk:
            # 1. Fetch the OLD settings from the database before they are overwritten
            # We use .get() to see what is currently saved in the DB
            old_data = Company.objects.get(pk=self.pk)

            # 2. Compare every timing field to see if HR changed anything
            timing_changed = (
                    old_data.office_start_time != self.office_start_time or
                    old_data.office_end_time != self.office_end_time or
                    old_data.grace_minutes != self.grace_minutes or
                    old_data.grace_allowed_count != self.grace_allowed_count or
                    old_data.half_day_threshold_hours != self.half_day_threshold_hours or
                    old_data.full_day_threshold_hours != self.full_day_threshold_hours or
                    old_data.overtime_threshold_hours != self.overtime_threshold_hours
            )

            # 3. If settings changed, we create a "Snapshot" of the NEW rules
            # starting from today (or the start of the month)
            if timing_changed:
                from datetime import date
                # We import AttendancePolicy inside to avoid circular import issues
                from .models import AttendancePolicy

                AttendancePolicy.objects.create(
                    company=self,
                    office_start_time=self.office_start_time,
                    office_end_time=self.office_end_time,
                    grace_minutes=self.grace_minutes,
                    grace_allowed_count=self.grace_allowed_count,
                    half_day_threshold_hours=self.half_day_threshold_hours,
                    full_day_threshold_hours=self.full_day_threshold_hours,
                    overtime_threshold_hours=self.overtime_threshold_hours,
                    effective_from=date.today()
                )

        # Finally, save the main Company record
        super().save(*args, **kwargs)

class Department(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='departments')
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        unique_together = ('company', 'name')

    def __str__(self):
        return f'{self.name} ({self.company})'


class Designation(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='designations')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='designations')
    title = models.CharField(max_length=150)
    level = models.PositiveSmallIntegerField(default=1)

    def __str__(self):
        return self.title

class HolidayCalendar(TimeStampedModel):
    """
    Acts as a Template (e.g., 'South India Calendar', 'General Office Calendar').
    """
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='holiday_calendars')
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False, help_text="Default calendar for new employees")

    def __str__(self):
        return f"{self.name} ({self.company.name})"


class Holiday(TimeStampedModel):
    class HolidayType(models.TextChoices):
        NATIONAL = 'national', 'National'
        FESTIVAL = 'festival', 'Festival'
        OPTIONAL = 'optional', 'Optional'

    # CHANGE THIS: Instead of linking to Company, link to HolidayCalendar
    calendar = models.ForeignKey(HolidayCalendar, on_delete=models.CASCADE, related_name='holidays',        null=True,   # Add this
        blank=True   )
    date = models.DateField()
    name = models.CharField(max_length=150)
    type = models.CharField(max_length=20, choices=HolidayType.choices, default=HolidayType.NATIONAL)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ('calendar', 'date', 'name')

    def __str__(self):
        return f'{self.name} - {self.date}'


# ---------------------------------------------------------------------------
# 2. EMPLOYEE
# ---------------------------------------------------------------------------
class Employee(TimeStampedModel):
    class EmploymentType(models.TextChoices):
        FULL_TIME = 'full_time', 'Full Time'
        PART_TIME = 'part_time', 'Part Time'
        CONTRACT = 'contract', 'Contract'
        INTERN = 'intern', 'Intern'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ON_LEAVE = 'on_leave', 'On Leave'
        SUSPENDED = 'suspended', 'Suspended'
        RELIEVED = 'relieved', 'Relieved'

    class Gender(models.TextChoices):
        MALE = 'M', 'Male'
        FEMALE = 'F', 'Female'
        OTHER = 'O', 'Other'

        # 1. Add MaritalStatus enum
    class MaritalStatus(models.TextChoices):
        SINGLE = 'single', 'Single'
        MARRIED = 'married', 'Married'
        DIVORCED = 'divorced', 'Divorced'
        WIDOWED = 'widowed', 'Widowed'

    # Link to the login account (inventory.User). Optional: HR can create the
    # HR record before an employee is issued login credentials.
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='employee_profile')

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='employees')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='employees')
    designation = models.ForeignKey(Designation, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='employees')

    employee_code = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    marital_status = models.CharField(
        max_length=15,
        choices=MaritalStatus.choices,
        default=MaritalStatus.SINGLE,
        blank=True
    )
    date_of_birth = models.DateField(null=True, blank=True)
    father_name = models.CharField(max_length=200, blank=True)
    mother_name = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True, help_text="Residential/Permanent Address")
    emergency_contact = models.TextField(
        blank=True,
        help_text="Name, relationship, and phone number of person to contact in emergency"
    )


    date_of_joining = models.DateField()
    date_of_confirmation = models.DateField(null=True, blank=True)

    employment_type = models.CharField(max_length=20, choices=EmploymentType.choices,
                                        default=EmploymentType.FULL_TIME)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_manager = models.BooleanField(
        default=False,
        help_text="Designates if this employee can act as a reporting manager."
    )

    reporting_manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subordinates',
        help_text="Select direct reporting manager"
    )

    is_finance = models.BooleanField(
        default=False,
        help_text='Designates whether this employee has access to Payroll, Payslips, and Salary Ledgers.'
    )
    # NEW: Allows HR to be assigned to multiple companies
    managed_companies = models.ManyToManyField(
        'Company',
        blank=True,
        related_name='managers',
        help_text="For HR/Admins: Which companies can this user manage?"
    )
    holiday_calendar = models.ForeignKey(
        HolidayCalendar,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
        help_text="Assign regional holiday template to this employee"
    )

    @property
    def is_eligible_for_maternity_leave(self) -> bool:
        return (
                self.gender == self.Gender.FEMALE
                and self.marital_status == self.MaritalStatus.MARRIED
        )

    @property
    def is_eligible_for_paternity_leave(self) -> bool:
        return (
                self.gender == self.Gender.MALE
                and self.marital_status == self.MaritalStatus.MARRIED
        )

    # ADDED: Attendance Policy Mode (Office Kiosk vs Remote/Field Allowed)
    # =========================================================================
    class AttendanceMode(models.TextChoices):
        OFFICE_ONLY = 'office_only', 'In-Office Kiosk Only (Delhi)'
        REMOTE_FIELD = 'remote_field', 'Remote / Field Allowed (Mobile)'

    attendance_mode = models.CharField(
        max_length=20,
        choices=AttendanceMode.choices,
        default=AttendanceMode.OFFICE_ONLY,
        help_text="Office Only: Punch via office tablet at reception. Remote/Field: Can punch via personal phone."
    )

    # Leave Policy Enforcement Mode (Annual-Wise vs Month-Wise)
    class LeavePolicyMode(models.TextChoices):
        ANNUAL = 'annual', 'Annual-Wise (Full Year Pool)'
        MONTHLY = 'monthly', 'Month-Wise (Accrued/Capped)'

    leave_policy_mode = models.CharField(
        max_length=15,
        choices=LeavePolicyMode.choices,
        default=LeavePolicyMode.ANNUAL,
        help_text="Enforcement mode: 'annual' allows taking leaves from annual balance anytime; 'monthly' caps leaves to monthly accrual."
    )

    def is_holiday(self, target_date):
        """Helper to check if a specific date is a holiday for this employee."""
        if not self.holiday_calendar:
            return False
        return self.holiday_calendar.holidays.filter(date=target_date).exists()

    def save(self, *args, **kwargs):
        if not self.employee_code:
            # 1. Find the highest existing employee code string
            # This looks for the "largest" string alphabetically (e.g., OHCE0098 > OHCE0043)
            max_code = Employee.objects.aggregate(Max('employee_code'))['employee_code__max']

            if max_code:
                # 2. Extract digits from the string "OHCE0098" -> 98
                nums = re.findall(r'\d+', max_code)
                if nums:
                    last_number = int(nums[0])
                    new_number = last_number + 1
                else:
                    new_number = 1
            else:
                # Start at 1 if no employees exist
                new_number = 1

            # 3. Format to OHCE + 4 digits (e.g., OHCE0099)
            self.employee_code = f'OHCE{new_number:04d}'

        super(Employee, self).save(*args, **kwargs)

    class Meta:
        ordering = ['employee_code']

    def __str__(self):
        return f'{self.employee_code} - {self.first_name} {self.last_name}'.strip()

    @property
    def full_name(self):
        name = f"{self.first_name} {self.last_name}".strip()
        return name if name else (self.email or self.employee_code)


class EmployeeBankDetail(TimeStampedModel):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='bank_detail')
    account_holder = models.CharField(max_length=150)
    account_number = models.CharField(max_length=40)
    ifsc_code = models.CharField('IFSC Code', max_length=15)
    bank_name = models.CharField(max_length=150)
    branch_name = models.CharField(max_length=150, blank=True)
    account_type = models.CharField(max_length=20, blank=True)
    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.employee} bank detail'


class EmployeeDocument(TimeStampedModel):
    class DocumentType(models.TextChoices):
        AADHAAR = 'aadhaar', 'Aadhaar Card'
        PAN = 'pan', 'PAN Card'
        OFFER_LETTER = 'offer_letter', 'Offer Letter'
        RESUME = 'resume', 'Resume'
        EXPERIENCE = 'experience_letter', 'Experience Letter'
        MARKSHEET = 'marksheet', '10th/12th Marksheet'
        BANK_PROOF = 'bank_proof', 'Bank Proof'
        ID_PROOF = 'id_proof', 'ID Proof'
        ADDRESS_PROOF = 'address_proof', 'Address Proof'
        EDUCATION = 'education', 'Education Certificate'
        OTHER = 'other', 'Other'

    class VerificationStatus(models.TextChoices):
        PENDING = 'pending', 'Pending Verification'
        VERIFIED = 'verified', 'Verified'
        REJECTED = 'rejected', 'Rejected'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=30, choices=DocumentType.choices)
    file = models.FileField(upload_to='employees/documents/')
    description = models.CharField(max_length=255, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expiry_on = models.DateField(null=True, blank=True)
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_documents'
    )
    verified_on = models.DateTimeField(null=True, blank=True)
    rejection_remarks = models.TextField(blank=True)

    def __str__(self):
        return f'{self.employee} - {self.get_document_type_display()}'


class EmployeeNotice(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='notices')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    notice_date = models.DateField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.title} - {self.employee}'


# ---------------------------------------------------------------------------
# 3. HIRING / RECRUITMENT PIPELINE
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 3. HIRING / RECRUITMENT PIPELINE
# ---------------------------------------------------------------------------
class RecruitmentStage(TimeStampedModel):
    """Reusable stage definitions (e.g. 'Technical Round', 'Culture Fit').
    Stages are composed, in whatever order a job needs, via JobPipeline —
    they are not tied to any one job posting."""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    # ADD THIS:
    evaluation_criteria = models.JSONField(
        default=dict,
        blank=True,
        help_text='Define what to grade, e.g. {"Python": 5, "Communication": 5}'
    )

    is_default = models.BooleanField(
        default=False,
        help_text='If checked, this stage is auto-added to every new JobPipeline.')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class JobPosting(TimeStampedModel):
    class JobType(models.TextChoices):
        FULL_TIME = 'full_time', 'Full Time'
        PART_TIME = 'part_time', 'Part Time'
        CONTRACT = 'contract', 'Contract'
        INTERN = 'intern', 'Intern'

    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='job_postings')
    department = models.ForeignKey('Department', on_delete=models.SET_NULL, null=True, blank=True)
    designation = models.ForeignKey('Designation', on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=200)
    job_type = models.CharField(max_length=20, choices=JobType.choices, default=JobType.FULL_TIME)
    location = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)
    requirements = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    posted_on = models.DateField(auto_now_add=True)
    closing_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.title

    def ordered_pipeline(self):
        """Convenience accessor used by templates/views: this job's stages,
        in hiring order."""
        return self.pipeline_stages.select_related('stage').order_by('order')


class JobPipeline(TimeStampedModel):
    """The ordered sequence of RecruitmentStages for ONE specific JobPosting —
    each job can define its own unique hiring flow (e.g. a Sales role might
    skip 'Technical Round' entirely)."""
    job = models.ForeignKey(JobPosting, on_delete=models.CASCADE, related_name='pipeline_stages')
    stage = models.ForeignKey(RecruitmentStage, on_delete=models.CASCADE, related_name='job_pipelines')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('job', 'stage')
        ordering = ['job', 'order']

    def __str__(self):
        return f'{self.job} — [{self.order}] {self.stage}'


class Candidate(TimeStampedModel):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    resume = models.FileField(upload_to='candidates/resumes/', blank=True, null=True)
    current_company = models.CharField(max_length=150, blank=True)
    experience_years = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    city = models.CharField(max_length=100, blank=True, help_text="Candidate's current city")
    state = models.CharField(max_length=100, blank=True, help_text="Candidate's current state")

    def __str__(self):
        return f'{self.first_name} {self.last_name}'.strip()


class Application(TimeStampedModel):
    class Status(models.TextChoices):
        APPLIED = 'applied', 'Applied'
        SHORTLISTED = 'shortlisted', 'Shortlisted'
        INTERVIEWING = 'interviewing', 'Interviewing'
        OFFERED = 'offered', 'Offered'
        REJECTED = 'rejected', 'Rejected'
        HIRED = 'hired', 'Hired'

    class Source(models.TextChoices):
        LINKEDIN = 'linkedin', 'LinkedIn'
        INDEED = 'indeed', 'Indeed'
        REFERRAL = 'referral', 'Referral'
        CAREER_SITE = 'career_site', 'Career Site'

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='applications')
    job_posting = models.ForeignKey(JobPosting, on_delete=models.CASCADE, related_name='applications')
    applied_on = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    source = models.CharField(max_length=20, choices=Source.choices, blank=True)
    cover_letter = models.TextField(blank=True)

    # --- ATS metadata ---
    expected_ctc = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    current_ctc = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notice_period = models.PositiveIntegerField(default=0, help_text='Notice period, in days.')
    current_stage = models.ForeignKey(RecruitmentStage, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='applications',
                                       help_text="Where this candidate currently sits in the job's pipeline.")
    progress_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    referred_by = models.ForeignKey(
        'Employee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='referrals',
        help_text="Employee who referred this candidate"
    )
    rejection_reason = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Reason for rejection (e.g., Salary mismatch, Skill gap)"
    )
    rejection_notes = models.TextField(
        blank=True,
        null=True,
        help_text="Detailed internal notes on why the candidate was not a fit"
    )

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None

        if not is_new:
            old_instance = Application.objects.get(pk=self.pk)
            old_status = old_instance.status

        super().save(*args, **kwargs)

        # Create audit log if status changed or it's a new record
        if is_new or old_status != self.status:
            RecruitmentAuditLog.objects.create(
                application=self,
                from_status=old_status or "New",
                to_status=self.status,
                action=f"Status changed to {self.get_status_display()}",
                note="Automatic system log"
            )

    def recompute_progress(self):
        """Sets progress_percentage from current_stage's position in the
        job's pipeline. Call after changing current_stage; not automatic on
        save() so bulk updates don't pay the query cost."""
        total = self.job_posting.pipeline_stages.count()
        if not total or not self.current_stage_id:
            return
        position = self.job_posting.pipeline_stages.filter(
            order__lte=self.job_posting.pipeline_stages.get(stage_id=self.current_stage_id).order
        ).count()
        self.progress_percentage = round((position / total) * 100, 2)
        self.save(update_fields=['progress_percentage', 'updated_at'])

    # --- Locking / confirmed-outcome protection ---
    is_locked = models.BooleanField(
        default=False,
        help_text='Set automatically once the outcome is confirmed (Hired/Rejected). '
                   'A locked application can only be modified by a Super Admin.')
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='locked_applications')
    locked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('candidate', 'job_posting')

    def __str__(self):
        return f'{self.candidate} -> {self.job_posting}'


class RecruitmentAuditLog(TimeStampedModel):
    """Every status transition / significant action on an Application, with
    who did it — the audit trail the prompt asked for."""
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='audit_logs')
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=150)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='recruitment_actions')
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.application} — {self.action}'


class Interview(TimeStampedModel):
    class Mode(models.TextChoices):
        ONLINE = 'online', 'Online'
        IN_PERSON = 'in_person', 'In Person'
        PHONE = 'phone', 'Phone'

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        NO_SHOW = 'no_show', 'No Show'

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='interviews')
    pipeline_stage = models.ForeignKey(
        JobPipeline, on_delete=models.SET_NULL, null=True, blank=True, related_name='interviews',
        help_text="Which stage of the job's own pipeline this interview covers.")
    interview_round = models.CharField(
        max_length=100, blank=True,
        help_text='Free-text label — kept for cases with no matching JobPipeline stage.')
    scheduled_on = models.DateTimeField()
    # ManyToManyField , allows multiple employees to see the candidate on their dashboard and submit their own feedback for the same round
    # interviewer = models.ManyToManyField ('Employee', on_delete=models.SET_NULL, null=True, blank=True,related_name='interviews_conducted',help_text="The panel of employees conducting this interview")
    interviewer = models.ManyToManyField(
        'Employee',
        blank=True,
        related_name='interviews_conducted',
        help_text="The panel of employees conducting this interview"
    )

    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.ONLINE)
    feedback = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED)
    scorecard = models.JSONField(
        default=dict, blank=True,
        help_text='Structured ratings, e.g. {"technical": 4, "communication": 5, "culture": 3}')

    def average_score(self):
        """Handy for templates: mean of whatever numeric ratings are in
        scorecard, or None if it's empty."""
        values = [v for v in self.scorecard.values() if isinstance(v, (int, float))]
        return round(sum(values) / len(values), 2) if values else None

    def __str__(self):
        label = self.pipeline_stage.stage.name if self.pipeline_stage_id else (self.interview_round or 'Interview')
        return f'{self.application} - {label}'

# this model allows every person on the panel to give their own private rating.

class InterviewFeedback(TimeStampedModel):
    """Individual feedback from each person on the interview panel."""
    interview = models.ForeignKey(
        'Interview',
        on_delete=models.CASCADE,
        related_name='individual_feedbacks'
    )
    interviewer = models.ForeignKey(
        'Employee',
        on_delete=models.CASCADE
    )
    feedback_text = models.TextField(blank=True)
    scorecard_filled = models.JSONField(
        default=dict,
        help_text="The actual scores given based on the Stage evaluation_criteria"
    )
    recommendation = models.CharField(
        max_length=20,
        choices=[('hire', 'Hire'), ('maybe', 'Maybe'), ('reject', 'Reject')],
        default='maybe'
    )

    def __str__(self):
        return f"Feedback from {self.interviewer} for {self.interview.application.candidate}"



# Add this to allow HR and Managers to "chat" about a candidate.
class ApplicationNote(TimeStampedModel):
    """Internal discussion/comments for a specific application."""
    application = models.ForeignKey(
        'Application',
        on_delete=models.CASCADE,
        related_name='notes'
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    message = models.TextField()
    is_private = models.BooleanField(
        default=False,
        help_text="If True, only HR can see this note"
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Note by {self.author} on {self.application}"
class OfferTemplate(TimeStampedModel):
    """Reusable HTML offer-letter templates with {{ placeholder }} tokens
    that OfferLetter.content is generated from."""
    name = models.CharField(max_length=150)
    body_html = models.TextField(
        help_text='HTML body. Supported placeholders: {{candidate_name}}, {{job_title}}, '
                   '{{company_name}}, {{ctc_offered}}, {{offer_date}}, {{joining_date}}.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def render(self, application, ctc_offered, offer_date, joining_date=None):
        """Simple, dependency-free token substitution. Swap for a proper
        template engine (Jinja2 / Django Template) later if you need
        loops/conditionals in offer bodies."""
        tokens = {
            '{{candidate_name}}': str(application.candidate),
            '{{job_title}}': application.job_posting.title,
            '{{company_name}}': str(application.job_posting.company),
            '{{ctc_offered}}': str(ctc_offered),
            '{{offer_date}}': offer_date.strftime('%d %b %Y') if offer_date else '',
            '{{joining_date}}': joining_date.strftime('%d %b %Y') if joining_date else 'TBD',
        }
        html = self.body_html
        for token, value in tokens.items():
            html = html.replace(token, value)
        return html


class OfferLetter(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SENT = 'sent', 'Sent'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'
        EXPIRED = 'expired', 'Expired'

    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name='offer_letter')
    template = models.ForeignKey(OfferTemplate, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='offer_letters')
    offer_date = models.DateField()
    ctc_offered = models.DecimalField(max_digits=12, decimal_places=2)
    joining_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    expiry_date = models.DateField(null=True, blank=True)
    content = models.TextField(
        blank=True,
        help_text='Final offer content (rendered from the chosen template, then hand-edited).')
    generated_pdf = models.FileField(upload_to='offers/generated/', blank=True, null=True)

    def __str__(self):
        return f'Offer - {self.application.candidate}'

# ---------------------------------------------------------------------------
# 4. ATTENDANCE & GRACE POLICY
# ---------------------------------------------------------------------------
class AttendancePolicy(TimeStampedModel):
    # company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name='attendance_policy')
    # company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='policy_archives')
    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='policy_history')

    # Use EXACT same names as Company model
    office_start_time = models.TimeField()
    office_end_time = models.TimeField()
    grace_minutes = models.PositiveIntegerField(default=15)
    grace_allowed_count = models.PositiveIntegerField(default=3)
    half_day_threshold_hours = models.DecimalField(max_digits=4, decimal_places=2, default=4)
    full_day_threshold_hours = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    overtime_threshold_hours = models.DecimalField(max_digits=4, decimal_places=2, default=9, null=True,blank=True)
    # This rule was active until the end of this date
    effective_from = models.DateField()

    def __str__(self):
        return f"{self.company.name} Policy (Starts {self.effective_from})"

    class Meta:
        ordering = ['-effective_from'] # Newest versions at the top


class AttendanceRecord(TimeStampedModel):
    class Status(models.TextChoices):
        PRESENT = 'present', 'Present'
        ABSENT = 'absent', 'Absent'
        HALF_DAY = 'half_day', 'Half Day'
        ON_LEAVE = 'on_leave', 'On Leave'
        HOLIDAY = 'holiday', 'Holiday'
        WEEK_OFF = 'week_off', 'Week Off'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendance_records')
    attendance_date = models.DateField()
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESENT)
    early_minutes = models.PositiveIntegerField(default=0)
    late_minutes = models.PositiveIntegerField(default=0)
    is_half_day = models.BooleanField(default=False)
    is_overtime = models.BooleanField(default=False)
    remarks = models.CharField(max_length=255, blank=True)

    # --- GEOLOCATION & BIOMETRICS FIELDS ---
    punch_in_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    punch_in_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    punch_out_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    punch_out_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    punch_in_photo = models.ImageField(upload_to='attendance/punches/', null=True, blank=True)
    is_face_verified = models.BooleanField(default=False)
    punch_source = models.CharField(
        max_length=20,
        choices=[('kiosk', 'Office Kiosk'), ('mobile', 'Remote/Mobile')],
        default='mobile'
    )

    # Module D: Audit trail for manual punch edits
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='attendance_edits', help_text='User who last manually edited this record'
    )
    edited_on = models.DateTimeField(null=True, blank=True, help_text='When the record was last manually edited')
    edit_reason = models.TextField(blank=True, help_text='Reason for the manual edit')
    manual_override = models.BooleanField(
        default=False,
        help_text='HR Quick Action: status is pinned by HR and must not be recomputed from punch hours'
    )

    class Meta:
        unique_together = ('employee', 'attendance_date')
        ordering = ['-attendance_date']

    def save(self, *args, **kwargs):
        # 1. Safe Policy Resolution
        policy = None
        if self.employee and getattr(self.employee, 'company', None):
            if hasattr(self.employee.company, 'get_policy_for_date'):
                try:
                    policy = self.employee.company.get_policy_for_date(self.attendance_date)
                except Exception:
                    policy = None
            if not policy:
                policy = AttendancePolicy.objects.filter(company=self.employee.company).first()

        # 2. Extract threshold values safely
        full_day_thresh = Decimal(str(getattr(policy, 'full_day_threshold_hours', '8.0') or '8.0'))
        half_day_thresh = Decimal(str(getattr(policy, 'half_day_threshold_hours', '4.0') or '4.0'))
        ot_thresh = getattr(policy, 'overtime_threshold_hours', None)
        ot_threshold_hours = Decimal(str(ot_thresh)) if ot_thresh is not None else None

        # 3. Calculate Gross Hours & Status when Check-In and Check-Out exist
        if self.check_in and self.check_out:
            diff = self.check_out - self.check_in
            gross_hours = Decimal(str(diff.total_seconds() / 3600)).quantize(Decimal('0.01'))
            self.total_hours = max(Decimal('0.00'), gross_hours)

            # Check if this record has a Grace Strike and employee completed shift till office_end_time
            is_grace_present = 'grace' in str(self.remarks).lower()
            local_out = timezone.localtime(self.check_out).time() if timezone.is_aware(self.check_out) else self.check_out.time()
            office_end = getattr(policy, 'office_end_time', None)
            stayed_until_end = bool(local_out and office_end and local_out >= office_end)

            # Apply thresholds if not already marked as specialized status (or pinned by HR)
            if not self.manual_override and self.status not in (self.Status.ON_LEAVE, self.Status.HOLIDAY, self.Status.WEEK_OFF):
                # Condition A: Agar Grace laga hai aur banda office time tak ruka, to ye Full Day (PRESENT) rahega
                if is_grace_present and stayed_until_end:
                    self.status = self.Status.PRESENT
                    self.is_half_day = False
                # Condition B: 8.0 ghante ya usse zyada
                elif self.total_hours >= full_day_thresh:
                    self.status = self.Status.PRESENT
                    self.is_half_day = False
                # Condition C: 4.0 ghante se zyada lekin short duration (without grace)
                elif self.total_hours >= half_day_thresh:
                    self.status = self.Status.HALF_DAY
                    self.is_half_day = True
                else:
                    self.status = self.Status.ABSENT
                    self.is_half_day = False

                # Overtime boolean flag
                if ot_threshold_hours is not None:
                    self.is_overtime = bool(self.total_hours >= ot_threshold_hours)
                else:
                    self.is_overtime = False

        elif self.check_in and not self.check_out:
            # Single punch scenario (checked in only)
            self.total_hours = Decimal('0.00')
            self.is_overtime = False
            if not self.manual_override and self.status not in (self.Status.ON_LEAVE, self.Status.HOLIDAY, self.Status.WEEK_OFF):
                self.status = self.Status.HALF_DAY
                self.is_half_day = True

        # 4. Calculate Late Arrival Minutes
        office_start = getattr(policy, 'office_start_time', None) or time(9, 0)
        if self.check_in and office_start:
            local_in = timezone.localtime(self.check_in)
            sched_start = timezone.make_aware(
                datetime.combine(self.attendance_date, office_start),
                timezone.get_current_timezone()
            )
            if local_in > sched_start:
                late_secs = (local_in - sched_start).total_seconds()
                self.late_minutes = int(late_secs // 60)
            else:
                self.late_minutes = 0

        super().save(*args, **kwargs)

        # 5. Late arrival penalty trigger (Only if Grace is exhausted and it actually is Half Day)
        if (not self.manual_override and self.status == self.Status.HALF_DAY
                and self.late_minutes > 0 and self.employee_id):
            try:
                from .services import process_late_arrival_penalty
                process_late_arrival_penalty(self)
            except Exception:
                pass

    def __str__(self):
        return f'{self.employee} - {self.attendance_date}'

    @property
    def is_grace_applied(self):
        return 'grace' in str(self.remarks).lower()
# --- NEW MODEL: BIOMETRIC PROFILES ---
class EmployeeBiometric(TimeStampedModel):
    """Stores the reference 128-dimensional facial encoding vector for an employee."""
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='biometric')
    face_encoding = models.JSONField(help_text="128-dimensional biometric facial embedding array")
    registered_photo = models.ImageField(upload_to='employees/faces/')

    def __str__(self):
        return f"Biometric Profile - {self.employee.full_name}"


# --- NEW MODEL:  LIVE BREADCRUMB LOCATION LOGS ---
class EmployeeLocationLog(TimeStampedModel):
    """Continuous GPS tracking coordinates logged while the employee is on duty."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='location_logs')
    attendance_record = models.ForeignKey(AttendanceRecord, on_delete=models.CASCADE, related_name='locations')
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    accuracy_meters = models.FloatField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']

    def __str__(self):
        return f"{self.employee.full_name} @ {self.recorded_at.strftime('%I:%M %p')}"


class GraceUsageTracker(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='grace_usages')
    month = models.PositiveSmallIntegerField()  # 1-12
    year = models.PositiveSmallIntegerField()
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('employee', 'month', 'year')

    def __str__(self):
        return f'{self.employee} - {self.month}/{self.year}'


class AttendancePenalty(TimeStampedModel):
    class DeductionStatus(models.TextChoices):
        APPLIED = 'applied', 'Applied'
        LWP = 'lwp', 'LWP (No Leave Balance)'
        WAIVED = 'waived', 'Waived'
        INTERN_SALARY = 'intern_salary', 'Intern Salary Deduction'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='penalties')
    attendance_record = models.ForeignKey('AttendanceRecord', on_delete=models.CASCADE, null=True, blank=True, related_name='penalties')
    penalty_date = models.DateField()
    reason = models.CharField(max_length=255, default='Late Arrival (Exceeded Grace Period)')
    late_minutes = models.PositiveIntegerField(default=0)
    deduction_days = models.DecimalField(max_digits=4, decimal_places=2, default=0.5)
    deduction_source = models.CharField(max_length=100, default='CL')
    status = models.CharField(max_length=20, choices=DeductionStatus.choices, default=DeductionStatus.APPLIED)
    # Intern penalty fields
    is_intern_penalty = models.BooleanField(default=False, help_text='True if this penalty is a direct salary deduction for an intern')
    salary_deduction_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='For interns: the monetary amount deducted (half-day salary)'
    )
    employment_type_snapshot = models.CharField(
        max_length=20, blank=True,
        help_text='Employment type at time of penalty (full_time, intern, etc)'
    )

    class Meta:
        ordering = ['-penalty_date', '-created_at']

    def __str__(self):
        return f"Penalty: {self.employee} - {self.penalty_date} ({self.deduction_source})"

    @property
    def late_duration(self):
        return self.late_minutes

    @property
    def leave_deducted(self):
        return self.deduction_days

    @property
    def logged_on(self):
        return self.created_at



# ---------------------------------------------------------------------------
# 5. LEAVE MANAGEMENT
# ---------------------------------------------------------------------------
class LeaveType(TimeStampedModel):
    class Gender(models.TextChoices):
        ALL = 'all', 'All'
        MALE = 'M', 'Male'
        FEMALE = 'F', 'Female'

    class ApplicableMaritalStatus(models.TextChoices):
        ALL = 'all', 'All'
        MARRIED = 'married', 'Married Only'
        UNMARRIED = 'single', 'Unmarried Only'
    class AllocationMode(models.TextChoices):
        ANNUAL = 'annual', 'Annual (Lump Sum)'
        MONTHLY_ACCRUED = 'monthly_accrued', 'Monthly Accrued'

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='leave_types')
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)
    days_per_year = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    is_paid = models.BooleanField(default=True)
    is_carry_forward = models.BooleanField(default=False)
    max_carry_forward = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    max_consecutive_days = models.PositiveIntegerField(null=True, blank=True)
    requires_approval = models.BooleanField(default=True)
    requires_document = models.BooleanField(default=False)
    applicable_gender = models.CharField(max_length=5, choices=Gender.choices, default=Gender.ALL)
    applicable_marital_status = models.CharField(
        max_length=15,
        choices=ApplicableMaritalStatus.choices,
        default=ApplicableMaritalStatus.ALL,
        help_text='Marital status eligible for this leave'
    )
    description = models.TextField(blank=True)
    # Module B enhancements
    allocation_mode = models.CharField(
        max_length=20, choices=AllocationMode.choices, default=AllocationMode.ANNUAL,
        help_text='Annual: full allocation at start. Monthly: accrued each month (CL/EL).'
    )
    min_consecutive_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Minimum consecutive days required (e.g., SL requires 2 days minimum).'
    )
    requires_relationship = models.BooleanField(
        default=False, help_text='If True, employee must specify relationship (e.g., Bereavement Leave).'
    )
    requires_stage = models.BooleanField(
        default=False, help_text='If True, employee must specify Pre-Natal/Post-Natal stage (Maternity/Paternity).'
    )

    class Meta:
        unique_together = ('company', 'code')

    def __str__(self):
        return self.name


class LeaveBalance(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_balances')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='balances')
    year = models.PositiveSmallIntegerField()
    allocated = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    used = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    pending = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    carried_forward = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        unique_together = ('employee', 'leave_type', 'year')

    def __str__(self):
        return f'{self.employee} - {self.leave_type} ({self.year})'

    @property
    def available(self):
        return self.allocated + self.carried_forward - self.used - self.pending


class LeaveApplication(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PENDING_MANAGER = 'pending_manager', 'Pending Manager Approval'
        PENDING_HR = 'pending_hr', 'Pending HR Approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        CANCELLED = 'cancelled', 'Cancelled'

    class DayType(models.TextChoices):
        FULL_DAY = 'full', 'Full Day'
        HALF_DAY = 'half', 'Half Day'

    class Relationship(models.TextChoices):
        MOTHER = 'mother', 'Mother'
        FATHER = 'father', 'Father'
        GRANDPARENT = 'grandparent', 'Grandparent'
        SIBLING = 'sibling', 'Sibling'
        SPOUSE = 'spouse', 'Spouse'
        OTHER = 'other', 'Other'

    class LeaveStage(models.TextChoices):
        PRE_NATAL = 'pre_natal', 'Pre-Natal Leave'
        POST_NATAL = 'post_natal', 'Post-Natal Leave'

    day_type = models.CharField(max_length=10, choices=DayType.choices, default=DayType.FULL_DAY)

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_applications')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='applications')
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    applied_on = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leave_approvals'
    )
    approved_on = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    # Module B: Document & specialized leave fields
    supporting_document = models.FileField(
        upload_to='leave/documents/', null=True, blank=True,
        help_text='Medical certificate (SL), proof for Maternity/Paternity/Bereavement'
    )
    relationship = models.CharField(
        max_length=20, choices=Relationship.choices, blank=True,
        help_text='Required for Bereavement Leave — specify relation to deceased'
    )
    leave_stage = models.CharField(
        max_length=20, choices=LeaveStage.choices, blank=True,
        help_text='Required for Maternity/Paternity — Pre-Natal or Post-Natal'
    )

    # Module C: Multi-tier approval tracking
    manager_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='manager_leave_approvals',
        help_text='The reporting manager who approved at Level 1'
    )
    manager_approved_on = models.DateTimeField(null=True, blank=True)
    parent_application = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reapplications',
        help_text='Links to the rejected application this re-application is based on'
    )
    sla_deadline = models.DateTimeField(
        null=True, blank=True,
        help_text='Expected deadline for approval review (for SLA tracking)'
    )

    class Meta:
        ordering = ['-applied_on']

    def __str__(self):
        return f'{self.employee} - {self.leave_type} ({self.start_date} to {self.end_date})'

from django.db import models




from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=Employee)
def auto_create_leave_bank_row(sender, instance, created, **kwargs):
    """
    When a new Employee is saved, this automatically creates
    a row in EmployeeLeaveBalance with 0.0 values.
    """
    if created:
        EmployeeLeaveBalance.objects.get_or_create(e_name=instance)
        EmployeeLeaveBalanceLive.objects.get_or_create(e_name=instance)


from django.db.models.signals import post_save
from django.dispatch import receiver
from datetime import timedelta




from django.db.models.signals import post_save
from django.dispatch import receiver
from datetime import timedelta
# Assuming your logic file is named leave_logic.py
from . import leave_logic as lv

@receiver(post_save, sender=LeaveApplication)
def sync_leave_and_attendance_on_approval(sender, instance, created, **kwargs):
    """
    1. Deducts leave from bank using Priority Logic (CL -> EL fallback).
    2. Automatically creates AttendanceRecord entries as 'ON_LEAVE'.
    3. Handles Refund if leave is cancelled.
    # If the application was created by the auto-approval engine,
    # auto_convert_absent_to_leaves() has ALREADY deducted the exact balance.
    """
    if 'auto-approved' in str(instance.reason).lower():
        return
    if instance.status == LeaveApplication.Status.APPROVED:
        # --- PART A: Smart Deduction from Leave Bank ---
        # This function now handles the fallback: Specific Code -> CL -> EL -> LWP
        result_msg = lv.adjust_leave_bank(
            employee=instance.employee,
            amount=instance.total_days,
            action="deduct",
            leave_code=instance.leave_type.code
        )

        # --- PART B: Create Attendance Records for the leave period ---
        current_date = instance.start_date
        while current_date <= instance.end_date:
            AttendanceRecord.objects.update_or_create(
                employee=instance.employee,
                attendance_date=current_date,
                defaults={
                    'status': AttendanceRecord.Status.ON_LEAVE,
                    # We store the leave code + the bank result (e.g., "SL | Deducted from CL")
                    'remarks': f"{instance.leave_type.code.upper()} | {result_msg}",
                }
            )
            current_date += timedelta(days=1)

    # Handle Cancellation/Rejection: If an APPROVED leave is cancelled, give the days back
    elif instance.status in [LeaveApplication.Status.REJECTED, LeaveApplication.Status.CANCELLED]:
        # 1. Refund the bank
        lv.adjust_leave_bank(
            employee=instance.employee,
            amount=instance.total_days,
            action="refund",
            leave_code=instance.leave_type.code
        )

        # 2. Reset Attendance Records
        AttendanceRecord.objects.filter(
            employee=instance.employee,
            attendance_date__range=[instance.start_date, instance.end_date],
            status=AttendanceRecord.Status.ON_LEAVE
        ).update(status=AttendanceRecord.Status.ABSENT, remarks="Leave Cancelled")

#this is leave bank which automatically calculate n assign employees to their annual leave
class EmployeeLeaveBalance(models.Model):
    # Link directly to your existing Employee table
    e_name = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assigned_leaves")

    # Status can stay here if 'Leave Status' is different from 'Work Status'
    # Otherwise, you can also pull 'status' from the Employee model.
    # status = models.CharField(max_length=100, default="Active")
    @property
    def work_status(self):
        """Fetches the live status directly from the Employee model."""
        return self.e_name.get_status_display()

    # Leave Balances
    bereavement_leave = models.FloatField(default=0.0)
    menstrual_leave = models.FloatField(default=0.0)
    sick_leave = models.FloatField(default=0.0)
    earned_leave = models.FloatField(default=0.0)
    casual_leave = models.FloatField(default=0.0)
    comp_off = models.FloatField(default=0.0)

    def __str__(self):
        return f"Leave Balance for {self.e_name.name}"

    # These properties allow you to "see" the Dept/Desig without saving them here
    @property
    def department(self):
        return self.e_name.department

    @property
    def designation(self):
        return self.e_name.designation

    @property
    def employment_type(self):
        return self.e_name.get_employment_type_display()
    # ADD THIS LINE TO SHOW THE DATE
    @property
    def joining_date(self):
        return self.e_name.date_of_joining
    @property
    def confirmation_date(self):
        return self.e_name.date_of_confirmation

    @property
    def employee(self):
        return self.e_name



class EmployeeLeaveBalanceLive(models.Model):
    """PAGE 2: THE LIVE REPORT - Deductions happen here"""
    e_name = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="live_balances")
    bereavement_leave = models.FloatField(default=0.0)
    menstrual_leave = models.FloatField(default=0.0)
    sick_leave = models.FloatField(default=0.0)
    earned_leave = models.FloatField(default=0.0)
    casual_leave = models.FloatField(default=0.0)
    comp_off = models.FloatField(default=0.0)
    # Add these two fields:
    maternity_leave = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('20.00'))
    paternity_leave = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('10.00'))

    @property
    def employee(self):
        return self.e_name

    def __str__(self):
        return f"Live Balance for {self.e_name.full_name}"
@receiver(post_save, sender=Employee)
def auto_create_leave_rows(sender, instance, created, **kwargs):
    """Creates a row in both Bank and Live Balance when a new Employee is created."""
    if created:
        EmployeeLeaveBalance.objects.get_or_create(e_name=instance)
        EmployeeLeaveBalanceLive.objects.get_or_create(e_name=instance)

# --- THE SYNC SIGNAL (Bank -> Balance) ---
@receiver(post_save, sender=EmployeeLeaveBalance)
def sync_bank_assignment_to_live_balance(sender, instance, created, **kwargs):
    """
    This handles your requirement: 'if i increase manually leave in leave bank to yha b wo increase ho jaegi'
    """
    live, _ = EmployeeLeaveBalanceLive.objects.get_or_create(e_name=instance.e_name)

    live.casual_leave = instance.casual_leave
    live.sick_leave = instance.sick_leave
    live.earned_leave = instance.earned_leave
    live.menstrual_leave = instance.menstrual_leave
    live.bereavement_leave = instance.bereavement_leave
    live.comp_off = instance.comp_off
    live.save()



# ---------------------------------------------------------------------------
# 6. PAYROLL
# ---------------------------------------------------------------------------
class SalaryStructure(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='salary_structures')
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class SalaryComponent(TimeStampedModel):
    class ComponentType(models.TextChoices):
        EARNING = 'earning', 'Earning'
        DEDUCTION = 'deduction', 'Deduction'

    structure = models.ForeignKey(SalaryStructure, on_delete=models.CASCADE, related_name='components')
    name = models.CharField(max_length=100)
    component_type = models.CharField(max_length=20, choices=ComponentType.choices)
    is_earning = models.BooleanField(default=True)
    is_deduction = models.BooleanField(default=False)
    is_taxable = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['display_order']

    def __str__(self):
        return f'{self.name} ({self.structure})'


class EmployeeSalary(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='salaries')
    structure = models.ForeignKey(SalaryStructure, on_delete=models.SET_NULL, null=True, related_name='employee_salaries')
    ctc_annual = models.DecimalField(max_digits=12, decimal_places=2)
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    hra = models.DecimalField('HRA', max_digits=12, decimal_places=2, default=0)
    special_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pf_employee = models.DecimalField('PF (Employee)', max_digits=12, decimal_places=2, default=0)
    pf_employer = models.DecimalField('PF (Employer)', max_digits=12, decimal_places=2, default=0)
    esic_employee = models.DecimalField('ESIC (Employee)', max_digits=12, decimal_places=2, default=0)
    esic_employer = models.DecimalField('ESIC (Employer)', max_digits=12, decimal_places=2, default=0)
    professional_tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tds = models.DecimalField('TDS', max_digits=12, decimal_places=2, default=0)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)



    class Meta:
        ordering = ['-effective_from']

    def __str__(self):
        return f'{self.employee} - CTC {self.ctc_annual}'


class PayrollRun(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        PAID = 'paid', 'Paid'

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='payroll_runs')
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    payroll_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        unique_together = ('company', 'month', 'year')
        ordering = ['-year', '-month']

    def __str__(self):
        return f'{self.company} Payroll {self.month}/{self.year}'


class PaySlip(TimeStampedModel):
    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'

    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payslips')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='payslips')

    # --- Attendance breakdown for the period (all in days) ---
    full_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    half_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    comp_off_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=0,
        help_text='Worked on a Sunday/Holiday — credited as a paid day.')
    off_days = models.DecimalField(
        max_digits=5, decimal_places=1, default=0,
        help_text='Weekly-offs/holidays the employee did not have to work — paid, non-working.')
    paid_leave_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    absent_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    paid_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    total_days_in_month = models.PositiveSmallIntegerField(default=30)

    # --- Money breakdown ---
    daily_wage = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    penalty_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    attendance_penalty = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Manual attendance penalty fees (Attendance Matrix > Add Penalty) deducted from salary')
    loan_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    extra_earning = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # --- Comp-off monthly encashment tracking ---
    comp_off_earned = models.DecimalField(max_digits=5, decimal_places=1, default=0, help_text='Comp-off credits earned in this month')
    comp_off_used = models.DecimalField(max_digits=5, decimal_places=1, default=0, help_text='Comp-off credits used in this month')
    comp_off_remaining = models.DecimalField(max_digits=5, decimal_places=1, default=0, help_text='Unused comp-off credits encashed in this payroll')
    comp_off_encashment = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Encashment amount added into salary for unused comp-off')

    net_pay = models.DecimalField(max_digits=12, decimal_places=2)
    total_earnings = models.DecimalField(max_digits=12, decimal_places=2)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices,
                                       default=PaymentStatus.PENDING)
    paid_on = models.DateField(null=True, blank=True)
    # SNAPSHOT FIELDS
    applied_start_time = models.TimeField(null=True, blank=True)
    applied_end_time = models.TimeField(null=True, blank=True)
    applied_grace_limit = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ('payroll_run', 'employee')

    def __str__(self):
        return f'{self.employee} - {self.payroll_run}'

    @property
    def gross_salary(self):
        """Fixed monthly salary (Basic+HRA+Special Allowance) excluding ad-hoc extras."""
        return self.total_earnings - self.extra_earning

    @property
    def monthly_ctc(self):
        """Calculates exact monthly package: Annual CTC / 12."""
        salary = self.employee.salaries.filter(is_active=True).first()
        if salary and salary.ctc_annual:
            return (salary.ctc_annual / Decimal('12.0')).quantize(Decimal('0.01'))
        return Decimal('0.00')

class LoanAdvance(TimeStampedModel):
    """A running loan/salary-advance whose monthly_installment is auto-deducted
    by the payroll engine each run, until remaining_balance hits zero."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='loans')
    description = models.CharField(max_length=255, blank=True)
    principal_amount = models.DecimalField(max_digits=12, decimal_places=2)
    monthly_installment = models.DecimalField(max_digits=12, decimal_places=2)
    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2)
    start_date = models.DateField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.employee} — {self.description or "Loan"} (₹{self.remaining_balance} left)'


class PayrollExtra(TimeStampedModel):
    """An ad-hoc one-off earning (incentive/bonus) queued for the employee's
    NEXT payroll run. Consumed (linked to a PayrollRun) once processed."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='payroll_extras')
    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='consumed_extras')
    label = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_consumed = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.employee} — {self.label} (₹{self.amount})'


# ---------------------------------------------------------------------------
# 7. POLICIES & COMPANY NOTICES
# ---------------------------------------------------------------------------
class Policy(TimeStampedModel):
    class Category(models.TextChoices):
        HR_POLICY = 'hr_policy', 'HR Policy'
        IT_POLICY = 'it_policy', 'IT Policy'
        SAFETY = 'safety', 'Safety & Compliance'
        FINANCE = 'finance', 'Finance'
        OTHER = 'other', 'Other'

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='policies')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.HR_POLICY)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to='policies/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_mandatory = models.BooleanField(default=True, help_text='Must be acknowledged by all employees')
    quiz_data = models.JSONField(
        default=list, blank=True,
        help_text='Quiz questions: [{"question": "...", "type": "mcq"|"true_false", '
                  '"options": [...], "correct": index_or_bool}]'
    )

    class Meta:
        verbose_name_plural = 'Policies'

    def __str__(self):
        return self.title

    @property
    def has_quiz(self):
        return bool(self.quiz_data)

    @property
    def quiz_question_count(self):
        return len(self.quiz_data) if self.quiz_data else 0

    def get_file_extension(self):
        if self.document:
            return self.document.name.rsplit('.', 1)[-1].lower() if '.' in self.document.name else ''
        return ''


class PolicyAcknowledgement(TimeStampedModel):
    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name='acknowledgements')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='policy_acknowledgements')
    acknowledged_on = models.DateTimeField(auto_now_add=True)
    # Quiz tracking
    quiz_responses = models.JSONField(
        default=list, blank=True, help_text='Employee quiz answers'
    )
    quiz_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Percentage score on the quiz'
    )
    quiz_passed = models.BooleanField(default=False)
    is_locked = models.BooleanField(
        default=False,
        help_text='Once acknowledged, record is locked and cannot be changed'
    )
    acknowledgment_text = models.TextField(
        blank=True, default='I acknowledge that I have read and fully understand '
        'all terms and conditions of this policy/notice.'
    )

    class Meta:
        unique_together = ('policy', 'employee')

    def __str__(self):
        return f'{self.employee} acknowledged {self.policy}'


class CompanyNotice(TimeStampedModel):
    class Category(models.TextChoices):
        GENERAL = 'general', 'General'
        URGENT = 'urgent', 'Urgent'
        EVENT = 'event', 'Event'
        UPDATE = 'update', 'Policy Update'

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='notices')
    title = models.CharField(max_length=200)
    message = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.GENERAL)
    notice_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to='notices/', null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title


class NoticeRead(TimeStampedModel):
    notice = models.ForeignKey(CompanyNotice, on_delete=models.CASCADE, related_name='reads')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='notice_reads')
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('notice', 'employee')

    def __str__(self):
        return f'{self.employee} read {self.notice}'


# ---------------------------------------------------------------------------
# 8. ASSET MANAGEMENT (bonus)
# ---------------------------------------------------------------------------

# ===========================================================================
# 8. ASSET MANAGEMENT & CUSTODY TRACKING ENGINE
# ===========================================================================

class AssetCategory(TimeStampedModel):
    # Enforce database-level uniqueness for category names (prevents duplicate categories)
    name = models.CharField(max_length=100, unique=True)
    # Stores comma-separated list of dynamic fields needed for this specific hardware type
    # e.g., "serial_number,device_password,model_number" for Laptops; "sim,phone_number" for SIM Cards
    required_fields = models.TextField(
        blank=True,
        help_text="Comma-separated field identifiers to show dynamically on the creation form"
    )

    class Meta:
        verbose_name_plural = "Asset Categories"
        ordering = ['name'] # Keep categories alphabetically sorted in all dropdowns

    def __str__(self):
        return self.name

    def clean(self):
        # Case-insensitive validation: prevents creating 'laptop' if 'Laptop' already exists
        self.name = self.name.strip()
        if AssetCategory.objects.filter(name__iexact=self.name).exclude(pk=self.pk).exists():
            from django.core.exceptions import ValidationError
            raise ValidationError({'name': f"An asset category named '{self.name}' already exists."})


class Asset(TimeStampedModel):
    class Status(models.TextChoices):
        AVAILABLE = 'available', 'Available in Warehouse' # Asset is unassigned and ready to issue
        ASSIGNED = 'assigned', 'Issued / In Active Use'   # Asset is currently with an employee
        UNDER_REPAIR = 'under_repair', 'Under Repair'     # Asset is sent for servicing/maintenance
        RETIRED = 'retired', 'Retired / Disposed'         # Asset is written off or scrapped

    # Scope asset ownership to company for multi-tenant isolation
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='assets')
    # Link to category; PROTECT prevents deleting a category that still contains active assets
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name='assets',null=True, blank=True)
    # Current holder: Nullable because asset can be unassigned in warehouse
    employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assets',
        help_text="Currently assigned employee. Leave empty if stored in office/warehouse."
    )

    # Core identification fields
    name = models.CharField(max_length=150, help_text="e.g., MacBook Pro M3, Dell Latitude 5420")
    model_number = models.CharField(max_length=100, blank=True, help_text="Manufacturer Model Number")
    serial_number = models.CharField(max_length=100, blank=True, help_text="Unique hardware serial number / Service tag")

    # Dynamic / Category-specific hardware specs
    sim = models.CharField(max_length=100, null=True, blank=True, help_text="SIM Provider / ICCID Number")
    phone_number = models.CharField(max_length=20, null=True, blank=True, help_text="Associated mobile phone number")
    device_password = models.CharField(max_length=255, blank=True, help_text="PIN / Master login password")
    additional_details = models.TextField(blank=True, help_text="RAM, SSD Capacity, Processor, Accessories included")

    # Procurement & Vendor lifecycle tracking
    vendor_name = models.CharField(max_length=200, blank=True, help_text="Supplier / Dealer company name")
    vendor_contact = models.CharField(max_length=100, blank=True, help_text="Supplier phone, email, or representative name")
    purchase_date = models.DateField(null=True, blank=True, help_text="Date of procurement")
    warranty_expiry = models.DateField(null=True, blank=True, help_text="Warranty end date for claim tracking")
    invoice_document = models.FileField(
        upload_to='assets/invoices/',
        null=True,
        blank=True,
        help_text="Upload scanned tax invoice or procurement bill (PDF/JPG)"
    )

    # Status & Handover details
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    assigned_on = models.DateField(null=True, blank=True, help_text="Date when current holder received the asset")
    remarks = models.TextField(blank=True, help_text="Internal notes on physical condition, scratches, or upgrades")

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.serial_number or 'No SN'})"

    def is_under_warranty(self):
        """Helper to quickly check warranty status on UI cards."""
        if self.warranty_expiry:
            return self.warranty_expiry >= date.today()
        return False


class AssetAssignmentHistory(TimeStampedModel):
    """
    Tracks complete lifecycle custody: who held the asset, from when,
    until when, return condition, and exact duration handled.
    """
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='history')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='asset_history')
    # Handover date
    assigned_date = models.DateField(default=timezone.localdate, help_text="Date handover occurred")
    # Return date (null while employee is actively using the asset)
    returned_date = models.DateField(null=True, blank=True, help_text="Date returned to company custody")
    # Active flag to easily find the open custody session
    is_still_using = models.BooleanField(default=True, help_text="True if asset is currently with this employee")
    # Return verification audit
    returned_in_good_condition = models.BooleanField(
        null=True,
        blank=True,
        help_text="True if returned working with no physical damage; False if damaged/defective"
    )
    return_remarks = models.TextField(blank=True, help_text="Mandatory audit notes if returned damaged or parts missing")
    assignment_notes = models.TextField(blank=True, help_text="Condition remarks at time of issuance")

    class Meta:
        ordering = ['-assigned_date']

    def __str__(self):
        return f"{self.asset.name} -> {self.employee.full_name} ({self.assigned_date})"

    @property
    def duration_display(self):
        """Calculates and returns human-readable tenure the employee held the asset."""
        end = self.returned_date or timezone.localdate()
        start = self.assigned_date
        if not start:
            return "--"
        days_count = (end - start).days
        if days_count < 30:
            return f"{days_count} day(s)"
        months = days_count // 30
        remaining_days = days_count % 30
        return f"{months} mo, {remaining_days} days"


# ---------------------------------------------------------------------------
# 9. PERFORMANCE (bonus)
# ---------------------------------------------------------------------------
class PerformanceReview(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SUBMITTED = 'submitted', 'Submitted'
        ACKNOWLEDGED = 'acknowledged', 'Acknowledged'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='performance_reviews')
    review_period_from = models.DateField()
    review_period_to = models.DateField()
    review_date = models.DateField()
    reviewer = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='reviews_given')
    rating = models.PositiveSmallIntegerField()  # 1-5
    comments = models.TextField(blank=True)
    goals = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    def __str__(self):
        return f'{self.employee} review ({self.review_period_from} - {self.review_period_to})'


# ---------------------------------------------------------------------------
# 10. MONTHLY LEAVE ACCRUAL (Module B)
# ---------------------------------------------------------------------------
class MonthlyLeaveAccrual(TimeStampedModel):
    """Tracks monthly leave credits for CL/EL accrual-based allocation."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='monthly_accruals')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='monthly_accruals')
    month = models.PositiveSmallIntegerField()  # 1-12
    year = models.PositiveSmallIntegerField()
    accrued_amount = models.DecimalField(max_digits=5, decimal_places=2, help_text='Amount credited this month (annual/12)')
    is_credited = models.BooleanField(default=False)
    credited_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('employee', 'leave_type', 'month', 'year')
        ordering = ['-year', '-month']

    def __str__(self):
        return f'{self.employee} - {self.leave_type.code} ({self.month}/{self.year})'


# ---------------------------------------------------------------------------
# 11. LEAVE APPROVAL AUDIT LOG (Module C)
# ---------------------------------------------------------------------------
class LeaveApprovalLog(TimeStampedModel):
    """Complete audit trail for every leave application action."""
    class Action(models.TextChoices):
        APPLIED = 'applied', 'Applied'
        MANAGER_APPROVED = 'manager_approved', 'Manager Approved'
        MANAGER_REJECTED = 'manager_rejected', 'Manager Rejected'
        HR_APPROVED = 'hr_approved', 'HR Approved'
        HR_REJECTED = 'hr_rejected', 'HR Rejected'
        CANCELLED = 'cancelled', 'Cancelled'
        REAPPLIED = 'reapplied', 'Re-Applied'

    application = models.ForeignKey(LeaveApplication, on_delete=models.CASCADE, related_name='approval_logs')
    action = models.CharField(max_length=30, choices=Action.choices)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leave_audit_actions'
    )
    remarks = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f'{self.application} — {self.get_action_display()} at {self.timestamp}'


# ---------------------------------------------------------------------------
# 12. PUNCH REGULARIZATION (Module D)
# ---------------------------------------------------------------------------
class PunchRegularizationRequest(TimeStampedModel):
    """Employee requests to regularize a missed or incorrect punch."""
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='regularization_requests')
    attendance_date = models.DateField()
    requested_check_in = models.DateTimeField(null=True, blank=True)
    requested_check_out = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    proof_document = models.FileField(upload_to='regularization/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='regularization_reviews'
    )
    rejection_reason = models.CharField(max_length=255, blank=True)
    reviewed_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('employee', 'attendance_date')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.employee} regularization for {self.attendance_date}'


# ---------------------------------------------------------------------------
# 13. COMP OFF TRACKING (Module F)
# ---------------------------------------------------------------------------
class CompOffRecord(TimeStampedModel):
    """
    Tracks every Comp Off credit earned when an employee punches in on a
    Sunday or a Company Holiday. One row per (employee, worked_date).

    Lifecycle:  AVAILABLE → AVAILED  (when CO leave is approved)
                AVAILABLE → EXPIRED  (if HR runs an expiry sweep)
    """

    class Status(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        PARTIAL   = 'partial',   'Partially Availed'
        AVAILED   = 'availed',   'Availed'
        EXPIRED   = 'expired',   'Expired'

    employee = models.ForeignKey(
        'Employee',
        on_delete=models.CASCADE,
        related_name='comp_off_records',
    )
    attendance_record = models.ForeignKey(
        'AttendanceRecord',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='comp_off_source',
        help_text='The attendance record that triggered this credit',
    )
    worked_date = models.DateField(
        help_text='The Sunday or Holiday on which the employee worked',
    )
    holiday_name = models.CharField(
        max_length=150,
        blank=True,
        default='Sunday / Weekly Off',
        help_text='Name of the holiday or "Sunday / Weekly Off"',
    )
    hours_worked = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text='Total hours logged on the off-day (updated at checkout)',
    )
    credits_earned = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('1.0'),
        help_text='1.0 for a full day, 0.5 for a half day worked',
    )
    credits_used = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        default=Decimal('0.0'),
        help_text='Total credits redeemed/consumed against this record',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    is_encashed = models.BooleanField(
        default=False,
        help_text='True if remaining credit was paid out in monthly payroll',
    )

    @property
    def credits_remaining(self):
        used = self.credits_used or Decimal('0.0')
        return max(Decimal('0.0'), self.credits_earned - Decimal(str(used)))
    availed_on_date = models.DateField(
        null=True,
        blank=True,
        help_text='The date the employee took this comp off as leave',
    )
    availed_leave_app = models.ForeignKey(
        'LeaveApplication',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='availed_comp_off',
        help_text='The approved LeaveApplication that consumed this credit',
    )

    class Meta:
        ordering = ['-worked_date']
        unique_together = ('employee', 'worked_date')
        verbose_name = 'Comp Off Record'
        verbose_name_plural = 'Comp Off Records'

    def __str__(self):
        return (
            f"{self.employee.full_name} — Comp Off for "
            f"{self.worked_date} ({self.get_status_display()})"
        )


# ---------------------------------------------------------------------------
# 14. PAYSLIP DOWNLOAD AUDIT (Module E)
# ---------------------------------------------------------------------------
class PayslipDownloadLog(TimeStampedModel):
    """Silent audit log for payslip downloads — notifies HR/Superadmin."""
    payslip = models.ForeignKey(PaySlip, on_delete=models.CASCADE, related_name='download_logs')
    downloaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='payslip_downloads'
    )
    downloaded_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.CharField(max_length=45, blank=True)

    class Meta:
        ordering = ['-downloaded_at']

    def __str__(self):
        return f'{self.downloaded_by} downloaded {self.payslip} at {self.downloaded_at}'