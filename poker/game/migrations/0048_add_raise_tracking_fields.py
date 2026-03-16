from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0047_remove_game_side_pots'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='last_raise_delta',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='player',
            name='can_reraise_this_round',
            field=models.BooleanField(default=True),
        ),
    ]
