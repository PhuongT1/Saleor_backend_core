from rest_framework.decorators import api_view, authentication_classes
from rest_framework.exceptions import ParseError
from rest_framework import status
from rest_framework.response import Response
from ..order.models import Order, DeliveryGroup
from ..product.models import Product, Stock
from .authentication import WombatAuthentication
from .serializers import (OrderSerializer, ProductSerializer,
                          GetWebhookSerializer,
                          StockSerializer, AddProductWebhookSerializer,
                          GetInventoryWebhookSerializer,
                          BaseWombatGetWebhookSerializer,
                          DeliveryGroupSerializer)


def get_serialized_data(request_serializer, queryset, serializer, wombat_name):
    if not request_serializer.is_valid():
        raise ParseError()
    request_id = request_serializer.data.get('request_id')
    query_filter = request_serializer.get_query_filter()
    data = queryset.filter(query_filter)
    serialized = serializer(data, many=True)
    response = {
        'request_id': request_id,
        wombat_name: serialized.data
    }
    return Response(data=response, status=status.HTTP_200_OK)


@api_view(['POST'])
@authentication_classes((WombatAuthentication,))
def get_orders_webhook(request):
    request_serializer = GetWebhookSerializer(
        data=request.data, since_query_field='last_status_change')
    return get_serialized_data(request_serializer,
                               queryset=Order.objects.with_all_related(),
                               serializer=OrderSerializer,
                               wombat_name='orders')


@api_view(['POST'])
@authentication_classes((WombatAuthentication,))
def get_products_webhook(request):
    request_serializer = GetWebhookSerializer(
        data=request.data, since_query_field='updated_at')
    return get_serialized_data(request_serializer,
                               queryset=Product.objects.with_all_related(),
                               serializer=ProductSerializer,
                               wombat_name='products')


@api_view(['POST'])
@authentication_classes((WombatAuthentication,))
def add_product_webhook(request):
    serializer = AddProductWebhookSerializer(data=request.data)
    response = {}
    valid = serializer.is_valid()
    response['request_id'] = serializer.data['request_id']
    if valid:
        product = serializer.save()
        response['summary'] = 'Product %s was added' % (product.name, )
        return Response(data=response, status=status.HTTP_200_OK)
    else:
        response['summary'] = 'Validation error - %s' % (serializer.errors, )
    return Response(data=response, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes((WombatAuthentication,))
def update_product_webhook(request):
    serializer = AddProductWebhookSerializer(data=request.data)
    response = {}
    if serializer.is_valid():
        try:
            product = Product.objects.get(pk=serializer.data['product']['id'])
        except Product.DoesNotExist:
            summary = 'Product does not exist'
            code = status.HTTP_500_INTERNAL_SERVER_ERROR
        else:
            code = status.HTTP_200_OK
            update_serializer = AddProductWebhookSerializer(instance=product,
                                                            data=request.data)
            if update_serializer.is_valid():
                update_serializer.save()
                summary = 'Product updated'
            else:
                summary = 'Validation error - %s' % (update_serializer.errors, )
                code = status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
        summary = 'Validation error - %s' % (serializer.errors, )
    response['request_id'] = serializer.data['request_id']
    response['summary'] = summary

    return Response(data=response, status=code)

@api_view(['POST'])
@authentication_classes((WombatAuthentication,))
def get_inventory_webhook(request):
    request_serializer = GetInventoryWebhookSerializer(data=request.data)
    return get_serialized_data(request_serializer,
                               queryset=Stock.objects.all(),
                               serializer=StockSerializer,
                               wombat_name='inventories')


@api_view(['POST'])
@authentication_classes((WombatAuthentication,))
def get_shipments_webhook(request):
    request_serializer = GetWebhookSerializer(data=request.data,
                                              since_query_field='last_updated')
    return get_serialized_data(request_serializer,
                               queryset=DeliveryGroup.objects.all(),
                               serializer=DeliveryGroupSerializer,
                               wombat_name='shipments')
