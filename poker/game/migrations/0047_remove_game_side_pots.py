# Generated by Django 5.1.5 on 2025-04-19 10:50

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0046_remove_player_is_next_to_play'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='game',
            name='side_pots',
        ),
    ]
