import tempfile
from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import Role, User
from apps.vendors.models import Vendor, VendorStatus

from .models import AuditLog, Consignment, POStatus, PurchaseOrder, RequirementStage, Tender, TenderStatus

PASSWORD = "Str0ng-Pass-123"


class Base(APITestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.users = {}
        for role in Role.values:
            if role == Role.VENDOR:
                continue
            u = User(username=role.lower(), role=role, first_name=role.title())
            u.set_password(PASSWORD)
            u.save()
            self.users[role] = u

    def as_(self, role):
        self.client.force_authenticate(self.users[role] if isinstance(role, str) else role)
        return self.client

    def post(self, role, url, data=None, fmt="json"):
        return self.as_(role).post(url, data or {}, format=fmt)

    def make_vendor(self, name="Alpha Engineering"):
        return Vendor.objects.create(name=name, email=f"{name.split()[0].lower()}@example.com")

    def make_tender_ready(self, qty=10, estimate="100000"):
        r = self.post(Role.MSR, "/api/internal/requirements/", {
            "title": "Wing bracket", "equipment_name": "Bracket A12", "quantity": qty, "unit": "Nos"})
        self.assertEqual(r.status_code, 201, r.data)
        rid = r.data["id"]
        base = f"/api/internal/requirements/{rid}"
        self.assertEqual(self.post(Role.MSR, f"{base}/submit/", {"ifs_ref": "IFS-1", "efile_ref": "EF-1"}).status_code, 200)
        self.assertEqual(self.post(Role.APPROVAL_COMMITTEE, f"{base}/committee-decision/", {"approve": True}).status_code, 200)
        self.assertEqual(self.post(Role.METHODS, f"{base}/methods-advice/", {"text": "CNC then anodise"}).status_code, 200)
        self.assertEqual(self.post(Role.IMM, f"{base}/imm-assessment/", {"text": "Al 7075 plate"}).status_code, 200)
        r = self.post(Role.SCHEDULING, f"{base}/allocate/", {
            "in_house_qty": 2, "other_division_qty": 0, "outsource_qty": qty - 2})
        self.assertEqual(r.status_code, 200, r.data)
        t = self.post(Role.OUTSOURCING, f"{base}/create-tender/", {"scope": "Manufacture brackets per drawing"})
        self.assertEqual(t.status_code, 201, t.data)
        tid = t.data["id"]
        tb = f"/api/internal/tenders/{tid}"
        self.assertEqual(self.post(Role.OUTSOURCING, f"{tb}/submit/", {"efile_ref": "EF-2"}).status_code, 200)
        self.assertEqual(self.post(Role.APPROVAL_COMMITTEE, f"{tb}/committee-decision/", {"approve": True}).status_code, 200)
        self.assertEqual(self.post(Role.MSD, f"{tb}/set-estimate/", {"amount": estimate}).status_code, 200)
        self.assertEqual(self.post(Role.INTERNAL_COMMITTEE, f"{tb}/review-estimate/", {"approve": True}).status_code, 200)
        r = self.post(Role.OUTSOURCING, f"{tb}/publish/", {
            "gem_ref": "GEM/1", "eportal_ref": "EP/1", "closing_date": str(date.today() + timedelta(days=10))})
        self.assertEqual(r.status_code, 200, r.data)
        return rid, tid, tb

    def quote(self, tb, vendor, price, qualified=True):
        r = self.post(Role.OUTSOURCING, f"{tb}/quotations/", {"vendor": vendor.id, "price": price, "delivery_days": 30})
        self.assertEqual(r.status_code, 200, r.data)
        qid = next(q["id"] for q in r.data["quotations"] if q["vendor"] == vendor.id)
        r = self.post(Role.OUTSOURCING, f"{tb}/evaluate-quotation/", {
            "quotation": qid, "qualified": qualified, "remarks": "" if qualified else "Lacks NADCAP"})
        self.assertEqual(r.status_code, 200, r.data)
        return qid

    def make_po(self, vendor=None, price="90000"):
        vendor = vendor or self.make_vendor()
        rid, tid, tb = self.make_tender_ready()
        qid = self.quote(tb, vendor, price)
        self.assertEqual(self.post(Role.OUTSOURCING, f"{tb}/start-evaluation/").status_code, 200)
        r = self.post(Role.OUTSOURCING, f"{tb}/award/", {"quotation": qid})
        self.assertEqual(r.status_code, 201, r.data)
        return PurchaseOrder.objects.get(pk=r.data["id"])

    def vendor_login(self, vendor, change_password=True):
        r = self.post(Role.OUTSOURCING, f"/api/internal/vendors/{vendor.id}/issue-credentials/")
        self.assertEqual(r.status_code, 200, r.data)
        self.client.force_authenticate(None)
        login = self.client.post("/api/vendor/auth/login/", {
            "username": r.data["username"], "password": r.data["temporary_password"]}, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        user = User.objects.get(username=r.data["username"])
        if change_password:
            self.client.force_authenticate(user)
            ok = self.client.post("/api/vendor/auth/change-password/", {
                "old_password": r.data["temporary_password"], "new_password": "Vendor-New-Pass-77"}, format="json")
            self.assertEqual(ok.status_code, 200, ok.data)
            user.refresh_from_db()
        return user


class EndToEndTests(Base):
    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_requirement_to_closed_order(self):
        po = self.make_po()
        vendor_user = self.vendor_login(po.vendor)
        v = f"/api/vendor/orders/{po.id}"

        self.assertEqual(self.post(Role.OUTSOURCING, f"/api/internal/orders/{po.id}/issue-material/", {
            "material": "Al 7075 plate", "quantity": "120.5", "unit": "kg", "vehicle_no": "KA01AB1234"}).status_code, 200)
        issue = po.material_issues.get()
        ack = self.post(vendor_user, f"{v}/material/{issue.id}/acknowledge/", {"quantity": "120.5"})
        self.assertEqual(ack.status_code, 200, ack.data)
        self.assertIsNotNone(po.material_issues.get().acknowledged_at)

        self.assertEqual(self.post(vendor_user, f"{v}/progress/", {"qty_completed": 4, "percent_complete": 50, "message": "Machining"}).status_code, 200)
        r = self.post(vendor_user, f"{v}/request-pdi/", {"qty_offered": 8})
        self.assertEqual(r.status_code, 200, r.data)
        pdi = po.inspections.get()

        self.assertEqual(self.post(Role.INSPECTOR, f"/api/internal/inspections/{pdi.id}/schedule/", {"date": str(date.today())}).status_code, 200)
        r = self.post(Role.INSPECTOR, f"/api/internal/inspections/{pdi.id}/complete/", {
            "qty_accepted": 8, "qty_rejected": 0, "report": "All dimensions in tolerance"})
        self.assertEqual(r.status_code, 200, r.data)

        r = self.post(vendor_user, f"{v}/dispatch/", {"qty": 8, "vehicle_no": "KA01AB1234", "challan_no": "CH-1"})
        self.assertEqual(r.status_code, 200, r.data)
        c = po.consignments.get()
        self.assertEqual(self.post(Role.GATE, f"/api/internal/consignments/{c.id}/gate-receipt/", {"qty_received": 8}).status_code, 200)
        hal = po.inspections.get(kind="HAL")
        r = self.post(Role.INSPECTOR, f"/api/internal/inspections/{hal.id}/complete/", {
            "qty_accepted": 8, "qty_rejected": 0, "report": "Accepted"})
        self.assertEqual(r.status_code, 200, r.data)
        c.refresh_from_db()
        self.assertEqual(c.status, Consignment.Status.ACCEPTED)

        self.assertEqual(self.post(Role.FINISHING, f"/api/internal/consignments/{c.id}/start-finishing/").status_code, 200)
        self.assertEqual(self.post(Role.FINISHING, f"/api/internal/consignments/{c.id}/complete-finishing/", {"remarks": "Anodised"}).status_code, 200)

        po.refresh_from_db()
        self.assertEqual(po.status, POStatus.COMPLETED)
        self.assertEqual(po.requirement.stage, RequirementStage.CLOSED)
        self.assertEqual(po.qty_outstanding, 0)
        self.assertTrue(AuditLog.objects.filter(action="po.close").exists())


class RuleTests(Base):
    def test_wrong_role_is_refused(self):
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 1})
        rid = r.data["id"]
        self.post(Role.MSR, f"/api/internal/requirements/{rid}/submit/")
        self.assertEqual(self.post(Role.MSR, f"/api/internal/requirements/{rid}/committee-decision/", {"approve": True}).status_code, 403)
        self.assertEqual(self.post(Role.MSD, f"/api/internal/requirements/{rid}/committee-decision/", {"approve": True}).status_code, 403)

    def test_admin_cannot_approve_on_behalf(self):
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 1})
        rid = r.data["id"]
        self.post(Role.MSR, f"/api/internal/requirements/{rid}/submit/")
        self.assertEqual(self.post(Role.ADMIN, f"/api/internal/requirements/{rid}/committee-decision/", {"approve": True}).status_code, 403)

    def test_return_needs_remarks_and_comes_back_to_msr(self):
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 1})
        rid = r.data["id"]
        self.post(Role.MSR, f"/api/internal/requirements/{rid}/submit/")
        url = f"/api/internal/requirements/{rid}/committee-decision/"
        self.assertEqual(self.post(Role.APPROVAL_COMMITTEE, url, {"approve": False}).status_code, 400)
        r = self.post(Role.APPROVAL_COMMITTEE, url, {"approve": False, "remarks": "Quantity unclear"})
        self.assertEqual(r.data["stage"], RequirementStage.RETURNED)
        self.assertEqual(self.post(Role.MSR, f"/api/internal/requirements/{rid}/submit/").status_code, 200)

    def test_steps_cannot_be_skipped(self):
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 5})
        rid = r.data["id"]
        self.assertEqual(self.post(Role.METHODS, f"/api/internal/requirements/{rid}/methods-advice/", {"text": "x"}).status_code, 400)
        self.assertEqual(self.post(Role.SCHEDULING, f"/api/internal/requirements/{rid}/allocate/", {
            "in_house_qty": 5, "other_division_qty": 0, "outsource_qty": 0}).status_code, 400)

    def test_allocation_must_add_up(self):
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 10})
        rid = r.data["id"]
        base = f"/api/internal/requirements/{rid}"
        self.post(Role.MSR, f"{base}/submit/")
        self.post(Role.APPROVAL_COMMITTEE, f"{base}/committee-decision/", {"approve": True})
        self.post(Role.METHODS, f"{base}/methods-advice/", {"text": "a"})
        self.post(Role.IMM, f"{base}/imm-assessment/", {"text": "b"})
        r = self.post(Role.SCHEDULING, f"{base}/allocate/", {"in_house_qty": 2, "other_division_qty": 0, "outsource_qty": 5})
        self.assertEqual(r.status_code, 400)

    def test_tender_cannot_publish_without_estimate_and_approval(self):
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 3})
        rid = r.data["id"]
        base = f"/api/internal/requirements/{rid}"
        self.post(Role.MSR, f"{base}/submit/")
        self.post(Role.APPROVAL_COMMITTEE, f"{base}/committee-decision/", {"approve": True})
        self.post(Role.METHODS, f"{base}/methods-advice/", {"text": "a"})
        self.post(Role.IMM, f"{base}/imm-assessment/", {"text": "b"})
        self.post(Role.SCHEDULING, f"{base}/allocate/", {"in_house_qty": 0, "other_division_qty": 0, "outsource_qty": 3})
        t = self.post(Role.OUTSOURCING, f"{base}/create-tender/", {"scope": "s"})
        r = self.post(Role.OUTSOURCING, f"/api/internal/tenders/{t.data['id']}/publish/", {
            "gem_ref": "g", "eportal_ref": "e", "closing_date": "2030-01-01"})
        self.assertEqual(r.status_code, 400)

    def test_publish_needs_both_portal_references(self):
        rid, tid, tb = None, None, None
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 3})
        rid = r.data["id"]
        base = f"/api/internal/requirements/{rid}"
        for role, url, data in [
            (Role.MSR, "submit", {}), (Role.APPROVAL_COMMITTEE, "committee-decision", {"approve": True}),
            (Role.METHODS, "methods-advice", {"text": "a"}), (Role.IMM, "imm-assessment", {"text": "b"}),
            (Role.SCHEDULING, "allocate", {"in_house_qty": 0, "other_division_qty": 0, "outsource_qty": 3}),
        ]:
            self.post(role, f"{base}/{url}/", data)
        tid = self.post(Role.OUTSOURCING, f"{base}/create-tender/", {"scope": "s"}).data["id"]
        tb = f"/api/internal/tenders/{tid}"
        self.post(Role.OUTSOURCING, f"{tb}/submit/")
        self.post(Role.APPROVAL_COMMITTEE, f"{tb}/committee-decision/", {"approve": True})
        self.post(Role.MSD, f"{tb}/set-estimate/", {"amount": "5000"})
        self.post(Role.INTERNAL_COMMITTEE, f"{tb}/review-estimate/", {"approve": True})
        r = self.post(Role.OUTSOURCING, f"{tb}/publish/", {"gem_ref": "g", "eportal_ref": "", "closing_date": "2030-01-01"})
        self.assertEqual(r.status_code, 400)

    def test_award_blocks_unqualified_and_over_estimate(self):
        a, b = self.make_vendor("Alpha Eng"), self.make_vendor("Beta Eng")
        rid, tid, tb = self.make_tender_ready(estimate="100000")
        q_bad = self.quote(tb, a, "80000", qualified=False)
        q_high = self.quote(tb, b, "150000", qualified=True)
        self.post(Role.OUTSOURCING, f"{tb}/start-evaluation/")
        self.assertEqual(self.post(Role.OUTSOURCING, f"{tb}/award/", {"quotation": q_bad}).status_code, 400)
        self.assertEqual(self.post(Role.OUTSOURCING, f"{tb}/award/", {"quotation": q_high}).status_code, 400)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_cannot_refer_to_pnc_when_acceptable_quote_exists(self):
        a = self.make_vendor("Alpha Eng")
        rid, tid, tb = self.make_tender_ready(estimate="100000")
        self.quote(tb, a, "90000")
        self.post(Role.OUTSOURCING, f"{tb}/start-evaluation/")
        self.assertEqual(self.post(Role.OUTSOURCING, f"{tb}/refer-to-pnc/").status_code, 400)

    def test_pnc_loop_opens_a_linked_new_round(self):
        a = self.make_vendor("Alpha Eng")
        rid, tid, tb = self.make_tender_ready(estimate="100000")
        self.quote(tb, a, "150000")
        self.post(Role.OUTSOURCING, f"{tb}/start-evaluation/")
        self.assertEqual(self.post(Role.OUTSOURCING, f"{tb}/refer-to-pnc/", {"remarks": "All above estimate"}).status_code, 200)
        r = self.post(Role.PNC, f"{tb}/pnc-decision/", {"revised_estimate": "160000", "remarks": "Market rate"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["round"], 2)
        self.assertEqual(r.data["status"], TenderStatus.READY_TO_PUBLISH)
        self.assertEqual(r.data["parent_number"], Tender.objects.get(pk=tid).number)
        self.assertEqual(Tender.objects.get(pk=tid).status, TenderStatus.RETENDERED)
        self.assertEqual(len(r.data["rounds"]), 2)

    def test_pnc_cannot_lower_the_estimate(self):
        a = self.make_vendor("Alpha Eng")
        rid, tid, tb = self.make_tender_ready(estimate="100000")
        self.quote(tb, a, "150000")
        self.post(Role.OUTSOURCING, f"{tb}/start-evaluation/")
        self.post(Role.OUTSOURCING, f"{tb}/refer-to-pnc/")
        self.assertEqual(self.post(Role.PNC, f"{tb}/pnc-decision/", {"revised_estimate": "90000"}).status_code, 400)

    def test_dispatch_limited_to_pdi_accepted_quantity(self):
        po = self.make_po()
        vendor_user = self.vendor_login(po.vendor)
        v = f"/api/vendor/orders/{po.id}"
        self.assertEqual(self.post(vendor_user, f"{v}/dispatch/", {"qty": 1, "vehicle_no": "K1", "challan_no": "C1"}).status_code, 400)
        self.post(vendor_user, f"{v}/request-pdi/", {"qty_offered": 8})
        pdi = po.inspections.get()
        self.post(Role.INSPECTOR, f"/api/internal/inspections/{pdi.id}/complete/", {
            "qty_accepted": 5, "qty_rejected": 3, "report": "ok", "deficiencies": "Burrs on 3 parts"})
        self.assertEqual(self.post(vendor_user, f"{v}/dispatch/", {"qty": 6, "vehicle_no": "K1", "challan_no": "C1"}).status_code, 400)
        self.assertEqual(self.post(vendor_user, f"{v}/dispatch/", {"qty": 5, "vehicle_no": "K1", "challan_no": "C1"}).status_code, 200)

    def test_rejected_inspection_needs_deficiencies(self):
        po = self.make_po()
        vendor_user = self.vendor_login(po.vendor)
        self.post(vendor_user, f"/api/vendor/orders/{po.id}/request-pdi/", {"qty_offered": 4})
        pdi = po.inspections.get()
        r = self.post(Role.INSPECTOR, f"/api/internal/inspections/{pdi.id}/complete/", {"qty_accepted": 2, "qty_rejected": 2, "report": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.post(Role.INSPECTOR, f"/api/internal/inspections/{pdi.id}/complete/", {"qty_accepted": 1, "qty_rejected": 2, "report": "x", "deficiencies": "d"})
        self.assertEqual(r.status_code, 400)

    def test_gate_shortage_is_reported(self):
        po = self.make_po()
        vendor_user = self.vendor_login(po.vendor)
        v = f"/api/vendor/orders/{po.id}"
        self.post(vendor_user, f"{v}/request-pdi/", {"qty_offered": 8})
        self.post(Role.INSPECTOR, f"/api/internal/inspections/{po.inspections.get().id}/complete/", {"qty_accepted": 8, "qty_rejected": 0, "report": "ok"})
        self.post(vendor_user, f"{v}/dispatch/", {"qty": 8, "vehicle_no": "K1", "challan_no": "C1"})
        c = po.consignments.get()
        r = self.post(Role.GATE, f"/api/internal/consignments/{c.id}/gate-receipt/", {"qty_received": 6})
        self.assertEqual(r.data["shortage"], 2)
        po = PurchaseOrder.objects.get(pk=po.pk)
        self.assertEqual(po.qty_dispatchable, 2)

    def test_audit_log_is_immutable(self):
        self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 1})
        entry = AuditLog.objects.first()
        entry.summary = "tampered"
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()


class VendorAccessTests(Base):
    def test_credentials_need_an_awarded_order(self):
        v = self.make_vendor()
        r = self.post(Role.OUTSOURCING, f"/api/internal/vendors/{v.id}/issue-credentials/")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.filter(vendor=v).exists())

    def test_only_outsourcing_or_admin_can_issue_credentials(self):
        po = self.make_po()
        self.assertEqual(self.post(Role.MSD, f"/api/internal/vendors/{po.vendor.id}/issue-credentials/").status_code, 403)
        self.assertEqual(self.post(Role.ADMIN, f"/api/internal/vendors/{po.vendor.id}/issue-credentials/").status_code, 200)

    def test_vendor_must_change_temporary_password(self):
        po = self.make_po()
        user = self.vendor_login(po.vendor, change_password=False)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get("/api/vendor/orders/").status_code, 403)
        self.assertEqual(self.client.get("/api/vendor/auth/me/").status_code, 200)

    def test_login_endpoints_are_separate(self):
        po = self.make_po()
        r = self.post(Role.OUTSOURCING, f"/api/internal/vendors/{po.vendor.id}/issue-credentials/")
        self.client.force_authenticate(None)
        vendor_creds = {"username": r.data["username"], "password": r.data["temporary_password"]}
        self.assertEqual(self.client.post("/api/internal/auth/login/", vendor_creds, format="json").status_code, 401)
        self.assertEqual(self.client.post("/api/vendor/auth/login/", {"username": "msr", "password": PASSWORD}, format="json").status_code, 401)
        self.assertEqual(self.client.post("/api/vendor/auth/login/", vendor_creds, format="json").status_code, 200)
        self.assertEqual(self.client.post("/api/internal/auth/login/", {"username": "msr", "password": PASSWORD}, format="json").status_code, 200)

    def test_vendor_token_cannot_reach_internal_api_and_vice_versa(self):
        po = self.make_po()
        vendor_user = self.vendor_login(po.vendor)
        c = self.as_(vendor_user)
        for url in ["/api/internal/requirements/", "/api/internal/tenders/", "/api/internal/vendors/", "/api/internal/orders/",
                    "/api/internal/audit/", "/api/internal/dashboard/", "/api/internal/queue/", "/api/internal/users/"]:
            self.assertEqual(c.get(url).status_code, 403, url)
        c = self.as_(Role.OUTSOURCING)
        for url in ["/api/vendor/orders/", "/api/vendor/dashboard/"]:
            self.assertEqual(c.get(url).status_code, 403, url)

    def test_vendor_sees_only_own_orders(self):
        a, b = self.make_vendor("Alpha Eng"), self.make_vendor("Beta Eng")
        po_a = self.make_po(vendor=a)
        po_b = self.make_po(vendor=b)
        user_a = self.vendor_login(a)
        c = self.as_(user_a)
        listing = c.get("/api/vendor/orders/").data["results"]
        self.assertEqual([o["number"] for o in listing], [po_a.number])
        self.assertEqual(c.get(f"/api/vendor/orders/{po_a.id}/").status_code, 200)
        self.assertEqual(c.get(f"/api/vendor/orders/{po_b.id}/").status_code, 404)
        for action in ("progress", "request-pdi", "dispatch"):
            self.assertEqual(c.post(f"/api/vendor/orders/{po_b.id}/{action}/", {"qty_completed": 1, "percent_complete": 1, "qty_offered": 1, "qty": 1, "vehicle_no": "x", "challan_no": "y"}, format="json").status_code, 404)

    def test_vendor_payload_never_contains_internal_fields(self):
        a, b = self.make_vendor("Alpha Eng"), self.make_vendor("Beta Eng")
        rid, tid, tb = self.make_tender_ready(estimate="100000")
        qa = self.quote(tb, a, "90000")
        self.quote(tb, b, "95000")
        self.post(Role.OUTSOURCING, f"{tb}/start-evaluation/")
        po = PurchaseOrder.objects.get(pk=self.post(Role.OUTSOURCING, f"{tb}/award/", {"quotation": qa}).data["id"])
        user = self.vendor_login(a)
        v = f"/api/vendor/orders/{po.id}"
        self.post(user, f"{v}/request-pdi/", {"qty_offered": 3})
        self.post(Role.INSPECTOR, f"/api/internal/inspections/{po.inspections.get().id}/complete/", {
            "qty_accepted": 3, "qty_rejected": 0, "report": "internal report", "internal_notes": "SECRET-INSPECTOR-NOTE"})
        body = self.as_(user).get(f"{v}/").content.decode()
        for forbidden in ("estimated_price", "100000", "95000", "Beta Eng", "SECRET-INSPECTOR-NOTE", "internal report",
                          "quotation", "trail", "internal_notes", "gate_remarks", "efile", "gem_ref"):
            self.assertNotIn(forbidden, body, forbidden)
        dash = self.as_(user).get("/api/vendor/dashboard/").content.decode()
        self.assertNotIn("Beta Eng", dash)
        self.assertNotIn("100000", dash)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_document_visibility_and_download_isolation(self):
        a, b = self.make_vendor("Alpha Eng"), self.make_vendor("Beta Eng")
        po_a, po_b = self.make_po(vendor=a), self.make_po(vendor=b)
        up = lambda po, shared: self.as_(Role.OUTSOURCING).post(
            f"/api/internal/orders/{po.id}/documents/",
            {"title": "Drawing", "doc_type": "DRAWING", "file": SimpleUploadedFile("d.pdf", b"%PDF-x"), "visible_to_vendor": shared},
            format="multipart")
        shared = up(po_a, True)
        hidden = up(po_a, False)
        other = up(po_b, True)
        self.assertEqual(shared.status_code, 201, shared.data)
        user_a = self.vendor_login(a)
        c = self.as_(user_a)
        docs = c.get(f"/api/vendor/orders/{po_a.id}/").data["documents"]
        self.assertEqual([d["id"] for d in docs], [shared.data["id"]])
        self.assertEqual(c.get(f"/api/vendor/documents/{shared.data['id']}/download/").status_code, 200)
        self.assertEqual(c.get(f"/api/vendor/documents/{hidden.data['id']}/download/").status_code, 404)
        self.assertEqual(c.get(f"/api/vendor/documents/{other.data['id']}/download/").status_code, 404)

    def test_disallowed_upload_type_is_refused(self):
        po = self.make_po()
        r = self.as_(Role.OUTSOURCING).post(
            f"/api/internal/orders/{po.id}/documents/",
            {"title": "x", "file": SimpleUploadedFile("run.exe", b"MZ")}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_suspended_vendor_is_locked_out(self):
        po = self.make_po()
        user = self.vendor_login(po.vendor)
        self.assertEqual(self.as_(user).get("/api/vendor/orders/").status_code, 200)
        self.assertEqual(self.post(Role.OUTSOURCING, f"/api/internal/vendors/{po.vendor.id}/suspend/").status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(self.as_(user).get("/api/vendor/orders/").status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post("/api/vendor/auth/login/", {"username": user.username, "password": "Vendor-New-Pass-77"}, format="json").status_code, 401)
        po.vendor.refresh_from_db()
        self.assertEqual(po.vendor.status, VendorStatus.SUSPENDED)

    def test_vendor_cannot_overstate_progress(self):
        po = self.make_po()
        user = self.vendor_login(po.vendor)
        r = self.post(user, f"/api/vendor/orders/{po.id}/progress/", {"qty_completed": 999, "percent_complete": 10})
        self.assertEqual(r.status_code, 400)


class DashboardTests(Base):
    def test_overdue_and_silent_vendors_are_flagged(self):
        po = self.make_po()
        PurchaseOrder.objects.filter(pk=po.pk).update(delivery_date=date.today() - timedelta(days=2))
        from django.utils import timezone

        PurchaseOrder.objects.filter(pk=po.pk).update(placed_at=timezone.now() - timedelta(days=10))
        data = self.as_(Role.OUTSOURCING).get("/api/internal/dashboard/").data
        self.assertEqual(len(data["overdue_orders"]), 1)
        self.assertEqual(len(data["silent_orders"]), 1)

    def test_work_queue_matches_role(self):
        r = self.post(Role.MSR, "/api/internal/requirements/", {"title": "x", "equipment_name": "y", "quantity": 1})
        self.post(Role.MSR, f"/api/internal/requirements/{r.data['id']}/submit/")
        self.assertEqual(self.as_(Role.APPROVAL_COMMITTEE).get("/api/internal/queue/").data["count"], 1)
        self.assertEqual(self.as_(Role.METHODS).get("/api/internal/queue/").data["count"], 0)

    def test_csv_export_neutralises_formula_cells(self):
        self.post(Role.MSR, "/api/internal/requirements/", {"title": "=HYPERLINK(\"http://x\")", "equipment_name": "y", "quantity": 1})
        body = self.as_(Role.OUTSOURCING).get("/api/internal/export/requirements/").content.decode()
        self.assertIn("'=HYPERLINK", body)

    def test_only_admin_manages_users(self):
        payload = {"username": "newmsr", "role": "MSR", "password": "Another-Str0ng-1"}
        self.assertEqual(self.post(Role.MSR, "/api/internal/users/", payload).status_code, 403)
        self.assertEqual(self.post(Role.ADMIN, "/api/internal/users/", payload).status_code, 201)
        self.assertEqual(self.post(Role.ADMIN, "/api/internal/users/", {**payload, "username": "v", "role": "VENDOR"}).status_code, 400)


class HealthTests(Base):
    def test_health_endpoint_needs_no_login(self):
        self.client.force_authenticate(None)
        r = self.client.get("/api/health/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})


class StaffPasswordTests(Base):
    def test_new_staff_must_change_temporary_password_first(self):
        self.post(Role.ADMIN, "/api/internal/users/", {"username": "newmsr", "role": "MSR", "password": "Another-Str0ng-1"})
        user = User.objects.get(username="newmsr")
        self.assertTrue(user.must_change_password)
        c = self.as_(user)
        self.assertEqual(c.get("/api/internal/requirements/").status_code, 403)
        self.assertEqual(c.get("/api/internal/queue/").status_code, 403)
        self.assertEqual(c.get("/api/internal/auth/me/").status_code, 200)
        ok = c.post("/api/internal/auth/change-password/", {"old_password": "Another-Str0ng-1", "new_password": "My-Own-Pass-2026"}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)
        user.refresh_from_db()
        self.assertEqual(self.as_(user).get("/api/internal/requirements/").status_code, 200)


class LoginThrottleTests(Base):
    def test_repeated_bad_logins_are_throttled(self):
        from unittest import mock

        from rest_framework.throttling import ScopedRateThrottle

        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "3/min"}):
            codes = [
                self.client.post("/api/internal/auth/login/", {"username": "msr", "password": "wrong"}, format="json").status_code
                for _ in range(5)
            ]
        self.assertEqual(codes[:3], [401, 401, 401])
        self.assertEqual(codes[3:], [429, 429])
