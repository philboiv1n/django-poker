# Generated by Django 5.1.5 on 2025-01-30 17:50

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0005_remove_game_code_game_betting_type_game_game_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='player',
            name='last_active',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
