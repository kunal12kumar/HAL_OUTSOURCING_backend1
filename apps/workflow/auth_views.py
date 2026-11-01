from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.vendors.models import VendorStatus

from .permissions import IsInternalAny, IsVendorUser
from .serializers import ChangePasswordIn, UserSerializer
from .services import audit


class LoginIn(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


def _tokens(user):
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class _BaseLogin(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    vendor_portal = False

    def post(self, request):
        data = LoginIn(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(username=data.validated_data["username"], password=data.validated_data["password"])
        # The same generic answer is used for every refusal so the response does not reveal
        # whether the account exists, belongs to the other site, or is suspended.
        if user is None or not user.is_active or user.is_vendor != self.vendor_portal:
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_401_UNAUTHORIZED)
        if self.vendor_portal and user.vendor.status != VendorStatus.ACTIVE:
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_401_UNAUTHORIZED)
        audit(user, "auth.login", user, f"{user.username} signed in")
        return Response({**_tokens(user), "user": UserSerializer(user).data})


class InternalLoginView(_BaseLogin):
    vendor_portal = False


class VendorLoginView(_BaseLogin):
    vendor_portal = True


class _BaseMe(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class InternalMeView(_BaseMe):
    permission_classes = [IsInternalAny]


class VendorMeView(_BaseMe):
    permission_classes = [IsVendorUser]


class _BaseChangePassword(APIView):
    def post(self, request):
        data = ChangePasswordIn(data=request.data)
        data.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(data.validated_data["old_password"]):
            return Response({"detail": "The current password is wrong."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(data.validated_data["new_password"], user)
        except DjangoValidationError as exc:
            return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        if data.validated_data["new_password"] == data.validated_data["old_password"]:
            return Response({"detail": "Choose a different password."}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(data.validated_data["new_password"])
        user.must_change_password = False
        user.save()
        audit(user, "auth.password_change", user, f"{user.username} changed password")
        return Response({"detail": "Password changed."})


class InternalChangePasswordView(_BaseChangePassword):
    permission_classes = [IsInternalAny]


class VendorChangePasswordView(_BaseChangePassword):
    permission_classes = [IsVendorUser]
