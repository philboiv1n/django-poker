from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("game", "0001_initial"),
    ]

    operations = [
        # Change total_bet from IntegerField to PositiveIntegerField
        migrations.AlterField(
            model_name="player",
            name="total_bet",
            field=models.PositiveIntegerField(default=0),
        ),
        # Add db_index to has_folded (used in nearly every active-player query)
        migrations.AlterField(
            model_name="player",
            name="has_folded",
            field=models.BooleanField(default=False, db_index=True),
        ),
        # Add db_index to position (used in ordering and seat lookups)
        migrations.AlterField(
            model_name="player",
            name="position",
            field=models.PositiveIntegerField(blank=True, db_index=True, null=True),
        ),
    ]
