from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class Role(models.TextChoices):
    ADMIN = "ADMIN", "HAL Administrator"
    MSR = "MSR", "MSR Department"
    APPROVAL_COMMITTEE = "APPROVAL_COMMITTEE", "Approval Committee"
    METHODS = "METHODS", "Methods Department"
    IMM = "IMM", "Integrated Material Management"
    SCHEDULING = "SCHEDULING", "Scheduling Department"
    OUTSOURCING = "OUTSOURCING", "Outsourcing Department"
    MSD = "MSD", "MSD Department"
    INTERNAL_COMMITTEE = "INTERNAL_COMMITTEE", "Internal Committee"
    PNC = "PNC", "PNC Committee"
    INSPECTOR = "INSPECTOR", "HAL Inspection Team"
    GATE = "GATE", "Security Gate and Receiving"
    FINISHING = "FINISHING", "Finishing Team"
    VENDOR = "VENDOR", "External Vendor"


class User(AbstractUser):
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.MSR)
    employee_id = models.CharField(max_length=32, blank=True)
    vendor = models.ForeignKey(
        "vendors.Vendor", null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    must_change_password = models.BooleanField(default=False)

    @property
    def is_vendor(self):
        return self.role == Role.VENDOR

    def clean(self):
        super().clean()
        if self.role == Role.VENDOR and not self.vendor_id:
            raise ValidationError("A vendor user must be linked to a vendor.")
        if self.role != Role.VENDOR and self.vendor_id:
            raise ValidationError("Only vendor users may be linked to a vendor.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
