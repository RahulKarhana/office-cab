from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("trips", "0024_safearrivalconfirmation"),
    ]

    operations = [
        migrations.AddField(
            model_name="emergencyalert",
            name="employee_acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emergencyalert",
            name="admin_acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
