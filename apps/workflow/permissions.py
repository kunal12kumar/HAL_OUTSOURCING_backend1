from rest_framework.permissions import BasePermission

from apps.accounts.models import Role
from apps.vendors.models import VendorStatus


class IsInternalAny(BasePermission):
    """HAL staff only, even while a temporary password is still in use. For sign-in housekeeping endpoints."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and not u.is_vendor)


class IsInternal(IsInternalAny):
    """HAL staff who have set their own password. Vendor logins are refused on every internal endpoint."""

    message = "You must change your temporary password before using the system."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and not request.user.must_change_password


class IsVendorUser(BasePermission):
    """An active vendor login whose company has not been suspended."""

    def has_permission(self, request, view):
        u = request.user
        return bool(
            u
            and u.is_authenticated
            and u.is_active
            and u.is_vendor
            and u.vendor_id
            and u.vendor.status == VendorStatus.ACTIVE
        )


class VendorPasswordChanged(BasePermission):
    message = "You must change your temporary password before using the portal."

    def has_permission(self, request, view):
        return not request.user.must_change_password


def roles_permission(*roles):
    class _HasRole(BasePermission):
        def has_permission(self, request, view):
            return request.user.is_authenticated and request.user.role in roles

    _HasRole.__name__ = "HasRole_" + "_".join(roles)
    return _HasRole


IsAdmin = roles_permission(Role.ADMIN)
IsOutsourcingOrAdmin = roles_permission(Role.OUTSOURCING, Role.ADMIN)
