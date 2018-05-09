from django.shortcuts import redirect
from django.template.response import TemplateResponse

from .discount import add_voucher_form, validate_voucher
from .shipping import (
    anonymous_user_shipping_address_view, user_shipping_address_view)
from .summary import (
    summary_with_shipping_view, anonymous_summary_without_shipping,
    summary_without_shipping)
from .validators import (
    validate_cart, validate_shipping_address,
    validate_shipping_method, validate_is_shipping_required)
from ..core import load_checkout
from ..forms import ShippingMethodForm
from ..utils import get_cart_data
from ...account.forms import LoginForm


@load_checkout
@validate_cart
@validate_is_shipping_required
def index_view(request, cart, checkout):
    """Redirect to the initial step of checkout."""
    return redirect('checkout:shipping-address')


@load_checkout
@validate_voucher
@validate_cart
@validate_is_shipping_required
@add_voucher_form
def shipping_address_view(request, cart, checkout):
    """Display the correct shipping address step."""
    if request.user.is_authenticated:
        return user_shipping_address_view(request, cart, checkout)
    return anonymous_user_shipping_address_view(request, cart, checkout)


@load_checkout
@validate_voucher
@validate_cart
@validate_is_shipping_required
@validate_shipping_address
@add_voucher_form
def shipping_method_view(request, cart, checkout):
    """Display the shipping method selection step."""
    country_code = checkout.shipping_address.country.code
    shipping_method_form = ShippingMethodForm(
        country_code, request.taxes, request.POST or None,
        initial={'method': checkout.shipping_method})

    if shipping_method_form.is_valid():
        checkout.shipping_method = shipping_method_form.cleaned_data['method']
        return redirect('checkout:summary')

    ctx = get_cart_data(cart, request.discounts, checkout.get_taxes())
    ctx.update({
        'checkout': checkout,
        'shipping_method_form': shipping_method_form})
    return TemplateResponse(request, 'checkout/shipping_method.html', ctx)


@load_checkout
@validate_voucher
@validate_cart
@add_voucher_form
def summary_view(request, cart, checkout):
    """Display the correct order summary."""
    if checkout.is_shipping_required:
        view = validate_shipping_address(summary_with_shipping_view)
        view = validate_shipping_method(view)
        return view(request, cart, checkout)
    if request.user.is_authenticated:
        return summary_without_shipping(request, cart, checkout)
    return anonymous_summary_without_shipping(request, cart, checkout)


@load_checkout
@validate_cart
def login(request, cart, checkout):
    """Allow the user to log in prior to checkout."""
    if request.user.is_authenticated:
        return redirect('checkout:index')
    form = LoginForm()
    return TemplateResponse(request, 'checkout/login.html', {'form': form})
