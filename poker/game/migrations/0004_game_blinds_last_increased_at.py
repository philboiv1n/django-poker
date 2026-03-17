from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("game", "0003_profile_str_status_choices_avatar_validator"),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="blinds_last_increased_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
