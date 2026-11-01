from django.conf import settings
from django.db import models


class VendorStatus(models.TextChoices):
    REGISTERED = "REGISTERED", "Registered (no portal access)"
    ACTIVE = "ACTIVE", "Active (portal enabled)"
    SUSPENDED = "SUSPENDED", "Suspended"


class Vendor(models.Model):
    code = models.CharField(max_length=20, unique=True, editable=False)
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=VendorStatus.choices, default=VendorStatus.REGISTERED)
    credentials_issued_at = models.DateTimeField(null=True, blank=True)
    credentials_issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} {self.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            from apps.workflow.numbering import next_number

            self.code = next_number("VND", with_year=False, width=4)
        super().save(*args, **kwargs)
