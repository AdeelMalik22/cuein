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
