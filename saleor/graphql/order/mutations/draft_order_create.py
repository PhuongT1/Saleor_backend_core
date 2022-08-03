from typing import List, Tuple

import graphene
from django.core.exceptions import ValidationError
from django.db import transaction
from graphene.types import InputObjectType

from ....checkout import AddressType
from ....core.permissions import OrderPermissions
from ....core.taxes import TaxError
from ....core.tracing import traced_atomic_transaction
from ....core.utils.url import validate_storefront_url
from ....order import OrderOrigin, OrderStatus, events, models
from ....order.error_codes import OrderErrorCode
from ....order.search import update_order_search_vector
from ....order.utils import (
    add_variant_to_order,
    invalidate_order_prices,
    recalculate_order_weight,
)
from ...account.i18n import I18nMixin
from ...account.types import AddressInput
from ...app.dataloaders import get_app
from ...channel.types import Channel
from ...core.mutations import ModelMutation
from ...core.scalars import PositiveDecimal
from ...core.types import NonNullList, OrderError
from ...product.types import ProductVariant
from ...shipping.utils import get_shipping_model_by_object_id
from ..types import Order, OrderLine
from ..utils import (
    validate_product_is_published_in_channel,
    validate_variant_channel_listings,
)


class OrderLineInput(graphene.InputObjectType):
    quantity = graphene.Int(
        description="Number of variant items ordered.", required=True
    )


class OrderLineCreateInput(OrderLineInput):
    variant_id = graphene.ID(
        description="Product variant ID.", name="variantId", required=True
    )


class DraftOrderInput(InputObjectType):
    billing_address = AddressInput(description="Billing address of the customer.")
    user = graphene.ID(
        description="Customer associated with the draft order.", name="user"
    )
    user_email = graphene.String(description="Email address of the customer.")
    discount = PositiveDecimal(description="Discount amount for the order.")
    shipping_address = AddressInput(description="Shipping address of the customer.")
    shipping_method = graphene.ID(
        description="ID of a selected shipping method.", name="shippingMethod"
    )
    voucher = graphene.ID(
        description="ID of the voucher associated with the order.", name="voucher"
    )
    customer_note = graphene.String(
        description="A note from a customer. Visible by customers in the order summary."
    )
    channel_id = graphene.ID(description="ID of the channel associated with the order.")
    redirect_url = graphene.String(
        required=False,
        description=(
            "URL of a view where users should be redirected to "
            "see the order details. URL in RFC 1808 format."
        ),
    )


class DraftOrderCreateInput(DraftOrderInput):
    lines = NonNullList(
        OrderLineCreateInput,
        description=(
            "Variant line input consisting of variant ID and quantity of products."
        ),
    )


class DraftOrderCreate(ModelMutation, I18nMixin):
    class Arguments:
        input = DraftOrderCreateInput(
            required=True, description="Fields required to create an order."
        )

    class Meta:
        description = "Creates a new draft order."
        model = models.Order
        object_type = Order
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"

    @classmethod
    def clean_input(cls, info, instance, data):
        shipping_address = data.pop("shipping_address", None)
        billing_address = data.pop("billing_address", None)
        redirect_url = data.pop("redirect_url", None)
        channel_id = data.pop("channel_id", None)

        shipping_method = get_shipping_model_by_object_id(
            object_id=data.pop("shipping_method", None), raise_error=False
        )

        cleaned_input = super().clean_input(info, instance, data)

        channel = cls.clean_channel_id(info, instance, cleaned_input, channel_id)

        voucher = cleaned_input.get("voucher", None)
        if voucher:
            cls.clean_voucher(voucher, channel)

        if channel:
            cleaned_input["currency"] = channel.currency_code

        lines = data.pop("lines", None)
        cls.clean_lines(cleaned_input, lines, channel)

        cleaned_input["shipping_method"] = shipping_method
        cleaned_input["status"] = OrderStatus.DRAFT
        cleaned_input["origin"] = OrderOrigin.DRAFT
        display_gross_prices = info.context.site.settings.display_gross_prices
        cleaned_input["display_gross_prices"] = display_gross_prices

        cls.clean_addresses(
            info, instance, cleaned_input, shipping_address, billing_address
        )

        if redirect_url:
            cls.clean_redirect_url(redirect_url)
            cleaned_input["redirect_url"] = redirect_url

        return cleaned_input

    @classmethod
    def clean_channel_id(cls, info, instance, cleaned_input, channel_id):
        if channel_id:
            if hasattr(instance, "channel"):
                raise ValidationError(
                    {
                        "channel_id": ValidationError(
                            "Can't update existing order channel id.",
                            code=OrderErrorCode.NOT_EDITABLE.value,
                        )
                    }
                )
            else:
                channel = cls.get_node_or_error(info, channel_id, only_type=Channel)
                cleaned_input["channel"] = channel
                return channel

        else:
            return instance.channel if hasattr(instance, "channel") else None

    @classmethod
    def clean_voucher(cls, voucher, channel):
        if not voucher.channel_listings.filter(channel=channel).exists():
            raise ValidationError(
                {
                    "voucher": ValidationError(
                        "Voucher not available for this order.",
                        code=OrderErrorCode.NOT_AVAILABLE_IN_CHANNEL.value,
                    )
                }
            )

    @classmethod
    def clean_lines(cls, cleaned_input, lines, channel):
        if lines:
            variant_ids = [line.get("variant_id") for line in lines]
            variants = cls.get_nodes_or_error(variant_ids, "variants", ProductVariant)
            validate_product_is_published_in_channel(variants, channel)
            validate_variant_channel_listings(variants, channel)
            quantities = [line.get("quantity") for line in lines]
            if not all(quantity > 0 for quantity in quantities):
                raise ValidationError(
                    {
                        "quantity": ValidationError(
                            "Ensure this value is greater than 0.",
                            code=OrderErrorCode.ZERO_QUANTITY,
                        )
                    }
                )
            cleaned_input["variants"] = variants
            cleaned_input["quantities"] = quantities

    @classmethod
    def clean_addresses(
        cls, info, instance, cleaned_input, shipping_address, billing_address
    ):
        if shipping_address:
            shipping_address = cls.validate_address(
                shipping_address,
                address_type=AddressType.SHIPPING,
                instance=instance.shipping_address,
                info=info,
            )
            shipping_address = info.context.plugins.change_user_address(
                shipping_address, "shipping", user=instance
            )
            cleaned_input["shipping_address"] = shipping_address
        if billing_address:
            billing_address = cls.validate_address(
                billing_address,
                address_type=AddressType.BILLING,
                instance=instance.billing_address,
                info=info,
            )
            billing_address = info.context.plugins.change_user_address(
                billing_address, "billing", user=instance
            )
            cleaned_input["billing_address"] = billing_address

    @classmethod
    def clean_redirect_url(cls, redirect_url):
        try:
            validate_storefront_url(redirect_url)
        except ValidationError as error:
            error.code = OrderErrorCode.INVALID.value
            raise ValidationError({"redirect_url": error})

    @staticmethod
    def _save_addresses(info, instance: models.Order, cleaned_input):
        shipping_address = cleaned_input.get("shipping_address")
        if shipping_address:
            shipping_address.save()
            instance.shipping_address = shipping_address.get_copy()
        billing_address = cleaned_input.get("billing_address")
        if billing_address:
            billing_address.save()
            instance.billing_address = billing_address.get_copy()

    @staticmethod
    def _save_lines(info, instance, quantities, variants):
        if variants and quantities:
            lines: List[Tuple[int, OrderLine]] = []
            app = get_app(info.context.auth_token)
            for variant, quantity in zip(variants, quantities):
                line = add_variant_to_order(
                    instance,
                    variant,
                    quantity,
                    info.context.user,
                    app,
                    info.context.plugins,
                    info.context.site.settings,
                )
                lines.append((quantity, line))

            # New event
            events.order_added_products_event(
                order=instance,
                user=info.context.user,
                app=app,
                order_lines=lines,
            )

    @classmethod
    def _commit_changes(cls, info, instance, cleaned_input, is_new_instance):
        super().save(info, instance, cleaned_input)

        # Create draft created event if the instance is from scratch
        if is_new_instance:
            app = get_app(info.context.auth_token)
            events.draft_order_created_event(
                order=instance, user=info.context.user, app=app
            )

        instance.save(
            update_fields=["billing_address", "shipping_address", "updated_at"]
        )

    @classmethod
    def should_invalidate_prices(cls, instance, cleaned_input, is_new_instance) -> bool:
        # Force price recalculation for all new instances
        return is_new_instance

    @classmethod
    def save(cls, info, instance, cleaned_input):
        return cls._save_draft_order(
            info, instance, cleaned_input, is_new_instance=True
        )

    @classmethod
    @traced_atomic_transaction()
    def _save_draft_order(cls, info, instance, cleaned_input, *, is_new_instance):
        # Process addresses
        cls._save_addresses(info, instance, cleaned_input)

        # Save any changes create/update the draft
        cls._commit_changes(info, instance, cleaned_input, is_new_instance)

        try:
            # Process any lines to add
            cls._save_lines(
                info,
                instance,
                cleaned_input.get("quantities"),
                cleaned_input.get("variants"),
            )
        except TaxError as tax_error:
            raise ValidationError(
                "Unable to calculate taxes - %s" % str(tax_error),
                code=OrderErrorCode.TAX_ERROR.value,
            )

        if is_new_instance:
            transaction.on_commit(
                lambda: info.context.plugins.draft_order_created(instance)
            )

        else:
            transaction.on_commit(
                lambda: info.context.plugins.draft_order_updated(instance)
            )

        # Post-process the results
        updated_fields = ["weight", "search_vector", "updated_at"]
        if cls.should_invalidate_prices(instance, cleaned_input, is_new_instance):
            invalidate_order_prices(instance)
            updated_fields.append("should_refresh_prices")
        recalculate_order_weight(instance)
        update_order_search_vector(instance, save=False)

        instance.save(update_fields=updated_fields)
