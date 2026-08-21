from django.shortcuts import render

# Create your views here.
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from django.views.generic import TemplateView
from django.db.models import Prefetch
from calendar import monthrange

from customer_dashboard.models import SalesPerson, Customer, CustomerVoucherStatus
from tally_voucher.models import Voucher, VoucherStockItem
from incentive_calculator.models import ProductIncentive, ProductIncentiveTier, CustomerIncentiveTrigger
from django.contrib.auth.mixins import LoginRequiredMixin
from inventory.mixins import AccountantRequiredMixin
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


# Create your views here.

class IncentiveCalculatorWelcomeView(TemplateView):
    template_name = "incentive_calculator/welcome.html"


class IncentiveCalculatorView(TemplateView):
    template_name = 'incentive_calculator/incentive_calculator.html'



# without dynamic incentives enabled
class ASMIncentiveCalculatorView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        # No salesperson selected yet
        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS FOR THIS ASM
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)

        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS (TAX INVOICE ONLY)
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS (ONE ROW PER ITEM)
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        # --------------------------------------------------
        # BUILD ROWS + TOTALS
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            has_incentive = incentive_obj is not None
            incentive_per_unit = (
                incentive_obj.ASM_incentive if has_incentive else Decimal("0.00")
            )

            incentive_amount = (
                si.quantity * incentive_per_unit if has_incentive else Decimal("0.00")
            )

            # ---- ROW DATA ----
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            # ---- TOTALS (ONLY IF INCENTIVE EXISTS) ----
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount

                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx



class ASMIncentiveCalculatorView2(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        # No salesperson selected yet
        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS FOR THIS ASM
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS (TAX INVOICE ONLY)
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS (ONE ROW PER ITEM)
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES (UNCHANGED)
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects
            .select_related("product")
            .prefetch_related("productincentivetier_set")
        }

        # ==================================================
        # 🔹 ADDITION 1:
        # Calculate TOTAL quantity for DYNAMIC products only
        # ==================================================
        dynamic_product_qty = defaultdict(Decimal)

        for si in stock_items:
            if not si.item_id:
                continue

            incentive = incentives.get(si.item_id)
            if incentive and incentive.has_dynamic_price:
                dynamic_product_qty[si.item_id] += si.quantity

        # ==================================================
        # 🔹 ADDITION 2:
        # Resolve FINAL incentive per unit for dynamic products
        # ==================================================
        dynamic_incentive_map = {}

        for product_id, total_qty in dynamic_product_qty.items():
            incentive = incentives[product_id]

            tier = (
                incentive.productincentivetier_set
                .filter(min_quantity__lte=total_qty)
                .order_by("-min_quantity")
                .first()
            )

            if tier:
                dynamic_incentive_map[product_id] = tier.ASM_incentive
            else:
                dynamic_incentive_map[product_id] = Decimal("0.00")

        # --------------------------------------------------
        # BUILD ROWS + TOTALS (LEGACY LOGIC + SMALL CHANGE)
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            if incentive_obj:
                if incentive_obj.has_dynamic_price:
                    incentive_per_unit = dynamic_incentive_map.get(
                        product.id,
                        Decimal("0.00")
                    )
                else:
                    incentive_per_unit = incentive_obj.ASM_incentive
            else:
                incentive_per_unit = Decimal("0.00")

            has_incentive = incentive_per_unit > 0
            incentive_amount = si.quantity * incentive_per_unit

            # ---- ROW DATA ----
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            # ---- TOTALS ----
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount
                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx



class ASMIncentiveCalculatorView3(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        # No salesperson selected yet
        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS FOR THIS ASM
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS (TAX INVOICE ONLY)
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS (ONE ROW PER ITEM)
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES + TIERS
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.prefetch_related(
                "productincentivetier_set"
            )
        }

        # --------------------------------------------------
        # PASS 1: TOTAL QUANTITY PER PRODUCT
        # --------------------------------------------------
        product_quantities = defaultdict(Decimal)

        for si in stock_items:
            if si.item_id:
                product_quantities[si.item_id] += si.quantity

        # --------------------------------------------------
        # RESOLVE FINAL INCENTIVE PER UNIT (FLAT OR TIERED)
        # --------------------------------------------------
        resolved_incentives = {}

        for product_id, total_qty in product_quantities.items():
            incentive = incentives.get(product_id)

            if not incentive:
                continue

            # Flat incentive
            if not incentive.has_dynamic_price:
                resolved_incentives[product_id] = incentive.ASM_incentive
                continue

            # Tier-based incentive
            applicable_tier = (
                incentive.productincentivetier_set
                .filter(min_quantity__lte=total_qty)
                .order_by("-min_quantity")
                .first()
            )

            if applicable_tier:
                resolved_incentives[product_id] = applicable_tier.ASM_incentive
            else:
                resolved_incentives[product_id] = Decimal("0.00")

        # --------------------------------------------------
        # PASS 2: BUILD ROWS + TOTALS
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item

            incentive_per_unit = resolved_incentives.get(
                product.id if product else None,
                Decimal("0.00")
            )

            has_incentive = incentive_per_unit > 0
            incentive_amount = si.quantity * incentive_per_unit

            # ---- ROW DATA ----
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            # ---- TOTALS ----
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount

                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx



class ASMIncentiveCalculatorView4(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx["rows"] = []
            ctx["product_totals"] = {}
            ctx["grand_total_incentive"] = Decimal("0.00")
            ctx["selected_salesperson"] = None
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.prefetch_related(
                "productincentivetier_set"
            )
        }

        # --------------------------------------------------
        # PASS 1: TOTAL QUANTITY PER PRODUCT
        # --------------------------------------------------
        product_quantities = defaultdict(Decimal)

        for si in stock_items:
            if si.item_id:
                product_quantities[si.item_id] += si.quantity

        # --------------------------------------------------
        # RESOLVE INCENTIVES
        # --------------------------------------------------
        resolved_incentives = {}

        for product_id, total_qty in product_quantities.items():
            incentive = incentives.get(product_id)
            if not incentive:
                continue

            if not incentive.has_dynamic_price:
                resolved_incentives[product_id] = incentive.ASM_incentive
                continue

            tier = (
                incentive.productincentivetier_set
                .filter(min_quantity__lte=total_qty)
                .order_by("-min_quantity")
                .first()
            )

            resolved_incentives[product_id] = (
                tier.ASM_incentive if tier else Decimal("0.00")
            )

        # --------------------------------------------------
        # BUILD ROWS + TOTALS
        # --------------------------------------------------
        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            incentive_per_unit = resolved_incentives.get(
                product.id if product else None,
                Decimal("0.00")
            )

            # 🔑 IMPORTANT FIX
            has_incentive = incentive_obj is not None

            incentive_amount = si.quantity * incentive_per_unit

            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": has_incentive,
            })

            if incentive_amount > 0:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount
                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx




class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator_paid.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
            })
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH TAX INVOICE VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH PAYMENT STATUS (IMPORTANT)
        # --------------------------------------------------
        voucher_status_map = {
            cvs.voucher_id: cvs
            for cvs in CustomerVoucherStatus.objects.filter(
                voucher__in=vouchers
            )
        }

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        rows = []
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for si in stock_items:
            product = si.item
            incentive_obj = incentives.get(product.id) if product else None

            voucher_status = voucher_status_map.get(si.voucher_id)

            is_fully_paid = bool(
                voucher_status and voucher_status.is_fully_paid
            )

            has_incentive = incentive_obj is not None and is_fully_paid

            incentive_per_unit = (
                incentive_obj.ASM_incentive
                if has_incentive
                else Decimal("0.00")
            )

            incentive_amount = (
                si.quantity * incentive_per_unit
                if has_incentive
                else Decimal("0.00")
            )

            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name if product else si.item_name_text,
                "quantity": si.quantity,
                "incentive_per_unit": incentive_per_unit,
                "incentive_amount": incentive_amount,
                "has_incentive": incentive_obj is not None,
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": bool(voucher_status and voucher_status.is_partially_paid),
                "is_unpaid": bool(voucher_status and voucher_status.is_unpaid),
            })

            # --------------------------------------------------
            # TOTALS — ONLY FULLY PAID
            # --------------------------------------------------
            if has_incentive:
                key = product.name
                if key not in product_totals:
                    product_totals[key] = {
                        "quantity": Decimal("0.00"),
                        "incentive": Decimal("0.00"),
                    }

                product_totals[key]["quantity"] += si.quantity
                product_totals[key]["incentive"] += incentive_amount
                grand_total_incentive += incentive_amount

        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx









class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator_paid2.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
            })
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH TAX INVOICE VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH PAYMENT STATUS
        # --------------------------------------------------
        voucher_status_map = {
            cvs.voucher_id: cvs
            for cvs in CustomerVoucherStatus.objects.filter(
                voucher__in=vouchers
            )
        }

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        # --------------------------------------------------
        # TRACKING MAPS
        # --------------------------------------------------
        rows = []

        total_quantity_map = {}   # paid + unpaid
        paid_quantity_map = {}    # only paid
        product_map = {}          # product_id → product

        # --------------------------------------------------
        # BUILD ROW DATA + QUANTITY MAPS
        # --------------------------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            product_id = product.id
            product_map[product_id] = product

            voucher_status = voucher_status_map.get(si.voucher_id)
            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)

            # ---------- TOTAL QUANTITY (ALL) ----------
            total_quantity_map.setdefault(product_id, Decimal("0.00"))
            total_quantity_map[product_id] += si.quantity

            # ---------- PAID QUANTITY ONLY ----------
            if is_fully_paid:
                paid_quantity_map.setdefault(product_id, Decimal("0.00"))
                paid_quantity_map[product_id] += si.quantity

            # ---------- ROW DISPLAY ----------
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": bool(voucher_status and voucher_status.is_partially_paid),
                "is_unpaid": bool(voucher_status and voucher_status.is_unpaid),
            })

        # --------------------------------------------------
        # PRELOAD TIERS
        # --------------------------------------------------
        tiers_map = {}
        for tier in ProductIncentiveTier.objects.select_related("Product_Incentive"):
            pid = tier.Product_Incentive.product_id
            tiers_map.setdefault(pid, []).append(tier)

        # --------------------------------------------------
        # APPLY DYNAMIC PRICING PER PRODUCT
        # --------------------------------------------------
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for product_id, total_qty in total_quantity_map.items():
            product = product_map[product_id]
            paid_qty = paid_quantity_map.get(product_id, Decimal("0.00"))
            incentive_obj = incentives.get(product_id)

            if not incentive_obj:
                continue

            incentive_rate = incentive_obj.ASM_incentive
            applied_tier = None

            # ---------- DYNAMIC TIER LOGIC ----------
            if incentive_obj.has_dynamic_price:
                tiers = sorted(
                    tiers_map.get(product_id, []),
                    key=lambda t: t.min_quantity,
                    reverse=True
                )

                for tier in tiers:
                    if total_qty >= tier.min_quantity:
                        incentive_rate = tier.ASM_incentive
                        applied_tier = tier
                        break

            incentive_amount = paid_qty * incentive_rate
            grand_total_incentive += incentive_amount

            product_totals[product.name] = {
                "total_qty": total_qty,
                "paid_qty": paid_qty,
                "rate": incentive_rate,
                "tier": applied_tier.min_quantity if applied_tier else None,
                "incentive": incentive_amount,
                "has_dynamic": incentive_obj.has_dynamic_price,
            }

        # --------------------------------------------------
        # CONTEXT
        # --------------------------------------------------
        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive

        return ctx

# this view marks all the products which have incentive as green
class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_calculator_paid2.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # --------------------------------------------------
        # BASIC FILTER DATA
        # --------------------------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")

        today = date.today()
        default_start = today - timedelta(days=90)

        start_date = self.request.GET.get("start_date") or default_start
        end_date = self.request.GET.get("end_date") or today

        ctx["start_date"] = start_date
        ctx["end_date"] = end_date

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
            })
            return ctx

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # --------------------------------------------------
        # FETCH CUSTOMERS
        # --------------------------------------------------
        customers = Customer.objects.filter(salesperson=salesperson)
        customer_names = customers.values_list("name", flat=True)

        # --------------------------------------------------
        # FETCH TAX INVOICE VOUCHERS
        # --------------------------------------------------
        vouchers = Voucher.objects.filter(
            voucher_type__iexact="TAX INVOICE",
            party_name__in=customer_names,
            date__range=[start_date, end_date],
        )

        # --------------------------------------------------
        # FETCH PAYMENT STATUS
        # --------------------------------------------------
        voucher_status_map = {
            cvs.voucher_id: cvs
            for cvs in CustomerVoucherStatus.objects.filter(
                voucher__in=vouchers
            )
        }

        # --------------------------------------------------
        # FETCH STOCK ITEMS
        # --------------------------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # --------------------------------------------------
        # PRELOAD INCENTIVES
        # --------------------------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        # --------------------------------------------------
        # TRACKING MAPS
        # --------------------------------------------------
        rows = []

        total_quantity_map = {}   # paid + unpaid
        paid_quantity_map = {}    # only paid
        product_map = {}          # product_id → product
        total_sales = Decimal("0.00")

        # --------------------------------------------------
        # BUILD ROW DATA + QUANTITY MAPS
        # --------------------------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            product_id = product.id
            product_map[product_id] = product
            #new change
            has_incentive = product_id in incentives
            total_sales += Decimal(str(si.amount))

            voucher_status = voucher_status_map.get(si.voucher_id)
            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)

            # ---------- TOTAL QUANTITY (ALL) ----------
            total_quantity_map.setdefault(product_id, Decimal("0.00"))
            total_quantity_map[product_id] += si.quantity

            # ---------- PAID QUANTITY ONLY ----------
            if is_fully_paid:
                paid_quantity_map.setdefault(product_id, Decimal("0.00"))
                paid_quantity_map[product_id] += si.quantity

            # ---------- ROW DISPLAY ----------
            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_id": si.voucher.id,  # ✅ ADD THIS
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "amount": si.amount,
                "has_incentive": has_incentive,  # ✅ ADD THIS
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": bool(voucher_status and voucher_status.is_partially_paid),
                "is_unpaid": bool(voucher_status and voucher_status.is_unpaid),
            })

        # --------------------------------------------------
        # PRELOAD TIERS
        # --------------------------------------------------
        tiers_map = {}
        for tier in ProductIncentiveTier.objects.select_related("Product_Incentive"):
            pid = tier.Product_Incentive.product_id
            tiers_map.setdefault(pid, []).append(tier)

        # --------------------------------------------------
        # APPLY DYNAMIC PRICING PER PRODUCT
        # --------------------------------------------------
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for product_id, total_qty in total_quantity_map.items():
            product = product_map[product_id]
            paid_qty = paid_quantity_map.get(product_id, Decimal("0.00"))
            incentive_obj = incentives.get(product_id)

            if not incentive_obj:
                continue

            incentive_rate = incentive_obj.ASM_incentive
            applied_tier = None

            # ---------- DYNAMIC TIER LOGIC ----------
            if incentive_obj.has_dynamic_price:
                tiers = sorted(
                    tiers_map.get(product_id, []),
                    key=lambda t: t.min_quantity,
                    reverse=True
                )

                for tier in tiers:
                    if total_qty >= tier.min_quantity:
                        incentive_rate = tier.ASM_incentive
                        applied_tier = tier
                        break

            incentive_amount = paid_qty * incentive_rate
            grand_total_incentive += incentive_amount

            product_totals[product.name] = {
                "total_qty": total_qty,
                "paid_qty": paid_qty,
                "rate": incentive_rate,
                "tier": applied_tier.min_quantity if applied_tier else None,
                "incentive": incentive_amount,
                "has_dynamic": incentive_obj.has_dynamic_price,
            }

        # --------------------------------------------------
        # CONTEXT
        # --------------------------------------------------
        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive
        ctx["total_sales"] = total_sales

        return ctx



#claimed vouchers only monthly reports and dynamic prices correction
class ASMIncentiveCalculatorPaidOnlyView(TemplateView):
    template_name = "incentive_calculator/asm_incentive_monthly.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        # ---------------------------------
        # BASIC FILTERS
        # ---------------------------------
        ctx["salespersons"] = SalesPerson.objects.all().order_by("name")

        salesperson_id = self.request.GET.get("salesperson")
        month_picker = self.request.GET.get("month_picker")


        today = date.today()
        if month_picker:
            year, month = map(int, month_picker.split("-"))
        else:
            year, month = today.year, today.month

        ctx["year"] = year
        ctx["month"] = month

        if not salesperson_id:
            ctx.update({
                "rows": [],
                "product_totals": {},
                "grand_total_incentive": Decimal("0.00"),
                "selected_salesperson": None,
                "dynamic_group_qty": Decimal("0.00"),
                "dynamic_rate_used": None,
            })
            return ctx

        year = ctx["year"]
        month = ctx["month"]

        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])

        salesperson = SalesPerson.objects.filter(id=salesperson_id).first()
        ctx["selected_salesperson"] = salesperson

        if not salesperson:
            return ctx

        # ---------------------------------
        # ALL TAX INVOICES (PAID + UNPAID)
        # ---------------------------------
        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[start_date, end_date],
        )

        vouchers = Voucher.objects.filter(
            id__in=voucher_statuses.values_list("voucher_id", flat=True)
        )

        voucher_status_map = {cvs.voucher_id: cvs for cvs in voucher_statuses}

        # ---------------------------------
        # STOCK ITEMS
        # ---------------------------------
        stock_items = (
            VoucherStockItem.objects
            .filter(voucher__in=vouchers)
            .select_related("voucher", "item")
            .order_by("voucher__date")
        )

        # ---------------------------------
        # INCENTIVES
        # ---------------------------------
        incentives = {
            pi.product_id: pi
            for pi in ProductIncentive.objects.select_related("product")
        }

        rows = []

        product_map = {}
        paid_quantity_map = {}
        dynamic_products = set()

        total_sales = Decimal("0.00")

        # ---------------------------------
        # BUILD ROWS + MAPS
        # ---------------------------------
        for si in stock_items:
            product = si.item
            if not product:
                continue

            product_id = product.id
            product_map[product_id] = product

            incentive_obj = incentives.get(product_id)
            has_incentive = product_id in incentives
            has_dynamic = bool(incentive_obj and incentive_obj.has_dynamic_price)

            if has_dynamic:
                dynamic_products.add(product_id)

            total_sales += Decimal(str(si.amount))

            voucher_status = voucher_status_map.get(si.voucher_id)

            is_fully_paid = bool(voucher_status and voucher_status.is_fully_paid)
            is_partially_paid = bool(voucher_status and voucher_status.is_partially_paid)
            is_unpaid = bool(voucher_status and voucher_status.is_unpaid)

            # ---- PAID QTY ONLY FOR INCENTIVE ----
            if is_fully_paid:
                paid_quantity_map.setdefault(product_id, Decimal("0.00"))
                paid_quantity_map[product_id] += si.quantity

            rows.append({
                "date": si.voucher.date,
                "customer": si.voucher.party_name,
                "customer_id": voucher_status.customer_id if voucher_status else None,
                "voucher_id": si.voucher.id,
                "voucher_no": si.voucher.voucher_number,
                "product": product.name,
                "quantity": si.quantity,
                "amount": si.amount,
                "has_incentive": has_incentive,   # ✅ for yellow/green
                "is_fully_paid": is_fully_paid,
                "is_partially_paid": is_partially_paid,
                "is_unpaid": is_unpaid,
            })

        # ---------------------------------
        # DYNAMIC GROUP QTY (PAID ONLY)
        # ---------------------------------
        dynamic_group_qty = sum(
            paid_quantity_map.get(pid, Decimal("0.00"))
            for pid in dynamic_products
        )

        if dynamic_group_qty < 500:
            dynamic_rate_used = Decimal("0.00")
        elif dynamic_group_qty >= 3000:
            dynamic_rate_used = Decimal("4.00")
        else:
            dynamic_rate_used = None  # use base rates

        # ---------------------------------
        # PRODUCT TOTALS
        # ---------------------------------
        product_totals = {}
        grand_total_incentive = Decimal("0.00")

        for product_id, paid_qty in paid_quantity_map.items():
            product = product_map[product_id]
            incentive_obj = incentives.get(product_id)

            if not incentive_obj:
                continue

            if incentive_obj.has_dynamic_price:
                if dynamic_rate_used is not None:
                    rate = dynamic_rate_used
                else:
                    rate = incentive_obj.ASM_incentive
            else:
                rate = incentive_obj.ASM_incentive

            incentive_amount = paid_qty * rate
            grand_total_incentive += incentive_amount

            product_totals[product.name] = {
                "paid_qty": paid_qty,
                "rate": rate,
                "incentive": incentive_amount,
                "has_dynamic": incentive_obj.has_dynamic_price,
            }

        # ---------------------------------
        # CONTEXT
        # ---------------------------------
        ctx["rows"] = rows
        ctx["product_totals"] = product_totals
        ctx["grand_total_incentive"] = grand_total_incentive
        ctx["total_sales"] = total_sales
        ctx["dynamic_group_qty"] = dynamic_group_qty
        ctx["dynamic_rate_used"] = dynamic_rate_used

        return ctx


class ASMIncentiveCalculatorPaidOnlyView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_monthly.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logged_in_user = self.request.user

        # 1. IDENTITY & PERMISSION CHECK
        # Find the salesperson profile linked to this login
        user_salesperson_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        # Decide who appears in the dropdown and who we are looking at
        if logged_in_user.is_superuser or logged_in_user.is_accountant:
            # Admins can see everyone
            allowed_salespersons_list = SalesPerson.objects.all().order_by("name")
            requested_id = self.request.GET.get("salesperson")
            # If admin picked someone from dropdown, use that, else default to themselves
            target_salesperson = allowed_salespersons_list.filter(
                id=requested_id).first() if requested_id else user_salesperson_profile
        else:
            # Regular user can ONLY see themselves
            target_salesperson = user_salesperson_profile
            allowed_salespersons_list = SalesPerson.objects.filter(
                id=target_salesperson.id) if target_salesperson else SalesPerson.objects.none()

        # 2. INITIALIZE CONTEXT DEFAULTS
        context.update({
            "salespersons": allowed_salespersons_list,
            "selected_salesperson": target_salesperson,
            "rows": [],
            "product_totals": {},
            "category_summary": {},
            "grand_total_incentive": Decimal("0.00"),  # Total Potential
            "payable_incentive": Decimal("0.00"),  # Total Realized (Paid by Customer)
            "unpayable_incentive": Decimal("0.00"),  # Total Pending (Unpaid Invoices)
            "total_sales": Decimal("0.00"),
            "dynamic_group_qty": Decimal("0.00"),
            "dynamic_rate_used": Decimal("0.00"),
        })

        # 3. VALIDATE FILTERS
        selected_month_picker = self.request.GET.get("month_picker")
        if not target_salesperson or not selected_month_picker:
            return context

        # 4. DATE RANGE SETUP
        try:
            year, month = map(int, selected_month_picker.split("-"))
            month_start = date(year, month, 1)
            month_end = date(year, month, monthrange(year, month)[1])
            context.update({"year": year, "month": month})
        except (ValueError, TypeError):
            return context

        # 5. FETCH DATA & RULES
        # Map rules by name for high-speed robust matching (fixes "Non-Incentive" labels)
        all_rules = ProductIncentive.objects.select_related("product", "category").all()
        name_based_rule_map = {rule.product.name.strip().lower(): rule for rule in all_rules}

        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=target_salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[month_start, month_end]
        )

        if not voucher_statuses.exists():
            return context

        unique_voucher_ids = voucher_statuses.values_list("voucher_id", flat=True).distinct()
        voucher_status_mapping = {vs.voucher_id: vs for vs in voucher_statuses}
        stock_items_list = VoucherStockItem.objects.filter(voucher_id__in=unique_voucher_ids).select_related("voucher",
                                                                                                             "item").prefetch_related(
            "voucher__rows")

        # 6. DATA PRE-PROCESSING
        transaction_log_rows = []
        monthly_calculation_queue = []
        processed_vouchers_set = set()
        total_monthly_revenue = Decimal("0.00")

        for item in stock_items_list:
            if not item.item: continue

            product_name_key = item.item.name.strip().lower()
            rule_config = name_based_rule_map.get(product_name_key)
            status_obj = voucher_status_mapping.get(item.voucher_id)

            # Robust payment check: Is the checkbox checked OR is the balance 0?
            is_invoice_cleared = bool(status_obj and (status_obj.is_fully_paid or status_obj.unpaid_amount == 0))

            # Revenue Total (Ledger amount calculation)
            if item.voucher_id not in processed_vouchers_set:
                processed_vouchers_set.add(item.voucher_id)
                party_row = item.voucher.rows.filter(ledger__icontains=item.voucher.party_name.strip()).first()
                total_monthly_revenue += Decimal(str(party_row.amount if party_row else (item.voucher.amount or 0)))

            if rule_config:
                unit_price = Decimal(str(item.amount)) / Decimal(str(item.quantity)) if item.quantity > 0 else Decimal(
                    '0')
                monthly_calculation_queue.append({
                    'name': item.item.name,
                    'rule': rule_config,
                    'qty': Decimal(str(item.quantity)),
                    'is_paid': is_invoice_cleared,
                    'unit_p': unit_price,
                    'cat': rule_config.category.name if rule_config.category else "Other"
                })

            transaction_log_rows.append({
                "date": item.voucher.date,
                "customer": item.voucher.party_name,
                "voucher_no": item.voucher.voucher_number,
                "product": item.item.name,
                "quantity": item.quantity,
                "amount": item.amount,
                "is_fully_paid": is_invoice_cleared,
                "has_incentive": rule_config is not None,
                "voucher_id": item.voucher.id,
                "customer_id": status_obj.customer_id if status_obj else None,
            })

        # 7. PERFORMANCE THRESHOLD (The 0/3/4 Rate)
        total_dynamic_volume = sum([
            s['qty'] * s['rule'].pack_size_multiplier
            for s in monthly_calculation_queue if s['rule'].has_dynamic_price
        ])

        if total_dynamic_volume < 500:
            active_rate = Decimal("0.00")
        elif total_dynamic_volume < 3000:
            active_rate = Decimal("3.00")
        else:
            active_rate = Decimal("4.00")

        # 8. FINAL AGGREGATION (Splitting into Potential vs. Payable)
        payout_breakdown_table = {}
        category_item_summary = {}
        grand_total_potential = Decimal("0.00")
        realized_payable_total = Decimal("0.00")

        for sale in monthly_calculation_queue:
            rule = sale['rule']

            # Category Physical Item Count
            category_item_summary[sale['cat']] = category_item_summary.get(sale['cat'], Decimal('0')) + sale['qty']

            # Use Dynamic Rate or Fixed Base Rate
            asm_base_rate, _ = rule.get_effective_rates
            applied_rate = active_rate if rule.has_dynamic_price else asm_base_rate

            # MSP Check (Protection)
            if rule.msp > 0 and sale['unit_p'] < rule.msp: continue

            # Final Row Math
            row_value = sale['qty'] * rule.pack_size_multiplier * applied_rate

            grand_total_potential += row_value
            if sale['is_paid']:
                realized_payable_total += row_value

            # Build data for the Breakdown Table
            prod_name = sale['name']
            if prod_name not in payout_breakdown_table:
                payout_breakdown_table[prod_name] = {"paid_qty": 0, "rate": applied_rate, "potential_payout": 0,
                                                     "ready_payout": 0}

            payout_breakdown_table[prod_name]["paid_qty"] += sale['qty']
            payout_breakdown_table[prod_name]["potential_payout"] += row_value
            payout_breakdown_table[prod_name]["ready_payout"] += row_value if sale['is_paid'] else 0

        # 9. RETURN FINAL DATA
        context.update({
            "rows": transaction_log_rows,
            "product_totals": payout_breakdown_table,
            "category_summary": category_item_summary,
            "grand_total_incentive": grand_total_potential,
            "payable_incentive": realized_payable_total,
            "unpayable_incentive": grand_total_potential - realized_payable_total,
            "total_sales": total_monthly_revenue,
            "dynamic_group_qty": total_dynamic_volume,
            "dynamic_rate_used": active_rate,
        })
        return context


#21-8-26 kashish version to see below msp and previous unpaid incentive
class ASMIncentiveCalculatorPaidOnlyView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_monthly.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logged_in_user = self.request.user

        # --- 1. IDENTITY & PERMISSION CHECK ---
        user_salesperson_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        if logged_in_user.is_superuser or logged_in_user.is_accountant:
            allowed_salespersons_list = SalesPerson.objects.all().order_by("name")
            requested_id = self.request.GET.get("salesperson")
            target_salesperson = allowed_salespersons_list.filter(
                id=requested_id).first() if requested_id else user_salesperson_profile
        else:
            target_salesperson = user_salesperson_profile
            allowed_salespersons_list = SalesPerson.objects.filter(
                id=target_salesperson.id) if target_salesperson else SalesPerson.objects.none()

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
            # Step 2 : Fetch Product GST
            # ---------------------------------------------
            product_price_meta = product_tax_rates.get(item_obj.item_id)
            tax_rate = Decimal(str(product_price_meta.tax_rate)) if product_price_meta else Decimal("0")

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
            difference = rule.msp - unit_price_inc

            if Decimal("0") < difference <= Decimal("1.00"):
                comparison_price = unit_price_inc.quantize(
                    Decimal("1"),
                    rounding=ROUND_HALF_UP
                )

            is_above_msp = (rule.msp == 0 or comparison_price >= rule.msp)

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
                    'item')

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
                    if not IncentivePaymentStatus.objects.filter(voucher_status__voucher_id=pi.voucher_id).exists():
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
                             IncentivePaymentStatus.objects.filter(voucher_status__voucher_id__in=current_v_ids)}

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
        })
        return context


#21-8-26 kashish
class ASMIncentivePaidUnpaidView(AccountantRequiredMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_admin.html"

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

        # Update base context for the template filters
        context.update({
            "year": reporting_year,
            "month": reporting_month,
            "salespersons": SalesPerson.objects.all().order_by("name")
        })

        if not selected_salesperson_id:
            return context

        reporting_month_start = date(reporting_year, reporting_month, 1)
        _, days_in_month = monthrange(reporting_year, reporting_month)
        reporting_month_end = date(reporting_year, reporting_month, days_in_month)

        target_salesperson = SalesPerson.objects.filter(id=selected_salesperson_id).first()
        context["selected_salesperson"] = target_salesperson

        # --- 2. DATA LOOKUPS & RULES ---
        # Mapping product names to their configuration rules
        product_incentive_rules = {
            rule.product.name.strip().lower(): rule
            for rule in ProductIncentive.objects.select_related("product", "category").all()
        }

        # Mapping product IDs to their GST rates
        product_gst_lookup = {
            price.product_id: price
            for price in ProductPrice.objects.only("product_id", "tax_rate")
        }

        # Set of customer IDs that have incentives disabled
        blocked_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list('customer_id', flat=True)
        )

        # --- 3. HELPER: PRECISE CALCULATION ENGINE ---
        def calculate_item_incentive(item_record, monthly_volume_total):
            """Calculates the specific incentive for a single line item based on MSP and thresholds."""
            rule = product_incentive_rules.get(item_record.item.name.strip().lower())
            if not rule:
                return Decimal("0")

            # MSP Check (GST Inclusive calculation)
            unit_price_raw = Decimal(str(item_record.amount)) / Decimal(
                str(item_record.quantity)) if item_record.quantity > 0 else 0
            tax_rate = Decimal(str(product_gst_lookup.get(item_record.item_id).tax_rate)) if product_gst_lookup.get(
                item_record.item_id) else 0
            unit_price_with_tax = unit_price_raw * (Decimal("1") + (tax_rate / Decimal("100")))

            # Rounding logic for MSP comparison (₹1 buffer)
            comparison_price = unit_price_with_tax
            price_difference = rule.msp - unit_price_with_tax
            if Decimal("0") < price_difference <= Decimal("1.00"):
                comparison_price = unit_price_with_tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

            if rule.msp > 0 and comparison_price < rule.msp:
                return Decimal("0")

            # Determine Rate
            asm_fixed_rate, _ = rule.get_effective_rates
            if rule.has_dynamic_price:
                # Company Policy: 1000 units -> ₹3, 4000 units -> ₹4
                applied_rate = Decimal("4.00") if monthly_volume_total >= 4000 else (
                    Decimal("3.00") if monthly_volume_total >= 1000 else Decimal("0.00"))
            else:
                applied_rate = asm_fixed_rate

            return (Decimal(str(item_record.quantity)) * rule.pack_size_multiplier) * applied_rate

        # --- 4. CURRENT MONTH PROCESSING ---
        monthly_voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=target_salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[reporting_month_start, reporting_month_end]
        )

        voucher_id_list = monthly_voucher_statuses.values_list("voucher_id", flat=True)
        voucher_status_lookup = {vs.voucher_id: vs for vs in monthly_voucher_statuses}
        monthly_line_items = VoucherStockItem.objects.filter(voucher_id__in=voucher_id_list).select_related("item",
                                                                                                            "voucher")

        # Step 4a: Calculate Monthly Sheet Volume (Only items where Trigger is ON)
        current_month_dynamic_volume = Decimal("0.00")
        for item in monthly_line_items:
            rule = product_incentive_rules.get(item.item.name.strip().lower())
            voucher_status = voucher_status_lookup.get(item.voucher_id)
            if rule and rule.has_dynamic_price and voucher_status:
                if voucher_status.customer_id not in blocked_customer_ids:
                    current_month_dynamic_volume += (item.quantity * rule.pack_size_multiplier)

        # Step 4b: Build Dashboard Buckets
        sales_log_data = []
        product_breakdown_summary = {}
        category_volume_summary = {}
        grand_potential_total = Decimal("0.00")
        payout_ready_total = Decimal("0.00")

        for item in monthly_line_items:
            rule = product_incentive_rules.get(item.item.name.strip().lower())
            voucher_status = voucher_status_lookup.get(item.voucher_id)

            is_customer_paid = bool(voucher_status.is_fully_paid or voucher_status.unpaid_amount == 0)
            is_customer_trigger_on = voucher_status.customer_id not in blocked_customer_ids

            # Calculate item earning
            calculated_incentive = calculate_item_incentive(item, current_month_dynamic_volume)

            # Check if Accountant has already finalized this payout
            payout_record = IncentivePaymentStatus.objects.filter(voucher_status__voucher_id=item.voucher_id).first()

            if is_customer_trigger_on:
                grand_potential_total += calculated_incentive
                if is_customer_paid:
                    payout_ready_total += calculated_incentive

                    if rule:
                        # Update tables for the summary cards
                        if rule.category:
                            category_volume_summary[rule.category.name] = category_volume_summary.get(
                                rule.category.name, 0) + (item.quantity * rule.pack_size_multiplier)

                        if item.item.name not in product_breakdown_summary:
                            asm_rate, _ = rule.get_effective_rates
                            final_rate = (Decimal("4.00") if current_month_dynamic_volume >= 4000 else Decimal(
                                "3.00")) if rule.has_dynamic_price else asm_rate
                            product_breakdown_summary[item.item.name] = {"incentive": 0, "paid_qty": 0,
                                                                         "rate": final_rate,
                                                                         "multiplier": rule.pack_size_multiplier}

                        product_breakdown_summary[item.item.name]["incentive"] += calculated_incentive
                        product_breakdown_summary[item.item.name]["paid_qty"] += item.quantity

            sales_log_data.append({
                "date": item.voucher.date, "customer": item.voucher.party_name, "voucher_id": item.voucher.id,
                "voucher_no": item.voucher.voucher_number,
                "product": item.item.name, "quantity": item.quantity, "amount": item.amount,
                "is_fully_paid": is_customer_paid,
                "has_incentive": rule is not None, "payout_done": bool(payout_record and payout_record.is_paid_to_asm),
            })

        # --- 5. ARREARS ENGINE (HISTORICAL UNPAID) ---
        fiscal_year_start = date(reporting_year if reporting_month >= 4 else reporting_year - 1, 4, 1)
        unpaid_past_months_list = []
        total_historical_arrears = Decimal("0.00")

        if fiscal_year_start < reporting_month_start:
            for month_index in range(4, 13):  # Cycle through fiscal months April (4) to Dec (12) then Jan (1)
                # Ensure we don't scan future months or the current month
                scan_date = date(reporting_year, month_index, 1) if month_index >= 4 else date(reporting_year + 1,
                                                                                               month_index, 1)
                if scan_date >= reporting_month_start:
                    break

                historical_statuses = CustomerVoucherStatus.objects.filter(
                    sold_by=target_salesperson,
                    voucher_date__month=month_index,
                    voucher_date__year=scan_date.year,
                    voucher_type__iexact="TAX INVOICE"
                )
                if not historical_statuses.exists(): continue

                month_unpaid_sum = Decimal("0.00")
                historical_voucher_ids = historical_statuses.values_list("voucher_id", flat=True)
                historical_line_items = VoucherStockItem.objects.filter(
                    voucher_id__in=historical_voucher_ids).select_related('item')

                # Calculate volume for that specific historical month
                historical_monthly_volume = sum([
                    hi.quantity * product_incentive_rules.get(hi.item.name.strip().lower()).pack_size_multiplier
                    for hi in historical_line_items
                    if product_incentive_rules.get(hi.item.name.strip().lower()) and
                    product_incentive_rules.get(hi.item.name.strip().lower()).has_dynamic_price and
                    hi.voucher_id in [vs.voucher_id for vs in historical_statuses if
                                      vs.customer_id not in blocked_customer_ids]
                ])

                for hi in historical_line_items:
                    # Is this historical voucher still waiting for Accountant sign-off?
                    payout_exists = IncentivePaymentStatus.objects.filter(
                        voucher_status__voucher_id=hi.voucher_id).exists()
                    if not payout_exists:
                        status_ref = historical_statuses.get(voucher_id=hi.voucher_id)
                        if status_ref.customer_id not in blocked_customer_ids:
                            month_unpaid_sum += calculate_item_incentive(hi, historical_monthly_volume)

                if month_unpaid_sum > 0:
                    unpaid_past_months_list.append({'month': scan_date.strftime('%b'), 'amount': month_unpaid_sum})
                    total_historical_arrears += month_unpaid_sum

        # --- 6. FINAL CONTEXT UPDATE ---
        context.update({
            "rows": sales_log_data,
            "product_totals": product_breakdown_summary,
            "grand_potential": grand_potential_total,
            "payout_ready": payout_ready_total,
            "total_sales": sum([Decimal(str(vs.voucher_amount or 0)) for vs in monthly_voucher_statuses]),
            "dynamic_group_qty": current_month_dynamic_volume,
            "dynamic_rate_used": Decimal("4.00") if current_month_dynamic_volume >= 4000 else (
                Decimal("3.00") if current_month_dynamic_volume >= 1000 else 0),
            "category_summary": category_volume_summary,
            "current_month_pending": grand_potential_total - payout_ready_total,
            "arrears_list": unpaid_past_months_list,
            "total_arrears": total_historical_arrears
        })
        return context

#21-8-26 kashish
class SalesHeadDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/sales_head_dashboard.html"

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
        SALES_HEAD_NAME = "Bhavya"

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
        ).select_related('item')

        # Map voucher IDs to their status objects for easy access inside the item loop
        voucher_status_lookup = {vs.voucher_id: vs for vs in monthly_voucher_statuses}

        # ------------------------------------------------------------------
        # PASS 1: CALCULATE TOTAL REVENUE & SHEET VOLUME PER SALESPERSON
        # ------------------------------------------------------------------
        salesperson_monthly_revenue = defaultdict(Decimal)
        salesperson_dynamic_volume = defaultdict(Decimal)  # Used for Sheets (3/4 rate logic)

        # Calculate revenue (Uses voucher_amount once per voucher)
        for status in monthly_voucher_statuses:
            if status.sold_by and status.sold_by.name != SALES_HEAD_NAME:
                salesperson_monthly_revenue[status.sold_by.id] += Decimal(str(status.voucher_amount or 0))

        # Calculate dynamic sheet volume
        for item in all_line_items:

            status = voucher_status_lookup.get(item.voucher_id)
            if not status or not status.sold_by or status.sold_by.name == SALES_HEAD_NAME:
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
            if not status or not status.sold_by or status.sold_by.name == SALES_HEAD_NAME:
                continue

            salesperson = status.sold_by
            is_asm = salesperson.manager is not None
            is_rsm = not is_asm

            # Initialize report entry for this salesperson
            if salesperson.id not in team_performance_report:
                required_target = 200000 if is_asm else 500000
                team_performance_report[salesperson.id] = {
                    "name": salesperson.name,
                    "role": "ASM" if is_asm else "RSM",
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

            # GST %
            product_price = product_price_lookup.get(item.item_id)
            tax_rate = Decimal(str(product_price.tax_rate)) if product_price else Decimal("0")

            # Selling price including GST
            unit_price_inc = unit_price_ex * (
                    Decimal("1") + (tax_rate / Decimal("100"))
            )

            comparison_price = unit_price_inc

            difference = rule.msp - unit_price_inc

            if Decimal("0") < difference <= Decimal("1.00"):
                comparison_price = rule.msp

            is_above_msp = (
                    rule.msp == 0 or
                    comparison_price >= rule.msp
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
            "sh_name": SALES_HEAD_NAME
        })
        return context

class RSMTeamIncentiveDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/rsm_team_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        logged_in_user = self.request.user
        user_sp_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        is_privileged_user = (
                logged_in_user.is_accountant or
                logged_in_user.groups.filter(name='Accountant').exists()
        )
        target_rsm_names = ["Ankush", "Bhavya"]

        if is_privileged_user:
            allowed_rsms = SalesPerson.objects.filter(
                name__in=target_rsm_names,
                manager__isnull=True
            ).order_by("name")
        elif user_sp_profile and user_sp_profile.name in target_rsm_names and user_sp_profile.manager is None:
            allowed_rsms = SalesPerson.objects.filter(id=user_sp_profile.id)
        else:
            allowed_rsms = SalesPerson.objects.none()


        ctx["rsms"] = allowed_rsms
        rsm_id = self.request.GET.get("rsm")
        month_picker = self.request.GET.get("month_picker")

        if not is_privileged_user:
            rsm_id = user_sp_profile.id if user_sp_profile else None

        if not rsm_id or not month_picker:
            return ctx

        if not allowed_rsms.filter(id=rsm_id).exists():
            return ctx

        year, month = map(int, month_picker.split("-"))
        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])
        rsm_user = get_object_or_404(SalesPerson, id=rsm_id)
        ctx.update({"selected_rsm": rsm_user, "year": year, "month": month})

        incentive_rules = {cfg.product.name.strip().lower(): cfg for cfg in
                           ProductIncentive.objects.select_related("product", "category").all()}

        team_report = []
        team_category_summary = {}

        # GRAND TOTAL BUCKETS for the RSM
        grand_rsm_paid = Decimal("0.00")
        grand_rsm_pending = Decimal("0.00")

        for asm in rsm_user.team_members.all():
            # FETCH ALL VOUCHERS (Removed is_fully_paid=True filter here)
            all_vouchers = CustomerVoucherStatus.objects.filter(
                sold_by=asm, voucher_type__iexact="TAX INVOICE",
                voucher_date__range=[start_date, end_date]
            ).select_related('voucher')

            asm_total_sheets_vol = Decimal("0.00")
            asm_category_summary = {}
            temp_item_list = []

            for vs in all_vouchers:
                is_paid = bool(vs.is_fully_paid or vs.unpaid_amount == 0)
                items = VoucherStockItem.objects.filter(voucher=vs.voucher).select_related('item')

                for si in items:
                    cfg = incentive_rules.get(si.item.name.strip().lower())
                    if cfg:
                        true_qty = Decimal(str(si.quantity)) * cfg.pack_size_multiplier
                        if cfg.has_dynamic_price:
                            asm_total_sheets_vol += true_qty

                        if cfg.category:
                            cat_name = cfg.category.name
                            physical_qty = Decimal(str(si.quantity))
                            asm_category_summary[cat_name] = asm_category_summary.get(cat_name,
                                                                                      Decimal('0')) + physical_qty
                            team_category_summary[cat_name] = team_category_summary.get(cat_name,
                                                                                        Decimal('0')) + physical_qty

                        temp_item_list.append({
                            'si': si, 'cfg': cfg, 'true_qty': true_qty, 'is_paid': is_paid
                        })

            # Rate Logic (Based on Total Potential Volume)
            rsm_sheet_rate = Decimal('1.00') if asm_total_sheets_vol >= 1000 else Decimal('0.00')
            if asm_total_sheets_vol < 500:
                asm_m_rate = Decimal('0.00')
            elif asm_total_sheets_vol < 3000:
                asm_m_rate = Decimal('3.00')
            else:
                asm_m_rate = Decimal('4.00')

            # ASM BUCKETS
            asm_paid = Decimal('0')
            asm_pending = Decimal('0')
            rsm_from_asm_paid = Decimal('0')
            rsm_from_asm_pending = Decimal('0')
            detailed_products = []

            for entry in temp_item_list:
                si, cfg, t_qty, is_paid = entry['si'], entry['cfg'], entry['true_qty'], entry['is_paid']
                asm_base, rsm_base = cfg.get_effective_rates

                final_asm_rate = asm_m_rate if cfg.has_dynamic_price else asm_base
                final_rsm_rate = rsm_sheet_rate if cfg.has_dynamic_price else rsm_base

                unit_price = Decimal(str(si.amount)) / Decimal(str(si.quantity)) if si.quantity > 0 else 0

                # Math
                asm_val = t_qty * final_asm_rate if not (cfg.msp > 0 and unit_price < cfg.msp) else 0
                rsm_val = t_qty * final_rsm_rate

                if is_paid:
                    asm_paid += asm_val
                    rsm_from_asm_paid += rsm_val
                else:
                    asm_pending += asm_val
                    rsm_from_asm_pending += rsm_val

                detailed_products.append({
                    'name': si.item.name, 'qty': si.quantity, 'true_qty': t_qty,
                    'asm_incentive': asm_val, 'rsm_incentive': rsm_val,
                    'is_sheet': cfg.has_dynamic_price, 'is_paid': is_paid
                })

            if temp_item_list:
                team_report.append({
                    'asm_name': asm.name,
                    'total_sheets': asm_total_sheets_vol,
                    'asm_paid': asm_paid,
                    'asm_pending': asm_pending,
                    'rsm_paid': rsm_from_asm_paid,
                    'rsm_pending': rsm_from_asm_pending,
                    'items': detailed_products,
                    'asm_categories': asm_category_summary
                })
                grand_rsm_paid += rsm_from_asm_paid
                grand_rsm_pending += rsm_from_asm_pending

        ctx.update({
            "team_report": team_report,
            "grand_rsm_paid": grand_rsm_paid,
            "grand_rsm_pending": grand_rsm_pending,
            "grand_rsm_potential": grand_rsm_paid + grand_rsm_pending,
            "team_category_summary": team_category_summary
        })
        return ctx


class RSMTeamIncentiveDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/rsm_team_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        logged_in_user = self.request.user
        user_sp_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        is_privileged_user = (
                logged_in_user.is_accountant or
                logged_in_user.groups.filter(name='Accountant').exists()
        )
        target_rsm_names = ["Ankush", "Bhavya"]

        if is_privileged_user:
            allowed_rsms = SalesPerson.objects.filter(
                name__in=target_rsm_names,
                manager__isnull=True
            ).order_by("name")
        elif user_sp_profile and user_sp_profile.name in target_rsm_names and user_sp_profile.manager is None:
            allowed_rsms = SalesPerson.objects.filter(id=user_sp_profile.id)
        else:
            allowed_rsms = SalesPerson.objects.none()


        ctx["rsms"] = allowed_rsms
        rsm_id = self.request.GET.get("rsm")
        month_picker = self.request.GET.get("month_picker")

        if not is_privileged_user:
            rsm_id = user_sp_profile.id if user_sp_profile else None

        if not rsm_id or not month_picker:
            return ctx

        if not allowed_rsms.filter(id=rsm_id).exists():
            return ctx

        year, month = map(int, month_picker.split("-"))
        start_date = date(year, month, 1)
        end_date = date(year, month, monthrange(year, month)[1])
        rsm_user = get_object_or_404(SalesPerson, id=rsm_id)
        ctx.update({"selected_rsm": rsm_user, "year": year, "month": month})

        # 1. NEW: FETCH DISABLED CUSTOMER IDS
        disabled_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list('customer_id', flat=True)
        )

        incentive_rules = {cfg.product.name.strip().lower(): cfg for cfg in
                           ProductIncentive.objects.select_related("product", "category").all()}

        team_report = []
        team_category_summary = {}

        # GRAND TOTAL BUCKETS for the RSM
        grand_rsm_paid = Decimal("0.00")
        grand_rsm_pending = Decimal("0.00")
        grand_rsm_triggered_off = Decimal("0.00") # NEW


        for asm in rsm_user.team_members.all():
            # FETCH ALL VOUCHERS (Removed is_fully_paid=True filter here)
            all_vouchers = CustomerVoucherStatus.objects.filter(
                sold_by=asm, voucher_type__iexact="TAX INVOICE",
                voucher_date__range=[start_date, end_date]
            ).select_related('voucher')

            asm_total_sheets_vol = Decimal("0.00")
            asm_category_summary = {}
            temp_item_list = []

            for vs in all_vouchers:
                # 2. NEW: Identify if this customer is triggered ON or OFF
                is_trigger_on = vs.customer_id not in disabled_customer_ids

                is_paid = bool(vs.is_fully_paid or vs.unpaid_amount == 0)
                items = VoucherStockItem.objects.filter(voucher=vs.voucher).select_related('item')

                for si in items:
                    cfg = incentive_rules.get(si.item.name.strip().lower())
                    if cfg:
                        true_qty = Decimal(str(si.quantity)) * cfg.pack_size_multiplier
                        if cfg.has_dynamic_price and is_trigger_on:
                            asm_total_sheets_vol += true_qty

                        if cfg.category:
                            cat_name = cfg.category.name
                            physical_qty = Decimal(str(si.quantity))
                            asm_category_summary[cat_name] = asm_category_summary.get(cat_name,
                                                                                      Decimal('0')) + physical_qty
                            team_category_summary[cat_name] = team_category_summary.get(cat_name,
                                                                                        Decimal('0')) + physical_qty

                        temp_item_list.append({
                            'si': si, 'cfg': cfg, 'true_qty': true_qty, 'is_paid': is_paid, 'is_trigger_on': is_trigger_on # Added flag
                        })

            # Rate Logic (Based on Total Potential Volume)
            rsm_sheet_rate = Decimal('1.00') if asm_total_sheets_vol >= 1000 else Decimal('0.00')
            if asm_total_sheets_vol < 500:
                asm_m_rate = Decimal('0.00')
            elif asm_total_sheets_vol < 3000:
                asm_m_rate = Decimal('3.00')
            else:
                asm_m_rate = Decimal('4.00')

            # ASM BUCKETS
            asm_paid = Decimal('0')
            asm_pending = Decimal('0')
            asm_triggered_off = Decimal('0.00')
            rsm_from_asm_paid = Decimal('0')
            rsm_from_asm_pending = Decimal('0')
            rsm_triggered_off = Decimal('0.00')
            detailed_products = []

            for entry in temp_item_list:
                si, cfg, t_qty, is_paid, is_trigger_on = entry['si'], entry['cfg'], entry['true_qty'], entry['is_paid'], entry['is_trigger_on']
                asm_base, rsm_base = cfg.get_effective_rates

                final_asm_rate = asm_m_rate if cfg.has_dynamic_price else asm_base
                final_rsm_rate = rsm_sheet_rate if cfg.has_dynamic_price else rsm_base

                unit_price = Decimal(str(si.amount)) / Decimal(str(si.quantity)) if si.quantity > 0 else 0

                # Math
                asm_val = t_qty * final_asm_rate if not (cfg.msp > 0 and unit_price < cfg.msp) else 0
                rsm_val = t_qty * final_rsm_rate

                # 4. NEW: Logic for Buckets
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
                    'is_sheet': cfg.has_dynamic_price, 'is_paid': is_paid
                })

            if temp_item_list:
                team_report.append({
                    'asm_name': asm.name,
                    'total_sheets': asm_total_sheets_vol,
                    'asm_paid': asm_paid,
                    'asm_pending': asm_pending,
                    'rsm_paid': rsm_from_asm_paid,
                    'rsm_pending': rsm_from_asm_pending,
                    'items': detailed_products,
                    'asm_categories': asm_category_summary,
                    'asm_triggered_off': asm_triggered_off,  # For UI
                    'rsm_triggered_off': rsm_triggered_off,  # For UI

                })
                grand_rsm_paid += rsm_from_asm_paid
                grand_rsm_pending += rsm_from_asm_pending
                grand_rsm_triggered_off += rsm_triggered_off


        ctx.update({
            "team_report": team_report,
            "grand_rsm_paid": grand_rsm_paid,
            "grand_rsm_pending": grand_rsm_pending,
            "grand_rsm_potential": grand_rsm_paid + grand_rsm_pending,
            "team_category_summary": team_category_summary,
            "grand_rsm_triggered_off": grand_rsm_triggered_off,  # For top card

        })
        return ctx

#21-08-26 kashish
class RSMTeamIncentiveDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/rsm_team_dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        logged_in_user = self.request.user

        # Find the SalesPerson profile linked to the logged-in user
        user_sp_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        # Check if user is an Admin or Accountant
        is_privileged_user = (
                logged_in_user.is_superuser or
                logged_in_user.is_accountant or
                logged_in_user.groups.filter(name='Accountant').exists()
        )

        # ---------------------------------------------------------
        # IDENTITY & DROPDOWN LOGIC
        # ---------------------------------------------------------
        # These are the individuals who act as Regional Heads (RSMs)
        target_rsm_names = ["Ankush", "Aman"]

        if is_privileged_user:
            # Admins/Accountants can see any RSM in the target list
            allowed_rsms = SalesPerson.objects.filter(
                name__in=target_rsm_names
            ).order_by("name")
        elif user_sp_profile and user_sp_profile.name in target_rsm_names:
            # If Ankush or Aman logs in, they can ONLY see their own name in the dropdown
            allowed_rsms = SalesPerson.objects.filter(id=user_sp_profile.id)
        else:
            # Access denied for everyone else
            allowed_rsms = SalesPerson.objects.none()

        ctx["rsms"] = allowed_rsms

        # ---------------------------------------------------------
        # DATA SELECTION
        # ---------------------------------------------------------
        rsm_id = self.request.GET.get("rsm")
        month_picker = self.request.GET.get("month_picker")

        # If it's a regular RSM (Ankush/Aman), force the ID to be their own ID
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
                items = VoucherStockItem.objects.filter(voucher=vs.voucher).select_related('item')

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
                unit_price_ex = Decimal(str(si.amount)) / Decimal(str(si.quantity)) if si.quantity > 0 else 0
                prod_price_meta = product_price_lookup.get(si.item_id)
                tax_rate = Decimal(str(prod_price_meta.tax_rate)) if prod_price_meta else 0
                unit_price_inc = unit_price_ex * (Decimal("1") + tax_rate / Decimal("100"))

                # Robust rounding for MSP comparison
                comparison_price = unit_price_inc
                difference = cfg.msp - unit_price_inc
                if Decimal("0") < difference <= Decimal("1.00"):
                    comparison_price = unit_price_inc.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

                is_above_msp = (cfg.msp == 0 or comparison_price >= cfg.msp)

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

class ProductIncentiveListView(TemplateView):
    template_name = "incentive_calculator/product_incentive_list.html"

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
    # Only allow Admin/Accountant to change this
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=403)

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

class ASMIncentiveCalculatorPaidOnlyView(LoginRequiredMixin, TemplateView):
    template_name = "incentive_calculator/asm_incentive_monthly.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logged_in_user = self.request.user

        # 1. IDENTITY & PERMISSION CHECK
        # Find the salesperson profile linked to this login
        user_salesperson_profile = SalesPerson.objects.filter(user=logged_in_user).first()

        # Decide who appears in the dropdown and who we are looking at
        if logged_in_user.is_superuser or logged_in_user.is_accountant:
            # Admins can see everyone
            allowed_salespersons_list = SalesPerson.objects.all().order_by("name")
            requested_id = self.request.GET.get("salesperson")
            # If admin picked someone from dropdown, use that, else default to themselves
            target_salesperson = allowed_salespersons_list.filter(
                id=requested_id).first() if requested_id else user_salesperson_profile
        else:
            # Regular user can ONLY see themselves
            target_salesperson = user_salesperson_profile
            allowed_salespersons_list = SalesPerson.objects.filter(
                id=target_salesperson.id) if target_salesperson else SalesPerson.objects.none()

        # 2. INITIALIZE CONTEXT DEFAULTS
        context.update({
            "salespersons": allowed_salespersons_list,
            "selected_salesperson": target_salesperson,
            "rows": [],
            "product_totals": {},
            "category_summary": {},
            "grand_total_incentive": Decimal("0.00"),  # Total Potential
            "payable_incentive": Decimal("0.00"),  # Total Realized (Paid by Customer)
            "unpayable_incentive": Decimal("0.00"),  # Total Pending (Unpaid Invoices)
            "triggered_off_incentive": Decimal("0.00"),  # NEW
            "total_sales": Decimal("0.00"),
            "dynamic_group_qty": Decimal("0.00"),
            "dynamic_rate_used": Decimal("0.00"),
        })

        # 3. VALIDATE FILTERS
        selected_month_picker = self.request.GET.get("month_picker")
        if not target_salesperson or not selected_month_picker:
            return context

        # 4. DATE RANGE SETUP
        try:
            year, month = map(int, selected_month_picker.split("-"))
            month_start = date(year, month, 1)
            month_end = date(year, month, monthrange(year, month)[1])
            context.update({"year": year, "month": month})
        except (ValueError, TypeError):
            return context

        # 5. FETCH DATA & RULES

        # NEW: Fetch disabled customer triggers for fast lookup
        disabled_customer_ids = set(
            CustomerIncentiveTrigger.objects.filter(is_enabled=False).values_list('customer_id', flat=True)
        )
        # Map rules by name for high-speed robust matching (fixes "Non-Incentive" labels)
        all_rules = ProductIncentive.objects.select_related("product", "category").all()
        name_based_rule_map = {rule.product.name.strip().lower(): rule for rule in all_rules}

        voucher_statuses = CustomerVoucherStatus.objects.filter(
            sold_by=target_salesperson,
            voucher_type__iexact="TAX INVOICE",
            voucher_date__range=[month_start, month_end]
        )

        if not voucher_statuses.exists():
            return context

        unique_voucher_ids = voucher_statuses.values_list("voucher_id", flat=True).distinct()
        voucher_status_mapping = {vs.voucher_id: vs for vs in voucher_statuses}
        stock_items_list = VoucherStockItem.objects.filter(voucher_id__in=unique_voucher_ids).select_related("voucher",
                                                                                                             "item").prefetch_related(
            "voucher__rows")

        # 6. DATA PRE-PROCESSING
        transaction_log_rows = []
        monthly_calculation_queue = []
        processed_vouchers_set = set()
        total_monthly_revenue = Decimal("0.00")

        for item in stock_items_list:
            if not item.item: continue

            product_name_key = item.item.name.strip().lower()
            rule_config = name_based_rule_map.get(product_name_key)
            status_obj = voucher_status_mapping.get(item.voucher_id)

            # NEW: Check if this specific customer's trigger is ON or OFF
            cust_id = status_obj.customer_id if status_obj else None
            is_trigger_on = cust_id not in disabled_customer_ids

            # Robust payment check: Is the checkbox checked OR is the balance 0?
            is_invoice_cleared = bool(status_obj and (status_obj.is_fully_paid or status_obj.unpaid_amount == 0))

            # Revenue Total (Ledger amount calculation)
            if item.voucher_id not in processed_vouchers_set:
                processed_vouchers_set.add(item.voucher_id)
                party_row = item.voucher.rows.filter(ledger__icontains=item.voucher.party_name.strip()).first()
                total_monthly_revenue += Decimal(str(party_row.amount if party_row else (item.voucher.amount or 0)))

            if rule_config:
                unit_price = Decimal(str(item.amount)) / Decimal(str(item.quantity)) if item.quantity > 0 else Decimal(
                    '0')
                monthly_calculation_queue.append({
                    'name': item.item.name,
                    'rule': rule_config,
                    'qty': Decimal(str(item.quantity)),
                    'is_paid': is_invoice_cleared,
                    'unit_p': unit_price,
                    'cat': rule_config.category.name if rule_config.category else "Other",
                    'is_trigger_on': is_trigger_on,  # NEW

                })

            transaction_log_rows.append({
                "date": item.voucher.date,
                "customer": item.voucher.party_name,
                "voucher_no": item.voucher.voucher_number,
                "product": item.item.name,
                "quantity": item.quantity,
                "amount": item.amount,
                "is_fully_paid": is_invoice_cleared,
                "has_incentive": rule_config is not None,
                "voucher_id": item.voucher.id,
                "customer_id": status_obj.customer_id if status_obj else None,
                "is_trigger_on": is_trigger_on,  # NEW

            })

        # 7. PERFORMANCE THRESHOLD (The 0/3/4 Rate)
        # NEW: Sales for "Triggered Off" customers are excluded from the volume threshold calculation
        total_dynamic_volume = sum([
            s['qty'] * s['rule'].pack_size_multiplier
            for s in monthly_calculation_queue if s['rule'].has_dynamic_price and s['is_trigger_on']
        ])

        if total_dynamic_volume < 500:
            active_rate = Decimal("0.00")
        elif total_dynamic_volume < 3000:
            active_rate = Decimal("3.00")
        else:
            active_rate = Decimal("4.00")

        # 8. FINAL AGGREGATION (Splitting into Potential vs. Payable)
        payout_breakdown_table = {}
        category_item_summary = {}
        grand_total_potential = Decimal("0.00")
        realized_payable_total = Decimal("0.00")
        unpayable_pending_total = Decimal("0.00")  # NEW: Added explicit bucket
        triggered_off_total = Decimal("0.00")  # NEW: Added explicit bucket

        for sale in monthly_calculation_queue:
            rule = sale['rule']

            # Category Physical Item Count
            category_item_summary[sale['cat']] = category_item_summary.get(sale['cat'], Decimal('0')) + sale['qty']

            # Use Dynamic Rate or Fixed Base Rate
            asm_base_rate, _ = rule.get_effective_rates
            applied_rate = active_rate if rule.has_dynamic_price else asm_base_rate

            # MSP Check (Protection)
            if rule.msp > 0 and sale['unit_p'] < rule.msp: continue

            # Final Row Math
            row_value = sale['qty'] * rule.pack_size_multiplier * applied_rate

            grand_total_potential += row_value
            # NEW: Split into 3 Buckets
            if not sale['is_trigger_on']:
                triggered_off_total += row_value
            elif sale['is_paid']:
                realized_payable_total += row_value
            else:
                unpayable_pending_total += row_value

            # Build data for the Breakdown Table
            prod_name = sale['name']
            if prod_name not in payout_breakdown_table:
                payout_breakdown_table[prod_name] = {"paid_qty": 0, "rate": applied_rate, "potential_payout": 0,
                                                     "ready_payout": 0}

            payout_breakdown_table[prod_name]["paid_qty"] += sale['qty']
            payout_breakdown_table[prod_name]["potential_payout"] += row_value
            payout_breakdown_table[prod_name]["ready_payout"] += row_value if (sale['is_paid'] and sale['is_trigger_on']) else 0

        # 9. RETURN FINAL DATA
        context.update({
            "rows": transaction_log_rows,
            "product_totals": payout_breakdown_table,
            "category_summary": category_item_summary,
            "grand_total_incentive": grand_total_potential,
            "payable_incentive": realized_payable_total,
            # "unpayable_incentive": grand_total_potential - realized_payable_total,
            "unpayable_incentive": unpayable_pending_total,  # NEW
            "triggered_off_incentive": triggered_off_total,  # NEW
            "total_sales": total_monthly_revenue,
            "dynamic_group_qty": total_dynamic_volume,
            "dynamic_rate_used": active_rate,
        })
        return context
