from django import forms
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from zoneinfo import available_timezones

from core.models import Business, Membership, PendingRegistration, User
from core.tenancy import users_for_business
from followups.models import FollowUpTask
from leads.models import Activity, Lead, Product


TIMEZONE_REGIONS = (
    'Africa', 'America', 'Antarctica', 'Arctic', 'Asia', 'Atlantic',
    'Australia', 'Europe', 'Indian', 'Pacific',
)


def timezone_choices():
    """Return common IANA location zones in concise, browsable region groups."""
    available = available_timezones()
    choices = [('UTC', 'UTC (Coordinated Universal Time)')]
    for region in TIMEZONE_REGIONS:
        prefix = f'{region}/'
        region_zones = sorted(zone for zone in available if zone.startswith(prefix))
        if region_zones:
            choices.append((region, [(zone, zone.replace('_', ' ')) for zone in region_zones]))
    return choices


TIMEZONE_CHOICES = timezone_choices()
TIMEZONE_VALUES = {
    value
    for _group, group_choices in TIMEZONE_CHOICES
    if isinstance(group_choices, (list, tuple))
    for value, _label in group_choices
}
TIMEZONE_VALUES.add('UTC')


class AvatarRadioSelect(forms.RadioSelect):
    """Render assignee choices in a photo-and-name dropdown."""

    template_name = 'web/widgets/avatar_radio_select.html'

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        account = getattr(value, 'instance', None)
        is_empty_choice = str(value) == ''
        if account is None and is_empty_choice:
            account = getattr(self, 'empty_choice_user', None)

        option['attrs']['class'] = 'assignee-dropdown-input'
        # The custom widget supplies the clickable label and visible option
        # content itself.  Django's stock radio option template would add a
        # second label around the input, which is invalid nested markup.
        option['wrap_label'] = False
        option['is_empty_choice'] = is_empty_choice
        option['avatar_url'] = ''
        option['display_name'] = str(label)
        option['secondary_label'] = ''

        if account:
            if account.profile_picture:
                option['avatar_url'] = account.profile_picture.url
            account_name = account.get_full_name() or account.username
            if is_empty_choice:
                option['display_name'] = 'Assign to me'
                option['secondary_label'] = f'{account_name} · your account'
            else:
                option['display_name'] = account_name
                option['secondary_label'] = account.email or account.username
        return option

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        selected_option = None
        first_option = None
        for _group_name, options, _index in context['widget']['optgroups']:
            for option in options:
                first_option = first_option or option
                if option['selected']:
                    selected_option = option
        context['widget']['selected_option'] = selected_option or first_option
        return context


class ProfileForm(forms.ModelForm):
    profile_picture = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}),
    )

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'username', 'phone', 'profile_picture')

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        other_users = User.objects.exclude(pk=self.instance.pk)
        if other_users.filter(username__iexact=username).exists() or PendingRegistration.objects.filter(
            username__iexact=username,
        ).exists():
            raise ValidationError('This username is already in use.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        other_users = User.objects.exclude(pk=self.instance.pk)
        if other_users.filter(email__iexact=email).exists() or PendingRegistration.objects.filter(
            email__iexact=email,
        ).exists():
            raise ValidationError('An account already uses this email address.')
        return email

    def clean_profile_picture(self):
        picture = self.cleaned_data.get('profile_picture')
        if picture and picture.size > 5 * 1024 * 1024:
            raise ValidationError('Choose an image smaller than 5 MB.')
        return picture


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
        if PendingRegistration.objects.filter(email__iexact=email).exists():
            raise ValidationError('A verification code is already waiting for this address. Check your inbox or request a new code.')
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
        while User.objects.filter(username=username).exists() or PendingRegistration.objects.filter(username=username).exists():
            username = f'{base_username[:140]}{suffix}'
            suffix += 1
        return PendingRegistration.objects.create(
            business_name=self.cleaned_data['business_name'],
            industry=self.cleaned_data['industry'],
            timezone='Asia/Karachi',
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=make_password(self.cleaned_data['password']),
        )


class EmailVerificationResendForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'autocomplete': 'email',
        'placeholder': 'name@company.com',
    }))

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class EmailVerificationCodeForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'autocomplete': 'email',
        'placeholder': 'name@company.com',
    }))
    code = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'maxlength': '6',
            'pattern': '[0-9]{6}',
            'placeholder': '123456',
            'class': 'verification-code-input',
        }),
    )

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ValidationError('Enter the six-digit code from your email.')
        return code


class PasswordResetRequestForm(EmailVerificationResendForm):
    """Collect the email address to which a reset code should be sent."""


class PasswordResetConfirmForm(EmailVerificationCodeForm):
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'placeholder': 'Choose a new password',
        }),
    )
    new_password_confirmation = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'placeholder': 'Enter it again',
        }),
    )

    def clean_new_password(self):
        password = self.cleaned_data['new_password']
        validate_password(password)
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('new_password')
        confirmation = cleaned_data.get('new_password_confirmation')
        if password and confirmation and password != confirmation:
            self.add_error('new_password_confirmation', 'The two password fields did not match.')
        return cleaned_data


class CurrentPasswordChangeForm(forms.Form):
    """Let an authenticated person change their password without email recovery."""

    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'current-password',
            'placeholder': 'Enter your current password',
        }),
    )
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'placeholder': 'Choose a new password',
        }),
    )
    new_password_confirmation = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={
            'autocomplete': 'new-password',
            'placeholder': 'Enter it again',
        }),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_current_password(self):
        password = self.cleaned_data['current_password']
        if not self.user.check_password(password):
            raise ValidationError('Your current password is incorrect.')
        return password

    def clean_new_password(self):
        password = self.cleaned_data['new_password']
        validate_password(password, self.user)
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('new_password')
        confirmation = cleaned_data.get('new_password_confirmation')
        if password and confirmation and password != confirmation:
            self.add_error('new_password_confirmation', 'The two password fields did not match.')
        return cleaned_data


class TeamUserForm(forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=User.Role.choices)
    is_active = forms.BooleanField(required=False, initial=True)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'phone', 'password')

    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business or getattr(self.instance, 'business', None)
        self.membership = None
        if self.instance.pk:
            self.membership = Membership.objects.filter(
                user=self.instance,
                business=business,
            ).first()
            if self.membership:
                self.fields['role'].initial = self.membership.role
                self.fields['is_active'].initial = self.membership.is_active

    def clean_password(self):
        value = self.cleaned_data['password']
        if value:
            validate_password(value)
        elif not self.instance.pk:
            raise ValidationError('A password is required for a new team member.')
        return value

    def clean(self):
        cleaned_data = super().clean()
        if self.membership and self.membership.role == User.Role.OWNER and self.membership.is_active:
            role = cleaned_data.get('role', self.membership.role)
            is_active = cleaned_data.get('is_active', self.membership.is_active)
            if role != User.Role.OWNER or not is_active:
                owner_count = Membership.objects.filter(
                    business=self.business,
                    role=User.Role.OWNER,
                    is_active=True,
                    user__is_active=True,
                ).count()
                if owner_count == 1:
                    self.add_error('role', 'A business must keep at least one active owner.')
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data['password']:
            user.set_password(self.cleaned_data['password'])
        if commit:
            if self.business is None:
                raise ValueError('A business is required to save a team membership.')
            if not user.pk:
                # Keep the legacy link populated only for newly created
                # accounts during the transition to membership-based access.
                user.business = self.business
                user.role = self.cleaned_data['role']
                user.is_active = True
            elif self.cleaned_data.get('is_active'):
                # A legacy single-workspace deactivation set User.is_active.
                # Re-enabling this membership must make the account usable
                # again, while deactivation stays scoped to this workspace.
                user.is_active = True
            user.save()
            Membership.objects.update_or_create(
                user=user,
                business=self.business,
                defaults={
                    'role': self.cleaned_data['role'],
                    'is_active': self.cleaned_data.get('is_active', False),
                },
            )
        return user


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ('name', 'description', 'is_active')


class BusinessForm(forms.ModelForm):
    timezone = forms.ChoiceField(choices=TIMEZONE_CHOICES, label='Time zone')

    class Meta:
        model = Business
        fields = ('name', 'industry', 'timezone')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_timezone = self.instance.timezone if self.instance and self.instance.pk else ''
        if current_timezone and current_timezone not in TIMEZONE_VALUES:
            # Preserve a legacy value long enough for an owner to choose a
            # replacement rather than making an existing workspace unsaveable.
            self.fields['timezone'].choices = [
                ('Current value', [(current_timezone, current_timezone)]),
                *TIMEZONE_CHOICES,
            ]


class LeadQuickAddForm(forms.ModelForm):
    """The deliberately small, phone-friendly lead capture form."""

    class Meta:
        model = Lead
        fields = ('customer_name', 'phone', 'source', 'assigned_user')

    def __init__(self, *args, business, user, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.user = user
        self.fields['source'].initial = Lead.Source.PHONE_CALL
        self.fields['assigned_user'].queryset = users_for_business(business).exclude(
            pk=user.pk,
        ).order_by('first_name', 'username')
        self.fields['assigned_user'].required = False
        self.fields['assigned_user'].empty_label = 'Assign to me'
        self.fields['assigned_user'].widget = AvatarRadioSelect()
        self.fields['assigned_user'].widget.empty_choice_user = user
        self.fields['assigned_user'].widget.choices = self.fields['assigned_user'].choices
        if (role or user.role) == User.Role.SALESPERSON:
            self.fields.pop('assigned_user')


class LeadDetailsForm(forms.ModelForm):
    """Everything useful after a lead has been captured."""

    class Meta:
        model = Lead
        fields = ('customer_name', 'phone', 'email', 'source', 'product', 'quoted_price', 'assigned_user')

    def __init__(self, *args, business, user, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.for_business(business).filter(is_active=True).order_by('name')
        self.fields['product'].required = False
        self.fields['assigned_user'].queryset = users_for_business(business).order_by(
            'first_name', 'username',
        )
        self.fields['assigned_user'].required = True
        self.fields['assigned_user'].empty_label = None
        self.fields['assigned_user'].widget = AvatarRadioSelect()
        self.fields['assigned_user'].widget.choices = self.fields['assigned_user'].choices
        if (role or user.role) == User.Role.SALESPERSON:
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
