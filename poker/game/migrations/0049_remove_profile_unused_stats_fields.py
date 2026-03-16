from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0048_add_raise_tracking_fields'),
    ]

    operations = [
        migrations.RemoveField(model_name='profile', name='total_chips_received'),
        migrations.RemoveField(model_name='profile', name='total_chips_won'),
        migrations.RemoveField(model_name='profile', name='total_chips_lost'),
        migrations.RemoveField(model_name='profile', name='games_played'),
        migrations.RemoveField(model_name='profile', name='games_won'),
        migrations.RemoveField(model_name='profile', name='games_lost'),
        migrations.RemoveField(model_name='profile', name='hands_played'),
        migrations.RemoveField(model_name='profile', name='hands_won'),
        migrations.RemoveField(model_name='profile', name='highest_win'),
        migrations.RemoveField(model_name='profile', name='longest_winning_streak'),
        migrations.RemoveField(model_name='profile', name='longest_losing_streak'),
        migrations.RemoveField(model_name='profile', name='average_bet'),
        migrations.RemoveField(model_name='profile', name='ranking'),
        migrations.RemoveField(model_name='profile', name='royal_flushes'),
        migrations.RemoveField(model_name='profile', name='straight_flushes'),
        migrations.RemoveField(model_name='profile', name='four_of_a_kinds'),
        migrations.RemoveField(model_name='profile', name='full_houses'),
    ]
