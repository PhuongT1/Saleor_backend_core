# Generated by Django 3.2.22 on 2023-10-09 21:11

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=25)),
                ('slug', models.SlugField(max_length=255, unique=True)),
                ('pharmacy_name', models.CharField(max_length=255)),
                ('npi', models.CharField(max_length=255)),
                ('phone_number', models.CharField(max_length=25)),
                ('fax_number', models.CharField(max_length=25)),
                ('image', models.FileField(upload_to='site/images')),
                ('css', models.FileField(upload_to='site/css')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]