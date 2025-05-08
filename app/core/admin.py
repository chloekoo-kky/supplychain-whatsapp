'''
Django admin customization
'''

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm

from django.utils.translation import gettext_lazy as _

from core import models


class UserAdmin(BaseUserAdmin):
    '''define the admin pages for users.'''
    add_form = UserCreationForm
    form = UserChangeForm
    ordering = ['id']
    list_display = ['email', 'name', 'warehouse']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        (
            _('Permission'),
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser'
                )
            }
        ),
        (_('Logs'), {'fields': ('last_login',)}),
    )
    readonly_fields = ['last_login']
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email',
                'password1',
                'password2',
                'name',
                'warehouse',
                'is_active',
                'is_staff',
                'is_superuser',
            )
        }),
    )

    search_fields = ('email', 'name')




admin.site.register(models.User, UserAdmin)
