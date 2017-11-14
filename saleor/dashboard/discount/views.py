from __future__ import unicode_literals

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils.translation import pgettext_lazy

from ...core.utils import get_paginator_items
from ...core.utils.filters import get_now_sorted_by
from ...discount.models import Sale, Voucher
from ..views import staff_member_required
from .filters import SaleFilter, SORT_BY_FIELDS
from . import forms


@staff_member_required
@permission_required('discount.view_sale')
def sale_list(request):
    sales = Sale.objects.prefetch_related('products').order_by('name')
    sale_filter = SaleFilter(request.GET, queryset=sales)
    sales = get_paginator_items(
        sale_filter.qs, settings.DASHBOARD_PAGINATE_BY, request.GET.get('page'))
    now_sorted_by = get_now_sorted_by(sale_filter, SORT_BY_FIELDS)
    arg_sort_by = request.GET.get('sort_by')
    is_descending = arg_sort_by.startswith('-') if arg_sort_by else False
    ctx = {
        'sales': sales, 'filter': sale_filter,
        'now_sorted_by': now_sorted_by, 'is_descending': is_descending}
    return TemplateResponse(request, 'dashboard/discount/sale/list.html', ctx)


@staff_member_required
@permission_required('discount.edit_sale')
def sale_edit(request, pk=None):
    if pk:
        instance = get_object_or_404(Sale, pk=pk)
    else:
        instance = Sale()
    form = forms.SaleForm(
        request.POST or None, instance=instance)
    if form.is_valid():
        instance = form.save()
        msg = pgettext_lazy(
            'Sale (discount) message', 'Updated sale') if pk else pgettext_lazy(
                'Sale (discount) message', 'Added sale')
        messages.success(request, msg)
        return redirect('dashboard:sale-update', pk=instance.pk)
    ctx = {'sale': instance, 'form': form}
    return TemplateResponse(request, 'dashboard/discount/sale/form.html', ctx)


@staff_member_required
@permission_required('discount.edit_sale')
def sale_delete(request, pk):
    instance = get_object_or_404(Sale, pk=pk)
    if request.method == 'POST':
        instance.delete()
        messages.success(
            request,
            pgettext_lazy('Sale (discount) message', 'Removed sale %s') % (instance.name,))
        return redirect('dashboard:sale-list')
    ctx = {'sale': instance}
    return TemplateResponse(
        request, 'dashboard/discount/sale/modal/confirm_delete.html', ctx)


@staff_member_required
@permission_required('discount.view_voucher')
def voucher_list(request):
    vouchers = (Voucher.objects.select_related('product', 'category')
                .order_by('name'))
    vouchers = get_paginator_items(
        vouchers, settings.DASHBOARD_PAGINATE_BY, request.GET.get('page'))
    ctx = {'vouchers': vouchers}
    return TemplateResponse(
        request, 'dashboard/discount/voucher/list.html', ctx)


@staff_member_required
@permission_required('discount.edit_voucher')
def voucher_edit(request, pk=None):
    if pk is not None:
        instance = get_object_or_404(Voucher, pk=pk)
    else:
        instance = Voucher()
    voucher_form = forms.VoucherForm(request.POST or None, instance=instance)
    type_base_forms = {
        Voucher.SHIPPING_TYPE: forms.ShippingVoucherForm(
            request.POST or None, instance=instance,
            prefix=Voucher.SHIPPING_TYPE),
        Voucher.VALUE_TYPE: forms.ValueVoucherForm(
            request.POST or None, instance=instance,
            prefix=Voucher.VALUE_TYPE),
        Voucher.PRODUCT_TYPE: forms.ProductVoucherForm(
            request.POST or None, instance=instance,
            prefix=Voucher.PRODUCT_TYPE),
        Voucher.CATEGORY_TYPE: forms.CategoryVoucherForm(
            request.POST or None, instance=instance,
            prefix=Voucher.CATEGORY_TYPE)}
    if voucher_form.is_valid():
        voucher_type = voucher_form.cleaned_data['type']
        form_type = type_base_forms.get(voucher_type)
        if form_type is None:
            instance = voucher_form.save()
        elif form_type.is_valid():
            instance = form_type.save()

        if form_type is None or form_type.is_valid():
            msg = pgettext_lazy(
                'Voucher message', 'Updated voucher') if pk else pgettext_lazy(
                    'Voucher message', 'Added voucher')
            messages.success(request, msg)
            return redirect('dashboard:voucher-update', pk=instance.pk)
    ctx = {
        'voucher': instance, 'default_currency': settings.DEFAULT_CURRENCY,
        'form': voucher_form, 'type_base_forms': type_base_forms}
    return TemplateResponse(
        request, 'dashboard/discount/voucher/form.html', ctx)


@staff_member_required
@permission_required('discount.edit_voucher')
def voucher_delete(request, pk):
    instance = get_object_or_404(Voucher, pk=pk)
    if request.method == 'POST':
        instance.delete()
        messages.success(
            request,
            pgettext_lazy('Voucher message', 'Removed voucher %s') % (instance,))
        return redirect('dashboard:voucher-list')
    ctx = {'voucher': instance}
    return TemplateResponse(
        request, 'dashboard/discount/voucher/modal/confirm_delete.html', ctx)
