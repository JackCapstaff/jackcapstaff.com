import re

from app import app, db, User, PageContent


def slugify(text: str) -> str:
    value = text.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def get_author_id() -> int:
    author = (
        User.query.filter(User.role.in_(["admin", "editor"]))
        .order_by(User.id.asc())
        .first()
    ) or User.query.order_by(User.id.asc()).first()

    if not author:
        raise RuntimeError("No users found; cannot assign author_id for media blocks")

    return author.id


def add_blocks() -> None:
    videos = [
        {
            "video_id": "Ky0GtWNlz_U",
            "title": "Ballet for Band [Horovitz] - Derwent Brass",
            "content": "Recent performance clip from Jack Capstaff's YouTube channel.",
        },
        {
            "video_id": "Ef9TZq7dM3c",
            "title": "Elgar Variations [Ellerby] - Derwent Brass",
            "content": "Contest performance feature from the Midlands campaign.",
        },
        {
            "video_id": "aG00uChK95Q",
            "title": "Into the Unknown - Brass Band and Vocal Solo",
            "content": "Performance highlight from Jack Capstaff's brass band programming work.",
        },
        {
            "video_id": "FIw2wqTF3CU",
            "title": "We Need a Little Christmas - Brass Band",
            "content": "Seasonal brass feature from Jack Capstaff's YouTube channel.",
        },
    ]

    with app.app_context():
        author_id = get_author_id()

        max_order = db.session.query(db.func.max(PageContent.order)).filter_by(page="media").scalar()
        next_order = (max_order or 0) + 1

        inserted = 0
        updated = 0

        for item in videos:
            watch_url = f"https://www.youtube.com/watch?v={item['video_id']}"
            section = f"media-youtube-{slugify(item['video_id'])}"

            existing = (
                PageContent.query.filter_by(page="media", section=section).first()
                or PageContent.query.filter_by(page="media", youtube_embed_url=watch_url).first()
            )

            if existing:
                existing.title = item["title"]
                existing.content = item["content"]
                existing.youtube_embed_url = watch_url
                existing.published = True
                if existing.order is None:
                    existing.order = next_order
                    next_order += 1
                updated += 1
                continue

            block = PageContent(
                page="media",
                section=section,
                title=item["title"],
                content=item["content"],
                youtube_embed_url=watch_url,
                image_url=None,
                order=next_order,
                published=True,
                author_id=author_id,
            )
            next_order += 1
            db.session.add(block)
            inserted += 1

        db.session.commit()
        print(f"Inserted: {inserted}, Updated: {updated}")


if __name__ == "__main__":
    add_blocks()
