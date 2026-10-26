from datetime import datetime, timedelta
from pathlib import Path

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from zoneinfo import ZoneInfo, available_timezones

from core.captcha import captcha_enabled, verify_turnstile
from core.membership_services import update_membership
from core.models import Business, EmailCampaign, EmailThread, MailboxConnection, Membership, PendingRegistration, User
from core.security import clear_login_failures, client_ip, login_attempt_state, record_failed_login
from core.tenancy import users_for_business
from followups.models import FollowUpTask
from leads.models import Activity, Lead, Product, SiteVisit
from leads.services import related_leads_for_customer


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


class BusinessLocalDateTimeField(forms.DateTimeField):
    """Parse and render a ``datetime-local`` value in one business timezone."""

    def __init__(self, business_timezone, *args, **kwargs):
        self.business_timezone = ZoneInfo(business_timezone)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        # Django's DateTimeField interprets naive browser input in the active
        # process timezone. Override it narrowly so appointment times always
        # mean the active business's local calendar time.
        with timezone.override(self.business_timezone):
            return super().to_python(value)

    def prepare_value(self, value):
        if isinstance(value, datetime):
            if timezone.is_aware(value):
                value = timezone.localtime(value, timezone=self.business_timezone)
            return value.strftime('%Y-%m-%dT%H:%M')
        return super().prepare_value(value)


class ProtectedAuthenticationForm(AuthenticationForm):
    """Normal Django login with progressive delay and optional CAPTCHA checks."""

    captcha_token = forms.CharField(required=False, widget=forms.HiddenInput)
    captcha_required = False
    protection_error = ''

    def _credential(self) -> str:
        credential = self.cleaned_data.get('username')
        if credential is None and self.is_bound:
            credential = self.data.get('username', '')
        return str(credential or '').strip()

    def clean(self):
        credential = self._credential()
        if credential:
            state = login_attempt_state(self.request, credential)
            self.captcha_required = state.captcha_required and captcha_enabled()
            if state.retry_after:
                self.protection_error = (
                    f'Too many sign-in attempts. Please try again in {state.retry_after} seconds.'
                )
                raise ValidationError(self.protection_error)
            if self.captcha_required and not verify_turnstile(
                self.cleaned_data.get('captcha_token', ''),
                client_ip(self.request),
            ):
                self.protection_error = 'Please complete the security check before signing in.'
                raise ValidationError(self.protection_error)

        try:
            cleaned_data = super().clean()
        except ValidationError:
            if credential:
                state = record_failed_login(self.request, credential)
                self.captcha_required = state.captcha_required and captcha_enabled()
            raise

        if credential:
            clear_login_failures(self.request, credential)
        return cleaned_data


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
        while User.objects.filter(username__iexact=username).exists() or PendingRegistration.objects.filter(
            username__iexact=username,
        ).exists():
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
    GLOBAL_ACCOUNT_FIELDS = ('username', 'first_name', 'last_name', 'email', 'phone')

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
            # A person can join several workspaces. Workspace owners may
            # administer only the role/status of this membership; profile and
            # password changes belong to the account owner.
            self.fields.pop('password')
            for field_name in self.GLOBAL_ACCOUNT_FIELDS:
                self.fields[field_name].disabled = True
                self.fields[field_name].help_text = 'Managed by this account owner.'

    def clean_password(self):
        value = self.cleaned_data['password']
        if value:
            validate_password(value)
        elif not self.instance.pk:
            raise ValidationError('A password is required for a new team member.')
        return value

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
        if not email:
            return email
        other_users = User.objects.exclude(pk=self.instance.pk)
        if other_users.filter(email__iexact=email).exists() or PendingRegistration.objects.filter(
            email__iexact=email,
        ).exists():
            raise ValidationError('An account already uses this email address.')
        return email

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
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        if commit:
            if self.business is None:
                raise ValueError('A business is required to save a team membership.')
            with transaction.atomic():
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
                if user.pk and self.membership:
                    update_membership(
                        membership_id=self.membership.id,
                        business=self.business,
                        role=self.cleaned_data['role'],
                        is_active=self.cleaned_data.get('is_active', False),
                    )
                else:
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
    def __init__(self, *args, business=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business

    class Meta:
        model = Product
        fields = ('name', 'description', 'is_active')

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if self.business is None:
            return name
        products = Product.objects.for_business(self.business).filter(name__iexact=name)
        if self.instance.pk:
            products = products.exclude(pk=self.instance.pk)
        if products.exists():
            raise ValidationError('A product with this name already exists in this business.')
        return name


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

    confirm_duplicate = forms.BooleanField(
        required=False,
        label='I checked the possible duplicates and want to add this lead anyway',
    )

    class Meta:
        model = Lead
        fields = ('customer_name', 'phone', 'email', 'source', 'assigned_user')

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

        self.fields['confirm_duplicate'].widget.attrs['class'] = 'duplicate-confirmation'
        self.duplicate_leads = []

    def clean(self):
        cleaned_data = super().clean()
        self.duplicate_leads = list(related_leads_for_customer(
            business=self.business,
            phone=cleaned_data.get('phone', ''),
            email=cleaned_data.get('email', ''),
        )[:5])
        if self.duplicate_leads and not cleaned_data.get('confirm_duplicate'):
            raise ValidationError(
                'A lead with this phone number or email already exists. Review the matches below, '
                'or confirm that you want to add another lead.'
            )
        return cleaned_data


class LeadImportForm(forms.Form):
    csv_file = forms.FileField(
        label='CSV file',
        help_text=(
            'Required columns: customer_name, phone. Optional: email, source, stage, '
            'quoted_price, product, assigned_user, lost_reason.'
        ),
        widget=forms.ClearableFileInput(attrs={'accept': '.csv,text/csv'}),
    )


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(item, initial) for item in data]
        return [single_file_clean(data, initial)] if data else []


class EmailCampaignForm(forms.ModelForm):
    attachments = MultipleFileField(
        required=False,
        label='Attachments',
        help_text='Up to 10 files, 10 MB each and 25 MB total. PDF, Office, CSV, TXT, ZIP, and common image files are supported.',
    )
    scheduled_at = forms.DateTimeField(
        required=False,
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    test_email = forms.EmailField(
        required=False,
        label='Test recipient',
        help_text='Send a preview to this address before queueing the campaign.',
    )

    class Meta:
        model = EmailCampaign
        fields = (
            'subject', 'preheader', 'body', 'html_body', 'reply_to', 'scheduled_at',
            'filter_stage', 'filter_source', 'filter_assigned_user',
        )
        widgets = {
            'body': forms.Textarea(attrs={
                'rows': 12,
                'placeholder': 'Write the plain-text version of your message.',
            }),
            'html_body': forms.Textarea(attrs={
                'rows': 10,
                'placeholder': 'Optional HTML version. Leave blank to generate a clean HTML email from the message above.',
            }),
        }
        labels = {
            'preheader': 'Inbox preview text',
            'html_body': 'HTML version (optional)',
            'reply_to': 'Reply-to email',
            'scheduled_at': 'Schedule for',
            'filter_stage': 'Stage filter',
            'filter_source': 'Source filter',
            'filter_assigned_user': 'Assigned salesperson',
        }

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields['scheduled_at'] = BusinessLocalDateTimeField(
            getattr(business, 'timezone', 'UTC'),
            required=False,
            input_formats=['%Y-%m-%dT%H:%M'],
            widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        )
        self.fields['filter_stage'].choices = [('', 'All stages'), *Lead.Stage.choices]
        self.fields['filter_source'].choices = [('', 'All sources'), *Lead.Source.choices]
        self.fields['filter_assigned_user'].queryset = users_for_business(business).order_by(
            'first_name', 'last_name', 'username',
        )
        self.fields['filter_assigned_user'].required = False
        self.fields['subject'].label = 'Email subject'
        self.fields['body'].label = 'Message'

    def clean(self):
        cleaned_data = super().clean()
        if self.data.get('action') == 'test' and not cleaned_data.get('test_email'):
            self.add_error('test_email', 'Enter an address for the test email.')
        scheduled_at = cleaned_data.get('scheduled_at')
        if self.data.get('action') != 'test' and scheduled_at and scheduled_at <= timezone.now():
            self.add_error('scheduled_at', 'Choose a future time or leave this blank to send now.')
        attachments = cleaned_data.get('attachments', [])
        allowed_extensions = {
            '.csv', '.doc', '.docx', '.gif', '.jpeg', '.jpg', '.json', '.pdf',
            '.png', '.ppt', '.pptx', '.txt', '.webp', '.xls', '.xlsx', '.zip',
        }
        max_file_size = 10 * 1024 * 1024
        max_total_size = 25 * 1024 * 1024
        if len(attachments) > 10:
            self.add_error('attachments', 'Attach no more than 10 files.')
        if sum(uploaded.size for uploaded in attachments) > max_total_size:
            self.add_error('attachments', 'Attachments must be 25 MB or smaller in total.')
        for uploaded in attachments:
            if uploaded.size > max_file_size:
                self.add_error('attachments', f'{uploaded.name} is larger than the 10 MB per-file limit.')
            if Path(uploaded.name).suffix.casefold() not in allowed_extensions:
                self.add_error('attachments', f'{uploaded.name} is not a supported attachment type.')
        return cleaned_data


class LeadBulkActionForm(forms.Form):
    action = forms.ChoiceField(choices=(
        ('assign', 'Assign selected leads'),
        ('stage', 'Move selected leads'),
    ))
    assigned_user = forms.ModelChoiceField(queryset=User.objects.none(), required=False)
    stage = forms.ChoiceField(choices=Lead.Stage.choices, required=False)
    lost_reason = forms.CharField(required=False, widget=forms.TextInput())

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_user'].queryset = users_for_business(business).order_by(
            'first_name', 'last_name', 'username',
        )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('action') == 'assign' and not cleaned_data.get('assigned_user'):
            self.add_error('assigned_user', 'Choose a teammate.')
        if cleaned_data.get('action') == 'stage':
            stage = cleaned_data.get('stage')
            if not stage:
                self.add_error('stage', 'Choose a stage.')
            elif stage == Lead.Stage.LOST and not cleaned_data.get('lost_reason', '').strip():
                self.add_error('lost_reason', 'A lost reason is required when moving leads to Lost.')
        return cleaned_data


class LeadDetailsForm(forms.ModelForm):
    """Everything useful after a lead has been captured."""

    class Meta:
        model = Lead
        fields = (
            'customer_name', 'phone', 'email', 'email_marketing_opt_out', 'source',
            'product', 'quoted_price', 'assigned_user',
        )

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


class SiteVisitScheduleForm(forms.ModelForm):
    """Schedule or reschedule one appointment in the business's local time."""

    class Meta:
        model = SiteVisit
        fields = ('scheduled_at', 'address', 'assigned_user', 'reminder_enabled')
        widgets = {
            'address': forms.Textarea(attrs={
                'rows': 2,
                'placeholder': 'Optional address, landmark, or access note',
            }),
        }

    def __init__(
        self,
        *args,
        business,
        user,
        role=None,
        lead=None,
        allow_assignee_change=True,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.business = business
        self.user = user
        self.role = role or user.role
        self.lead = lead or getattr(self.instance, 'lead', None)
        self.allow_assignee_change = allow_assignee_change
        scheduled_initial = self.initial.get('scheduled_at')
        if not scheduled_initial and self.instance and self.instance.pk:
            scheduled_initial = self.instance.scheduled_at
        self.fields['scheduled_at'] = BusinessLocalDateTimeField(
            business.timezone,
            label='Date and time',
            widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        )
        if scheduled_initial:
            self.initial['scheduled_at'] = scheduled_initial

        assignees = users_for_business(business).order_by('first_name', 'last_name', 'username')
        if self.role == User.Role.SALESPERSON or not self.allow_assignee_change:
            self.fields.pop('assigned_user')
        else:
            self.fields['assigned_user'].queryset = assignees
            default_assignee = (
                self.instance.assigned_user_id
                if self.instance and self.instance.pk
                else getattr(self.lead, 'assigned_user_id', None)
            )
            if (
                default_assignee
                and not self.initial.get('assigned_user')
                and assignees.filter(pk=default_assignee).exists()
            ):
                self.initial['assigned_user'] = default_assignee

        self.fields['reminder_enabled'].label = 'Create a reminder task one hour before'
        self.fields['reminder_enabled'].required = False
        if not self.instance or not self.instance.pk:
            self.initial.setdefault('reminder_enabled', True)

    def clean_scheduled_at(self):
        scheduled_at = self.cleaned_data['scheduled_at']
        if scheduled_at <= timezone.now():
            raise ValidationError('Choose a future time for the site visit.')
        return scheduled_at

    def clean(self):
        cleaned_data = super().clean()
        scheduled_at = cleaned_data.get('scheduled_at')
        reminder_enabled = cleaned_data.get('reminder_enabled', False)
        if scheduled_at and reminder_enabled and scheduled_at - timedelta(hours=1) <= timezone.now():
            self.add_error(
                'scheduled_at',
                'Choose a visit time at least one hour ahead or turn off the reminder.',
            )
        return cleaned_data


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


class MailboxImapForm(forms.Form):
    account_email = forms.EmailField(label='Mailbox email')
    display_name = forms.CharField(required=False, label='Display name')
    is_shared = forms.BooleanField(required=False, label='Shared team inbox')
    imap_host = forms.CharField(label='IMAP host')
    imap_port = forms.IntegerField(min_value=1, max_value=65535, initial=993)
    imap_use_ssl = forms.BooleanField(required=False, initial=True, label='Use IMAP SSL')
    smtp_host = forms.CharField(label='SMTP host')
    smtp_port = forms.IntegerField(min_value=1, max_value=65535, initial=587)
    smtp_use_tls = forms.BooleanField(required=False, initial=True, label='Use SMTP TLS')
    username = forms.CharField(label='Username')
    password = forms.CharField(widget=forms.PasswordInput(render_value=False), label='Password')
    backfill_days = forms.IntegerField(min_value=30, max_value=365, initial=90, label='Initial sync days')


class MailReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={'rows': 8}), label='Reply')
    html_body = forms.CharField(required=False, widget=forms.HiddenInput)
    to_emails = forms.CharField(required=False, widget=forms.HiddenInput)
    cc_emails = forms.CharField(required=False, widget=forms.HiddenInput)
    attachments = MultipleFileField(required=False, label='Attachments')

    def clean_to_emails(self):
        values = [item.strip() for item in self.cleaned_data.get('to_emails', '').split(',') if item.strip()]
        return values

    def clean_cc_emails(self):
        values = [item.strip() for item in self.cleaned_data.get('cc_emails', '').split(',') if item.strip()]
        return values

    def clean_attachments(self):
        attachments = self.cleaned_data.get('attachments', [])
        allowed_extensions = {
            '.csv', '.doc', '.docx', '.gif', '.jpeg', '.jpg', '.json', '.pdf',
            '.png', '.ppt', '.pptx', '.txt', '.webp', '.xls', '.xlsx', '.zip',
        }
        if len(attachments) > 10:
            raise ValidationError('Attach no more than 10 files.')
        if sum(uploaded.size for uploaded in attachments) > 25 * 1024 * 1024:
            raise ValidationError('Attachments must be 25 MB or smaller in total.')
        for uploaded in attachments:
            if uploaded.size > 10 * 1024 * 1024:
                raise ValidationError(f'{uploaded.name} is larger than the 10 MB per-file limit.')
            if Path(uploaded.name).suffix.casefold() not in allowed_extensions:
                raise ValidationError(f'{uploaded.name} is not a supported attachment type.')
        return attachments


class UnmatchedLeadForm(forms.Form):
    customer_name = forms.CharField(max_length=255, label='Customer name')
    phone = forms.CharField(max_length=32, label='Phone')
    assigned_user = forms.ModelChoiceField(queryset=User.objects.none(), label='Assign to')

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_user'].queryset = users_for_business(business).order_by(
            'first_name', 'last_name', 'username',
        )
