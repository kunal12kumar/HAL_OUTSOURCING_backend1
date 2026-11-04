import os
import tempfile
import threading
import time
from decimal import Decimal
from unittest import mock

from django.core.files import File
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.vendors.models import Vendor

from . import commercial, graph, ingest, scoring, technical, tender, units
from .models import AnalysisStatus, DocStatus, TenderAnalysis, VendorDocument
from .runner import run_analysis
from .samples import build_samples

MEDIA = tempfile.mkdtemp()


class UnitParsing(TestCase):
    def test_money(self):
        self.assertEqual(units.parse_money("Rs. 5,00,000/-"), Decimal("500000"))
        self.assertEqual(units.parse_money("₹4.5 lakh"), Decimal("450000"))
        self.assertEqual(units.parse_money("INR 1.2 crore"), Decimal("12000000"))
        self.assertIsNone(units.parse_money("as applicable"))

    def test_tax_and_freight_never_assumed(self):
        self.assertEqual(units.parse_tax("GST extra at 18%"), {"mode": "excluded", "rate": Decimal("18")})
        self.assertEqual(units.parse_tax("GST inclusive")["mode"], "included")
        self.assertEqual(units.parse_tax("18% GST")["mode"], "unknown")
        self.assertEqual(units.parse_tax("As applicable")["mode"], "unknown")
        self.assertEqual(units.parse_freight("Included")["amount"], Decimal("0"))
        self.assertEqual(units.parse_freight("at actuals")["mode"], "unknown")

    def test_units_convert(self):
        self.assertEqual(units.parse_quantity("0.05 mm")["base"], Decimal("0.05"))
        self.assertEqual(units.parse_quantity("2 inch")["base"], Decimal("50.8"))
        self.assertEqual(units.parse_quantity("2 months")["base"], Decimal("60"))


def req(label, raw, key=None, cmp_="eq", mandatory=None):
    from . import facts

    q = units.parse_quantity(raw)
    return {"id": "R1", "category": "technical", "key": key or facts.canonical_key(label), "label": label, "raw": raw, "comparator": cmp_,
            "mandatory": mandatory, "quantity": tender._q(q)}


class TechnicalCompare(TestCase):
    def test_ss304_is_not_ss316(self):
        r = req("Material", "SS304")
        self.assertEqual(technical.compare(r, "SS304")[0], technical.COMPLIANT)
        self.assertEqual(technical.compare(r, "Stainless Steel 304")[0], technical.COMPLIANT)
        self.assertEqual(technical.compare(r, "SS316")[0], technical.NON)
        self.assertEqual(technical.compare(r, "SS304L")[0], technical.NON)

    def test_tolerance_tighter_is_ok_looser_fails(self):
        r = req("Tolerance", "±0.05 mm maximum", cmp_="lte")
        self.assertEqual(technical.compare(r, "±0.03 mm")[0], technical.COMPLIANT)
        self.assertEqual(technical.compare(r, "±0.08 mm")[0], technical.NON)
        self.assertEqual(technical.compare(r, "±0.002 inch")[0], technical.PARTIAL)  # 0.0508 mm: 1.6% over the limit
        self.assertEqual(technical.compare(r, "±0.05 days")[0], technical.VERIFY)

    def test_minimum_and_unit_conversion(self):
        r = req("Wall Thickness", "minimum 2.5 mm", cmp_="gte")
        self.assertEqual(technical.compare(r, "0.1 inch")[0], technical.COMPLIANT)   # 2.54 mm
        self.assertEqual(technical.compare(r, "2.4 mm")[0], technical.PARTIAL)       # within 5%
        self.assertEqual(technical.compare(r, "1.5 mm")[0], technical.NON)

    def test_negative_certification_is_non_compliant_not_missing(self):
        r = req("Certification", "AS9100")
        self.assertEqual(technical.compare(r, "ISO 9001 only. AS9100 - Not certified")[0], technical.NON)
        self.assertEqual(technical.compare(r, "AS9100 Rev D and ISO 9001")[0], technical.COMPLIANT)
        self.assertEqual(technical.compare(r, "Not available")[0], technical.NON)


class CommercialMath(TestCase):
    def vendor(self, **facts):
        rows = [{"key": k, "label": k, "value": v, "line": f"{k}: {v}", "ref": "Sheet", "page": None, "filename": "q.xlsx", "document_id": 1} for k, v in facts.items()]
        return {"vendor_id": 1, "vendor_name": "V", "documents": [{"doc_type": "COMMERCIAL_QUOTATION", "facts": rows, "filename": "q.xlsx", "document_id": 1}], "tech_chunks": []}

    def test_total_cost_with_assumptions(self):
        r = commercial.evaluate_vendor(self.vendor(quantity="200 nos", unit_price="Rs. 5,200", freight="Rs. 20,000", taxes="GST extra at 18%"), [])
        self.assertEqual(r["base_price"], 1040000.0)
        self.assertEqual(r["comparable_pre_tax_cost"], 1060000.0)
        self.assertEqual(r["taxes"]["amount"], 187200.0)
        self.assertEqual(r["comparable_total_cost"], 1247200.0)
        self.assertTrue(any("18%" in a for a in r["assumptions"]))

    def test_unresolved_tax_leaves_total_null_and_flags_it(self):
        r = commercial.evaluate_vendor(self.vendor(base_price="Rs. 5,00,000", freight="Rs. 20,000", taxes="As applicable"), [])
        self.assertIsNone(r["comparable_total_cost"])
        self.assertEqual(r["comparable_pre_tax_cost"], 520000.0)
        self.assertTrue(any("Tax" in m for m in r["missing_information"]))

    def test_missing_price_is_not_invented(self):
        r = commercial.evaluate_vendor(self.vendor(delivery_days="30 days"), [])
        self.assertIsNone(r["base_price"])
        self.assertIn("Base price", r["missing_information"])

    def test_tax_included_not_double_counted(self):
        r = commercial.evaluate_vendor(self.vendor(base_price="Rs. 8,80,000", freight="Rs. 60,000", taxes="GST inclusive"), [])
        self.assertEqual(r["comparable_total_cost"], 940000.0)


class ScoringEngine(TestCase):
    def test_weights_default_and_normalised(self):
        c = scoring.build_config()
        self.assertAlmostEqual(c["technical_weight"], 0.6)
        c = scoring.build_config({"technical_weight": 3, "commercial_weight": 1})
        self.assertAlmostEqual(c["technical_weight"], 0.75)
        with self.assertRaises(ValueError):
            scoring.build_config({"technical_weight": 0, "commercial_weight": 0})

    def test_mandatory_failure_beats_low_price(self):
        cfg = scoring.build_config()
        cheap = {"vendor_id": 1, "vendor_name": "Cheap", "technical": {"technical_score": 60, "requirements": [
            {"parameter": "Material", "mandatory": True, "status": "NON_COMPLIANT", "deviation": "wrong grade", "evidence": ""}]},
                 "commercial": {"commercial_score": 100, "requirement_checks": [], "comparable_pre_tax_cost": 1}}
        good = {"vendor_id": 2, "vendor_name": "Good", "technical": {"technical_score": 90, "requirements": [
            {"parameter": "Material", "mandatory": True, "status": "COMPLIANT", "deviation": "", "evidence": ""}]},
                "commercial": {"commercial_score": 50, "requirement_checks": [], "comparable_pre_tax_cost": 9}}
        out = scoring.score_and_rank([cheap, good], cfg)
        self.assertEqual([o["vendor_name"] for o in out], ["Good", "Cheap"])
        self.assertEqual(out[0]["rank"], 1)
        self.assertIsNone(out[1]["rank"])
        self.assertEqual(out[1]["eligibility"], "NOT_ELIGIBLE")
        self.assertEqual(out[1]["overall_score"], 76.0)  # still computed and shown, but unranked

    def test_nothing_mandatory_means_nobody_excluded(self):
        v = {"vendor_id": 1, "vendor_name": "A", "technical": {"technical_score": 10, "requirements": [
            {"parameter": "P", "mandatory": None, "status": "NON_COMPLIANT", "deviation": "x", "evidence": ""}]},
             "commercial": {"commercial_score": 10, "requirement_checks": [], "comparable_pre_tax_cost": 1}}
        self.assertEqual(scoring.score_and_rank([v], scoring.build_config())[0]["eligibility"], "ELIGIBLE")

    def test_incomplete_branch_gives_no_overall_score(self):
        v = {"vendor_id": 1, "vendor_name": "A", "technical": {"technical_score": 90, "requirements": []}, "commercial": None}
        o = scoring.score_and_rank([v], scoring.build_config())[0]
        self.assertEqual(o["eligibility"], "INCOMPLETE")
        self.assertIsNone(o["overall_score"])


@override_settings(MEDIA_ROOT=MEDIA)
class EndToEnd(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.files = build_samples.build(tempfile.mkdtemp())
        cls.admin = User.objects.create_user("admin1", password="x" * 12, role=Role.ADMIN)

    def upload(self, vendor, filename, path):
        v = Vendor.objects.filter(name=vendor).first() or Vendor.objects.create(name=vendor)
        with open(path, "rb") as fh:
            d = VendorDocument.objects.create(vendor=v, file=File(fh, name=filename), filename=filename, sha256=filename + vendor,
                                              size=os.path.getsize(path), uploaded_by=self.admin)
        ingest.process_document(d.id)
        d.refresh_from_db()
        return d

    def load_all(self):
        docs = []
        for vendor in ("Alpha Precision Works", "Beta Fabricators", "Gamma Aerotech"):
            folder = os.path.join(self.files, vendor)
            for name in sorted(os.listdir(folder)):
                docs.append(self.upload(vendor, name, os.path.join(folder, name)))
        return docs

    def new_analysis(self, **config):
        path = os.path.join(self.files, "Tender_Hydraulic_Fitting_Housing.docx")
        reqs = tender.extract_requirements(path, ".docx")
        with open(path, "rb") as fh:
            return TenderAnalysis.objects.create(title="T", tender_file=File(fh, name="t.docx"), filename="t.docx", requirements=reqs,
                                                 config=config, status=AnalysisStatus.RUNNING, created_by=self.admin)

    def test_documents_extracted_and_classified(self):
        docs = {(d.vendor.name, d.filename): d for d in self.load_all()}
        self.assertTrue(all(d.status == DocStatus.READY for d in docs.values()), [(k, d.status, d.status_detail) for k, d in docs.items() if d.status != DocStatus.READY])
        self.assertEqual(docs[("Alpha Precision Works", "Technical_Quotation.docx")].doc_type, "TECHNICAL_QUOTATION")
        self.assertEqual(docs[("Alpha Precision Works", "Commercial_Quotation.xlsx")].doc_type, "COMMERCIAL_QUOTATION")
        self.assertEqual(docs[("Alpha Precision Works", "ISO_AS9100_Certificate.pdf")].doc_type, "CERTIFICATE")
        self.assertEqual(docs[("Gamma Aerotech", "Product_Datasheet.csv")].doc_type, "DATASHEET")
        facts = {f["key"]: f["value"] for f in docs[("Beta Fabricators", "Technical_Quotation.docx")].structured["facts"]}
        self.assertEqual(facts["material"], "SS316")
        d = docs[("Gamma Aerotech", "Technical_Quotation.pdf")]
        self.assertGreater(d.chunks.count(), 0)
        self.assertEqual(d.chunks.first().vendor_id, d.vendor_id)

    def test_tender_requirements_extracted_with_mandatory_flags(self):
        reqs = tender.extract_requirements(os.path.join(self.files, "Tender_Hydraulic_Fitting_Housing.docx"), ".docx")
        by = {r["key"]: r for r in reqs["requirements"]}
        self.assertEqual(by["material"]["raw"], "SS304")
        self.assertTrue(by["material"]["mandatory"])
        self.assertTrue(by["certification"]["mandatory"])
        self.assertIsNone(by["wall_thickness"]["mandatory"])
        self.assertEqual(by["tolerance"]["comparator"], "lte")
        self.assertEqual(by["wall_thickness"]["comparator"], "gte")
        self.assertEqual(by["delivery_days"]["category"], "commercial")
        self.assertEqual(by["quantity"]["category"], "commercial")
        self.assertEqual(by["material"]["category"], "technical")
        self.assertTrue(reqs["mandatory_identified"])

    def test_full_run_ranking_and_evidence(self):
        self.load_all()
        a = self.new_analysis()
        run_analysis(a.pk)
        a.refresh_from_db()
        self.assertEqual(a.status, AnalysisStatus.COMPLETED, a.error)
        rank = {r["vendor_name"]: r for r in a.result["ranking"]}
        alpha, beta, gamma = rank["Alpha Precision Works"], rank["Beta Fabricators"], rank["Gamma Aerotech"]
        # Beta is cheapest but fails mandatory material, tolerance and AS9100
        self.assertEqual(beta["eligibility"], "NOT_ELIGIBLE")
        self.assertIsNone(beta["rank"])
        self.assertEqual(alpha["eligibility"], "ELIGIBLE")
        self.assertEqual(gamma["eligibility"], "ELIGIBLE")
        self.assertEqual((alpha["rank"], gamma["rank"]), (1, 2))
        self.assertGreater(alpha["overall_score"], gamma["overall_score"])
        self.assertTrue(any("Material" in r for r in beta["eligibility_reasons"]))
        beta_v = next(v for v in a.result["vendors"].values() if v["vendor_name"] == "Beta Fabricators")
        self.assertIn("does not meet 3 mandatory requirement(s): Material, Tolerance, Certification", beta_v["summary"])
        # score formula
        for r in (alpha, beta, gamma):
            self.assertAlmostEqual(r["overall_score"], round(r["technical_score"] * 0.6 + r["commercial_score"] * 0.4, 2), places=2)
        # commercial maths from the real files
        comm = {v["vendor_name"]: v["commercial"] for v in a.result["vendors"].values()}
        self.assertEqual(comm["Alpha Precision Works"]["comparable_total_cost"], 1247200.0)
        self.assertEqual(comm["Beta Fabricators"]["comparable_total_cost"], 940000.0)
        self.assertEqual(comm["Gamma Aerotech"]["comparable_total_cost"], 1392400.0)
        self.assertEqual(comm["Beta Fabricators"]["cost_comparison"]["difference_from_lowest"], 0.0)
        # technical detail with evidence/provenance
        tech = {v["vendor_name"]: {t["parameter"]: t for t in v["technical"]["requirements"]} for v in a.result["vendors"].values()}
        self.assertEqual(tech["Beta Fabricators"]["Material"]["status"], "NON_COMPLIANT")
        self.assertEqual(tech["Beta Fabricators"]["Certification"]["status"], "NON_COMPLIANT")
        self.assertEqual(tech["Alpha Precision Works"]["Tolerance"]["status"], "COMPLIANT")
        cert = tech["Alpha Precision Works"]["Certification"]
        self.assertEqual(cert["status"], "COMPLIANT")
        self.assertEqual(cert["source_document"], "ISO_AS9100_Certificate.pdf")
        self.assertEqual(cert["page"], 1)
        pdf = tech["Gamma Aerotech"]["Material"]
        self.assertEqual((pdf["source_document"], pdf["page"]), ("Technical_Quotation.pdf", 1))
        # explanations
        alpha_v = next(v for v in a.result["vendors"].values() if v["vendor_name"] == "Alpha Precision Works")
        self.assertIn("ranked 1 of 2", alpha_v["summary"])
        self.assertTrue(all(p["evidence"] is None or p["evidence"]["document"] for p in alpha_v["advantages"]))
        gamma_v = next(v for v in a.result["vendors"].values() if v["vendor_name"] == "Gamma Aerotech")
        self.assertIn("ranks below Alpha", gamma_v["summary"])
        self.assertIn("comparable cost", gamma_v["summary"])
        self.assertIn("misses 1 commercial requirement(s) (Delivery)", gamma_v["summary"])  # 50 days vs maximum 45

    def test_deterministic(self):
        self.load_all()
        runs = []
        for _ in range(2):
            a = self.new_analysis()
            run_analysis(a.pk)
            a.refresh_from_db()
            runs.append([(r["vendor_name"], r["overall_score"], r["rank"]) for r in a.result["ranking"]])
        self.assertEqual(runs[0], runs[1])

    def test_custom_weights_change_scores(self):
        self.load_all()
        a = self.new_analysis(technical_weight=0.5, commercial_weight=0.5)
        run_analysis(a.pk)
        a.refresh_from_db()
        r = a.result["ranking"][0]
        self.assertAlmostEqual(r["overall_score"], round(r["technical_score"] * 0.5 + r["commercial_score"] * 0.5, 2), places=2)

    def test_branches_run_in_parallel_and_merge(self):
        self.load_all()
        a = self.new_analysis()
        real_t, real_c = technical.evaluate_vendor, commercial.evaluate_vendor
        spans = {}

        def slow(name, fn):
            def wrapper(*args, **kw):
                spans.setdefault(name, [time.monotonic(), None])
                time.sleep(0.4)
                spans[name][1] = time.monotonic()
                return fn(*args, **kw)
            return wrapper

        with mock.patch.object(technical, "evaluate_vendor", slow("t", real_t)), mock.patch.object(commercial, "evaluate_vendor", slow("c", real_c)):
            run_analysis(a.pk)
        a.refresh_from_db()
        (ts, te), (cs, ce) = spans["t"], spans["c"]
        self.assertLess(max(ts, cs), min(te, ce), "technical and commercial branches did not overlap in time")
        log = {e["node"]: e for e in a.result["node_log"]}
        self.assertLessEqual(max(log["technical_evaluation"]["finished_at"], log["commercial_evaluation"]["finished_at"]),
                             log["merge_evaluations"]["finished_at"])
        self.assertEqual(a.result["status"], "COMPLETED")

    def test_branch_failure_reports_incomplete_without_fake_scores(self):
        self.load_all()
        a = self.new_analysis()
        with mock.patch.object(commercial, "evaluate_vendor", side_effect=RuntimeError("boom")):
            run_analysis(a.pk)
        a.refresh_from_db()
        self.assertEqual(a.status, AnalysisStatus.INCOMPLETE)
        for r in a.result["ranking"]:
            self.assertEqual(r["eligibility"], "INCOMPLETE")
            self.assertIsNone(r["overall_score"])
            self.assertIsNone(r["rank"])
        self.assertEqual(a.result["errors"][0]["branch"], "commercial")
        self.assertIsNotNone(next(iter(a.result["vendors"].values()))["technical"])

    def test_no_documents_fails_cleanly(self):
        a = self.new_analysis()
        run_analysis(a.pk)
        a.refresh_from_db()
        self.assertEqual(a.status, AnalysisStatus.FAILED)
        self.assertIn("No processed vendor documents", a.error)

    def test_missing_commercial_document_is_reported_not_invented(self):
        self.upload("Delta Works", "Technical_Quotation.pdf", os.path.join(self.files, "Gamma Aerotech", "Technical_Quotation.pdf"))
        a = self.new_analysis()
        run_analysis(a.pk)
        a.refresh_from_db()
        v = next(iter(a.result["vendors"].values()))
        self.assertIsNone(v["commercial"]["base_price"])
        self.assertIsNone(v["commercial"]["commercial_score"])
        self.assertIn("Base price", v["commercial"]["missing_information"])
        self.assertEqual(a.result["ranking"][0]["eligibility"], "INCOMPLETE")
        self.assertIsNone(a.result["ranking"][0]["overall_score"])

    def test_corrupt_file_marked_failed(self):
        v = Vendor.objects.create(name="Bad")
        d = VendorDocument.objects.create(vendor=v, file=File(open(os.devnull, "rb"), name="x.pdf"), filename="x.pdf", sha256="z",
                                          uploaded_by=self.admin)
        with open(d.file.path, "wb") as fh:
            fh.write(b"not a pdf at all")
        ingest.process_document(d.id)
        d.refresh_from_db()
        self.assertEqual(d.status, DocStatus.FAILED)
        self.assertIn("could not be read", d.status_detail)


@override_settings(MEDIA_ROOT=MEDIA)
class Api(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("adm", password="x" * 12, role=Role.ADMIN)
        cls.msr = User.objects.create_user("msr1", password="x" * 12, role=Role.MSR)
        cls.vendor_user = None

    def client_for(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_role_enforced(self):
        self.assertEqual(self.client_for(self.msr).get("/api/internal/evaluation/vendor-documents/").status_code, 403)
        self.assertEqual(APIClient().get("/api/internal/evaluation/vendor-documents/").status_code, 401)
        self.assertEqual(self.client_for(self.admin).get("/api/internal/evaluation/vendor-documents/").status_code, 200)

    def test_vendor_role_refused(self):
        v = Vendor.objects.create(name="V")
        vu = User.objects.create_user("vv", password="x" * 12, role=Role.VENDOR, vendor=v)
        self.assertEqual(self.client_for(vu).get("/api/internal/evaluation/vendor-documents/").status_code, 403)

    def test_upload_validation_and_duplicates(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        c = self.client_for(self.admin)
        url = "/api/internal/evaluation/vendor-documents/"
        self.assertEqual(c.post(url, {"files": SimpleUploadedFile("a.csv", b"Material: SS304\n")}, format="multipart").status_code, 400)  # no vendor
        bad = c.post(url, {"vendor_name": "Acme", "files": SimpleUploadedFile("a.exe", b"x")}, format="multipart")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("unsupported", bad.data["rejected"][0])
        ok = c.post(url, {"vendor_name": "Acme", "files": SimpleUploadedFile("Technical_Quotation.csv", b"Material,SS304\nTolerance,0.05 mm\n")}, format="multipart")
        self.assertEqual(ok.status_code, 201)
        dup = c.post(url, {"vendor_name": "acme", "files": SimpleUploadedFile("copy.csv", b"Material,SS304\nTolerance,0.05 mm\n")}, format="multipart")
        self.assertEqual(dup.status_code, 400)
        self.assertIn("identical", dup.data["rejected"][0])
        self.assertEqual(Vendor.objects.filter(name__iexact="acme").count(), 1)
