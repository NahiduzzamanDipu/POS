"""Report parameter forms. Validated server-side like every other input."""

from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

MONTHS = [(i, date(2000, i, 1).strftime('%B')) for i in range(1, 13)]


def _year_choices():
    this_year = timezone.localdate().year
    return [(y, y) for y in range(this_year - 6, this_year + 2)]


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = 'select' if isinstance(field.widget, forms.Select) else 'input'
            field.widget.attrs['class'] = css


class DailyReportForm(StyledForm):
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        initial=timezone.localdate,
        label='Date',
    )

    def clean_date(self):
        value = self.cleaned_data['date']
        if value > timezone.localdate():
            raise ValidationError('That date is in the future.')
        return value


class MonthlyReportForm(StyledForm):
    month = forms.TypedChoiceField(choices=MONTHS, coerce=int, label='Month')
    year = forms.TypedChoiceField(choices=_year_choices, coerce=int, label='Year')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields['month'].initial = today.month
        self.fields['year'].initial = today.year


class YearlyReportForm(StyledForm):
    year = forms.TypedChoiceField(choices=_year_choices, coerce=int, label='Year')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['year'].initial = timezone.localdate().year


class CustomerReportForm(StyledForm):
    """Inclusive From/To range."""

    from_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}), label='From Date'
    )
    to_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}), label='To Date'
    )
    customer_number = forms.CharField(
        max_length=20, required=False, label='Customer Number (optional)',
        widget=forms.TextInput(attrs={'placeholder': '01XXXXXXXXX', 'inputmode': 'numeric'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields['from_date'].initial = today.replace(day=1)
        self.fields['to_date'].initial = today

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('from_date'), cleaned.get('to_date')
        if start and end and start > end:
            self.add_error('to_date', 'The To date cannot be before the From date.')
        if end and end > timezone.localdate():
            self.add_error('to_date', 'The To date is in the future.')
        return cleaned
