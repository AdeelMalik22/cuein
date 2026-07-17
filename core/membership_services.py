"""Transactional membership lifecycle operations."""

from .models import Membership, User


class LastActiveOwnerError(ValueError):
    """Raised when an operation would remove a workspace's final owner."""


def _lock_workspace_memberships(business):
    """Take workspace membership locks in a deterministic order.

    Locking the whole small workspace set avoids the A-locks-B / B-locks-A
    deadlock that can otherwise occur when two owners are changed together.
    """
    return list(
        Membership.objects.select_related('user').select_for_update(of=('self',)).filter(
            business=business,
        ).order_by('pk'),
    )


def _locked_membership(*, membership_id, business):
    memberships = _lock_workspace_memberships(business)
    for membership in memberships:
        if membership.id == membership_id:
            return membership, memberships
    raise Membership.DoesNotExist


def _ensure_not_last_active_owner(membership, memberships, *, next_role, next_is_active):
    if not (
        membership.role == User.Role.OWNER
        and membership.is_active
        and (next_role != User.Role.OWNER or not next_is_active)
    ):
        return
    active_owner_count = sum(
        member.role == User.Role.OWNER and member.is_active and member.user.is_active
        for member in memberships
    )
    if active_owner_count <= 1:
        raise LastActiveOwnerError('A business must keep at least one active owner.')


def update_membership(*, membership_id, business, role=None, is_active=None):
    """Update a membership while serializing changes to its owner set.

    Call within ``transaction.atomic()``. Locking every workspace membership
    prevents concurrent demotions/deactivations from leaving it orphaned.
    """
    membership, memberships = _locked_membership(membership_id=membership_id, business=business)
    next_role = membership.role if role is None else role
    next_is_active = membership.is_active if is_active is None else is_active
    _ensure_not_last_active_owner(
        membership,
        memberships,
        next_role=next_role,
        next_is_active=next_is_active,
    )

    update_fields = []
    if membership.role != next_role:
        membership.role = next_role
        update_fields.append('role')
    if membership.is_active != next_is_active:
        membership.is_active = next_is_active
        update_fields.append('is_active')
    if update_fields:
        membership.save(update_fields=update_fields)
    return membership


def remove_membership(*, membership_id, business):
    """Remove one workspace membership without deleting the global account."""
    membership, memberships = _locked_membership(membership_id=membership_id, business=business)
    _ensure_not_last_active_owner(
        membership,
        memberships,
        next_role=membership.role,
        next_is_active=False,
    )
    membership.delete()
    return membership
