# Generated by Django 3.2.6 on 2022-01-13 11:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0005_appextension"),
    ]

    operations = [
        migrations.AddField(
            model_name="appextension",
            name="open_as",
            field=models.CharField(
                choices=[("popup", "popup"), ("app_page", "app_page")],
                default="popup",
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="appextension",
            name="target",
            field=models.CharField(
                choices=[
                    ("more_actions", "more_actions"),
                    ("create", "create"),
                    ("catalog", "catalog"),
                    ("orders", "orders"),
                    ("customers", "customers"),
                    ("discounts", "discounts"),
                    ("translations", "translations"),
                    ("pages", "pages"),
                ],
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="appextension",
            name="type",
            field=models.CharField(
                choices=[
                    ("overview", "overview"),
                    ("details", "details"),
                    ("navigation", "navigation"),
                ],
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="appextension",
            name="view",
            field=models.CharField(
                choices=[("product", "product"), ("all", "all")], max_length=128
            ),
        ),
    ]
