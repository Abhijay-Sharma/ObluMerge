from django.shortcuts import render

# Create your views here.
from datetime import date, timedelta, datetime
from decimal import Decimal
from collections import defaultdict

from django.views.generic import TemplateView
from django.db.models import Prefetch
from calendar import monthrange

from customer_dashboard.models import SalesPerson, Customer, CustomerVoucherStatus
from tally_voucher.models import Voucher, VoucherStockItem
from incentive_calculator.models import ProductIncentive, ProductIncentiveTier, CustomerIncentiveTrigger
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from access_control.mixins import RBACViewMixin
from access_control.services import can, get_user_effective_permissions, scope_queryset
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from incentive_calculator.models import ProductIncentive, ProductIncentiveTier, IncentivePaymentStatus
from merger.settings import LOGIN_REDIRECT_URL
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from decimal import Decimal, ROUND_HALF_UP
from incentive_calculator.models import ProductIncentive, ProductIncentiveTier, IncentivePaymentStatus, CustomerIncentiveTrigger
from proforma_invoice.models import ProductPrice
from django.db.models import Q


# Create your views here.

def get_effective_product_tax_and_msp(item_name, voucher_date, base_tax_rate, base_msp):
    """
    Centralized resolver for effective tax rate and MSP based on transaction date.

    Business Rules:
    - Molekur 0.76 (e.g., 'Molekur pro s 0.76mm'):
        * Prior to Oct 01, 2026: 18% GST, MSP = 120.00
        * From Oct 01, 2026 onwards: 5% GST, MSP = 119.00
    - Other products:
        * Tax rate defaults to base_tax_rate from ProductPrice, falling back to 18% if missing or 0.
        * MSP remains base_msp from ProductIncentive.
    """
    resolved_tax = base_tax_rate if (base_tax_rate and base_tax_rate > 0) else Decimal("18.00")
    if not item_name:
        return resolved_tax, base_msp

    name_clean = item_name.strip().lower()

    if "molekur" in name_clean and "0.76" in name_clean:
        v_date = voucher_date
        if isinstance(v_date, datetime):
            v_date = v_date.date()

        if v_date and v_date >= date(2026, 10, 1):
            return Decimal("5.00"), Decimal("119.00")
        else:
            return Decimal("18.00"), Decimal("120.00")

    return resolved_tax, base_msp


EXCLUDED_SALESPERSON_NAMES = [
    'abhijay', 'aryan', 'charan', 'danish', 'jackson', 'mukesh',
    'nimit', 'online', 'raman', 'vibhuti', 'online order', 'test1'
]


def get_salesperson_exclusion_q():
    """Builds a case-insensitive Q object to filter out non-sales and inactive accounts."""
    q_filter = Q()
    for name in EXCLUDED_SALESPERSON_NAMES:
        q_filter |= Q(name__iexact=name)
    return q_filter


def get_sales_head_profile():
    """Returns the SalesPerson profile for the Sales Head dynamically via RBAC with fallback."""
    sp = SalesPerson.objects.filter(
        user__employee_profile__role_assignments__role__role_key='sales_head',
        user__employee_profile__role_assignments__is_active=True
    ).first()
    if not sp:
        sp = SalesPerson.objects.filter(name__iexact='Bhavya').first()
    return sp


def get_rsm_salespersons_qs():
    """Returns the queryset of Regional Sales Managers (RSMs) dynamically via RBAC or team hierarchy."""
    return SalesPerson.objects.exclude(get_salesperson_exclusion_q()).filter(
        Q(user__employee_profile__role_assignments__role__role_key__in=['rsm', 'sales_head'],
          user__employee_profile__role_assignments__is_active=True) |
        Q(team_members__isnull=False)
    ).distinct().order_by('name')


def get_eligible_salespersons_qs():
    """
    Returns SalesPersons eligible for incentive dashboards and payout management,
    excluding non-sales, test, online ledger, and inactive accounts requested by admin.
    """
    return SalesPerson.objects.exclude(get_salesperson_exclusion_q()).order_by('name')


def get_salesperson_role_type(sp):
    """Returns 'RSM' or 'ASM' based on RBAC role assignment or hierarchy."""
    if sp.user and hasattr(sp.user, 'employee_profile') and sp.user.employee_profile:
        user_roles = set(sp.user.employee_profile.role_assignments.filter(is_active=True).values_list('role__role_key', flat=True))
        if 'rsm' in user_roles or 'sales_head' in user_roles:
            return 'RSM'
        if 'asm' in user_roles or 'salesperson' in user_roles:
            return 'ASM'
    if sp.manager is not None:
        return 'ASM'
    if sp.team_members.exists():
        return 'RSM'
    return 'ASM'


class IncentiveCalculatorWelcomeView(RBACViewMixin, TemplateView):
    template_name = "incentive_calculator/welcome.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        user = request.user
        if not (user.is_superuser or can(user, "incentive.view_self") or can(user, "incentive.view_team") or can(user, "incentive.view_all")):
            raise PermissionDenied("You do not have permission to access the Incentive Calculator.")
        return super().dispatch(request, *args, **kwargs)


class IncentiveCalculatorView(RBACViewMixin, TemplateView):
    template_name = 'incentive_calculator/incentive_calculator.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        user = request.user
        if not (user.is_superuser or can(user, "incentive.view_self") or can(user, "incentive.view_team") or can(user, "incentive.view_all")):
            raise PermissionDenied("You do not have permission to access the Incentive Calculator.")
        return super().dispatch(request, *args, **kwargs)


#21-8-26 kashish
class ASMIncentivePaidUnpaidView(RBACViewMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_admin.html"
    rbac_permission = "incentive.manage_payout"

    @staticmethod
    def calculate_item_incentive(item_record, monthly_volume_total, product_incentive_rules, product_gst_lookup):
        """Calculates (incentive_amount, is_above_msp) for a single line item based on MSP and dynamic volume."""
        rule = product_incentive_rules.get(item_record.item.name.strip().lower())
        if not rule:
            return Decimal("0.00"), True

        unit_price_raw = (
            Decimal(str(item_record.amount)) / Decimal(str(item_record.quantity))
            if item_record.quantity > 0
            else Decimal("0.00")
        )
        base_tax = (
            Decimal(str(product_gst_lookup.get(item_record.item_id).tax_rate))
            if product_gst_lookup.get(item_record.item_id)
            else Decimal("0.00")
        )
        voucher_date = item_record.voucher.date if (hasattr(item_record, "voucher") and item_record.voucher) else None
        tax_rate, effective_msp = get_effective_product_tax_and_msp(
            item_record.item.name, voucher_date, base_tax, rule.msp
        )
        unit_price_with_tax = unit_price_raw * (Decimal("1") + (tax_rate / Decimal("100")))

        # Rounding logic for MSP comparison (₹1 buffer)
        comparison_price = unit_price_with_tax
        price_difference = effective_msp - unit_price_with_tax
        if Decimal("0") < price_difference <= Decimal("1.00"):
            comparison_price = effective_msp

        is_above_msp = (effective_msp == 0 or comparison_price >= effective_msp)
        if not is_above_msp:
            return Decimal("0.00"), False

        # Determine Rate
        asm_fixed_rate, _ = rule.get_effective_rates
        if rule.has_dynamic_price:
            applied_rate = (
                Decimal("4.00")
                if monthly_volume_total >= 4000
                else (Decimal("3.00") if monthly_volume_total >= 1000 else Decimal("0.00"))
            )
        else:
            applied_rate = asm_fixed_rate

        earning = (Decimal(str(item_record.quantity)) * rule.pack_size_multiplier) * applied_rate
        return earning, True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # --- 1. FILTERS & DATE SETUP ---
        selected_salesperson_id = self.request.GET.get("salesperson")
        month_picker_input = self.request.GET.get("month_picker")
        today_date = date.today()

        if month_picker_input:
            reporting_year, reporting_month = map(int, month_picker_input.split("-"))
        else:
            reporting_year, reporting_month = today_date.year, today_date.month

        allowed_salespersons = get_eligible_salespersons_qs()

        # Update base context for the template filters
        context.update({
            "year": reporting_year,
            "month": reporting_month,
            "salespersons": allowed_salespersons
        })

        if not selected_salesperson_id:
            return context

        reporting_month_start = date(reporting_year, reporting_month, 1)
        _, days_in_month = monthrange(reporting_year, reporting_month)
        reporting_month_end = date(reporting_year, reporting_month, days_in_month)

        target_salesperson = allowed_salespersons.filter(id=selected_salesperson_id).first()
        context["selected_salesperson"] = target_salesperson

        # --- 2. DATA LOOKUPS & RULES ---
        product_incentive_rules = {
            rule.product.name.strip().lower(): rule
            for rule in ProductIncentive.objects.select_related("product", "category").all()
        }
        product_gst_lookup = {
            price.product_id: price
            for price in ProductPrice.objects.only("product_id", "tax_rate")
        }
        blocked_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list('customer_id', flat=True)
        )

        # --- 3. CURRENT MONTH PROCESSING ---
        monthly_voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=target_salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[reporting_month_start, reporting_month_end]
        ).select_related("voucher", "customer")

        voucher_id_list = list(monthly_voucher_statuses.values_list("voucher_id", flat=True))
        voucher_status_lookup = {vs.voucher_id: vs for vs in monthly_voucher_statuses}
        monthly_line_items = VoucherStockItem.objects.filter(
            voucher_id__in=voucher_id_list
        ).select_related("item", "voucher")

        # Pre-fetch payout status map to eliminate N+1 queries
        payout_records_map = {
            pr.voucher_status.voucher_id: pr
            for pr in IncentivePaymentStatus.objects.filter(voucher_status__voucher_id__in=voucher_id_list)
        }

        # Step 3a: Calculate Monthly Sheet Volume (Only items where Trigger is ON)
        current_month_dynamic_volume = Decimal("0.00")
        for item in monthly_line_items:
            rule = product_incentive_rules.get(item.item.name.strip().lower())
            voucher_status = voucher_status_lookup.get(item.voucher_id)
            if rule and rule.has_dynamic_price and voucher_status:
                if voucher_status.customer_id not in blocked_customer_ids:
                    current_month_dynamic_volume += (item.quantity * rule.pack_size_multiplier)

        # Step 3b: Build Dashboard Buckets
        sales_log_data = []
        product_breakdown_summary = {}
        category_volume_summary = {}
        grand_potential_total = Decimal("0.00")
        payout_ready_total = Decimal("0.00")

        for item in monthly_line_items:
            rule = product_incentive_rules.get(item.item.name.strip().lower())
            voucher_status = voucher_status_lookup.get(item.voucher_id)
            if not voucher_status:
                continue

            is_customer_paid = bool(voucher_status.is_fully_paid or voucher_status.unpaid_amount == 0)
            is_customer_trigger_on = voucher_status.customer_id not in blocked_customer_ids

            # Calculate item earning
            calculated_incentive, is_above_msp = self.calculate_item_incentive(
                item, current_month_dynamic_volume, product_incentive_rules, product_gst_lookup
            )

            payout_record = payout_records_map.get(item.voucher_id)

            if is_customer_trigger_on:
                grand_potential_total += calculated_incentive
                if is_customer_paid:
                    payout_ready_total += calculated_incentive

                    if rule:
                        if rule.category:
                            category_volume_summary[rule.category.name] = category_volume_summary.get(
                                rule.category.name, 0) + (item.quantity * rule.pack_size_multiplier)

                        if item.item.name not in product_breakdown_summary:
                            asm_rate, _ = rule.get_effective_rates
                            final_rate = (
                                Decimal("4.00")
                                if current_month_dynamic_volume >= 4000
                                else Decimal("3.00")
                            ) if rule.has_dynamic_price else asm_rate
                            product_breakdown_summary[item.item.name] = {
                                "incentive": Decimal("0.00"),
                                "paid_qty": Decimal("0.00"),
                                "rate": final_rate,
                                "multiplier": rule.pack_size_multiplier,
                            }

                        product_breakdown_summary[item.item.name]["incentive"] += calculated_incentive
                        product_breakdown_summary[item.item.name]["paid_qty"] += item.quantity

            sales_log_data.append({
                "date": item.voucher.date,
                "customer": item.voucher.party_name,
                "voucher_id": item.voucher.id,
                "voucher_no": item.voucher.voucher_number,
                "product": item.item.name,
                "quantity": item.quantity,
                "amount": item.amount,
                "is_fully_paid": is_customer_paid,
                "is_trigger_on": is_customer_trigger_on,
                "is_above_msp": is_above_msp,
                "has_incentive": rule is not None,
                "payout_done": bool(payout_record and payout_record.is_paid_to_asm),
            })

        # --- 4. ARREARS ENGINE (HISTORICAL UNPAID) ---
        fiscal_year_start = date(reporting_year if reporting_month >= 4 else reporting_year - 1, 4, 1)
        unpaid_past_months_list = []
        total_historical_arrears = Decimal("0.00")

        if fiscal_year_start < reporting_month_start:
            for month_index in range(4, 13):
                scan_date = date(reporting_year, month_index, 1) if month_index >= 4 else date(reporting_year + 1, month_index, 1)
                if scan_date >= reporting_month_start:
                    break

                historical_statuses = CustomerVoucherStatus.objects.filter(
                    sold_by=target_salesperson,
                    voucher_date__month=month_index,
                    voucher_date__year=scan_date.year,
                    voucher_type__iexact="TAX INVOICE"
                )
                if not historical_statuses.exists():
                    continue

                historical_voucher_ids = list(historical_statuses.values_list("voucher_id", flat=True))
                historical_line_items = VoucherStockItem.objects.filter(
                    voucher_id__in=historical_voucher_ids
                ).select_related('item', 'voucher')

                historical_paid_ids = set(
                    IncentivePaymentStatus.objects.filter(
                        voucher_status__voucher_id__in=historical_voucher_ids,
                        is_paid_to_asm=True
                    ).values_list("voucher_status__voucher_id", flat=True)
                )

                # Calculate volume for historical month
                historical_monthly_volume = sum([
                    hi.quantity * product_incentive_rules.get(hi.item.name.strip().lower()).pack_size_multiplier
                    for hi in historical_line_items
                    if product_incentive_rules.get(hi.item.name.strip().lower()) and
                    product_incentive_rules.get(hi.item.name.strip().lower()).has_dynamic_price and
                    hi.voucher_id in [vs.voucher_id for vs in historical_statuses if vs.customer_id not in blocked_customer_ids]
                ])

                month_unpaid_sum = Decimal("0.00")
                for hi in historical_line_items:
                    if hi.voucher_id not in historical_paid_ids:
                        status_ref = historical_statuses.filter(voucher_id=hi.voucher_id).first()
                        if status_ref and status_ref.customer_id not in blocked_customer_ids:
                            earning, _ = self.calculate_item_incentive(
                                hi, historical_monthly_volume, product_incentive_rules, product_gst_lookup
                            )
                            month_unpaid_sum += earning

                if month_unpaid_sum > 0:
                    unpaid_past_months_list.append({'month': scan_date.strftime('%b'), 'amount': month_unpaid_sum})
                    total_historical_arrears += month_unpaid_sum

        # --- 5. FINAL CONTEXT UPDATE ---
        context.update({
            "rows": sales_log_data,
            "product_totals": product_breakdown_summary,
            "grand_potential": grand_potential_total,
            "payout_ready": payout_ready_total,
            "total_sales": sum([Decimal(str(vs.voucher_amount or 0)) for vs in monthly_voucher_statuses]),
            "dynamic_group_qty": current_month_dynamic_volume,
            "dynamic_rate_used": Decimal("4.00") if current_month_dynamic_volume >= 4000 else (
                Decimal("3.00") if current_month_dynamic_volume >= 1000 else Decimal("0.00")
            ),
            "category_summary": category_volume_summary,
            "current_month_pending": grand_potential_total - payout_ready_total,
            "arrears_list": unpaid_past_months_list,
            "total_arrears": total_historical_arrears,
        })
        return context

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        selected_salesperson_id = request.POST.get("salesperson")
        month_picker_input = request.POST.get("month_picker")
        voucher_id = request.POST.get("voucher_id")

        today_date = date.today()
        if month_picker_input:
            try:
                reporting_year, reporting_month = map(int, month_picker_input.split("-"))
            except (ValueError, TypeError):
                reporting_year, reporting_month = today_date.year, today_date.month
        else:
            reporting_year, reporting_month = today_date.year, today_date.month

        reporting_month_start = date(reporting_year, reporting_month, 1)
        _, days_in_month = monthrange(reporting_year, reporting_month)
        reporting_month_end = date(reporting_year, reporting_month, days_in_month)

        target_salesperson = None
        if selected_salesperson_id:
            target_salesperson = get_eligible_salespersons_qs().filter(id=selected_salesperson_id).first()

        def get_redirect():
            redirect_url = request.path
            params = []
            if selected_salesperson_id:
                params.append(f"salesperson={selected_salesperson_id}")
            if month_picker_input:
                params.append(f"month_picker={month_picker_input}")
            if params:
                redirect_url += f"?{'&'.join(params)}"
            return redirect(redirect_url)

        if not target_salesperson and action in ("bulk_pay", "bulk_unpay"):
            messages.error(request, "Please select a valid salesperson.")
            return get_redirect()

        # Load rules and lookups
        product_incentive_rules = {
            rule.product.name.strip().lower(): rule
            for rule in ProductIncentive.objects.select_related("product", "category").all()
        }
        product_gst_lookup = {
            price.product_id: price
            for price in ProductPrice.objects.only("product_id", "tax_rate")
        }
        blocked_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list("customer_id", flat=True)
        )

        def compute_month_vouchers(sp):
            statuses = CustomerVoucherStatus.objects.filter(
                sold_by=sp,
                voucher_type__iexact="TAX INVOICE",
                voucher_date__range=[reporting_month_start, reporting_month_end]
            ).select_related("voucher")
            v_ids = statuses.values_list("voucher_id", flat=True)
            vs_lookup = {vs.voucher_id: vs for vs in statuses}
            line_items = VoucherStockItem.objects.filter(voucher_id__in=v_ids).select_related("item", "voucher")

            dyn_volume = Decimal("0.00")
            for item in line_items:
                rule = product_incentive_rules.get(item.item.name.strip().lower())
                vs = vs_lookup.get(item.voucher_id)
                if rule and rule.has_dynamic_price and vs:
                    if vs.customer_id not in blocked_customer_ids:
                        dyn_volume += (item.quantity * rule.pack_size_multiplier)

            v_incentives = defaultdict(Decimal)
            for item in line_items:
                vs = vs_lookup.get(item.voucher_id)
                if not vs or vs.customer_id in blocked_customer_ids:
                    continue
                inc, _ = self.calculate_item_incentive(
                    item, dyn_volume, product_incentive_rules, product_gst_lookup
                )
                v_incentives[item.voucher_id] += inc

            return statuses, vs_lookup, v_incentives, dyn_volume

        if action == "bulk_pay":
            statuses, vs_lookup, v_incentives, _ = compute_month_vouchers(target_salesperson)
            paid_count = 0
            total_amount = Decimal("0.00")

            for vs in statuses:
                is_customer_paid = bool(vs.is_fully_paid or vs.unpaid_amount == 0)
                is_trigger_on = vs.customer_id not in blocked_customer_ids
                if is_customer_paid and is_trigger_on:
                    amount = v_incentives.get(vs.voucher_id, Decimal("0.00"))
                    payout_rec = IncentivePaymentStatus.objects.filter(voucher_status=vs).first()
                    if not payout_rec:
                        IncentivePaymentStatus.objects.create(
                            voucher_status=vs,
                            is_paid_to_asm=True,
                            paid_at=timezone.now(),
                            paid_by=request.user,
                            amount_frozen=amount,
                        )
                    else:
                        payout_rec.is_paid_to_asm = True
                        if not payout_rec.paid_at:
                            payout_rec.paid_at = timezone.now()
                        if not payout_rec.paid_by:
                            payout_rec.paid_by = request.user
                        payout_rec.amount_frozen = amount
                        payout_rec.save()

                    paid_count += 1
                    total_amount += amount

            if paid_count > 0:
                messages.success(
                    request,
                    f"Bulk Payout Complete: Marked {paid_count} vouchers as PAID (Total: ₹{total_amount:,.2f}) for {target_salesperson.name}."
                )
            else:
                messages.info(
                    request,
                    f"No eligible pending vouchers to mark as PAID for {target_salesperson.name} in {reporting_month_start.strftime('%B %Y')}."
                )

        elif action == "bulk_unpay":
            statuses = CustomerVoucherStatus.objects.filter(
                sold_by=target_salesperson,
                voucher_type__iexact="TAX INVOICE",
                voucher_date__range=[reporting_month_start, reporting_month_end]
            )
            deleted_count, _ = IncentivePaymentStatus.objects.filter(
                voucher_status__in=statuses
            ).delete()
            messages.success(
                request,
                f"Bulk UNPAY Complete: Removed PAID status for {deleted_count} vouchers of {target_salesperson.name}."
            )

        elif action == "pay_single":
            if not voucher_id:
                messages.error(request, "No voucher specified.")
                return get_redirect()

            vs = CustomerVoucherStatus.objects.filter(voucher_id=voucher_id).select_related("voucher", "sold_by").first()
            if not vs:
                messages.error(request, f"Voucher record #{voucher_id} not found.")
                return get_redirect()

            sp = target_salesperson or vs.sold_by
            _, _, v_incentives, dyn_vol = compute_month_vouchers(sp) if sp else (None, None, {}, Decimal("0.00"))

            amount = v_incentives.get(vs.voucher_id)
            if amount is None:
                v_items = VoucherStockItem.objects.filter(voucher_id=vs.voucher_id).select_related("item", "voucher")
                amount = sum(
                    self.calculate_item_incentive(vi, dyn_vol, product_incentive_rules, product_gst_lookup)[0]
                    for vi in v_items
                ) if vs.customer_id not in blocked_customer_ids else Decimal("0.00")

            IncentivePaymentStatus.objects.update_or_create(
                voucher_status=vs,
                defaults={
                    "is_paid_to_asm": True,
                    "paid_at": timezone.now(),
                    "paid_by": request.user,
                    "amount_frozen": amount,
                }
            )
            v_num = vs.voucher.voucher_number if (hasattr(vs, "voucher") and vs.voucher) else vs.voucher_id
            messages.success(request, f"Voucher #{v_num} marked as PAID (₹{amount:,.2f}).")

        elif action == "unpay":
            if not voucher_id:
                messages.error(request, "No voucher specified.")
                return get_redirect()

            vs = CustomerVoucherStatus.objects.filter(voucher_id=voucher_id).select_related("voucher").first()
            deleted_count, _ = IncentivePaymentStatus.objects.filter(voucher_status__voucher_id=voucher_id).delete()
            v_num = vs.voucher.voucher_number if (vs and hasattr(vs, "voucher") and vs.voucher) else voucher_id
            messages.success(request, f"Voucher #{v_num} marked as UNPAID.")

        else:
            messages.error(request, f"Unrecognized action '{action}'.")

        return get_redirect()

#21-8-26 kashish
class SalesHeadDashboardView(RBACViewMixin, TemplateView):
    template_name = "incentive_calculator/sales_head_dashboard.html"
    rbac_permission = "incentive.view_all"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 1. DATE AND CALENDAR SETUP
        month_picker_value = self.request.GET.get("month_picker")
        today = date.today()

        if month_picker_value:
            selected_year, selected_month = map(int, month_picker_value.split("-"))
        else:
            selected_year, selected_month = today.year, today.month

        month_start_date = date(selected_year, selected_month, 1)
        last_day_of_month = monthrange(selected_year, selected_month)[1]
        month_end_date = date(selected_year, selected_month, last_day_of_month)

        context.update({
            "month_picker": month_picker_value or f"{selected_year}-{selected_month:02d}",
            "display_month": month_start_date.strftime("%B, %Y")
        })

        # 2. CONFIGURATION & PRE-LOADING RULES
        # Dynamically resolve Sales Head via RBAC
        sh_profile = get_sales_head_profile()
        sales_head_id = sh_profile.id if sh_profile else None
        sales_head_name = sh_profile.name if sh_profile else "Sales Head"

        # Get IDs of customers where incentives are disabled (Trigger: OFF)
        blocked_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list('customer_id', flat=True)
        )

        # Create a map of product names to their incentive rules for fast lookup
        product_rule_lookup = {
            rule.product.name.strip().lower(): rule
            for rule in ProductIncentive.objects.select_related("product", "category").all()
        }

        product_price_lookup = {
            pp.product_id: pp
            for pp in ProductPrice.objects.only(
                "product_id",
                "tax_rate"
            )
        }

        # 3. FETCH VOUCHERS AND LINE ITEMS
        monthly_voucher_statuses = CustomerVoucherStatus.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[month_start_date, month_end_date]
        ).select_related('sold_by', 'sold_by__manager', 'customer', 'voucher')

        voucher_id_list = monthly_voucher_statuses.values_list('voucher_id', flat=True)
        all_line_items = VoucherStockItem.objects.filter(
            voucher_id__in=voucher_id_list
        ).select_related('item', 'voucher')

        # Map voucher IDs to their status objects for easy access inside the item loop
        voucher_status_lookup = {vs.voucher_id: vs for vs in monthly_voucher_statuses}

        # ------------------------------------------------------------------
        # PASS 1: CALCULATE TOTAL REVENUE & SHEET VOLUME PER SALESPERSON
        # ------------------------------------------------------------------
        salesperson_monthly_revenue = defaultdict(Decimal)
        salesperson_dynamic_volume = defaultdict(Decimal)  # Used for Sheets (3/4 rate logic)

        # Calculate revenue (Uses voucher_amount once per voucher)
        for status in monthly_voucher_statuses:
            if status.sold_by_id and status.sold_by_id != sales_head_id:
                salesperson_monthly_revenue[status.sold_by.id] += Decimal(str(status.voucher_amount or 0))

        # Calculate dynamic sheet volume
        for item in all_line_items:

            status = voucher_status_lookup.get(item.voucher_id)
            if not status or not status.sold_by_id or status.sold_by_id == sales_head_id:
                continue

            rule = product_rule_lookup.get(item.item.name.strip().lower())
            is_customer_trigger_on = status.customer_id not in blocked_customer_ids

            if rule and rule.has_dynamic_price and is_customer_trigger_on:
                salesperson_dynamic_volume[status.sold_by.id] += Decimal(str(item.quantity)) * rule.pack_size_multiplier

        # ------------------------------------------------------------------
        # PASS 2: CALCULATE TEAM OVERRIDES & STATUS BUCKETS
        # ------------------------------------------------------------------
        team_performance_report = {}
        company_category_summary = {}
        summary_totals = {
            "potential": Decimal("0"),
            "payable": Decimal("0"),
            "pending": Decimal("0"),
            "blocked": Decimal("0")
        }

        for item in all_line_items:
            status = voucher_status_lookup.get(item.voucher_id)
            if not status or not status.sold_by_id or status.sold_by_id == sales_head_id:
                continue

            salesperson = status.sold_by
            role_type = get_salesperson_role_type(salesperson)
            is_rsm = (role_type == "RSM")
            is_asm = not is_rsm

            # Initialize report entry for this salesperson
            if salesperson.id not in team_performance_report:
                required_target = 200000 if is_asm else 500000
                team_performance_report[salesperson.id] = {
                    "name": salesperson.name,
                    "role": role_type,
                    "rev": salesperson_monthly_revenue[salesperson.id],
                    "target": required_target,
                    "paid": Decimal("0"), "pending": Decimal("0"), "blocked": Decimal("0"),
                    "asm_earned": Decimal("0"),
                    "cats": {}, "items": []
                }

            rule = product_rule_lookup.get(item.item.name.strip().lower())
            if not rule:
                continue

            # Check Status Flags
            is_customer_trigger_on = status.customer_id not in blocked_customer_ids
            is_invoice_paid = bool(status.is_fully_paid or status.unpaid_amount == 0)

            # ----------------------------------------------------
            # MSP Calculation
            # ----------------------------------------------------

            # Price excluding GST
            unit_price_ex = (
                Decimal(str(item.amount)) / Decimal(str(item.quantity))
                if item.quantity > 0
                else Decimal("0")
            )

            # GST % & Effective MSP
            product_price = product_price_lookup.get(item.item_id)
            base_tax = Decimal(str(product_price.tax_rate)) if product_price else Decimal("0")
            voucher_date = status.voucher_date if status else (item.voucher.date if (hasattr(item, 'voucher') and item.voucher) else None)
            tax_rate, effective_msp = get_effective_product_tax_and_msp(
                item.item.name, voucher_date, base_tax, rule.msp
            )

            # Selling price including GST
            unit_price_inc = unit_price_ex * (
                    Decimal("1") + (tax_rate / Decimal("100"))
            )

            comparison_price = unit_price_inc

            difference = effective_msp - unit_price_inc

            if Decimal("0") < difference <= Decimal("1.00"):
                comparison_price = effective_msp

            is_above_msp = (
                    effective_msp == 0 or
                    comparison_price >= effective_msp
            )

            # Update Category Summary
            cat_name = rule.category.name if rule.category else "Other"
            company_category_summary[cat_name] = company_category_summary.get(cat_name, 0) + int(item.quantity)
            team_performance_report[salesperson.id]["cats"][cat_name] = team_performance_report[salesperson.id][
                                                                            "cats"].get(cat_name, 0) + int(
                item.quantity)

            # Resolve Rates
            asm_base_rate, rsm_cascade_rate = rule.get_effective_rates

            # Dynamic Rate (Sheets logic: 3000 -> 4, 500 -> 3)
            if rule.has_dynamic_price:
                vol = salesperson_dynamic_volume[salesperson.id]
                if vol < 1000:
                    active_asm_rate = Decimal("0.00")
                elif vol < 4000:
                    active_asm_rate = Decimal("3.00")
                else:
                    active_asm_rate = Decimal("4.00")
            else:
                active_asm_rate = asm_base_rate

            quantity_with_multiplier = Decimal(str(item.quantity)) * rule.pack_size_multiplier

            # ASM Earnings
            asm_incentive_amount = (
                quantity_with_multiplier * active_asm_rate
                if is_above_msp
                else Decimal("0")
            )
            team_performance_report[salesperson.id]["asm_earned"] += asm_incentive_amount

            # Sales Head Override Resolution
            sales_head_override_amount = Decimal("0")
            monthly_revenue_total = salesperson_monthly_revenue[salesperson.id]

            # Qualifier: Must hit Revenue Target AND MSRP
            if (
                    monthly_revenue_total >= team_performance_report[salesperson.id]["target"]
                    and is_above_msp
            ):
                if is_rsm:
                    # RSM Sale: SH gets ₹1 for Sheets, else full RSM Cascade
                    sales_head_override_amount = quantity_with_multiplier * (
                        Decimal("1.00") if rule.has_dynamic_price else rsm_cascade_rate)
                else:
                    # ASM Sale: SH gets ₹1 for Sheets (if vol > 1000), else 50% of RSM Cascade
                    if rule.has_dynamic_price:
                        sales_head_override_amount = quantity_with_multiplier * Decimal("1.00") if vol >= 1000 else Decimal("0.00")
                    else:
                        sales_head_override_amount = quantity_with_multiplier * (rsm_cascade_rate * Decimal('0.5'))

            # Sort into Payable, Pending, or Blocked Buckets
            summary_totals["potential"] += sales_head_override_amount
            row_status = "green"

            if not is_customer_trigger_on:
                summary_totals["blocked"] += sales_head_override_amount
                team_performance_report[salesperson.id]["blocked"] += sales_head_override_amount
                row_status = "blocked"
            elif is_invoice_paid:
                summary_totals["payable"] += sales_head_override_amount
                team_performance_report[salesperson.id]["paid"] += sales_head_override_amount
                row_status = "payable"
            else:
                summary_totals["pending"] += sales_head_override_amount
                team_performance_report[salesperson.id]["pending"] += sales_head_override_amount
                row_status = "pending"

            # Add individual row to the table
            team_performance_report[salesperson.id]["items"].append({
                "date": status.voucher_date,
                "customer": status.customer.name,
                "trigger": is_customer_trigger_on,
                "product": item.item.name,
                "qty": item.quantity,
                "asm": asm_incentive_amount,
                "head": sales_head_override_amount,
                "status": row_status
            })

        # Final Context Update
        context.update({
            "report": team_performance_report.values(),
            "totals": summary_totals,
            "cats": company_category_summary,
            "sh_name": sales_head_name
        })
        return context


#21-08-26 kashish
class RSMTeamIncentiveDashboardView(RBACViewMixin, TemplateView):
    template_name = "incentive_calculator/rsm_team_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        user = request.user
        if not (user.is_superuser or can(user, "incentive.view_all") or can(user, "incentive.view_team")):
            raise PermissionDenied("You do not have permission to view the RSM Team Dashboard.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        logged_in_user = self.request.user

        # Find the SalesPerson profile linked to the logged-in user
        user_sp_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        # Check if user has organization-wide incentive access (Admin, Sales Head, Business Manager, Accounts, Operations)
        is_privileged_user = (
            logged_in_user.is_superuser or
            can(logged_in_user, "incentive.view_all")
        )

        # ---------------------------------------------------------
        # IDENTITY & DROPDOWN LOGIC (Pure RBAC)
        # ---------------------------------------------------------
        all_rsms = get_rsm_salespersons_qs()

        if is_privileged_user:
            # Admins/Sales Heads/Accounts can see any RSM in the organization
            allowed_rsms = all_rsms
        elif user_sp_profile and all_rsms.filter(id=user_sp_profile.id).exists():
            # If an RSM logs in, they can ONLY see their own name in the dropdown
            allowed_rsms = all_rsms.filter(id=user_sp_profile.id)
        else:
            # Access denied for everyone else
            allowed_rsms = SalesPerson.objects.none()

        ctx["rsms"] = allowed_rsms

        # ---------------------------------------------------------
        # DATA SELECTION
        # ---------------------------------------------------------
        rsm_id = self.request.GET.get("rsm")
        month_picker = self.request.GET.get("month_picker")

        # If it's a regular RSM, force the ID to be their own ID
        if not is_privileged_user:
            rsm_id = user_sp_profile.id if user_sp_profile else None

        if not rsm_id or not month_picker:
            return ctx

        # Security check: Ensure the requested ID is within the allowed list
        if not allowed_rsms.filter(id=rsm_id).exists():
            return ctx

        # ---------------------------------------------------------
        # DATE & RULES SETUP
        # ---------------------------------------------------------
        try:
            year, month = map(int, month_picker.split("-"))
            start_date = date(year, month, 1)
            end_date = date(year, month, monthrange(year, month)[1])
        except (ValueError, TypeError):
            return ctx

        rsm_user = get_object_or_404(SalesPerson, id=rsm_id)
        ctx.update({"selected_rsm": rsm_user, "year": year, "month": month})

        # Fetch Rules and MSP Lookups
        disabled_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list('customer_id', flat=True)
        )
        incentive_rules = {cfg.product.name.strip().lower(): cfg for cfg in
                           ProductIncentive.objects.select_related("product", "category").all()}
        product_price_lookup = {
            pp.product_id: pp for pp in ProductPrice.objects.only("product_id", "tax_rate")
        }

        # ---------------------------------------------------------
        # CALCULATION ENGINE (TEAM BREAKDOWN)
        # ---------------------------------------------------------
        team_report = []
        team_category_summary = {}
        grand_rsm_paid = Decimal("0.00")
        grand_rsm_pending = Decimal("0.00")
        grand_rsm_triggered_off = Decimal("0.00")

        # Loop through ASMs that report to this RSM
        for asm in rsm_user.team_members.all():
            all_vouchers = CustomerVoucherStatus.objects.filter(
                sold_by=asm, voucher_type__iexact="TAX INVOICE",
                voucher_date__range=[start_date, end_date]
            ).select_related('voucher')

            asm_total_sheets_vol = Decimal("0.00")
            asm_category_summary = {}
            temp_item_list = []

            for vs in all_vouchers:
                is_trigger_on = vs.customer_id not in disabled_customer_ids
                is_paid = bool(vs.is_fully_paid or vs.unpaid_amount == 0)
                items = VoucherStockItem.objects.filter(voucher=vs.voucher).select_related('item', 'voucher')

                for si in items:
                    cfg = incentive_rules.get(si.item.name.strip().lower())
                    if cfg:
                        true_qty = Decimal(str(si.quantity)) * cfg.pack_size_multiplier
                        if cfg.has_dynamic_price and is_trigger_on:
                            asm_total_sheets_vol += true_qty

                        if cfg.category:
                            cat_name = cfg.category.name
                            asm_category_summary[cat_name] = asm_category_summary.get(cat_name, 0) + int(si.quantity)
                            team_category_summary[cat_name] = team_category_summary.get(cat_name, 0) + int(si.quantity)

                        temp_item_list.append({
                            'si': si, 'cfg': cfg, 'true_qty': true_qty, 'is_paid': is_paid,
                            'is_trigger_on': is_trigger_on
                        })

            # TIERED RATE LOGIC (MATCHING ASM DASHBOARD)
            rsm_sheet_rate = Decimal("1.00") if asm_total_sheets_vol >= 1000 else Decimal("0.00")
            if asm_total_sheets_vol < 1000:
                asm_m_rate = Decimal("0.00")
            elif asm_total_sheets_vol < 4000:
                asm_m_rate = Decimal("3.00")
            else:
                asm_m_rate = Decimal("4.00")

            asm_paid, asm_pending, asm_triggered_off = Decimal('0'), Decimal('0'), Decimal('0')
            rsm_from_asm_paid, rsm_from_asm_pending, rsm_triggered_off = Decimal('0'), Decimal('0'), Decimal('0')
            detailed_products = []

            for entry in temp_item_list:
                si, cfg, t_qty, is_paid, is_trigger_on = entry['si'], entry['cfg'], entry['true_qty'], entry['is_paid'], \
                entry['is_trigger_on']

                # MSP Calculation
                unit_price_ex = Decimal(str(si.amount)) / Decimal(str(si.quantity)) if si.quantity > 0 else Decimal("0")
                prod_price_meta = product_price_lookup.get(si.item_id)
                base_tax = Decimal(str(prod_price_meta.tax_rate)) if prod_price_meta else Decimal("0")
                voucher_date = si.voucher.date if (hasattr(si, 'voucher') and si.voucher) else None
                tax_rate, effective_msp = get_effective_product_tax_and_msp(
                    si.item.name, voucher_date, base_tax, cfg.msp
                )
                unit_price_inc = unit_price_ex * (Decimal("1") + tax_rate / Decimal("100"))

                # Robust rounding for MSP comparison
                comparison_price = unit_price_inc
                difference = effective_msp - unit_price_inc
                if Decimal("0") < difference <= Decimal("1.00"):
                    comparison_price = effective_msp

                is_above_msp = (effective_msp == 0 or comparison_price >= effective_msp)

                # Final Row Math
                asm_base, rsm_base = cfg.get_effective_rates
                final_asm_rate = asm_m_rate if cfg.has_dynamic_price else asm_base
                final_rsm_rate = rsm_sheet_rate if cfg.has_dynamic_price else rsm_base

                asm_val = t_qty * final_asm_rate if is_above_msp else 0
                rsm_val = t_qty * final_rsm_rate if is_above_msp else 0

                if not is_trigger_on:
                    asm_triggered_off += asm_val
                    rsm_triggered_off += rsm_val
                elif is_paid:
                    asm_paid += asm_val
                    rsm_from_asm_paid += rsm_val
                else:
                    asm_pending += asm_val
                    rsm_from_asm_pending += rsm_val

                detailed_products.append({
                    'name': si.item.name, 'qty': si.quantity, 'true_qty': t_qty,
                    'asm_incentive': asm_val, 'rsm_incentive': rsm_val,
                    'is_sheet': cfg.has_dynamic_price, 'is_paid': is_paid, 'is_trigger_on': is_trigger_on
                })

            if temp_item_list:
                team_report.append({
                    'asm_name': asm.name, 'total_sheets': asm_total_sheets_vol,
                    'asm_paid': asm_paid, 'asm_pending': asm_pending,
                    'rsm_paid': rsm_from_asm_paid, 'rsm_pending': rsm_from_asm_pending,
                    'items': detailed_products, 'asm_categories': asm_category_summary,
                    'asm_triggered_off': asm_triggered_off, 'rsm_triggered_off': rsm_triggered_off
                })
                grand_rsm_paid += rsm_from_asm_paid
                grand_rsm_pending += rsm_from_asm_pending
                grand_rsm_triggered_off += rsm_triggered_off

        ctx.update({
            "team_report": team_report, "grand_rsm_paid": grand_rsm_paid,
            "grand_rsm_pending": grand_rsm_pending, "grand_rsm_potential": grand_rsm_paid + grand_rsm_pending,
            "team_category_summary": team_category_summary, "grand_rsm_triggered_off": grand_rsm_triggered_off,
        })
        return ctx

class ProductIncentiveListView(RBACViewMixin, TemplateView):
    template_name = "incentive_calculator/product_incentive_list.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        user = request.user
        if not (user.is_superuser or can(user, "incentive.manage_config") or can(user, "incentive.view_all")):
            raise PermissionDenied("You do not have permission to view product incentive configurations.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["products"] = (
            ProductIncentive.objects
            .select_related("product")
            .order_by("product__name")
        )

        return ctx


@require_POST
def update_customer_trigger(request):
    if not request.user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Authentication required'}, status=401)

    # Check permission with RBAC: Admin, Accounts, Operations with manage_config or manage_payout
    if not (request.user.is_superuser or can(request.user, "incentive.manage_config") or can(request.user, "incentive.manage_payout")):
        return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)

    customer_id = request.POST.get('customer_id')
    # Convert the string 'true'/'false' from JS to a Python Boolean
    is_enabled = request.POST.get('is_enabled') == 'true'

    if not customer_id:
        return JsonResponse({'status': 'error', 'message': 'Missing Customer ID'}, status=400)

    # get_or_create handles both new settings and updating existing ones
    trigger, created = CustomerIncentiveTrigger.objects.get_or_create(customer_id=customer_id)
    trigger.is_enabled = is_enabled
    trigger.save()

    return JsonResponse({'status': 'success', 'is_enabled': trigger.is_enabled})

#21-8-26 kashish version to see below msp and previous unpaid incentive ...
class ASMIncentiveCalculatorPaidOnlyView(RBACViewMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_monthly.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        user = request.user
        if not (user.is_superuser or can(user, "incentive.view_all") or can(user, "incentive.view_team") or can(user, "incentive.view_self")):
            raise PermissionDenied("You do not have permission to view incentive calculations.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logged_in_user = self.request.user

        # --- 1. IDENTITY & PERMISSION CHECK (Pure RBAC) ---
        user_salesperson_profile = SalesPerson.objects.filter(user=logged_in_user).first()
        eligible_salespersons = get_eligible_salespersons_qs()

        is_all_viewer = logged_in_user.is_superuser or can(logged_in_user, "incentive.view_all")
        is_team_viewer = can(logged_in_user, "incentive.view_team")

        if is_all_viewer:
            allowed_salespersons_list = eligible_salespersons
            requested_id = self.request.GET.get("salesperson")
            if requested_id:
                target_salesperson = allowed_salespersons_list.filter(id=requested_id).first()
            elif user_salesperson_profile and allowed_salespersons_list.filter(id=user_salesperson_profile.id).exists():
                target_salesperson = user_salesperson_profile
            else:
                target_salesperson = None

        elif is_team_viewer and user_salesperson_profile:
            team_ids = list(user_salesperson_profile.team_members.values_list('id', flat=True))
            team_ids.append(user_salesperson_profile.id)

            allowed_salespersons_list = eligible_salespersons.filter(id__in=team_ids)
            requested_id = self.request.GET.get("salesperson")
            if requested_id:
                target_salesperson = allowed_salespersons_list.filter(id=requested_id).first()
            elif allowed_salespersons_list.filter(id=user_salesperson_profile.id).exists():
                target_salesperson = user_salesperson_profile
            else:
                target_salesperson = None

        else:
            if user_salesperson_profile and eligible_salespersons.filter(id=user_salesperson_profile.id).exists():
                target_salesperson = user_salesperson_profile
                allowed_salespersons_list = eligible_salespersons.filter(id=target_salesperson.id)
            else:
                target_salesperson = None
                allowed_salespersons_list = SalesPerson.objects.none()

        # --- 2. DATE AND CALENDAR SETUP ---
        selected_month_picker = self.request.GET.get("month_picker")
        today_date = date.today()

        if selected_month_picker:
            reporting_year, reporting_month = map(int, selected_month_picker.split("-"))
        else:
            reporting_year, reporting_month = today_date.year, today_date.month

        reporting_month_start = date(reporting_year, reporting_month, 1)
        _, last_day_of_month = monthrange(reporting_year, reporting_month)
        reporting_month_end = date(reporting_year, reporting_month, last_day_of_month)

        # --- 3. INITIALIZE CONTEXT DEFAULTS ---
        context.update({
            "salespersons": allowed_salespersons_list,
            "selected_salesperson": target_salesperson,
            "year": reporting_year, "month": reporting_month,
            "grand_total_incentive": Decimal("0.00"),
            "payable_incentive": Decimal("0.00"),
            "unpayable_incentive": Decimal("0.00"),
            "triggered_off_incentive": Decimal("0.00"),
            "total_sales": Decimal("0.00"),
            "dynamic_group_qty": Decimal("0.00"),
            "dynamic_rate_used": Decimal("0.00"),
            "historical_arrears_list": [],
            "total_arrears_amount": Decimal("0.00")
        })

        if not target_salesperson: return context

        # --- 4. PRE-LOAD RULES AND LOOKUPS ---

        all_incentive_rules = {
            rule.product.name.strip().lower(): rule
            for rule in ProductIncentive.objects.select_related("product", "category").all()
        }
        product_tax_rates = {
            price.product_id: price for price in ProductPrice.objects.only("product_id", "tax_rate")
        }
        blocked_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list('customer_id', flat=True)
        )

        # Helper function for calculating specific item payout
        def calculate_item_earning(item_obj, monthly_vol):
            rule = all_incentive_rules.get(item_obj.item.name.strip().lower())
            if not rule: return Decimal("0.00"), True


            # ---------------------------------------------
            # Step 1 : Price Excluding GST
            # ---------------------------------------------
            unit_price_ex = (
                Decimal(str(item_obj.amount)) / Decimal(str(item_obj.quantity))
                if item_obj.quantity > 0
                else Decimal("0")
            )

            # ---------------------------------------------
            # Step 2 : Fetch Product GST & Effective MSP
            # ---------------------------------------------
            product_price_meta = product_tax_rates.get(item_obj.item_id)
            base_tax = Decimal(str(product_price_meta.tax_rate)) if product_price_meta else Decimal("0")
            voucher_date = item_obj.voucher.date if (hasattr(item_obj, "voucher") and item_obj.voucher) else None
            tax_rate, effective_msp = get_effective_product_tax_and_msp(
                item_obj.item.name, voucher_date, base_tax, rule.msp
            )

            # ---------------------------------------------
            # Step 3 : Calculate Invoice Selling Price
            # ---------------------------------------------
            unit_price_inc = unit_price_ex * (
                    Decimal("1") + (tax_rate / Decimal("100"))
            )

            # ---------------------------------------------
            # Step 4 : Comparison Price (With ₹1 Buffer)
            # ---------------------------------------------
            comparison_price = unit_price_inc
            difference = effective_msp - unit_price_inc

            if Decimal("0") < difference <= Decimal("1.00"):
                comparison_price = effective_msp

            is_above_msp = (effective_msp == 0 or comparison_price >= effective_msp)

            if not is_above_msp:
                return Decimal("0.00"), False

            # Rate Resolution
            asm_rate, _ = rule.get_effective_rates
            if rule.has_dynamic_price:
                applied_rate = Decimal("4.00") if monthly_vol >= 4000 else (
                    Decimal("3.00") if monthly_vol >= 1000 else 0)
            else:
                applied_rate = asm_rate
            earning = (Decimal(str(item_obj.quantity)) * rule.pack_size_multiplier) * applied_rate
            return earning, True  # Returns money and True status

        # --- 5. ARREARS ENGINE (SCANNING PAST MONTHS IN FY) ---
        fiscal_year_start = date(reporting_year if reporting_month >= 4 else reporting_year - 1, 4, 1)
        historical_arrears_total = Decimal("0.00")
        historical_arrears_breakdown = []

        if fiscal_year_start < reporting_month_start:
            # Loop through months from April up to the month before current
            for past_m_idx in range(4, 13):
                # Setup scan date for this iteration
                scan_date = date(reporting_year, past_m_idx, 1) if past_m_idx >= 4 else date(reporting_year + 1,
                                                                                             past_m_idx, 1)
                if scan_date >= reporting_month_start: break

                past_month_statuses = CustomerVoucherStatus.objects.filter(
                    sold_by=target_salesperson, voucher_date__month=past_m_idx,
                    voucher_date__year=scan_date.year, voucher_type__iexact="TAX INVOICE"
                )
                if not past_month_statuses.exists(): continue

                past_month_v_ids = past_month_statuses.values_list("voucher_id", flat=True)
                past_month_items = VoucherStockItem.objects.filter(voucher_id__in=past_month_v_ids).select_related(
                    'item', 'voucher')

                # Calculate Volume for that specific past month
                past_month_vol = sum([
                    pi.quantity * all_incentive_rules.get(pi.item.name.strip().lower()).pack_size_multiplier
                    for pi in past_month_items if all_incentive_rules.get(pi.item.name.strip().lower()) and
                    all_incentive_rules.get(pi.item.name.strip().lower()).has_dynamic_price and
                    pi.voucher_id in [v.voucher_id for v in past_month_statuses if
                                      v.customer_id not in blocked_customer_ids]
                ])

                past_month_unpaid_sum = Decimal("0.00")
                for pi in past_month_items:
                    # Check if accountant has NOT yet finalized this payout
                    if not IncentivePaymentStatus.objects.filter(voucher_status__voucher_id=pi.voucher_id, is_paid_to_asm=True).exists():
                        if past_month_statuses.get(voucher_id=pi.voucher_id).customer_id not in blocked_customer_ids:
                            earning, _ = calculate_item_earning(pi, past_month_vol)
                            past_month_unpaid_sum += earning

                if past_month_unpaid_sum > 0:
                    historical_arrears_breakdown.append(
                        {'month': scan_date.strftime('%b'), 'amount': past_month_unpaid_sum})
                    historical_arrears_total += past_month_unpaid_sum

        # --- 6. CURRENT MONTH CALCULATION ---
        current_voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=target_salesperson, voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[reporting_month_start, reporting_month_end]
        )
        current_v_ids = current_voucher_statuses.values_list("voucher_id", flat=True)
        current_voucher_mapping = {vs.voucher_id: vs for vs in current_voucher_statuses}
        current_line_items = VoucherStockItem.objects.filter(voucher_id__in=current_v_ids).select_related("item",
                                                                                                          "voucher").prefetch_related(
            "voucher__rows")

        # Get Admin Payout Status
        payout_status_map = {pr.voucher_status.voucher_id: True for pr in
                             IncentivePaymentStatus.objects.filter(voucher_status__voucher_id__in=current_v_ids, is_paid_to_asm=True)}

        # Current Monthly Volume
        current_month_dynamic_volume = sum([
            si.quantity * all_incentive_rules.get(si.item.name.strip().lower()).pack_size_multiplier
            for si in current_line_items if all_incentive_rules.get(si.item.name.strip().lower()) and
            all_incentive_rules.get(si.item.name.strip().lower()).has_dynamic_price and
            current_voucher_mapping[si.voucher_id].customer_id not in blocked_customer_ids
        ])

        transaction_log_rows = []
        product_totals_map = {}
        category_summary_map = {}
        current_month_potential = Decimal("0.00")
        current_month_payable = Decimal("0.00")
        current_month_unpayable = Decimal("0.00")
        current_month_blocked = Decimal("0.00")
        total_revenue_accumulated = Decimal("0.00")
        processed_vouchers = set()

        for item in current_line_items:
            rule = all_incentive_rules.get(item.item.name.strip().lower())
            status_obj = current_voucher_mapping.get(item.voucher_id)
            is_trigger_on = status_obj.customer_id not in blocked_customer_ids
            is_fully_paid = bool(status_obj and (status_obj.is_fully_paid or status_obj.unpaid_amount == 0))

            # Revenue calculation
            if item.voucher_id not in processed_vouchers:
                processed_vouchers.add(item.voucher_id)
                party_row = item.voucher.rows.filter(ledger__icontains=item.voucher.party_name.strip()).first()
                total_revenue_accumulated += Decimal(str(party_row.amount if party_row else (item.voucher.amount or 0)))

            earning, is_item_above_msp = calculate_item_earning(item, current_month_dynamic_volume)

            if is_trigger_on:
                current_month_potential += earning
                if is_fully_paid:
                    current_month_payable += earning
                    if rule:
                        if rule.category: category_summary_map[rule.category.name] = category_summary_map.get(
                            rule.category.name, 0) + (item.quantity * rule.pack_size_multiplier)
                        if item.item.name not in product_totals_map:
                            asm_r, _ = rule.get_effective_rates
                            final_r = (Decimal("4.00") if current_month_dynamic_volume >= 4000 else Decimal(
                                "3.00")) if rule.has_dynamic_price else asm_r
                            product_totals_map[item.item.name] = {"potential_payout": 0, "ready_payout": 0,
                                                                  "paid_qty": 0, "rate": final_r}

                        product_totals_map[item.item.name]["paid_qty"] += item.quantity

                        product_totals_map[item.item.name]["ready_payout"] += earning
                else:
                    current_month_unpayable += earning
            else:
                current_month_blocked += earning

            transaction_log_rows.append({
                "date": item.voucher.date, "customer": item.voucher.party_name,
                "voucher_no": item.voucher.voucher_number,
                "product": item.item.name, "quantity": item.quantity, "amount": item.amount,
                "is_fully_paid": is_fully_paid, "has_incentive": rule is not None, "is_trigger_on": is_trigger_on,
                "voucher_id": item.voucher.id, "customer_id": status_obj.customer_id,
                "payout_done": payout_status_map.get(item.voucher_id, False),
                "is_above_msp": is_item_above_msp,

            })

        # RBAC helpers for template rendering
        emp = getattr(logged_in_user, "employee_profile", None)
        user_roles = []
        if emp:
            for assignment in emp.role_assignments.filter(is_active=True):
                if assignment.is_currently_valid():
                    user_roles.append(assignment.role.role_key)

        # Final Summary Context
        context.update({
            "rows": transaction_log_rows, "product_totals": product_totals_map,
            "category_summary": category_summary_map,
            "total_sales": total_revenue_accumulated, "dynamic_group_qty": current_month_dynamic_volume,
            "dynamic_rate_used": Decimal("4.00") if current_month_dynamic_volume >= 4000 else (
                Decimal("3.00") if current_month_dynamic_volume >= 1000 else 0),
            "historical_arrears_list": historical_arrears_breakdown,
            "total_arrears_amount": historical_arrears_total,
            "current_month_potential": current_month_potential,
            "payable_incentive": current_month_payable,
            "unpayable_incentive": current_month_unpayable,
            "triggered_off_incentive": current_month_blocked,
            "grand_total_incentive": current_month_potential + historical_arrears_total,
            "is_superuser": logged_in_user.is_superuser,
            "can_view_all": is_all_viewer,
            "can_manage_triggers": bool(logged_in_user.is_superuser or can(logged_in_user, "incentive.manage_config") or can(logged_in_user, "incentive.manage_payout")),
            "user_roles": user_roles,
        })
        return context
