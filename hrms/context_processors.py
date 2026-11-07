from .permissions import get_role, is_hr_or_above, ROLE_SUPERADMIN, ROLE_HR, ROLE_EMPLOYEE


def hrms_role(request):
    """Adds `hrms_role` and `hrms_is_hr` to every template's context."""
    user = getattr(request, 'user', None)
    role = get_role(user) if user else None
    return {
        'hrms_role': role,
        'hrms_is_hr': is_hr_or_above(user) if user else False,
        'ROLE_SUPERADMIN': ROLE_SUPERADMIN,
        'ROLE_HR': ROLE_HR,
        'ROLE_EMPLOYEE': ROLE_EMPLOYEE,
    }

#if superuser, then can see any companies data and Hr will see their own company
# context_processors.py
from .models import Company
from .permissions import get_employee_profile, is_hr_or_above


def hrms_company_context(request):
    """
    Makes company multi-tenancy information globally available in templates.
    - Super Admin: Can view and switch between all companies.
    - HR / Manager: Automatically locked to the first company in managed_companies (or assigned company).
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}

    # Super Admin can switch companies
    if user.is_superuser:
        available_companies = Company.objects.all()
        active_company_id = request.session.get('active_company_id')
        active_company = None
        if active_company_id and active_company_id != 'all':
            active_company = Company.objects.filter(id=active_company_id).first()

        return {
            'available_companies': available_companies,
            'active_company': active_company,  # None means "All Global Units"
            'active_company_id': active_company_id or 'all',
            'can_switch_company': True,
        }

    # HR Role or other staff
    profile = get_employee_profile(user)
    if profile:
        # Check first managed company, fallback to primary assigned company
        first_managed = profile.managed_companies.first() if hasattr(profile, 'managed_companies') else None
        locked_company = first_managed or profile.company

        if locked_company:
            # Automatically lock session to this company for consistent backend query filtering
            request.session['active_company_id'] = str(locked_company.id)
            return {
                'available_companies': [locked_company],
                'active_company': locked_company,
                'active_company_id': str(locked_company.id),
                'can_switch_company': False,
            }

    return {
        'available_companies': Company.objects.none(),
        'active_company': None,
        'active_company_id': None,
        'can_switch_company': False,
    }