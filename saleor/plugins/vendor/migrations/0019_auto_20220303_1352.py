# Generated by Django 3.2.10 on 2022-03-03 13:52

from django.db import migrations, models

import saleor.plugins.vendor.models


class Migration(migrations.Migration):

    dependencies = [
        ("vendor", "0018_auto_20220303_1136"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vendor",
            name="email",
            field=models.EmailField(db_index=True, max_length=254, unique=True),
        ),
        migrations.AlterField(
            model_name="vendor",
            name="phone_number",
            field=saleor.plugins.vendor.models.PossiblePhoneNumberField(
                db_index=True, max_length=128, region=None, unique=True
            ),
        ),
    ]
