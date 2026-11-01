from django.db import transaction
from django.utils import timezone


def next_number(prefix, with_year=True, width=4):
    from .models import Counter

    year = timezone.now().year
    key = f"{prefix}-{year}" if with_year else prefix
    with transaction.atomic():
        counter, _ = Counter.objects.select_for_update().get_or_create(key=key)
        counter.value += 1
        counter.save(update_fields=["value"])
    seq = f"{counter.value:0{width}d}"
    return f"{prefix}-{year}-{seq}" if with_year else f"{prefix}-{seq}"
