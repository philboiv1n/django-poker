# Generated by Django 5.1.5 on 2025-02-06 18:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0014_game_current_turn'),
    ]

    operations = [
        migrations.AlterField(
            model_name='game',
            name='current_turn',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='game',
            name='dealer_position',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
