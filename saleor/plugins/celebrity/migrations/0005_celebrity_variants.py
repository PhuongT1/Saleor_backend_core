# Generated by Django 3.2.10 on 2022-03-07 00:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("product", "0155_merge_20211208_1108"),
        ("celebrity", "0004_alter_celebrity_phone_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="celebrity",
            name="variants",
            field=models.ManyToManyField(to="product.ProductVariant"),
        ),
    ]
