"""Server-side workspace resolution and membership helpers.

Business identity is deliberately resolved here rather than accepted from a
request header or form field.  Web requests keep the selected workspace in the
session; JWT requests carry a server-validated membership claim.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import PermissionDenied

from .models import Membership, User


ACTIVE_BUSINESS_SESSION_KEY = 'active_business_id'


def ensure_legacy_membership(user):
    """Create the one membership represented by a pre-workspace user record.

    This is intentionally idempotent.  It keeps deployments safe while the
    legacy ``User.business`` bridge is still present and also makes fixtures
    created with the old model shape usable during the transition.
    """
    if not user or not getattr(user, 'pk', None) or not user.business_id:
        return None

    membership, _created = Membership.objects.get_or_create(
        user_id=user.pk,
        business_id=user.business_id,
        defaults={
            'role': user.role,
            'is_active': user.is_active,
        },
    )
    return membership


def sync_legacy_memberships(*, using='default'):
    """Backfill memberships for all existing legacy user-to-business links."""
    created = 0
    users = User.objects.using(using).exclude(business__isnull=True).only(
        'id', 'business_id', 'role', 'is_active',
    )
    for user in users.iterator():
        _membership, was_created = Membership.objects.using(using).get_or_create(
            user_id=user.pk,
            business_id=user.business_id,
            defaults={
                'role': user.role,
                'is_active': user.is_active,
            },
        )
        created += int(was_created)
    return created


def ensure_legacy_memberships_for_business(business):
    """Bridge legacy teammates when a workspace is first opened.

    Production data is backfilled during ``migrate``.  This narrower fallback
    also supports old fixtures and integrations that still create users with
    only ``User.business`` during the compatibility period.
    """
    if business is None:
        return
    users = User.objects.filter(business=business).only('id', 'business_id', 'role', 'is_active')
    for user in users.iterator():
        ensure_legacy_membership(user)


def active_memberships_for(user):
    """Return only memberships that may be selected as a workspace."""
    ensure_legacy_membership(user)
    return Membership.objects.filter(
        user=user,
        is_active=True,
        business__is_active=True,
    ).select_related('business')


def _membership_by_business_id(queryset, business_id):
    if not business_id:
        return None
    try:
        return queryset.filter(business_id=business_id).first()
    except (DjangoValidationError, TypeError, ValueError):
        return None


def default_active_membership_for(user):
    """Choose a deterministic workspace when no valid selection exists."""
    memberships = active_memberships_for(user)
    # Existing users continue to start in their original workspace; new
    # multi-workspace users fall back to their earliest active membership.
    legacy_membership = _membership_by_business_id(memberships, user.business_id)
    return legacy_membership or memberships.order_by('joined_at', 'id').first()


def membership_for_active_business(user, business_id):
    """Validate that a requested business is selectable by this user."""
    return _membership_by_business_id(active_memberships_for(user), business_id)


def resolve_web_membership(request, *, user=None):
    """Resolve and persist a valid active workspace for a session request."""
    user = user or request.user
    membership = membership_for_active_business(
        user,
        request.session.get(ACTIVE_BUSINESS_SESSION_KEY),
    )
    if membership is None:
        membership = default_active_membership_for(user)

    if membership is not None:
        workspace_id = str(membership.business_id)
        if request.session.get(ACTIVE_BUSINESS_SESSION_KEY) != workspace_id:
            request.session[ACTIVE_BUSINESS_SESSION_KEY] = workspace_id
    else:
        request.session.pop(ACTIVE_BUSINESS_SESSION_KEY, None)
    return membership


def attach_active_membership(request, membership):
    """Expose one resolved workspace to downstream API and view code."""
    request.active_membership = membership
    request.active_business = membership.business if membership else None
    request.active_role = membership.role if membership else None
    return membership


def request_membership(request):
    """Return the already-authenticated workspace, resolving session fallback.

    JWT authentication populates ``request.active_membership`` itself.  The
    fallback is needed for normal browser sessions and DRF's test client; it
    never uses a client-supplied business ID.
    """
    membership = getattr(request, 'active_membership', None)
    if membership is not None:
        return membership

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None
    if hasattr(request, 'session'):
        membership = resolve_web_membership(request)
    else:
        membership = default_active_membership_for(user)
    return attach_active_membership(request, membership)


def active_business(request):
    membership = request_membership(request)
    if membership is None:
        raise PermissionDenied('You do not have an active business workspace.')
    return membership.business


def active_role(request):
    membership = request_membership(request)
    if membership is None:
        raise PermissionDenied('You do not have an active business workspace.')
    return membership.role


def is_active_member_of_business(user, business_id):
    """Whether a user can currently be selected in a business workspace."""
    ensure_legacy_membership(user)
    return Membership.objects.filter(
        user=user,
        business_id=business_id,
        is_active=True,
        user__is_active=True,
        business__is_active=True,
    ).exists()


def belongs_to_business(user_id, business_id):
    """Membership validation for historical tenant-scoped model records.

    Inactive memberships remain valid for old leads/tasks assigned before a
    person was removed, so this deliberately does not filter ``is_active``.
    The legacy fallback disappears when the old User field is eventually
    removed.
    """
    if Membership.objects.filter(user_id=user_id, business_id=business_id).exists():
        return True
    return User.objects.filter(pk=user_id, business_id=business_id).exists()


def users_for_business(business, *, active_only=True):
    """Users selectable inside one workspace, without relying on User.business."""
    ensure_legacy_memberships_for_business(business)
    queryset = User.objects.filter(memberships__business=business)
    if active_only:
        queryset = queryset.filter(memberships__is_active=True, is_active=True)
    return queryset.distinct()
