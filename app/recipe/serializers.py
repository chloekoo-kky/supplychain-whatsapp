# '''
# Serializers for recipe APIs
# '''
# from rest_framework import serializers
# from django.db import IntegrityError

# from core.models import (
#     Recipe,
#     Tag,
#     Product,
# )


# class ProductSerializer(serializers.ModelSerializer):
#     """Serializer for ingredients"""

#     class Meta:
#         model = Product
#         fields = ['id', 'name', 'supplier', 'price']
#         read_only_fields = ['id']


# class TagSerializer(serializers.ModelSerializer):
#     '''Serializer for tags.'''

#     class Meta:
#         model = Tag
#         fields = ['id', 'name']
#         read_only_fields = ['id']


# class RecipeSerializer(serializers.ModelSerializer):
#     '''Serializer for recipes.'''
#     tags = TagSerializer(many=True, required=False)
#     products = ProductSerializer(many=True, required=False)

#     class Meta:
#         model = Recipe
#         fields = [
#             'id', 'title', 'products', 'est_price', 'tags',
#             ]
#         read_only_fields = ['id']

#     def _get_or_create_tags(self, tags, recipe):
#         """Handle getting or creating tags as needed."""
#         auth_user = self.context['request'].user
#         for tag in tags:
#             try:
#                 tag_obj, created = Tag.objects.get_or_create(
#                     user=auth_user,
#                     **tag,
#                 )
#                 recipe.tags.add(tag_obj)
#             except IntegrityError as e:
#                 raise serializers.ValidationError(
#                     f"Error creating tag: {str(e)}"
#                     )

#     def _get_or_create_products(self, products, recipe):
#         """Handle getting or creating products as needed"""
#         auth_user = self.context['request'].user
#         for product in products:
#             try:
#                 product_obj, created = Product.objects.get_or_create(
#                     user=auth_user,
#                     **product,
#                 )
#                 recipe.products.add(product_obj)
#             except IntegrityError as e:
#                 raise serializers.ValidationError(
#                     f"Error creating products: {str(e)}"
#                     )

#     def create(self, validated_data):
#         '''Create a recipe.'''
#         tags = validated_data.pop('tags', [])
#         products = validated_data.pop('products', [])
#         recipe = Recipe.objects.create(**validated_data)
#         self._get_or_create_tags(tags, recipe)
#         self._get_or_create_products(products, recipe)

#         return recipe

#     def update(self, instance, validated_data):
#         """Update recipe."""
#         tags = validated_data.pop('tags', None)
#         products = validated_data.pop('products', None)
#         if tags is not None:
#             instance.tags.clear()
#             self._get_or_create_tags(tags, instance)

#         if products is not None:
#             instance.products.clear()
#             self._get_or_create_ingredients(products, instance)

#         for attr, value in validated_data.items():
#             setattr(instance, attr, value)

#         instance.save()
#         return instance


# class RecipeDetailSerializer(RecipeSerializer):

#     class Meta(RecipeSerializer.Meta):
#         fields = RecipeSerializer.Meta.fields + ['notes', 'image']


# class RecipeImageSerializer(serializers.ModelSerializer):
#     """Serializer for uploading images to recipes."""

#     class Meta:
#         model = Recipe
#         fields = ['id', 'image']
#         read_only_fields = ['id']
#         extra_kwargs = {'image': {'required': 'True'}}
