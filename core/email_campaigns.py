from leads.models import Lead


def campaign_recipient_emails(
    business,
    *,
    stage='',
    source='',
    assigned_user_id=None,
):
    """Return unique, opted-in customer email addresses for a campaign filter."""
    queryset = Lead.objects.for_business(business).exclude(
        email='',
    ).exclude(email_marketing_opt_out=True)
    if stage:
        queryset = queryset.filter(stage=stage)
    if source:
        queryset = queryset.filter(source=source)
    if assigned_user_id:
        queryset = queryset.filter(assigned_user_id=assigned_user_id)

    emails = []
    seen = set()
    for email in queryset.values_list('email', flat=True).iterator():
        normalized = email.strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            emails.append(email.strip())
    return emails
