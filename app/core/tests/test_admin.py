'''
Tests for the Django admin modifications
'''
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import Client


class AdminSiteTests(TestCase):
    '''Tests for Django admin.'''

    def setUp(self):
        '''create user and client.'''
        self.client = Client()
        self.admin_user = get_user_model().objects.create_superuser(
            email='kwanyee.koo@gmail.com',
            password='pass123',
            )
        self.client.force_login(self.admin_user)
        self.user = get_user_model().objects.create_user(
            email='user@example.com',
            password='test124',
            name='Test User'
        )

    def test_users_list(self):
        '''test that users are listed on page'''
        url = reverse('admin:core_user_changelist')
        result = self.client.get(url)

        self.assertContains(result, self.user.name)
        self.assertContains(result, self.user.email)

    def test_edit_user_page(self):
        '''test the edit user page works'''
        url = reverse('admin:core_user_change', args=[self.user.id])
        result = self.client.get(url)

        self.assertEqual(result.status_code, 200)

    def test_create_user_page(self):
        '''test the create user page works'''
        url = reverse('admin:core_user_add')
        result = self.client.get(url)

        self.assertEqual(result.status_code, 200)
