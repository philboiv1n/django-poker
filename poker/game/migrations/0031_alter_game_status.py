# Generated by Django 5.1.5 on 2025-02-26 17:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0030_player_has_checked'),
    ]

    operations = [
        migrations.AlterField(
            model_name='game',
            name='status',
            field=models.CharField(default='waiting', max_length=20),
        ),
    ]
