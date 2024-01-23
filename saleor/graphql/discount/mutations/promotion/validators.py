from typing import TYPE_CHECKING, Union

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from graphene.utils.str_converters import to_camel_case

from .....discount import PromotionType, RewardType, RewardValueType
from .....discount.models import PromotionRule
from ....core.validators import validate_price_precision

if TYPE_CHECKING:
    from decimal import Decimal


def clean_promotion_rule(
    cleaned_input, promotion_type, errors, error_class, index=None, instance=None
):
    catalogue_predicate = cleaned_input.get("catalogue_predicate")
    order_predicate = cleaned_input.get("order_predicate")
    if instance:
        catalogue_predicate = catalogue_predicate or instance.catalogue_predicate
        order_predicate = order_predicate or instance.order_predicate
    invalid_predicates = _clean_predicates(
        catalogue_predicate,
        order_predicate,
        errors,
        error_class,
        index,
        promotion_type,
    )
    if not invalid_predicates:
        channel_currencies = _get_channel_currencies(cleaned_input, instance)
        _clean_catalogue_predicate(
            cleaned_input, catalogue_predicate, errors, error_class, index, instance
        )
        _clean_order_predicate(
            cleaned_input,
            order_predicate,
            channel_currencies,
            errors,
            error_class,
            index,
            instance,
        )
        _clean_reward(
            cleaned_input,
            catalogue_predicate,
            order_predicate,
            channel_currencies,
            errors,
            error_class,
            index,
            instance,
        )

    return cleaned_input


def _clean_predicates(
    catalogue_predicate,
    order_predicate,
    errors,
    error_class,
    index,
    promotion_type,
):
    """Validate if predicates are provided and if they aren't mixed.

    - At least one predicate is required - `catalogue` or `order` predicate.
    - Promotion can have only one predicate type, raise error if there are mixed.
    """
    invalid_predicates = False
    if promotion_type == PromotionType.CATALOGUE:
        if catalogue_predicate is None:
            errors["catalogue_predicate"].append(
                ValidationError(
                    message=(
                        "For `catalogue` predicate type, `cataloguePredicate` "
                        "must be provided."
                    ),
                    code=error_class.REQUIRED.value,
                    params={"index": index} if index is not None else {},
                )
            )
            invalid_predicates = True
        if order_predicate:
            errors["order_predicate"].append(
                ValidationError(
                    message=(
                        "`Order` predicate cannot be provided for promotion rule with "
                        "`catalogue` predicate type."
                    ),
                    code=error_class.INVALID.value,
                    params={"index": index} if index is not None else {},
                )
            )
            invalid_predicates = True
    if promotion_type == PromotionType.ORDER:
        if order_predicate is None:
            errors["order_predicate"].append(
                ValidationError(
                    message=(
                        "For `order` predicate type, `orderPredicate` "
                        "must be provided."
                    ),
                    code=error_class.REQUIRED.value,
                    params={"index": index} if index is not None else {},
                )
            )
            invalid_predicates = True
        if catalogue_predicate:
            errors["catalogue_predicate"].append(
                ValidationError(
                    message=(
                        "`Catalogue` predicate cannot be provided for promotion rule "
                        "with `order` predicate type."
                    ),
                    code=error_class.INVALID.value,
                    params={"index": index} if index is not None else {},
                )
            )
            invalid_predicates = True
    return invalid_predicates


def _clean_catalogue_predicate(
    cleaned_input, catalogue_predicate, errors, error_class, index, instance
):
    """Clean and validate catalogue predicate.

    - Reward type can't be specified for rule with catalogue predicate.
    """

    if not catalogue_predicate:
        return

    reward_type = cleaned_input.get("reward_type")
    if instance and "reward_type" not in cleaned_input:
        reward_type = reward_type or instance.reward_type
    if reward_type:
        errors["reward_type"].append(
            ValidationError(
                message=(
                    "The rewardType can't be specified for rule "
                    "with cataloguePredicate."
                ),
                code=error_class.INVALID.value,
                params={"index": index} if index is not None else {},
            )
        )
    else:
        if "catalogue_predicate" not in cleaned_input:
            return
        try:
            cleaned_input["catalogue_predicate"] = clean_predicate(
                catalogue_predicate,
                error_class,
                index,
            )
        except ValidationError as error:
            errors["catalogue_predicate"].append(error)


def _clean_order_predicate(
    cleaned_input,
    order_predicate,
    channel_currencies,
    errors,
    error_class,
    index,
    instance,
):
    """Clean and validate order predicate.

    - Reward type is required for rule with order predicate.
    - Price based predicates are allowed only for rules with one currency.
    - Rules number with order predicate doesn't exceed the limit.
    """
    if not order_predicate:
        return

    reward_type = cleaned_input.get("reward_type")
    if "reward_type" not in cleaned_input and instance:
        reward_type = reward_type or instance.reward_type
    if not reward_type:
        errors["reward_type"].append(
            ValidationError(
                message="The rewardType is required when orderPredicate is provided.",
                code=error_class.REQUIRED.value,
                params={"index": index} if index is not None else {},
            )
        )
        return

    price_based_predicate = any(
        field in str(order_predicate)
        for field in [
            "base_subtotal_price",
            "baseSubtotalPrice",
            "base_total_price",
            "baseTotalPrice",
        ]
    )
    if len(channel_currencies) > 1 and price_based_predicate:
        error_field = "channels"
        if instance:
            error_field = (
                "add_channels" if "add_channels" in cleaned_input else "order_predicate"
            )
        errors[error_field].append(
            ValidationError(
                message=(
                    "For price based predicates, all channels must have "
                    "the same currency."
                ),
                code=error_class.MULTIPLE_CURRENCIES_NOT_ALLOWED.value,
                params={"index": index} if index is not None else {},
            )
        )
        return

    if "order_predicate" not in cleaned_input:
        return

    order_rules_count = PromotionRule.objects.filter(~Q(order_predicate={})).count()
    rules_limit = settings.ORDER_RULES_LIMIT
    if order_rules_count >= int(rules_limit):
        errors["order_predicate"].append(
            ValidationError(
                message="Number of rules with orderPredicate has reached the limit.",
                code=error_class.RULES_NUMBER_LIMIT.value,
                params={
                    "rules_limit": rules_limit,
                    "exceed_by": 1,
                },
            )
        )
        return

    try:
        cleaned_input["order_predicate"] = clean_predicate(
            order_predicate,
            error_class,
            index,
        )
    except ValidationError as error:
        errors["order_predicate"].append(error)
        return

    if reward_type == RewardType.GIFT:
        _clean_gift_rule(cleaned_input, errors, error_class, index)


def _clean_gift_rule(cleaned_input, errors, error_class, index):
    if cleaned_input.get("reward_value"):
        errors["reward_value"].append(
            ValidationError(
                message=(
                    "The rewardValue field must be empty "
                    "when rewardType is set to GIFT."
                ),
                code=error_class.INVALID.value,
                params={"index": index} if index is not None else {},
            )
        )

    if cleaned_input.get("reward_value_type"):
        errors["reward_value_type"].append(
            ValidationError(
                message=(
                    "The rewardValueType field must be empty "
                    "when rewardType is set to GIFT."
                ),
                code=error_class.INVALID.value,
                params={"index": index} if index is not None else {},
            )
        )

    gifts = cleaned_input.get("gifts")
    if not gifts:
        errors["gifts"].append(
            ValidationError(
                message="The gifts field is required when rewardType is set to GIFT.",
                code=error_class.REQUIRED.value,
                params={"index": index} if index is not None else {},
            )
        )
        return

    for gift in gifts:
        model_name = gift.__class__.__name__
        if model_name != "ProductVariant":
            errors["gifts"].append(
                ValidationError(
                    message=f"Gift IDs must be a type of ProductVariant, not a {model_name} type.",
                    code=error_class.INVALID_GIFT_TYPE.value,
                    params={"index": index} if index is not None else {},
                )
            )
            return


def _clean_reward(
    cleaned_input,
    catalogue_predicate,
    order_predicate,
    currencies,
    errors,
    error_class,
    index,
    instance,
):
    """Validate reward value and reward value type.

    - Fixed reward value type requires channels with the same currency code
    to be specified.
    - Validate price precision for fixed reward value.
    - Check if percentage reward value is not above 100.
    """
    if (
        instance
        and "reward_value" not in cleaned_input
        and "reward_value_type" not in cleaned_input
    ) or cleaned_input.get("reward_type") == RewardType.GIFT:
        return

    reward_value = cleaned_input.get("reward_value")
    reward_value_type = cleaned_input.get("reward_value_type")
    if instance:
        reward_value = (
            reward_value if "reward_value" in cleaned_input else instance.reward_value
        )
        reward_value_type = (
            reward_value_type
            if "reward_value_type" in cleaned_input
            else instance.reward_value_type
        )

    if reward_value_type is None and (catalogue_predicate or order_predicate):
        errors["reward_value_type"].append(
            ValidationError(
                message=(
                    "The rewardValueType is required when "
                    "cataloguePredicate or orderPredicate is provided."
                ),
                code=error_class.REQUIRED.value,
                params={"index": index} if index is not None else {},
            )
        )
    if reward_value is None and (catalogue_predicate or order_predicate):
        errors["reward_value"].append(
            ValidationError(
                message=(
                    "The rewardValue is required when "
                    "cataloguePredicate or orderPredicate is provided."
                ),
                code=error_class.REQUIRED.value,
                params={"index": index} if index is not None else {},
            )
        )
    if reward_value and reward_value_type:
        _clean_reward_value(
            cleaned_input,
            reward_value,
            reward_value_type,
            currencies,
            errors,
            error_class,
            index,
            instance,
        )


def _get_channel_currencies(cleaned_input, instance) -> set[str]:
    """Get currencies for which the rules apply."""
    if not instance:
        channels = cleaned_input.get("channels", [])
        return {channel.currency_code for channel in channels}

    channel_currencies = set(instance.channels.values_list("currency_code", flat=True))
    if remove_channels := cleaned_input.get("remove_channels"):
        channel_currencies = channel_currencies - {
            channel.currency_code for channel in remove_channels
        }
    if add_channels := cleaned_input.get("add_channels"):
        channel_currencies.update([channel.currency_code for channel in add_channels])

    return channel_currencies


def _clean_reward_value(
    cleaned_input,
    reward_value,
    reward_value_type,
    channel_currencies,
    errors,
    error_class,
    index,
    instance,
):
    """Validate reward value and reward value type.

    - The Fixed reward value type requires channels with the same currency code.
    - Validate price precision for fixed reward value.
    - Check if percentage reward value is not above 100.
    """
    if reward_value_type == RewardValueType.FIXED:
        if "channels" in errors:
            return
        if not channel_currencies:
            error_field = "channels"
            if instance:
                error_field = (
                    "reward_value_type"
                    if "reward_value_type" in cleaned_input
                    else "remove_channels"
                )
            errors[error_field].append(
                ValidationError(
                    "Channels must be specified for FIXED rewardValueType.",
                    code=error_class.MISSING_CHANNELS.value,
                    params={"index": index} if index is not None else {},
                )
            )
            return
        if len(channel_currencies) > 1:
            error_code = error_class.MULTIPLE_CURRENCIES_NOT_ALLOWED.value
            error_field = "reward_value_type"
            if instance:
                error_field = (
                    "reward_value_type"
                    if "reward_value_type" in cleaned_input
                    else "add_channels"
                )
            errors[error_field].append(
                ValidationError(
                    "For FIXED rewardValueType, all channels must have "
                    "the same currency.",
                    code=error_code,
                    params={"index": index} if index is not None else {},
                )
            )
            return

        currency = channel_currencies.pop()
        try:
            clean_fixed_discount_value(
                reward_value,
                error_class.INVALID_PRECISION.value,
                currency,
                index,
            )
        except ValidationError as error:
            errors["reward_value"].append(error)

    elif reward_value_type == RewardValueType.PERCENTAGE:
        try:
            clean_percentage_discount_value(
                reward_value, error_class.INVALID.value, index
            )
        except ValidationError as error:
            errors["reward_value"].append(error)


def clean_predicate(predicate, error_class, index=None):
    """Validate operators and convert snake cases keys into camel case.

    Operators cannot be mixed with other filter inputs. There could be only
    one operator on each level.
    """
    if isinstance(predicate, list):
        return [
            clean_predicate(item, error_class, index)
            if isinstance(item, (dict, list))
            else item
            for item in predicate
        ]
    # when any operator appear there cannot be any more data in filter input
    if _contains_operator(predicate) and len(predicate.keys()) > 1:
        raise ValidationError(
            "Cannot mix operators with other filter inputs.",
            code=error_class.INVALID.value,
            params={"index": index} if index is not None else {},
        )
    return {
        to_camel_case(key): clean_predicate(value, error_class, index)
        if isinstance(value, (dict, list))
        else value
        for key, value in predicate.items()
    }


def _contains_operator(input: dict[str, Union[dict, str]]):
    return any([operator in input for operator in ["AND", "OR"]])


def clean_fixed_discount_value(
    reward_value: "Decimal", error_code: str, currency_code: str, index=None
):
    try:
        validate_price_precision(reward_value, currency_code)
    except ValidationError:
        raise ValidationError(
            "Invalid amount precision.",
            code=error_code,
            params={"index": index} if index is not None else {},
        )


def clean_percentage_discount_value(
    reward_value: "Decimal", error_code: str, index=None
):
    if reward_value > 100:
        raise ValidationError(
            "Invalid percentage value.",
            code=error_code,
            params={"index": index} if index is not None else {},
        )
