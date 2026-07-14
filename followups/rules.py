from dataclasses import dataclass
from datetime import timedelta

from leads.models import Lead


@dataclass(frozen=True)
class FollowUpRule:
    key: str
    delay: timedelta
    description: str


QUOTE_FOLLOWUP = FollowUpRule('quote_followup_v1', timedelta(days=2), 'Follow up on the quotation.')
DELAYED_FOLLOWUP = FollowUpRule('delayed_followup_v1', timedelta(days=7), 'Customer requested more time; follow up.')
WARRANTY_CHECKIN = FollowUpRule('warranty_checkin_v1', timedelta(days=334), 'Warranty check-in and referral request.')
STALE_LEAD_ESCALATION = FollowUpRule('stale_lead_escalation_v1', timedelta(days=10), 'Lead has had no customer activity for 10 days.')

RULES = {rule.key: rule for rule in (QUOTE_FOLLOWUP, DELAYED_FOLLOWUP, WARRANTY_CHECKIN, STALE_LEAD_ESCALATION)}


def rule_for_stage(stage):
    return {
        Lead.Stage.QUOTATION_SENT: QUOTE_FOLLOWUP,
        Lead.Stage.WON: WARRANTY_CHECKIN,
    }.get(stage)
