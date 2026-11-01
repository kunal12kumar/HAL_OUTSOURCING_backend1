"""Business rules for the HAL outsourcing workflow.

Every state change goes through a function here. Each one checks the actor's role,
checks the current state, writes the change and records an audit entry, so the API
layer cannot skip a rule.
"""
import secrets
import string
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import Role
from apps.vendors.models import VendorStatus

from .models import (
    Allocation,
    AuditLog,
    Consignment,
    Inspection,
    MaterialIssue,
    POStatus,
    ProgressUpdate,
    PurchaseOrder,
    Quotation,
    RequirementStage,
    StageRecord,
    Tender,
    TenderStatus,
)

User = get_user_model()
D = StageRecord.Decision


def fail(message):
    raise ValidationError({"detail": message})


def require_role(user, *roles):
    if user.role not in roles:
        raise PermissionDenied("Your role is not allowed to perform this action.")


def require_state(current, allowed, what):
    if current not in allowed:
        fail(f"This {what} is not in a state that allows this action (currently: {current}).")


def audit(user, action, obj, summary):
    AuditLog.objects.create(
        actor=user,
        actor_name=user.get_full_name() or user.username,
        actor_role=user.role,
        action=action,
        object_type=obj.__class__.__name__,
        object_id=str(getattr(obj, "number", None) or getattr(obj, "code", None) or obj.pk),
        summary=summary[:300],
    )


def record(user, stage, decision, remarks="", requirement=None, tender=None):
    StageRecord.objects.create(
        requirement=requirement,
        tender=tender,
        stage=stage,
        decision=decision,
        remarks=remarks,
        actor=user,
        actor_name=user.get_full_name() or user.username,
        actor_role=user.role,
    )


def _require_remarks(remarks, what="rejection"):
    if not (remarks or "").strip():
        fail(f"Remarks are required for a {what}.")


# ---------------------------------------------------------------- requirements

@transaction.atomic
def submit_requirement(user, req, ifs_ref="", efile_ref=""):
    require_role(user, Role.MSR)
    require_state(req.stage, (RequirementStage.DRAFT, RequirementStage.RETURNED), "requirement")
    if ifs_ref:
        req.ifs_ref = ifs_ref
    if efile_ref:
        req.efile_ref = efile_ref
    req.stage = RequirementStage.SUBMITTED
    req.save()
    record(user, "REQUIREMENT_SUBMITTED", D.COMPLETED, "Submitted via IFS / e-File", requirement=req)
    audit(user, "requirement.submit", req, f"Submitted requirement {req.number}")


@transaction.atomic
def committee_decision(user, req, approve, remarks=""):
    require_role(user, Role.APPROVAL_COMMITTEE)
    require_state(req.stage, (RequirementStage.SUBMITTED,), "requirement")
    if approve:
        req.stage = RequirementStage.APPROVED
    else:
        _require_remarks(remarks, "return")
        req.stage = RequirementStage.RETURNED
    req.save()
    record(user, "COMMITTEE_REQUIREMENT", D.APPROVED if approve else D.REJECTED, remarks, requirement=req)
    audit(user, "requirement.committee", req, f"{'Approved' if approve else 'Returned'} {req.number}")


@transaction.atomic
def methods_advice(user, req, advice):
    require_role(user, Role.METHODS)
    require_state(req.stage, (RequirementStage.APPROVED,), "requirement")
    if not (advice or "").strip():
        fail("Manufacturing and process advice is required.")
    req.methods_advice = advice
    req.stage = RequirementStage.METHODS_DONE
    req.save()
    record(user, "METHODS_ADVICE", D.COMPLETED, advice, requirement=req)
    audit(user, "requirement.methods", req, f"Methods advice recorded for {req.number}")


@transaction.atomic
def imm_assessment(user, req, materials):
    require_role(user, Role.IMM)
    require_state(req.stage, (RequirementStage.METHODS_DONE,), "requirement")
    if not (materials or "").strip():
        fail("Material and procurement requirements are required.")
    req.material_requirements = materials
    req.stage = RequirementStage.IMM_DONE
    req.save()
    record(user, "IMM_ASSESSMENT", D.COMPLETED, materials, requirement=req)
    audit(user, "requirement.imm", req, f"IMM assessment recorded for {req.number}")


@transaction.atomic
def allocate(user, req, in_house, other_division, other_division_name, outsource, remarks=""):
    require_role(user, Role.SCHEDULING)
    require_state(req.stage, (RequirementStage.IMM_DONE,), "requirement")
    total = in_house + other_division + outsource
    if total != req.quantity:
        fail(f"The split adds up to {total} but the requirement is for {req.quantity}.")
    if other_division and not other_division_name.strip():
        fail("Name the other HAL division that will supply its share.")
    Allocation.objects.update_or_create(
        requirement=req,
        defaults=dict(
            in_house_qty=in_house,
            other_division_qty=other_division,
            other_division_name=other_division_name,
            outsource_qty=outsource,
            remarks=remarks,
            set_by=user,
        ),
    )
    req.stage = RequirementStage.ALLOCATED if outsource > 0 else RequirementStage.INTERNAL_ONLY
    req.save()
    record(
        user,
        "SCHEDULING_ALLOCATION",
        D.COMPLETED,
        f"HAL {in_house}, other divisions {other_division}, vendors {outsource}. {remarks}".strip(),
        requirement=req,
    )
    audit(user, "requirement.allocate", req, f"Allocated {req.number}: {outsource} to vendors")


# --------------------------------------------------------------------- tenders

@transaction.atomic
def create_tender(user, req, scope):
    require_role(user, Role.OUTSOURCING)
    require_state(req.stage, (RequirementStage.ALLOCATED,), "requirement")
    if not (scope or "").strip():
        fail("Tender scope is required.")
    tender = Tender.objects.create(
        requirement=req,
        scope=scope,
        quantity=req.allocation.outsource_qty,
        created_by=user,
    )
    req.stage = RequirementStage.TENDERING
    req.save()
    record(user, "TENDER_CREATED", D.COMPLETED, scope, requirement=req, tender=tender)
    audit(user, "tender.create", tender, f"Created tender {tender.number} for {req.number}")
    return tender


@transaction.atomic
def submit_tender(user, tender, efile_ref=""):
    require_role(user, Role.OUTSOURCING)
    require_state(tender.status, (TenderStatus.DRAFT,), "tender")
    if efile_ref:
        tender.efile_ref = efile_ref
    tender.status = TenderStatus.PENDING_APPROVAL
    tender.save()
    record(user, "TENDER_SUBMITTED", D.COMPLETED, "Submitted for committee approval via e-File", tender=tender)
    audit(user, "tender.submit", tender, f"Submitted {tender.number} for approval")


@transaction.atomic
def tender_approval(user, tender, approve, remarks=""):
    require_role(user, Role.APPROVAL_COMMITTEE)
    require_state(tender.status, (TenderStatus.PENDING_APPROVAL,), "tender")
    if approve:
        tender.status = TenderStatus.APPROVED
    else:
        _require_remarks(remarks, "return")
        tender.status = TenderStatus.DRAFT
    tender.save()
    record(user, "COMMITTEE_TENDER", D.APPROVED if approve else D.REJECTED, remarks, tender=tender)
    audit(user, "tender.committee", tender, f"{'Approved' if approve else 'Returned'} {tender.number}")


@transaction.atomic
def set_estimate(user, tender, amount, remarks=""):
    require_role(user, Role.MSD)
    require_state(tender.status, (TenderStatus.APPROVED,), "tender")
    amount = Decimal(amount)
    if amount <= 0:
        fail("The estimate must be greater than zero.")
    tender.estimated_price = amount
    tender.status = TenderStatus.ESTIMATED
    tender.save()
    record(user, "MSD_ESTIMATE", D.COMPLETED, f"Estimate {amount}. {remarks}".strip(), tender=tender)
    audit(user, "tender.estimate", tender, f"MSD estimate {amount} for {tender.number}")


@transaction.atomic
def review_estimate(user, tender, approve, remarks=""):
    require_role(user, Role.INTERNAL_COMMITTEE)
    require_state(tender.status, (TenderStatus.ESTIMATED,), "tender")
    if approve:
        tender.status = TenderStatus.READY_TO_PUBLISH
    else:
        _require_remarks(remarks, "return")
        tender.status = TenderStatus.APPROVED
    tender.save()
    record(user, "INTERNAL_COMMITTEE_ESTIMATE", D.APPROVED if approve else D.REJECTED, remarks, tender=tender)
    audit(user, "tender.estimate_review", tender, f"{'Approved' if approve else 'Returned'} estimate for {tender.number}")


@transaction.atomic
def publish_tender(user, tender, gem_ref, eportal_ref, closing_date):
    require_role(user, Role.OUTSOURCING)
    require_state(tender.status, (TenderStatus.READY_TO_PUBLISH,), "tender")
    if not gem_ref or not eportal_ref:
        fail("Both the GeM reference and the HAL e-Portal reference are required to publish.")
    if not closing_date:
        fail("A closing date is required.")
    tender.gem_ref = gem_ref
    tender.eportal_ref = eportal_ref
    tender.closing_date = closing_date
    tender.published_at = timezone.now()
    tender.status = TenderStatus.PUBLISHED
    tender.save()
    record(user, "TENDER_PUBLISHED", D.COMPLETED, f"GeM {gem_ref}; e-Portal {eportal_ref}", tender=tender)
    audit(user, "tender.publish", tender, f"Published {tender.number}")


@transaction.atomic
def add_quotation(user, tender, vendor, price, delivery_days, source, remarks=""):
    require_role(user, Role.OUTSOURCING)
    require_state(tender.status, (TenderStatus.PUBLISHED, TenderStatus.EVALUATION), "tender")
    if Quotation.objects.filter(tender=tender, vendor=vendor).exists():
        fail("This vendor already has a quotation on this tender.")
    if vendor.status == VendorStatus.SUSPENDED:
        fail("This vendor is suspended.")
    q = Quotation.objects.create(
        tender=tender,
        vendor=vendor,
        price=Decimal(price),
        delivery_days=delivery_days,
        source=source,
        remarks=remarks,
        created_by=user,
    )
    audit(user, "quotation.add", tender, f"Quotation from {vendor.name} for {tender.number}")
    return q


@transaction.atomic
def evaluate_technical(user, quotation, qualified, remarks=""):
    require_role(user, Role.OUTSOURCING)
    require_state(quotation.tender.status, (TenderStatus.PUBLISHED, TenderStatus.EVALUATION), "tender")
    if not qualified:
        _require_remarks(remarks, "technical disqualification")
    quotation.technically_qualified = bool(qualified)
    quotation.technical_remarks = remarks
    quotation.save()
    audit(
        user,
        "quotation.technical",
        quotation.tender,
        f"{quotation.vendor.name}: {'qualified' if qualified else 'not qualified'} on {quotation.tender.number}",
    )


@transaction.atomic
def start_evaluation(user, tender):
    require_role(user, Role.OUTSOURCING)
    require_state(tender.status, (TenderStatus.PUBLISHED,), "tender")
    if not tender.quotations.exists():
        fail("There are no quotations to evaluate.")
    tender.status = TenderStatus.EVALUATION
    tender.save()
    audit(user, "tender.evaluation", tender, f"Evaluation started for {tender.number}")


@transaction.atomic
def award(user, tender, quotation, delivery_date=None, po_ref="", terms=""):
    require_role(user, Role.OUTSOURCING)
    require_state(tender.status, (TenderStatus.EVALUATION,), "tender")
    if quotation.tender_id != tender.id:
        fail("That quotation does not belong to this tender.")
    if not quotation.technically_qualified:
        fail("The vendor is not technically qualified. Price alone cannot justify an award.")
    if not quotation.within_estimate:
        fail("The quotation is above the approved estimate. Refer the tender to the PNC Committee.")
    if quotation.vendor.status == VendorStatus.SUSPENDED:
        fail("This vendor is suspended.")
    if delivery_date is None:
        delivery_date = timezone.localdate() + timedelta(days=quotation.delivery_days or 30)
    po = PurchaseOrder.objects.create(
        po_ref=po_ref,
        tender=tender,
        quotation=quotation,
        requirement=tender.requirement,
        vendor=quotation.vendor,
        quantity=tender.quantity,
        value=quotation.price,
        delivery_date=delivery_date,
        terms=terms,
        placed_by=user,
    )
    tender.status = TenderStatus.AWARDED
    tender.save()
    req = tender.requirement
    req.stage = RequirementStage.IN_EXECUTION
    req.save()
    record(user, "VENDOR_SELECTED", D.COMPLETED, f"{quotation.vendor.name} at {quotation.price}; {po.number}", tender=tender, requirement=req)
    audit(user, "tender.award", tender, f"Awarded {tender.number} to {quotation.vendor.name} as {po.number}")
    return po


@transaction.atomic
def refer_to_pnc(user, tender, remarks=""):
    require_role(user, Role.OUTSOURCING)
    require_state(tender.status, (TenderStatus.EVALUATION,), "tender")
    if any(q.eligible for q in tender.quotations.select_related("tender")):
        fail("An eligible quotation exists at or below the estimate. Award it instead of referring to PNC.")
    tender.status = TenderStatus.PNC_REVIEW
    tender.save()
    record(user, "REFERRED_TO_PNC", D.COMPLETED, remarks or "No acceptable quotation at or below the estimate", tender=tender)
    audit(user, "tender.pnc_refer", tender, f"{tender.number} referred to PNC")


@transaction.atomic
def pnc_decision(user, tender, revised_estimate, remarks=""):
    """Record the PNC outcome and open the next tender round.

    revised_estimate=None means retender at the existing estimate.
    """
    require_role(user, Role.PNC)
    require_state(tender.status, (TenderStatus.PNC_REVIEW,), "tender")
    estimate = tender.estimated_price
    if revised_estimate is not None:
        revised_estimate = Decimal(revised_estimate)
        if revised_estimate <= estimate:
            fail("A revised estimate must be higher than the current estimate.")
        estimate = revised_estimate
        what = f"Estimate revised to {estimate}"
    else:
        what = "Retender at the existing estimate"
    tender.status = TenderStatus.RETENDERED
    tender.save()
    new = Tender.objects.create(
        requirement=tender.requirement,
        round=tender.round + 1,
        parent=tender,
        scope=tender.scope,
        quantity=tender.quantity,
        estimated_price=estimate,
        status=TenderStatus.READY_TO_PUBLISH,
        efile_ref=tender.efile_ref,
        created_by=user,
    )
    record(user, "PNC_DECISION", D.COMPLETED, f"{what}. {remarks}".strip(), tender=tender)
    record(user, "RETENDER_OPENED", D.COMPLETED, f"Round {new.round} opened from {tender.number}", tender=new)
    audit(user, "tender.pnc_decision", tender, f"PNC: {what}; new round {new.number}")
    return new


# ------------------------------------------------------------ order execution

def _assert_vendor_owns(user, po):
    if not user.is_vendor or po.vendor_id != user.vendor_id:
        raise PermissionDenied("This order does not belong to your company.")


def _require_active_po(po):
    require_state(po.status, (POStatus.ACTIVE,), "order")


@transaction.atomic
def issue_material(user, po, material, quantity, unit, specification="", vehicle_no="", dispatch_doc_no="", remarks=""):
    require_role(user, Role.OUTSOURCING)
    _require_active_po(po)
    quantity = Decimal(quantity)
    if quantity <= 0:
        fail("Quantity must be greater than zero.")
    issue = MaterialIssue.objects.create(
        po=po,
        material=material,
        specification=specification,
        quantity=quantity,
        unit=unit,
        vehicle_no=vehicle_no,
        dispatch_doc_no=dispatch_doc_no,
        remarks=remarks,
        issued_by=user,
    )
    audit(user, "material.issue", po, f"Issued {quantity} {unit} {material} on {po.number}")
    return issue


@transaction.atomic
def revise_delivery_date(user, po, new_date, remarks=""):
    require_role(user, Role.OUTSOURCING)
    _require_active_po(po)
    _require_remarks(remarks, "delivery date change")
    old = po.delivery_date
    po.delivery_date = new_date
    po.save()
    audit(user, "po.delivery_date", po, f"{po.number} delivery {old} -> {new_date}: {remarks}")


@transaction.atomic
def acknowledge_material(user, issue, quantity, remarks=""):
    _assert_vendor_owns(user, issue.po)
    if issue.acknowledged_at:
        fail("This material issue is already acknowledged.")
    quantity = Decimal(quantity)
    if quantity < 0:
        fail("Quantity cannot be negative.")
    issue.acknowledged_at = timezone.now()
    issue.acknowledged_qty = quantity
    issue.acknowledged_by = user
    issue.ack_remarks = remarks
    issue.save()
    audit(user, "material.ack", issue.po, f"Vendor acknowledged {quantity} {issue.unit} {issue.material}")


@transaction.atomic
def post_progress(user, po, qty_completed, percent_complete, message="", expected_ready_date=None):
    _assert_vendor_owns(user, po)
    _require_active_po(po)
    if qty_completed > po.quantity:
        fail(f"Quantity completed cannot exceed the ordered quantity ({po.quantity}).")
    if not 0 <= percent_complete <= 100:
        fail("Percent complete must be between 0 and 100.")
    update = ProgressUpdate.objects.create(
        po=po,
        posted_by=user,
        qty_completed=qty_completed,
        percent_complete=percent_complete,
        message=message,
        expected_ready_date=expected_ready_date,
    )
    audit(user, "progress.post", po, f"Progress {percent_complete}% / {qty_completed} on {po.number}")
    return update


@transaction.atomic
def request_pdi(user, po, qty_offered, note=""):
    _assert_vendor_owns(user, po)
    _require_active_po(po)
    if qty_offered < 1:
        fail("Offer at least one item for inspection.")
    open_offered = sum(
        i.qty_offered
        for i in po.inspections.filter(kind=Inspection.Kind.PDI).exclude(status=Inspection.Status.COMPLETED)
    )
    room = po.quantity - po.qty_pdi_accepted - open_offered
    if qty_offered > room:
        fail(f"Only {max(room, 0)} more can be offered; the rest is already accepted or awaiting inspection.")
    insp = Inspection.objects.create(
        po=po, kind=Inspection.Kind.PDI, qty_offered=qty_offered, requested_by=user, request_note=note
    )
    audit(user, "pdi.request", po, f"PDI requested for {qty_offered} on {po.number}")
    return insp


@transaction.atomic
def schedule_inspection(user, insp, date):
    require_role(user, Role.INSPECTOR)
    require_state(insp.status, (Inspection.Status.PENDING, Inspection.Status.SCHEDULED), "inspection")
    insp.scheduled_date = date
    insp.status = Inspection.Status.SCHEDULED
    insp.inspector = user
    insp.save()
    audit(user, "inspection.schedule", insp.po, f"{insp.kind} inspection scheduled {date} on {insp.po.number}")


@transaction.atomic
def complete_inspection(user, insp, qty_accepted, qty_rejected, report, deficiencies="", internal_notes=""):
    require_role(user, Role.INSPECTOR)
    require_state(insp.status, (Inspection.Status.PENDING, Inspection.Status.SCHEDULED), "inspection")
    if qty_accepted + qty_rejected != insp.qty_offered:
        fail(f"Accepted plus rejected must equal the quantity inspected ({insp.qty_offered}).")
    if not (report or "").strip():
        fail("An inspection report is required.")
    if qty_rejected > 0 and not (deficiencies or "").strip():
        fail("List the deficiencies for the rejected quantity.")
    insp.qty_accepted = qty_accepted
    insp.qty_rejected = qty_rejected
    insp.report = report
    insp.deficiencies = deficiencies
    insp.internal_notes = internal_notes
    insp.inspector = user
    insp.inspected_at = timezone.now()
    insp.status = Inspection.Status.COMPLETED
    insp.save()
    if insp.kind == Inspection.Kind.HAL and insp.consignment_id:
        c = insp.consignment
        c.qty_accepted = qty_accepted
        c.qty_rejected = qty_rejected
        c.status = Consignment.Status.ACCEPTED if qty_accepted > 0 else Consignment.Status.REJECTED
        c.save()
    audit(user, "inspection.complete", insp.po, f"{insp.kind} inspection on {insp.po.number}: {qty_accepted} accepted, {qty_rejected} rejected")


@transaction.atomic
def dispatch_consignment(user, po, qty, vehicle_no, challan_no):
    _assert_vendor_owns(user, po)
    _require_active_po(po)
    if qty < 1:
        fail("Dispatch at least one item.")
    if not vehicle_no.strip() or not challan_no.strip():
        fail("Vehicle number and challan number are required.")
    if qty > po.qty_dispatchable:
        fail(f"Only {po.qty_dispatchable} can be dispatched; dispatch is limited to quantity accepted at pre-dispatch inspection.")
    c = Consignment.objects.create(po=po, qty=qty, vehicle_no=vehicle_no, challan_no=challan_no, created_by=user)
    audit(user, "consignment.dispatch", po, f"Dispatched {qty} on {c.number}")
    return c


@transaction.atomic
def gate_receipt(user, consignment, qty_received, remarks=""):
    require_role(user, Role.GATE)
    require_state(consignment.status, (Consignment.Status.DISPATCHED,), "consignment")
    if qty_received < 1:
        fail("Record at least one item received.")
    if qty_received > consignment.qty:
        fail(f"Received quantity cannot exceed the dispatched quantity ({consignment.qty}).")
    consignment.qty_received = qty_received
    consignment.received_at = timezone.now()
    consignment.received_by = user
    consignment.gate_remarks = remarks
    consignment.status = Consignment.Status.RECEIVED
    consignment.save()
    Inspection.objects.create(
        po=consignment.po,
        kind=Inspection.Kind.HAL,
        consignment=consignment,
        qty_offered=qty_received,
        requested_by=user,
        request_note="Received at gate",
    )
    audit(user, "consignment.receive", consignment.po, f"Gate received {qty_received}/{consignment.qty} on {consignment.number}")


@transaction.atomic
def start_finishing(user, consignment):
    require_role(user, Role.FINISHING)
    require_state(consignment.status, (Consignment.Status.ACCEPTED,), "consignment")
    consignment.status = Consignment.Status.IN_FINISHING
    consignment.finishing_started_at = timezone.now()
    consignment.finishing_by = user
    consignment.save()
    audit(user, "finishing.start", consignment.po, f"Finishing started on {consignment.number}")


@transaction.atomic
def complete_finishing(user, consignment, remarks=""):
    require_role(user, Role.FINISHING)
    require_state(consignment.status, (Consignment.Status.IN_FINISHING,), "consignment")
    consignment.status = Consignment.Status.FINISHED
    consignment.finishing_completed_at = timezone.now()
    consignment.finishing_remarks = remarks
    consignment.save()
    audit(user, "finishing.complete", consignment.po, f"Finishing completed on {consignment.number}")
    po = consignment.po
    if po.qty_finished >= po.quantity:
        po.status = POStatus.COMPLETED
        po.save()
        req = po.requirement
        req.stage = RequirementStage.CLOSED
        req.save()
        record(user, "ORDER_CLOSED", D.COMPLETED, f"{po.number} fully finished", requirement=req)
        audit(user, "po.close", po, f"{po.number} completed and requirement {req.number} closed")


# ------------------------------------------------------------- vendor access

_ALPHABET = string.ascii_letters + string.digits


def _temp_password():
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(14))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


@transaction.atomic
def issue_credentials(user, vendor):
    """Create or reset the vendor's portal login. Only possible once the vendor holds an order."""
    require_role(user, Role.OUTSOURCING, Role.ADMIN)
    if vendor.status == VendorStatus.SUSPENDED:
        fail("This vendor is suspended. Reactivate it before issuing credentials.")
    if not vendor.orders.exclude(status=POStatus.CANCELLED).exists():
        fail("Credentials can only be issued to a vendor that has been awarded an order.")
    password = _temp_password()
    login = User.objects.filter(vendor=vendor).order_by("id").first()
    if login is None:
        login = User(
            username=vendor.code.lower(),
            first_name=vendor.contact_person or vendor.name,
            email=vendor.email,
            role=Role.VENDOR,
            vendor=vendor,
        )
    login.is_active = True
    login.must_change_password = True
    login.set_password(password)
    login.save()
    vendor.status = VendorStatus.ACTIVE
    vendor.credentials_issued_at = timezone.now()
    vendor.credentials_issued_by = user
    vendor.save()
    audit(user, "vendor.credentials", vendor, f"Portal login issued to {vendor.name}")
    return login.username, password


@transaction.atomic
def suspend_vendor(user, vendor):
    require_role(user, Role.OUTSOURCING, Role.ADMIN)
    vendor.status = VendorStatus.SUSPENDED
    vendor.save()
    vendor.users.update(is_active=False)
    audit(user, "vendor.suspend", vendor, f"Suspended {vendor.name}")


@transaction.atomic
def reactivate_vendor(user, vendor):
    require_role(user, Role.OUTSOURCING, Role.ADMIN)
    if vendor.status != VendorStatus.SUSPENDED:
        fail("This vendor is not suspended.")
    vendor.status = VendorStatus.ACTIVE if vendor.credentials_issued_at else VendorStatus.REGISTERED
    vendor.save()
    vendor.users.update(is_active=True)
    audit(user, "vendor.reactivate", vendor, f"Reactivated {vendor.name}")
