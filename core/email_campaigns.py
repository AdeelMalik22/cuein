import re

from leads.models import Lead


def campaign_recipient_contacts(
    business,
    *,
    stage='',
    source='',
    assigned_user_id=None,
):
    """Return unique opted-in contacts with enough data for personalization."""
    queryset = Lead.objects.for_business(business).exclude(
        email='',
    ).exclude(email_marketing_opt_out=True).order_by('id')
    if stage:
        queryset = queryset.filter(stage=stage)
    if source:
        queryset = queryset.filter(source=source)
    if assigned_user_id:
        queryset = queryset.filter(assigned_user_id=assigned_user_id)

    contacts = []
    seen = set()
    for email, customer_name in queryset.values_list('email', 'customer_name').iterator():
        email = email.strip()
        normalized = email.casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            contacts.append({'email': email, 'customer_name': customer_name.strip()})
    return contacts


def campaign_recipient_emails(
    business,
    *,
    stage='',
    source='',
    assigned_user_id=None,
):
    """Return unique, opted-in customer email addresses for a campaign filter."""
    return [
        contact['email']
        for contact in campaign_recipient_contacts(
            business,
            stage=stage,
            source=source,
            assigned_user_id=assigned_user_id,
        )
    ]


def campaign_audience_summary(
    business,
    *,
    stage='',
    source='',
    assigned_user_id=None,
):
    """Return eligible and excluded unique contacts for the audience picker."""
    queryset = Lead.objects.for_business(business)
    if stage:
        queryset = queryset.filter(stage=stage)
    if source:
        queryset = queryset.filter(source=source)
    if assigned_user_id:
        queryset = queryset.filter(assigned_user_id=assigned_user_id)

    all_keys = set()
    eligible_keys = set()
    for email, phone, customer_name, opted_out in queryset.values_list(
        'email', 'phone', 'customer_name', 'email_marketing_opt_out',
    ).iterator():
        email_key = email.strip().casefold()
        key = email_key or re.sub(r'\D+', '', phone or '') or customer_name.strip().casefold()
        if not key:
            continue
        all_keys.add(key)
        if email_key and not opted_out:
            eligible_keys.add(email_key)
    return {
        'eligible': len(eligible_keys),
        'excluded': len(all_keys - eligible_keys),
    }
