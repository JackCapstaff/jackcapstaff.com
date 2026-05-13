from datetime import datetime, timedelta, timezone

from app import app, db, NewsItem


IMAGE_CONDUCTING = "https://res.cloudinary.com/darait3ge/image/upload/v1778508695/jackcapstaff/images/Conducting-medium_hwrqzu.jpg"
IMAGE_STAGE = "https://res.cloudinary.com/darait3ge/image/upload/v1778508704/jackcapstaff/images/Stage-medium_sqsw71.jpg"
IMAGE_COMMUNICATOR = "https://res.cloudinary.com/darait3ge/image/upload/v1778508710/jackcapstaff/images/Communicator-medium_jna7c6.png"


def apply_updates() -> None:
    base_now = datetime.now(timezone.utc).replace(tzinfo=None)

    updates = [
        {
            "title": "Derwent Brass Qualifies for National Finals at the Royal Albert Hall",
            "featured_image": IMAGE_CONDUCTING,
            "published_at": base_now + timedelta(minutes=5),
        },
        {
            "title": "ConsTest Birmingham Open Victory for Derwent Brass",
            "featured_image": IMAGE_STAGE,
            "published_at": base_now - timedelta(minutes=1),
        },
        {
            "title": "Derby Concert Orchestra Announces 2026 Season",
            "featured_image": IMAGE_COMMUNICATOR,
            "published_at": base_now - timedelta(minutes=2),
        },
        {
            "title": "Continued Contest Success for Derwent Brass",
            "featured_image": IMAGE_STAGE,
            "published_at": base_now - timedelta(minutes=3),
        },
        {
            "title": "Expanding Adjudicating Portfolio",
            "featured_image": IMAGE_COMMUNICATOR,
            "published_at": base_now - timedelta(minutes=4),
        },
    ]

    updated = 0
    missing = []

    with app.app_context():
        for item in updates:
            row = NewsItem.query.filter_by(title=item["title"]).first()
            if not row:
                missing.append(item["title"])
                continue

            row.featured_image = item["featured_image"]
            row.published = True
            row.published_at = item["published_at"]
            updated += 1

        db.session.commit()

    print(f"Updated: {updated}")
    if missing:
        print("Missing:")
        for title in missing:
            print(f"- {title}")


if __name__ == "__main__":
    apply_updates()
