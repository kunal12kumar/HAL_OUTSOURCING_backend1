"""Which role may take which action in which state.

One table drives both the buttons a user sees on a record and the work queue
on their home screen, so the two can never disagree.
"""
from apps.accounts.models import Role as R

ACTION_TABLE = {
    "requirement": {
        "DRAFT": [("submit", R.MSR)],
        "RETURNED": [("submit", R.MSR)],
        "SUBMITTED": [("committee_decision", R.APPROVAL_COMMITTEE)],
        "APPROVED": [("methods_advice", R.METHODS)],
        "METHODS_DONE": [("imm_assessment", R.IMM)],
        "IMM_DONE": [("allocate", R.SCHEDULING)],
        "ALLOCATED": [("create_tender", R.OUTSOURCING)],
    },
    "tender": {
        "DRAFT": [("submit", R.OUTSOURCING)],
        "PENDING_APPROVAL": [("committee_decision", R.APPROVAL_COMMITTEE)],
        "APPROVED": [("set_estimate", R.MSD)],
        "ESTIMATED": [("review_estimate", R.INTERNAL_COMMITTEE)],
        "READY_TO_PUBLISH": [("publish", R.OUTSOURCING)],
        "PUBLISHED": [
            ("add_quotation", R.OUTSOURCING),
            ("evaluate_quotation", R.OUTSOURCING),
            ("start_evaluation", R.OUTSOURCING),
        ],
        "EVALUATION": [
            ("add_quotation", R.OUTSOURCING),
            ("evaluate_quotation", R.OUTSOURCING),
            ("award", R.OUTSOURCING),
            ("refer_to_pnc", R.OUTSOURCING),
        ],
        "PNC_REVIEW": [("pnc_decision", R.PNC)],
    },
    "order": {
        "ACTIVE": [("issue_material", R.OUTSOURCING), ("revise_delivery_date", R.OUTSOURCING)],
    },
    "inspection": {
        "PENDING": [("schedule", R.INSPECTOR), ("complete", R.INSPECTOR)],
        "SCHEDULED": [("schedule", R.INSPECTOR), ("complete", R.INSPECTOR)],
    },
    "consignment": {
        "DISPATCHED": [("gate_receipt", R.GATE)],
        "ACCEPTED": [("start_finishing", R.FINISHING)],
        "IN_FINISHING": [("complete_finishing", R.FINISHING)],
    },
}


def actions_for(kind, state, role):
    return [a for a, r in ACTION_TABLE.get(kind, {}).get(state, []) if r == role]


def queue_states(kind, role):
    """States of `kind` in which `role` has something to do, excluding passive 'order' actions."""
    return [s for s, acts in ACTION_TABLE.get(kind, {}).items() if any(r == role for _, r in acts)]
