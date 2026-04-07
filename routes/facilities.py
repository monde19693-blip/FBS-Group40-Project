from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user, login_fresh
from extensions import db
from models import Facility, Booking
from datetime import date, timedelta
from functools import wraps

facilities = Blueprint('facilities', __name__)

DUT_CAMPUSES = [
    'Indumiso', 'Ritson', 'ML Sultan', 'Riverside',
    'Brickfield', 'City Campus', 'Steve Biko'
]


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('Admin access required.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated


# Public facility list (no login required)
@facilities.route('/facilities')
def list_facilities():
    type_filter   = request.args.get('type',   'all')
    campus_filter = request.args.get('campus', 'all')
    page          = request.args.get('page', 1, type=int)
    per_page      = 9

    query = Facility.query
    if type_filter != 'all':
        query = query.filter_by(facility_type=type_filter)
    if campus_filter != 'all':
        query = query.filter_by(campus=campus_filter)

    pagination     = query.order_by(Facility.name).paginate(
                         page=page, per_page=per_page, error_out=False)
    all_facilities = pagination.items

    return render_template('facilities/list.html',
        facilities=all_facilities, pagination=pagination,
        type_filter=type_filter, campus_filter=campus_filter,
        campuses=DUT_CAMPUSES)


# Public facility detail (no login required)
@facilities.route('/facilities/<int:facility_id>')
def facility_detail(facility_id):
    facility = Facility.query.get_or_404(facility_id)
    today    = date.today()

    todays_bookings = Booking.query.filter(
        Booking.facility_id  == facility.id,
        Booking.booking_date == today,
        Booking.status.in_(['approved', 'paid'])
    ).order_by(Booking.start_time).all()

    upcoming = Booking.query.filter(
        Booking.facility_id  == facility.id,
        Booking.status.in_(['approved', 'paid']),
        Booking.booking_date >  today,
        Booking.booking_date <= today + timedelta(days=7)
    ).order_by(Booking.booking_date, Booking.start_time).all()

    reviews = FacilityRating.query.filter_by(facility_id=facility.id)\
                  .order_by(FacilityRating.created_at.desc()).limit(10).all()

    # Check if logged-in user can write a review and if they already have one
    user_review      = None
    can_write_review = False
    if current_user.is_authenticated:
        user_review = FacilityRating.query.filter_by(
            user_id=current_user.id, facility_id=facility.id, booking_id=None
        ).first()
        if current_user.is_external():
            can_write_review = facility.allow_external
        else:
            can_write_review = bool(Booking.query.filter(
                Booking.user_id     == current_user.id,
                Booking.facility_id == facility.id,
                Booking.status.in_(['approved', 'paid'])
            ).first())

    # Rating breakdown (count per star)
    all_ratings = FacilityRating.query.filter_by(facility_id=facility.id).all()
    rating_breakdown = {i: sum(1 for r in all_ratings if r.rating == i) for i in range(1, 6)}

    return render_template('facilities/detail.html',
        facility=facility, todays_bookings=todays_bookings,
        upcoming=upcoming, reviews=reviews, today=today,
        user_review=user_review, can_write_review=can_write_review,
        rating_breakdown=rating_breakdown,
        total_reviews=len(all_ratings))


# Submit facility review (standalone, no booking required)
@facilities.route('/facilities/<int:facility_id>/review', methods=['POST'])
@login_required
def submit_review(facility_id):
    facility = Facility.query.get_or_404(facility_id)

    rating_val = request.form.get('rating', '').strip()
    comment    = request.form.get('comment', '').strip()

    if not rating_val or not rating_val.isdigit() or not (1 <= int(rating_val) <= 5):
        flash('Please select a rating between 1 and 5 stars.', 'danger')
        return redirect(url_for('facilities.facility_detail', facility_id=facility_id))

    # External users can always review open facilities
    # Internal users must have had at least one booking at this facility
    if not current_user.is_external():
        has_booking = Booking.query.filter(
            Booking.user_id     == current_user.id,
            Booking.facility_id == facility_id,
            Booking.status.in_(['approved', 'paid'])
        ).first()
        if not has_booking:
            flash('You can only review facilities you have booked.', 'warning')
            return redirect(url_for('facilities.facility_detail', facility_id=facility_id))

    # One review per user per facility (not per booking — standalone)
    existing = FacilityRating.query.filter_by(
        user_id=current_user.id, facility_id=facility_id, booking_id=None
    ).first()
    if existing:
        # Update existing standalone review
        existing.rating  = int(rating_val)
        existing.comment = comment or None
        db.session.commit()
        flash('Your review has been updated.', 'success')
    else:
        db.session.add(FacilityRating(
            facility_id = facility_id,
            user_id     = current_user.id,
            booking_id  = None,
            rating      = int(rating_val),
            comment     = comment or None,
        ))
        db.session.commit()
        flash('Thank you for your review!', 'success')

    return redirect(url_for('facilities.facility_detail', facility_id=facility_id) + '#reviews')


# Add facility 
@facilities.route('/admin/facilities/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_facility():
    if request.method == 'POST':
        name           = request.form.get('name', '').strip()
        facility_type  = request.form.get('facility_type', '').strip()
        campus         = request.form.get('campus', '').strip()
        location       = request.form.get('location', '').strip()
        capacity       = request.form.get('capacity', 0)
        description    = request.form.get('description', '').strip()
        equipment      = request.form.get('equipment', '').strip()
        allow_external = request.form.get('allow_external') == 'on'
        price_str      = request.form.get('price_per_hour', '').strip()
        price_per_hour = float(price_str) if price_str else None

        if not all([name, facility_type, campus, location, capacity]):
            flash('Name, type, campus, location and capacity are required.', 'danger')
            return render_template('admin/facility_form.html', facility=None, campuses=DUT_CAMPUSES)

        if allow_external and not price_per_hour:
            flash('A price per hour is required when allowing external bookings.', 'danger')
            return render_template('admin/facility_form.html', facility=None, campuses=DUT_CAMPUSES)

        f = Facility(name=name, facility_type=facility_type, campus=campus,
                     location=location, capacity=int(capacity),
                     description=description, equipment=equipment,
                     allow_external=allow_external, price_per_hour=price_per_hour)
        db.session.add(f)
        db.session.flush()

        img_file = request.files.get('facility_image')
        if img_file and img_file.filename:
            from utils.file_upload import save_facility_image
            try:
                f.image_filename = save_facility_image(img_file)
            except ValueError as e:
                flash(str(e), 'warning')

        db.session.commit()
        flash(f'Facility "{name}" added.', 'success')
        return redirect(url_for('facilities.list_facilities'))

    return render_template('admin/facility_form.html', facility=None, campuses=DUT_CAMPUSES)


# Edit facility 
@facilities.route('/admin/facilities/<int:facility_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_facility(facility_id):
    facility = Facility.query.get_or_404(facility_id)
    if request.method == 'POST':
        facility.name           = request.form.get('name', '').strip()
        facility.facility_type  = request.form.get('facility_type', '').strip()
        facility.campus         = request.form.get('campus', '').strip()
        facility.location       = request.form.get('location', '').strip()
        facility.capacity       = int(request.form.get('capacity', facility.capacity))
        facility.description    = request.form.get('description', '').strip()
        facility.equipment      = request.form.get('equipment', '').strip()
        facility.is_available   = request.form.get('is_available') == 'on'
        facility.allow_external = request.form.get('allow_external') == 'on'
        price_str               = request.form.get('price_per_hour', '').strip()
        facility.price_per_hour = float(price_str) if price_str else None

        if facility.allow_external and not facility.price_per_hour:
            flash('A price per hour is required when allowing external bookings.', 'danger')
            return render_template('admin/facility_form.html', facility=facility, campuses=DUT_CAMPUSES)

        if request.form.get('remove_image') == '1' and facility.image_filename:
            from utils.file_upload import delete_facility_image
            delete_facility_image(facility.image_filename)
            facility.image_filename = None

        img_file = request.files.get('facility_image')
        if img_file and img_file.filename:
            from utils.file_upload import save_facility_image
            try:
                facility.image_filename = save_facility_image(
                    img_file, old_filename=facility.image_filename)
            except ValueError as e:
                flash(str(e), 'warning')

        db.session.commit()
        flash(f'Facility "{facility.name}" updated.', 'success')
        return redirect(url_for('facilities.list_facilities'))

    return render_template('admin/facility_form.html', facility=facility, campuses=DUT_CAMPUSES)


# Delete facility 
@facilities.route('/admin/facilities/<int:facility_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_facility(facility_id):
    facility = Facility.query.get_or_404(facility_id)
    if facility.image_filename:
        from utils.file_upload import delete_facility_image
        delete_facility_image(facility.image_filename)
    db.session.delete(facility)
    db.session.commit()
    flash(f'Facility "{facility.name}" deleted.', 'info')
    return redirect(url_for('facilities.list_facilities'))
