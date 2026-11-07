from .models import Company


def get_active_company(request):
    """
    Returns the Company object the user is currently viewing.
    """
    company_id = request.session.get('active_company_id')

    if company_id:
        return Company.objects.filter(id=company_id).first()

    # Default: If no session, return the employee's primary company
    if hasattr(request.user, 'employee_profile'):
        return request.user.employee_profile.company

    return None


def set_active_company(request, company_id):
    """
    Updates the session to a specific company, or 'all' for superadmins.
    """
    if company_id == 'all' and request.user.is_superuser:
        request.session['active_company_id'] = None
    else:
        request.session['active_company_id'] = company_id