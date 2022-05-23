# Generated by Django 3.2.12 on 2022-05-23 15:33

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('order', '0118_auto_20220523_1533'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderline',
            name='original_product_sku',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='orderline',
            name='original_variant_id',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
