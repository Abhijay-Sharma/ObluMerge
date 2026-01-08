from django.shortcuts import render

# Create your views here.
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from django.views.generic import TemplateView
from django.db.models import Prefetch

from customer_dashboard.models import SalesPerson, Customer, CustomerVoucherStatus
from tally_voucher.models import Voucher, VoucherStockItem
from incentive_calculator.models import ProductIncentive, ProductIncentiveTier




# Create your views here.

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

        return ctx