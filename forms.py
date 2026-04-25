from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, BooleanField, SubmitField, RadioField, FileField
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

class VoteForm(FlaskForm):
    candidate = RadioField('Choose a candidate', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Vote Now')
