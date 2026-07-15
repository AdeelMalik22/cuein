from rest_framework.permissions import BasePermission

from .models import User
from .tenancy import request_membership


class IsBusinessOwner(BasePermission):
    message = 'Only a business owner can perform this action.'

    def has_permission(self, request, view):
        membership = request_membership(request)
        return bool(
            request.user
            and request.user.is_authenticated
            and membership
            and membership.role == User.Role.OWNER
        )


class IsBusinessManagerOrOwner(BasePermission):
    message = 'Only a business owner or manager can perform this action.'

    def has_permission(self, request, view):
        membership = request_membership(request)
        return bool(
            request.user
            and request.user.is_authenticated
            and membership
            and membership.role in (User.Role.OWNER, User.Role.MANAGER)
        )
