user = User.query.filter_by(google_id=user_info["id"]).first()

if not user:
    # First login: create user record
    user = User(
        google_id=user_info["id"],
        email=user_info["email"],
        name=user_info.get("name"),
        picture_url=user_info.get("picture"),
    )
    db.session.add(user)
    db.session.flush()

    # Create default settings (no iCal URL yet)
    settings = UserSettings(
        user_id=user.id,
        ics_secret_token=secrets.token_urlsafe(32),
    )
    db.session.add(settings)

user.last_login = datetime.utcnow()
db.session.commit()

login_user(user)