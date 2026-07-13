from rest_framework.permissions import BasePermission

from .models import User


class IsBusinessOwner(BasePermission):
    message = 'Only a business owner can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == User.Role.OWNER
            and request.user.business_id
        )


class IsBusinessManagerOrOwner(BasePermission):
    message = 'Only a business owner or manager can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.business_id
            and request.user.role in (User.Role.OWNER, User.Role.MANAGER)
        )
