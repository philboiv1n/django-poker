# Generated by Django 5.1.5 on 2025-03-24 13:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0043_player_is_dealer_player_is_small_blind'),
    ]

    operations = [
        migrations.AddField(
            model_name='player',
            name='is_nextToPlay',
            field=models.BooleanField(default=False),
        ),
    ]
