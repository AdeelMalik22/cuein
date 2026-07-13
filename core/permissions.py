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
