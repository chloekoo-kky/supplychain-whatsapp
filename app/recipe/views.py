'''
Views for the recipe APIs.
'''
from rest_framework import (
    viewsets,
    mixins,
    status,
)
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated

from core.models import (
    Recipe,
    Tag,
    Ingredient
)
from recipe import serializers


class RecipeViewSet(viewsets.ModelViewSet):
    '''View for manage recipe APIs.'''
    serializer_class = serializers.RecipeDetailSerializer
    queryset = Recipe.objects.all()
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        '''Retrieve recipes for authenticated user.'''
        return self.queryset.filter(user=self.request.user).order_by('-id')

    def get_serializer_class(self):
        '''Return the serializer class for request.'''
        if self.action == 'list':
            return serializers.RecipeSerializer
        elif self.action == 'upload-image':
            return serializers.RecipeImageSerializer

        return self.serializer_class

    def perform_create(self, serializer):
        '''Create a new recipe'''
        serializer.save(user=self.request.user)

    @action(methods=['POST'], detail=True, url_path='upload-image')
    def upload_image(self, request, pk=None):
        """Upload an image to recipe."""
        recipe = self.get_object()
        serializer = self.get_serializer(recipe, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            recipe.refresh_from_db() 
            print("Serializer data:", serializer.data)  # Debug: Check if 'image' exists
            return Response(serializer.data, status=status.HTTP_200_OK)

        print("Serializer errors:", serializer.errors)  # Debug: Check why validation fails
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class BaseIngredientAttrViewSet(mixins.DestroyModelMixin,
                 mixins.UpdateModelMixin,
                 mixins.ListModelMixin,
                 viewsets.GenericViewSet):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        '''Filter queryset to authenticated user.'''
        return self.queryset.filter(user=self.request.user).order_by('-name')


class TagViewSet(BaseIngredientAttrViewSet):
    '''Manage tags in the database.'''
    serializer_class = serializers.TagSerializer
    queryset = Tag.objects.all()


class IngredientViewSet(BaseIngredientAttrViewSet):
    """Manage ingredients in the database."""
    serializer_class = serializers.IngredientSerializer
    queryset = Ingredient.objects.all()