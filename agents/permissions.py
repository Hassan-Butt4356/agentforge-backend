from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsOwnerOrPublicReadOnly(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return obj.is_public or obj.owner == request.user
        return obj.owner == request.user
