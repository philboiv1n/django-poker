from django.core.management.base import BaseCommand
from django.utils.timezone import now
from datetime import timedelta
from game.models import Player


class Command(BaseCommand):
    help = "Removes players who have been inactive for more than 10 minutes"

    def handle(self, *args, **kwargs):
        threshold = now() - timedelta(minutes=10)
        removed = Player.objects.filter(last_active__lt=threshold).delete()
        self.stdout.write(self.style.SUCCESS(f"Removed {removed[0]} inactive players."))
