from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from core.models import Business, User
from followups.models import FollowUpTask
from leads.models import Activity, Lead, Product


class SignupForm(forms.Form):
    business_name = forms.CharField(max_length=255)
    industry = forms.ChoiceField(choices=Business.Industry.choices)
    owner_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account already uses this email. Sign in instead.')
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        validate_password(password)
        return password

    @transaction.atomic
    def save(self):
        owner_name = self.cleaned_data['owner_name'].strip()
        first_name, *remaining_names = owner_name.split(maxsplit=1)
        last_name = remaining_names[0] if remaining_names else ''
        email = self.cleaned_data['email']
        base_username = slugify(email.split('@', 1)[0]).replace('-', '')[:140] or 'owner'
        username = base_username
        suffix = 2
        while User.objects.filter(username=username).exists():
            username = f'{base_username[:140]}{suffix}'
            suffix += 1
        business = Business.objects.create(
            name=self.cleaned_data['business_name'], industry=self.cleaned_data['industry'], timezone='Asia/Karachi',
        )
        return User.objects.create_user(
            username=username, email=email, first_name=first_name, last_name=last_name,
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


class LeadQuickAddForm(forms.ModelForm):
    """The deliberately small, phone-friendly lead capture form."""

    class Meta:
        model = Lead
        fields = ('customer_name', 'phone', 'source', 'assigned_user')

    def __init__(self, *args, business, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.user = user
        self.fields['source'].initial = Lead.Source.PHONE_CALL
        self.fields['assigned_user'].queryset = User.objects.filter(
            business=business, is_active=True,
        ).order_by('first_name', 'username')
        self.fields['assigned_user'].required = False
        if user.role == User.Role.SALESPERSON:
            self.fields.pop('assigned_user')


class LeadDetailsForm(forms.ModelForm):
    """Everything useful after a lead has been captured."""

    class Meta:
        model = Lead
        fields = ('customer_name', 'phone', 'email', 'source', 'product', 'quoted_price', 'assigned_user')

    def __init__(self, *args, business, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.for_business(business).filter(is_active=True).order_by('name')
        self.fields['product'].required = False
        self.fields['assigned_user'].queryset = User.objects.filter(
            business=business, is_active=True,
        ).order_by('first_name', 'username')
        self.fields['assigned_user'].required = True
        if user.role == User.Role.SALESPERSON:
            self.fields.pop('assigned_user')


class LeadStageForm(forms.Form):
    stage = forms.ChoiceField(choices=Lead.Stage.choices)
    lost_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('stage') == Lead.Stage.LOST and not cleaned_data.get('lost_reason', '').strip():
            self.add_error('lost_reason', 'A lost lead needs a reason — add one below.')
        return cleaned_data


class ActivityForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ('kind', 'content')
        widgets = {
            'content': forms.Textarea(attrs={'rows': 3, 'placeholder': 'What happened? Keep it short and useful.'}),
        }


class LeadFollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUpTask
        fields = ('due_at', 'description')
        widgets = {
            'due_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'What needs to happen next?'}),
        }

    def clean_due_at(self):
        due_at = self.cleaned_data['due_at']
        if due_at <= timezone.now():
            raise ValidationError('Choose a future time for the next action.')
        return due_at


class TaskCompletionForm(forms.Form):
    next_due_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    next_description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'What is the next action?'}),
    )

    def clean_next_due_at(self):
        due_at = self.cleaned_data['next_due_at']
        if due_at <= timezone.now():
            raise ValidationError('Choose a future time for the next action.')
        return due_at


class TaskRescheduleForm(forms.Form):
    due_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))

    def clean_due_at(self):
        due_at = self.cleaned_data['due_at']
        if due_at <= timezone.now():
            raise ValidationError('Choose a future time for the next action.')
        return due_at
