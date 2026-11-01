from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class HalUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("HAL", {"fields": ("role", "employee_id", "vendor", "must_change_password")}),)
    list_display = ("username", "get_full_name", "role", "vendor", "is_active")
    list_filter = ("role", "is_active")
