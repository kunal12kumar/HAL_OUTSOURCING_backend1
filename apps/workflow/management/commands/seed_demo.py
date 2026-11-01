"""Load demo users, vendors and records at several stages of the workflow.

All records are created through the same service functions the API uses, so the
demo data obeys every rule. Never run this against a production database.
"""
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.vendors.models import Vendor
from apps.workflow import services
from apps.workflow.models import (
    PurchaseOrder,
    Requirement,
)

DEMO_PASSWORD = "Hal-Demo-2026!"

STAFF = [
    (Role.ADMIN, "admin", "Anita Rao"),
    (Role.MSR, "msr", "Manoj Kulkarni"),
    (Role.APPROVAL_COMMITTEE, "committee", "Dr. S. Hegde"),
    (Role.METHODS, "methods", "Ravi Shetty"),
    (Role.IMM, "imm", "Kavitha N"),
    (Role.SCHEDULING, "scheduling", "Prakash Murthy"),
    (Role.OUTSOURCING, "outsourcing", "Deepa Nair"),
    (Role.MSD, "msd", "Ganesh Iyer"),
    (Role.INTERNAL_COMMITTEE, "internal", "R. Venkatesh"),
    (Role.PNC, "pnc", "Latha Reddy"),
    (Role.INSPECTOR, "inspector", "Imran Pasha"),
    (Role.GATE, "gate", "Suresh Gowda"),
    (Role.FINISHING, "finishing", "Mahesh Bhat"),
]

VENDORS = [
    ("Alpha Precision Works", "Rohit Menon", "alpha@example.com"),
    ("Beta Aerospace Fabricators", "Sunita Das", "beta@example.com"),
    ("Gamma Machining Pvt Ltd", "Arjun Patil", "gamma@example.com"),
]


class Command(BaseCommand):
    help = "Load demo data (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off.")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("Refusing to load demo data with DEBUG off. Use --force only on a throwaway database.")
        if Requirement.objects.exists() or User.objects.filter(username="msr").exists():
            raise CommandError("Data already exists. Use a fresh database for the demo.")

        u = {}
        for role, username, name in STAFF:
            first, _, last = name.partition(" ")
            user = User(username=username, role=role, first_name=first, last_name=last, email=f"{username}@hal.example")
            user.set_password(DEMO_PASSWORD)
            user.save()
            u[username] = user
        vendors = [Vendor.objects.create(name=n, contact_person=c, email=e) for n, c, e in VENDORS]
        alpha, beta, gamma = vendors

        def requirement(title, equipment, qty, upto):
            from apps.workflow.models import Requirement as R

            req = R.objects.create(title=title, equipment_name=equipment, quantity=qty, unit="Nos",
                                   description="Manufacture per released drawing.", purpose="Tejas production line",
                                   required_by=date.today() + timedelta(days=120), raised_by=u["msr"])
            steps = ["submit", "approve", "methods", "imm", "allocate"]
            for step in steps[: steps.index(upto) + 1] if upto in steps else []:
                if step == "submit":
                    services.submit_requirement(u["msr"], req, f"IFS-{req.id:04d}", f"EF-{req.id:04d}")
                elif step == "approve":
                    services.committee_decision(u["committee"], req, True)
                elif step == "methods":
                    services.methods_advice(u["methods"], req, "CNC machining, deburr, anodise per spec.")
                elif step == "imm":
                    services.imm_assessment(u["imm"], req, "Al 7075-T6 plate 12 mm; stock available for 60%.")
                elif step == "allocate":
                    services.allocate(u["scheduling"], req, qty // 5, 0, "", qty - qty // 5, "Balance to vendors")
            return req

        def tender_to_publish(req, estimate):
            t = services.create_tender(u["outsourcing"], req, f"Manufacture {req.equipment_name} per drawing and specification.")
            services.submit_tender(u["outsourcing"], t, "EF-T")
            services.tender_approval(u["committee"], t, True)
            services.set_estimate(u["msd"], t, estimate)
            services.review_estimate(u["internal"], t, True)
            services.publish_tender(u["outsourcing"], t, f"GEM/2026/B/{req.id:06d}", f"HALEP/{req.id:05d}", date.today() + timedelta(days=14))
            return t

        def quote(t, vendor, price, qualified=True, days=45):
            q = services.add_quotation(u["outsourcing"], t, vendor, price, days, "GEM")
            services.evaluate_technical(u["outsourcing"], q, qualified, "" if qualified else "Process approval missing")
            return q

        # 1-3 requirements early in the flow
        requirement("Flap hinge bracket", "Hinge bracket HB-204", 40, "none")
        requirement("Aileron rib set", "Rib R-17 left", 24, "submit")
        requirement("Canopy frame fittings", "Fitting CF-09", 60, "imm")

        # 4 requirement allocated, waiting for a tender
        requirement("Landing gear bay doors", "Door panel LG-3", 20, "allocate")

        # 5 tender published with quotations under evaluation
        r5 = requirement("Fuel system clamps", "Clamp FC-12", 100, "allocate")
        t5 = tender_to_publish(r5, "850000")
        quote(t5, alpha, "790000")
        quote(t5, beta, "910000")
        services.start_evaluation(u["outsourcing"], t5)

        # 6 tender where every quotation is above the estimate and is with PNC
        r6 = requirement("Avionics bay rails", "Rail AB-31", 50, "allocate")
        t6 = tender_to_publish(r6, "400000")
        quote(t6, beta, "520000")
        quote(t6, gamma, "505000")
        services.start_evaluation(u["outsourcing"], t6)
        services.refer_to_pnc(u["outsourcing"], t6, "Both quotations exceed the estimate")

        # 7 active order, mid execution, healthy vendor
        r7 = requirement("Wing root fairing brackets", "Bracket WR-88", 60, "allocate")
        t7 = tender_to_publish(r7, "1200000")
        q7 = quote(t7, alpha, "1100000")
        services.start_evaluation(u["outsourcing"], t7)
        po7 = services.award(u["outsourcing"], t7, q7, date.today() + timedelta(days=40), "HAL/PO/2026/1187", "Delivery to Stores, Tejas Division")
        issue = services.issue_material(u["outsourcing"], po7, "Al 7075-T6 plate 12 mm", 410, "kg", "AMS 4045", "KA05MN4411", "RM-DISP-2291")
        alpha_user_login = services.issue_credentials(u["outsourcing"], alpha)
        alpha_user = User.objects.get(vendor=alpha)
        alpha_user.must_change_password = False
        alpha_user.set_password(DEMO_PASSWORD)
        alpha_user.save()
        services.acknowledge_material(alpha_user, issue, 410, "Received in good condition")
        services.post_progress(alpha_user, po7, 20, 35, "Rough machining complete on first batch", date.today() + timedelta(days=10))
        services.post_progress(alpha_user, po7, 30, 50, "Finish machining started", date.today() + timedelta(days=7))
        insp = services.request_pdi(alpha_user, po7, 30, "First 30 pieces ready")
        services.schedule_inspection(u["inspector"], insp, date.today() - timedelta(days=1))
        services.complete_inspection(u["inspector"], insp, 28, 2, "Dimensional check done", "2 pieces: hole position out of tolerance", "Check fixture wear")
        con = services.dispatch_consignment(alpha_user, po7, 28, "KA01AB7788", "CH-0412")
        services.gate_receipt(u["gate"], con, 28, "Seals intact")

        # 8 overdue, silent order for a second vendor
        r8 = requirement("Pylon attachment lugs", "Lug PL-5", 30, "allocate")
        t8 = tender_to_publish(r8, "600000")
        q8 = quote(t8, beta, "560000")
        services.start_evaluation(u["outsourcing"], t8)
        po8 = services.award(u["outsourcing"], t8, q8, date.today() + timedelta(days=30), "HAL/PO/2026/1090", "")
        PurchaseOrder.objects.filter(pk=po8.pk).update(delivery_date=date.today() - timedelta(days=6),
                                                       placed_at=timezone.now() - timedelta(days=21))
        services.issue_material(u["outsourcing"], po8, "Ti-6Al-4V bar 40 mm", 180, "kg", "AMS 4928", "KA09CD2201", "RM-DISP-2154")

        self.stdout.write(self.style.SUCCESS("Demo data loaded.\n"))
        self.stdout.write(f"Staff logins (password for all: {DEMO_PASSWORD})")
        for role, username, name in STAFF:
            self.stdout.write(f"  {username:<12} {role.label:<34} {name}")
        self.stdout.write("\nVendor portal login (demo vendor with an active order):")
        self.stdout.write(f"  {alpha_user.username}   password: {DEMO_PASSWORD}   ({alpha.name})")
        self.stdout.write("  Beta and Gamma have no portal login yet. Issue one from Vendors to show the flow.")
