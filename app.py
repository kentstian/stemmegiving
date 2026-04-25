import os
import uuid
import hashlib
import csv
from datetime import datetime
from io import BytesIO

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    request,
    flash,
    send_file,
    make_response,
    abort,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.utils import secure_filename

import qrcode
import pandas as pd

from config import Config
from forms import (
    RegistrationForm,
    LoginForm,
    PollForm,
    CreatePollForm,
    CandidateForm,
    UploadCandidatesForm,
    VoteForm,
)
from models import db, User, Poll, Candidate, Vote

ALLOWED_EXTENSIONS = {'csv', 'xls', 'xlsx'}
COOKIE_NAME = 'voter_token'

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def ensure_directories():
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['QR_FOLDER'], exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def hash_value(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def parse_candidates_from_text(text):
    candidates = []
    if not text:
        return candidates
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if ',' in cleaned and len(cleaned.split(',')) > 1:
            for part in cleaned.split(','):
                name = part.strip()
                if name:
                    candidates.append(name)
        else:
            candidates.append(cleaned)
    return candidates


def parse_candidates_file(filepath):
    candidates = []
    try:
        extension = filepath.rsplit('.', 1)[1].lower()
        if extension in ['xls', 'xlsx']:
            df = pd.read_excel(filepath, header=None, dtype=str)
        else:
            df = pd.read_csv(filepath, header=None, dtype=str, keep_default_na=False)
        for value in df.values.flatten():
            if value is None:
                continue
            candidate = str(value).strip()
            if candidate:
                candidates.append(candidate)
    except Exception:
        candidates = []
    return candidates


def get_client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def get_voter_hash(poll_id):
    # This voting protection is suitable for informal events only.
    # For serious voting, add stronger verification such as email, SMS, or authenticated user accounts.
    cookie_value = request.cookies.get(COOKIE_NAME)
    if not cookie_value:
        cookie_value = str(uuid.uuid4())
    ip_hash = hash_value(get_client_ip())
    user_agent_hash = hash_value(request.headers.get('User-Agent', ''))
    voter_hash = hash_value(f'{poll_id}|{cookie_value}|{ip_hash}|{user_agent_hash}')
    return cookie_value, ip_hash, user_agent_hash, voter_hash


@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('admin_login'))


@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    form = RegistrationForm()
    if form.validate_on_submit():
        existing = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if existing:
            flash('Email already registered. Please log in.', 'danger')
            return redirect(url_for('admin_login'))
        user = User(
            name=form.name.data.strip(),
            email=form.email.data.strip().lower(),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash('Administrator account created.', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('register.html', form=form)


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash('Logged in successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html', form=form)


@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    polls = Poll.query.filter_by(user_id=current_user.id).order_by(Poll.created_at.desc()).all()
    total_votes = sum(poll.vote_count() for poll in polls)
    return render_template('dashboard.html', polls=polls, total_votes=total_votes)


@app.route('/admin/poll/<int:poll_id>/delete', methods=['POST'])
@login_required
def delete_poll(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first()
    if poll:
        # Delete associated votes and candidates first
        Vote.query.filter_by(poll_id=poll.id).delete()
        Candidate.query.filter_by(poll_id=poll.id).delete()
        db.session.delete(poll)
        db.session.commit()
        flash('Poll deleted successfully.', 'success')
    else:
        flash('Poll not found or you do not have permission to delete it.', 'danger')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/polls')
@login_required
def admin_polls():
    polls = Poll.query.filter_by(user_id=current_user.id).order_by(Poll.created_at.desc()).all()
    return render_template('polls.html', polls=polls)


@app.route('/admin/polls/create', methods=['GET', 'POST'])
@login_required
def create_poll():
    form = CreatePollForm()
    if form.validate_on_submit():
        poll = Poll(
            user_id=current_user.id,
            title=form.title.data.strip(),
            description=form.description.data.strip(),
            status='draft',
            public_results=form.public_results.data,
        )
        db.session.add(poll)
        db.session.commit()

        candidate_names = parse_candidates_from_text(form.candidates_manual.data)
        uploaded_file = form.upload_file.data
        if uploaded_file and allowed_file(uploaded_file.filename):
            filename = secure_filename(uploaded_file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            uploaded_file.save(filepath)
            candidate_names += parse_candidates_file(filepath)

        for idx, name in enumerate(candidate_names, start=1):
            candidate = Candidate(poll_id=poll.id, name=name, sort_order=idx)
            db.session.add(candidate)
        db.session.commit()

        flash('Poll created. You can add more candidates and activate the poll.', 'success')
        return redirect(url_for('edit_poll', poll_id=poll.id))
    return render_template('create_poll.html', form=form)


@app.route('/admin/polls/<int:poll_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_poll(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    form = PollForm(obj=poll)
    if form.validate_on_submit():
        poll.title = form.title.data.strip()
        poll.description = form.description.data.strip()
        poll.public_results = form.public_results.data
        db.session.commit()
        flash('Poll updated successfully.', 'success')
        return redirect(url_for('edit_poll', poll_id=poll.id))
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    return render_template('edit_poll.html', form=form, poll=poll, candidates=candidates)


@app.route('/admin/polls/<int:poll_id>/activate', methods=['POST'])
@login_required
def activate_poll(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    if poll.status != 'active':
        poll.status = 'active'
        poll.closed_at = None
        db.session.commit()
        flash('Poll is now active.', 'success')
    return redirect(url_for('edit_poll', poll_id=poll.id))


@app.route('/admin/polls/<int:poll_id>/close', methods=['POST'])
@login_required
def close_poll(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    if poll.status == 'active':
        poll.status = 'closed'
        poll.closed_at = datetime.utcnow()
        db.session.commit()
        flash('Poll has been closed.', 'success')
    return redirect(url_for('edit_poll', poll_id=poll.id))


@app.route('/admin/polls/<int:poll_id>/candidates', methods=['GET', 'POST'])
@login_required
def manage_candidates(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    candidate_form = CandidateForm()
    upload_form = UploadCandidatesForm()
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()

    if candidate_form.validate_on_submit() and candidate_form.candidate_name.data:
        candidate = Candidate(
            poll_id=poll.id,
            name=candidate_form.candidate_name.data.strip(),
            sort_order=len(candidates) + 1,
        )
        db.session.add(candidate)
        db.session.commit()
        flash('Candidate added.', 'success')
        return redirect(url_for('manage_candidates', poll_id=poll.id))

    if upload_form.validate_on_submit() and upload_form.upload_file.data:
        uploaded_file = upload_form.upload_file.data
        if uploaded_file and allowed_file(uploaded_file.filename):
            filename = secure_filename(uploaded_file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            uploaded_file.save(filepath)
            names = parse_candidates_file(filepath)
            for idx, name in enumerate(names, start=len(candidates) + 1):
                candidate = Candidate(poll_id=poll.id, name=name, sort_order=idx)
                db.session.add(candidate)
            db.session.commit()
            flash('Candidates imported.', 'success')
            return redirect(url_for('manage_candidates', poll_id=poll.id))
        flash('Uploaded file is not a CSV or Excel file.', 'danger')
    return render_template(
        'candidates.html',
        poll=poll,
        candidates=candidates,
        candidate_form=candidate_form,
        upload_form=upload_form,
    )


@app.route('/admin/polls/<int:poll_id>/candidates/delete/<int:candidate_id>', methods=['POST'])
@login_required
def delete_candidate(poll_id, candidate_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    candidate = Candidate.query.filter_by(id=candidate_id, poll_id=poll.id).first_or_404()
    db.session.delete(candidate)
    db.session.commit()
    flash('Candidate deleted.', 'success')
    return redirect(url_for('manage_candidates', poll_id=poll.id))


@app.route('/admin/polls/<int:poll_id>/qr')
@login_required
def poll_qr(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    vote_url = url_for('vote', poll_id=poll.id, _external=True)
    qr_img = qrcode.make(vote_url)
    filename = f'poll_{poll.id}.png'
    file_path = os.path.join(app.config['QR_FOLDER'], filename)
    qr_img.save(file_path)
    return render_template('qr.html', poll=poll, qr_filename=filename, vote_url=vote_url)


@app.route('/admin/polls/<int:poll_id>/results')
@login_required
def poll_results(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    vote_counts = {candidate.id: candidate.vote_count() for candidate in candidates}
    total = sum(vote_counts.values())
    return render_template('results.html', poll=poll, candidates=candidates, vote_counts=vote_counts, total=total)


@app.route('/admin/polls/<int:poll_id>/export')
@login_required
def export_results(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    vote_counts = [(candidate.name, candidate.vote_count()) for candidate in candidates]
    total_votes = sum(count for _, count in vote_counts)

    output = BytesIO()
    writer = csv.writer(output)
    writer.writerow(['Poll title', poll.title])
    writer.writerow(['Description', poll.description or ''])
    writer.writerow(['Status', poll.status])
    writer.writerow(['Total votes', total_votes])
    writer.writerow([])
    writer.writerow(['Candidate', 'Votes'])
    for name, count in vote_counts:
        writer.writerow([name, count])
    output.seek(0)

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename=results_poll_{poll.id}.csv'
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    return response


@app.route('/vote/<int:poll_id>', methods=['GET', 'POST'])
def vote(poll_id):
    poll = Poll.query.get_or_404(poll_id)
    if poll.status != 'active':
        message = 'This poll is not active.'
        if poll.status == 'closed':
            message = 'This poll has been closed.'
        elif poll.status == 'draft':
            message = 'This poll is still in draft mode.'
        return render_template('vote.html', poll=poll, form=None, error=message)

    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    if not candidates:
        return render_template('vote.html', poll=poll, form=None, error='No candidates are available for this poll.')

    form = VoteForm()
    form.candidate.choices = [(candidate.id, candidate.name) for candidate in candidates]
    if form.validate_on_submit():
        candidate = Candidate.query.filter_by(id=form.candidate.data, poll_id=poll.id).first()
        if not candidate:
            flash('Please select a valid candidate.', 'danger')
            return redirect(url_for('vote', poll_id=poll.id))

        cookie_value, ip_hash, user_agent_hash, voter_hash = get_voter_hash(poll.id)
        existing = Vote.query.filter_by(poll_id=poll.id, voter_hash=voter_hash).first()
        if existing:
            return render_template('vote.html', poll=poll, form=form, error='You have already voted in this poll.')

        vote = Vote(
            poll_id=poll.id,
            candidate_id=candidate.id,
            voter_hash=voter_hash,
            ip_hash=ip_hash,
            user_agent_hash=user_agent_hash,
        )
        db.session.add(vote)
        db.session.commit()

        response = make_response(redirect(url_for('thanks')))
        response.set_cookie(COOKIE_NAME, cookie_value, max_age=365 * 24 * 60 * 60)
        return response

    return render_template('vote.html', poll=poll, form=form, error=None)


@app.route('/thanks')
def thanks():
    return render_template('thanks.html')


@app.route('/results/<int:poll_id>')
def public_results(poll_id):
    poll = Poll.query.get_or_404(poll_id)
    if not poll.public_results and not (current_user.is_authenticated and poll.user_id == current_user.id):
        flash('Public results are not available for this poll.', 'warning')
        return redirect(url_for('vote', poll_id=poll.id))
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    vote_counts = {candidate.id: candidate.vote_count() for candidate in candidates}
    total = sum(vote_counts.values())
    return render_template('results.html', poll=poll, candidates=candidates, vote_counts=vote_counts, total=total)


if __name__ == '__main__':
    ensure_directories()
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
