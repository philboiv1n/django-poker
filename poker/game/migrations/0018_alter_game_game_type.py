# Generated by Django 5.1.5 on 2025-02-16 16:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0017_player_hole_cards'),
    ]

    operations = [
        migrations.AlterField(
            model_name='game',
            name='game_type',
            field=models.CharField(choices=[('sit_and_go', "Texas Hold'em - Sit & Go"), ('cash_game', "Texas Hold'em - Cash Game")], default='sit_and_go', max_length=20),
        ),
    ]
