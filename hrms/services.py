"""
HRMS Service Layer — encapsulates business processes such as attendance penalties,
leave deductions, automated payroll adjustments, and applicant resume parsing.
"""
import io
import os
import re
import uuid
from datetime import timedelta
from decimal import Decimal
import logging

from django.core.files.base import ContentFile
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    from PyPDF2 import PdfReader

from . import models as m

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resume / PDF helpers
# ---------------------------------------------------------------------------

def _normalize_text(value):
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", "\n").split())


def _extract_pdf_text(file_path_or_buffer):
    """Read raw text from a PDF file path or file-like object."""
    if isinstance(file_path_or_buffer, (str, os.PathLike)):
        with open(file_path_or_buffer, "rb") as file_handle:
            return _extract_pdf_text(file_handle)

    file_obj = file_path_or_buffer
    if hasattr(file_obj, "read"):
        data = file_obj.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        file_obj = io.BytesIO(data)
    else:
        raise ValueError("Unsupported resume input. Expected a file path or file-like object.")

    try:
        reader = PdfReader(file_obj)
    except Exception as exc:  # pragma: no cover
        raise ValueError(f"Unable to read PDF content: {exc}") from exc

    pages = []
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text:
            pages.append(page_text)

    extracted = "\n".join(pages).strip()
    if not extracted:
        raise ValueError("The PDF file did not contain readable text.")
    return extracted


def _extract_email(text):
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text, flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def _extract_phone_number(text):
    phone_candidates = []
    for match in re.finditer(
            r"(?:(?:\+?\d{1,3})[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}",
            text,
    ):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if 10 <= len(digits) <= 12:
            phone_candidates.append(raw.strip())

    if not phone_candidates:
        return ""
    for candidate in phone_candidates:
        if "+" in candidate:
            return candidate
    return phone_candidates[0]


def _extract_name(text):
    patterns = [
        r"(?:^|[\n\r])\s*(?:name\s*[:\-]?)\s*([A-Z][A-Za-z'.\- ]{2,60})",
        r"(?:^|[\n\r])\s*([A-Z][A-Za-z'.\-]{2,}\s+[A-Z][A-Za-z'.\-]{2,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if len(candidate.split()) >= 2:
                return candidate

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines[:20]:
        if re.search(r"resume|curriculum|profile|summary|experience|education|contact", line, re.IGNORECASE):
            continue
        cleaned = re.sub(r"[^A-Za-z\s'.-]", "", line).strip()
        words = cleaned.split()
        if 2 <= len(words) <= 4 and all(word and word[0].isupper() for word in words if word.isalpha()):
            return cleaned
    return "Candidate"


def _extract_experience_years(text):
    candidates = []
    for match in re.finditer(r"(?:(\d+)\s*(?:\+)?\s*(?:years?|yrs?|yr))", text, flags=re.IGNORECASE):
        value = match.group(1)
        if value:
            candidates.append(float(value))
    for match in re.finditer(r"(?:(\d+)\s*(?:\+)?\s*(?:months?|mos?))", text, flags=re.IGNORECASE):
        value = match.group(1)
        if value:
            candidates.append(float(value) / 12.0)
    if not candidates:
        return 0.0
    return round(max(candidates), 2)


# Indian states and union territories (title case for matching)
_INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "New Delhi", "Chandigarh", "Jammu and Kashmir", "Jammu & Kashmir",
    "Ladakh", "Puducherry", "Lakshadweep", "Andaman and Nicobar",
    "Dadra and Nagar Haveli", "Daman and Diu",
]

# Major Indian cities (covers metros + tier-2 cities commonly found in resumes)
_INDIAN_CITIES = [
    "Mumbai", "Delhi", "Bangalore", "Bengaluru", "Hyderabad", "Ahmedabad",
    "Chennai", "Kolkata", "Pune", "Jaipur", "Lucknow", "Kanpur", "Nagpur",
    "Indore", "Thane", "Bhopal", "Visakhapatnam", "Patna", "Vadodara",
    "Ghaziabad", "Ludhiana", "Agra", "Nashik", "Faridabad", "Meerut",
    "Rajkot", "Varanasi", "Srinagar", "Aurangabad", "Dhanbad", "Amritsar",
    "Allahabad", "Prayagraj", "Ranchi", "Howrah", "Coimbatore", "Jabalpur",
    "Gwalior", "Vijayawada", "Jodhpur", "Madurai", "Raipur", "Kochi",
    "Chandigarh", "Mysore", "Mysuru", "Gurgaon", "Gurugram", "Noida",
    "Greater Noida", "Trivandrum", "Thiruvananthapuram", "Mangalore",
    "Mangaluru", "Dehradun", "Hubli", "Shimla", "Jammu", "Udaipur",
    "Jamshedpur", "Bhubaneswar", "Cuttack", "Kota", "Ajmer", "Bareilly",
    "Moradabad", "Gorakhpur", "Aligarh", "Jalandhar", "Tiruchirappalli",
    "Salem", "Warangal", "Guntur", "Bhilai", "Bikaner", "Amravati",
    "Bokaro", "Navi Mumbai", "Panipat", "Rohtak", "Sonipat", "Karnal",
    "Hisar", "Ambala", "Bathinda", "Patiala", "Mohali", "Zirakpur",
    "Pondicherry", "Gangtok", "Imphal", "Shillong", "Aizawl", "Kohima",
    "Itanagar", "Agartala", "Panaji", "Daman", "Silvassa", "Kavaratti",
    "Port Blair", "Surat", "Nellore",
]


def _extract_city_state(text):
    """
    Extract city and state from resume text using curated Indian location lists.

    Strategy:
    1. First check for explicit "City:" or "Location:" labels in the resume.
    2. Then scan for known Indian state names (longer names first to avoid partial matches).
    3. Then scan for known Indian city names.

    Returns (city: str, state: str).
    """
    city = ""
    state = ""

    # Normalize text for searching (keep original case info via case-insensitive matching)
    search_text = _normalize_text(text)

    # ── Strategy 1: Look for explicit location labels ──
    # Patterns like "Location: Mumbai, Maharashtra" or "City: Delhi"
    location_patterns = [
        r"(?:location|city|address|place|residing|current\s+location|based\s+(?:in|at))\s*[:–—-]\s*([^\n,;]{2,60})",
        r"(?:city|town)\s*[:–—-]\s*([^\n,;]{2,40})",
        r"(?:state|province)\s*[:–—-]\s*([^\n,;]{2,40})",
    ]
    for pattern in location_patterns:
        match = re.search(pattern, search_text, flags=re.IGNORECASE)
        if match:
            location_text = match.group(1).strip()
            # Try to parse "City, State" or just a single value
            parts = [p.strip() for p in re.split(r"[,|/]", location_text) if p.strip()]
            for part in parts:
                if not state:
                    for s in _INDIAN_STATES:
                        if re.search(r'\b' + re.escape(s) + r'\b', part, re.IGNORECASE):
                            state = s
                            break
                if not city:
                    for c in _INDIAN_CITIES:
                        if re.search(r'\b' + re.escape(c) + r'\b', part, re.IGNORECASE):
                            city = c
                            break

    # ── Strategy 2: Scan full text for state names (longer names first) ──
    if not state:
        # Sort by length descending so "Madhya Pradesh" matches before "Pradesh"
        for s in sorted(_INDIAN_STATES, key=len, reverse=True):
            if re.search(r'\b' + re.escape(s) + r'\b', search_text, re.IGNORECASE):
                state = s
                break

    # ── Strategy 3: Scan full text for city names ──
    if not city:
        for c in sorted(_INDIAN_CITIES, key=len, reverse=True):
            if re.search(r'\b' + re.escape(c) + r'\b', search_text, re.IGNORECASE):
                city = c
                break

    return city, state


def parse_resume_pdf(file_path_or_buffer):
    """Extract structured metadata from a PDF resume."""
    raw_text = _extract_pdf_text(file_path_or_buffer)
    city, state = _extract_city_state(raw_text)
    return {
        "name": _normalize_text(_extract_name(raw_text) or "Candidate"),
        "email": _extract_email(raw_text) or "",
        "phone": _extract_phone_number(raw_text) or "",
        "experience_years": _extract_experience_years(raw_text),
        "city": city,
        "state": state,
        "raw_text": raw_text,
    }


def _split_name(full_name):
    normalized = " ".join(str(full_name).split())
    parts = normalized.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def create_candidate_from_resume(file_obj, job_posting_id):
    """
    Parse a resume PDF, deduplicate by email, and create/link a Candidate + Application.

    Deduplication rules:
      - If a Candidate with the parsed email exists → reuse it (no new Candidate).
      - If that Candidate already has an Application for this job → skip creation.
      - Otherwise → create a fresh Application pointing to the first pipeline stage.

    Returns a dict::
        {
          "status": "created" | "duplicate_linked" | "duplicate_skipped",
          "candidate_id": int,
          "candidate_name": str,
          "email": str,
          "phone": str,
          "experience_years": float,
          "application_id": int | None,
          "filename": str,
        }
    """
    job_posting = get_object_or_404(m.JobPosting, pk=job_posting_id)
    if file_obj is None:
        raise ValueError("No resume file was provided.")

    filename = getattr(file_obj, "name", "resume.pdf")

    if hasattr(file_obj, "read"):
        original_position = None
        if hasattr(file_obj, "tell"):
            try:
                original_position = file_obj.tell()
            except (AttributeError, OSError):
                original_position = None
        file_bytes = file_obj.read()
        if original_position is not None and hasattr(file_obj, "seek"):
            file_obj.seek(original_position)
        if isinstance(file_bytes, str):
            file_bytes = file_bytes.encode("utf-8")
    else:
        raise ValueError("Expected a file-like object with a .read() method.")

    if not file_bytes:
        raise ValueError("The uploaded resume is empty.")

    parsed = parse_resume_pdf(io.BytesIO(file_bytes))
    name = parsed.get("name") or "Candidate"
    email = parsed.get("email") or ""
    phone = parsed.get("phone") or ""
    experience_years = float(parsed.get("experience_years", 0) or 0)
    city = parsed.get("city") or ""
    state = parsed.get("state") or ""

    if not email:
        email = f"{slugify(name) or 'candidate'}-{uuid.uuid4().hex[:8]}@upload.invalid"

    first_name, last_name = _split_name(name)
    default_pipeline = job_posting.pipeline_stages.select_related("stage").order_by("order").first()
    first_stage = default_pipeline.stage if default_pipeline else None

    with transaction.atomic():
        # ── Deduplication by email ──────────────────────────────────────────
        existing_candidate = m.Candidate.objects.filter(email__iexact=email).first()

        if existing_candidate:
            candidate = existing_candidate
            # Check if already applied to this specific job
            existing_app = m.Application.objects.filter(
                candidate=candidate,
                job_posting=job_posting,
            ).first()

            if existing_app:
                # Fully duplicate — skip
                return {
                    "status": "duplicate_skipped",
                    "candidate_id": candidate.pk,
                    "candidate_name": str(candidate),
                    "email": candidate.email,
                    "phone": candidate.phone,
                    "experience_years": float(candidate.experience_years),
                    "application_id": existing_app.pk,
                    "filename": filename,
                }

            # Candidate exists, but hasn't applied to this job → link new Application
            application = m.Application.objects.create(
                candidate=candidate,
                job_posting=job_posting,
                status=m.Application.Status.APPLIED,
                current_stage=first_stage,
                progress_percentage=0,
                source=m.Application.Source.CAREER_SITE,
            )
            m.RecruitmentAuditLog.objects.create(
                application=application,
                from_status="New",
                to_status=m.Application.Status.APPLIED,
                action="Bulk uploaded — linked to existing candidate profile",
                note=f"Resume file: {filename}",
            )
            return {
                "status": "duplicate_linked",
                "candidate_id": candidate.pk,
                "candidate_name": str(candidate),
                "email": candidate.email,
                "phone": candidate.phone,
                "experience_years": float(candidate.experience_years),
                "application_id": application.pk,
                "filename": filename,
            }

        # ── Brand new candidate ─────────────────────────────────────────────
        candidate = m.Candidate.objects.create(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            experience_years=Decimal(str(experience_years)),
            city=city,
            state=state,
        )

        safe_name = f"{slugify(name) or 'candidate'}-{uuid.uuid4().hex[:8]}.pdf"
        candidate.resume.save(safe_name, ContentFile(file_bytes), save=False)
        candidate.save(update_fields=["resume", "updated_at"])

        application = m.Application.objects.create(
            candidate=candidate,
            job_posting=job_posting,
            status=m.Application.Status.APPLIED,
            current_stage=first_stage,
            progress_percentage=0,
            source=m.Application.Source.CAREER_SITE,
        )
        m.RecruitmentAuditLog.objects.create(
            application=application,
            from_status="New",
            to_status=m.Application.Status.APPLIED,
            action="Bulk uploaded and parsed from PDF resume",
            note=f"Resume file: {filename}",
        )

    return {
        "status": "created",
        "candidate_id": candidate.pk,
        "candidate_name": str(candidate),
        "email": candidate.email,
        "phone": candidate.phone,
        "experience_years": experience_years,
        "city": city,
        "state": state,
        "application_id": application.pk,
        "filename": filename,
    }


# ---------------------------------------------------------------------------
# ICS Calendar File Generator
# ---------------------------------------------------------------------------

def generate_ics_file(interview):
    """
    Generate an RFC 5545 compliant .ics calendar file for an Interview.

    Returns bytes suitable for attaching to an email or serving as a download.
    """
    application = interview.application
    candidate = application.candidate
    company = application.job_posting.company

    dtstart = interview.scheduled_on
    dtend = dtstart + timedelta(hours=1)

    def fmt_dt(dt):
        """Format datetime as iCal UTC timestamp."""
        import pytz
        if dt.tzinfo is not None:
            dt = dt.astimezone(pytz.utc)
        return dt.strftime("%Y%m%dT%H%M%SZ")

    uid = f"interview-{interview.pk}-{uuid.uuid4().hex}@oblu-hrms"
    now_str = fmt_dt(timezone.now())
    start_str = fmt_dt(dtstart)
    end_str = fmt_dt(dtend)

    round_label = interview.interview_round or "Interview"
    summary = f"{round_label} — {candidate} @ {company.name}"
    description = (
        f"Candidate: {candidate}\\n"
        f"Role: {application.job_posting.title}\\n"
        f"Mode: {interview.get_mode_display()}\\n"
        f"Contact: {candidate.email}"
    )

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Oblu HRMS//Interview Scheduler//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now_str}",
        f"DTSTART:{start_str}",
        f"DTEND:{end_str}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"ORGANIZER;CN={company.name}:mailto:{company.email or 'noreply@oblu.com'}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    # Add ATTENDEEs
    if candidate.email:
        ics_lines.insert(-2, f"ATTENDEE;ROLE=REQ-PARTICIPANT;CN={candidate}:mailto:{candidate.email}")

    for interviewer in interview.interviewer.all():
        if interviewer.email:
            ics_lines.insert(-2, f"ATTENDEE;ROLE=REQ-PARTICIPANT;CN={interviewer}:mailto:{interviewer.email}")

    return "\r\n".join(ics_lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Attendance Penalty
# ---------------------------------------------------------------------------

def process_late_arrival_penalty(record, reason=None):
    """
    Automated Late Arrival Penalty and Leave Deduction system.

    Triggered when an employee exceeds their monthly grace limit or arrives
    beyond the grace window and is marked as 'Half Day' (HD).

    Business Rules:
    1. Duplicate Prevention: Checks if a penalty already exists for (employee, penalty_date).
    2. Fallback Deduction Hierarchy (for Full-Time):
       - First Priority: Deduct 0.5 from CL (Casual Leave) if CL >= 0.5.
       - Second Priority: Deduct 0.5 from EL (Earned Leave) if EL >= 0.5.
       - Third Priority: If both CL and EL < 0.5, mark Deduction Source as 'LWP / Salary' (0.5 day wage deduction).
    3. Intern Handling:
       - Direct half-day stipend/salary calculation without deducting from standard paid leaves.
    4. Records entry in AttendancePenalty with status, source, and audit timestamps.
    """
    if not record or not record.employee:
        return None

    employee = record.employee
    penalty_date = record.attendance_date or timezone.localdate()

    # 1. Prevent duplicate penalty logs for the same employee and date
    existing_penalty = m.AttendancePenalty.objects.filter(
        employee=employee,
        penalty_date=penalty_date
    ).first()
    if existing_penalty:
        return existing_penalty

    late_mins = int(record.late_minutes or 0)
    deduction_amount = Decimal('0.5')
    employment_type = getattr(employee, 'employment_type', 'full_time') or 'full_time'

    penalty_reason = reason or f"Grace limit exceeded - Late Arrival ({late_mins} mins late)"

    with transaction.atomic():
        # Case A: INTERN EMPLOYEES
        if employment_type == 'intern':
            salary_deduction = Decimal('0.00')
            try:
                emp_salary = m.EmployeeSalary.objects.filter(
                    employee=employee, is_active=True
                ).first()
                if emp_salary and emp_salary.ctc_annual:
                    daily_wage = (emp_salary.ctc_annual / Decimal('12')) / Decimal('30')
                    salary_deduction = (daily_wage / Decimal('2')).quantize(Decimal('0.01'))
            except Exception as e:
                logger.warning(f"Failed calculating intern salary deduction for {employee}: {e}")

            penalty = m.AttendancePenalty.objects.create(
                employee=employee,
                attendance_record=record,
                penalty_date=penalty_date,
                reason=penalty_reason,
                late_minutes=late_mins,
                deduction_days=deduction_amount,
                deduction_source="LWP / Salary",
                status=m.AttendancePenalty.DeductionStatus.INTERN_SALARY,
                is_intern_penalty=True,
                salary_deduction_amount=salary_deduction,
                employment_type_snapshot=employment_type,
            )
            return penalty

        # Case B: FULL-TIME EMPLOYEES (CL -> EL -> LWP Hierarchy)
        live_balance, _ = m.EmployeeLeaveBalanceLive.objects.select_for_update().get_or_create(e_name=employee)
        bank_balance = m.EmployeeLeaveBalance.objects.select_for_update().filter(e_name=employee).first()

        cl_available = Decimal(str(live_balance.casual_leave or 0.0))
        el_available = Decimal(str(live_balance.earned_leave or 0.0))

        if cl_available >= deduction_amount:
            # First priority: Deduct 0.5 from CL
            new_cl = float(cl_available - deduction_amount)
            live_balance.casual_leave = new_cl
            live_balance.save(update_fields=['casual_leave'])

            if bank_balance:
                bank_balance.casual_leave = max(0.0, float(Decimal(str(bank_balance.casual_leave or 0.0)) - deduction_amount))
                bank_balance.save(update_fields=['casual_leave'])

            deduction_source = "CL"
            penalty_status = m.AttendancePenalty.DeductionStatus.APPLIED

        elif el_available >= deduction_amount:
            # Second priority: Deduct 0.5 from EL
            new_el = float(el_available - deduction_amount)
            live_balance.earned_leave = new_el
            live_balance.save(update_fields=['earned_leave'])

            if bank_balance:
                bank_balance.earned_leave = max(0.0, float(
                    Decimal(str(bank_balance.earned_leave or 0.0)) - deduction_amount))
                bank_balance.save(update_fields=['earned_leave'])

            deduction_source = "EL"
            penalty_status = m.AttendancePenalty.DeductionStatus.APPLIED

        else:
            # Third priority: Both exhausted -> Mark as LWP / Salary deduction
            deduction_source = "LWP / Salary"
            penalty_status = m.AttendancePenalty.DeductionStatus.LWP

        penalty = m.AttendancePenalty.objects.create(
            employee=employee,
            attendance_record=record,
            penalty_date=penalty_date,
            reason=penalty_reason,
            late_minutes=late_mins,
            deduction_days=deduction_amount,
            deduction_source=deduction_source,
            status=penalty_status,
            is_intern_penalty=False,
            employment_type_snapshot=employment_type,
        )
        return penalty
