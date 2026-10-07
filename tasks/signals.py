from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import List, Task


@receiver(post_save, sender=Task)
@receiver(post_delete, sender=Task)
def touch_list(sender, instance, **kwargs):
    List.objects.filter(pk=instance.list_id).update(last_activity_at=timezone.now())
