import os
import uuid
import hashlib
import csv
import functools
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
    session,
    g,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from sqlalchemy import text
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
    SponsorForm,
)
from models import db, User, Poll, Candidate, Vote, Sponsor
from translations import t as _t

ALLOWED_EXTENSIONS = {'csv', 'xls', 'xlsx'}
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
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


@app.context_processor
def inject_ui():
    lang = session.get('admin_lang', 'en')
    return {'lang': lang, 'ui': functools.partial(_t, lang)}


def ensure_directories():
    for folder in [
        app.config['UPLOAD_FOLDER'],
        app.config['QR_FOLDER'],
        app.config['LOGOS_FOLDER'],
        app.config['SPONSORS_FOLDER'],
        app.config['CANDIDATE_PHOTOS_FOLDER'],
    ]:
        os.makedirs(folder, exist_ok=True)


def migrate_db():
    with db.engine.connect() as conn:
        polls_cols = [
            ('logo_filename', 'VARCHAR(255)'),
            ('starts_at', 'DATETIME'),
            ('ends_at', 'DATETIME'),
            ('background_color', "VARCHAR(7) NOT NULL DEFAULT '#f3f5f7'"),
            ('language', "VARCHAR(5) NOT NULL DEFAULT 'en'"),
            ('poll_label', 'VARCHAR(100)'),
        ]
        for col, typedef in polls_cols:
            try:
                conn.execute(text(f'ALTER TABLE polls ADD COLUMN {col} {typedef}'))
                conn.commit()
            except Exception:
                pass

        candidates_cols = [
            ('jersey_number', 'VARCHAR(10)'),
            ('position', 'VARCHAR(100)'),
            ('stats_line', 'VARCHAR(255)'),
            ('photo_filename', 'VARCHAR(255)'),
        ]
        for col, typedef in candidates_cols:
            try:
                conn.execute(text(f'ALTER TABLE candidates ADD COLUMN {col} {typedef}'))
                conn.commit()
            except Exception:
                pass

    db.create_all()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_image(file_storage, folder):
    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    filename = f'{uuid.uuid4().hex}.{ext}'
    file_storage.save(os.path.join(folder, filename))
    return filename


def parse_datetime(s):
    if not s or not s.strip():
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def hash_value(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def parse_candidates_from_text(text_input):
    candidates = []
    if not text_input:
        return candidates
    for line in text_input.splitlines():
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
    cookie_value = request.cookies.get(COOKIE_NAME)
    if not cookie_value:
        cookie_value = str(uuid.uuid4())
    ip_hash = hash_value(get_client_ip())
    user_agent_hash = hash_value(request.headers.get('User-Agent', ''))
    voter_hash = hash_value(f'{poll_id}|{cookie_value}|{ip_hash}|{user_agent_hash}')
    return cookie_value, ip_hash, user_agent_hash, voter_hash


def persist_status_change(poll, eff):
    if eff == 'closed' and poll.status == 'active':
        poll.status = 'closed'
        poll.closed_at = poll.ends_at or datetime.now()
        db.session.commit()
    elif eff == 'active' and poll.status == 'draft':
        poll.status = 'active'
        db.session.commit()


def build_results(poll):
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    vote_counts = {c.id: c.vote_count() for c in candidates}
    total = sum(vote_counts.values())
    return candidates, vote_counts, total


# ── Public routes ─────────────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('admin_login'))


def _vote_context_extras(poll):
    """Compute extra template vars for the voter UI."""
    # Time remaining until poll closes
    time_left_str = None
    if poll.ends_at:
        delta = poll.ends_at - datetime.now()
        if delta.total_seconds() > 0:
            days = delta.days
            hours = delta.seconds // 3600
            time_left_str = f'{days}D {hours}H' if days > 0 else f'{hours}H'

    # Category label with auto-appended month/year
    label_display = None
    if poll.poll_label:
        ref_date = poll.ends_at or poll.created_at
        label_display = f"{poll.poll_label.upper()} · {ref_date.strftime('%B %Y').upper()}"

    return time_left_str, label_display


@app.route('/vote/<int:poll_id>', methods=['GET', 'POST'])
def vote(poll_id):
    poll = Poll.query.get_or_404(poll_id)
    ui = functools.partial(_t, poll.language)
    eff = poll.effective_status()
    persist_status_change(poll, eff)

    cookie_value, ip_hash, user_agent_hash, voter_hash = get_voter_hash(poll.id)
    existing_vote = Vote.query.filter_by(poll_id=poll.id, voter_hash=voter_hash).first()

    if existing_vote:
        return redirect(url_for('vote_done', poll_id=poll.id))

    time_left_str, label_display = _vote_context_extras(poll)

    if eff == 'not_started':
        opens_msg = f"{ui('poll_not_open')} {ui('poll_opens_at')} {poll.starts_at.strftime('%d.%m.%Y %H:%M')}."
        return render_template('vote.html', poll=poll, form=None, error=opens_msg, ui=ui,
                               eff=eff, candidates=[], vote_counts={}, total_votes=0,
                               candidates_by_votes=[], time_left_str=time_left_str,
                               label_display=label_display)

    if eff in ('draft', 'closed'):
        error = ui('poll_closed') if eff == 'closed' else ui('poll_not_open')
        if eff == 'closed':
            candidates, vote_counts, total = build_results(poll)
            return render_template(
                'vote_results.html',
                poll=poll,
                candidates=candidates,
                vote_counts=vote_counts,
                total=total,
                just_voted=False,
                already_voted=False,
                voted_candidate_id=None,
                closed=True,
                ui=ui,
            )
        return render_template('vote.html', poll=poll, form=None, error=error, ui=ui,
                               eff=eff, candidates=[], vote_counts={}, total_votes=0,
                               candidates_by_votes=[], time_left_str=time_left_str,
                               label_display=label_display)

    # Poll is active
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    if not candidates:
        return render_template('vote.html', poll=poll, form=None, error=ui('no_candidates'), ui=ui,
                               eff=eff, candidates=[], vote_counts={}, total_votes=0,
                               candidates_by_votes=[], time_left_str=time_left_str,
                               label_display=label_display)

    vote_counts = {c.id: c.vote_count() for c in candidates}
    total_votes = sum(vote_counts.values())
    candidates_by_votes = sorted(candidates, key=lambda c: vote_counts[c.id], reverse=True)

    form = VoteForm()
    form.candidate.choices = [(c.id, c.name) for c in candidates]

    if form.validate_on_submit():
        candidate = Candidate.query.filter_by(id=form.candidate.data, poll_id=poll.id).first()
        if not candidate:
            flash('Please select a valid candidate.', 'danger')
            return redirect(url_for('vote', poll_id=poll.id))

        new_vote = Vote(
            poll_id=poll.id,
            candidate_id=candidate.id,
            voter_hash=voter_hash,
            ip_hash=ip_hash,
            user_agent_hash=user_agent_hash,
        )
        db.session.add(new_vote)
        db.session.commit()

        response = make_response(redirect(url_for('vote_done', poll_id=poll.id, just_voted=1)))
        response.set_cookie(COOKIE_NAME, cookie_value, max_age=365 * 24 * 60 * 60)
        return response

    return render_template('vote.html', poll=poll, form=form, error=None, ui=ui,
                           eff=eff, candidates=candidates, vote_counts=vote_counts,
                           total_votes=total_votes, candidates_by_votes=candidates_by_votes,
                           time_left_str=time_left_str, label_display=label_display)


@app.route('/vote/<int:poll_id>/done')
def vote_done(poll_id):
    poll = Poll.query.get_or_404(poll_id)
    ui = functools.partial(_t, poll.language)
    cookie_value, ip_hash, user_agent_hash, voter_hash = get_voter_hash(poll.id)
    existing_vote = Vote.query.filter_by(poll_id=poll.id, voter_hash=voter_hash).first()
    if not existing_vote:
        return redirect(url_for('vote', poll_id=poll.id))

    just_voted = request.args.get('just_voted') == '1'
    candidates, vote_counts, total = build_results(poll)
    return render_template(
        'vote_results.html',
        poll=poll,
        candidates=candidates,
        vote_counts=vote_counts,
        total=total,
        just_voted=just_voted,
        already_voted=not just_voted,
        voted_candidate_id=existing_vote.candidate_id,
        closed=False,
        ui=ui,
    )


@app.route('/results/<int:poll_id>')
def public_results(poll_id):
    poll = Poll.query.get_or_404(poll_id)
    if not poll.public_results and not (current_user.is_authenticated and poll.user_id == current_user.id):
        flash('Public results are not available for this poll.', 'warning')
        return redirect(url_for('vote', poll_id=poll.id))
    candidates, vote_counts, total = build_results(poll)
    return render_template(
        'results.html',
        poll=poll,
        candidates=candidates,
        vote_counts=vote_counts,
        total=total,
    )


@app.route('/thanks')
def thanks():
    return render_template('thanks.html')


# ── Admin routes ────────────────────────────────────────────────────────────────────────────────────────

@app.route('/admin/set-language/<lang>')
def set_admin_language(lang):
    if lang in ('en', 'no'):
        session['admin_lang'] = lang
    return redirect(request.referrer or url_for('admin_dashboard'))


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
    flash('Logged out.', 'success')
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    polls = Poll.query.filter_by(user_id=current_user.id).order_by(Poll.created_at.desc()).all()
    for poll in polls:
        eff = poll.effective_status()
        persist_status_change(poll, eff)
    total_votes = sum(poll.vote_count() for poll in polls)
    return render_template('dashboard.html', polls=polls, total_votes=total_votes)


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
        lang = form.language.data or session.get('admin_lang', 'en')
        poll = Poll(
            user_id=current_user.id,
            title=form.title.data.strip(),
            description=form.description.data.strip() if form.description.data else '',
            status='draft',
            public_results=form.public_results.data,
            starts_at=parse_datetime(form.starts_at.data),
            ends_at=parse_datetime(form.ends_at.data),
            background_color=form.background_color.data or '#f3f5f7',
            language=lang,
        )
        logo_file = form.logo.data
        if logo_file and logo_file.filename and allowed_image(logo_file.filename):
            poll.logo_filename = save_image(logo_file, app.config['LOGOS_FOLDER'])

        db.session.add(poll)
        db.session.commit()

        candidate_names = parse_candidates_from_text(form.candidates_manual.data)
        uploaded_file = form.upload_file.data
        if uploaded_file and uploaded_file.filename and allowed_file(uploaded_file.filename):
            filename = secure_filename(uploaded_file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            uploaded_file.save(filepath)
            candidate_names += parse_candidates_file(filepath)

        for idx, name in enumerate(candidate_names, start=1):
            candidate = Candidate(poll_id=poll.id, name=name, sort_order=idx)
            db.session.add(candidate)
        db.session.commit()

        flash('Poll created.', 'success')
        return redirect(url_for('edit_poll', poll_id=poll.id))

    # Pre-fill language from session
    if request.method == 'GET':
        form.language.data = session.get('admin_lang', 'en')
    return render_template('create_poll.html', form=form)


@app.route('/admin/polls/<int:poll_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_poll(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    form = PollForm(obj=poll)
    if request.method == 'GET':
        if poll.starts_at:
            form.starts_at.data = poll.starts_at.strftime('%Y-%m-%dT%H:%M')
        if poll.ends_at:
            form.ends_at.data = poll.ends_at.strftime('%Y-%m-%dT%H:%M')

    if form.validate_on_submit():
        poll.title = form.title.data.strip()
        poll.description = form.description.data.strip() if form.description.data else ''
        poll.poll_label = form.poll_label.data.strip() if form.poll_label.data else None
        poll.public_results = form.public_results.data
        poll.starts_at = parse_datetime(form.starts_at.data)
        poll.ends_at = parse_datetime(form.ends_at.data)
        poll.background_color = form.background_color.data or '#f3f5f7'
        poll.language = form.language.data or 'en'

        logo_file = form.logo.data
        if logo_file and logo_file.filename and allowed_image(logo_file.filename):
            poll.logo_filename = save_image(logo_file, app.config['LOGOS_FOLDER'])

        db.session.commit()
        flash('Poll updated.', 'success')
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
    if poll.status != 'closed':
        poll.status = 'closed'
        poll.closed_at = datetime.utcnow()
        db.session.commit()
        flash('Poll has been closed.', 'success')
    return redirect(url_for('edit_poll', poll_id=poll.id))


@app.route('/admin/polls/<int:poll_id>/copy', methods=['POST'])
@login_required
def copy_poll(poll_id):
    original = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    lang = session.get('admin_lang', 'en')
    suffix = _t(lang, 'copy_suffix')
    new_poll = Poll(
        user_id=current_user.id,
        title=f'{original.title} {suffix}',
        description=original.description,
        status='draft',
        public_results=original.public_results,
        logo_filename=original.logo_filename,
        background_color=original.background_color,
        language=original.language,
        starts_at=None,
        ends_at=None,
    )
    db.session.add(new_poll)
    db.session.flush()

    for candidate in original.candidates:
        db.session.add(Candidate(
            poll_id=new_poll.id,
            name=candidate.name,
            sort_order=candidate.sort_order,
        ))
    for sponsor in original.sponsors:
        db.session.add(Sponsor(
            poll_id=new_poll.id,
            name=sponsor.name,
            logo_filename=sponsor.logo_filename,
            sort_order=sponsor.sort_order,
        ))
    db.session.commit()
    flash('Poll copied.', 'success')
    return redirect(url_for('edit_poll', poll_id=new_poll.id))


@app.route('/admin/poll/<int:poll_id>/delete', methods=['POST'])
@login_required
def delete_poll(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first()
    if poll:
        db.session.delete(poll)
        db.session.commit()
        flash('Poll deleted.', 'success')
    else:
        flash('Poll not found.', 'danger')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/polls/<int:poll_id>/candidates', methods=['GET', 'POST'])
@login_required
def manage_candidates(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    candidate_form = CandidateForm()
    upload_form = UploadCandidatesForm()
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()

    if candidate_form.validate_on_submit() and candidate_form.candidate_name.data:
        photo_filename = None
        photo_file = candidate_form.candidate_photo.data
        if photo_file and photo_file.filename and allowed_image(photo_file.filename):
            photo_filename = save_image(photo_file, app.config['CANDIDATE_PHOTOS_FOLDER'])
        candidate = Candidate(
            poll_id=poll.id,
            name=candidate_form.candidate_name.data.strip(),
            sort_order=len(candidates) + 1,
            jersey_number=candidate_form.jersey_number.data.strip() or None,
            position=candidate_form.position.data.strip() or None,
            stats_line=candidate_form.stats_line.data.strip() or None,
            photo_filename=photo_filename,
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
        flash('Please upload a CSV or Excel file.', 'danger')

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


@app.route('/admin/polls/<int:poll_id>/candidates/bulk-delete', methods=['POST'])
@login_required
def bulk_delete_candidates(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    ids = request.form.getlist('candidate_ids')
    if ids:
        Candidate.query.filter(
            Candidate.id.in_([int(i) for i in ids]),
            Candidate.poll_id == poll.id,
        ).delete(synchronize_session=False)
        db.session.commit()
        flash(f'{len(ids)} candidate(s) deleted.', 'success')
    return redirect(url_for('manage_candidates', poll_id=poll.id))


@app.route('/admin/polls/<int:poll_id>/candidates/<int:candidate_id>/edit', methods=['POST'])
@login_required
def edit_candidate(poll_id, candidate_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    candidate = Candidate.query.filter_by(id=candidate_id, poll_id=poll.id).first_or_404()
    candidate.name = request.form.get('name', candidate.name).strip() or candidate.name
    candidate.jersey_number = request.form.get('jersey_number', '').strip() or None
    candidate.position = request.form.get('position', '').strip() or None
    candidate.stats_line = request.form.get('stats_line', '').strip() or None
    photo_file = request.files.get('candidate_photo')
    if photo_file and photo_file.filename and allowed_image(photo_file.filename):
        candidate.photo_filename = save_image(photo_file, app.config['CANDIDATE_PHOTOS_FOLDER'])
    db.session.commit()
    flash('Candidate updated.', 'success')
    return redirect(url_for('manage_candidates', poll_id=poll.id))


@app.route('/admin/polls/<int:poll_id>/sponsors', methods=['GET', 'POST'])
@login_required
def manage_sponsors(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    form = SponsorForm()
    if form.validate_on_submit():
        logo_filename = None
        if form.sponsor_logo.data and form.sponsor_logo.data.filename and allowed_image(form.sponsor_logo.data.filename):
            logo_filename = save_image(form.sponsor_logo.data, app.config['SPONSORS_FOLDER'])
        sponsor = Sponsor(
            poll_id=poll.id,
            name=form.sponsor_name.data.strip() if form.sponsor_name.data else None,
            logo_filename=logo_filename,
            sort_order=len(poll.sponsors) + 1,
        )
        db.session.add(sponsor)
        db.session.commit()
        flash('Sponsor added.', 'success')
        return redirect(url_for('manage_sponsors', poll_id=poll.id))
    return render_template('sponsors.html', poll=poll, form=form)


@app.route('/admin/polls/<int:poll_id>/sponsors/delete/<int:sponsor_id>', methods=['POST'])
@login_required
def delete_sponsor(poll_id, sponsor_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    sponsor = Sponsor.query.filter_by(id=sponsor_id, poll_id=poll.id).first_or_404()
    db.session.delete(sponsor)
    db.session.commit()
    flash('Sponsor removed.', 'success')
    return redirect(url_for('manage_sponsors', poll_id=poll.id))


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
    candidates, vote_counts, total = build_results(poll)
    return render_template('results.html', poll=poll, candidates=candidates, vote_counts=vote_counts, total=total)


@app.route('/admin/polls/<int:poll_id>/export')
@login_required
def export_results(poll_id):
    poll = Poll.query.filter_by(id=poll_id, user_id=current_user.id).first_or_404()
    candidates = Candidate.query.filter_by(poll_id=poll.id).order_by(Candidate.sort_order).all()
    vote_counts = [(c.name, c.vote_count()) for c in candidates]
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


# Run at startup (works with both dev server and gunicorn)
with app.app_context():
    ensure_directories()
    migrate_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
