from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Business, User
from leads.models import Product


class SignupForm(forms.Form):
    business_name = forms.CharField(max_length=255)
    industry = forms.ChoiceField(choices=Business.Industry.choices)
    timezone = forms.CharField(initial='Asia/Karachi', max_length=64)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise ValidationError('This username is already in use.')
        return username

    def clean_password(self):
        password = self.cleaned_data['password']
        validate_password(password)
        return password

    @transaction.atomic
    def save(self):
        business = Business.objects.create(
            name=self.cleaned_data['business_name'], industry=self.cleaned_data['industry'], timezone=self.cleaned_data['timezone'],
        )
        return User.objects.create_user(
            username=self.cleaned_data['username'], email=self.cleaned_data['email'], first_name=self.cleaned_data['first_name'],
            password=self.cleaned_data['password'], business=business, role=User.Role.OWNER,
        )


class TeamUserForm(forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'phone', 'role', 'is_active', 'password')

    def clean_password(self):
        value = self.cleaned_data['password']
        if value:
            validate_password(value)
        elif not self.instance.pk:
            raise ValidationError('A password is required for a new team member.')
        return value

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data['password']:
            user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ('name', 'description', 'is_active')


class BusinessForm(forms.ModelForm):
    class Meta:
        model = Business
        fields = ('name', 'industry', 'timezone')
