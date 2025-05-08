# '''
# Views for the recipe APIs.
# '''
# from drf_spectacular.utils import (
#     extend_schema_view,
#     extend_schema,
#     OpenApiParameter,
#     OpenApiTypes,
# )

# from rest_framework import (
#     viewsets,
#     mixins,
#     status,
# )
# from rest_framework.decorators import action
# from rest_framework.response import Response
# from rest_framework.authentication import TokenAuthentication
# from rest_framework.permissions import IsAuthenticated

# from core.models import (
#     Recipe,
#     Tag,
#     Product
# )
# from recipe import serializers


# @extend_schema_view(
#     list=extend_schema(
#         parameters=[
#             OpenApiParameter(
#                 'tags',
#                 OpenApiTypes.STR,
#                 description='Coma separated list of IDs to filter',
#             ),
#             OpenApiParameter(
#                 'products',
#                 OpenApiTypes.STR,
#                 description='Coma separated list of product IDs to filter',
#             )
#         ]
#     )
# )
# class RecipeViewSet(viewsets.ModelViewSet):
#     '''View for manage recipe APIs.'''
#     serializer_class = serializers.RecipeDetailSerializer
#     queryset = Recipe.objects.all()
#     authentication_classes = [TokenAuthentication]
#     permission_classes = [IsAuthenticated]

#     def _params_to_ints(self, qs):
#         """Convert a list of strings to integers."""
#         return [int(str_id) for str_id in qs.split(',')]

#     def get_queryset(self):
#         '''Retrieve recipes for authenticated user.'''
#         tags = self.request.query_params.get('tags')
#         products = self.request.query_params.get('products')
#         queryset = self.queryset
#         if tags:
#             tag_ids = self._params_to_ints(tags)
#             queryset = queryset.filter(tags__id__in=tag_ids)
#         if products:
#             product_ids = self._params_to_ints(products)
#             queryset = queryset.filter(products__id__in=product_ids)

#         return queryset.filter(
#             user=self.request.user
#         ).order_by('-id').distinct()

#     def get_serializer_class(self):
#         '''Return the serializer class for request.'''
#         if self.action == 'list':
#             return serializers.RecipeSerializer
#         elif self.action == 'upload_image':
#             return serializers.RecipeImageSerializer

#         return self.serializer_class

#     def perform_create(self, serializer):
#         '''Create a new recipe'''
#         serializer.save(user=self.request.user)

#     @action(methods=['POST'], detail=True, url_path='upload-image')
#     def upload_image(self, request, pk=None):
#         """Upload an image to recipe."""
#         recipe = self.get_object()
#         serializer = self.get_serializer(recipe, data=request.FILES)

#         if serializer.is_valid():
#             serializer.save()
#             recipe.refresh_from_db()
#             return Response(serializer.data, status=status.HTTP_200_OK)

#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# @extend_schema_view(
#     list=extend_schema(
#         parameters=[
#             OpenApiParameter(
#                 'assigned_only',
#                 OpenApiTypes.INT, enum=[0, 1],
#                 description='Filter by items assigned to recipes.'
#             )
#         ]
#     )
# )
# class BaseRecipeAttrViewSet(
#     mixins.DestroyModelMixin,
#     mixins.UpdateModelMixin,
#     mixins.ListModelMixin,
#     viewsets.GenericViewSet
# ):
#     authentication_classes = [TokenAuthentication]
#     permission_classes = [IsAuthenticated]

#     def get_queryset(self):
#         '''Filter queryset to authenticated user.'''
#         assigned_only = bool(
#             int(self.request.query_params.get('assigned_only', 0))
#         )
#         queryset = self.queryset
#         if assigned_only:
#             queryset = queryset.filter(recipe__isnull=False)

#         return queryset.filter(
#             user=self.request.user
#             ).order_by('-name').distinct()


# class TagViewSet(BaseRecipeAttrViewSet):
#     '''Manage tags in the database.'''
#     serializer_class = serializers.TagSerializer
#     queryset = Tag.objects.all()


# class ProductViewSet(BaseRecipeAttrViewSet):
#     """Manage products in the database."""
#     serializer_class = serializers.ProductSerializer
#     queryset = Product.objects.all()
