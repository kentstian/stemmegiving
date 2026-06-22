from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, TextAreaField, BooleanField,
    SubmitField, RadioField, FileField, SelectField,
)
from wtforms.validators import DataRequired, Email, EqualTo, Optional


class RegistrationForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    confirm = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match')])
    submit = SubmitField('Register')


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Log In')


class PollForm(FlaskForm):
    title = StringField('Poll title', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[Optional()])
    public_results = BooleanField('Show public results')
    # Stored as strings, parsed to datetime in the route
    starts_at = StringField('Start time', validators=[Optional()])
    ends_at = StringField('End time', validators=[Optional()])
    background_color = StringField('Background color', default='#f3f5f7', validators=[Optional()])
    language = SelectField('Language', choices=[('en', 'English'), ('no', 'Norsk')], default='en')
    logo = FileField('Logo / image', validators=[Optional()])
    submit = SubmitField('Save Poll')


class CreatePollForm(PollForm):
    candidates_manual = TextAreaField('Candidates', validators=[Optional()], description='One name per line')
    upload_file = FileField('Upload CSV / Excel file', validators=[Optional()])
    submit = SubmitField('Create Poll')


class CandidateForm(FlaskForm):
    candidate_name = StringField('Candidate name', validators=[DataRequired()])
    submit = SubmitField('Add Candidate')


class UploadCandidatesForm(FlaskForm):
    upload_file = FileField('Upload CSV / Excel file', validators=[Optional()])
    submit = SubmitField('Upload Candidates')


class SponsorForm(FlaskForm):
    sponsor_name = StringField('Sponsor name / text', validators=[Optional()])
    sponsor_logo = FileField('Sponsor logo / image', validators=[Optional()])
    submit = SubmitField('Add Sponsor')


class VoteForm(FlaskForm):
    candidate = RadioField('Choose a candidate', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Vote Now')
