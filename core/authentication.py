"""Business-scoped JWT issuance and request authentication."""

from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from .tenancy import (
    attach_active_membership,
    membership_for_active_business,
    resolve_web_membership,
)


BUSINESS_ID_CLAIM = 'business_id'


def select_token_membership(user, business_id=None):
    """Select a valid workspace for a new token pair.

    A person with several workspaces must choose one explicitly.  This keeps
    each token bound to exactly one tenant and avoids a mutable client-supplied
    workspace header on normal API requests.
    """
    if business_id:
        membership = membership_for_active_business(user, business_id)
        if membership is None:
            raise ValidationError({
                'business_id': 'You do not have an active membership in this business.',
            })
        return membership

    # Avoid depending on a legacy default when a person has several active
    # memberships: the API caller must make the workspace choice explicit.
    from .tenancy import active_memberships_for

    active_memberships = active_memberships_for(user).order_by('joined_at', 'id')
    count = active_memberships.count()
    if count == 0:
        raise ValidationError({'business_id': 'You do not have an active business workspace.'})
    if count > 1:
        raise ValidationError({
            'business_id': 'Choose the business workspace for this token.',
        })
    return active_memberships.first()


def token_pair_for_membership(user, membership):
    """Issue refresh/access tokens that both carry the validated workspace."""
    refresh = RefreshToken.for_user(user)
    refresh[BUSINESS_ID_CLAIM] = str(membership.business_id)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


class BusinessScopedJWTAuthentication(JWTAuthentication):
    """Accept only access tokens tied to a currently active membership."""

    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None

        user, token = authenticated
        business_id = token.get(BUSINESS_ID_CLAIM)
        if not business_id:
            raise AuthenticationFailed('This token is not scoped to a business workspace.')
        membership = membership_for_active_business(user, business_id)
        if membership is None:
            raise AuthenticationFailed('This workspace is no longer available to this account.')
        attach_active_membership(request, membership)
        return user, token


class WorkspaceSessionAuthentication(SessionAuthentication):
    """Resolve the server-validated session workspace for browser API calls."""

    def authenticate(self, request):
        authenticated = super().authenticate(request)
        if authenticated is None:
            return None

        user, auth = authenticated
        # Accessing request.user here would recursively invoke DRF's current
        # authentication chain, so use the user returned by the session
        # authenticator directly.
        membership = resolve_web_membership(request, user=user)
        if membership is None:
            raise AuthenticationFailed('You do not have an active business workspace.')
        attach_active_membership(request, membership)
        return user, auth
