from django.core.validators import RegexValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("game", "0002_add_indexes_and_fix_total_bet_field"),
    ]

    operations = [
        # Add choices constraint to Game.status
        migrations.AlterField(
            model_name="game",
            name="status",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("waiting", "Waiting"),
                    ("active", "Active"),
                    ("finished", "Finished"),
                ],
                default="waiting",
            ),
        ),
        # Add hex color validator to Profile.avatar_color
        migrations.AlterField(
            model_name="profile",
            name="avatar_color",
            field=models.CharField(
                max_length=7,
                default="#000000",
                validators=[
                    RegexValidator(
                        r"^#[0-9A-Fa-f]{6}$",
                        "Enter a valid hex color (e.g. #FF5733).",
                    )
                ],
            ),
        ),
    ]
