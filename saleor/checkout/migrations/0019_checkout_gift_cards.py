# Generated by Django 2.2.1 on 2019-06-07 09:43

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('giftcard', '0001_initial'),
        ('checkout', '0018_auto_20190410_0132'),
    ]

    operations = [
        migrations.AddField(
            model_name='checkout',
            name='gift_cards',
            field=models.ManyToManyField(blank=True, related_name='checkouts', to='giftcard.GiftCard'),
        ),
    ]
